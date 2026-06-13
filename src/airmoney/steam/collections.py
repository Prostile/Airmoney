from __future__ import annotations

import base64
import re
import struct
import urllib.parse
from typing import Any

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


def steam_float_assetproperty(float_min: float | None = None, float_max: float | None = None) -> str:
    if float_min is None and float_max is None:
        return ""
    left = _clamp_float_filter_value(float_min if float_min is not None else 0.0)
    right = _clamp_float_filter_value(float_max if float_max is not None else 1.0)
    if left > right:
        return ""
    payload = b"\x08\x02" + b"\x15" + struct.pack("<f", left) + b"\x1d" + struct.pack("<f", right)
    return base64.b64encode(payload).decode("ascii").rstrip("=")


def steam_market_filter_params(item: dict, rule: dict[str, Any] | None = None) -> dict[str, str]:
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
    float_filter = _steam_float_filter_range(rule)
    if float_filter is not None:
        assetproperty = steam_float_assetproperty(*float_filter)
        if assetproperty:
            params["appid"] = "730"
            params["assetproperty"] = assetproperty
    return params


def _steam_float_filter_range(rule: dict[str, Any] | None) -> tuple[float | None, float | None] | None:
    if not rule:
        return None
    if str(rule.get("enabled", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return None
    float_min = _optional_float(rule.get("float_min"))
    float_max = _optional_float(rule.get("float_max"))
    if float_min is not None or float_max is not None:
        return float_min, float_max
    target_min = _optional_float(rule.get("target_float_min"))
    target_max = _optional_float(rule.get("target_float_max"))
    if target_min is not None or target_max is not None:
        return target_min, target_max
    return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_float_filter_value(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
