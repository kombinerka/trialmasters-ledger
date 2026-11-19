"""Settings of Trialmaster's Ledger, kept in config/settings.json next to the app."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("poe_companion")

APP_DIR = Path(__file__).resolve().parent.parent


@dataclass
class AppSettings:
    league: str = ""  # "" = the current challenge league (asked from poe.ninja)
    price_key: str = "F3"  # Ctrl + this key prices the item under the mouse
    # price popup position relative to the mouse (the popup remembers where you drag it)
    translation_popup_dx: int = 30
    translation_popup_dy: int = -150
    translate_target: str = "price"
    # ultimatum scanner
    scan_min_gain: float = 100.0
    scan_only_clean: bool = True
    scan_hide_thin: bool = True
    scan_only_good: bool = True
    hard_mods: List[str] = field(default_factory=list)  # ultimatum modifiers you don't want to run ([] = defaults)
    # loot filter
    filter_league: str = ""
    filter_strictness: str = "Soft"  # NeverSink's levels: Soft … Uber Plus Strict
    filter_tier4: str = "highlight"
    filter_top_chaos: float = 100.0
    filter_high_chaos: float = 20.0
    filter_mid_chaos: float = 5.0
    filter_auto: bool = True
    filter_dir: str = ""
    filter_written: bool = False
    filter_sounds: bool = True
    filter_beams: bool = True
    filter_minimap: bool = True
    filter_font_boost: int = 0
    filter_chaos: bool = True
    filter_heist: bool = True
    filter_ultimatum: bool = True
    filter_always_show: str = ""  # comma separated base types
    filter_always_hide: str = ""
    # window
    window_x: int = -1
    window_y: int = -1
    window_w: int = 1280
    window_h: int = 760
    last_tab: int = 0


class SettingsManager:
    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.config_path = Path(config_path) if config_path else APP_DIR / "config" / "settings.json"
        self.settings = AppSettings()
        self.load()

    @property
    def cache_dir(self) -> Path:
        return self.config_path.parent / "cache"

    def load(self) -> None:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        known = {f.name for f in fields(AppSettings)}
        for key, value in (data or {}).items():
            if key in known:
                setattr(self.settings, key, value)

    def save(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(asdict(self.settings), indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Couldn't save settings: %s", exc)
