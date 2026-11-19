"""
Loot filter: NeverSink's filter (MIT) as the base, with the companion's own rules on top:

- Leveling (zones below level 68): your PoB build's socket colours for the current level stage (main skill big),
  any 4-link, movement-speed boots.
- Everywhere: price tiers from poe.ninja for the league (currency, cards, scarabs, fragments, essences, oils, ...
  exactly by name; uniques by base type, since a filter can't see which unique an item is).

The companion only *writes a .filter file* into your PoE folder (like FilterBlade does). You pick it once in the game's
options; after an update you reload it there. Nothing is sent to the game. Qt-free so it can be unit-tested.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)
Fetch = Callable[[str], str]

NEVERSINK_URL = "https://raw.githubusercontent.com/NeverSinkDev/NeverSink-Filter/master/{}"
STRICTNESS = {  # label -> NeverSink file
    "show a lot": "NeverSink's filter - 0-SOFT.filter",
    "medium": "NeverSink's filter - 2-SEMI-STRICT.filter",
    "only what matters": "NeverSink's filter - 3-STRICT.filter",
}
NINJA = "https://poe.ninja/poe1/api"
EXCHANGE_TYPES = ("Currency", "Fragment", "DivinationCard", "Scarab", "Essence", "Oil", "Fossil", "Resonator",
                  "DeliriumOrb", "Omen", "Tattoo", "Artifact", "Incubator")
UNIQUE_TYPES = ("UniqueArmour", "UniqueWeapon", "UniqueAccessory", "UniqueJewel", "UniqueFlask")
FILTER_NAME = "TrialmastersLedger.filter"
COLOUR_OF_ATTR = {"strength": "R", "dexterity": "G", "intelligence": "B"}
MOVE_SPEED_PREFIXES = ("Runner's", "Sprinter's", "Stallion's", "Gazelle's", "Cheetah's", "Hellion's")
# Affix names (what HasExplicitMod matches), lowest tier first
LIFE_PREFIXES = ("Hale", "Healthy", "Sanguine", "Stalwart", "Stout", "Robust", "Rotund", "Virile", "Athlete's", "Fecund", "Vigorous")
FIRE_RES = ("of the Whelpling", "of the Salamander", "of the Drake", "of the Kiln", "of the Furnace", "of the Volcano", "of Magma", "of Tzteosh")
COLD_RES = ("of the Seal", "of the Penguin", "of the Yeti", "of the Walrus", "of the Polar Bear", "of the Ice", "of Haast")
LIGHTNING_RES = ("of the Cloud", "of the Squall", "of the Storm", "of the Thunderhead", "of the Tempest", "of the Maelstrom", "of Ephij")
LIFE_REGEN = ("of the Newt", "of the Lizard", "of the Flatworm", "of the Starfish", "of the Hydra", "of the Troll")
GEAR_CLASSES = ("Body Armours", "Helmets", "Gloves", "Boots", "Belts", "Rings", "Amulets")
WEAPON_KEYWORDS = (("Sceptre", "Sceptres"), ("Shield", "Shields"), ("Buckler", "Shields"), ("Warstaff", "Warstaves"),
                   ("Quarterstaff", "Warstaves"), ("Staff", "Staves"), ("Wand", "Wands"), ("Bow", "Bows"),
                   ("Quiver", "Quivers"), ("Dagger", "Daggers"), ("Kris", "Daggers"), ("Claw", "Claws"))


@dataclass
class GearProfile:
    """What gear to highlight while leveling, worked out from the imported PoB build."""
    label: str
    weapon_classes: List[str] = field(default_factory=list)  # e.g. ["Sceptres", "Shields"]
    good_mods: List[str] = field(default_factory=list)  # affix names; 2+ of them on an item = highlight


def gear_profile(build) -> Optional["GearProfile"]:
    """Weapon/shield types from the build's PoB items; good affixes: life + resistances (RF: fire res + life regen)."""
    if build is None:
        return None
    classes: List[str] = []
    suffixes: List[str] = []
    for it in getattr(build, "items", []):
        base = it.get("base", "")
        if "Flask" in base or "Jewel" in base:
            continue
        for keyword, cls in WEAPON_KEYWORDS:
            if keyword in base:
                if cls not in classes:
                    classes.append(cls)
                break
        name = it.get("name", "")
        if it.get("rarity") == "MAGIC" and " of " in name:  # "Ribbed Leather Belt of the Kiln" -> "of the Kiln"
            suffixes.append("of " + name.split(" of ", 1)[1])
    rf = any("Righteous Fire" in g.name for g in getattr(build, "gems", []))
    mods = list(LIFE_PREFIXES) + list(FIRE_RES) + (list(LIFE_REGEN) if rf else list(COLD_RES) + list(LIGHTNING_RES))
    mods += [m for m in suffixes if m not in mods]
    label = ("Righteous Fire: life, fire res, life regen" if rf else "life + resistances") + (
        f" · {', '.join(classes)}" if classes else "")
    return GearProfile(label, classes, mods)


def _default_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "trialmasters-ledger/1.0 (+https://github.com/kombinerka/trialmasters-ledger)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def default_filter_dir() -> Path:
    """Documents\\My Games\\Path of Exile (asks Windows where 'Documents' really is - it may be in OneDrive)."""
    import sys

    docs = Path.home() / "Documents"
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
                docs = Path(buf.value)
        except Exception:  # noqa: BLE001 - fall back to the usual place
            pass
    return docs / "My Games" / "Path of Exile"


# ---------------------------------------------------------------- prices
@dataclass
class Prices:
    league: str
    fetched_at: float
    items: Dict[str, float] = field(default_factory=dict)  # exact name -> chaos (currency, cards, scarabs, ...)
    unique_bases: Dict[str, list] = field(default_factory=dict)  # base type -> [cheapest, most expensive] unique (chaos)
    note: str = ""  # shown to the user, e.g. "no prices yet"

    def to_json(self) -> str:
        return json.dumps({"league": self.league, "fetched_at": self.fetched_at, "items": self.items,
                           "unique_bases": self.unique_bases})

    @staticmethod
    def from_json(text: str) -> "Prices":
        d = json.loads(text)
        return Prices(d["league"], float(d["fetched_at"]), dict(d.get("items", {})), dict(d.get("unique_bases", {})))


def current_leagues(fetch: Optional[Fetch] = None) -> List[str]:
    """Trade leagues poe.ninja tracks right now, challenge league first."""
    data = json.loads((fetch or _default_fetch)(f"{NINJA}/data/index-state"))
    names = [lg["name"] for lg in data.get("economyLeagues", [])]
    return sorted(names, key=lambda n: (n in ("Standard", "Hardcore") or n.startswith("Hardcore"), names.index(n)))


def fetch_prices(league: str, fetch: Optional[Fetch] = None, min_listings: int = 5) -> Prices:
    fetch = fetch or _default_fetch
    q = urllib.parse.quote(league)
    prices = Prices(league, time.time())
    for t in EXCHANGE_TYPES:
        try:
            d = json.loads(fetch(f"{NINJA}/economy/exchange/current/overview?league={q}&type={t}"))
        except Exception as exc:  # noqa: BLE001 - one missing category shouldn't stop the rest
            logger.warning("poe.ninja %s: %s", t, exc)
            continue
        names = {i["id"]: i["name"] for i in d.get("items", [])}
        for line in d.get("lines", []):
            name = names.get(line.get("id"))
            if name and line.get("primaryValue") is not None:
                prices.items[name] = round(float(line["primaryValue"]), 2)
    for t in UNIQUE_TYPES:
        try:
            d = json.loads(fetch(f"{NINJA}/economy/stash/current/item/overview?league={q}&type={t}"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("poe.ninja %s: %s", t, exc)
            continue
        for line in d.get("lines", []):
            # Ignore rare variants a normal drop can't be: Foulborn (mutated) uniques, linked copies, thin listings
            if str(line.get("name", "")).startswith("Foulborn") or line.get("links") or int(line.get("listingCount", 0)) < min_listings:
                continue
            base, value = line.get("baseType"), round(float(line.get("chaosValue") or 0), 1)
            if not base:
                continue
            lo, hi = prices.unique_bases.get(base, [value, value])
            prices.unique_bases[base] = [min(lo, value), max(hi, value)]
    return prices


# ---------------------------------------------------------------- build sockets
def socket_colours(gem_ids: Sequence[str], gems: Dict[str, list]) -> str:
    """'RRG' for a socket group: one letter per gem, from the gem's main attribute (sorted R, G, B, W)."""
    by_name = {v[0].lower(): k for k, v in gems.items()}
    letters = []
    for gid in gem_ids:
        key = gid if gid in gems else by_name.get(str(gid).lower())
        attr = gems[key][3] if key and len(gems[key]) > 3 else "none"
        letters.append(COLOUR_OF_ATTR.get(attr, "W"))
    order = {"R": 0, "G": 1, "B": 2, "W": 3}
    return "".join(sorted((ch for ch in letters if ch != "W"), key=order.get))


def stage_socket_groups(skill_groups: Sequence[tuple], gems: Dict[str, list], level: Optional[int],
                        max_links: int = 4) -> List[Tuple[str, bool]]:
    """Colour strings for the build's level stage you're in: [('RRG', is_main), ...], main group first."""
    if not skill_groups:
        return []
    stages = sorted({g[0] for g in skill_groups})
    current = max([s for s in stages if level is None or s <= level] or stages[:1])
    groups = [g for g in skill_groups if g[0] == current and len(g[2]) >= 2]
    if not any(g[1] for g in groups) and groups:  # later stages: the biggest group is the main skill
        biggest = max(groups, key=lambda g: len(g[2]))
        groups = [(g[0], g is biggest, g[2]) for g in groups]
    out, seen = [], set()
    for _, is_main, ids in sorted(groups, key=lambda g: not g[1]):
        colours = socket_colours(ids, gems)[:max_links] if len(ids) <= max_links else socket_colours(ids[:max_links], gems)
        if len(colours) >= 2 and colours not in seen:
            seen.add(colours)
            out.append((colours, bool(is_main)))
    return out


# ---------------------------------------------------------------- filter text
def _quote_list(names: Sequence[str]) -> str:
    return " ".join('"' + n.replace('"', "") + '"' for n in sorted(set(names)))


def _block(conditions: List[str], style: List[str], comment: str) -> str:
    return "\n".join([f"Show # Trialmaster's Ledger: {comment}"] + ["\t" + c for c in conditions + style]) + "\n"


STYLES = {
    "top": ["SetFontSize 45", "SetTextColor 255 0 0 255", "SetBorderColor 255 0 0 255", "SetBackgroundColor 255 255 255 255",
            "PlayAlertSound 1 300", "PlayEffect Red", "MinimapIcon 0 Red Star"],
    "high": ["SetFontSize 42", "SetTextColor 0 0 0 255", "SetBorderColor 0 0 0 255", "SetBackgroundColor 249 150 25 255",
             "PlayAlertSound 2 300", "PlayEffect Orange", "MinimapIcon 1 Orange Circle"],
    "mid": ["SetFontSize 38", "SetTextColor 255 255 255 255", "SetBorderColor 249 150 25 255", "SetBackgroundColor 60 40 20 240",
            "PlayEffect Orange Temp", "MinimapIcon 2 Orange Circle"],
    "main_links": ["SetFontSize 45", "SetTextColor 255 255 255 255", "SetBorderColor 52 211 153 255",
                   "SetBackgroundColor 6 78 59 245", "PlayAlertSound 2 300", "PlayEffect Green", "MinimapIcon 1 Green Square"],
    "other_links": ["SetFontSize 40", "SetTextColor 255 255 255 255", "SetBorderColor 52 211 153 255",
                    "SetBackgroundColor 20 50 40 230", "PlayEffect Green Temp", "MinimapIcon 2 Green Square"],
    "four_link": ["SetFontSize 40", "SetBorderColor 255 255 255 255", "SetBackgroundColor 40 40 60 230",
                  "PlayEffect Grey Temp", "MinimapIcon 2 Grey Square"],
    "gear_top": ["SetFontSize 44", "SetTextColor 255 255 255 255", "SetBorderColor 251 146 60 255",
                 "SetBackgroundColor 124 45 18 245", "PlayAlertSound 4 300", "PlayEffect Orange", "MinimapIcon 1 Orange Diamond"],
    "gear_weapon": ["SetFontSize 40", "SetBorderColor 251 146 60 255", "SetBackgroundColor 67 30 12 230",
                    "PlayEffect Orange Temp", "MinimapIcon 2 Orange Diamond"],
    # Chaos Orbs: the everyday currency – big, gold and with a sound, so a single one never gets missed on the ground
    "chaos": ["SetFontSize 45", "SetTextColor 255 215 0 255", "SetBorderColor 255 215 0 255",
              "SetBackgroundColor 70 45 0 245", "PlayAlertSound 2 250", "PlayEffect Yellow", "MinimapIcon 1 Yellow Circle"],
    "boots": ["SetFontSize 42", "SetTextColor 255 255 255 255", "SetBorderColor 147 197 253 255",
              "SetBackgroundColor 30 58 138 240", "PlayAlertSound 3 300", "PlayEffect Blue", "MinimapIcon 1 Blue Triangle"],
}


TIER4_MODES = ("highlight", "quiet", "hide")
_TIER_RE = re.compile(r"^(\s*)UnidentifiedItemTier\s*(>=|==)?\s*(\d)\s*$", re.M)
_EFFECT_RE = re.compile(r"^\s*(PlayEffect|PlayAlertSound|PlayAlertSoundPositional|CustomAlertSound|MinimapIcon)\b")


def _clean_newlines(text: str) -> str:
    """\r\r\n (an older cache written in Windows text mode) and \r\n -> \n."""
    return text.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")


def adjust_tier4(base_filter: str, mode: str) -> str:
    """
    Item tiers (new in 3.30): NeverSink highlights tiered gear with a blue beam, sound and minimap icon. For 'quiet' /
    'hide', every block that tier-4 gear can reach gets a copy in front of it that catches exactly tier 4
    (UnidentifiedItemTier == 4): same look without beam/sound/icon, or hidden. Tiers 3 and 5 keep NeverSink's style.
    """
    if mode not in ("quiet", "hide"):
        return base_filter
    out = []
    for block in re.split(r"(\n\s*\n)", base_filter):
        m = _TIER_RE.search(block)
        if block.lstrip().startswith("Show") and m:
            op, tier = m.group(2) or "==", int(m.group(3))
            if (op == ">=" and tier <= 4) or (op == "==" and tier == 4):
                lines = [ln for ln in block.split("\n") if not _EFFECT_RE.match(ln)]
                copy = "\n".join(lines)
                copy = _TIER_RE.sub(lambda mm: f"{mm.group(1)}UnidentifiedItemTier == 4", copy, count=1)
                copy = copy.replace("Show", "Hide" if mode == "hide" else "Show", 1)
                copy = copy.replace("#", "# Trialmaster's Ledger: tier 4 " + mode + " ·", 1) if "#" in copy.split("\n")[0] else copy
                out.append(copy + "\n\n")
        out.append(block)
    return "".join(out)


@dataclass
class FilterSettings:
    strictness: str = "show a lot"
    tier4: str = "highlight"  # highlight | quiet | hide - gear of the new item tier 4
    top_chaos: float = 100.0
    high_chaos: float = 20.0
    mid_chaos: float = 5.0
    unique_high_chaos: float = 20.0


def companion_rules(cfg: FilterSettings, prices: Optional[Prices], socket_groups: Sequence[Tuple[str, bool]],
                    gear: Optional[GearProfile] = None) -> str:
    out = []
    # ---- leveling: gear for your build (before the link rules: an item with good mods AND links stays big)
    if gear and gear.good_mods:
        classes = list(gear.weapon_classes) + list(GEAR_CLASSES)
        out.append(_block(["AreaLevel <= 67", "Rarity Magic Rare", f"Class == {_quote_list(classes)}",
                           f"HasExplicitMod >= 2 {_quote_list(gear.good_mods)}"],
                          STYLES["gear_top"], f"build gear with 2+ good mods ({gear.label})"))
    if gear and gear.weapon_classes:
        out.append(_block(["AreaLevel <= 67", "Rarity Magic Rare", f"Class == {_quote_list(gear.weapon_classes)}"],
                          STYLES["gear_weapon"], f"your weapon types: {', '.join(gear.weapon_classes)}"))
    # ---- leveling: your build's links (zones below maps)
    for colours, is_main in socket_groups:
        out.append(_block(["AreaLevel <= 67", "Rarity Normal Magic Rare", f'SocketGroup "{colours}"'],
                          STYLES["main_links" if is_main else "other_links"],
                          f"{'main skill' if is_main else 'build'} links {colours}"))
    out.append(_block(["AreaLevel <= 67", "Rarity Normal Magic Rare", "LinkedSockets >= 4"], STYLES["four_link"], "any 4-link while leveling"))
    out.append(_block(["AreaLevel <= 67", "Rarity Magic Rare", 'Class == "Boots"', f"HasExplicitMod {_quote_list(MOVE_SPEED_PREFIXES)}"],
                      STYLES["boots"], "movement speed boots while leveling"))
    # ---- Chaos Orbs (everywhere): always clearly visible
    out.append(_block(['Class == "Stackable Currency"', 'BaseType == "Chaos Orb"'], STYLES["chaos"], "Chaos Orb"))
    # ---- prices (everywhere)
    if prices:
        for tier, low, high in (("top", cfg.top_chaos, None), ("high", cfg.high_chaos, cfg.top_chaos), ("mid", cfg.mid_chaos, cfg.high_chaos)):
            names = [n for n, v in prices.items.items() if v >= low and (high is None or v < high)]
            if names:
                out.append(_block([f"BaseType == {_quote_list(names)}"], STYLES[tier], f"{tier} value ({low:g}c+) by poe.ninja"))
        # A filter only sees the base type: "top" when every unique on that base is expensive,
        # "worth a look" when at least one of them is (e.g. Heavy Belt: Mageblood among cheap belts)
        top = [b for b, (lo, hi) in prices.unique_bases.items() if lo >= cfg.top_chaos]
        if top:
            out.append(_block(["Rarity Unique", f"BaseType == {_quote_list(top)}"], STYLES["top"],
                              f"unique base, every unique on it is worth {cfg.top_chaos:g}c+"))
        maybe = [b for b, (lo, hi) in prices.unique_bases.items() if hi >= cfg.unique_high_chaos and b not in top]
        if maybe:
            out.append(_block(["Rarity Unique", f"BaseType == {_quote_list(maybe)}"], STYLES["high"],
                              f"unique base, one of its uniques is worth {cfg.unique_high_chaos:g}c+ (check it)"))
    return "\n".join(out)


def build_filter(base_filter: str, cfg: FilterSettings, prices: Optional[Prices], socket_groups: Sequence[Tuple[str, bool]],
                 build_title: str = "", gear: Optional[GearProfile] = None) -> str:
    head = [
        "#" + "=" * 100,
        "# Trialmaster's Ledger loot filter (written by the app - your changes here are overwritten on the next update)",
        f"# Base: NeverSink's filter, {cfg.strictness} (MIT, https://github.com/NeverSinkDev/NeverSink-Filter)",
        f"# Build: {build_title or 'none imported'} · links: {', '.join(c for c, _ in socket_groups) or '-'}"
        + (f" · gear: {gear.label}" if gear else ""),
        f"# Prices: poe.ninja, {prices.league if prices else 'not loaded'}"
        + (f", {time.strftime('%Y-%m-%d %H:%M', time.localtime(prices.fetched_at))}" if prices else ""),
        "# In game: Options -> UI -> Item Filter -> pick TrialmastersLedger; after an update pick it again / press reload there",
        "#" + "=" * 100,
        "",
    ]
    if prices:
        # PoE rejects a filter with an unknown BaseType: only keep names NeverSink's filter itself uses
        known = set(re.findall(r'"([^"\n]+)"', base_filter))
        prices = Prices(prices.league, prices.fetched_at,
                        {n: v for n, v in prices.items.items() if n in known},
                        {b: v for b, v in prices.unique_bases.items() if b in known})
    base_filter = adjust_tier4(base_filter, cfg.tier4)
    return "\n".join(head) + companion_rules(cfg, prices, socket_groups, gear) + "\n\n" + base_filter


class LootFilterManager:
    """Downloads/caches the base filter and prices, writes the filter file."""

    PRICE_MAX_AGE = 3600  # refresh prices hourly
    BASE_MAX_AGE = 24 * 3600  # base filter daily (NeverSink ships a league-start update) and on a league change

    def __init__(self, cache_dir: Path, fetch: Optional[Fetch] = None) -> None:
        self.cache_dir = Path(cache_dir)
        self.fetch = fetch or _default_fetch

    def _fresh(self, path: Path, max_age: float) -> bool:
        return path.is_file() and time.time() - path.stat().st_mtime < max_age

    def base_filter(self, strictness: str, force: bool = False) -> str:
        fname = STRICTNESS.get(strictness, STRICTNESS["show a lot"])
        path = self.cache_dir / ("neversink_" + re.sub(r"[^A-Za-z0-9]+", "_", fname))
        if force or not self._fresh(path, self.BASE_MAX_AGE):
            try:
                text = _clean_newlines(self.fetch(NEVERSINK_URL.format(urllib.parse.quote(fname))))
                if "Show" not in text:
                    raise ValueError("unexpected download")
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text.encode("utf-8"))  # bytes: Windows text mode would double the \r
            except Exception as exc:  # noqa: BLE001 - use the cached copy if there is one
                if not path.is_file():
                    raise RuntimeError(f"couldn't download NeverSink's filter: {exc}") from exc
                logger.warning("Base filter update failed, using cached copy: %s", exc)
        return _clean_newlines(path.read_bytes().decode("utf-8", errors="replace"))

    def prices(self, league: str, force: bool = False) -> Prices:
        path = self.cache_dir / f"prices_{re.sub(r'[^A-Za-z0-9]+', '_', league)}.json"
        if not force and self._fresh(path, self.PRICE_MAX_AGE):
            return Prices.from_json(path.read_text(encoding="utf-8"))
        try:
            p = fetch_prices(league, self.fetch)
            if not p.items and not p.unique_bases:
                raise ValueError("no prices returned")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(p.to_json(), encoding="utf-8")
            return p
        except Exception as exc:  # noqa: BLE001
            if path.is_file():
                logger.warning("Price update failed, using cached prices: %s", exc)
                return Prices.from_json(path.read_text(encoding="utf-8"))
            raise RuntimeError(f"couldn't load prices from poe.ninja: {exc}") from exc

    def write(self, target_dir: Path, cfg: FilterSettings, league: str, socket_groups: Sequence[Tuple[str, bool]],
              build_title: str = "", force_prices: bool = False, gear: Optional[GearProfile] = None) -> Tuple[Path, Prices]:
        # A new league: fetch NeverSink's league-start update right away
        marker = self.cache_dir / "last_league.txt"
        league_changed = marker.is_file() and marker.read_text(encoding="utf-8").strip() != league
        base = self.base_filter(cfg.strictness, force=league_changed)
        try:
            prices = self.prices(league, force=force_prices)
        except RuntimeError as exc:
            # Fresh league without prices on poe.ninja yet: still write the filter (NeverSink tiers + build rules)
            logger.warning("No prices for %s yet: %s", league, exc)
            prices = Prices(league, time.time())
            prices.note = "no prices on poe.ninja yet (new league?) – using NeverSink's tiers; retried every hour"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(league, encoding="utf-8")
        text = build_filter(base, cfg, prices if (prices.items or prices.unique_bases) else None, socket_groups, build_title, gear)
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        out = target_dir / FILTER_NAME
        tmp = out.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(out)
        return out, prices
