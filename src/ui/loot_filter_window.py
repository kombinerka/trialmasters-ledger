"""🧹 Loot filter window: league, strictness, price thresholds, where the filter goes, and 'write now'."""

from __future__ import annotations

from typing import Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.loot_filter import STRICTNESS, TIER4_MODES


class LootFilterWindow(QWidget):
    def __init__(self, settings_mgr, on_write: Callable[[], object], on_open_folder: Callable[[], object],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings_mgr = settings_mgr
        cfg = settings_mgr.settings
        self.setWindowTitle("Trialmaster's Ledger – Loot filter")
        self.resize(620, 470)
        self.setStyleSheet(
            "QWidget { background-color: #0b0e14; color: #f5f5f4; font-family: 'Segoe UI'; font-size: 13px; }"
            "QComboBox, QDoubleSpinBox { background-color: #111827; border: 1px solid #374151; border-radius: 6px; padding: 4px 8px; }"
            "QPushButton { background-color: #1f2937; border: 1px solid #4b5563; border-radius: 6px; padding: 5px 12px; }"
            "QPushButton#Write { background-color: #065f46; border: 1px solid #10b981; font-weight: bold; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        intro = QLabel(
            "Writes <b>TrialmastersLedger.filter</b> into your PoE folder: NeverSink's filter as the base, with poe.ninja "
            "prices for your league on top (valuable uniques, currency, cards and bases get bigger, louder styles). "
            "<span style='color:#9ca3af;'>Nothing is sent to the game – PoE simply reads the file.</span>"
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.addWidget(QLabel("League:"), 0, 0)
        self.league_combo = QComboBox()
        self.league_combo.setEditable(True)
        self.league_combo.addItem(cfg.filter_league or "(current league)")
        self.league_combo.currentTextChanged.connect(self._league_changed)
        grid.addWidget(self.league_combo, 0, 1)
        grid.addWidget(QLabel("Show:"), 1, 0)
        self.strict_combo = QComboBox()
        for label in STRICTNESS:
            self.strict_combo.addItem(label)
        self.strict_combo.setCurrentText(cfg.filter_strictness if cfg.filter_strictness in STRICTNESS else "show a lot")
        self.strict_combo.currentTextChanged.connect(lambda t: self._set("filter_strictness", t))
        grid.addWidget(self.strict_combo, 1, 1)
        grid.addWidget(QLabel("Tier 4 gear (new item tiers):"), 5, 0)
        self.tier4_combo = QComboBox()
        labels = {"highlight": "highlight (blue beam + sound)", "quiet": "quiet (no beam, sound or map icon)", "hide": "hide"}
        for mode in TIER4_MODES:
            self.tier4_combo.addItem(labels[mode], mode)
        self.tier4_combo.setCurrentIndex(max(0, self.tier4_combo.findData(cfg.filter_tier4)))
        self.tier4_combo.currentIndexChanged.connect(lambda i: self._set("filter_tier4", self.tier4_combo.itemData(i)))
        grid.addWidget(self.tier4_combo, 5, 1)
        self.spins = {}
        for row, (field_name, label) in enumerate((("filter_top_chaos", "Top tier (big + sound) from"),
                                                  ("filter_high_chaos", "High tier from"),
                                                  ("filter_mid_chaos", "Highlighted from")), start=2):
            grid.addWidget(QLabel(label), row, 0)
            spin = QDoubleSpinBox()
            spin.setRange(0.5, 100000)
            spin.setDecimals(1)
            spin.setSuffix(" chaos")
            spin.setValue(float(getattr(cfg, field_name)))
            spin.valueChanged.connect(lambda v, f=field_name: self._set(f, float(v)))
            self.spins[field_name] = spin
            grid.addWidget(spin, row, 1)
        for w in (self.league_combo, self.strict_combo, self.tier4_combo, *self.spins.values()):
            w.setMinimumWidth(260)
            w.setMaximumWidth(380)

        # ---- look & extra highlights (right column)
        opts = QVBoxLayout()
        opts.setSpacing(6)
        head = QLabel("<b>Look</b>")
        head.setTextFormat(Qt.TextFormat.RichText)
        opts.addWidget(head)
        self.checks = {}
        for field_name, label, tip in (
                ("filter_sounds", "🔊 Drop sounds", "The app's own rules play a sound for valuable drops"),
                ("filter_beams", "🔦 Light beams", "Coloured beams on valuable drops"),
                ("filter_minimap", "🗺 Minimap icons", "Icons on the minimap for valuable drops")):
            box = QCheckBox(label)
            box.setToolTip(tip)
            box.setChecked(bool(getattr(cfg, field_name, True)))
            box.toggled.connect(lambda v, f=field_name: self._set(f, bool(v)))
            self.checks[field_name] = box
            opts.addWidget(box)
        boost_row = QHBoxLayout()
        boost_row.addWidget(QLabel("Bigger text on highlighted drops:"))
        self.boost_spin = QSpinBox()
        self.boost_spin.setRange(0, 10)
        self.boost_spin.setPrefix("+")
        self.boost_spin.setValue(int(getattr(cfg, "filter_font_boost", 0)))
        self.boost_spin.valueChanged.connect(lambda v: self._set("filter_font_boost", int(v)))
        boost_row.addWidget(self.boost_spin)
        boost_row.addStretch(1)
        opts.addLayout(boost_row)
        head2 = QLabel("<b>Always highlight</b>")
        head2.setTextFormat(Qt.TextFormat.RichText)
        opts.addWidget(head2)
        for field_name, label in (("filter_chaos", "🟡 Chaos Orbs – big and gold"),
                                  ("filter_heist", "🟣 Heist Blueprints and Contracts"),
                                  ("filter_ultimatum", "🔴 Inscribed Ultimatums")):
            box = QCheckBox(label)
            box.setChecked(bool(getattr(cfg, field_name, True)))
            box.toggled.connect(lambda v, f=field_name: self._set(f, bool(v)))
            self.checks[field_name] = box
            opts.addWidget(box)
        self.texts = {}
        for field_name, label, hint in (
                ("filter_always_show", "Always show big (cyan):", "e.g. Orb of Alchemy, Vaal Orb, Stacked Deck"),
                ("filter_always_hide", "Never show:", "e.g. Scroll of Wisdom, Portal Scroll")):
            opts.addWidget(QLabel(label))
            edit = QLineEdit(str(getattr(cfg, field_name, "") or ""))
            edit.setPlaceholderText(hint + " – exact item names, comma separated")
            edit.setMinimumWidth(420)
            edit.editingFinished.connect(lambda f=field_name, e=edit: self._set(f, e.text().strip()))
            self.texts[field_name] = edit
            opts.addWidget(edit)
        note = QLabel("Names the game doesn't know are skipped, so a typo never breaks the filter.")
        note.setStyleSheet("color: #9ca3af; font-size: 11px;")
        opts.addWidget(note)
        opts.addStretch(1)
        columns = QHBoxLayout()
        columns.addLayout(grid)
        columns.addSpacing(40)
        columns.addLayout(opts)
        columns.addStretch(1)
        lay.addLayout(columns)

        self.auto_check = QCheckBox("Keep it up to date (new prices every hour while the app runs)")
        self.auto_check.setChecked(cfg.filter_auto)
        self.auto_check.toggled.connect(lambda v: self._set("filter_auto", bool(v)))
        lay.addWidget(self.auto_check)

        folder_row = QHBoxLayout()
        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color: #9ca3af; font-size: 12px;")
        folder_row.addWidget(self.folder_label, stretch=1)
        change = QPushButton("Change folder…")
        change.clicked.connect(self._choose_folder)
        folder_row.addWidget(change)
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(lambda: on_open_folder())
        folder_row.addWidget(open_btn)
        lay.addLayout(folder_row)

        self.write_button = QPushButton("🧹 Write / update the filter now")
        self.write_button.setObjectName("Write")
        self.write_button.clicked.connect(lambda: on_write())
        lay.addWidget(self.write_button)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status_label)
        howto = QLabel(
            "<b>In game, once:</b> Escape → Options → UI → Item Filter → pick <b>TrialmastersLedger</b>.<br>"
            "<b>After an update:</b> pick it again there (or use the reload button next to it) – the game only reads "
            "the file when you do."
        )
        howto.setWordWrap(True)
        howto.setTextFormat(Qt.TextFormat.RichText)
        howto.setStyleSheet("color: #d1d5db; font-size: 12px; background-color: #0f131b; border: 1px solid #1f2937; border-radius: 6px; padding: 8px;")
        lay.addWidget(howto)
        credit = QLabel("Base filter: NeverSink (MIT) · prices: poe.ninja")
        credit.setStyleSheet("color: #6b7280; font-size: 10px;")
        lay.addWidget(credit)
        lay.addStretch(1)

    def set_leagues(self, leagues: List[str]) -> None:
        cfg = self.settings_mgr.settings
        current = cfg.filter_league or (leagues[0] if leagues else "")
        self.league_combo.blockSignals(True)
        self.league_combo.clear()
        for lg in leagues:
            self.league_combo.addItem(lg)
        if current and self.league_combo.findText(current) < 0:
            self.league_combo.addItem(current)
        self.league_combo.setCurrentText(current)
        self.league_combo.blockSignals(False)

    def set_folder(self, path: str) -> None:
        self.folder_label.setText(f"Folder: {path}")

    def set_status(self, html_text: str) -> None:
        self.status_label.setText(html_text)

    def _league_changed(self, text: str) -> None:
        if text and not text.startswith("("):
            self._set("filter_league", text.strip())

    def _set(self, field_name: str, value) -> None:
        setattr(self.settings_mgr.settings, field_name, value)
        self.settings_mgr.save()

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Path of Exile folder (where .filter files go)", self.folder_label.text()[8:])
        if path:
            self._set("filter_dir", path)
            self.set_folder(path)
