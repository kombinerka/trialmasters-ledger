"""⚙ Settings: league, your optional trade login, and what the app does / doesn't do."""

from __future__ import annotations

from typing import Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SettingsTab(QWidget):
    def __init__(self, settings_mgr, on_league: Callable[[str], None], on_session: Callable[[str], bool],
                 session_set: bool, mod_names: Optional[List[str]] = None, hard_mods: Optional[List[str]] = None,
                 on_hard_mods: Optional[Callable[[List[str]], None]] = None, defaults: Optional[List[str]] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        cfg = settings_mgr.settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        grid = QGridLayout()
        grid.addWidget(QLabel("League:"), 0, 0)
        self.league_combo = QComboBox()
        self.league_combo.setEditable(True)
        self.league_combo.addItem(cfg.league or "(current league)")
        self.league_combo.currentTextChanged.connect(lambda t: on_league("" if t.startswith("(") else t.strip()))
        grid.addWidget(self.league_combo, 0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        sess = QHBoxLayout()
        sess.addWidget(QLabel("🔑 Trade login (optional, your POESESSID):"))
        self.session_edit = QLineEdit()
        self.session_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.session_edit.setPlaceholderText("saved ✓ – paste a new one to replace" if session_set
                                             else "32 characters, from your browser's cookies")
        sess.addWidget(self.session_edit, stretch=1)
        save_btn, clear_btn = QPushButton("Save"), QPushButton("Clear")
        sess.addWidget(save_btn)
        sess.addWidget(clear_btn)
        lay.addLayout(sess)

        def _save() -> None:
            ok = on_session(self.session_edit.text())
            self.session_edit.clear()
            self.session_edit.setPlaceholderText("saved ✓ – paste a new one to replace" if ok
                                                 else "that isn't a POESESSID (32 characters)")

        def _clear() -> None:
            on_session("")
            self.session_edit.clear()
            self.session_edit.setPlaceholderText("cleared – searches run logged out")

        save_btn.clicked.connect(_save)
        clear_btn.clicked.connect(_clear)
        note = QLabel(
            "Stored only on this PC (%APPDATA%\\PoECompanion\\poesessid.txt), never in the log or "
            "the app folder. It is your login: never share it, and the app never sends it anywhere but pathofexile.com.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9ca3af; font-size: 11px;")
        lay.addWidget(note)
        # ---- ultimatum: which modifiers count as "hard" for you ----
        hard_box = QVBoxLayout()
        title = QLabel("<b>📜 Ultimatum modifiers you don't want to run</b> <span style='color:#9ca3af;'>– ticked ones "
                       "count as hard: marked ⚠ in the scanner, hidden by “Only without hard modifiers”, and left out "
                       "of the search the 🏠 button opens.</span>")
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.RichText)
        hard_box.addWidget(title)
        self.hard_list = QListWidget()
        # all ~60 modifiers in columns (top to bottom, then the next column) instead of one long scrolling list
        self.hard_list.setFlow(QListView.Flow.TopToBottom)
        self.hard_list.setWrapping(True)
        self.hard_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.hard_list.setSpacing(2)
        self.hard_list.setMinimumHeight(300)
        self.hard_list.setMaximumHeight(420)
        self.hard_list.setStyleSheet(
            "QListWidget::indicator { width: 14px; height: 14px; border: 1px solid #6b7280; border-radius: 3px; "
            "background: #111827; } QListWidget::indicator:checked { background: #b91c1c; border-color: #f87171; }")
        chosen = set(hard_mods or defaults or [])
        names = list(dict.fromkeys(list(mod_names or []) + sorted(chosen)))  # every known modifier, once
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in chosen else Qt.CheckState.Unchecked)
            self.hard_list.addItem(item)
        self._on_hard = on_hard_mods
        self.hard_list.itemChanged.connect(lambda _i: self._hard_changed())
        hard_box.addWidget(self.hard_list)
        reset = QPushButton("Reset to the defaults (Ruin, Drought, Reduced Recovery, Escalating …)")
        self._defaults = list(defaults or [])
        reset.clicked.connect(self.reset_hard_mods)
        hard_row = QHBoxLayout()
        hard_row.addWidget(reset)
        hard_row.addStretch(1)
        hard_box.addLayout(hard_row)
        lay.addLayout(hard_box)
        shared = QLabel(
            "<b>⚠ Shared limits</b> – the trade site counts searches <i>per internet connection (IP)</i> and per account, "
            "not per app. If several people on the same connection (a household, a dorm, a VPN exit) run this app, or "
            "you also use Awakened PoE Trade or the trade site in a browser, you all share one budget: the scanner then "
            "slows down or pauses, and the site may answer “too many requests” and lock searches for a while "
            "(up to an hour). The app backs off on its own – if it says it's paused, just let it wait. poe.ninja can "
            "also be slow or busy now and then; the last known prices are used meanwhile.")
        shared.setWordWrap(True)
        shared.setTextFormat(Qt.TextFormat.RichText)
        shared.setStyleSheet("background-color: #2a1f05; border: 1px solid #a16207; border-radius: 6px; padding: 10px; "
                             "color: #fde68a;")
        lay.addWidget(shared)
        rules = QLabel(
            "<b>Playing by the rules</b><br>"
            "• The app never touches the game: no key presses, clicks or memory reading. Price checks take one "
            "screenshot per key press.<br>"
            "• The trade site is used at its own pace: the app reads the limits it sends with every answer, uses at "
            "most half of each short window (three quarters of the 3-hour one) and always leaves room for your browser "
            "and Awakened PoE Trade.<br>"
            "• Buying happens on pathofexile.com – <i>Travel to Hideout</i> there, as usual.<br>"
            "<span style='color:#9ca3af;'>Not affiliated with or endorsed by Grinding Gear Games. Prices: poe.ninja · "
            "listings: pathofexile.com · base filter: NeverSink (MIT).</span>")
        rules.setWordWrap(True)
        rules.setTextFormat(Qt.TextFormat.RichText)
        rules.setStyleSheet("background-color: #0f131b; border: 1px solid #1f2937; border-radius: 6px; padding: 10px;")
        lay.addWidget(rules)
        lay.addStretch(1)

    def checked_hard_mods(self) -> List[str]:
        return [self.hard_list.item(i).text() for i in range(self.hard_list.count())
                if self.hard_list.item(i).checkState() == Qt.CheckState.Checked]

    def _hard_changed(self) -> None:
        if self._on_hard:
            self._on_hard(self.checked_hard_mods())

    def reset_hard_mods(self) -> None:
        self.hard_list.blockSignals(True)
        for i in range(self.hard_list.count()):
            item = self.hard_list.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.text() in self._defaults else Qt.CheckState.Unchecked)
        self.hard_list.blockSignals(False)
        self._hard_changed()

    def set_leagues(self, leagues: List[str], current: str) -> None:
        self.league_combo.blockSignals(True)
        self.league_combo.clear()
        self.league_combo.addItem("(current league)")
        for lg in leagues:
            self.league_combo.addItem(lg)
        self.league_combo.setCurrentText(current or "(current league)")
        self.league_combo.blockSignals(False)
