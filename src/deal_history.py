"""
📒 Your ultimatums: every deal you open from the scanner is remembered here; mark it bought, won or lost and the app
shows your real win rate per challenge and the chaos you actually made. The scanner uses that win rate to tell you
whether a deal's break-even ("you must win ≥ 87%") is above or below what you really manage.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Optional, Tuple

STATUSES = ("opened", "bought", "won", "lost", "skipped")


@dataclass
class DealRecord:
    listing_id: str
    sacrifice: str
    count: int
    reward: str
    challenge: str
    area_level: int
    price_chaos: float
    price_text: str
    sacrifice_chaos: float
    reward_chaos: float
    seller: str = ""
    hard_mods: str = ""
    url: str = ""
    status: str = "opened"
    opened_at: float = field(default_factory=time.time)
    decided_at: float = 0.0
    # what it really cost / brought (chaos); -1 = not entered, the poe.ninja / listing value is used
    paid_chaos: float = -1.0  # what you paid for the ultimatum
    sacrifice_each: float = -1.0  # what you paid per sacrificed item (e.g. 72c per Azyran's Reward)
    sold_each: float = -1.0  # what you sold each reward item for (a unique reward: what you sold it for)

    @property
    def reward_amount(self) -> int:
        """How many items the reward is: 8 for '8× Divine Orb', 1 for a unique."""
        head = self.reward.split("×", 1)[0].strip()
        return int(head) if "×" in self.reward and head.isdigit() else 1

    @property
    def edited(self) -> bool:
        return self.paid_chaos >= 0 or self.sacrifice_each >= 0 or self.sold_each >= 0

    @property
    def cost_ultimatum(self) -> float:
        return self.paid_chaos if self.paid_chaos >= 0 else self.price_chaos

    @property
    def cost_sacrifice(self) -> float:
        return self.sacrifice_each * self.count if self.sacrifice_each >= 0 else self.sacrifice_chaos

    @property
    def reward_value(self) -> float:
        return self.sold_each * self.reward_amount if self.sold_each >= 0 else self.reward_chaos

    @property
    def profit(self) -> float:
        return self.reward_value - self.cost_sacrifice - self.cost_ultimatum

    @property
    def loss(self) -> float:
        return self.cost_sacrifice + self.cost_ultimatum

    @property
    def result(self) -> float:
        """Chaos this deal really made you (0 until it's won or lost)."""
        return self.profit if self.status == "won" else -self.loss if self.status == "lost" else 0.0

    @staticmethod
    def from_deal(deal) -> "DealRecord":
        return DealRecord(
            listing_id=deal.listing_id, sacrifice=deal.sacrifice, count=deal.count, reward=deal.reward,
            challenge=deal.challenge, area_level=deal.area_level, price_chaos=deal.price_chaos,
            price_text=deal.price_text, sacrifice_chaos=deal.sacrifice_chaos, reward_chaos=deal.reward_chaos,
            seller=deal.seller, hard_mods=getattr(deal, "hard_mods", ""), url=getattr(deal, "url", ""),
        )


class DealHistory:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.records: List[DealRecord] = []
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        known = {f.name for f in fields(DealRecord)}
        self.records = [DealRecord(**{k: v for k, v in r.items() if k in known}) for r in data if isinstance(r, dict)]

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(r) for r in self.records], indent=1), encoding="utf-8")
        except OSError:
            pass

    def add(self, deal) -> DealRecord:
        """Remember a deal you opened (once per listing)."""
        for r in self.records:
            if r.listing_id and r.listing_id == deal.listing_id:
                return r
        rec = DealRecord.from_deal(deal)
        self.records.insert(0, rec)
        self.save()
        return rec

    def set_status(self, rec: DealRecord, status: str) -> None:
        if status in STATUSES:
            rec.status = status
            rec.decided_at = time.time()
            self.save()

    def set_prices(self, rec: DealRecord, paid: float = -1.0, sacrifice_each: float = -1.0, sold_each: float = -1.0) -> None:
        """Your real prices (chaos); -1 leaves that one on the poe.ninja / listing value."""
        rec.paid_chaos, rec.sacrifice_each, rec.sold_each = float(paid), float(sacrifice_each), float(sold_each)
        self.save()

    def remove(self, rec: DealRecord) -> None:
        self.records = [r for r in self.records if r is not rec]
        self.save()

    def status_of(self, listing_id: str) -> str:
        """'' if you never opened this listing, else its status (opened / bought / won / lost / skipped)."""
        for r in self.records:
            if listing_id and r.listing_id == listing_id:
                return r.status
        return ""

    # ---------- stats ----------
    def record_for(self, challenge: str) -> Optional[Tuple[int, int]]:
        """(wins, finished) for a challenge, or None if you haven't finished any yet."""
        done = [r for r in self.records if r.challenge == challenge and r.status in ("won", "lost")]
        if not done:
            return None
        return sum(1 for r in done if r.status == "won"), len(done)

    def by_challenge(self) -> Dict[str, Tuple[int, int, float]]:
        """challenge -> (wins, finished, chaos made)."""
        out: Dict[str, Tuple[int, int, float]] = {}
        for r in self.records:
            if r.status not in ("won", "lost"):
                continue
            w, n, c = out.get(r.challenge, (0, 0, 0.0))
            out[r.challenge] = (w + (r.status == "won"), n + 1, c + r.result)
        return out

    def totals(self) -> Tuple[int, int, float]:
        """(wins, finished, chaos made) over everything."""
        stats = self.by_challenge().values()
        return sum(s[0] for s in stats), sum(s[1] for s in stats), sum(s[2] for s in stats)
