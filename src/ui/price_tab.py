"""💰 Price check tab: which key, how it works, and your recent checks (click one to see its full answer again)."""

from __future__ import annotations

import time
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.hotkeys import FKEYS
from src.price_service import PriceAnswer


class PriceTab(QWidget):
    MAX_ROWS = 100

    def __init__(self, key: str, on_key: Callable[[str], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.answers: List[PriceAnswer] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        row = QHBoxLayout()
        row.addWidget(QLabel("Hover an item in game and press"))
        self.key_combo = QComboBox()
        for k in FKEYS:
            self.key_combo.addItem(f"Ctrl+{k}", k)
        self.key_combo.setCurrentIndex(FKEYS.index(key) if key in FKEYS else 2)
        self.key_combo.currentIndexChanged.connect(lambda i: on_key(self.key_combo.itemData(i)))
        row.addWidget(self.key_combo)
        row.addStretch(1)
        lay.addLayout(row)
        how = QLabel(
            "Works on <b>Inscribed Ultimatums</b> (precise trade search: challenge, reward, sacrifice amount, hard "
            "modifiers – plus what you'd make buying at the median and how often you must win), "
            "<b>Heist curios</b> and other items that are only a picture, <b>uniques, gems, currency, cards, bases</b> "
            "(poe.ninja) and <b>rares</b> (the trade site, by their affixes).<br>"
            "<span style='color:#9ca3af;'>It takes one screenshot when you press the key and reads it on this PC – "
            "nothing is sent to the game, like the Print key. Ctrl+D (Awakened PoE Trade) is left alone.</span>")
        how.setWordWrap(True)
        how.setTextFormat(Qt.TextFormat.RichText)
        how.setStyleSheet("background-color: #0f131b; border: 1px solid #1f2937; border-radius: 6px; padding: 8px;")
        lay.addWidget(how)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        split.addWidget(self.list)
        self.detail = QLabel("Your price checks show up here.")
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.detail.setOpenExternalLinks(True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.detail.setStyleSheet("background-color: #171538; border: 1px solid #4f46e5; border-radius: 8px; padding: 10px;")
        split.addWidget(self.detail)
        split.setSizes([380, 620])
        lay.addWidget(split, stretch=1)

    def add(self, answer: PriceAnswer) -> None:
        self.answers.insert(0, answer)
        del self.answers[self.MAX_ROWS:]
        when = time.strftime("%H:%M", time.localtime(answer.at))
        text = f"{when}  {answer.item or 'not recognised'}" + (f" – {answer.summary}" if answer.summary else "")
        if answer.error:
            text = f"{when}  ⚠ {answer.error[:70]}"
        self.list.insertItem(0, QListWidgetItem(text))
        while self.list.count() > self.MAX_ROWS:
            self.list.takeItem(self.list.count() - 1)
        self.list.setCurrentRow(0)

    def _show(self, row: int) -> None:
        if not 0 <= row < len(self.answers):
            return
        a = self.answers[row]
        body = a.html or f"<span style='color:#fca5a5;'>{a.error}</span>"
        if a.link and a.link[0]:
            body += f"<br><br><a style='color:#93c5fd;' href='{a.link[0]}'>{a.link[1]}</a>"
        self.detail.setText(body)
