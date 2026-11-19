"""📒 My ultimatums: the deals you opened, what became of them, and your real win rate per challenge."""

from __future__ import annotations

import time
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.deal_history import DealHistory, DealRecord

COLUMNS = ["Status", "Sacrifice", "Reward", "Challenge", "Paid", "Sacrifice cost", "Reward worth", "If you win",
           "If you fail", "Result", "Seller", "Opened"]


def parse_chaos(text: str, divine: float) -> float:
    """'72' / '72c' -> 72 · '1.5 div' / '1.5d' -> 1.5 × divine · '' -> -1 (not entered)."""
    t = (text or "").strip().lower().replace(",", ".")
    if not t:
        return -1.0
    mult = 1.0
    for suffix in ("divines", "divine", "div", "d"):
        if t.endswith(suffix):
            t, mult = t[: -len(suffix)].strip(), (divine or 0.0)
            break
    else:
        for suffix in ("chaos", "c"):
            if t.endswith(suffix):
                t = t[: -len(suffix)].strip()
                break
    try:
        value = float(t) * mult
    except ValueError:
        return -1.0
    return value if value >= 0 and mult else -1.0


class PricesDialog(QDialog):
    """Your real prices for one ultimatum: what you paid for it, for each sacrificed item, and what the reward sold for."""

    def __init__(self, rec: DealRecord, fmt: Callable[[float], str], divine: float, parent: Optional[QWidget] = None,
                 card=None) -> None:
        super().__init__(parent)
        self.divine = divine
        self.setWindowTitle("My prices")
        lay = QVBoxLayout(self)
        intro = QLabel(f"<b>{rec.count}× {rec.sacrifice} → {rec.reward}</b><br><span style='color:#9ca3af;'>Chaos, or "
                       f"divines with “div” (e.g. <i>72</i> or <i>1.5 div</i>). Leave empty to use poe.ninja / the "
                       f"listing.</span>")
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        form = QFormLayout()

        def field(value: float, hint: str) -> QLineEdit:
            edit = QLineEdit(f"{value:g}" if value >= 0 else "")
            edit.setPlaceholderText(hint)
            return edit

        each_reward = rec.reward_chaos / max(1, rec.reward_amount)
        self.paid = field(rec.paid_chaos, f"listing: {rec.price_text}")
        self.sacrifice = field(rec.sacrifice_each, f"poe.ninja: {fmt(rec.sacrifice_chaos / max(1, rec.count))} each")
        self.sold = field(rec.sold_each, f"poe.ninja: {fmt(each_reward)}" + (" each" if rec.reward_amount > 1 else ""))
        form.addRow("Paid for the ultimatum:", self.paid)
        form.addRow(f"Paid per {rec.sacrifice}:", self.sacrifice)
        form.addRow("Sold the reward for" + (" (each):" if rec.reward_amount > 1 else ":"), self.sold)
        lay.addLayout(form)
        if card is not None:  # a card stack: what a full set turns into, to compare with selling the cards
            turn = QLabel("🎴 " + card.describe(fmt) + ("<br><span style='color:#9ca3af;'>Turned a set in? Enter what you "
                          "sold the result for, divided by the cards it took, as the price per card.</span>"))
            turn.setWordWrap(True)
            turn.setTextFormat(Qt.TextFormat.RichText)
            turn.setStyleSheet("color: #c4b5fd;")
            lay.addWidget(turn)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> tuple:
        return (parse_chaos(self.paid.text(), self.divine), parse_chaos(self.sacrifice.text(), self.divine),
                parse_chaos(self.sold.text(), self.divine))
STATUS_LOOK = {"opened": ("👀 opened", "#9ca3af"), "bought": ("🛒 bought", "#93c5fd"), "won": ("✅ won", "#6ee7b7"),
               "lost": ("❌ lost", "#fca5a5"), "skipped": ("⏭ skipped", "#6b7280")}


class HistoryTab(QWidget):
    def __init__(self, history: DealHistory, fmt: Callable[[float], str], on_changed: Optional[Callable[[], None]] = None,
                 divine: Optional[Callable[[], float]] = None, card_info: Optional[Callable[[str], object]] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.history, self.fmt, self.on_changed = history, fmt, on_changed
        self.divine = divine or (lambda: 0.0)
        self.card_info = card_info
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        intro = QLabel("Every ultimatum you open from the scanner lands here. Mark what you did with it – your real "
                       "win rate per challenge then shows up in the scanner next to each deal's break-even. Double-click a row (or "
                       "✏ My prices) to enter what you really paid and sold for – the results then use your prices.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #9ca3af;")
        lay.addWidget(intro)
        self.summary = QLabel("")
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-size: 14px; padding: 6px 0;")
        lay.addWidget(self.summary)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.edit_prices())
        lay.addWidget(self.table, stretch=1)
        row = QHBoxLayout()
        for label, status in (("🛒 Bought", "bought"), ("✅ Won", "won"), ("❌ Lost", "lost"), ("⏭ Didn't buy", "skipped")):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _c=False, s=status: self.mark(s))
            row.addWidget(btn)
        prices_btn = QPushButton("✏ My prices…")
        prices_btn.setToolTip("What you really paid for the ultimatum and the sacrifice, and what you sold the reward for")
        prices_btn.clicked.connect(self.edit_prices)
        row.addWidget(prices_btn)
        open_btn = QPushButton("Open on pathofexile.com ↗")
        open_btn.clicked.connect(self.open_selected)
        row.addWidget(open_btn)
        remove = QPushButton("🗑 Remove")
        remove.clicked.connect(self.remove_selected)
        row.addWidget(remove)
        row.addStretch(1)
        lay.addLayout(row)
        self.refresh()

    def selected(self) -> Optional[DealRecord]:
        r = self.table.currentRow()
        recs = self.history.records
        return recs[r] if 0 <= r < len(recs) else None

    def mark(self, status: str) -> None:
        rec = self.selected()
        if rec is None:
            return
        self.history.set_status(rec, status)
        self.refresh(keep=rec)
        if self.on_changed:
            self.on_changed()

    def edit_prices(self, rec: Optional[DealRecord] = None) -> None:
        rec = rec or self.selected()
        if rec is None:
            return
        card = self.card_info(rec.sacrifice) if self.card_info and "×" in rec.reward else None
        dlg = PricesDialog(rec, self.fmt, self.divine(), self, card=card)
        if dlg.exec():
            self.history.set_prices(rec, *dlg.values())
            self.refresh(keep=rec)
            if self.on_changed:
                self.on_changed()

    def remove_selected(self) -> None:
        rec = self.selected()
        if rec is not None:
            self.history.remove(rec)
            self.refresh()
            if self.on_changed:
                self.on_changed()

    def open_selected(self) -> None:
        rec = self.selected()
        if rec is not None and rec.url:
            QDesktopServices.openUrl(QUrl(rec.url))

    def refresh(self, keep: Optional[DealRecord] = None) -> None:
        recs = self.history.records
        self.table.setRowCount(len(recs))
        for r, rec in enumerate(recs):
            text, color = STATUS_LOOK.get(rec.status, (rec.status, "#d1d5db"))
            mine = "  ✏"  # your own price, not poe.ninja's
            cells = [
                QTableWidgetItem(text), QTableWidgetItem(f"{rec.count}× {rec.sacrifice}"), QTableWidgetItem(rec.reward),
                QTableWidgetItem(rec.challenge + (f" · ⚠ {rec.hard_mods}" if rec.hard_mods else "")),
                QTableWidgetItem((self.fmt(rec.paid_chaos) + mine) if rec.paid_chaos >= 0 else rec.price_text),
                QTableWidgetItem(self.fmt(rec.cost_sacrifice) + (mine if rec.sacrifice_each >= 0 else "")),
                QTableWidgetItem(self.fmt(rec.reward_value) + (mine if rec.sold_each >= 0 else "")),
                QTableWidgetItem(("+" if rec.profit >= 0 else "−") + self.fmt(abs(rec.profit))),
                QTableWidgetItem("−" + self.fmt(rec.loss)),
                QTableWidgetItem(("+" if rec.result > 0 else "−" if rec.result < 0 else "") + self.fmt(abs(rec.result))
                                 if rec.status in ("won", "lost") else ""),
                QTableWidgetItem(rec.seller), QTableWidgetItem(time.strftime("%d.%m %H:%M", time.localtime(rec.opened_at))),
            ]
            cells[0].setForeground(QBrush(QColor(color)))
            cells[7].setForeground(QBrush(QColor("#6ee7b7" if rec.profit >= 0 else "#fca5a5")))
            cells[8].setForeground(QBrush(QColor("#fca5a5")))
            cells[9].setForeground(QBrush(QColor("#6ee7b7" if rec.result > 0 else "#fca5a5")))
            for c, item in enumerate(cells):
                self.table.setItem(r, c, item)
            if rec is keep:
                self.table.selectRow(r)
        wins, done, made = self.history.totals()
        if not done:
            self.summary.setText("No finished ultimatums yet – mark the ones you ran as <b>won</b> or <b>lost</b>.")
            return
        parts = [f"<b>{wins}/{done} won ({wins / done * 100:.0f}%)</b> · "
                 f"<b style='color:{'#6ee7b7' if made >= 0 else '#fca5a5'};'>{'+' if made >= 0 else '−'}"
                 f"{self.fmt(abs(made))}</b> made in total"]
        for ch, (w, n, c) in sorted(self.history.by_challenge().items(), key=lambda kv: -kv[1][1]):
            parts.append(f"{ch}: {w}/{n} ({w / n * 100:.0f}%)")
        self.summary.setText("<br>".join([parts[0], "<span style='color:#d1d5db;'>" + " · ".join(parts[1:]) + "</span>"]))
