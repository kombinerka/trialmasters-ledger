"""
🔎 One-off searches you start yourself (no background scanning):

  * Inscribed Ultimatums by reward – e.g. "Divine Orb doubled, under 150 chaos", or "gives Defiance of Destiny"
  * Heist Blueprints by revealed wings – e.g. "4/4 wings, area level 83, under 2 div"

Each search is one trade-site search plus one or two listing reads, paced by the same limiter as everything else.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.trade_search import TRADE, ULTIMATUM_STATUS, TradeQueryError, _http_get, _http_post, to_chaos
from src.ultimatum_scan import UltimatumDeal, parse_listing

ULTIMATUM_REWARDS = {  # label -> trade-site option
    "Doubles currency": "DoubleCurrency",
    "Doubles divination cards": "DoubleDivCards",
    "Exchanges a unique": "ExchangeUnique",
    "Any reward": "",
}
# blueprint enchant "Heist Targets are always …" -> trade stat id
BLUEPRINT_TARGETS = {
    "Currency (or Threads/Scarabs)": "enchant.stat_4168369352",
    "Thieves' Trinkets": "enchant.stat_1123534836",
    "Enchanted Armaments": "enchant.stat_3709545805",
    "Experimented Items": "enchant.stat_4182516619",
    "Replica Unique Items": "enchant.stat_2619914138",
}
BLUEPRINTS = ("Any blueprint", "Bunker", "Records Office", "Laboratory", "Prohibited Library", "Mansion",
              "Smuggler's Den", "Repository", "Tunnels", "Underbelly")


def _site_url(league: str, query_id: str) -> str:
    return f"https://www.pathofexile.com/trade/search/{urllib.parse.quote(league)}/{query_id}"


def ultimatum_body(reward_type: str, item: str, max_chaos: float = 0.0, not_ids=()) -> dict:
    """reward_type: DoubleCurrency / DoubleDivCards / ExchangeUnique / ''. item: the sacrifice when something is
    doubled, the unique you get when it's an exchange (or '' for any)."""
    uf: dict = {}
    if reward_type:
        uf["ultimatum_reward"] = {"option": reward_type}
    if item:
        uf["ultimatum_output" if reward_type == "ExchangeUnique" else "ultimatum_input"] = {"option": item}
    filters: dict = {"ultimatum_filters": {"filters": uf}}
    if max_chaos > 0:
        filters["trade_filters"] = {"filters": {"price": {"max": max_chaos}}}
    query: dict = {"status": {"option": ULTIMATUM_STATUS}, "type": "Inscribed Ultimatum", "filters": filters}
    if not_ids:  # your hard modifiers left out by the site itself
        query["stats"] = [{"type": "not", "filters": [{"id": i} for i in not_ids]}]
    return {"query": query, "sort": {"price": "asc"}}


def search_ultimatums(idx, league: str, reward_type: str, item: str, max_chaos: float = 0.0, pages: int = 2,
                      post=None, get=None, not_ids=(), only_clean: bool = False) -> Tuple[List[UltimatumDeal], str, int]:
    """
    -> (deals, url of the search on pathofexile.com, how many are listed). only_clean: no listing with one of your
    hard modifiers (left out by the site via `not_ids`; if it rejects that group, they're dropped here instead).
    """
    post, get = post or _http_post, get or _http_get
    lg = urllib.parse.quote(league)
    try:
        res = post(f"{TRADE}/search/{lg}", ultimatum_body(reward_type, item, max_chaos, not_ids if only_clean else ()))
    except TradeQueryError:
        if not (only_clean and not_ids):
            raise
        res = post(f"{TRADE}/search/{lg}", ultimatum_body(reward_type, item, max_chaos))
    ids, qid = res.get("result", [])[: 10 * pages], res.get("id", "")
    deals: List[UltimatumDeal] = []
    for i in range(0, len(ids), 10):
        for r in get(f"{TRADE}/fetch/{','.join(ids[i:i + 10])}?query={qid}").get("result", []):
            d = parse_listing(r, idx, league, qid)
            if d and not (only_clean and d.hard_mods):
                deals.append(d)
    return deals, _site_url(league, qid), int(res.get("total") or len(ids))


@dataclass
class BlueprintListing:
    name: str
    price_text: str
    price_chaos: Optional[float]
    area_level: int
    wings: str  # "4/4"
    escape_routes: str
    reward_rooms: str
    seller: str
    indexed: str
    targets: str = ""  # "Heist Targets are always Replica Unique Items" (the blueprint's enchant), "" = none


def blueprint_body(blueprint: str = "", wings: int = 0, total_wings: int = 0, min_area: int = 0,
                   max_chaos: float = 0.0, target: str = "") -> dict:
    """target: '' any · 'none' no "Heist Targets are always …" enchant · or one of BLUEPRINT_TARGETS' stat ids."""
    hf: dict = {}
    if wings:
        hf["heist_wings"] = {"min": wings}
    if total_wings:
        hf["heist_max_wings"] = {"min": total_wings, "max": total_wings}
    filters: dict = {"type_filters": {"filters": {"category": {"option": "heistmission.blueprint"}}},
                     "heist_filters": {"filters": hf}}
    if min_area:
        filters["map_filters"] = {"filters": {"area_level": {"min": min_area}}}
    if max_chaos > 0:
        filters["trade_filters"] = {"filters": {"price": {"max": max_chaos}}}
    query: dict = {"status": {"option": "securable"}, "filters": filters}
    if target == "none":
        query["stats"] = [{"type": "not", "filters": [{"id": i} for i in BLUEPRINT_TARGETS.values()]}]
    elif target:
        query["stats"] = [{"type": "and", "filters": [{"id": target}]}]
    if blueprint and not blueprint.startswith("Any"):
        query["type"] = f"Blueprint: {blueprint}"
    return {"query": query, "sort": {"price": "asc"}}


def _prop(item: dict, name: str) -> str:
    for p in item.get("properties") or []:
        if p.get("name") == name and p.get("values"):
            return str(p["values"][0][0])
    return ""


def search_blueprints(league: str, blueprint: str = "", wings: int = 0, total_wings: int = 0, min_area: int = 0,
                      max_chaos: float = 0.0, divine_chaos: float = 0.0, pages: int = 2,
                      post=None, get=None, target: str = "") -> Tuple[List[BlueprintListing], str, int]:
    post, get = post or _http_post, get or _http_get
    lg = urllib.parse.quote(league)
    res = post(f"{TRADE}/search/{lg}", blueprint_body(blueprint, wings, total_wings, min_area, max_chaos, target))
    ids, qid = res.get("result", [])[: 10 * pages], res.get("id", "")
    out: List[BlueprintListing] = []
    for i in range(0, len(ids), 10):
        for r in get(f"{TRADE}/fetch/{','.join(ids[i:i + 10])}?query={qid}").get("result", []):
            listing, item = r.get("listing") or {}, r.get("item") or {}
            price = listing.get("price") or {}
            amount, cur = price.get("amount"), str(price.get("currency") or "")
            level = _prop(item, "Area Level")
            out.append(BlueprintListing(
                name=str(item.get("typeLine") or item.get("baseType") or ""),
                price_text=f"{amount:g} {cur}" if amount is not None else "?",
                price_chaos=to_chaos(float(amount), cur, divine_chaos) if amount is not None else None,
                area_level=int(level) if level.isdigit() else 0,
                wings=_prop(item, "Wings Revealed"), escape_routes=_prop(item, "Escape Routes Revealed"),
                reward_rooms=_prop(item, "Reward Rooms Revealed"),
                seller=str((listing.get("account") or {}).get("name", "")),
                indexed=str(listing.get("indexed", ""))[:16].replace("T", " "),
                targets=", ".join(str(m.get("description", "")) if isinstance(m, dict) else str(m)
                                  for m in (item.get("enchantMods") or [])),
            ))
    return out, _site_url(league, qid), int(res.get("total") or len(ids))
