from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import json


EXTERIORS = [
    "Factory New",
    "Minimal Wear",
    "Field-Tested",
    "Well-Worn",
    "Battle-Scarred",
]

RECOMMENDATION_LEVELS = ["critical", "good", "watch", "skip"]
CANDIDATE_STATUSES = ["new", "opened", "checked", "bought_manually", "skipped", "expired"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "да"}


@dataclass
class Collection:
    id: str
    name: str
    steam_collection_url: str = ""
    enabled: bool = True
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class ItemDefinition:
    id: str
    collection_id: str
    market_hash_name: str
    display_name: str = ""
    weapon_type: str = ""
    rarity: str = ""
    quality: str = ""
    exterior: str = ""
    is_souvenir: bool = False
    is_stattrak: bool = False
    steam_market_url: str = ""
    enabled: bool = True
    last_parsed_at: str | None = None


@dataclass
class ParserSettings:
    enabled: bool = False
    check_interval_seconds: int = 300
    headless: bool = True
    max_scrolls: int = 1
    request_delay_seconds: float = 2.0
    steam_block_pause_seconds: int = 1800
    currency_provider: str = "steam_currency"
    currency_cache_ttl_seconds: int = 21600
    fallback_usd_to_rub: float = 72.0
    fallback_eur_to_rub: float = 86.0
    telegram_alerts_enabled: bool = False
    telegram_min_alert_level: str = "good"
    web_table_limit: int = 200
    default_roi_percent: float = 12.0
    default_market_fee_percent: float = 15.0
    default_min_profit_rub: float = 300.0
    default_min_roi_percent: float = 7.0
    selected_exteriors: str = field(default_factory=lambda: json.dumps(EXTERIORS))
    updated_at: str = field(default_factory=utc_now_iso)

    @property
    def selected_exterior_list(self) -> list[str]:
        try:
            values = json.loads(self.selected_exteriors or "[]")
        except Exception:
            values = []
        if not isinstance(values, list):
            return []
        return [str(value) for value in values if str(value) in EXTERIORS]

    def set_selected_exteriors(self, values: list[str]) -> None:
        selected = [value for value in values if value in EXTERIORS]
        self.selected_exteriors = json.dumps(selected, ensure_ascii=False)


@dataclass
class SnipingRule:
    id: str
    item_definition_id: str
    enabled: bool = True
    max_buy_price_rub: float | None = None
    target_resale_price_rub: float | None = None
    custom_roi_percent: float | None = None
    min_profit_rub: float | None = None
    min_roi_percent: float | None = None
    float_min: float | None = None
    float_max: float | None = None
    target_float_min: float | None = None
    target_float_max: float | None = None
    pattern_ranges: str = ""
    priority: int = 0
    telegram_alert_enabled: bool = True
    notes: str = ""


@dataclass
class MarketListing:
    id: str
    item_definition_id: str
    rule_id: str | None
    skin_name: str
    market_hash_name: str = ""
    listing_url: str = ""
    search_url: str = ""
    buy_price_rub: float = 0.0
    buy_price_original: float | None = None
    currency_original: str = "RUB"
    currency_rate: float | None = None
    currency_source: str = ""
    currency_fetched_at: str = ""
    float_value: float | None = None
    pattern: int | None = None
    wear_name: str = ""
    raw_text: str = ""
    first_seen_at: str = field(default_factory=utc_now_iso)
    last_seen_at: str = field(default_factory=utc_now_iso)
    is_active: bool = True
    parse_status: str = "ok"


@dataclass
class Candidate:
    id: str
    listing_id: str
    rule_id: str | None
    buy_price_rub: float
    estimated_resale_price_rub: float
    estimated_net_resale_rub: float
    estimated_profit_rub: float
    estimated_roi_percent: float
    market_fee_percent: float
    recommendation_level: str
    recommendation_score: float
    recommendation_reason: str
    status: str = "new"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
