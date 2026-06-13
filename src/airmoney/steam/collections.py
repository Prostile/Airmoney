from __future__ import annotations

import re
import urllib.parse

from airmoney.config.models import EXTERIORS


EXTERIOR_FILTER_TAGS = {
    "Factory New": "tag_WearCategory0",
    "Minimal Wear": "tag_WearCategory1",
    "Field-Tested": "tag_WearCategory2",
    "Well-Worn": "tag_WearCategory3",
    "Battle-Scarred": "tag_WearCategory4",
}


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9а-яё]+", "_", text, flags=re.IGNORECASE)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "item"


def build_market_listing_url(market_hash_name: str) -> str:
    encoded = urllib.parse.quote(market_hash_name)
    return f"https://steamcommunity.com/market/listings/730/{encoded}"


def build_exterior_variants(base_market_hash_name: str, exteriors: list[str] | None = None) -> list[str]:
    selected = exteriors or EXTERIORS
    base = re.sub(r"\s+\((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)\s*$", "", base_market_hash_name)
    return [f"{base} ({exterior})" for exterior in selected]


def steam_market_filter_params(item: dict) -> dict[str, str]:
    params: dict[str, str] = {}
    exterior_tag = EXTERIOR_FILTER_TAGS.get(str(item.get("exterior") or ""))
    if exterior_tag:
        params["appid"] = "730"
        params["category_730_Exterior"] = exterior_tag
    if item.get("is_souvenir"):
        params["appid"] = "730"
        params["category_730_Quality"] = "tag_tournament"
    elif item.get("is_stattrak"):
        params["appid"] = "730"
        params["category_730_Quality"] = "tag_strange"
    return params
