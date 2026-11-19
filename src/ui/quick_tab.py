"""🔎 Quick search tab: one-off searches for Inscribed Ultimatums (by reward) and Heist Blueprints (by revealed wings)."""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.quick_search import BLUEPRINTS, ULTIMATUM_REWARDS
from src.trade_search import TradeBusy, TradeQueryError
from src.ui.ultimatum_scan_window import _NumItem

U_COLUMNS = ["Profit if you win", "Loss if you fail", "You must win", "Price", "Sacrifice", "Reward", "Challenge", "Lvl", "Seller"]
B_COLUMNS = ["Price", "Blueprint", "Wings", "Escape routes", "Reward rooms", "Area lvl", "Seller", "Listed"]


class _Bridge(QObject):
    done = pyqtSignal(str, object)  # which ("u" / "b"), (rows, url, total) or an error text


def _table(columns: List[str]) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSortingEnabled(True)
    return t


class QuickSearchTab(QWidget):
    def __init__(self, search_ultimatums: Callable, search_blueprints: Callable, fmt: Callable[[float], str],
                 item_names: Callable[[], List[str]], open_deal: Optional[Callable] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._search_u, self._search_b, self.fmt, self._open_deal = search_ultimatums, search_blueprints, fmt, open_deal
        self._bridge = _Bridge()
        self._bridge.done.connect(self._done)
        self.u_rows: list = []
        self.b_rows: list = []
        self.urls = {"u": "", "b": ""}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        intro = QLabel("One search when you press the button – nothing runs in the background. Each search is one "
                       "trade-site search plus a listing read, inside the same rate limits as everything else.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #9ca3af;")
        lay.addWidget(intro)

        # ---- Inscribed Ultimatums
        ubox = QGroupBox("📜 Inscribed Ultimatums by reward")
        ul = QVBoxLayout(ubox)
        row = QHBoxLayout()
        self.u_reward = QComboBox()
        for label in ULTIMATUM_REWARDS:
            self.u_reward.addItem(label, ULTIMATUM_REWARDS[label])
        self.u_reward.currentIndexChanged.connect(self._reward_changed)
        row.addWidget(self.u_reward)
        self.u_item_label = QLabel("Sacrifice:")
        row.addWidget(self.u_item_label)
        self.u_item = QComboBox()
        self.u_item.setEditable(True)
        self.u_item.setMinimumWidth(260)
        self.u_item.lineEdit().setPlaceholderText("e.g. Divine Orb (empty = any)")
        names = sorted(set(item_names() or []))
        self.u_item.addItems([""] + names)
        completer = QCompleter(names, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.u_item.setCompleter(completer)
        self.u_item.setCurrentText("Divine Orb")
        row.addWidget(self.u_item)
        row.addWidget(QLabel("under"))
        self.u_max = QDoubleSpinBox()
        self.u_max.setRange(0, 1_000_000)
        self.u_max.setDecimals(0)
        self.u_max.setSuffix(" chaos")
        self.u_max.setSpecialValueText("any price")
        self.u_max.setValue(150)
        row.addWidget(self.u_max)
        self.u_button = QPushButton("🔎 Search")
        self.u_button.clicked.connect(self.run_ultimatums)
        row.addWidget(self.u_button)
        u_open = QPushButton("Open on pathofexile.com ↗")
        u_open.clicked.connect(lambda: self._open("u"))
        row.addWidget(u_open)
        row.addStretch(1)
        ul.addLayout(row)
        self.u_status = QLabel("")
        self.u_status.setStyleSheet("color: #9ca3af;")
        ul.addWidget(self.u_status)
        self.u_table = _table(U_COLUMNS)
        self.u_table.cellDoubleClicked.connect(lambda r, _c: self._open_u(r))
        ul.addWidget(self.u_table)
        lay.addWidget(ubox, stretch=1)

        # ---- Heist Blueprints
        bbox = QGroupBox("🗝 Heist Blueprints by revealed wings")
        bl = QVBoxLayout(bbox)
        row = QHBoxLayout()
        self.b_type = QComboBox()
        self.b_type.addItems(list(BLUEPRINTS))
        row.addWidget(self.b_type)
        row.addWidget(QLabel("Wings revealed:"))
        self.b_wings = QSpinBox()
        self.b_wings.setRange(0, 4)
        self.b_wings.setValue(4)
        row.addWidget(self.b_wings)
        row.addWidget(QLabel("of"))
        self.b_total = QSpinBox()
        self.b_total.setRange(0, 4)
        self.b_total.setSpecialValueText("any")
        self.b_total.setValue(4)
        row.addWidget(self.b_total)
        row.addWidget(QLabel("Area level from"))
        self.b_area = QSpinBox()
        self.b_area.setRange(0, 86)
        self.b_area.setSpecialValueText("any")
        self.b_area.setValue(83)
        row.addWidget(self.b_area)
        row.addWidget(QLabel("under"))
        self.b_max = QDoubleSpinBox()
        self.b_max.setRange(0, 1_000_000)
        self.b_max.setDecimals(0)
        self.b_max.setSuffix(" chaos")
        self.b_max.setSpecialValueText("any price")
        self.b_max.setValue(0)
        row.addWidget(self.b_max)
        self.b_button = QPushButton("🔎 Search")
        self.b_button.clicked.connect(self.run_blueprints)
        row.addWidget(self.b_button)
        b_open = QPushButton("Open on pathofexile.com ↗")
        b_open.clicked.connect(lambda: self._open("b"))
        row.addWidget(b_open)
        row.addStretch(1)
        bl.addLayout(row)
        self.b_status = QLabel("")
        self.b_status.setStyleSheet("color: #9ca3af;")
        bl.addWidget(self.b_status)
        self.b_table = _table(B_COLUMNS)
        self.b_table.cellDoubleClicked.connect(lambda _r, _c: self._open("b"))
        bl.addWidget(self.b_table)
        lay.addWidget(bbox, stretch=1)

    # ---------- actions ----------
    def _reward_changed(self) -> None:
        exchange = self.u_reward.currentData() == "ExchangeUnique"
        self.u_item_label.setText("Unique you get:" if exchange else "Sacrifice:")
        self.u_item.lineEdit().setPlaceholderText("e.g. Defiance of Destiny (empty = any)" if exchange
                                                  else "e.g. Divine Orb (empty = any)")

    def _run(self, which: str, work: Callable, sync: bool) -> None:
        button, status = (self.u_button, self.u_status) if which == "u" else (self.b_button, self.b_status)
        button.setEnabled(False)
        status.setText("⏳ searching…")

        def job():
            try:
                return work()
            except TradeBusy as exc:
                return f"⏸ {exc}"
            except TradeQueryError as exc:
                return f"⚠ The trade site rejected the search: {exc}"
            except Exception as exc:  # noqa: BLE001 - shown to the user
                return f"⚠ {exc}"

        if sync:
            self._done(which, job())
        else:
            threading.Thread(target=lambda: self._bridge.done.emit(which, job()), daemon=True).start()

    def run_ultimatums(self, sync: bool = False) -> None:
        reward, item, max_c = self.u_reward.currentData(), self.u_item.currentText().strip(), float(self.u_max.value())
        self._run("u", lambda: self._search_u(reward, item, max_c), sync)

    def run_blueprints(self, sync: bool = False) -> None:
        bp, wings, total = self.b_type.currentText(), int(self.b_wings.value()), int(self.b_total.value())
        area, max_c = int(self.b_area.value()), float(self.b_max.value())
        self._run("b", lambda: self._search_b(bp, wings, total, area, max_c), sync)

    def _done(self, which: str, payload) -> None:
        button, status = (self.u_button, self.u_status) if which == "u" else (self.b_button, self.b_status)
        button.setEnabled(True)
        if isinstance(payload, str):
            status.setText(payload)
            return
        rows, url, total = payload
        self.urls[which] = url
        status.setText(f"{total} listed (Instant Buyout) · showing the cheapest {len(rows)} · double-click a row to open it")
        (self._fill_u if which == "u" else self._fill_b)(rows)

    def _fill_u(self, deals: list) -> None:
        self.u_rows = sorted(deals, key=lambda d: d.price_chaos)
        t = self.u_table
        t.setSortingEnabled(False)
        t.setRowCount(len(self.u_rows))
        for r, d in enumerate(self.u_rows):
            cells = [
                _NumItem(("+" if d.profit >= 0 else "−") + self.fmt(abs(d.profit)), d.profit),
                _NumItem("−" + self.fmt(d.loss), -d.loss),
                _NumItem(f"≥ {d.breakeven * 100:.0f}%" if d.profit > 0 else "–", d.breakeven),
                _NumItem(d.price_text, d.price_chaos), QTableWidgetItem(f"{d.count}× {d.sacrifice}"),
                QTableWidgetItem(d.reward + (f"  ⚠ {d.market}" if d.market else "")),
                QTableWidgetItem(d.challenge + (f" · ⚠ {d.hard_mods}" if d.hard_mods else "")),
                _NumItem(str(d.area_level), d.area_level), QTableWidgetItem(d.seller),
            ]
            cells[0].setForeground(QBrush(QColor("#6ee7b7" if d.profit > 0 else "#fca5a5")))
            cells[1].setForeground(QBrush(QColor("#fca5a5")))
            cells[0].setData(Qt.ItemDataRole.UserRole, r)
            for c, item in enumerate(cells):
                t.setItem(r, c, item)
        t.setSortingEnabled(True)

    def _fill_b(self, rows: list) -> None:
        self.b_rows = rows
        t = self.b_table
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        for r, b in enumerate(rows):
            cells = [
                _NumItem(b.price_text + (f"  ({self.fmt(b.price_chaos)})" if b.price_chaos and "chaos" not in b.price_text else ""),
                         b.price_chaos or 0.0),
                QTableWidgetItem(b.name), QTableWidgetItem(b.wings), QTableWidgetItem(b.escape_routes),
                QTableWidgetItem(b.reward_rooms), _NumItem(str(b.area_level), b.area_level),
                QTableWidgetItem(b.seller), QTableWidgetItem(b.indexed),
            ]
            for c, item in enumerate(cells):
                t.setItem(r, c, item)
        t.setSortingEnabled(True)

    def _open_u(self, row: int) -> None:
        item = self.u_table.item(row, 0)
        idx = item.data(Qt.ItemDataRole.UserRole) if item else None
        if idx is None or not 0 <= idx < len(self.u_rows):
            return
        if self._open_deal is not None:  # that seller's listing (and it lands in 📒 My ultimatums)
            self._open_deal(self.u_rows[idx])
        else:
            self._open("u")

    def _open(self, which: str) -> None:
        if self.urls.get(which):
            QDesktopServices.openUrl(QUrl(self.urls[which]))
