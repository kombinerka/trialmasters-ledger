"""
Inscribed Ultimatum price check (part of Ctrl+F3): reads Challenge / Area Level / Requires Sacrifice / Reward from the
tooltip and shows, compactly, what the sacrifice and the reward are worth (poe.ninja) plus what the same ultimatum
(same sacrifice + reward) is listed for on the official trade site. Qt-free so it can be unit-tested.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from src.trade_search import TRADE, ULTIMATUM_STATUS, TradeBusy, _http_get, _http_post, squash, try_steps

_FIELD_RE = re.compile(r"^\s*(challenge|area\s*level|requires\s*sacrifice|reward)\s*:\s*(.+?)\s*$", re.I)
_COUNT_RE = re.compile(r"\s*[x×]\s*(\d+)\s*$", re.I)
CHALLENGE_NAMES = {"defeatwavesofenemies": "Defeat Waves of Enemies", "survive": "Survive",
                   "protectthealtar": "Protect the Altar", "standinthestonecircles": "Stand in the Stone Circles"}
CHALLENGES = {"defeatwavesofenemies": "Exterminate", "survive": "Survival", "protectthealtar": "Defense",
              "standinthestonecircles": "Conquer"}


@dataclass
class Ultimatum:
    challenge: str = ""
    area_level: int = 0
    sacrifice: str = ""
    count: int = 1
    reward: str = ""  # as written: "Doubles sacrificed Currency", a unique's name, ...
    reward_type: str = ""  # DoubleCurrency | DoubleDivCards | MirrorRare | ExchangeUnique
    mods: List[Tuple[str, List[str]]] = field(default_factory=list)  # ("Stalking Ruin II", [trade stat ids])
    nasty: List[Tuple[str, List[str]]] = field(default_factory=list)  # the hard modifiers it has
    nasty_not_ids: List[str] = field(default_factory=list)  # the hard modifiers it doesn't have (NOT group)
    matched: str = ""
    same_count: bool = True  # prices are only listings that ask for the same number of sacrificed items
    errors: List[str] = field(default_factory=list)  # search steps the trade site rejected (with its reason)
    prices: List[Tuple[float, str]] = field(default_factory=list)
    total: int = 0
    url: str = ""


CHALLENGE_OPTIONS = {"Defeat Waves of Enemies": "Exterminate", "Survive": "Survival", "Protect the Altar": "Defense",
                     "Stand in the Stone Circles": "Conquer"}


# The modifiers that decide whether people want an ultimatum (the dangerous, tiered ones)
KEY_MOD_FAMILIES = ("ragingdead", "stalkingruin", "ruin", "stormcallerrunes", "blisteringcold", "chokingmiasma",
                    "razordance", "restlessground", "totemofcostly")
_ROMAN_FIX = re.compile(r"(?<=\s)([IiLl1|]{1,3}|[IiLl1|]?[Vv])\s*$")


def _roman(text: str) -> str:
    """OCR reads 'II' as 'Il' / 'll' / '11': normalise a trailing numeral."""
    return _ROMAN_FIX.sub(lambda m: re.sub(r"[iLl1|]", "I", m.group(1)).upper(), text.strip())


def read_mods(lines: Sequence[str], ultimatum_mods: dict) -> List[Tuple[str, List[str]]]:
    """Modifier names on the tooltip ('RAGING DEAD IV', 'STalking Ruin Il', ...) -> trade site stat ids."""
    import difflib

    out: List[Tuple[str, List[str]]] = []
    keys = list(ultimatum_mods)
    for text in lines:
        if len(text) > 40 or "(" in text:
            continue
        key = squash(_roman(text))
        if key not in ultimatum_mods:
            close = difflib.get_close_matches(key, keys, n=1, cutoff=0.9) if len(key) >= 8 else []
            if not close:
                continue
            key = close[0]
        if all(squash(n) != key for n, _ in out):
            name = _roman(text).title()
            name = re.sub(r"\b(Ii|Iii|Iv)$", lambda m: m.group(1).upper(), name)
            out.append((name, ultimatum_mods[key]))
    return out


def key_mods(mods: Sequence[Tuple[str, List[str]]], limit: int = 4) -> List[Tuple[str, List[str]]]:
    return [m for m in mods if squash(m[0]).startswith(KEY_MOD_FAMILIES)][:limit]


# The modifiers that make an ultimatum hard to finish (and so worth less) - what players exclude with a NOT group
DEFAULT_HARD_MODS = ("Stalking Ruin", "Stalking Ruin II", "Stalking Ruin III", "Stalking Ruin IV", "Ruin II", "Ruin III",
                     "Ruin IV", "Drought", "Reduced Recovery", "Reduced Recovery II", "Escalating Damage Taken",
                     "Escalating Monster Speed")
# your list (⚙ Settings): changed in place, so every module that imported it sees the change
NASTY_MODS: List[str] = list(DEFAULT_HARD_MODS)


def set_hard_mods(names: Sequence[str]) -> None:
    """Which modifiers count as 'hard' for you ("-" alone = none at all)."""
    NASTY_MODS[:] = [str(n) for n in names if str(n).strip() and str(n) != "-"]


def split_nasty(mods: Sequence[Tuple[str, List[str]]], ultimatum_mods: dict) -> Tuple[List[Tuple[str, List[str]]], List[str]]:
    """(nasty modifiers this ultimatum HAS, trade ids of the nasty ones it does NOT have -> for a NOT group)."""
    have_keys = {squash(n) for n, _ in mods}
    has = [(n, ids) for n, ids in mods if squash(n) in {squash(x) for x in NASTY_MODS}]
    missing = [i for name in NASTY_MODS if squash(name) not in have_keys for i in ultimatum_mods.get(squash(name), [])]
    return has, missing


def _field(text: str) -> Optional[Tuple[str, str]]:
    """('sacrifice', 'DIVINE ORB X4') from 'REQUIRES SACRIFICE: …' - also when OCR cut the start ('ACRIFICE: …')."""
    if ":" not in text.replace("：", ":"):
        return None
    label, value = text.replace("：", ":").split(":", 1)
    key = squash(label)
    for name, tails in (("sacrifice", ("sacrifice", "acrifice", "crifice", "rifice")), ("reward", ("reward", "eward", "ward")),
                        ("challenge", ("challenge", "hallenge", "allenge", "llenge")), ("level", ("arealevel", "realevel", "ealevel"))):
        if key.endswith(tails) and value.strip():
            return name, value.strip()
    return None


def is_ultimatum(lines: Sequence[str]) -> bool:
    keys = [squash(t) for t in lines]
    if any(k.endswith("ultimatum") and len(k) <= 20 for k in keys):
        return True
    fields = {f[0] for f in (_field(t) for t in lines) if f}
    return "sacrifice" in fields and "reward" in fields


def parse(lines: Sequence[str]) -> Ultimatum:
    u = Ultimatum()
    for text in lines:
        f = _field(text)
        if not f:
            continue
        name, value = f
        if name == "challenge":
            u.challenge = CHALLENGE_NAMES.get(squash(value), value.title())
        elif name == "level":
            digits = re.findall(r"\d+", value)
            u.area_level = int(digits[0]) if digits else 0
        elif name == "sacrifice":
            c = _COUNT_RE.search(value)
            if c:
                u.count = int(c.group(1))
                value = value[: c.start()].strip()
            u.sacrifice = value
        elif name == "reward":
            u.reward = value
    key = squash(u.reward)
    # tolerant: OCR can cut letters ("DOUBLESSACRIFICEDCURRENC")
    if "divination" in key or "cards" in key:
        u.reward_type = "DoubleDivCards"
    elif "mirror" in key:
        u.reward_type = "MirrorRare"
    elif "currenc" in key or key.startswith("doubles"):
        u.reward_type = "DoubleCurrency"
    elif key:
        u.reward_type = "ExchangeUnique"
    return u


def proper_name(idx, text: str) -> str:
    """'SACRED ORB' (OCR, small caps) -> 'Sacred Orb' via the price list; otherwise title case."""
    key = idx.lookup_line(text) if idx is not None and text else None
    return idx.by_key[key][0].name if key else text.title()


def best_price(idx, name: str) -> Optional[float]:
    key = idx.lookup_line(name) if idx is not None and name else None
    if not key:
        return None
    entries = idx.by_key[key]
    # currency/cards: the one price; uniques: the unlinked / most listed variant
    return sorted(entries, key=lambda e: (e.kind != "currency", -e.listings))[0].chaos


def _body(u: Ultimatum, nasty: str = "exact") -> dict:
    """
    Always: challenge type + reward type + required item (+ reward unique), like a hand-made trade search.
    nasty='exact': the same hard modifiers as this ultimatum AND a NOT group of the hard ones it doesn't have;
    'has': only the ones it has; 'none': no modifier filter.
    """
    filters: dict = {}
    if u.challenge in CHALLENGE_OPTIONS:
        filters["ultimatum_challenge"] = {"option": CHALLENGE_OPTIONS[u.challenge]}
    if u.reward_type:
        filters["ultimatum_reward"] = {"option": u.reward_type}
    if u.sacrifice:
        filters["ultimatum_input"] = {"option": u.sacrifice}
    if u.reward_type == "ExchangeUnique" and u.reward:
        filters["ultimatum_output"] = {"option": u.reward}
    query: dict = {"status": {"option": ULTIMATUM_STATUS}, "type": "Inscribed Ultimatum",
                   "filters": {"ultimatum_filters": {"filters": filters}}}
    stats = []
    if nasty in ("exact", "has") and u.nasty:
        # a name that exists twice on the trade site has both ids in the group; count = how many it has
        stats.append({"type": "count", "value": {"min": len(u.nasty)},
                      "filters": [{"id": i} for _, ids in u.nasty for i in ids]})
    if nasty == "exact" and u.nasty_not_ids:
        stats.append({"type": "not", "filters": [{"id": i} for i in u.nasty_not_ids]})
    if stats:
        query["stats"] = stats
    return {"query": query, "sort": {"price": "asc"}}


def describe_nasty(u: Ultimatum) -> str:
    return ("with " + ", ".join(n for n, _ in u.nasty)) if u.nasty else "without hard modifiers"


def search_listings(u: Ultimatum, league: str, post=None, get=None, enough: int = 3) -> Ultimatum:
    """
    Max 3 searches, challenge + reward + required item always set:
    same hard modifiers + NOT the others -> same hard modifiers -> no modifier filter.
    """
    post, get = post or _http_post, get or _http_get
    steps = []
    if u.nasty or u.nasty_not_ids:
        steps.append((f"same challenge, reward & item, {describe_nasty(u)}", "exact"))
    if u.nasty:
        steps.append((f"same challenge, reward & item, also {', '.join(n for n, _ in u.nasty)}", "has"))
    steps.append(("same challenge, reward & item (any modifiers)", "none"))
    lg = urllib.parse.quote(league)
    res, u.matched, u.errors = try_steps(post, f"{TRADE}/search/{lg}", [(lbl, _body(u, mode)) for lbl, mode in steps], enough)
    u.total = int(res.get("total", 0))
    u.url = f"https://www.pathofexile.com/trade/search/{lg}/{res.get('id', '')}"
    # The trade site can't filter by how MANY items are sacrificed (Divine Orb x1 vs x4): fetch up to 30 listings
    # (cheapest first) and keep the ones asking for the same number
    ids = res.get("result", [])[:30]  # at most 3 fetch pages per check (trade site limits)
    same, other = [], []
    for i in range(0, len(ids), 10):
        try:
            fetched = get(f"{TRADE}/fetch/{','.join(ids[i:i + 10])}?query={res.get('id', '')}")
        except TradeBusy:
            u.errors.append("stopped fetching early to stay under the trade site's limit")
            break
        for r in fetched.get("result", []):
            price = ((r or {}).get("listing") or {}).get("price") or {}
            if price.get("amount") is None:
                continue
            entry = (float(price["amount"]), str(price.get("currency", "")))
            (same if _listing_count(r) in (u.count, None) else other).append(entry)
    u.same_count = bool(same) or not other
    u.prices = same if same else other
    return u


def _listing_count(r: dict) -> Optional[int]:
    """Sacrifice count of a fetched listing: properties 'Requires Sacrifice: {0} {1}' -> [['Divine Orb', 18], ['x4', 0]]."""
    for p in ((r or {}).get("item") or {}).get("properties", []):
        if "Sacrifice" in str(p.get("name", "")):
            for v in p.get("values", []):
                m = re.match(r"^\s*[x×]\s*(\d+)\s*$", str(v[0]) if v else "")
                if m:
                    return int(m.group(1))
    return None


def to_html(u: Ultimatum, idx) -> str:
    """Compact: one line what it is, one line sacrifice -> reward with values, one line listings."""
    esc = html.escape
    fmt = idx.fmt if idx is not None else (lambda c: f"{c:,.0f}c")
    head = f"<b style='color:#fde68a;'>Inscribed Ultimatum</b> <span style='color:#9ca3af;'>· {esc(u.challenge)}" \
           + (f" · lvl {u.area_level}" if u.area_level else "") + "</span>"
    sac_price = best_price(idx, u.sacrifice)
    sac = f"{u.count}× {esc(u.sacrifice)}" + (f" ({esc(fmt(sac_price * u.count))})" if sac_price is not None else "")
    if u.reward_type in ("DoubleCurrency", "DoubleDivCards"):
        rew = f"{u.count * 2}× {esc(u.sacrifice)}"
        if sac_price is not None:
            rew += f" ({esc(fmt(sac_price * u.count * 2))}) · <b style='color:#6ee7b7;'>+{esc(fmt(sac_price * u.count))} if you win</b>"
    elif u.reward_type == "ExchangeUnique":
        rew_price = best_price(idx, u.reward)
        rew = esc(u.reward) + (f" ({esc(fmt(rew_price))})" if rew_price is not None else "")
        if rew_price is not None and sac_price is not None:
            gain = rew_price - sac_price * u.count
            color = "#6ee7b7" if gain >= 0 else "#fca5a5"
            rew += f" · <b style='color:{color};'>{'+' if gain >= 0 else '−'}{esc(fmt(abs(gain)))}</b>"
    elif u.reward_type == "MirrorRare":
        rew = "a mirrored copy of the sacrificed rare"
    else:
        rew = esc(u.reward or "?")
    lines = [head, f"{sac} → {rew}"]
    if u.prices:
        div = idx.divine_chaos if idx is not None else 0
        values = sorted(c for c in (a if cur == "chaos" else a * div if cur == "divine" and div else None
                                    for a, cur in u.prices) if c is not None)
        what = f"{u.count}× {esc(u.sacrifice)}" if u.same_count else "any amount (none with yours listed)"
        if values:
            median = values[len(values) // 2]
            lines.append(f"Listed ({what}): <b>median {esc(fmt(median))}</b> · low {esc(fmt(values[0]))} · "
                         f"high {esc(fmt(values[-1]))} <span style='color:#6b7280;'>({len(values)} · {esc(u.matched)})</span>")
    elif u.url:
        lines.append("<span style='color:#9ca3af;'>No listings with this sacrifice + reward yet</span>")
    return "<br>".join(lines)
