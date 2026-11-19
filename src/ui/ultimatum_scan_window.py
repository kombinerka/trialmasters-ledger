"""📜 Ultimatum scan window: a slow background scan of listed Inscribed Ultimatums, sorted by profit."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.ultimatum_scan import UltimatumDeal, UltimatumScanner

COLUMNS = ["Profit if you win", "Loss if you fail", "You must win", "Price", "Sacrifice", "Reward", "Challenge", "Lvl", "Seller", "Listed"]


class _NumItem(QTableWidgetItem):
    """Sorts by the number behind the text."""

    def __init__(self, text: str, value: float) -> None:
        super().__init__(text)
        self.value = value

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D105
        return self.value < getattr(other, "value", 0)


class _Bridge(QObject):
    deals = pyqtSignal(str, object)
    status = pyqtSignal(str)


class UltimatumScanWindow(QWidget):
    def __init__(self, make_scanner: Callable[..., UltimatumScanner], fmt: Callable[[float], str],
                 open_deal: Optional[Callable[[UltimatumDeal], None]] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Trialmaster's Ledger – Inscribed Ultimatum scan")
        self.resize(1150, 620)
        self.fmt = fmt
        self._open_deal = open_deal
        self.win_rate: Optional[Callable[[str], Optional[tuple]]] = None
        self.card_info: Optional[Callable[[str], object]] = None
        # listing -> your status in 📒 My ultimatums ('' = never opened); done ones leave the table
        self.deal_status: Optional[Callable[[str], str]] = None  # card -> what a full set turns into (SetValue)  # challenge -> (wins, finished) from your history
        self._bridge = _Bridge()
        self._bridge.deals.connect(self.add_deals)
        self._bridge.status.connect(self.set_status)
        self.scanner = make_scanner(on_deals=lambda name, d: self._bridge.deals.emit(name, d),
                                    on_status=lambda s: self._bridge.status.emit(s))
        self.deals: Dict[str, List[UltimatumDeal]] = {}  # by sacrifice item (replaced on every pass)
        self.setStyleSheet(
            "QWidget { background-color: #0b0e14; color: #f5f5f4; font-family: 'Segoe UI'; font-size: 12px; }"
            "QPushButton { background-color: #1f2937; border: 1px solid #4b5563; border-radius: 6px; padding: 5px 12px; }"
            "QPushButton#Start { background-color: #065f46; border: 1px solid #10b981; font-weight: bold; }"
            "QTableWidget { background-color: #0f131b; gridline-color: #1f2937; border: 1px solid #1f2937; }"
            "QHeaderView::section { background-color: #111827; color: #a5b4fc; border: none; padding: 4px; }"
            "QDoubleSpinBox { background-color: #111827; border: 1px solid #374151; border-radius: 6px; padding: 3px 6px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        self.start_button = QPushButton("▶ Start scan")
        self.start_button.setObjectName("Start")
        self.start_button.clicked.connect(self.toggle_scan)
        top.addWidget(self.start_button)
        self.clean_check = QCheckBox("Only without my hard modifiers (⚙ Settings)")
        self.clean_check.setChecked(True)
        self.clean_check.setToolTip("Only hides them in this table – the scan always reads all listings.")
        self.clean_check.toggled.connect(lambda _v: self.refresh_table())
        top.addWidget(self.clean_check)
        gain = QLabel("Search sacrifices that can gain at least")
        gain.setToolTip("Checked on poe.ninja first: stack value (it gets doubled when you win). Lower = more trade searches.")
        top.addWidget(gain)
        self.min_spin = QDoubleSpinBox()
        self.min_spin.setRange(10, 100000)
        self.min_spin.setValue(100)
        self.min_spin.setSuffix(" c")
        self.min_spin.valueChanged.connect(lambda _v: self.refresh_table())
        top.addWidget(self.min_spin)
        self.thin_check = QCheckBox("Hide rewards that barely sell")
        self.thin_check.setToolTip("poe.ninja: fewer than ~5 traded per day or price pumped >300% this week – "
                                   "the profit may only exist on paper.")
        self.thin_check.setChecked(True)  # their poe.ninja price isn't what you can buy/sell at (Faustus spread)
        self.thin_check.toggled.connect(lambda _v: self.refresh_table())
        top.addWidget(self.thin_check)
        self.profitable_check = QCheckBox("Only deals that gain at least that much")
        self.profitable_check.setChecked(True)
        self.profitable_check.toggled.connect(lambda _v: self.refresh_table())
        top.addWidget(self.profitable_check)
        top.addStretch(1)
        lay.addLayout(top)
        self.status_label = QLabel("Not running. Prices: poe.ninja · listings: pathofexile.com (paced, keeps a reserve "
                                   "for your Ctrl+F3 and Awakened PoE Trade).")
        self.status_label.setStyleSheet("color: #9ca3af;")
        lay.addWidget(self.status_label)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.open_listing())
        lay.addWidget(self.table, stretch=1)
        bottom = QHBoxLayout()
        self.open_button = QPushButton("🏠 Open on pathofexile.com (Travel to Hideout there) ↗")
        self.open_button.setObjectName("Start")
        self.open_button.setToolTip("Opens this seller's matching ultimatums on the trade site (also: double-click a row)")
        self.open_button.clicked.connect(self.open_listing)
        bottom.addWidget(self.open_button)
        self.whisper_button = QPushButton("📋 Copy whisper")
        self.whisper_button.setToolTip("For sellers without hideout trading: paste it in game chat yourself")
        self.whisper_button.clicked.connect(self.copy_whisper)
        self.whisper_button.hide()  # scan only lists Instant Buyout now – no whispering needed
        bottom.addWidget(self.whisper_button)
        note = QLabel("Profit if you win = reward − sacrifice − price · Loss if you fail = sacrifice + price · "
                      "You must win = how often you have to beat the trial to come out ahead. 🔥 = steal (must win ≤ 55%).")
        note.setStyleSheet("color: #6b7280; font-size: 11px;")
        bottom.addWidget(note, stretch=1)
        lay.addLayout(bottom)

    # ---------- control ----------
    def toggle_scan(self) -> None:
        if self.scanner.running():
            self.scanner.stop()
            self.start_button.setText("▶ Start scan")
            self.set_status("⏹ stopping after the current request …")
            return
        self.scanner.only_clean = self.clean_check.isChecked()
        self.scanner.min_potential = float(self.min_spin.value())
        self.scanner.start()
        self.start_button.setText("⏹ Stop scan")

    def set_status(self, text: str) -> None:
        if text.startswith("✅ pass done"):
            self._alerts_ready = True  # after the first full pass, only *new* steals beep
        if text.startswith(("⏳", "💤")) and self.status_label.text().startswith("🔥"):
            return  # keep a steal alert visible instead of the pacing chatter
        self.status_label.setText(text)
        if text.startswith("⏹ stopped"):
            self.start_button.setText("▶ Start scan")

    # ---------- table ----------
    HOT_RATIO = 0.8  # profit ÷ risk from here on counts as a steal (🔥 + a beep when a new one shows up)

    def add_deals(self, sacrifice: str, deals: List[UltimatumDeal]) -> None:
        seen = {d.listing_id for ds in self.deals.values() for d in ds}
        self.deals[sacrifice] = list(deals)
        hot = [d for d in deals if d.listing_id not in seen and d.profit >= float(self.min_spin.value()) and d.ratio >= self.HOT_RATIO and not d.thin
               and not (self.clean_check.isChecked() and d.hard_mods)]
        self.refresh_table()
        if hot and getattr(self, "_alerts_ready", False):
            best = max(hot, key=lambda d: d.profit)
            QApplication.beep()
            QApplication.alert(self)  # flashes the taskbar button
            self.set_status(f"🔥 new steal: {best.count}× {best.sacrifice} for {best.price_text} "
                            f"(+{self.fmt(best.profit)} if you win) – {best.seller}")

    def visible_deals(self) -> List[UltimatumDeal]:
        seen, rows = set(), []
        self._copies: Dict[tuple, int] = {}
        for ds in self.deals.values():
            for d in ds:  # the same listing can show up in the discovery and the targeted search
                key = d.listing_id or (d.seller, d.sacrifice, d.count, d.reward, d.challenge, d.price_text)
                same = (d.seller, d.sacrifice, d.count, d.reward, d.challenge, d.price_text, d.hard_mods)
                if key in seen:
                    continue
                seen.add(key)
                if same in seen:  # the seller lists several identical ones: one row, "(2 listed)"
                    self._copies[same] = self._copies.get(same, 1) + 1
                    continue
                seen.add(same)
                self._copies[same] = 1
                rows.append(d)
        if self.deal_status is not None:  # bought / won / lost / didn't buy: done with, gone from the market
            rows = [d for d in rows if self.deal_status(d.listing_id) in ("", "opened")]
        if self.profitable_check.isChecked():  # same bar as the search setting next to it (e.g. 100c)
            rows = [d for d in rows if d.profit >= float(self.min_spin.value())]
        if self.clean_check.isChecked():
            rows = [d for d in rows if not d.hard_mods]
        if self.thin_check.isChecked():
            rows = [d for d in rows if not d.thin]
        return sorted(rows, key=lambda d: -d.profit)

    def refresh_table(self) -> None:
        rows = self.visible_deals()
        keep = self.selected_deal()  # new results arrive every few seconds: don't lose your selection / scroll
        scroll = self.table.verticalScrollBar().value()
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, d in enumerate(rows):
            cells = [
                _NumItem(self.fmt(d.profit), d.profit), _NumItem(self.fmt(d.loss), d.loss),
                _NumItem(f"≥ {d.breakeven * 100:.0f}%" if d.profit > 0 else "–", d.breakeven), _NumItem(f"{d.price_text}", d.price_chaos), QTableWidgetItem(f"{d.count}× {d.sacrifice}"),
                QTableWidgetItem(d.reward + (f"  ⚠ {d.market}" if d.market else "")), QTableWidgetItem(d.challenge + (f" · ⚠ {d.hard_mods}" if d.hard_mods else "")), _NumItem(str(d.area_level), d.area_level),
                QTableWidgetItem(("👀 " if self.deal_status and self.deal_status(d.listing_id) == "opened" else "") + d.seller + (f"  ({n} listed)" if (n := self._copies.get((d.seller, d.sacrifice, d.count, d.reward, d.challenge, d.price_text, d.hard_mods), 1)) > 1 else "")), QTableWidgetItem(d.indexed),
            ]
            info = self.card_info(d.sacrifice) if self.card_info and "×" in d.reward else None
            if info is not None:  # cards: what a full set turns into, per card – next to what the card sells for
                each = (f"{self.fmt(info.per_card('low'))}–{self.fmt(info.per_card('high'))}" if info.random
                        else self.fmt(info.per_card()))
                cells[5].setText(f"{cells[5].text()}  🎴 turn-in {each}/card")
                cells[5].setToolTip(info.describe(self.fmt) + f"\nThe card itself: {self.fmt(d.reward_chaos / max(1, d.count * 2))} each")
            if d.market:
                cells[5].setForeground(QBrush(QColor("#fbbf24")))
                cells[5].setToolTip("Hard to sell at poe.ninja's price: " + d.market)
            if d.ratio >= self.HOT_RATIO and d.profit >= float(self.min_spin.value()) and not d.thin:  # cheap compared to what's at stake: a steal
                cells[0].setText("🔥 " + cells[0].text())
                cells[0].setToolTip("Priced far below its sacrifice – mispriced listings like this usually sell within minutes.")
            cells[0].setForeground(QBrush(QColor("#6ee7b7" if d.profit > 0 else "#fca5a5")))
            cells[1].setForeground(QBrush(QColor("#fca5a5")))
            cells[2].setForeground(QBrush(QColor("#6ee7b7" if d.breakeven <= 0.5 else "#fde68a" if d.breakeven <= 0.75 else "#fca5a5")))
            cells[2].setToolTip(f"Break-even: win at least {d.breakeven * 100:.0f}% of these, or you lose chaos on average")
            record = self.win_rate(d.challenge) if self.win_rate else None
            if record and d.profit > 0:  # your own results for this challenge (📒 My ultimatums)
                wins, done = record
                rate = wins / done
                cells[2].setText(f"{cells[2].text()} · you {rate * 100:.0f}%")
                ok = rate >= d.breakeven
                cells[2].setForeground(QBrush(QColor("#6ee7b7" if ok and done >= 5 else "#fde68a" if ok else "#fca5a5")))
                cells[2].setToolTip(f"Break-even {d.breakeven * 100:.0f}% · you won {wins} of {done} {d.challenge} "
                                    + ("(few runs yet – a rough guide)" if done < 5 else ""))
            cells[0].setData(Qt.ItemDataRole.UserRole, d.listing_id)
            for c, item in enumerate(cells):
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        if keep is not None:
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == keep.listing_id:
                    self.table.setCurrentCell(r, self.table.currentColumn() if self.table.currentColumn() >= 0 else 0)
                    break
        self.table.verticalScrollBar().setValue(scroll)
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)

    def selected_deal(self) -> Optional[UltimatumDeal]:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        lid = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((d for ds in self.deals.values() for d in ds if d.listing_id == lid), None)

    def copy_whisper(self) -> None:
        d = self.selected_deal()
        if d and d.whisper:
            QGuiApplication.clipboard().setText(d.whisper)
            self.set_status(f"📋 Whisper to {d.seller} copied – paste it in game chat (Enter, Ctrl+V)")

    def open_listing(self) -> None:
        d = self.selected_deal()
        if d is None:
            return
        if self._open_deal is not None:
            self.set_status(f"🔗 opening {d.seller}'s listing on the trade site …")
            self._open_deal(d)
        elif d.url:
            QDesktopServices.openUrl(QUrl(d.url))

    def open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))
        self.set_status("🔗 opened in your browser – use “Travel to Hideout” there (Instant Buyout)")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.scanner.stop()
        super().closeEvent(event)
