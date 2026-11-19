"""
Small always-on-top popup with the translation of the item you copied (Ctrl+C).

- Independent of the in-game overlay (works with Alt+L on or off).
- Opens right next to the mouse cursor (= next to the item you copied), at an offset you can change by dragging
  the popup once; the offset is remembered and mirrored on the other half of the screen.
- Stays until you click ✕ (or copy the next item). It never takes keyboard focus away from the game, and clicks
  outside the popup still go to the game.
Reading the cursor position is a passive OS call; nothing is sent to the game.
"""

from __future__ import annotations

import html

from typing import Optional, Tuple

from PyQt6.QtCore import QPoint, Qt, QRect
from PyQt6.QtGui import QCursor, QGuiApplication, QMouseEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class TranslationPopup(QWidget):
    WIDTH = 470
    GAP = 30  # default distance between the cursor and the popup
    DEFAULT_DY = -150

    def __init__(self, settings_mgr=None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings_mgr = settings_mgr
        self._anchor: Optional[QPoint] = None
        self._drag_offset: Optional[QPoint] = None
        self.setWindowTitle("Trialmaster's Ledger – Price")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus  # clicking it never steals focus from Path of Exile
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(self.WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(self)
        frame.setObjectName("TranslationFrame")
        frame.setStyleSheet(
            "QFrame#TranslationFrame { background-color: rgba(23, 21, 56, 240); border: 1px solid #818cf8; "
            "border-radius: 8px; }"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 6, 8, 10)
        lay.setSpacing(2)

        head = QHBoxLayout()
        self.title = QLabel("", frame)
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setStyleSheet("color: #a5b4fc; font-size: 12px; font-weight: bold; background: transparent; border: none;")
        head.addWidget(self.title, stretch=1)
        self.set_language(str(getattr(getattr(settings_mgr, "settings", None), "translate_target", "pl") or "pl"))
        self.close_button = QPushButton("✕", frame)
        self.close_button.setFixedSize(24, 22)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setStyleSheet(
            "QPushButton { color: #e0e7ff; background: #312e81; border: 1px solid #6366f1; border-radius: 4px; "
            "font-weight: bold; } QPushButton:hover { background: #4338ca; }"
        )
        self.close_button.clicked.connect(self.hide)
        head.addWidget(self.close_button)
        lay.addLayout(head)

        self.label = QLabel("", frame)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setStyleSheet("color: #e0e7ff; font-size: 13px; background: transparent; border: none;")
        lay.addWidget(self.label)
        # Optional clickable link (e.g. "open this search on pathofexile.com"); opens in your browser
        self.link_label = QLabel("", frame)
        self.link_label.setTextFormat(Qt.TextFormat.RichText)
        self.link_label.setOpenExternalLinks(True)
        self.link_label.setStyleSheet("color: #93c5fd; font-size: 12px; background: transparent; border: none;")
        self.link_label.setVisible(False)
        lay.addWidget(self.link_label)
        outer.addWidget(frame)

        # The text labels must not swallow mouse presses: let them fall through to the popup so it can be dragged
        for w in (self.title, self.label):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def set_title(self, text: str) -> None:
        self.title.setText(f"{html.escape(text)}  <span style='color:#a5b4fc;font-weight:normal;'>· drag to move</span>")

    def set_language(self, code: str) -> None:
        self.title.setText(f"🌐 {html.escape(code.upper())}  <span style='color:#a5b4fc;font-weight:normal;'>· drag to move</span>")

    # ---------- placement ----------
    def _saved_offset(self) -> QPoint:
        cfg = getattr(self.settings_mgr, "settings", None)
        dx = getattr(cfg, "translation_popup_dx", None)
        dy = getattr(cfg, "translation_popup_dy", None)
        if dx is None or dy is None:
            return QPoint(-(self.WIDTH + self.GAP), self.DEFAULT_DY)  # left of the cursor (cursor on the right half)
        return QPoint(int(dx), int(dy))

    @staticmethod
    def _is_right_half(anchor: QPoint) -> bool:
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        return screen is None or anchor.x() >= screen.availableGeometry().center().x()

    def _position_for(self, anchor: QPoint) -> QPoint:
        off = self._saved_offset()
        x = anchor.x() + off.x()
        if not self._is_right_half(anchor):
            # Mirror horizontally on the left half of the screen
            x = anchor.x() - off.x() - self.width()
        pos = QPoint(x, anchor.y() + off.y())
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            pos.setX(max(geo.left() + 4, min(pos.x(), geo.right() - self.width() - 4)))
            pos.setY(max(geo.top() + 4, min(pos.y(), geo.bottom() - self.height() - 4)))
        return pos

    def _position_beside(self, rect: QRect) -> QPoint:
        """Right next to a window (the passive search box): on its right if there is room, else on its left."""
        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        x, y = rect.right() + self.GAP // 3, rect.top()
        if screen is not None:
            geo = screen.availableGeometry()
            if x + self.width() > geo.right() - 4:
                x = rect.left() - self.GAP // 3 - self.width()
            x = max(geo.left() + 4, min(x, geo.right() - self.width() - 4))
            y = max(geo.top() + 4, min(y, geo.bottom() - self.height() - 4))
        return QPoint(x, y)

    def show_html(self, html_text: str, anchor: Optional[QPoint] = None, beside: Optional[QRect] = None,
                  link: Optional[Tuple[str, str]] = None) -> None:
        """Show the translation next to the mouse cursor (the item you copied), or beside a given window."""
        self.label.setText(html_text)
        if link:
            self.link_label.setText(f"<a style='color:#93c5fd;' href='{html.escape(link[0])}'>{html.escape(link[1])}</a>")
        self.link_label.setVisible(bool(link))
        self.adjustSize()
        self._anchor = anchor if anchor is not None else QCursor.pos()
        self.move(self._position_beside(beside) if beside is not None else self._position_for(self._anchor))
        self.show()
        self.raise_()
        self._pin_topmost()

    def _pin_topmost(self) -> None:
        """Re-assert 'always on top' for this popup without activating it (PoE keeps keyboard focus)."""
        import sys

        if sys.platform != "win32" or QGuiApplication.platformName() == "offscreen":
            return
        import ctypes

        HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = -1, 0x1, 0x2, 0x10, 0x40
        ctypes.windll.user32.SetWindowPos(
            int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        )

    # ---------- dragging (remembers the offset to the cursor) ----------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drag_offset is not None:
            self._drag_offset = None
            self.remember_current_offset()
            event.accept()

    def remember_current_offset(self) -> None:
        """Store where the popup sits relative to the item/cursor, so it opens there next time."""
        if self._anchor is None or self.settings_mgr is None:
            return
        dx = self.x() - self._anchor.x()
        if not self._is_right_half(self._anchor):
            dx = -(self.x() + self.width() - self._anchor.x())  # store in right-half terms (mirrored)
        cfg = self.settings_mgr.settings
        cfg.translation_popup_dx = int(dx)
        cfg.translation_popup_dy = int(self.y() - self._anchor.y())
        self.settings_mgr.save()
