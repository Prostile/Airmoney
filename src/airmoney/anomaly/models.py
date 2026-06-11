from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from airmoney.config.models import MarketListing, utc_now, utc_now_iso


@dataclass
class ParsedListing:
    item_id: str
    expected_market_hash_name: str
    actual_title: str
    listing_url: str | None
    raw_text: str
    price_rub: float
    wear_rating: float | None
    pattern_template: int | None
    is_souvenir: bool
    is_stattrak: bool
    exterior: str | None
    parsed_at: datetime
    listing_id: str = ""
    search_url: str = ""
    currency_source: str = ""
    currency_fetched_at: str = ""


@dataclass
class BaselineResult:
    median: float | None = None
    discount_percent: float | None = None
    robust_z: float | None = None
    sample_size: int = 0
    bucket: str | None = None
    neighbor_count: int = 0


@dataclass
class AnomalyResult:
    listing: ParsedListing
    fair_price_rub: float | None
    local_median_rub: float | None
    float_peer_median_rub: float | None
    historical_baseline_rub: float | None
    local_discount_percent: float | None
    float_peer_discount_percent: float | None
    historical_discount_percent: float | None
    net_resale_rub: float | None
    net_profit_rub: float | None
    roi_percent: float | None
    anomaly_score: float
    alert_level: str
    reasons: list[str] = field(default_factory=list)
    robust_z: float | None = None
    float_bucket: str | None = None
    exact_item_match: bool = False
    sample_size: int = 0
    neighbor_count: int = 0
    analysis_mode: str = "anomaly"


def parsed_listing_from_market_listing(
    listing: MarketListing,
    item: dict[str, Any],
) -> ParsedListing:
    title = listing.skin_name or listing.market_hash_name or str(item.get("market_hash_name") or "")
    title_lower = title.lower()
    return ParsedListing(
        item_id=listing.item_definition_id,
        expected_market_hash_name=str(item.get("market_hash_name") or listing.market_hash_name or title),
        actual_title=title,
        listing_url=listing.listing_url or None,
        raw_text=listing.raw_text,
        price_rub=listing.buy_price_rub,
        wear_rating=listing.float_value,
        pattern_template=listing.pattern,
        is_souvenir=title_lower.startswith("souvenir ") or bool(item.get("is_souvenir")),
        is_stattrak="stattrak" in title_lower or "stat trak" in title_lower or bool(item.get("is_stattrak")),
        exterior=str(item.get("exterior") or "") or None,
        parsed_at=utc_now(),
        listing_id=listing.id,
        search_url=listing.search_url,
        currency_source=listing.currency_source,
        currency_fetched_at=listing.currency_fetched_at,
    )


def parsed_at_iso(listing: ParsedListing) -> str:
    try:
        return listing.parsed_at.replace(microsecond=0).isoformat()
    except Exception:
        return utc_now_iso()
