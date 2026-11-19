"""
Trialmaster's Ledger – Inscribed Ultimatum scanner, Heist curio / item price check and deal tracking for Path of Exile 1.

Run:  .venv\\Scripts\\python.exe main.py   (or double-click run_trialmasters_ledger.bat)
"""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from src.logger import setup_logging
from src.settings import SettingsManager
from src.ui.main_window import MainWindow


def main() -> int:
    logger = setup_logging()
    logger.info("=== Launching Trialmaster's Ledger ===")
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Trialmaster's Ledger")
        window = MainWindow(SettingsManager())
        window.show()
        logger.info("Entering Qt event loop")
        code = app.exec()
        logger.info("Exited with code %d", code)
        return code
    except Exception:
        logger.exception("Fatal error during startup")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
