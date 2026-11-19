"""
Trialmaster's Ledger main window: one window, five tabs.

  📜 Ultimatum scanner · 📒 My ultimatums · 💰 Price check (Ctrl+F3) · 🧹 Loot filter · ⚙ Settings
"""

from __future__ import annotations

import html
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QBuffer, QIODevice, QObject, QPoint, QRect, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor, QDesktopServices, QGuiApplication, QImage, QKeySequence, QShortcut
from PyQt6.QtWidgets import QLabel, QMainWindow, QTabWidget

from src import trade_session
from src.card_rewards import CardRewards
from src.deal_history import DealHistory
from src.hotkeys import HotkeyPoller
from src.loot_filter import FilterSettings, LootFilterManager, current_leagues, default_filter_dir
from src.price_check import PriceCheckManager
from src.price_service import PriceAnswer, PriceService
from src.screen_ocr import OcrEngine
from src.settings import SettingsManager
from src.trade_search import LIMITER, SESSION, squash
from src.ui.history_tab import HistoryTab
from src.ui.loot_filter_window import LootFilterWindow
from src.ui.price_tab import PriceTab
from src.ui.quick_tab import QuickSearchTab
from src.quick_search import search_blueprints, search_ultimatums
from src.ui.settings_tab import SettingsTab
from src.ui.translation_popup import TranslationPopup
from src.ui.ultimatum_scan_window import UltimatumScanWindow
from src.ultimatum import DEFAULT_HARD_MODS, NASTY_MODS, set_hard_mods
from src.ultimatum_scan import UltimatumScanner, hard_mods_of, seller_search_url

logger = logging.getLogger("poe_companion")

STYLE = (
    "QMainWindow, QWidget { background-color: #0b0e14; color: #f5f5f4; font-family: 'Segoe UI'; font-size: 13px; }"
    "QTabWidget::pane { border: 1px solid #1f2937; border-radius: 6px; }"
    "QTabBar::tab { background: #111827; color: #d1d5db; padding: 8px 16px; border: 1px solid #1f2937; "
    "border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }"
    "QTabBar::tab:selected { background: #1e293b; color: #fde68a; font-weight: bold; }"
    "QComboBox, QDoubleSpinBox, QLineEdit { background-color: #111827; border: 1px solid #374151; border-radius: 6px; padding: 4px 8px; }"
    "QPushButton { background-color: #1f2937; border: 1px solid #4b5563; border-radius: 6px; padding: 5px 12px; }"
    "QPushButton:hover { background-color: #374151; }"
    "QTableWidget, QListWidget { background-color: #0f131b; alternate-background-color: #111827; gridline-color: #1f2937; }"
    "QHeaderView::section { background-color: #111827; color: #93c5fd; border: none; padding: 6px; }"
)


class _Bridge(QObject):
    price = pyqtSignal(object)  # (PriceAnswer, QPoint)
    url = pyqtSignal(str)
    filter_done = pyqtSignal(object)
    leagues = pyqtSignal(object)


class MainWindow(QMainWindow):
    PRICE_REGION_W = 2000  # width of the full-height strip read around the mouse (tall ultimatum tooltips)
    FILTER_EVERY_MS = 60 * 60 * 1000

    def __init__(self, settings_mgr: Optional[SettingsManager] = None, ocr: Optional[OcrEngine] = None,
                 post=None, get=None) -> None:
        super().__init__()
        self.settings_mgr = settings_mgr or SettingsManager()
        cfg = self.settings_mgr.settings
        cache = self.settings_mgr.cache_dir
        self._post, self._get = post, get  # tests swap in fake trade-site calls
        self.setWindowTitle("Trialmaster's Ledger")
        self.setStyleSheet(STYLE)
        LIMITER.use_state_file(cache / "trade_blocked_until.txt")
        set_hard_mods(cfg.hard_mods or DEFAULT_HARD_MODS)  # your own list of ultimatum modifiers you don't want to run
        SESSION["poesessid"] = trade_session.load()
        self.price_check = PriceCheckManager(cache)
        self.loot_filter = LootFilterManager(cache)
        self.ocr = ocr or OcrEngine()
        self.cards = CardRewards(cache)
        self.service = PriceService(self.price_check, lambda: self.settings_mgr.settings.league, post=post, get=get,
                                    cards=self.cards)
        self.history = DealHistory(self.settings_mgr.config_path.parent / "deal_history.json")
        self.popup = TranslationPopup(settings_mgr=self.settings_mgr)
        self.popup.set_title("💰 Price")
        self._bridge = _Bridge()
        self._bridge.price.connect(self._on_price)
        self._bridge.filter_done.connect(self._on_filter_written)
        self._bridge.leagues.connect(self._on_leagues)
        self._price_busy = False
        self._filter_busy = False

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.scan = UltimatumScanWindow(self._make_scanner, self.fmt, open_deal=self.open_deal)
        self.scan.win_rate = self.history.record_for
        self.scan.card_info = self.card_value
        self.scan.deal_status = self.history.status_of
        self._bridge.url.connect(self.scan.open_url)
        self._restore_scan_settings()
        self.history_tab = HistoryTab(self.history, self.fmt, on_changed=self.scan.refresh_table,
                                      divine=lambda: getattr(self.price_check.cached(), "divine_chaos", 0.0) or 0.0,
                                      card_info=self.card_value)
        self.price_tab = PriceTab(cfg.price_key, self.set_price_key)
        self.quick_tab = QuickSearchTab(self.quick_ultimatums, self.quick_blueprints, self.fmt, self._quick_item_names,
                                        open_deal=self.open_deal)
        self.filter_tab = LootFilterWindow(self.settings_mgr, on_write=lambda: self.write_filter(force_prices=True),
                                           on_open_folder=self.open_filter_folder)
        self.filter_tab.set_folder(str(self.filter_dir()))
        self.settings_tab = SettingsTab(self.settings_mgr, self.set_league, self.set_session, bool(SESSION["poesessid"]),
                                        mod_names=self._ultimatum_mod_names(), hard_mods=list(NASTY_MODS),
                                        on_hard_mods=self.set_hard_mods, defaults=list(DEFAULT_HARD_MODS))
        for widget, label in ((self.scan, "📜 Ultimatum scanner"), (self.history_tab, "📒 My ultimatums"),
                              (self.quick_tab, "🔎 Quick search"),
                              (self.price_tab, f"💰 Price check (Ctrl+{cfg.price_key})"),
                              (self.filter_tab, "🧹 Loot filter"),
                              (self.settings_tab, "⚙ Settings")):
            self.tabs.addTab(widget, label)
        self.tabs.setCurrentIndex(max(0, min(int(cfg.last_tab), self.tabs.count() - 1)))
        self.tabs.currentChanged.connect(self._tab_changed)
        status = QLabel("Not affiliated with Grinding Gear Games · prices: poe.ninja · listings: pathofexile.com")
        status.setStyleSheet("color: #6b7280; font-size: 11px; padding: 2px 8px;")
        self.statusBar().addWidget(status)

        self.hotkeys = HotkeyPoller(lambda: {"price": self.settings_mgr.settings.price_key}, self._hotkey)
        if self.hotkeys.start():
            logger.info("Global price-check key active: Ctrl+%s", cfg.price_key)
        self.shortcut = QShortcut(QKeySequence(f"Ctrl+{cfg.price_key}"), self)
        self.shortcut.activated.connect(self.price_check_at_cursor)
        self.filter_timer = QTimer(self)
        self.filter_timer.setInterval(self.FILTER_EVERY_MS)
        self.filter_timer.timeout.connect(self._auto_filter)
        self.filter_timer.start()
        self._fit_to_screen()
        threading.Thread(target=self._load_leagues, daemon=True).start()
        threading.Thread(target=self.ocr.warm_up, daemon=True).start()
        threading.Thread(target=self.cards.ensure, daemon=True).start()  # card turn-ins (wiki), cached a week

    # ---------- helpers ----------
    def fmt(self, chaos: float) -> str:
        idx = self.price_check.cached()  # never downloads on the UI thread
        if idx is None:
            return f"{chaos:,.0f}c"
        return idx.fmt(chaos) if chaos >= 0 else "−" + idx.fmt(-chaos)

    UNREAL_FACTOR = 5.0  # a thinly traded card priced over 5x what its full set turns into: poe.ninja's price isn't real

    def reality_check(self, d) -> None:
        """
        poe.ninja can show a high price for a card hardly anyone trades (e.g. The Fathomless Depths at ~216c while a
        full set turns into items worth under 1c). Then the card is valued at its turn-in instead.
        """
        if "×" not in d.reward or not d.market:
            return
        info = self.card_value(d.sacrifice)
        each = d.sacrifice_chaos / max(1, d.count)
        if info is None or each <= self.UNREAL_FACTOR * max(1.0, info.per_card("high")):
            return
        real = info.per_card("high")
        d.market = (f"poe.ninja's {self.fmt(each)} per card isn't real ({d.market}; a full set turns into "
                    f"≤ {self.fmt(real)} per card) – valued at that")
        d.sacrifice_chaos = real * d.count
        d.reward_chaos = real * d.count * 2

    # ---------- quick search (one-off, you press the button) ----------
    def quick_ultimatums(self, reward_type: str, item: str, max_chaos: float, only_clean: bool = False):
        not_ids: list = []
        if only_clean:  # your hard-modifier list (⚙ Settings) as the site's NOT group
            mods = self.service.trade_data().ultimatum_mods
            not_ids = [i for name in NASTY_MODS for i in mods.get(squash(name), [])]
        deals, url, total = search_ultimatums(self._scan_index(), self.service.current_league(), reward_type, item,
                                              max_chaos, post=self._post, get=self._get, not_ids=not_ids,
                                              only_clean=only_clean)
        for d in deals:
            self.reality_check(d)
        return deals, url, total

    def quick_blueprints(self, blueprint: str, wings: int, total_wings: int, min_area: int, max_chaos: float,
                         target: str = ""):
        idx = self.price_check.cached()
        return search_blueprints(self.service.current_league(), blueprint, wings, total_wings, min_area, max_chaos,
                                 divine_chaos=getattr(idx, "divine_chaos", 0.0) or 0.0, post=self._post, get=self._get,
                                 target=target)

    def _quick_item_names(self) -> list:
        """Sacrifices and unique rewards seen on the market so far, plus the usual currency."""
        import json
        from src.ultimatum_scan import COMMON_SACRIFICES
        names = set(COMMON_SACRIFICES)
        for f in ("ultimatum_sacrifices.json", "ultimatum_rewards.json"):
            try:
                names |= set(json.loads((self.settings_mgr.cache_dir / f).read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                pass
        return sorted(names)

    def card_value(self, name: str):
        """What a full set of this divination card turns into (None if it isn't a card or prices aren't loaded)."""
        return self.cards.value(name, self.price_check.cached())

    def _fit_to_screen(self) -> None:
        cfg = self.settings_mgr.settings
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1600, 900)
        w, h = min(int(cfg.window_w), avail.width()), min(int(cfg.window_h), avail.height())
        self.resize(max(900, w), max(560, h))
        if cfg.window_x >= 0 and cfg.window_y >= 0 and any(
                s.availableGeometry().contains(QPoint(cfg.window_x + 40, cfg.window_y + 40)) for s in QGuiApplication.screens()):
            self.move(cfg.window_x, cfg.window_y)

    def _tab_changed(self, i: int) -> None:
        self.settings_mgr.settings.last_tab = i
        self.settings_mgr.save()
        if self.tabs.widget(i) is self.history_tab:
            self.history_tab.refresh()

    # ---------- settings ----------
    def set_league(self, league: str) -> None:
        self.settings_mgr.settings.league = league
        self.settings_mgr.settings.filter_league = league
        self.settings_mgr.save()

    def set_session(self, value: str) -> bool:
        if not value.strip():
            trade_session.clear()
            SESSION["poesessid"] = ""
            logger.info("Trade login cleared")
            return True
        if not trade_session.save(value):
            return False
        SESSION["poesessid"] = value.strip()
        logger.info("Trade login saved (searches now run logged in)")
        return True

    def _ultimatum_mod_names(self) -> list:
        """Every Inscribed Ultimatum modifier the trade site knows (from its cached stat list), in its order."""
        import json
        try:
            data = json.loads((self.settings_mgr.cache_dir / "trade_stats.json").read_text(encoding="utf-8"))
            group = next(g for g in data.get("result", []) if g.get("id") == "ultimatum")
            return list(dict.fromkeys(e["text"] for e in group.get("entries", []) if e.get("text")))
        except (OSError, ValueError, StopIteration, KeyError, TypeError):
            return list(DEFAULT_HARD_MODS)

    def set_hard_mods(self, names: list) -> None:
        cfg = self.settings_mgr.settings
        # [] = the defaults, ["-"] = you unticked everything
        cfg.hard_mods = [] if sorted(names) == sorted(DEFAULT_HARD_MODS) else (sorted(names) or ["-"])
        self.settings_mgr.save()
        set_hard_mods(names)
        for deals in self.scan.deals.values():  # re-mark what's already in the table
            for d in deals:
                if d.all_mods:
                    d.hard_mods = ", ".join(hard_mods_of(d.all_mods))
        self.scan.refresh_table()

    def set_price_key(self, key: str) -> None:
        self.settings_mgr.settings.price_key = key
        self.settings_mgr.save()
        self.shortcut.setKey(QKeySequence(f"Ctrl+{key}"))
        self.tabs.setTabText(self.tabs.indexOf(self.price_tab), f"💰 Price check (Ctrl+{key})")

    def _load_leagues(self) -> None:
        try:
            self._bridge.leagues.emit(current_leagues(self.loot_filter.fetch))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Couldn't load leagues from poe.ninja: %s", exc)

    def _on_leagues(self, leagues) -> None:
        self.settings_tab.set_leagues(list(leagues), self.settings_mgr.settings.league)
        self.filter_tab.set_leagues(list(leagues))

    # ---------- ultimatum scanner ----------
    def _restore_scan_settings(self) -> None:
        cfg = self.settings_mgr.settings
        self.scan.min_spin.setValue(float(cfg.scan_min_gain))
        self.scan.clean_check.setChecked(bool(cfg.scan_only_clean))
        self.scan.thin_check.setChecked(bool(cfg.scan_hide_thin))
        self.scan.profitable_check.setChecked(bool(cfg.scan_only_good))

        def save(*_a) -> None:
            cfg.scan_min_gain = float(self.scan.min_spin.value())
            cfg.scan_only_clean = self.scan.clean_check.isChecked()
            cfg.scan_hide_thin = self.scan.thin_check.isChecked()
            cfg.scan_only_good = self.scan.profitable_check.isChecked()
            self.scan.scanner.min_potential = cfg.scan_min_gain
            self.settings_mgr.save()

        for sig in (self.scan.min_spin.valueChanged, self.scan.clean_check.toggled, self.scan.thin_check.toggled,
                    self.scan.profitable_check.toggled):
            sig.connect(save)

    def _scan_index(self):
        return self.price_check.index(self.service.current_league())

    def _make_scanner(self, on_deals, on_status) -> UltimatumScanner:
        def status(text: str) -> None:
            if not text.startswith("⏳"):
                logger.info("Ultimatum scan: %s", text)
            on_status(text)

        def deals(name: str, found: list) -> None:
            logger.info("Ultimatum scan: %s -> %d listings", name, len(found))
            for d in found:
                self.reality_check(d)
            on_deals(name, found)

        scanner = UltimatumScanner(self._scan_index, lambda: self.service.trade_data().ultimatum_mods, deals, status,
                                   post=self._post, get=self._get)
        scanner.min_potential = float(self.settings_mgr.settings.scan_min_gain)
        scanner.load_known(self.settings_mgr.cache_dir / "ultimatum_sacrifices.json")
        return scanner

    def open_deal(self, deal, sync: bool = False) -> None:
        """Remember it in 📒 and open this seller's matching ultimatums on pathofexile.com (Travel to Hideout there)."""
        self.history.add(deal)
        self.history_tab.refresh()
        self.scan.refresh_table()  # 👀 marks it as opened
        clean = self.scan.clean_check.isChecked() and not getattr(deal, "hard_mods", "")
        league = deal.url.split("/trade/search/", 1)[-1].split("/", 1)[0] if "/trade/search/" in deal.url else ""

        def work() -> str:
            try:
                not_ids = []
                if clean:
                    mods = self.service.trade_data().ultimatum_mods
                    not_ids = [i for name in NASTY_MODS for i in mods.get(squash(name), [])]
                import urllib.parse
                return seller_search_url(deal, urllib.parse.unquote(league) or self.service.current_league(),
                                         post=self._post, not_ids=not_ids)
            except Exception as exc:  # noqa: BLE001
                logger.info("Seller search failed (%s) - opening the scan search instead", exc)
                return deal.url

        if sync:
            self._bridge.url.emit(work())
        else:
            threading.Thread(target=lambda: self._bridge.url.emit(work()), daemon=True).start()

    # ---------- price check ----------
    def _hotkey(self, name: str) -> None:
        if name == "price":
            self.price_check_at_cursor()

    def price_check_at_cursor(self, image: Optional[QImage] = None, region: Optional[QRect] = None,
                              pos: Optional[QPoint] = None, sync: bool = False) -> bool:
        """One screenshot of the strip around the mouse, read on this PC, priced, shown next to the item."""
        if self._price_busy:
            return False
        if image is None:
            pos = QCursor.pos()
            screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
            if screen is None:
                return False
            self.popup.hide()
            geo = screen.geometry()
            width = min(geo.width(), self.PRICE_REGION_W)
            left = max(geo.left(), min(pos.x() - width // 2, geo.right() + 1 - width))
            region = QRect(left, geo.top(), width, geo.height())
            shot = screen.grabWindow(0)
            s = shot.width() / max(1, geo.width())
            image = shot.copy(QRect(int((region.x() - geo.x()) * s), int((region.y() - geo.y()) * s),
                                    int(region.width() * s), int(region.height() * s))).toImage()
        region = region or QRect(0, 0, image.width(), image.height())
        pos = pos or region.center()
        scale = image.width() / max(1, region.width())
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        data = bytes(buf.data())
        self._price_busy = True

        def work() -> PriceAnswer:
            try:
                if not self.ocr.available():
                    return PriceAnswer(error=self.ocr.error or "Screen reading (OCR) isn't available")
                lines = self.ocr.recognize(data)
                centred = [((region.x() + (ln.x + ln.w / 2) / scale, region.y() + (ln.y + ln.h / 2) / scale), ln.text)
                           for ln in lines]
                return self.service.check(centred, (pos.x(), pos.y()))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Price check failed")
                return PriceAnswer(error=str(exc))

        if sync:
            self._on_price((work(), pos))
        else:
            threading.Thread(target=lambda: self._bridge.price.emit((work(), pos)), daemon=True).start()
        return True

    def _on_price(self, payload) -> None:
        answer, pos = payload
        self._price_busy = False
        if answer.error:
            logger.info("Price check: %s", answer.error)
        body = answer.html or f"<span style='color:#fca5a5;'>{html.escape(answer.error)}</span>"
        self.popup.set_title(answer.title)
        self.popup.show_html(body, anchor=pos, link=answer.link)
        self.price_tab.add(answer)

    # ---------- loot filter ----------
    def filter_dir(self) -> Path:
        d = self.settings_mgr.settings.filter_dir
        return Path(d) if d else default_filter_dir()

    def open_filter_folder(self) -> None:
        folder = self.filter_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def write_filter(self, force_prices: bool = False, sync: bool = False) -> bool:
        if self._filter_busy:
            return False
        cfg = self.settings_mgr.settings
        names = lambda text: tuple(n.strip() for n in str(text or "").replace("\n", ",").split(",") if n.strip())  # noqa: E731
        fcfg = FilterSettings(cfg.filter_strictness, cfg.filter_tier4, cfg.filter_top_chaos, cfg.filter_high_chaos,
                              cfg.filter_mid_chaos, cfg.filter_high_chaos, sounds=cfg.filter_sounds,
                              beams=cfg.filter_beams, minimap=cfg.filter_minimap, font_boost=int(cfg.filter_font_boost),
                              chaos=cfg.filter_chaos, heist=cfg.filter_heist, ultimatum=cfg.filter_ultimatum,
                              always_show=names(cfg.filter_always_show), always_hide=names(cfg.filter_always_hide))
        self._filter_busy = True
        self.filter_tab.set_status("⏳ Downloading the base filter / prices and writing…")

        def work():
            try:
                league = cfg.filter_league or cfg.league or self.service.current_league()
                path, prices = self.loot_filter.write(self.filter_dir(), fcfg, league, [], "", force_prices=force_prices)
                return path, prices, ""
            except Exception as exc:  # noqa: BLE001
                return None, None, str(exc)

        if sync:
            self._on_filter_written(work())
        else:
            threading.Thread(target=lambda: self._bridge.filter_done.emit(work()), daemon=True).start()
        return True

    def _on_filter_written(self, payload) -> None:
        path, prices, error = payload
        self._filter_busy = False
        if error:
            self.filter_tab.set_status(f"<span style='color:#fca5a5;'>⚠ {html.escape(error)}</span>")
            return
        self.settings_mgr.settings.filter_written = True
        self.settings_mgr.save()
        self.filter_tab.set_status(
            f"✅ Written {time.strftime('%H:%M')}: <b>{html.escape(str(path))}</b><br>League {html.escape(prices.league)} · "
            + (f"<span style='color:#fde68a;'>{html.escape(prices.note)}</span>" if prices.note
               else f"{len(prices.items)} prices · {len(prices.unique_bases)} unique bases")
            + "<br><span style='color:#fde68a;'>Reload it in game (Options → UI → Item Filter).</span>")
        logger.info("Loot filter written to %s (%s)", path, prices.league)

    def _auto_filter(self) -> None:
        cfg = self.settings_mgr.settings
        if cfg.filter_auto and cfg.filter_written:
            self.write_filter()

    # ---------- closing ----------
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        cfg = self.settings_mgr.settings
        cfg.window_x, cfg.window_y, cfg.window_w, cfg.window_h = self.x(), self.y(), self.width(), self.height()
        self.settings_mgr.save()
        self.scan.scanner.stop()
        self.popup.close()
        super().closeEvent(event)
