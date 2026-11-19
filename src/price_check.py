"""
Price check for things you can't Ctrl+C - e.g. Heist curio display rewards (Ctrl+F3 by default):
hover the item, press the key; the companion reads the tooltip from a screenshot (OCR, only on the key press),
finds the item name and shows poe.ninja prices for it. Nothing is sent to the game.

Covers uniques (incl. Replica uniques, by link variant), skill gems (level/quality/corrupted), base types (by item
level, no influence - like curio bases) and currency-exchange items (currency, cards, scarabs, ...).
Qt-free so it can be unit-tested.
"""

from __future__ import annotations

import difflib
import html
import json
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from src.loot_filter import EXCHANGE_TYPES, NINJA, UNIQUE_TYPES, Fetch, _default_fetch

KIND_ORDER = {"unique": 0, "gem": 1, "currency": 2, "base": 3}


@dataclass
class PriceEntry:
    name: str
    kind: str  # unique | gem | currency | base
    label: str  # variant, e.g. "6-link", "20/20", "ilvl 86"
    chaos: float
    listings: int = 0
    group: str = ""  # poe.ninja category, e.g. "Currency", "DivinationCard", "UniqueWeapon"
    volume: float = -1.0  # exchange: chaos traded per day (-1 = unknown) – ~1 card a day means nobody really buys
    change: float = 0.0  # price change over the last week in % (+1850 = suddenly pumped)
    base: str = ""  # uniques: their base type ("Prismatic Jewel") - a card that gives "a random unique Prismatic Jewel"


def squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


class PriceIndex:
    def __init__(self, league: str, entries: List[PriceEntry], divine_chaos: float = 0.0, fetched_at: float = 0.0) -> None:
        self.league = league
        self.divine_chaos = divine_chaos
        self.fetched_at = fetched_at or time.time()
        self.by_key: Dict[str, List[PriceEntry]] = {}
        for e in entries:
            self.by_key.setdefault(squash(e.name), []).append(e)

    # ---------- building ----------
    @staticmethod
    def download(league: str, fetch: Optional[Fetch] = None, min_listings: int = 3) -> "PriceIndex":
        fetch = fetch or _default_fetch
        q = urllib.parse.quote(league)
        entries: List[PriceEntry] = []
        divine = 0.0
        for t in EXCHANGE_TYPES:
            try:
                d = json.loads(fetch(f"{NINJA}/economy/exchange/current/overview?league={q}&type={t}"))
            except Exception as exc:  # noqa: BLE001 - one missing category shouldn't stop the rest
                if getattr(exc, "code", None) == 429:
                    raise  # poe.ninja says slow down: stop instead of firing the other categories
                continue
            rate = (d.get("core") or {}).get("rates", {}).get("divine")
            if rate:
                divine = round(1 / float(rate), 1)
            names = {i["id"]: i["name"] for i in d.get("items", [])}
            for line in d.get("lines", []):
                if line.get("id") in names and line.get("primaryValue") is not None:
                    entries.append(PriceEntry(names[line["id"]], "currency", "", float(line["primaryValue"]), group=t,
                                              volume=float(line.get("volumePrimaryValue") or 0),
                                              change=float((line.get("sparkline") or {}).get("totalChange") or 0)))
        stash = [(t, "unique") for t in UNIQUE_TYPES] + [("SkillGem", "gem"), ("BaseType", "base")]
        for t, kind in stash:
            try:
                d = json.loads(fetch(f"{NINJA}/economy/stash/current/item/overview?league={q}&type={t}"))
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "code", None) == 429:
                    raise
                continue
            for line in d.get("lines", []):
                listings = int(line.get("listingCount") or 0)
                if listings < min_listings or not line.get("name"):
                    continue
                if kind == "unique":
                    label = f"{line['links']}-link" if line.get("links") else (line.get("variant") or "no links")
                elif kind == "gem":
                    label = f"{line.get('gemLevel', '?')}/{line.get('gemQuality') or 0}" + (" corrupted" if line.get("corrupted") else "")
                else:
                    if line.get("variant"):  # influenced bases don't come out of curio displays
                        continue
                    label = f"ilvl {line.get('levelRequired', '?')}"
                entries.append(PriceEntry(line["name"], kind, label, float(line.get("chaosValue") or 0), listings, group=t,
                                          change=float((line.get("sparkline") or line.get("sparkLine") or {}).get("totalChange") or 0),
                                          base=str(line.get("baseType") or "")))
        return PriceIndex(league, entries, divine)

    def to_json(self) -> str:
        return json.dumps({"league": self.league, "divine_chaos": self.divine_chaos, "fetched_at": self.fetched_at,
                           "entries": [asdict(e) for es in self.by_key.values() for e in es]})

    @staticmethod
    def from_json(text: str) -> "PriceIndex":
        d = json.loads(text)
        return PriceIndex(d["league"], [PriceEntry(**e) for e in d["entries"]], float(d.get("divine_chaos") or 0), float(d["fetched_at"]))

    # ---------- matching ----------
    def lookup_line(self, text: str) -> Optional[str]:
        """Squashed key of the item a single OCR'd line names (tolerates a misread letter or two)."""
        key = squash(text)
        if len(key) < 4 or len(text) > 60:
            return None
        if key in self.by_key:
            return key
        if len(key) < 7:
            return None
        close = difflib.get_close_matches(key, list(self.by_key), n=1, cutoff=0.88)
        return close[0] if close else None

    def match(self, lines: Sequence[Tuple[Tuple[float, float], str]], cursor: Tuple[float, float]) -> Optional[List[PriceEntry]]:
        """lines: ((x, y) centre, text). Best item: uniques before gems/currency/bases, then nearest the mouse."""
        found = []
        for (x, y), text in lines:
            key = self.lookup_line(text)
            if key:
                entries = self.by_key[key]
                kind = min(KIND_ORDER[e.kind] for e in entries)
                found.append((kind, abs(x - cursor[0]) + abs(y - cursor[1]), key))
        if not found:
            return None
        return self.by_key[min(found)[2]]

    def to_html(self, entries: List[PriceEntry], max_rows: int = 6) -> str:
        esc = html.escape
        name = entries[0].name
        best_kind = min(entries, key=lambda e: KIND_ORDER[e.kind]).kind
        rows = sorted((e for e in entries if e.kind == best_kind), key=lambda e: (-e.listings, e.chaos))[:max_rows]
        rows.sort(key=lambda e: e.chaos)
        kind_label = {"unique": "Unique", "gem": "Skill gem", "currency": "Currency exchange", "base": "Base type (no influence)"}[best_kind]
        out = [f"<b style='color:#fde68a;'>{esc(name)}</b> <span style='color:#9ca3af;'>· {kind_label}</span>"]
        for e in rows:
            out.append(f"{esc(e.label or 'price')}: <b>{esc(self.fmt(e.chaos))}</b>"
                       + (f" <span style='color:#6b7280;'>({e.listings} listed)</span>" if e.listings else ""))
        age = int((time.time() - self.fetched_at) // 60)
        out.append(f"<span style='color:#6b7280;'>poe.ninja · {esc(self.league)} · {age} min old</span>")
        return "<br>".join(out)

    def fmt(self, chaos: float) -> str:
        if self.divine_chaos and chaos >= self.divine_chaos:
            return f"{chaos / self.divine_chaos:.1f} div ({chaos:,.0f}c)"
        return f"{chaos:,.1f}c" if chaos < 10 else f"{chaos:,.0f}c"


class PriceCheckManager:
    MAX_AGE = 3600
    RETRY_AFTER = 600  # after a failed poe.ninja download, keep using the old prices this long before retrying

    def __init__(self, cache_dir: Path, fetch: Optional[Fetch] = None) -> None:
        self.cache_dir = Path(cache_dir)
        self.fetch = fetch or _default_fetch
        self._index: Optional[PriceIndex] = None
        self._retry_at = 0.0

    def cached(self) -> Optional[PriceIndex]:
        """Whatever prices are loaded right now - never downloads (safe to call from the UI thread)."""
        return self._index

    def index(self, league: str) -> PriceIndex:
        if self._index and self._index.league == league and (
                time.time() - self._index.fetched_at < self.MAX_AGE or time.time() < self._retry_at):
            return self._index
        path = self.cache_dir / f"price_index_{re.sub(r'[^A-Za-z0-9]+', '_', league)}.json"
        fresh = path.is_file() and time.time() - path.stat().st_mtime < self.MAX_AGE
        if fresh and '"base"' in path.read_text(encoding="utf-8")[:4000]:  # older caches lack the categories
            self._index = PriceIndex.from_json(path.read_text(encoding="utf-8"))
            return self._index
        try:
            idx = PriceIndex.download(league, self.fetch)
            if not idx.by_key:
                raise ValueError("no prices returned")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(idx.to_json(), encoding="utf-8")
            self._index = idx
        except Exception:  # noqa: BLE001
            self._retry_at = time.time() + self.RETRY_AFTER  # never hammer poe.ninja (it answers 429 then)
            if path.is_file():
                self._index = PriceIndex.from_json(path.read_text(encoding="utf-8"))
            else:
                raise
        return self._index
