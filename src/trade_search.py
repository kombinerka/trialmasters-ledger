"""
Price estimate for rare items (e.g. a rare Heist trinket in a curio display) via the official trade site:
the affixes read from the tooltip become a pathofexile.com trade search, and the cheapest listings are shown.
Like Awakened PoE Trade - one search per key press, only the official website, nothing sent to the game.

If too few items match, the search is relaxed: all affixes (at ~90% of your values) -> all but one -> base type only.
Qt-free so it can be unit-tested.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

TRADE = "https://www.pathofexile.com/api/trade"
UA = "trialmasters-ledger/1.0 (+https://github.com/kombinerka/trialmasters-ledger)"
Post = Callable[[str, dict], dict]
Get = Callable[[str], dict]

_RANGE_RE = re.compile(r"\(\s*[-+]?\d+(?:\.\d+)?\s*-\s*[-+]?\d+(?:\.\d+)?\s*\)")
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_SKIP_RE = re.compile(r"(prefix|suffix|implicit|unique)\s*modifier|item\s*level|requires|corrupted|contraband|curio", re.I)
_PROPERTY_RE = re.compile(r"^[^:%]{3,40}:\s*[-+]?\d")  # "Critical Strike Chance: 8.50%" is a property, not an affix
_IMPLICIT_HEADER = re.compile(r"implicit\s*modifier", re.I)
_AFFIX_HEADER = re.compile(r"(prefix|suffix)\s*modifier", re.I)
# Item class line in the tooltip -> trade site category (for "any item of this kind" when the base has no listings)
CATEGORIES = {
    "runedagger": "weapon.runedagger", "dagger": "weapon.dagger", "claw": "weapon.claw", "wand": "weapon.wand",
    "sceptre": "weapon.sceptre", "staff": "weapon.staff", "warstaff": "weapon.warstaff", "bow": "weapon.bow",
    "onehandedsword": "weapon.onesword", "twohandedsword": "weapon.twosword", "onehandedaxe": "weapon.oneaxe",
    "twohandedaxe": "weapon.twoaxe", "onehandedmace": "weapon.onemace", "twohandedmace": "weapon.twomace",
    "thrustingonehandedsword": "weapon.onesword", "bodyarmour": "armour.chest", "helmet": "armour.helmet",
    "gloves": "armour.gloves", "boots": "armour.boots", "shield": "armour.shield", "quiver": "armour.quiver",
    "amulet": "accessory.amulet", "ring": "accessory.ring", "belt": "accessory.belt",
}


class TradeBusy(RuntimeError):
    """The trade site asked us to slow down (HTTP 429)."""


class TradeQueryError(RuntimeError):
    """The trade site rejected a search (HTTP 400) - carries its reason, e.g. 'Query is too complex'."""


logger = logging.getLogger("poe_companion")

# Inscribed Ultimatums: only Instant Buyout listings – whisper-only ones are mostly bait/price-fixing
ULTIMATUM_STATUS = "securable"


LONG_SHARE = 0.75  # in the hours-long windows (600 searches / 3 h) a background scan may use this much


class RateLimiter:
    """
    Keeps the companion under pathofexile.com's trade limits (exceeding them blocks your IP, e.g. 30 searches per
    5 minutes -> 30 minutes). Uses the limits and the *current counters* the site sends with every answer - those
    counters also include other tools on your PC (Awakened PoE Trade) - and stops one request short of each limit.
    """

    # used until the site has told us its real rules: hits per period (s) -> penalty (s)
    DEFAULT_RULES = {"search": [(5, 10, 60), (15, 60, 300), (30, 300, 1800)],
                     "fetch": [(12, 4, 10), (16, 12, 300), (50, 300, 300)]}

    def __init__(self) -> None:
        self.rules: Dict[str, List[Tuple[int, int, int]]] = {}
        self.server: Dict[str, Tuple[float, List[Tuple[int, int, int]]]] = {}  # policy -> (seen at, [(hits, period, restricted)])
        # logged in (POESESSID): the site also limits per account
        self.acc_rules: Dict[str, List[Tuple[int, int, int]]] = {}
        self.acc_server: Dict[str, Tuple[float, List[Tuple[int, int, int]]]] = {}
        self.local: Dict[str, List[float]] = {}
        self.blocked_until = 0.0
        self.state_file: Optional[Path] = None  # remembers a block across restarts

    def use_state_file(self, path: Path) -> None:
        self.state_file = Path(path)
        try:
            self.blocked_until = max(self.blocked_until, float(self.state_file.read_text(encoding="utf-8").strip() or 0))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        if self.state_file is not None:
            try:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                self.state_file.write_text(str(self.blocked_until), encoding="utf-8")
            except OSError:
                pass

    def smooth_gap(self, url: str, share: float) -> float:
        """
        Seconds between requests so a long-running caller spreads its part of the hours-long windows evenly
        (instead of using it up in one hour and then waiting two).
        """
        pol = self.policy_for(url)
        rules = (self.rules.get(pol) or self.DEFAULT_RULES.get(pol, [])) + self.acc_rules.get(pol, []) if pol else []
        gaps = [period / max(1.0, hits * max(share, LONG_SHARE)) for hits, period, _p in rules if period >= 3600]
        return max(gaps, default=0.0)

    @staticmethod
    def policy_for(url: str) -> Optional[str]:
        return "search" if "/trade/search/" in url else "fetch" if "/trade/fetch/" in url else None

    def wait_needed(self, url: str, now: Optional[float] = None, reserve: int = 1, share: float = 1.0) -> float:
        """
        Seconds to wait before this request is safe (0 = go); `reserve` requests stay spare for other uses.
        `share` < 1 caps the caller to that part of every window (the background scan uses only half).
        """
        now = time.time() if now is None else now
        if now < self.blocked_until:
            return self.blocked_until - now
        pol = self.policy_for(url)
        if not pol:
            return 0.0
        mine = [t for t in self.local.get(pol, []) if now - t < 3600]
        self.local[pol] = mine
        rules = self.rules.get(pol) or self.DEFAULT_RULES[pol]
        wait = self._wait_for(rules, self.server.get(pol, (0.0, [])), mine, now, reserve, share)
        if pol in self.acc_rules:
            wait = max(wait, self._wait_for(self.acc_rules[pol], self.acc_server.get(pol, (0.0, [])), mine, now, reserve, share))
        return wait

    @staticmethod
    def _wait_for(rules, server_entry, mine: List[float], now: float, reserve: int, share: float = 1.0) -> float:
        seen_at, state = server_entry
        server_hits = {period: hits for hits, period, _r in state}
        wait = 0.0
        for hits, period, _penalty in rules:
            recent = [t for t in mine if now - t < period]
            used = len(recent)
            if now - seen_at < period:  # the site's own count (includes other tools), plus ours since then
                used = max(used, server_hits.get(period, 0) + len([t for t in recent if t > seen_at]))
            part = share if period < 3600 or share >= 1 else max(share, LONG_SHARE)
            if used >= min(hits - reserve, int(hits * part)):  # always keep `reserve` requests spare
                oldest = min(recent) if recent else seen_at
                wait = max(wait, period - (now - oldest) + 1)
        return wait

    def before(self, url: str) -> None:
        wait = self.wait_needed(url)
        if wait > 0:
            raise TradeBusy(f"holding back to stay under the trade site's limit – try again in {int(wait) + 1} s")
        pol = self.policy_for(url)
        if pol:
            self.local.setdefault(pol, []).append(time.time())

    @staticmethod
    def _parse(value: str) -> List[Tuple[int, int, int]]:
        out = []
        for part in (value or "").split(","):
            bits = part.strip().split(":")
            if len(bits) == 3 and all(b.strip().isdigit() for b in bits):
                out.append(tuple(int(b) for b in bits))  # type: ignore[arg-type]
        return out

    def after(self, url: str, headers, status: int = 200) -> None:
        pol = self.policy_for(url)
        now = time.time()
        if headers is not None:
            rules = self._parse(headers.get("X-Rate-Limit-Ip", ""))
            state = self._parse(headers.get("X-Rate-Limit-Ip-State", ""))
            if pol and rules:
                if self.rules.get(pol) != rules:
                    logger.info("Trade site %s limits (hits:seconds:penalty): IP %s%s", pol, rules,
                                f", account {self._parse(headers.get('X-Rate-Limit-Account', ''))}"
                                if headers.get("X-Rate-Limit-Account") else "")
                self.rules[pol] = rules
            acc_rules = self._parse(headers.get("X-Rate-Limit-Account", ""))
            acc_state = self._parse(headers.get("X-Rate-Limit-Account-State", ""))
            if pol and acc_rules:
                self.acc_rules[pol] = acc_rules
            if pol and acc_state:
                self.acc_server[pol] = (now, acc_state)
            if pol and (state or acc_state):
                if state:
                    self.server[pol] = (now, state)
                restricted = max((r for _h, _p, r in state + acc_state), default=0)
                if restricted:
                    self.blocked_until = max(self.blocked_until, now + restricted)
                    self._save()
            if status == 429:
                logger.warning("Trade site 429 on %s: IP %s state %s, account %s state %s", pol,
                               headers.get("X-Rate-Limit-Ip", ""), headers.get("X-Rate-Limit-Ip-State", ""),
                               headers.get("X-Rate-Limit-Account", ""), headers.get("X-Rate-Limit-Account-State", ""))
                try:
                    retry = int(headers.get("Retry-After", "60"))
                except ValueError:
                    retry = 60
                self.blocked_until = max(self.blocked_until, now + retry)
                self._save()


LIMITER = RateLimiter()
SESSION: Dict[str, str] = {"poesessid": ""}  # your own POESESSID if you entered one (never logged or saved here)


def _headers(extra: Optional[dict] = None) -> dict:
    h = {"User-Agent": UA}
    if SESSION.get("poesessid"):
        h["Cookie"] = f"POESESSID={SESSION['poesessid']}"
    h.update(extra or {})
    return h
_CACHE: Dict[str, Tuple[float, dict]] = {}  # same request within 10 min (pressing again on the same item): no new request
CACHE_SECONDS = 600


def _cached(key: str) -> Optional[dict]:
    hit = _CACHE.get(key)
    return hit[1] if hit and time.time() - hit[0] < CACHE_SECONDS else None


def _http_get(url: str) -> dict:
    hit = _cached("GET " + url)
    if hit is not None:
        return hit
    req = urllib.request.Request(url, headers=_headers())
    out = _open(req, url)
    _CACHE["GET " + url] = (time.time(), out)
    return out


def _http_post(url: str, body: dict) -> dict:
    key = "POST " + url + json.dumps(body, sort_keys=True)
    hit = _cached(key)
    if hit is not None:
        return hit
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=_headers({"Content-Type": "application/json"}))
    out = _open(req, url)
    _CACHE[key] = (time.time(), out)
    return out


def _open(req: urllib.request.Request, url: str = "") -> dict:
    LIMITER.before(url)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            LIMITER.after(url, resp.headers)
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        LIMITER.after(url, exc.headers, exc.code)
        if exc.code == 429:
            wait = int(max(0, LIMITER.blocked_until - time.time())) or 60
            raise TradeBusy(f"the trade site has paused searches from your IP for {wait // 60} min {wait % 60} s") from exc
        if exc.code == 400:
            try:
                reason = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001
                reason = ""
            raise TradeQueryError(reason.split("\n")[0] or "bad request") from exc
        raise


def try_steps(post: Post, url: str, bodies: Sequence[Tuple[str, dict]], enough: int) -> Tuple[dict, str, List[str]]:
    """
    Run search steps in order until one finds enough; a rejected step (400) is skipped. If the rate limit would be
    reached, stop and use the best result so far. -> (best, label, errors)
    """
    best: Tuple[dict, str] = ({}, "")
    errors: List[str] = []
    for label, body in bodies:
        try:
            got = post(url, body)
        except TradeQueryError as exc:
            errors.append(f"{label}: {exc}")
            continue
        except TradeBusy as exc:
            if best[1]:
                errors.append(f"stopped early: {exc}")
                break
            raise
        if not best[1] or len(got.get("result", [])) > len(best[0].get("result", [])):
            best = (got, label)
        if len(got.get("result", [])) >= enough:
            break
    if not best[1]:
        raise TradeQueryError("; ".join(errors) or "no search possible")
    return best[0], best[1], errors


def squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


@dataclass
class ReadMod:
    stat_id: str
    text: str
    value: Optional[float]
    numbers: List[float] = field(default_factory=list)  # every number as read, for display

    def display(self) -> str:
        out = self.text
        for n in self.numbers or ([self.value] if self.value is not None else []):
            out = out.replace("#", f"{n:g}", 1)
        return out


@dataclass
class TradeResult:
    base: str
    name: str
    mods: List[ReadMod]
    matched: str  # "all 3 affixes" | "2 of 3 affixes" | "base type only"
    prices: List[Tuple[float, str]] = field(default_factory=list)  # (amount, currency), cheapest first
    total: int = 0
    url: str = ""


class TradeData:
    """The trade site's stat and base type lists (cached for a week)."""

    MAX_AGE = 7 * 24 * 3600

    def __init__(self, stats: dict, items: dict) -> None:
        self.by_group: Dict[str, Dict[str, Tuple[str, str]]] = {"explicit": {}, "implicit": {}}
        for group in stats.get("result", []):
            if group.get("id") not in self.by_group:
                continue
            for e in group.get("entries", []):
                key = squash(e["text"])
                if key:
                    self.by_group[group["id"]].setdefault(key, (e["id"], e["text"]))
        self.stat_by_key = self.by_group["explicit"]
        # Ultimatum modifiers ("Stalking Ruin II"): some names exist twice, so name -> every id
        self.ultimatum_mods: Dict[str, List[str]] = {}
        for group in stats.get("result", []):
            if group.get("id") == "ultimatum":
                for e in group.get("entries", []):
                    self.ultimatum_mods.setdefault(squash(e["text"]), []).append(e["id"])
        self.bases: Dict[str, str] = {}
        for group in items.get("result", []):
            for e in group.get("entries", []):
                if e.get("type") and "name" not in e:
                    self.bases[squash(e["type"])] = e["type"]
        self._keys = list(self.stat_by_key)

    @staticmethod
    def load(cache_dir: Path, get: Optional[Get] = None) -> "TradeData":
        get = get or _http_get
        out = {}
        for name in ("stats", "items"):
            path = Path(cache_dir) / f"trade_{name}.json"
            if path.is_file() and time.time() - path.stat().st_mtime < TradeData.MAX_AGE:
                out[name] = json.loads(path.read_text(encoding="utf-8"))
                continue
            try:
                out[name] = get(f"{TRADE}/data/{name}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(out[name]), encoding="utf-8")
            except Exception:
                if not path.is_file():
                    raise
                out[name] = json.loads(path.read_text(encoding="utf-8"))
        return TradeData(out["stats"], out["items"])

    def _find_stat(self, clean: str, group: str) -> Optional[Tuple[str, str]]:
        key = squash(clean)
        for g in (group, "explicit" if group == "implicit" else "implicit"):
            if key in self.by_group[g]:
                return self.by_group[g][key]
        if len(key) >= 12:
            close = difflib.get_close_matches(key, self._keys, n=1, cutoff=0.9)
            if close:
                return self.by_group["explicit"][close[0]]
        return None

    def read_item(self, lines: Sequence) -> Tuple[str, str, List[ReadMod]]:
        """
        (base type, item class category, affixes) from the OCR'd tooltip lines. Lines can be plain strings or
        ((centre x, centre y), text): then only lines of the tooltip around the base type line are used (PoE centres
        tooltip text), so a second tooltip next to it is ignored.
        """
        items = [(None, ln) if isinstance(ln, str) else (ln[0], ln[1]) for ln in lines]
        base, category, mods = "", "", []
        base_at = None
        for at, raw in items:
            if squash(raw) in self.bases:
                base, base_at = self.bases[squash(raw)], at
                break
        group = "explicit"
        for at, raw in items:
            text = raw.strip()
            if not text or (base_at is not None and at is not None and
                            (abs(at[0] - base_at[0]) > 90 or at[1] < base_at[1] - 60)):
                continue
            if squash(text) in self.bases:
                continue
            if not category and squash(text) in CATEGORIES:
                category = CATEGORIES[squash(text)]
                continue
            if _IMPLICIT_HEADER.search(text):
                group = "implicit"
                continue
            if _AFFIX_HEADER.search(text):
                group = "explicit"
                continue
            if _SKIP_RE.search(text) or _PROPERTY_RE.search(text):
                continue
            clean = _RANGE_RE.sub("", text)  # "25(21-25)%" -> "25%" (advanced mod descriptions)
            stat = self._find_stat(clean, group)
            if stat is None:
                continue
            nums = [float(n) for n in _NUM_RE.findall(clean)]
            holes = stat[1].count("#")
            # "Adds # to # ..." is searched by the average of both numbers (like the trade site does)
            value = (sum(nums[:holes]) / holes if len(nums) >= holes else nums[0]) if nums and holes else None
            same = next((m for m in mods if m.stat_id == stat[0]), None)
            if same is None:
                mods.append(ReadMod(stat[0], stat[1], value, list(nums[:holes]) if holes else []))
            elif holes == 1 and value is not None and same.value is not None:
                # the same stat from two affixes (e.g. 76% + 31% increased Spell Damage): the trade site sums them
                same.value += value
                same.numbers = [same.value]
        return base, category, mods


def _query(base: str, mods: Sequence[ReadMod], need: Optional[int], category: str = "") -> dict:
    filters = []
    for m in mods:
        f: dict = {"id": m.stat_id}
        if m.value is not None:
            f["value"] = {"min": max(1, int(m.value * 0.9)) if m.value >= 1 else m.value}
        filters.append(f)
    query: dict = {"status": {"option": "online"}}
    if base:
        query["type"] = base
    if category:
        query["filters"] = {"type_filters": {"filters": {"category": {"option": category}}}}
    if filters:
        query["stats"] = [{"type": "count", "value": {"min": need}, "filters": filters} if need else {"type": "and", "filters": filters}]
    return {"query": query, "sort": {"price": "asc"}}


def search(league: str, base: str, mods: Sequence[ReadMod], post: Optional[Post] = None, get: Optional[Get] = None,
           enough: int = 3, category: str = "") -> TradeResult:
    """
    At most 4 searches per press (the trade site allows 5 per 10 s): all affixes -> fewer affixes (n-2, at least half)
    -> same with any item of this kind (category) when the exact base isn't listed -> base type only.
    """
    post, get = post or _http_post, get or _http_get
    n = len(mods)
    fewer = max(1, min(n - 1, max(n - 2, (n + 1) // 2)))
    kind = category.split(".")[-1] if category else ""
    steps: List[Tuple[str, Optional[int], Sequence[ReadMod], str, str]] = []
    if mods:
        steps.append((f"all {n} affixes" if n > 1 else "the affix", None, mods, base, ""))
    if n > 1:
        steps.append((f"{fewer} of {n} affixes", fewer, mods, base, ""))
        if category:
            steps.append((f"{fewer} of {n} affixes on any {kind}", fewer, mods, "", category))
    steps.append(("base type only", None, [], base, ""))
    lg = urllib.parse.quote(league)
    res, label, _errors = try_steps(post, f"{TRADE}/search/{lg}",
                                    [(lbl, _query(b, use, need, cat)) for lbl, need, use, b, cat in steps], enough)
    ids = res.get("result", [])[:10]
    out = TradeResult(base, "", list(mods), label, total=int(res.get("total", 0)),
                      url=f"https://www.pathofexile.com/trade/search/{lg}/{res.get('id', '')}")
    if ids:
        try:
            fetched = get(f"{TRADE}/fetch/{','.join(ids)}?query={res.get('id', '')}")
        except TradeBusy:
            fetched = {}
        for r in fetched.get("result", []):
            price = ((r or {}).get("listing") or {}).get("price") or {}
            if price.get("amount") is not None:
                out.prices.append((float(price["amount"]), str(price.get("currency", ""))))
    return out


def to_chaos(amount: float, currency: str, divine_chaos: float) -> Optional[float]:
    if currency == "chaos":
        return amount
    if currency == "divine" and divine_chaos:
        return amount * divine_chaos
    return None
