"""
Slow background scan for profitable Inscribed Ultimatums.

poe.ninja values every sacrifice/reward (no trade-site cost); the official trade site is only asked for the
ultimatum listings themselves: one search per worthwhile sacrifice item (cheapest first, optionally only ultimatums
WITHOUT hard modifiers via a NOT group) and up to 2 pages of listings. Requests are paced and always leave a reserve
for your own price checks (Ctrl+F3) and other tools such as Awakened PoE Trade.

Per listing: profit if you win (reward - sacrifice - price) and loss if you fail (sacrifice + price).
Nothing is sent to the game and nothing is bought - you contact the seller yourself.
Qt-free so it can be unit-tested.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from src.trade_search import LIMITER, TRADE, ULTIMATUM_STATUS, TradeBusy, TradeQueryError, _http_get, _http_post, squash
from src.loot_filter import UNIQUE_TYPES
from src.ultimatum import CHALLENGE_NAMES, NASTY_MODS, _listing_count


@dataclass
class UltimatumDeal:
    listing_id: str
    sacrifice: str
    count: int
    reward: str  # "2× Divine Orb", or a unique's name
    challenge: str
    area_level: int
    price_chaos: float
    price_text: str
    sacrifice_chaos: float
    reward_chaos: float
    seller: str = ""
    whisper: str = ""
    indexed: str = ""
    url: str = ""
    hard_mods: str = ""  # e.g. "Stalking Ruin IV, Drought" – the risky modifiers this ultimatum has
    all_mods: Tuple[str, ...] = ()  # every modifier on it (to re-check when you change your hard-modifier list)
    market: str = ""  # warning about the reward's market, e.g. "~1 sold/day, +1850% this week"

    @property
    def profit(self) -> float:
        return self.reward_chaos - self.sacrifice_chaos - self.price_chaos

    @property
    def loss(self) -> float:
        return self.sacrifice_chaos + self.price_chaos

    @property
    def breakeven(self) -> float:
        """How often you must win for this to pay off on average (0.88 = win 88% or lose money)."""
        total = self.profit + self.loss
        return self.loss / total if total > 0 and self.profit > 0 else 1.0

    @property
    def thin(self) -> bool:
        """The reward's price on poe.ninja isn't backed by real trading – you may not be able to sell it."""
        return bool(self.market)

    @property
    def suspicious(self) -> bool:
        """Price far below what the sacrifice alone is worth (e.g. 1c for 3× The Lord in Black) – a mispriced steal."""
        return self.sacrifice_chaos > 50 and self.price_chaos < self.sacrifice_chaos * 0.02

    @property
    def ratio(self) -> float:
        """Profit per chaos you put at risk (0.5 = win half of what you'd lose on a fail)."""
        return self.profit / self.loss if self.loss > 0 else 0.0


# Sacrifices that are common on Inscribed Ultimatums (the scan also learns the real ones from the listings it sees)
COMMON_SACRIFICES = ("Divine Orb", "Exalted Orb", "Orb of Annulment", "Sacred Orb", "Awakened Sextant",
                     "Orb of Dominance", "Gemcutter's Prism", "Vaal Orb", "Regal Orb", "Chaos Orb")
REWARD_TYPES = ("DoubleCurrency", "DoubleDivCards", "ExchangeUnique")


def candidates(idx, min_chaos: float = 50.0, limit: int = 500, allowed: Optional[Iterable[str]] = None) -> List[tuple]:
    """
    Sacrifice items worth scanning: (name, chaos), most valuable first. Only names that really are ultimatum
    sacrifices (`allowed`: learned from listings + COMMON_SACRIFICES) - a Mirror or a Headhunter never is.
    """
    allow = {squash(n) for n in (allowed if allowed is not None else COMMON_SACRIFICES)}
    out: Dict[str, float] = {}
    for key, entries in idx.by_key.items():
        if key not in allow:
            continue
        best = sorted(entries, key=lambda e: (e.kind != "currency", -e.listings))[0]
        if best.kind in ("currency", "unique") and best.chaos >= min_chaos:
            out[best.name] = best.chaos
    return sorted(out.items(), key=lambda kv: -kv[1])[:limit]


UNRELIABLE_LISTINGS = 10  # a unique with fewer listings has no real market price
THIN_SALES_PER_DAY = 5.0  # fewer of the reward traded per day (poe.ninja) = you'll struggle to sell it
PUMP_CHANGE = 300.0  # price up more than this % in a week = probably pumped, will drop back


def hard_mods_of(mods: Sequence[str]) -> List[str]:
    """The modifiers on your hard list (⚙ Settings) among these."""
    nasty = {squash(n) for n in NASTY_MODS}
    return [m for m in mods if squash(m) in nasty]


def market_warning(entry, amount: int = 1) -> str:
    """'' if the reward sells fine at poe.ninja's price, else a short warning ('~1 sold/day, +1850% this week')."""
    notes = []
    volume = getattr(entry, "volume", -1.0)
    if volume >= 0 and entry.chaos > 0:
        per_day = volume / entry.chaos
        if per_day < max(THIN_SALES_PER_DAY, amount):
            notes.append(f"~{per_day:.0f} sold/day" if per_day >= 1 else "<1 sold/day")
    elif entry.kind == "unique" and 0 < getattr(entry, "listings", 0) < UNRELIABLE_LISTINGS:
        notes.append(f"price unknown – poe.ninja's {entry.chaos:,.0f}c rests on only {entry.listings} listings")
    if getattr(entry, "change", 0) > PUMP_CHANGE:
        notes.append(f"+{entry.change:,.0f}% this week")
    return ", ".join(notes)


def _props(item: dict) -> Dict[str, list]:
    return {str(p.get("name", "")): p.get("values", []) for p in (item or {}).get("properties", [])}


def parse_listing(r: dict, idx, league: str, query_id: str) -> Optional[UltimatumDeal]:
    listing, item = (r or {}).get("listing") or {}, (r or {}).get("item") or {}
    price = listing.get("price") or {}
    if price.get("amount") is None:
        return None
    amount, cur = float(price["amount"]), str(price.get("currency", ""))
    price_chaos = amount if cur == "chaos" else amount * idx.divine_chaos if cur == "divine" and idx.divine_chaos else None
    if price_chaos is None:
        return None
    props = _props(item)
    sac = next((v for k, v in props.items() if "Sacrifice" in k), [])
    reward_vals = next((v for k, v in props.items() if k.startswith("Reward")), [])
    if not sac or not reward_vals:
        return None
    sacrifice = str(sac[0][0])
    count = _listing_count(r) or 1
    sac_key = idx.lookup_line(sacrifice)
    if not sac_key:
        return None
    sac_value = sorted(idx.by_key[sac_key], key=lambda e: (e.kind != "currency", -e.listings))[0].chaos
    reward_text = str(reward_vals[0][0])
    if "doubles" in reward_text.lower():
        reward, reward_chaos = f"{count * 2}× {sacrifice}", sac_value * count * 2
        reward_entry = sorted(idx.by_key[sac_key], key=lambda e: (e.kind != "currency", -e.listings))[0]
    else:
        key = idx.lookup_line(reward_text)
        if not key or "mirror" in reward_text.lower():
            return None  # mirrored rare: value unknown
        reward = idx.by_key[key][0].name
        reward_entry = sorted(idx.by_key[key], key=lambda e: -e.listings)[0]
        reward_chaos = reward_entry.chaos
        if reward_entry.kind == "unique" and 0 < reward_entry.listings < UNRELIABLE_LISTINGS:
            reward_chaos = 0.0  # e.g. Song of the Sirens "59,000c" from 3 listings: a price-fixed number, not a value
    challenge = str((props.get("Challenge") or [[""]])[0][0])
    level = (props.get("Area Level") or [["0"]])[0][0]
    market = market_warning(reward_entry, count * 2 if "doubles" in reward_text.lower() else 1)
    all_mods = tuple(str(m.get("description", "")) if isinstance(m, dict) else str(m)
                     for m in item.get("explicitMods") or [] if isinstance(m, (dict, str)))
    all_mods = tuple(m for m in all_mods if m and not m[0].isdigit())  # skip "50% increased Monster Damage" etc.
    hard = hard_mods_of(all_mods)
    return UltimatumDeal(
        listing_id=str(r.get("id", "")), sacrifice=sacrifice, count=count, reward=reward,
        challenge=CHALLENGE_NAMES.get(squash(challenge), challenge.title()),
        area_level=int(level) if str(level).isdigit() else 0,
        price_chaos=price_chaos, price_text=f"{amount:g} {cur}", sacrifice_chaos=sac_value * count,
        reward_chaos=reward_chaos, seller=str((listing.get("account") or {}).get("name", "")),
        whisper=str(listing.get("whisper", "")), indexed=str(listing.get("indexed", ""))[:16].replace("T", " "),
        url=f"https://www.pathofexile.com/trade/search/{urllib.parse.quote(league)}/{query_id}",
        hard_mods=", ".join(hard), market=market, all_mods=all_mods,
    )


def seller_search_url(deal: UltimatumDeal, league: str, post=None, not_ids: Sequence[str] = ()) -> str:
    """
    A trade-site search showing just this seller's ultimatums with this sacrifice, so the listing is right on top -
    there you use the site's own "Travel to Hideout" / whisper yourself. One search per click.
    """
    post = post or _http_post
    filters: dict = {"ultimatum_filters": {"filters": {"ultimatum_input": {"option": deal.sacrifice}}}}
    if deal.seller:
        filters["trade_filters"] = {"filters": {"account": {"input": deal.seller}}}
    query: dict = {"status": {"option": ULTIMATUM_STATUS}, "type": "Inscribed Ultimatum", "filters": filters}
    if not_ids:  # same NOT group as the scan: the hard modifiers stay excluded in your browser too
        query["stats"] = [{"type": "not", "filters": [{"id": i} for i in not_ids]}]
    body = {"query": query, "sort": {"price": "asc"}}
    lg = urllib.parse.quote(league)
    try:
        res = post(f"{TRADE}/search/{lg}", body)
    except TradeQueryError:
        if not not_ids:
            raise
        query.pop("stats", None)  # too big without a login: open it without the NOT group
        res = post(f"{TRADE}/search/{lg}", body)
    return f"https://www.pathofexile.com/trade/search/{lg}/{res.get('id', '')}" if res.get("id") else deal.url


def load_unique_pool(path: Optional[Path] = None) -> Dict[str, bool]:
    """Uniques Inscribed Ultimatums are known to use (poedb.tw tier list): squashed name -> can be the sacrifice."""
    path = path or Path(__file__).resolve().parent.parent / "data" / "ultimatum_uniques.json"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {squash(u["name"]): bool(u.get("sacrifice")) for u in data.get("uniques", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


class UltimatumScanner:
    """Runs in its own thread; reports deals and status through callbacks (the window turns them into Qt signals)."""

    SEARCH_RESERVE = 4  # searches always left for Ctrl+F3 / Awakened PoE Trade
    FETCH_RESERVE = 4
    MIN_GAP = 8.0  # seconds between the scan's own requests
    SHARE = 0.5  # the scan uses at most half of every trade-site window; the rest stays yours (Ctrl+F3, browser, APT)
    CYCLE_PAUSE = 5 * 60  # after a full pass, wait before rescanning
    PROBE_INPUTS = 4  # per pass: untried cards / uniques / currency tried once as a sacrifice
    PROBE_OUTPUTS = 8  # per pass – the reward side is where the big margins are: expensive uniques tried once as the *reward* of "exchange a unique"
    PROBE_MIN_CHAOS = 20.0  # cheaper things can't hide a big margin
    PROBE_MAX_CHAOS = 3000.0  # chase items (Mirror, Headhunter, Mageblood …) are never ultimatum sacrifices
    PROBE_MAX_UNIQUE = 400.0  # unique sacrifices seen on the market are all cheap ones
    # every pass reads the cheapest listings plus one rotating price slice, so passes cover different listings
    CHEAP_PAGES = 3  # listings read (×10) from the cheapest end of each reward type
    FETCH_GAP = 3.0  # seconds between listing reads (their own, looser limit)
    CHASE_FLOOR = 2000.0  # chaos: ultimatums priced from here are read every pass (chase uniques live there)
    DISCOVERY_BANDS = (20.0, 60.0, 150.0, 400.0, 1000.0)
    PROBE_AGAIN_DAYS = 1  # an item that had no listings is tried again after this
    PROBE_GROUPS = ("Currency", "DivinationCard") + UNIQUE_TYPES

    def __init__(self, get_index: Callable[[], object], get_ultimatum_mods: Callable[[], dict],
                 on_deals: Callable[[str, List[UltimatumDeal]], None], on_status: Callable[[str], None],
                 post=None, get=None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.get_index, self.get_ultimatum_mods = get_index, get_ultimatum_mods
        self.on_deals, self.on_status = on_deals, on_status
        self.post, self.get = post or _http_post, get or _http_get
        self.sleep = sleep
        self.min_chaos = 5.0
        self.only_clean = True
        self.pages = 3
        self.discovery_pages = 10  # fetches are cheap compared to searches: read up to 100 listings per broad search
        self.min_potential = 100.0
        self.pass_no = 0  # skip sacrifices whose doubled stack can't be worth this much more (poe.ninja)
        self.known_file: Optional[Path] = None  # learned sacrifice names (kept between runs)
        self.known: Dict[str, int] = {}
        self.probed: Dict[str, float] = {}
        self.stacks: Dict[str, int] = {}
        self.rewards: Dict[str, int] = {}  # unique rewards seen on the market (searched directly every pass)
        self.pool = load_unique_pool()
        self.dry: Dict[str, dict] = {}  # search -> {"n": empty runs in a row, "until": skipped until}  # sacrifice -> biggest stack seen (8× Divine, 3× The Apothecary …)  # "in:Name" / "out:Name" -> time tried (0 listings)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------- control ----------
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---------- pacing ----------
    def _wait_turn(self, url: str, reserve: int) -> bool:
        """Sleep until the request is safe (keeping `reserve` spare). False if stopped meanwhile."""
        last = getattr(self, "_last_request", 0.0)
        while not self._stop.is_set():
            gap = max(self.MIN_GAP, LIMITER.smooth_gap(url, self.SHARE)) if "/search/" in url else self.FETCH_GAP
            wait = max(LIMITER.wait_needed(url, reserve=reserve, share=self.SHARE), gap - (time.time() - last))
            if wait <= 0:
                self._last_request = time.time()
                return True
            self.on_status(f"⏳ pacing for the trade site's limit – next request in {int(wait) + 1} s")
            self.sleep(min(wait, 5.0))
        return False

    # ---------- learned sacrifices ----------
    def load_known(self, path: Path) -> None:
        self.known_file = Path(path)
        try:
            self.known = {str(k): int(v) for k, v in json.loads(self.known_file.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, AttributeError):
            self.known = {}
        try:
            self.probed = {str(k): float(v) for k, v in json.loads(self._probed_file().read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, AttributeError):
            self.probed = {}
        try:
            self.dry = {str(k): dict(v) for k, v in json.loads(self._dry_file().read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, AttributeError, TypeError):
            self.dry = {}
        try:
            self.rewards = {str(k): int(v) for k, v in json.loads(self._rewards_file().read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, AttributeError):
            self.rewards = {}
        try:
            self.stacks = {str(k): int(v) for k, v in json.loads(self._stacks_file().read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, AttributeError):
            self.stacks = {}

    def _rewards_file(self) -> Path:
        return (self.known_file or Path("ultimatum_sacrifices.json")).with_name("ultimatum_rewards.json")

    def _stacks_file(self) -> Path:
        return (self.known_file or Path("ultimatum_sacrifices.json")).with_name("ultimatum_stacks.json")

    def _probed_file(self) -> Path:
        return (self.known_file or Path("ultimatum_sacrifices.json")).with_name("ultimatum_probed.json")

    def _mark_probed(self, key: str) -> None:
        self.probed[key] = time.time()
        if self.known_file is not None:
            try:
                self._probed_file().write_text(json.dumps(self.probed, indent=0), encoding="utf-8")
            except OSError:
                pass

    def reward_targets(self, idx, limit: int = 12) -> List[tuple]:
        """(unique reward, chaos) seen on the market and worth >= min_potential, priciest first."""
        out = []
        for name in self.rewards:
            key = idx.lookup_line(name) if hasattr(idx, "lookup_line") else squash(name)
            entries = idx.by_key.get(key or "", [])
            if entries:
                value = sorted(entries, key=lambda e: -e.listings)[0].chaos
                if value >= self.min_potential:
                    out.append((name, value))
        return sorted(out, key=lambda kv: -kv[1])[:limit]

    def ranked_targets(self, idx) -> List[tuple]:
        """(sacrifice, potential chaos) worth a targeted search: value × biggest stack seen >= min_potential."""
        out = []
        for name, value in candidates(idx, self.min_chaos, allowed=set(self.known) | set(COMMON_SACRIFICES)):
            potential = value * max(1, self.stacks.get(name, 1))
            if potential >= self.min_potential:
                out.append((name, potential))
        return sorted(out, key=lambda kv: -kv[1])

    def probe_candidates(self, idx) -> Tuple[List[tuple], List[tuple]]:
        """
        Lesser-known items nobody has shown us as a sacrifice yet: (inputs, outputs), most valuable first.
        Inputs: cards / uniques / currency worth >= PROBE_MIN_CHAOS. Outputs: the priciest uniques, searched as the
        reward of "exchange a unique" (finds cheap uniques that turn into expensive ones).
        """
        known = {squash(k) for k in self.known} | {squash(k) for k in COMMON_SACRIFICES}
        again = time.time() - self.PROBE_AGAIN_DAYS * 86400
        best: Dict[str, tuple] = {}
        for key, entries in idx.by_key.items():
            for e in entries:
                if e.name.startswith("Foulborn "):  # league variants: the trade site rejects them as input
                    continue
                if e.group in self.PROBE_GROUPS and e.chaos >= max(self.PROBE_MIN_CHAOS, self.min_chaos):
                    if key not in best or e.chaos > best[key][1]:
                        best[key] = (e.name, e.chaos, e.kind)
        rewards = {squash(r) for r in self.rewards}

        def can_be_sacrifice(key: str, value: float, kind: str) -> bool:
            if kind != "unique":
                return value <= self.PROBE_MAX_CHAOS
            if self.pool:  # known ultimatum uniques only (poedb list); others never show up
                return self.pool.get(key, False) and value <= self.PROBE_MAX_CHAOS
            return value <= self.PROBE_MAX_UNIQUE

        ins = [(n, c) for k, (n, c, kind) in best.items()
               if k not in known and can_be_sacrifice(k, c, kind) and self.probed.get(f"in:{n}", 0) < again]
        # rewards: the poedb pool first (any value), then other uniques worth a lot (e.g. newer ones like
        # Defiance of Destiny that the old list misses)
        outs = [(n, c + (1e9 if k in self.pool else 0)) for k, (n, c, kind) in best.items()
                if kind == "unique" and k not in rewards and (k in self.pool or c >= 500)
                and self.probed.get(f"out:{n}", 0) < again]
        by_value = lambda kv: -kv[1]  # noqa: E731
        outs = [(n, c % 1e9 if c >= 1e9 else c) for n, c in sorted(outs, key=by_value)[: self.PROBE_OUTPUTS]]
        return sorted(ins, key=by_value)[: self.PROBE_INPUTS], outs

    def _learn(self, deals: Sequence[UltimatumDeal]) -> None:
        for d in deals:
            self.known[d.sacrifice] = self.known.get(d.sacrifice, 0) + 1
            self.stacks[d.sacrifice] = max(self.stacks.get(d.sacrifice, 0), d.count)
            if "×" not in d.reward:  # a unique reward ("2× Divine Orb" is just the doubled stack)
                self.rewards[d.reward] = self.rewards.get(d.reward, 0) + 1
        if self.known_file is not None:
            try:
                self.known_file.parent.mkdir(parents=True, exist_ok=True)
                self.known_file.write_text(json.dumps(self.known, indent=0), encoding="utf-8")
                self._stacks_file().write_text(json.dumps(self.stacks, indent=0), encoding="utf-8")
                self._rewards_file().write_text(json.dumps(self.rewards, indent=0), encoding="utf-8")
            except OSError:
                pass

    # ---------- scanning ----------
    def _query(self, sacrifice: str, not_ids: Sequence[str], reward_type: str = "", output: str = "",
               min_price: float = 0.0, newest: bool = False) -> dict:
        uf: dict = {}
        if output:
            uf["ultimatum_output"] = {"option": output}
        if sacrifice:
            uf["ultimatum_input"] = {"option": sacrifice}
        if reward_type:
            uf["ultimatum_reward"] = {"option": reward_type}
        query: dict = {"status": {"option": ULTIMATUM_STATUS}, "type": "Inscribed Ultimatum",
                       "filters": {"ultimatum_filters": {"filters": uf}}}
        if min_price > 0:  # price slice (chaos equivalent)
            query["filters"]["trade_filters"] = {"filters": {"price": {"min": min_price}}}
        if not_ids:
            query["stats"] = [{"type": "not", "filters": [{"id": i} for i in not_ids]}]
        return {"query": query, "sort": {"indexed": "desc"} if newest else {"price": "asc"}}

    def scan_item(self, idx, league: str, sacrifice: str, not_ids: Sequence[str], reward_type: str = "",
                  pages: Optional[int] = None, output: str = "", min_price: float = 0.0,
                  newest: bool = False) -> List[UltimatumDeal]:
        lg = urllib.parse.quote(league)
        url = f"{TRADE}/search/{lg}"
        if not self._wait_turn(url, self.SEARCH_RESERVE):
            return []
        try:
            res = self.post(url, self._query(sacrifice, not_ids, reward_type, output, min_price, newest))
        except TradeQueryError as exc:
            if "unknown" in str(exc).lower():  # the site doesn't know this item as input/output: retrying won't help
                raise
            if not not_ids or not self._wait_turn(url, self.SEARCH_RESERVE):
                return []
            res = self.post(url, self._query(sacrifice, [], reward_type, output, min_price, newest))  # NOT group too big logged out
        self.last_total = int(res.get("total") or len(res.get("result", [])))
        ids, qid, deals = res.get("result", [])[: 10 * (pages or self.pages)], res.get("id", ""), []
        for i in range(0, len(ids), 10):
            furl = f"{TRADE}/fetch/{','.join(ids[i:i + 10])}?query={qid}"
            if not self._wait_turn(furl, self.FETCH_RESERVE):
                break
            for r in self.get(furl).get("result", []):
                deal = parse_listing(r, idx, league, qid)
                if deal:
                    deals.append(deal)
        return deals

    def run_once(self) -> int:
        idx = self.get_index()
        # everything is scanned; hard modifiers are marked on each deal and the window hides them if you want
        # (a NOT group in the search would hide e.g. every Defiance of Destiny ultimatum - they all have Ruin)
        not_ids: List[str] = []
        found = 0
        # 1) discovery: per reward type the cheapest listings, plus a price slice that rotates every pass
        band = self.DISCOVERY_BANDS[self.pass_no % len(self.DISCOVERY_BANDS)]
        self.pass_no += 1
        broad = [(rtype, 0.0) for rtype in REWARD_TYPES] + [(rtype, band) for rtype in REWARD_TYPES]
        # every pass also the expensive end of "exchange a unique": chase rewards (Defiance of Destiny, Headhunter …)
        # cost many divines, so this is where they show up – whichever unique it is
        chase = [("ExchangeUnique", self.CHASE_FLOOR)]

        def discover(batch, first: int, total: int) -> int:
            got = 0
            for n, (rtype, floor) in enumerate(batch, start=first):
                if self._stop.is_set():
                    break
                what = f"from {floor:,.0f}c" if floor else "cheapest"
                self.on_status(f"🔎 discovery {n}/{total}: {rtype} ultimatums, {what}")
                try:
                    # the cheapest listings rarely clear a 100c bar: read fewer of them, more of the pricier slices
                    deals = self.scan_item(idx, idx.league, "", not_ids, reward_type=rtype, min_price=floor,
                                           pages=self.discovery_pages if floor else self.CHEAP_PAGES)
                except TradeBusy as exc:
                    self.on_status(f"⏸ {exc}")
                    self.sleep(min(max(5.0, LIMITER.blocked_until - time.time()), 60.0))
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.on_status(f"⚠ discovery {rtype}: {exc}")
                    continue
                self._learn(deals)
                got += len(deals)
                self.on_deals(f"(discovery {rtype} {what})", deals)
            return got

        # the big-money searches first (chase uniques, then the targeted ones below), the broad sweeps last
        found += discover(chase, 1, len(broad) + 1)
        # 2) targeted: every real sacrifice worth enough, most valuable first
        # poe.ninja first: what a won ultimatum can add at most = the sacrifice stack's value (it gets doubled).
        # Only sacrifices where that's worth a trade search, biggest potential first.
        items = self.ranked_targets(idx)
        jobs = []
        for n, (name, pot) in enumerate(items, 1):
            jobs.append((f"{n}/{len(items)}: {name} (up to +{pot:,.0f}c)", name, name, "", "", 0.0))
            # bigger stacks (4× / 8× Divine …) are priced above the cheap 1× ones and never make the cheapest 30:
            # one extra search per stack range, starting at that stack's price level
            value, top = pot / max(1, self.stacks.get(name, 1)), self.stacks.get(name, 1)
            for m in (1, 3, 6):
                if m < top and value * (m + 1) >= self.min_potential:
                    jobs.append((f"{n}/{len(items)}: {name}, bigger stacks (from {value * m:,.0f}c)",
                                 f"{name} (from {m}×)", name, "", "", value * m))
        # unique rewards already seen on the market, priciest first: re-checked every pass
        outs = self.reward_targets(idx)
        jobs += [(f"reward {n}/{len(outs)}: what turns into {name} ({value:,.0f}c)", f"→ {name}", "", name, "", 0.0)
                 for n, (name, value) in enumerate(outs, 1)]
        # 3) probes: lesser-known cards / uniques nobody has listed for us yet, and uniques as the reward
        ins, outs = self.probe_candidates(idx)
        jobs += [(f"probe {n}/{len(ins)}: {name} ({value:,.0f}c) as sacrifice", name, name, "", f"in:{name}", 0.0)
                 for n, (name, value) in enumerate(ins, 1)]
        jobs += [(f"probe {n}/{len(outs)}: what turns into {name} ({value:,.0f}c)", f"→ {name}", "", name, f"out:{name}", 0.0)
                 for n, (name, value) in enumerate(outs, 1)]
        complete: set = set()  # sacrifices whose plain search already returned every listing there is
        now = time.time()
        for label, group, sacrifice, output, probe_key, floor in jobs:
            if self._stop.is_set():
                break
            key = sacrifice or f"→ {output}"
            if floor and key in complete:
                continue  # the stack-size slices would only return the same listings again
            if not probe_key and self.dry.get(key, {}).get("until", 0) > now:
                continue  # kept coming back empty: rested for a while (see _note_result)
            self.on_status(f"🔎 {label}")
            try:
                deals = self.scan_item(idx, idx.league, sacrifice, not_ids, output=output, min_price=floor)
            except TradeBusy as exc:
                self.on_status(f"⏸ {exc}")
                wait = max(5.0, LIMITER.blocked_until - time.time())
                self.sleep(min(wait, 60.0))
                continue
            except Exception as exc:  # noqa: BLE001 - one item failing shouldn't end the scan
                self.on_status(f"⚠ {group}: {exc}")
                if probe_key:
                    self._mark_probed(probe_key)
                continue
            total = getattr(self, "last_total", 0)
            if probe_key and not total:  # really nothing listed (not just unpriced)
                self._mark_probed(probe_key)
            if not floor and total <= 10 * self.pages:
                complete.add(key)
            if not probe_key and not floor:
                self._note_result(key, deals)
            self._learn(deals)
            found += len(deals)
            self.on_deals(group, deals)
        found += discover(broad, 2, len(broad) + 1)
        return found

    DRY_RUNS = 3  # this many searches in a row without a single good deal ...
    DRY_REST = 12 * 3600  # ... and the search is skipped for this long (saves the budget for ones that pay)

    def _dry_file(self) -> Path:
        return (self.known_file or Path("ultimatum_sacrifices.json")).with_name("ultimatum_dry.json")

    def _note_result(self, key: str, deals: Sequence[UltimatumDeal]) -> None:
        """
        Count searches whose listings are all poor deals (none gaining >= min_potential after price and sacrifice).
        No listings at all doesn't count: a rare sacrifice (e.g. Orb of Dominance) is exactly what we want to catch.
        """
        if not deals:
            return
        good = any(d.profit >= self.min_potential for d in deals)
        entry = self.dry.setdefault(key, {"n": 0, "until": 0.0})
        if good:
            self.dry.pop(key, None)
        else:
            entry["n"] = int(entry.get("n", 0)) + 1
            if entry["n"] >= self.DRY_RUNS:
                entry["n"], entry["until"] = 0, time.time() + self.DRY_REST
                self.on_status(f"💤 {key}: no deal over {self.min_potential:,.0f}c profit {self.DRY_RUNS}× in a row "
                               f"– skipped for {self.DRY_REST // 3600} h")
        if self.known_file is not None:
            try:
                self._dry_file().write_text(json.dumps(self.dry, indent=0), encoding="utf-8")
            except OSError:
                pass

    FRESH_EVERY = 10 * 60  # between passes: look at the newest listings this often (mispriced ones sell fast)

    def fresh_sweep(self) -> int:
        """The newest listings of each reward type - a 1c ultimatum with a 120c sacrifice is gone in minutes."""
        idx, found = self.get_index(), 0
        for rtype in REWARD_TYPES:
            if self._stop.is_set():
                break
            self.on_status(f"🆕 newest {rtype} listings")
            try:
                deals = self.scan_item(idx, idx.league, "", [], reward_type=rtype, pages=5, newest=True)
            except TradeBusy as exc:
                self.on_status(f"⏸ {exc}")
                break
            except Exception as exc:  # noqa: BLE001
                self.on_status(f"⚠ newest {rtype}: {exc}")
                continue
            self._learn(deals)
            found += len(deals)
            self.on_deals(f"(newest {rtype})", deals)
        return found

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                found = self.run_once()
            except Exception as exc:  # noqa: BLE001 - e.g. poe.ninja busy: say so and try again, don't die silently
                self.on_status(f"⚠ couldn't get prices ({exc}) – retrying in 2 min")
                end = time.time() + 120
                while not self._stop.is_set() and time.time() < end:
                    self.sleep(5.0)
                continue
            if self._stop.is_set():
                break
            self.on_status(f"✅ pass done ({found} listings) – next pass in {self.CYCLE_PAUSE // 60} min, "
                           f"newest listings every {self.FRESH_EVERY // 60} min meanwhile")
            end = time.time() + self.CYCLE_PAUSE
            next_fresh = time.time() + self.FRESH_EVERY
            while not self._stop.is_set() and time.time() < end:
                if time.time() >= next_fresh:
                    try:
                        self.fresh_sweep()
                    except Exception as exc:  # noqa: BLE001
                        self.on_status(f"⚠ newest listings: {exc}")
                    next_fresh = time.time() + self.FRESH_EVERY
                    self.on_status(f"💤 waiting – next full pass in {max(0, int(end - time.time())) // 60} min")
                self.sleep(5.0)
        self.on_status("⏹ stopped")
