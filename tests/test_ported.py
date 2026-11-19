"""Tests ported from the PoE Campaign Companion (the price tools are the same code)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])

def _write_fake_png(path: Path) -> None:
    # Smallest valid 1x1 PNG
    import base64

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    ))

GEM_TEXT = """Item Class: Skill Gems
Rarity: Gem
Fireball
--------
Projectile, Spell, AoE, Fire
Level: 1
--------
Unleashes a ball of fire towards a target.
Deals 4 to 7 Fire Damage
--------
Place into an item socket of the right colour to gain this skill."""

def _fake_google(url: str) -> str:
    import json
    import urllib.parse

    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]
    lines = q.split("\n")
    return json.dumps([[[("PL:" + ln) + ("\n" if i < len(lines) - 1 else ""), ln] for i, ln in enumerate(lines)]])

def _fake_ocr_backend(lines):
    """RapidOCR-shaped result: [(box 4 points, text, score), ...]."""
    return lambda data: [([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], t, 0.97) for t, x, y, w, h in lines]

def _pob_xml(specs: str = "", gems: str = "", cls: str = "Witch", asc: str = "Occultist") -> str:
    gems = gems or (
        '<Skill enabled="true" slot="Body Armour" label="Main">'
        '<Gem gemId="Metadata/Items/Gems/SkillGemFrostBoltNova" nameSpec="Vortex" enabled="true"/>'
        '<Gem gemId="Metadata/Items/Gems/SupportGemControlledDestruction" nameSpec="Controlled Destruction" enabled="true"/>'
        '<Gem gemId="Metadata/Items/Gems/SupportGemArcaneSurge" nameSpec="Arcane Surge" enabled="true"/></Skill>'
        '<Skill enabled="true" slot="Helmet"><Gem gemId="Metadata/Items/Gems/SkillGemFrostBomb" nameSpec="Frost Bomb" enabled="true"/>'
        '<Gem gemId="Metadata/Items/Gems/SkillGemFlameDash" nameSpec="Flame Dash" enabled="false"/></Skill>'
        '<Skill enabled="true" slot="Ring 1" source="Item:3:Ring"><Gem gemId="Metadata/Items/Gems/SkillGemCurseOnHit" nameSpec="Item skill"/></Skill>'
    )
    return (f'<PathOfBuilding><Build level="90" className="{cls}" ascendClassName="{asc}" mainSocketGroup="1"/>'
            f'<Skills activeSkillSet="1"><SkillSet id="1">{gems}</SkillSet></Skills>'
            f'<Tree activeSpec="1">{specs}</Tree></PathOfBuilding>')

def _fake_ninja(url: str) -> str:
    import json

    if "index-state" in url:
        return json.dumps({"economyLeagues": [{"name": "Standard"}, {"name": "Allflame"}, {"name": "Hardcore"}]})
    if "exchange" in url and "type=DivinationCard" in url:
        return json.dumps({"items": [{"id": "doc", "name": "The Doctor"}, {"id": "rain", "name": "Rain of Chaos"},
                                     {"id": "mid", "name": "The Wolf"}],
                           "lines": [{"id": "doc", "primaryValue": 700}, {"id": "rain", "primaryValue": 0.5},
                                     {"id": "mid", "primaryValue": 30}]})
    if "exchange" in url:
        return json.dumps({"items": [], "lines": []})
    if "type=UniqueAccessory" in url:
        return json.dumps({"lines": [
            {"name": "Mageblood", "baseType": "Heavy Belt", "chaosValue": 90000, "listingCount": 20},
            {"name": "Bated Breath", "baseType": "Heavy Belt", "chaosValue": 1, "listingCount": 50},
            {"name": "Ventor's Gamble", "baseType": "Gold Ring", "chaosValue": 150, "listingCount": 30},
            {"name": "Foulborn Ventor's Gamble", "baseType": "Gold Ring", "chaosValue": 5, "listingCount": 30},
        ]})
    if "stash" in url:
        return json.dumps({"lines": []})
    if "NeverSink" in url:
        return ('#NeverSink base\nShow\n\tBaseType == "The Doctor" "The Wolf" "Rain of Chaos" "Heavy Belt" "Gold Ring"\n'
                'Show\n\tRarity Unique\n')
    raise AssertionError(url)

def _fake_ninja_prices(url: str) -> str:
    import json

    if "index-state" in url:
        return json.dumps({"economyLeagues": [{"name": "Allflame"}, {"name": "Standard"}]})
    if "exchange" in url and "type=Currency" in url:
        return json.dumps({"core": {"rates": {"divine": 0.002}}, "items": [{"id": "div", "name": "Divine Orb"}],
                           "lines": [{"id": "div", "primaryValue": 500}]})
    if "exchange" in url:
        return json.dumps({"items": [], "lines": []})
    if "type=UniqueArmour" in url:
        return json.dumps({"lines": [
            {"name": "Replica Farrul's Fur", "chaosValue": 1200, "listingCount": 25},
            {"name": "Replica Farrul's Fur", "links": 6, "chaosValue": 4600, "listingCount": 30},
            {"name": "Rare Thin Listing", "chaosValue": 9, "listingCount": 1},
        ]})
    if "type=SkillGem" in url:
        return json.dumps({"lines": [{"name": "Fireball", "gemLevel": 20, "gemQuality": 20, "chaosValue": 80, "listingCount": 50}]})
    if "type=BaseType" in url:
        return json.dumps({"lines": [
            {"name": "Triumphant Lamellar", "levelRequired": 86, "chaosValue": 3, "listingCount": 10},
            {"name": "Simplex Amulet", "levelRequired": 86, "chaosValue": 40, "listingCount": 10},
            {"name": "Simplex Amulet", "levelRequired": 86, "variant": "Shaper", "chaosValue": 900, "listingCount": 10},
        ]})
    return json.dumps({"lines": []})

_TRADE_STATS = {"result": [{"id": "explicit", "entries": [
    {"id": "explicit.stat_533969887", "text": "#% chance in Heists for Jeweller's Orbs to drop as Orbs of Fusing instead"},
    {"id": "explicit.stat_1323476506", "text": "#% chance to receive additional Jewellery when opening a Reward Chest in a Heist"},
    {"id": "explicit.stat_854906305", "text": "#% chance to receive additional Gold when opening a Reward Chest in a Heist"},
]}]}

_TRADE_ITEMS = {"result": [{"id": "heistequipment", "entries": [{"type": "Thief's Trinket"}]}]}

def _fake_trade_get(url: str) -> dict:
    if url.endswith("/data/stats"):
        return _TRADE_STATS
    if url.endswith("/data/items"):
        return _TRADE_ITEMS
    if "/fetch/" in url:
        return {"result": [{"listing": {"price": {"amount": a, "currency": c}}} for a, c in ((40, "chaos"), (1, "divine"))]}
    raise AssertionError(url)

def _fake_trade_post(results_per_step):
    calls = []

    def post(url: str, body: dict) -> dict:
        calls.append(body)
        n = results_per_step[min(len(calls) - 1, len(results_per_step) - 1)] if results_per_step else 0
        return {"id": f"q{len(calls)}", "result": [f"id{i}" for i in range(n)], "total": n}

    post.calls = calls
    return post

_TRINKET_LINES = [
    ("CURIO DISPLAY", 551, 22, 157, 23), ("GHOUL REMNANT", 514, 80, 230, 25), ("THIEF'S TRINKET", 522, 113, 213, 27),
    ("ITEM LEVEL:78", 549, 161, 159, 26), ("REQUIRESLEVEL40", 529, 192, 199, 26),
    ('PREFIXMODIFIER"LUCKY"(TIER:2)-DROP', 469, 237, 323, 21),
    ("25(21-25)% CHANCE IN HEISTS FOR JEWELLER's ORBS TO DROP AS ORBS OF Fu SING INSTEAD", 154, 261, 949, 28),
    ('SUFFIXMODIFIEROFSUPERSTITION"(TIER:2)-DROP', 431, 296, 395, 20),
    ("5(5-6)% CHANCE TO RECEIVE ADDITIONAL JEWELLERY WHEN OPENING A REWARD CHEST IN A HEIST", 113, 321, 1030, 28),
    ("2%CHANCETO RECEIVEADDITIONALGOLDWHENOPENINGA REWARD CHESTINA HEIST", 164, 380, 928, 23),
    ("CORRUPTED", 566, 420, 124, 25), ("A DIRTY JOB", 1220, 303, 92, 25),
]

_TIERED_BASE = (
    "Show # $type->gear->tiered $tier->t5r1\r\r\n\tUnidentifiedItemTier >= 5\r\r\n\tRarity Normal Magic Rare\r\r\n"
    "\tPlayEffect Blue\r\r\n\tMinimapIcon 0 Blue Diamond\r\r\n\r\r\n"
    "Show # $type->gear->tiered $tier->any5\r\r\n\tUnidentifiedItemTier >= 4\r\r\n\tRarity Normal Magic Rare\r\r\n"
    "\tSetFontSize 45\r\r\n\tPlayAlertSound 3 300\r\r\n\tPlayEffect Blue\r\r\n\tMinimapIcon 1 Blue Diamond\r\r\n\r\r\n"
    "Show # $type->gear->tiered $tier->any4\r\r\n\tUnidentifiedItemTier >= 3\r\r\n\tPlayEffect Blue\r\r\n\r\r\n"
    "Show # $type->gear->tiered $tier->any2\r\r\n\tUnidentifiedItemTier >= 2\r\r\n\r\r\n"
)

_ULTI_LINES = [("INSCRIBED ULTIMATUM", 300, 5, 260, 30), ("CHALLENGE:PROTECTTHEALTAR", 380, 55, 340, 26),
               ("AREALEVEL:80", 470, 85, 160, 24), ("REQUIRESSACRIFICE:SACRED ORBX1", 355, 115, 380, 26),
               ("REWARD:DOUBLESSACRIFICED CURRENCY", 335, 145, 430, 26), ("RAGING DEAD IV", 460, 260, 180, 24)]

def _ulti_listing(lid, amount, cur, sacrifice, count, reward, challenge="Survive", level="83", seller="Exile1"):
    return {"id": lid, "listing": {"price": {"amount": amount, "currency": cur}, "account": {"name": seller},
                                   "whisper": f"@{seller} Hi, I would like to buy your Inscribed Ultimatum",
                                   "indexed": "2026-11-17T23:10:00Z"},
            "item": {"properties": [{"name": "Challenge", "values": [[challenge, 0]]},
                                    {"name": "Area Level", "values": [[level, 0]]},
                                    {"name": "Requires Sacrifice: {0} {1}", "values": [[sacrifice, 18], [f"x{count}", 0]]},
                                    {"name": "Reward: {0}", "values": [[reward, 0]]}]}}

def _scan_index():
    from src.price_check import PriceEntry, PriceIndex

    return PriceIndex("Covens", [PriceEntry("Divine Orb", "currency", "", 146.0), PriceEntry("Chaos Orb", "currency", "", 1.0),
                                 PriceEntry("Mageblood", "unique", "no links", 30000, 20),
                                 PriceEntry("Bated Breath", "unique", "no links", 60, 50)], divine_chaos=146)


def test_ocr_blocks_join_wrapped_sentences_and_fix_caps() -> None:
    from src.screen_ocr import build_blocks, detect_alignment, normalize_case

    assert normalize_case("MINIONS HAVE +48% TO ALL ELEMENTAL RESISTANCES") == "Minions have +48% to all elemental resistances"
    assert normalize_case("Decay Ward") == "Decay Ward"
    # Gem description (small caps, wrapped over 3 lines) + stat lines (never joined)
    gem = build_blocks([
        ((100, 100, 700, 24), "SUMMONS A NUMBER OF SKELETON WARRIORS AT A TARGETED LOCATION. THEY USE A"),
        ((90, 128, 720, 24), "MELEE ATTACK AND DIE AFTER A DURATION. IF AGGRESSIVE, SKELETON WARRIORS"),
        ((200, 156, 420, 24), "WILL ALSO DASH TOWARDS NEARBY ENEMIES."),
        ((200, 220, 450, 24), "MINIONS HAVE +48% TO ALL ELEMENTAL RESISTANCES"),
        ((220, 248, 380, 24), "MINI0NS HAVE 11073 MAXIMUM LIFE"),
    ])
    assert [len(b.boxes) for b in gem] == [3, 1, 1]
    assert gem[0].text.startswith("Summons a number of skeleton warriors") and gem[0].text.endswith("nearby enemies.")
    assert gem[2].text == "Minions have 11073 maximum life"
    # Character sheet: label + value on the same row stay separate rows, left aligned
    sheet = [((40, 670, 200, 20), "DAMAGE PER SECOND"), ((650, 670, 50, 20), "57.53"),
             ((40, 694, 300, 20), "MAIN HAND CHANCE TO HIT ALL MONSTERS"), ((650, 694, 40, 20), "24%"),
             ((40, 718, 250, 20), "ATTACKS PER SECOND"), ((650, 718, 40, 20), "3.39")]
    blocks = build_blocks(sheet)
    assert [b.text for b in blocks if not b.text[0].isdigit()] == ["Damage per second", "Main hand chance to hit all monsters", "Attacks per second"]
    assert detect_alignment([b for b, t in sheet if not t[0].isdigit()]) == "left"
    assert detect_alignment([(100, 100, 700, 24), (90, 128, 720, 24), (200, 156, 420, 24)]) == "center"

def test_new_league_without_prices_still_writes_the_filter(tmp_path: Path) -> None:
    import json
    from src.loot_filter import FilterSettings, LootFilterManager

    def no_prices_yet(url: str) -> str:
        if "NeverSink" in url:
            return '#NeverSink base\nShow\n\tBaseType == "The Doctor"\n'
        return json.dumps({"items": [], "lines": []})

    mgr = LootFilterManager(tmp_path / "cache", fetch=no_prices_yet)
    out, prices = mgr.write(tmp_path / "poe", FilterSettings(), "NewLeague", [("RRG", True)])
    text = out.read_text(encoding="utf-8")
    assert "no prices" in prices.note and 'SocketGroup "RRG"' in text and "#NeverSink base" in text
    # League change: the base filter is downloaded again right away (NeverSink's league-start update)
    calls = []
    mgr2 = LootFilterManager(tmp_path / "cache", fetch=lambda u: calls.append(u) or no_prices_yet(u))
    mgr2.write(tmp_path / "poe", FilterSettings(), "NewLeague", [])
    assert not any("NeverSink" in c for c in calls)  # same league, cached
    mgr2.write(tmp_path / "poe", FilterSettings(), "NextLeague", [])
    assert any("NeverSink" in c for c in calls)

def test_price_index_matches_ocr_text_with_typos() -> None:
    from src.price_check import PriceIndex

    idx = PriceIndex.download("Allflame", _fake_ninja_prices)
    assert idx.divine_chaos == 500 and "rarethinlisting" not in idx.by_key  # too few listings to trust
    assert idx.lookup_line("Replica Farru1s Fur") == "replicafarrulsfur" and idx.lookup_line("Simplex Amu1et") == "simplexamulet"
    # Unique name beats its base type line; uniques show every variant
    entries = idx.match([((500, 300), "Triumphant Lamellar"), ((500, 270), "Replica Farrul's Fur")], (500, 290))
    text = idx.to_html(entries)
    assert "Replica Farrul" in text and "no links" in text and "6-link" in text and "9.2 div" in text
    assert [e.label for e in idx.by_key["simplexamulet"]] == ["ilvl 86"]  # influenced bases aren't curio drops
    assert idx.match([((0, 0), "Some random text")], (0, 0)) is None
    assert PriceIndex.from_json(idx.to_json()).by_key.keys() == idx.by_key.keys()

def test_rare_weapon_tooltip_ignores_neighbour_tooltip_and_reads_implicits() -> None:
    from src.trade_search import TradeData, search

    stats = {"result": [
        {"id": "explicit", "entries": [
            {"id": "explicit.stat_2831165374", "text": "Adds # to # Lightning Damage to Spells"},
            {"id": "explicit.stat_2974417149", "text": "#% increased Spell Damage"},
            {"id": "explicit.stat_1050105434", "text": "+# to maximum Mana"},
            {"id": "explicit.stat_2308278768", "text": "#% increased Cooldown Recovery Rate of Travel Skills"},
            {"id": "explicit.stat_crit", "text": "#% to Critical Strike Chance"}]},
        {"id": "implicit", "entries": [
            {"id": "implicit.stat_2308278768", "text": "#% increased Cooldown Recovery Rate of Travel Skills"}]},
    ]}
    td = TradeData(stats, {"result": [{"id": "weapon", "entries": [{"type": "Infernal Blade"}]}]})
    # ((centre x, centre y), text) as OCR'd from a curio display; x=150 is a second tooltip next to it
    lines = [((800, 52), "CATACLYSM BANE"), ((800, 88), "INFERNAL BLADE"), ((800, 134), "RUNE DAGGER"),
             ((800, 194), "CRITICAL STRIKE CHANCE: 8.50%"), ((150, 230), "+5% to Critical Strike Chance"),
             ((800, 368), "IMPLICIT MODIFIER"), ((800, 424), "40% INCREASED COOLDOWN RECOVERY RATE OF TRAVEL SKILLS"),
             ((800, 466), 'PREFIX MODIFIER "SHOCKING" (TIER: 3)'),
             ((800, 494), "ADDS 6(2-11) TO 133(132-139) LIGHTNING DAMAGE TO SPELLS"),
             ((800, 553), "76(70-84)% INCREASED SPELL DAMAGE"), ((800, 611), "31(31-36)% INCREASED SPELL DAMAGE"),
             ((800, 640), "+37(34-37) TO MAXIMUM MANA")]
    base, category, mods = td.read_item(lines)
    assert (base, category) == ("Infernal Blade", "weapon.runedagger")
    got = {m.stat_id: m.value for m in mods}
    assert got == {"implicit.stat_2308278768": 40, "explicit.stat_2831165374": 69.5,
                   "explicit.stat_2974417149": 107, "explicit.stat_1050105434": 37}
    assert next(m for m in mods if m.stat_id.endswith("2831165374")).display() == "Adds 6 to 133 Lightning Damage to Spells"
    # Exact base not listed -> fewer affixes -> same on any rune dagger (max 4 searches)
    post = _fake_trade_post([0, 0, 2, 0])
    res = search("Covens", base, mods, post=post, get=_fake_trade_get, category=category)
    assert len(post.calls) == 4 and res.matched == "2 of 4 affixes on any runedagger" and res.total == 2
    q = post.calls[2]["query"]
    assert "type" not in q and q["filters"]["type_filters"]["filters"]["category"]["option"] == "weapon.runedagger"

def test_tier4_gear_can_be_quiet_or_hidden() -> None:
    from src.loot_filter import _clean_newlines, adjust_tier4

    base = _clean_newlines(_TIERED_BASE)
    assert "\r" not in base and "\n\n\t" not in base  # doubled Windows line endings repaired
    assert adjust_tier4(base, "highlight") == base
    quiet = adjust_tier4(base, "quiet")
    blocks = quiet.strip().split("\n\n")
    # tier-5 block untouched, then an exact tier-4 copy in front of each block tier 4 can reach (>=4, >=3, >=2)
    assert [b.split("\n")[1].strip() for b in blocks] == [
        "UnidentifiedItemTier >= 5", "UnidentifiedItemTier == 4", "UnidentifiedItemTier >= 4",
        "UnidentifiedItemTier == 4", "UnidentifiedItemTier >= 3", "UnidentifiedItemTier == 4", "UnidentifiedItemTier >= 2"]
    copy = blocks[1]
    assert "SetFontSize 45" in copy and "PlayEffect" not in copy and "MinimapIcon" not in copy and "PlayAlertSound" not in copy
    assert "PlayEffect Blue" in blocks[0] and "PlayEffect Blue" in blocks[2]  # tier 5 and the originals keep theirs
    hidden = adjust_tier4(base, "hide").strip().split("\n\n")
    assert hidden[1].startswith("Hide") and hidden[2].startswith("Show")

def test_tier4_setting_reaches_the_written_filter(tmp_path: Path) -> None:
    from src.loot_filter import FilterSettings, LootFilterManager

    def fetch(url: str) -> str:
        return _TIERED_BASE if "NeverSink" in url else _fake_ninja(url)

    mgr = LootFilterManager(tmp_path / "cache", fetch=fetch)
    out, _ = mgr.write(tmp_path / "poe", FilterSettings(tier4="quiet"), "Allflame", [])
    assert out.read_text(encoding="utf-8").count("UnidentifiedItemTier == 4") == 3
    assert b"\r\r\n" not in (next((tmp_path / "cache").glob("neversink_*"))).read_bytes()

def test_ultimatum_search_is_precise_like_a_hand_made_trade_search() -> None:
    from src import ultimatum as U
    from src.trade_search import TradeData

    stats = {"result": [{"id": "ultimatum", "entries": [
        {"id": "ultimatum.umod_39655", "text": "Raging Dead IV"},
        {"id": "ultimatum.umod_7958", "text": "Stalking Ruin"}, {"id": "ultimatum.umod_32061", "text": "Stalking Ruin II"},
        {"id": "ultimatum.umod_15668", "text": "Drought"}, {"id": "ultimatum.umod_5772", "text": "Reduced Recovery"},
        {"id": "ultimatum.umod_24728", "text": "Ruin II"}]}]}
    td = TradeData(stats, {"result": []})
    lines = [t for t, *_ in _ULTI_LINES] + ["STalking Ruin Il", "(An invulnerable shade stalks you)", "REDUCED RECOVERY"]
    u = U.parse(lines)
    u.mods = U.read_mods(lines, td.ultimatum_mods)
    assert [n for n, _ in u.mods] == ["Raging Dead IV", "Stalking Ruin II", "Reduced Recovery"]  # OCR 'Il' -> II
    u.nasty, u.nasty_not_ids = U.split_nasty(u.mods, td.ultimatum_mods)
    assert [n for n, _ in u.nasty] == ["Stalking Ruin II", "Reduced Recovery"]
    assert u.nasty_not_ids == ["ultimatum.umod_7958", "ultimatum.umod_24728", "ultimatum.umod_15668"]
    calls = []

    def post(url, body):
        calls.append(body)
        n = [0, 1, 6][len(calls) - 1]
        return {"id": f"u{len(calls)}", "result": [str(i) for i in range(n)], "total": n}

    U.search_listings(u, "Covens", post=post, get=lambda url: {"result": []})
    assert len(calls) == 3 and u.matched == "same challenge, reward & item (any modifiers)" and u.total == 6
    for c in calls:  # challenge, reward type and required item are never dropped
        f = c["query"]["filters"]["ultimatum_filters"]["filters"]
        assert f == {"ultimatum_challenge": {"option": "Defense"}, "ultimatum_reward": {"option": "DoubleCurrency"},
                     "ultimatum_input": {"option": "SACRED ORB"}}
    assert calls[0]["query"]["stats"] == [
        {"type": "count", "value": {"min": 2}, "filters": [{"id": "ultimatum.umod_32061"}, {"id": "ultimatum.umod_5772"}]},
        {"type": "not", "filters": [{"id": "ultimatum.umod_7958"}, {"id": "ultimatum.umod_24728"}, {"id": "ultimatum.umod_15668"}]}]
    assert [g["type"] for g in calls[1]["query"]["stats"]] == ["count"] and "stats" not in calls[2]["query"]
    # a clean ultimatum is compared with clean ones only (NOT group of every hard modifier)
    clean = U.parse([t for t, *_ in _ULTI_LINES])
    clean.nasty, clean.nasty_not_ids = U.split_nasty([("Raging Dead IV", ["x"])], td.ultimatum_mods)
    assert clean.nasty == [] and len(clean.nasty_not_ids) == 5
    assert U._body(clean, "exact")["query"]["stats"] == [{"type": "not", "filters": [{"id": i} for i in clean.nasty_not_ids]}]

def test_rejected_trade_search_steps_are_skipped_with_the_reason() -> None:
    from src import ultimatum as U
    from src.trade_search import TradeQueryError

    u = U.parse([t for t, *_ in _ULTI_LINES])
    u.sacrifice = "Sacred Orb"
    u.nasty, u.nasty_not_ids = [("Drought", ["d"])], ["r1", "r2"]
    calls = []

    def post(url, body):
        calls.append(body)
        if len(calls) == 1:  # logged out, the NOT group can be too big
            raise TradeQueryError("Query is too complex. Please reduce the amount of filters used.")
        return {"id": "ok", "result": ["a", "b", "c"], "total": 3}

    U.search_listings(u, "Covens", post=post, get=lambda url: {"result": []})
    assert u.matched == "same challenge, reward & item, also Drought" and u.errors and "too complex" in u.errors[0]

    def always_bad(url, body):
        raise TradeQueryError("Unknown item base type")

    with pytest.raises(TradeQueryError, match="Unknown item"):
        U.search_listings(U.parse([t for t, *_ in _ULTI_LINES]), "Covens", post=always_bad, get=lambda url: {})

def test_ultimatum_reward_survives_cut_off_ocr() -> None:
    from src import ultimatum as U

    assert U.parse(["REWARD:DOUBLESSACRIFICEDCURRENC"]).reward_type == "DoubleCurrency"
    assert U.parse(["Reward: Doubles sacrificed Divination Cards"]).reward_type == "DoubleDivCards"
    assert U.parse(["REWARD:MIRRORED RARE ITEM"]).reward_type == "MirrorRare"
    assert U.parse(["Reward: Mageblood"]).reward_type == "ExchangeUnique"

def test_ultimatum_lines_with_cut_off_starts() -> None:
    from src import ultimatum as U

    lines = ["BED ULTIMATUM", "LLENGE: SURVIVE", "REA LEVEL:82", "Monster Level: 60", "ACRIFICE: DIVINE ORB X4",
             "EWARD: DOUBLESSACRIFICEDCURRENCY", "MCALLER RUNESII"]
    assert U.is_ultimatum(lines)
    u = U.parse(lines)
    assert (u.challenge, u.area_level, u.sacrifice, u.count, u.reward_type) == ("Survive", 82, "DIVINE ORB", 4, "DoubleCurrency")
    assert not U.is_ultimatum(["Item Level: 82", "Requires Level 70"])

def test_ultimatum_prices_only_count_listings_with_the_same_sacrifice_amount() -> None:
    from src import ultimatum as U
    from src.price_check import PriceEntry, PriceIndex

    idx = PriceIndex("Covens", [PriceEntry("Divine Orb", "currency", "", 146.0)], divine_chaos=146)
    u = U.parse(["Challenge: Survive", "Requires Sacrifice: Divine Orb x4", "Reward: Doubles sacrificed Currency"])

    def listing(amount, cur, count):
        return {"listing": {"price": {"amount": amount, "currency": cur}},
                "item": {"properties": [{"name": "Requires Sacrifice: {0} {1}", "values": [["Divine Orb", 18], [f"x{count}", 0]]}]}}

    page = [listing(5, "chaos", 1), listing(5, "chaos", 4), listing(20, "chaos", 1), listing(20, "chaos", 4),
            listing(1, "divine", 4), listing(2, "divine", 4), listing(3, "divine", 4), listing(20, "chaos", 8)]
    gets = []

    def get(url):
        gets.append(url)
        return {"result": page}

    U.search_listings(u, "Covens", post=lambda url, body: {"id": "q", "result": [str(i) for i in range(12)], "total": 12},
                      get=get)
    assert len(gets) == 2  # 12 results -> two fetch pages of up to 10
    assert u.same_count and len(u.prices) == 10 and all(p not in u.prices for p in [])
    text = U.to_html(u, idx)
    assert "Listed (4× Divine Orb)" in text and "low 5.0c" in text and "high 3.0 div" in text
    # nobody lists your amount: falls back to every amount and says so
    u2 = U.parse(["Requires Sacrifice: Divine Orb x16", "Reward: Doubles sacrificed Currency"])
    U.search_listings(u2, "Covens", post=lambda url, body: {"id": "q", "result": ["1"], "total": 1}, get=lambda url: {"result": page})
    assert not u2.same_count and "none with yours listed" in U.to_html(u2, idx)

def test_trade_rate_limiter_stays_under_the_limits(tmp_path: Path) -> None:
    from src.trade_search import RateLimiter, TradeBusy, try_steps

    lim = RateLimiter()
    url = "https://www.pathofexile.com/api/trade/search/Covens"
    now = 1000.0
    # the site's answer: rules + its current counters (which include Awakened PoE Trade on the same IP)
    headers = {"X-Rate-Limit-Ip": "5:10:60,15:60:300,30:300:1800", "X-Rate-Limit-Ip-State": "2:10:0,14:60:0,20:300:0"}
    lim.after(url, headers)
    assert lim.rules["search"] == [(5, 10, 60), (15, 60, 300), (30, 300, 1800)]
    assert lim.wait_needed(url) > 0  # 14 of 15 per minute used: keep the last one spare
    lim.after(url, {"X-Rate-Limit-Ip": "5:10:60,15:60:300,30:300:1800", "X-Rate-Limit-Ip-State": "1:10:0,1:60:0,1:300:0"})
    assert lim.wait_needed(url) == 0
    # our own requests count too: 4 searches within 10 s -> the 5th waits
    lim.local["search"] = [__import__("time").time() - i for i in range(4)]
    assert lim.wait_needed(url) > 0
    with pytest.raises(TradeBusy, match="holding back"):
        lim.before(url)
    # a 429 / restriction blocks everything for the given time, and survives a restart
    lim2 = RateLimiter()
    lim2.use_state_file(tmp_path / "blocked.txt")
    lim2.after(url, {"Retry-After": "1800"}, status=429)
    assert 1790 < lim2.wait_needed("https://www.pathofexile.com/api/trade/fetch/a") <= 1800
    lim3 = RateLimiter()
    lim3.use_state_file(tmp_path / "blocked.txt")
    assert lim3.wait_needed(url) > 1700
    # search steps: when the limit would be hit, stop and use the best result so far
    calls = []

    def post(u, body):
        calls.append(body)
        if len(calls) > 1:
            raise TradeBusy("holding back")
        return {"id": "a", "result": ["x"], "total": 1}

    res, label, errors = try_steps(post, url, [("step 1", {}), ("step 2", {}), ("step 3", {})], enough=3)
    assert label == "step 1" and len(calls) == 2 and "stopped early" in errors[0]

def test_ultimatum_scan_finds_profitable_listings() -> None:
    from src.trade_search import LIMITER
    from src.ultimatum_scan import UltimatumScanner, candidates, parse_listing

    idx = _scan_index()
    # only real ultimatum sacrifices (a Mirror / Headhunter never is), most valuable first
    assert candidates(idx, 50) == [("Divine Orb", 146.0)]
    assert candidates(idx, 50, allowed={"Divine Orb", "Bated Breath", "Mageblood"}) == [
        ("Mageblood", 30000), ("Divine Orb", 146.0), ("Bated Breath", 60)]
    d = parse_listing(_ulti_listing("a", 20, "chaos", "Divine Orb", 4, "Doubles sacrificed Currency"), idx, "Covens", "q1")
    assert (d.count, d.reward, d.challenge, d.area_level) == (4, "8× Divine Orb", "Survive", 83)
    assert d.profit == 146 * 4 - 20 and d.loss == 146 * 4 + 20 and d.url.endswith("/Covens/q1")
    u = parse_listing(_ulti_listing("b", 1, "divine", "Bated Breath", 1, "Mageblood"), idx, "Covens", "q2")
    assert u.reward == "Mageblood" and u.profit == 30000 - 60 - 146
    assert parse_listing(_ulti_listing("c", 5, "chaos", "Divine Orb", 1, "Mirrored Rare Item"), idx, "Covens", "q") is None

    posts, gets, statuses, found = [], [], [], {}

    def post(url, body):
        posts.append(body)
        return {"id": "q", "result": ["a", "b"], "total": 2}

    def get(url):
        gets.append(url)
        return {"result": [_ulti_listing("a", 20, "chaos", "Divine Orb", 4, "Doubles sacrificed Currency")]}

    LIMITER.local.clear(); LIMITER.server.clear(); LIMITER.blocked_until = 0
    sc = UltimatumScanner(lambda: idx, lambda: {"stalkingruin": ["ultimatum.umod_7958"], "drought": ["ultimatum.umod_15668"]},
                          on_deals=lambda name, deals: found.__setitem__(name, deals), on_status=statuses.append,
                          post=post, get=get, sleep=lambda s: None)
    sc.MIN_GAP = sc.FETCH_GAP = 0
    sc.min_chaos = 100
    sc.load_known(Path(__import__("tempfile").mkdtemp()) / "known.json")
    # broad searches: cheapest + a rotating price slice per reward type, plus the expensive end of unique exchanges;
    # then the learned/common sacrifices (Divine Orb)
    n = sc.run_once()
    assert n == len(posts) and len(posts) >= 8
    flt = [p["query"]["filters"] for p in posts]
    # big-money first: the expensive end of unique exchanges, then the targeted sacrifices, the broad sweeps last
    assert flt[0]["ultimatum_filters"]["filters"] == {"ultimatum_reward": {"option": "ExchangeUnique"}}
    assert flt[0]["trade_filters"]["filters"]["price"]["min"] >= 2000
    assert flt[1]["ultimatum_filters"]["filters"] == {"ultimatum_input": {"option": "Divine Orb"}}
    cheap = [f for f in flt if f["ultimatum_filters"]["filters"] == {"ultimatum_reward": {"option": "DoubleCurrency"}}
             and "trade_filters" not in f]
    assert cheap and all("stats" not in p["query"] for p in posts)  # hard modifiers are only marked, never excluded
    assert "Divine Orb" in found and sc.known["Divine Orb"] >= 3 and sc.known_file.is_file()
    assert any("discovery 1/7" in s for s in statuses) and any(": Divine Orb" in s for s in statuses)
    LIMITER.local.clear()

def test_ultimatum_scan_paces_and_keeps_a_reserve() -> None:
    import time as _t
    from src.trade_search import LIMITER
    from src.ultimatum_scan import UltimatumScanner

    LIMITER.local.clear(); LIMITER.server.clear(); LIMITER.blocked_until = 0
    url = "https://www.pathofexile.com/api/trade/search/Covens"
    # 26 of 30 searches per 5 min already used (e.g. by Awakened): the scan waits, a normal Ctrl+F3 still may go
    LIMITER.after(url, {"X-Rate-Limit-Ip": "5:10:60,15:60:300,30:300:1800", "X-Rate-Limit-Ip-State": "0:10:0,0:60:0,26:300:0"})
    assert LIMITER.wait_needed(url, reserve=4) > 0 and LIMITER.wait_needed(url, reserve=1) == 0
    slept = []
    sc = UltimatumScanner(lambda: None, lambda: {}, lambda *a: None, lambda s: None, sleep=lambda s: (slept.append(s), sc.stop()))
    assert sc._wait_turn(url, sc.SEARCH_RESERVE) is False and slept  # waited (and was stopped while waiting)
    LIMITER.server.clear(); LIMITER.local.clear()
