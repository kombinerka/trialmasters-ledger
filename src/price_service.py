"""
The price check behind Ctrl+F3, without any UI: OCR'd tooltip lines in, a priced answer out.

  * Inscribed Ultimatum -> precise trade search (challenge, reward, sacrifice amount, hard modifiers)
  * fixed-name items (Heist curios, uniques, gems, currency, cards, bases) -> poe.ninja
  * anything else (rares) -> the trade site, by its affixes
"""

from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from src import ultimatum as ultimatum_check
from src.loot_filter import current_leagues
from src.price_check import PriceCheckManager
from src.trade_search import TradeBusy, TradeData, TradeQueryError, search as trade_search, to_chaos

logger = logging.getLogger("poe_companion")

Line = Tuple[Tuple[float, float], str]  # ((x, y) screen centre, text)


@dataclass
class PriceAnswer:
    html: str = ""
    error: str = ""
    link: Optional[Tuple[str, str]] = None  # (url, label)
    title: str = "💰 Price"
    item: str = ""  # short name for the history list
    summary: str = ""  # short price text for the history list
    at: float = field(default_factory=time.time)


class PriceService:
    def __init__(self, price_check: PriceCheckManager, league: Callable[[], str], post=None, get=None,
                 cards=None) -> None:
        self.price_check = price_check
        self.cards = cards  # CardRewards: what a divination card set turns into
        self.league = league  # "" = current league
        self.post, self.get = post, get
        self._trade_data: Optional[TradeData] = None
        self._league_cache: Tuple[str, float] = ("", 0.0)

    def current_league(self) -> str:
        chosen = self.league()
        if chosen:
            return chosen
        name, at = self._league_cache
        if name and time.time() - at < 3600:
            return name
        try:
            name = current_leagues(self.price_check.fetch)[0]
        except Exception:  # noqa: BLE001 - poe.ninja busy: last known league
            marker = self.price_check.cache_dir / "last_league.txt"
            name = name or (marker.read_text(encoding="utf-8").strip() if marker.is_file() else "Standard")
        self._league_cache = (name, time.time())
        return name

    def trade_data(self) -> TradeData:
        if self._trade_data is None:
            self._trade_data = TradeData.load(self.price_check.cache_dir, get=self.get)
        return self._trade_data

    def check(self, lines: Sequence[Line], cursor: Tuple[float, float]) -> PriceAnswer:
        rows = sorted(lines, key=lambda c: (c[0][1], c[0][0]))
        texts = [t for _, t in rows]
        try:
            idx = self.price_check.index(self.current_league())
            if ultimatum_check.is_ultimatum(texts):
                return self._ultimatum(texts, idx)
            entries = idx.match(list(lines), cursor)
            if entries:
                best = entries[0]
                body = idx.to_html(entries)
                info = self.cards.value(best.name, idx) if self.cards is not None else None
                if info is not None:
                    body += "<br><span style='color:#c4b5fd;'>🎴 " + html.escape(info.describe(idx.fmt)) + "</span>"
                return PriceAnswer(body, item=best.name, summary=idx.fmt(best.chaos))
            base, category, mods = self.trade_data().read_item(rows)
            if not base:
                read = " | ".join(texts[:8]) or "nothing"
                return PriceAnswer(error=f"No priced item found under the mouse (read: {read})")
            res = trade_search(idx.league, base, mods, post=self.post, get=self.get, category=category)
            summary = ""
            if res.prices:
                chaos = to_chaos(res.prices[0][0], res.prices[0][1], idx.divine_chaos)
                summary = idx.fmt(chaos) if chaos is not None else f"{res.prices[0][0]:g} {res.prices[0][1]}"
            return PriceAnswer(self.trade_html(res, idx), link=(res.url, "Open this search on pathofexile.com ↗"),
                               item=res.base, summary=summary or "no listings")
        except TradeBusy as exc:
            return PriceAnswer(error=f"Trade site: {exc}")
        except TradeQueryError as exc:
            logger.info("Price check read: %s", " | ".join(texts[:20]))
            return PriceAnswer(error=f"The trade site rejected the search: {exc}")
        except Exception as exc:  # noqa: BLE001 - shown to the user
            logger.exception("Price check failed")
            return PriceAnswer(error=str(exc))

    def _ultimatum(self, texts: List[str], idx) -> PriceAnswer:
        u = ultimatum_check.parse(texts)
        u.sacrifice = ultimatum_check.proper_name(idx, u.sacrifice)
        if u.reward_type == "ExchangeUnique":
            known = idx.lookup_line(u.reward)  # only a name the price list knows (a garbled one -> HTTP 400)
            u.reward = idx.by_key[known][0].name if known else ""
        mods = self.trade_data().ultimatum_mods
        u.mods = ultimatum_check.read_mods(texts, mods)
        u.nasty, u.nasty_not_ids = ultimatum_check.split_nasty(u.mods, mods)
        logger.info("Ultimatum read: %s / %s x%d -> %s / mods %s", u.challenge, u.sacrifice, u.count, u.reward,
                    [n for n, _ in u.mods])
        ultimatum_check.search_listings(u, idx.league, post=self.post, get=self.get)
        for err in u.errors:
            logger.info("Ultimatum search step skipped: %s", err)
        body = ultimatum_check.to_html(u, idx)
        summary = "no listings"
        values = sorted(c for c in (a if cur == "chaos" else a * idx.divine_chaos if cur == "divine" and idx.divine_chaos
                                    else None for a, cur in u.prices) if c is not None)
        if values:
            median = values[len(values) // 2]
            summary = f"median {idx.fmt(median)}"
            odds = self.odds(u, idx, median)
            if odds:
                body += "<br>" + odds
        return PriceAnswer(body, link=(u.url, "Open on pathofexile.com ↗"), title="📜 Inscribed Ultimatum",
                           item=f"Ultimatum: {u.count}× {u.sacrifice}", summary=summary)

    @staticmethod
    def odds(u, idx, price: float) -> str:
        """If you buy it at that price: what a win makes, what a fail costs, how often you must win."""
        sac = ultimatum_check.best_price(idx, u.sacrifice)
        if sac is None:
            return ""
        stake = sac * u.count
        if u.reward_type in ("DoubleCurrency", "DoubleDivCards"):
            reward = stake * 2
        elif u.reward_type == "ExchangeUnique":
            reward = ultimatum_check.best_price(idx, u.reward) if u.reward else None
            if reward is None:
                return ""
        else:
            return ""
        profit, loss = reward - stake - price, stake + price
        if profit <= 0:
            return (f"<span style='color:#fca5a5;'>Bought at the median: no profit even if you win "
                    f"({html.escape(idx.fmt(profit))}).</span>")
        must = loss / (profit + loss) * 100
        color = "#6ee7b7" if must <= 50 else "#fde68a" if must <= 75 else "#fca5a5"
        return (f"Bought at the median: <b style='color:#6ee7b7;'>+{html.escape(idx.fmt(profit))}</b> if you win, "
                f"<b style='color:#fca5a5;'>−{html.escape(idx.fmt(loss))}</b> if you fail · "
                f"<b style='color:{color};'>you must win ≥ {must:.0f}%</b>")

    @staticmethod
    def trade_html(res, idx) -> str:
        esc = html.escape
        out = [f"<b style='color:#fde68a;'>{esc(res.base)}</b> <span style='color:#9ca3af;'>· rare – priced by "
               f"similar items on the trade site</span>"]
        if res.mods:
            out.append("<span style='color:#c4b5fd;'>" + "<br>".join(esc(m.display()) for m in res.mods) + "</span>")
        out.append(f"Matched: <b>{esc(res.matched)}</b> · {res.total} listed")
        if res.prices:
            shown = []
            for amount, cur in res.prices[:6]:
                chaos = to_chaos(amount, cur, idx.divine_chaos)
                shown.append(idx.fmt(chaos) if chaos is not None else f"{amount:g} {cur}")
            out.append("Cheapest: <b>" + "</b>, <b>".join(esc(x) for x in shown) + "</b>")
        else:
            out.append("<span style='color:#fca5a5;'>No listings yet (new league?)</span>")
        if res.matched == "base type only" and res.mods:
            out.append("<span style='color:#9ca3af;'>Nothing with these affixes is listed – this is just the base type.</span>")
        return "<br>".join(out)
