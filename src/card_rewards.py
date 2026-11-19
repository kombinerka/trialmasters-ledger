"""
Divination card turn-ins: what a full set gives and what that's worth, so a stack of cards can be judged by what it
turns into (9× Azyran's Reward -> a random corrupted unique Prismatic Jewel) and not only by the card's own price.

Card data (stack size + reward) comes from the community wiki (poewiki.net, CC BY-NC-SA), cached for a week;
values come from poe.ninja. A random reward ("a unique Prismatic Jewel") lists every unique it can be with its value -
the game doesn't publish the odds, so the "typical" figure treats them as equally likely and is only a rough guide.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from src.trade_search import squash

WIKI_API = "https://www.poewiki.net/w/api.php"
MAX_AGE = 7 * 24 * 3600


@dataclass
class CardReward:
    name: str
    stack: int
    text: str  # "Prismatic Jewel (Corrupted)" / "10x Divine Orb" / "Headhunter"
    item: str  # the linked item: a unique, a base type, a currency …
    amount: int = 1
    kind: str = "other"  # unique | currency | gem | divination | other
    corrupted: bool = False


@dataclass
class SetValue:
    card: CardReward
    outcomes: List[Tuple[str, float]] = field(default_factory=list)  # (item, chaos) – one entry if it's fixed
    random: bool = False  # True: one of `outcomes` (odds unknown)

    @property
    def low(self) -> float:
        return min(c for _, c in self.outcomes)

    @property
    def high(self) -> float:
        return max(c for _, c in self.outcomes)

    @property
    def typical(self) -> float:
        values = sorted(c for _, c in self.outcomes)
        return values[len(values) // 2]

    def per_card(self, which: str = "typical") -> float:
        return getattr(self, which) / max(1, self.card.stack)

    def describe(self, fmt: Callable[[float], str], limit: int = 6) -> str:
        c = self.card
        head = f"{c.stack}× {c.name} → {c.text}"
        if not self.random:
            return f"{head}: {fmt(self.typical)} (≈ {fmt(self.per_card())} per card)"
        best = sorted(self.outcomes, key=lambda o: -o[1])[:limit]
        items = ", ".join(f"{n} {fmt(v)}" for n, v in best)
        return (f"{head}: one of {len(self.outcomes)} – {items}"
                f"{' …' if len(self.outcomes) > limit else ''} · typical {fmt(self.typical)} "
                f"(≈ {fmt(self.per_card())} per card, odds unknown)")


def _reward_from_html(raw: str) -> Tuple[str, str, int, str, bool]:
    """(readable text, linked item, amount, kind, corrupted) from the wiki's card html."""
    page = html.unescape(raw or "")
    m = re.search(r'divicard-reward">(.*?)<span class="divicard-flavour', page, re.S)
    part = m.group(1) if m else page
    kinds = re.findall(r'class="tc -(\w+)"', part)
    corrupted = "corrupted" in kinds
    # the visible text of every [[Link|Text]] or [[Link]], then the plain text around it
    links = [(a, b or a) for a, b in re.findall(r"\[\[(?!File:)([^|\]]+)(?:\|([^\]]+))?\]\]", part)]
    text = re.sub(r"\[\[File:[^\]]*\]\]", "", part)
    text = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r'<span class="hoverbox__display.*?</span>', "", text, flags=re.S)
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    item = links[0][1].strip() if links else text
    amount = 1
    am = re.match(r"(\d+)\s*x\s+", text, re.I)
    if am:
        amount = int(am.group(1))
    kind = next((k for k in kinds if k in ("unique", "currency", "gem", "divination")), "other")
    return text, item, amount, kind, corrupted


class CardRewards:
    def __init__(self, cache_dir: Path, fetch: Optional[Callable[[str], str]] = None) -> None:
        self.path = Path(cache_dir) / "card_rewards.json"
        self.fetch = fetch
        self.cards: Dict[str, CardReward] = {}
        self._load_cache()

    def _load_cache(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        self.cards = {squash(c["name"]): CardReward(**c) for c in data.get("cards", [])}
        return time.time() - float(data.get("fetched_at", 0)) < MAX_AGE

    def ensure(self) -> int:
        """Download the card list if the cache is missing or a week old. Returns how many cards are known."""
        if self.cards and self._load_cache():
            return len(self.cards)
        try:
            cards = self.download()
        except Exception:  # noqa: BLE001 - the wiki being down only means no turn-in values for now
            return len(self.cards)
        if cards:
            self.cards = {squash(c.name): c for c in cards}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps({"fetched_at": time.time(), "cards": [asdict(c) for c in cards]}),
                                     encoding="utf-8")
            except OSError:
                pass
        return len(self.cards)

    def download(self) -> List[CardReward]:
        from src.loot_filter import _default_fetch

        fetch = self.fetch or _default_fetch
        out: List[CardReward] = []
        offset = 0
        while True:
            q = {"action": "cargoquery", "format": "json", "tables": "items,stackables",
                 "join_on": "items._pageID=stackables._pageID", "fields": "items.name=name,stackables.stack_size=stack,items.html=html",
                 "where": 'items.class_id="DivinationCard"', "limit": "500", "offset": str(offset)}
            data = json.loads(fetch(f"{WIKI_API}?{urllib.parse.urlencode(q)}"))
            rows = [r.get("title", {}) for r in data.get("cargoquery", [])]
            for r in rows:
                name = html.unescape(str(r.get("name") or "")).strip()
                try:
                    stack = int(r.get("stack") or 0)
                except ValueError:
                    stack = 0
                if not name or stack <= 0:
                    continue
                text, item, amount, kind, corrupted = _reward_from_html(str(r.get("html") or ""))
                out.append(CardReward(name, stack, text, item, amount, kind, corrupted))
            if len(rows) < 500:
                return out
            offset += 500

    def get(self, name: str) -> Optional[CardReward]:
        return self.cards.get(squash(name or ""))

    def value(self, name: str, idx) -> Optional[SetValue]:
        """What a full set of this card is worth on poe.ninja (None if unknown)."""
        card = self.get(name)
        if card is None or idx is None:
            return None
        key = squash(card.item)
        entries = idx.by_key.get(key, [])
        if entries:  # a fixed item: a named unique, a currency, a gem
            if card.kind == "unique" and all(e.kind != "unique" for e in entries):
                entries = []
            else:
                uniques = [e for e in entries if e.kind == "unique" and not e.label.endswith("-link") and e.listings >= 3]
                if card.kind == "unique" and len({round(e.chaos) for e in uniques}) > 1:
                    # a unique with variants (Voices: 1/3/5/7 passives …): which one the card gives isn't known here
                    outs = sorted(((f"{e.name} ({e.label})", e.chaos) for e in uniques), key=lambda o: o[1])
                    return SetValue(card, outs, random=True)
                best = sorted(entries, key=lambda e: (e.kind != "currency", -e.listings))[0]
                return SetValue(card, [(best.name, best.chaos * card.amount)])
        if card.kind == "unique":  # "a random unique <base>": every unique on that base
            by_name: Dict[str, Tuple[int, float]] = {}
            for es in idx.by_key.values():
                for e in es:
                    if e.kind == "unique" and squash(getattr(e, "base", "")) == key:
                        if e.name not in by_name or e.listings > by_name[e.name][0]:
                            by_name[e.name] = (e.listings, e.chaos)
            if by_name:
                return SetValue(card, sorted(((n, c) for n, (_l, c) in by_name.items()), key=lambda o: o[1]), random=True)
        return None
