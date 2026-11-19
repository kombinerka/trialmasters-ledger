"""
Global Ctrl+F-key hotkey, read the same way overlays like Awakened PoE Trade do: the app only *looks* whether the keys
are held (GetAsyncKeyState) - it never sends keys or clicks to the game. One key press = one price check.
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QGuiApplication

FKEYS = [f"F{i}" for i in range(1, 13)]
VK_CONTROL, VK_MENU = 0x11, 0x12


def fkey_vk(name: str) -> int:
    name = (name or "F3").upper()
    return 0x70 + (FKEYS.index(name) if name in FKEYS else 2)


class HotkeyPoller(QObject):
    """Calls `on_press(name)` on the rising edge of Ctrl+<F-key> (not with Alt held), at most every 0.3 s."""

    def __init__(self, keys: Callable[[], Dict[str, str]], on_press: Callable[[str], object],
                 key_state: Optional[Callable[[int], int]] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.keys, self.on_press = keys, on_press
        self._state = key_state
        self._down: Dict[str, bool] = {}
        self._last: Dict[str, float] = {}
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.poll)

    def start(self) -> bool:
        if self._state is None:
            if sys.platform != "win32" or QGuiApplication.platformName() == "offscreen":
                return False
            self._state = ctypes.windll.user32.GetAsyncKeyState
        self.timer.start()
        return True

    def poll(self) -> None:
        get = self._state
        if get is None:
            return
        ctrl = bool(get(VK_CONTROL) & 0x8000) and not bool(get(VK_MENU) & 0x8000)
        for name, fkey in self.keys().items():
            down = ctrl and bool(get(fkey_vk(fkey)) & 0x8000)
            was = self._down.get(name, False)
            self._down[name] = down
            if down and not was and time.monotonic() - self._last.get(name, 0.0) > 0.3:
                self._last[name] = time.monotonic()
                self.on_press(name)
