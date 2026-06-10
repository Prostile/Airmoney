from __future__ import annotations

import re
import urllib.parse

from airmoney.config.models import EXTERIORS


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
