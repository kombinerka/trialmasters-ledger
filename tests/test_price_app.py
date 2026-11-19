"""Tests for Trialmaster's Ledger's own parts: settings, deal history, hotkey, price service, main window."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.deal_history import DealHistory  # noqa: E402
from src.hotkeys import HotkeyPoller, fkey_vk  # noqa: E402
from src.price_check import PriceCheckManager, PriceEntry, PriceIndex  # noqa: E402
from src.price_service import PriceService  # noqa: E402
from src.settings import SettingsManager  # noqa: E402
from src.ultimatum_scan import UltimatumDeal  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _deal(lid="a", challenge="Survive", price=100.0, sac=190.0, reward=380.0, **kw) -> UltimatumDeal:
    return UltimatumDeal(lid, "Divine Orb", 1, "2× Divine Orb", challenge, 83, price, f"{price:g} chaos", sac, reward,
                         seller="seller#1", url="https://www.pathofexile.com/trade/search/Covens/abc", **kw)


def _index() -> PriceIndex:
    return PriceIndex("Covens", [
        PriceEntry("Divine Orb", "currency", "", 190.0, group="Currency", volume=1e6),
        PriceEntry("Aegis Aurora", "unique", "no links", 290.0, 500, group="UniqueArmour"),
        PriceEntry("The Primordial Chain", "unique", "no links", 2.0, 300, group="UniqueArmour"),
    ], 190.0)


class _Prices(PriceCheckManager):
    def __init__(self, cache: Path) -> None:
        super().__init__(cache, fetch=lambda url: (_ for _ in ()).throw(OSError("offline")))
        self._index = _index()
        self._index.fetched_at = 9e18  # never stale in tests

    def index(self, league: str) -> PriceIndex:
        return self._index


def test_settings_round_trip(tmp_path: Path) -> None:
    mgr = SettingsManager(tmp_path / "settings.json")
    mgr.settings.price_key = "F4"
    mgr.settings.scan_min_gain = 250.0
    mgr.save()
    again = SettingsManager(tmp_path / "settings.json")
    assert again.settings.price_key == "F4" and again.settings.scan_min_gain == 250.0
    assert again.cache_dir == tmp_path / "cache"


def test_deal_history_tracks_your_win_rate_and_real_profit(tmp_path: Path) -> None:
    h = DealHistory(tmp_path / "h.json")
    won = h.add(_deal("a"))
    assert h.add(_deal("a")) is won  # the same listing twice = one record
    lost = h.add(_deal("b", challenge="Survive"))
    other = h.add(_deal("c", challenge="Protect the Altar"))
    h.set_status(won, "won")
    h.set_status(lost, "lost")
    h.set_status(other, "won")
    assert h.record_for("Survive") == (1, 2)
    assert h.record_for("Stand in the Stone Circles") is None
    wins, done, made = h.totals()
    assert (wins, done) == (2, 3)
    assert made == pytest.approx(90 - 290 + 90)  # won: 380-190-100, lost: -(190+100)
    assert DealHistory(tmp_path / "h.json").record_for("Survive") == (1, 2)  # saved


def test_hotkey_fires_once_per_press_and_never_with_alt() -> None:
    held = set()
    pressed = []
    poller = HotkeyPoller(lambda: {"price": "F3"}, pressed.append, key_state=lambda vk: 0x8000 if vk in held else 0)
    held.update({0x11, fkey_vk("F3")})
    poller.poll()
    poller.poll()  # still held: no repeat
    assert pressed == ["price"]
    held.clear()
    poller.poll()
    held.update({0x11, 0x12, fkey_vk("F3")})  # Ctrl+Alt+F3 is someone else's
    poller.poll()
    assert pressed == ["price"]


def test_price_service_prices_a_unique_from_ocr(tmp_path: Path) -> None:
    svc = PriceService(_Prices(tmp_path), lambda: "Covens")
    ans = svc.check([((500, 300), "Aegis Aurora"), ((500, 320), "Champion Kite Shield")], (500, 310))
    assert not ans.error and "Aegis Aurora" in ans.html and ans.item == "Aegis Aurora"


def test_ultimatum_odds_say_how_often_you_must_win() -> None:
    class U:
        sacrifice, count, reward_type, reward = "Divine Orb", 4, "DoubleCurrency", ""

    text = PriceService.odds(U(), _index(), 577.0)  # 4x Divine for 3 div: +1 div / -7 div
    assert "must win ≥ 88%" in text
    U.count = 1
    assert "no profit" in PriceService.odds(U(), _index(), 400.0)


def test_main_window_builds_and_records_opened_deals(qapp: QApplication, tmp_path: Path, monkeypatch) -> None:
    from src.ui import main_window as mw

    monkeypatch.setattr(mw, "PriceCheckManager", lambda cache: _Prices(cache))
    monkeypatch.setattr(mw.threading, "Thread", lambda target=None, daemon=None: type("T", (), {"start": lambda self: None})())

    class FakeOcr:
        error = ""

        def available(self):
            return True

        def warm_up(self):
            pass

        def recognize(self, data):
            from src.screen_ocr import OcrLine
            return [OcrLine("Aegis Aurora", 10, 10, 120, 20, 0.99)]

    posted = []

    def post(url, body):
        posted.append(body)
        return {"id": "xyz", "result": [], "total": 0}

    win = mw.MainWindow(SettingsManager(tmp_path / "settings.json"), ocr=FakeOcr(), post=post, get=lambda url: {"result": []})
    assert win.tabs.count() == 6
    win.scan.add_deals("Divine Orb", [_deal("a", price=50.0), _deal("z", price=55.0)])
    assert win.scan.table.rowCount() == 2  # +140c / +135c >= the 100c bar

    win.open_deal(_deal("a", price=50.0), sync=True)
    assert win.history.records and win.history.records[0].listing_id == "a"
    assert posted and posted[-1]["query"]["filters"]["trade_filters"]["filters"]["account"]["input"] == "seller#1"
    win.history.set_status(win.history.records[0], "won")
    win.scan.refresh_table()
    assert win.scan.table.rowCount() == 1  # the one you ran left the table; the other shows your record
    assert "you 100%" in win.scan.table.item(0, 2).text()

    img = QImage(400, 200, QImage.Format.Format_RGB32)
    assert win.price_check_at_cursor(image=img, region=QRect(0, 0, 400, 200), pos=QPoint(70, 20), sync=True)
    assert "Aegis Aurora" in win.price_tab.answers[0].html
    win.close()


def test_your_own_prices_replace_poe_ninja_in_the_results(tmp_path: Path) -> None:
    from src.ui.history_tab import parse_chaos

    assert parse_chaos("72", 190) == 72 and parse_chaos("72c", 190) == 72 and parse_chaos("1.5 div", 190) == 285
    assert parse_chaos("", 190) == -1 and parse_chaos("abc", 190) == -1
    h = DealHistory(tmp_path / "h.json")
    d = _deal("x", price=50.0, sac=3 * 80.0, reward=6 * 80.0)
    d.sacrifice, d.count, d.reward = "Azyran's Reward", 3, "6× Azyran's Reward"
    rec = h.add(d)
    assert rec.reward_amount == 6 and rec.profit == 480 - 240 - 50
    h.set_prices(rec, paid=40, sacrifice_each=72, sold_each=-1)  # bought the cards at 72c, sold at ninja value
    assert rec.cost_sacrifice == 216 and rec.profit == 480 - 216 - 40 and rec.loss == 256
    h.set_status(rec, "won")
    assert DealHistory(tmp_path / "h.json").records[0].result == 480 - 216 - 40  # saved


def test_card_sets_are_valued_by_what_they_turn_into(tmp_path: Path) -> None:
    import json as _json

    from src.card_rewards import CardRewards

    def card_html(reward: str) -> str:
        return ('<span class="divicard-reward"><span class="tc -unique">[[File:x.png]][[%s|%s]]</span><br>'
                '<span class="tc -corrupted">Corrupted</span></span><span class="divicard-flavour">x</span>') % (reward, reward)

    wiki = {"cargoquery": [
        {"title": {"name": "Azyran&#039;s Reward", "stack": "9", "html": card_html("Prismatic Jewel")}},
        {"title": {"name": "Divine Beauty", "stack": "12",
                   "html": '<span class="divicard-reward"><span class="tc -currency">7x [[Divine Orb]]</span></span>'
                           '<span class="divicard-flavour">x</span>'}},
    ]}
    cards = CardRewards(tmp_path, fetch=lambda url: _json.dumps(wiki))
    assert cards.ensure() == 2
    az = cards.get("Azyran's Reward")
    assert (az.stack, az.item, az.kind, az.corrupted) == (9, "Prismatic Jewel", "unique", True)
    idx = PriceIndex("Covens", [
        PriceEntry("Divine Orb", "currency", "", 190.0),
        PriceEntry("Watcher's Eye", "unique", "no links", 30.0, 900, base="Prismatic Jewel"),
        PriceEntry("The Anima Stone", "unique", "no links", 1190.0, 50, base="Prismatic Jewel"),
        PriceEntry("Sublime Vision", "unique", "no links", 100.0, 180, base="Prismatic Jewel"),
    ], 190.0)
    v = cards.value("Azyran's Reward", idx)
    assert v.random and v.low == 30 and v.high == 1190 and v.typical == 100
    assert "one of 3" in v.describe(lambda c: f"{c:.0f}c")
    beauty = cards.value("Divine Beauty", idx)
    assert not beauty.random and beauty.typical == 7 * 190 and round(beauty.per_card()) == 111
    assert CardRewards(tmp_path).get("Divine Beauty").amount == 7  # cached


def test_deals_you_bought_or_skipped_leave_the_scanner(qapp: QApplication, tmp_path: Path) -> None:
    from src.ui.ultimatum_scan_window import UltimatumScanWindow

    class S:
        def __init__(self, **k): pass
        def running(self): return False
        def stop(self): pass

    h = DealHistory(tmp_path / "h.json")
    w = UltimatumScanWindow(lambda **k: S(), lambda c: f"{c:.0f}c")
    w.deal_status = h.status_of
    a, b = _deal("a", price=50.0), _deal("b", price=60.0)
    w.add_deals("Divine Orb", [a, b])
    assert w.table.rowCount() == 2
    h.add(a)
    w.refresh_table()
    assert w.table.rowCount() == 2 and any("👀" in w.table.item(r, 8).text() for r in range(2))
    h.set_status(h.records[0], "won")
    w.refresh_table()
    assert w.table.rowCount() == 1


def test_your_own_hard_modifier_list(qapp: QApplication, tmp_path: Path, monkeypatch) -> None:
    from src import ultimatum
    from src.ui import main_window as mw

    monkeypatch.setattr(mw, "PriceCheckManager", lambda cache: _Prices(cache))
    monkeypatch.setattr(mw.threading, "Thread", lambda target=None, daemon=None: type("T", (), {"start": lambda self: None})())

    class O:
        error = ""
        def available(self): return True
        def warm_up(self): pass

    mgr = SettingsManager(tmp_path / "settings.json")
    win = mw.MainWindow(mgr, ocr=O(), post=lambda u, b: {"id": "q", "result": [], "total": 0}, get=lambda u: {"result": []})
    assert "Drought" in ultimatum.NASTY_MODS  # defaults
    d = _deal("a", price=50.0, all_mods=("Drought", "Choking Miasma III"), hard_mods="Drought")
    win.scan.add_deals("Divine Orb", [d])
    assert win.scan.table.rowCount() == 0  # Drought is hard -> hidden with "only without hard modifiers"
    win.set_hard_mods(["Choking Miasma III"])  # you're fine with Drought, but not with Choking Miasma
    assert d.hard_mods == "Choking Miasma III" and mgr.settings.hard_mods == ["Choking Miasma III"]
    win.set_hard_mods([])
    assert d.hard_mods == "" and win.scan.table.rowCount() == 1 and mgr.settings.hard_mods == ["-"]
    win.set_hard_mods(list(ultimatum.DEFAULT_HARD_MODS))
    assert mgr.settings.hard_mods == []
    win.close()


def test_a_card_poe_ninja_overprices_is_valued_at_its_turn_in(qapp: QApplication, tmp_path: Path, monkeypatch) -> None:
    from src.card_rewards import CardReward, SetValue
    from src.ui import main_window as mw

    monkeypatch.setattr(mw, "PriceCheckManager", lambda cache: _Prices(cache))
    monkeypatch.setattr(mw.threading, "Thread", lambda target=None, daemon=None: type("T", (), {"start": lambda self: None})())

    class O:
        error = ""
        def available(self): return True
        def warm_up(self): pass

    win = mw.MainWindow(SettingsManager(tmp_path / "s.json"), ocr=O(), post=lambda u, b: {"result": []}, get=lambda u: {"result": []})
    card = CardReward("The Fathomless Depths", 8, "junk", "junk", 1, "unique")
    win.card_value = lambda name: SetValue(card, [("a", 0.8), ("b", 4.8)], random=True)  # 0.1c-0.6c per card
    d = _deal("f", price=1.0, sac=4 * 216.0, reward=8 * 216.0, market="<1 sold/day")
    d.sacrifice, d.count, d.reward = "The Fathomless Depths", 4, "8× The Fathomless Depths"
    win.reality_check(d)
    assert d.profit < 5 and "isn't real" in d.market  # ~1c, not "+4 div" any more
    ok = _deal("n", price=100.0, market="")  # a liquid market is left alone
    win.reality_check(ok)
    assert ok.profit == 90
    win.close()


def test_chaos_orbs_are_big_and_gold_in_the_filter() -> None:
    from src.loot_filter import FilterSettings, companion_rules

    rules = companion_rules(FilterSettings(), None, [])
    block = rules.split('BaseType == "Chaos Orb"')[0].rsplit("Show", 1)[1] + rules.split('BaseType == "Chaos Orb"')[1].split("Show")[0]
    assert 'Class == "Stackable Currency"' in block and "SetFontSize 45" in block and "MinimapIcon" in block


def test_loot_filter_options() -> None:
    from src.loot_filter import FilterSettings, build_filter

    base = 'Show\n\tBaseType == "Chaos Orb" "Scroll of Wisdom" "Orb of Alchemy" "Inscribed Ultimatum"\n'
    quiet = FilterSettings(sounds=False, beams=False, minimap=False, font_boost=3, heist=False,
                           always_show=("Orb of Alchemy", "Not An Item"), always_hide=("Scroll of Wisdom",))
    text = build_filter(base, quiet, None, [])
    mine = text.split("\n\n" + base)[0]
    assert "PlayAlertSound" not in mine and "PlayEffect" not in mine and "MinimapIcon" not in mine
    assert 'Hide # Trialmaster\'s Ledger: your always-hide list\n\tBaseType == "Scroll of Wisdom"' in mine
    assert '"Orb of Alchemy"' in mine and "Not An Item" not in mine  # unknown names never break the filter
    assert "Blueprints" not in mine and '"Inscribed Ultimatum"' in mine
    assert "SetFontSize 45" in mine and "SetFontSize 48" not in mine  # boosted, capped at 45
    loud = build_filter(base, FilterSettings(), None, [])
    assert 'Class == "Blueprints" "Contracts"' in loud and "PlayEffect Purple" in loud


def test_neversink_strictness_levels() -> None:
    from src.loot_filter import STRICTNESS, strictness_label

    assert list(STRICTNESS)[0] == "Soft" and list(STRICTNESS)[-1] == "Uber Plus Strict" and len(STRICTNESS) == 7
    assert strictness_label("show a lot") == "Soft" and strictness_label("only what matters") == "Strict"
    assert strictness_label("Very Strict") == "Very Strict" and strictness_label("nonsense") == "Soft"


def test_quick_searches_build_the_right_queries(qapp: QApplication, tmp_path: Path) -> None:
    from src.quick_search import blueprint_body, search_blueprints, ultimatum_body
    from src.ui.quick_tab import QuickSearchTab

    u = ultimatum_body("DoubleCurrency", "Divine Orb", 150)
    assert u["query"]["filters"]["ultimatum_filters"]["filters"] == {
        "ultimatum_reward": {"option": "DoubleCurrency"}, "ultimatum_input": {"option": "Divine Orb"}}
    assert u["query"]["filters"]["trade_filters"]["filters"]["price"] == {"max": 150}
    ex = ultimatum_body("ExchangeUnique", "Defiance of Destiny")
    assert ex["query"]["filters"]["ultimatum_filters"]["filters"]["ultimatum_output"] == {"option": "Defiance of Destiny"}
    b = blueprint_body("Bunker", 4, 4, 83, 0)
    assert b["query"]["type"] == "Blueprint: Bunker"
    assert b["query"]["filters"]["heist_filters"]["filters"] == {"heist_wings": {"min": 4}, "heist_max_wings": {"min": 4, "max": 4}}
    assert "type" not in blueprint_body("Any blueprint")["query"]

    listing = {"listing": {"price": {"amount": 1, "currency": "divine"}, "account": {"name": "s"}, "indexed": "2026-11-19T10:00:00Z"},
               "item": {"typeLine": "Blueprint: Bunker of Selfishness", "properties": [
                   {"name": "Area Level", "values": [["83", 0]]}, {"name": "Wings Revealed", "values": [["4/4", 0]]},
                   {"name": "Escape Routes Revealed", "values": [["4/8", 0]]}, {"name": "Reward Rooms Revealed", "values": [["12/28", 0]]}]}}
    rows, url, total = search_blueprints("Covens", "Bunker", 4, 4, 83, divine_chaos=190.0,
                                         post=lambda u, body: {"id": "q1", "result": ["a"], "total": 7},
                                         get=lambda u: {"result": [listing]})
    assert total == 7 and url.endswith("/Covens/q1") and rows[0].wings == "4/4" and rows[0].price_chaos == 190.0

    tab = QuickSearchTab(lambda r, i, m, clean: ([_deal("a", price=50.0)], "https://x/u", 3),
                         lambda *a: (rows, "https://x/b", 7), lambda c: f"{c:.0f}c", lambda: ["Divine Orb"])
    tab.run_ultimatums(sync=True)
    tab.run_blueprints(sync=True)
    assert tab.u_table.rowCount() == 1 and tab.b_table.rowCount() == 1 and tab.urls == {"u": "https://x/u", "b": "https://x/b"}
    assert "3 listed" in tab.u_status.text() and tab.b_table.item(0, 3).text() == "4/4" and tab.b_table.item(0, 2).text() == "–"


def test_quick_search_hard_mods_and_blueprint_targets() -> None:
    from src.quick_search import BLUEPRINT_TARGETS, blueprint_body, search_ultimatums, ultimatum_body
    from src.trade_search import TradeQueryError
    from src.ultimatum_scan import market_warning

    assert ultimatum_body("DoubleCurrency", "Divine Orb", 150, not_ids=["u1", "u2"])["query"]["stats"] == [
        {"type": "not", "filters": [{"id": "u1"}, {"id": "u2"}]}]
    replica = blueprint_body("Any blueprint", 4, 4, 83, target=BLUEPRINT_TARGETS["Replica Unique Items"])["query"]
    assert replica["stats"] == [{"type": "and", "filters": [{"id": "enchant.stat_2619914138"}]}]
    none = blueprint_body(target="none")["query"]["stats"][0]
    assert none["type"] == "not" and len(none["filters"]) == 5

    calls = []

    def post(url, body):  # the site rejects the NOT group: searched again without it, filtered here instead
        calls.append(body)
        if "stats" in body["query"]:
            raise TradeQueryError("too complex")
        return {"id": "q", "result": [], "total": 0}

    search_ultimatums(_index(), "Covens", "DoubleCurrency", "Divine Orb", 150, post=post, get=lambda u: {"result": []},
                      not_ids=["u1"], only_clean=True)
    assert len(calls) == 2 and "stats" not in calls[1]["query"]
    divine = PriceEntry("Divine Orb", "currency", "", 190.0, volume=190.0 * 5000, change=623)
    assert market_warning(divine, 2) == ""  # Divine's own price swing is no pump
