from __future__ import annotations

import re
from typing import Any

from airmoney.anomaly.models import ParsedListing


EXTERIOR_ALIASES = {
    "factory new": "factory new",
    "minimal wear": "minimal wear",
    "field-tested": "field-tested",
    "well-worn": "well-worn",
    "battle-scarred": "battle-scarred",
}


def normalize_market_name(value: str) -> str:
    text = str(value or "").lower().replace("™", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_exact_item_match(listing: ParsedListing, expected_name: str) -> bool:
    actual = normalize_market_name(listing.actual_title)
    expected = normalize_market_name(expected_name)
    return bool(actual and expected and actual == expected)


def is_compatible_listing(listing: ParsedListing, item_config: dict[str, Any]) -> bool:
    actual = normalize_market_name(listing.actual_title)
    expected = normalize_market_name(str(item_config.get("market_hash_name") or ""))
    if not actual or not expected:
        return False

    item_is_souvenir = bool(item_config.get("is_souvenir"))
    item_is_stattrak = bool(item_config.get("is_stattrak"))
    actual_is_souvenir = actual.startswith("souvenir ")
    actual_is_stattrak = "stattrak" in actual or "stat trak" in actual

    if item_is_souvenir != actual_is_souvenir:
        return False
    if item_is_stattrak != actual_is_stattrak:
        return False

    exterior = normalize_market_name(str(item_config.get("exterior") or ""))
    if exterior and exterior not in actual:
        return False

    stripped_expected = expected.replace("souvenir ", "").replace("stattrak ", "").strip()
    stripped_actual = actual.replace("souvenir ", "").replace("stattrak ", "").strip()
    return stripped_expected in stripped_actual


def passes_item_match(
    listing: ParsedListing,
    item_config: dict[str, Any],
    require_exact_item_match: bool = True,
) -> bool:
    expected = str(item_config.get("market_hash_name") or listing.expected_market_hash_name)
    if require_exact_item_match:
        return is_exact_item_match(listing, expected)
    return is_exact_item_match(listing, expected) or is_compatible_listing(listing, item_config)
