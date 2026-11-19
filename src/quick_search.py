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

from src.trade_search import TRADE, ULTIMATUM_STATUS, _http_get, _http_post, to_chaos
from src.ultimatum_scan import UltimatumDeal, parse_listing

ULTIMATUM_REWARDS = {  # label -> trade-site option
    "Doubles currency": "DoubleCurrency",
    "Doubles divination cards": "DoubleDivCards",
    "Exchanges a unique": "ExchangeUnique",
    "Any reward": "",
}
BLUEPRINTS = ("Any blueprint", "Bunker", "Records Office", "Laboratory", "Prohibited Library", "Mansion",
              "Smuggler's Den", "Repository", "Tunnels", "Underbelly")


def _site_url(league: str, query_id: str) -> str:
    return f"https://www.pathofexile.com/trade/search/{urllib.parse.quote(league)}/{query_id}"


def ultimatum_body(reward_type: str, item: str, max_chaos: float = 0.0) -> dict:
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
    return {"query": {"status": {"option": ULTIMATUM_STATUS}, "type": "Inscribed Ultimatum", "filters": filters},
            "sort": {"price": "asc"}}


def search_ultimatums(idx, league: str, reward_type: str, item: str, max_chaos: float = 0.0, pages: int = 2,
                      post=None, get=None) -> Tuple[List[UltimatumDeal], str, int]:
    """-> (deals, url of the search on pathofexile.com, how many are listed)."""
    post, get = post or _http_post, get or _http_get
    lg = urllib.parse.quote(league)
    res = post(f"{TRADE}/search/{lg}", ultimatum_body(reward_type, item, max_chaos))
    ids, qid = res.get("result", [])[: 10 * pages], res.get("id", "")
    deals: List[UltimatumDeal] = []
    for i in range(0, len(ids), 10):
        for r in get(f"{TRADE}/fetch/{','.join(ids[i:i + 10])}?query={qid}").get("result", []):
            d = parse_listing(r, idx, league, qid)
            if d:
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


def blueprint_body(blueprint: str = "", wings: int = 0, total_wings: int = 0, min_area: int = 0,
                   max_chaos: float = 0.0) -> dict:
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
                      post=None, get=None) -> Tuple[List[BlueprintListing], str, int]:
    post, get = post or _http_post, get or _http_get
    lg = urllib.parse.quote(league)
    res = post(f"{TRADE}/search/{lg}", blueprint_body(blueprint, wings, total_wings, min_area, max_chaos))
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
            ))
    return out, _site_url(league, qid), int(res.get("total") or len(ids))
