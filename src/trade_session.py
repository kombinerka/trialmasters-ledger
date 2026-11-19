"""
Your own POESESSID (optional): lets the companion search the trade site logged in, like your browser - bigger queries
(e.g. a long NOT group). It is stored only on this PC, OUTSIDE the app folder (%APPDATA%\\PoECompanion\\), so sharing
the companion never includes it, and it is never written to the log. Treat it like a password: it is your login.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

_VALID = re.compile(r"^[0-9a-fA-F]{32}$")


def default_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "PoECompanion" / "poesessid.txt"


def is_valid(value: str) -> bool:
    """A POESESSID is 32 hex characters."""
    return bool(_VALID.match((value or "").strip()))


def load(path: Optional[Path] = None) -> str:
    try:
        value = (path or default_path()).read_text(encoding="utf-8").strip()
        return value if is_valid(value) else ""
    except OSError:
        return ""


def save(value: str, path: Optional[Path] = None) -> bool:
    value = (value or "").strip()
    if not is_valid(value):
        return False
    p = path or default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value, encoding="utf-8")
    return True


def clear(path: Optional[Path] = None) -> None:
    try:
        (path or default_path()).unlink()
    except OSError:
        pass
