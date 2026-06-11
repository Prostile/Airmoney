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


DEFAULT_FLOAT_BUCKETS = [
    {"id": "micro", "min": 0.0, "max": 0.005},
    {"id": "very_low", "min": 0.005, "max": 0.010},
    {"id": "craft_low", "min": 0.010, "max": 0.015},
    {"id": "low_fn", "min": 0.015, "max": 0.030},
    {"id": "normal_fn", "min": 0.030, "max": 0.070},
]


DEFAULT_TELEGRAM_ALERT_CONFIG = {
    "message_format": "compact",
    "include_link": True,
    "include_pattern": False,
    "include_sample_stats": False,
    "include_reasons": False,
    "batch_alerts": True,
    "batch_interval_seconds": 60,
    "max_alerts_per_message": 5,
    "max_message_length": 3500,
}


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


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _json_dict(value: str | dict[str, Any] | None, default: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        raw = value
    else:
        try:
            raw = json.loads(value or "{}")
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {**default, **raw}


def _json_list(value: Any, default: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(value, list):
        raw = value
    else:
        try:
            raw = json.loads(value or "[]")
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return [dict(row) for row in default]
    return [row for row in raw if isinstance(row, dict)]


@dataclass
class FloatBucketConfig:
    id: str
    min: float
    max: float


@dataclass
class AnomalySampleSettings:
    min_listings: int = 8
    target_listings: int = 30
    max_listings: int = 60
    exclude_candidate_from_baseline: bool = True
    require_exact_item_match: bool = True


@dataclass
class AnomalyThresholdSettings:
    min_local_discount_percent: float = 15.0
    min_float_peer_discount_percent: float = 12.0
    min_net_profit_rub: float = 120.0
    min_roi_percent: float = 8.0
    critical_score: float = 85.0
    good_score: float = 70.0
    watch_score: float = 55.0


@dataclass
class AnomalyScoringSettings:
    local_discount_weight: float = 0.45
    float_peer_discount_weight: float = 0.35
    historical_discount_weight: float = 0.15
    float_quality_weight: float = 0.05


@dataclass
class NearestNeighborsSettings:
    enabled: bool = True
    k: int = 7
    min_neighbors: int = 5
    max_float_distance: float = 0.025


@dataclass
class AnomalyHistorySettings:
    enabled: bool = False
    storage: str = "sqlite"
    ewma_alpha: float = 0.25
    min_snapshots: int = 5


@dataclass
class AnomalySettings:
    enabled: bool = True
    sample: AnomalySampleSettings = field(default_factory=AnomalySampleSettings)
    thresholds: AnomalyThresholdSettings = field(default_factory=AnomalyThresholdSettings)
    scoring: AnomalyScoringSettings = field(default_factory=AnomalyScoringSettings)
    float_buckets: list[FloatBucketConfig] = field(
        default_factory=lambda: [
            FloatBucketConfig(str(row["id"]), float(row["min"]), float(row["max"]))
            for row in DEFAULT_FLOAT_BUCKETS
        ]
    )
    nearest_neighbors: NearestNeighborsSettings = field(default_factory=NearestNeighborsSettings)
    history: AnomalyHistorySettings = field(default_factory=AnomalyHistorySettings)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AnomalySettings":
        raw = data or {}
        sample = _json_dict(raw.get("sample"), {})
        thresholds = _json_dict(raw.get("thresholds"), {})
        scoring = _json_dict(raw.get("scoring"), {})
        nearest = _json_dict(raw.get("nearest_neighbors"), {})
        history = _json_dict(raw.get("history"), {})
        buckets = []
        for row in _json_list(raw.get("float_buckets"), DEFAULT_FLOAT_BUCKETS):
            bucket_id = str(row.get("id", "")).strip()
            if not bucket_id:
                continue
            buckets.append(
                FloatBucketConfig(
                    id=bucket_id,
                    min=_safe_float(row.get("min"), 0.0),
                    max=_safe_float(row.get("max"), 0.0),
                )
            )
        if not buckets:
            buckets = cls().float_buckets
        return cls(
            enabled=to_bool(raw.get("enabled", True)),
            sample=AnomalySampleSettings(
                min_listings=max(1, _safe_int(sample.get("min_listings"), 8)),
                target_listings=max(1, _safe_int(sample.get("target_listings"), 30)),
                max_listings=max(1, _safe_int(sample.get("max_listings"), 60)),
                exclude_candidate_from_baseline=to_bool(sample.get("exclude_candidate_from_baseline", True)),
                require_exact_item_match=to_bool(sample.get("require_exact_item_match", True)),
            ),
            thresholds=AnomalyThresholdSettings(
                min_local_discount_percent=max(0.0, _safe_float(thresholds.get("min_local_discount_percent"), 15.0)),
                min_float_peer_discount_percent=max(0.0, _safe_float(thresholds.get("min_float_peer_discount_percent"), 12.0)),
                min_net_profit_rub=max(0.0, _safe_float(thresholds.get("min_net_profit_rub"), 120.0)),
                min_roi_percent=max(0.0, _safe_float(thresholds.get("min_roi_percent"), 8.0)),
                critical_score=max(0.0, _safe_float(thresholds.get("critical_score"), 85.0)),
                good_score=max(0.0, _safe_float(thresholds.get("good_score"), 70.0)),
                watch_score=max(0.0, _safe_float(thresholds.get("watch_score"), 55.0)),
            ),
            scoring=AnomalyScoringSettings(
                local_discount_weight=max(0.0, _safe_float(scoring.get("local_discount_weight"), 0.45)),
                float_peer_discount_weight=max(0.0, _safe_float(scoring.get("float_peer_discount_weight"), 0.35)),
                historical_discount_weight=max(0.0, _safe_float(scoring.get("historical_discount_weight"), 0.15)),
                float_quality_weight=max(0.0, _safe_float(scoring.get("float_quality_weight"), 0.05)),
            ),
            float_buckets=buckets,
            nearest_neighbors=NearestNeighborsSettings(
                enabled=to_bool(nearest.get("enabled", True)),
                k=max(1, _safe_int(nearest.get("k"), 7)),
                min_neighbors=max(1, _safe_int(nearest.get("min_neighbors"), 5)),
                max_float_distance=max(0.0, _safe_float(nearest.get("max_float_distance"), 0.025)),
            ),
            history=AnomalyHistorySettings(
                enabled=to_bool(history.get("enabled", False)),
                storage=str(history.get("storage", "sqlite") or "sqlite"),
                ewma_alpha=max(0.0, min(1.0, _safe_float(history.get("ewma_alpha"), 0.25))),
                min_snapshots=max(1, _safe_int(history.get("min_snapshots"), 5)),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "sample": {
                "min_listings": self.sample.min_listings,
                "target_listings": self.sample.target_listings,
                "max_listings": self.sample.max_listings,
                "exclude_candidate_from_baseline": self.sample.exclude_candidate_from_baseline,
                "require_exact_item_match": self.sample.require_exact_item_match,
            },
            "thresholds": {
                "min_local_discount_percent": self.thresholds.min_local_discount_percent,
                "min_float_peer_discount_percent": self.thresholds.min_float_peer_discount_percent,
                "min_net_profit_rub": self.thresholds.min_net_profit_rub,
                "min_roi_percent": self.thresholds.min_roi_percent,
                "critical_score": self.thresholds.critical_score,
                "good_score": self.thresholds.good_score,
                "watch_score": self.thresholds.watch_score,
            },
            "scoring": {
                "local_discount_weight": self.scoring.local_discount_weight,
                "float_peer_discount_weight": self.scoring.float_peer_discount_weight,
                "historical_discount_weight": self.scoring.historical_discount_weight,
                "float_quality_weight": self.scoring.float_quality_weight,
            },
            "float_buckets": [
                {"id": bucket.id, "min": bucket.min, "max": bucket.max}
                for bucket in self.float_buckets
            ],
            "nearest_neighbors": {
                "enabled": self.nearest_neighbors.enabled,
                "k": self.nearest_neighbors.k,
                "min_neighbors": self.nearest_neighbors.min_neighbors,
                "max_float_distance": self.nearest_neighbors.max_float_distance,
            },
            "history": {
                "enabled": self.history.enabled,
                "storage": self.history.storage,
                "ewma_alpha": self.history.ewma_alpha,
                "min_snapshots": self.history.min_snapshots,
            },
        }


@dataclass
class TelegramAlertSettings:
    message_format: str = "compact"
    include_link: bool = True
    include_pattern: bool = False
    include_sample_stats: bool = False
    include_reasons: bool = False
    batch_alerts: bool = True
    batch_interval_seconds: int = 60
    max_alerts_per_message: int = 5
    max_message_length: int = 3500

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TelegramAlertSettings":
        raw = {**DEFAULT_TELEGRAM_ALERT_CONFIG, **(data or {})}
        return cls(
            message_format=str(raw.get("message_format") or "compact"),
            include_link=to_bool(raw.get("include_link", True)),
            include_pattern=to_bool(raw.get("include_pattern", False)),
            include_sample_stats=to_bool(raw.get("include_sample_stats", False)),
            include_reasons=to_bool(raw.get("include_reasons", False)),
            batch_alerts=to_bool(raw.get("batch_alerts", True)),
            batch_interval_seconds=max(1, _safe_int(raw.get("batch_interval_seconds"), 60)),
            max_alerts_per_message=max(1, _safe_int(raw.get("max_alerts_per_message"), 5)),
            max_message_length=max(1000, _safe_int(raw.get("max_message_length"), 3500)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_format": self.message_format,
            "include_link": self.include_link,
            "include_pattern": self.include_pattern,
            "include_sample_stats": self.include_sample_stats,
            "include_reasons": self.include_reasons,
            "batch_alerts": self.batch_alerts,
            "batch_interval_seconds": self.batch_interval_seconds,
            "max_alerts_per_message": self.max_alerts_per_message,
            "max_message_length": self.max_message_length,
        }


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
    anomaly_config: str = field(default_factory=lambda: json.dumps(AnomalySettings().to_dict(), ensure_ascii=False))
    telegram_config: str = field(default_factory=lambda: json.dumps(DEFAULT_TELEGRAM_ALERT_CONFIG, ensure_ascii=False))
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

    @property
    def anomaly_settings(self) -> AnomalySettings:
        try:
            data = json.loads(self.anomaly_config or "{}")
        except Exception:
            data = {}
        return AnomalySettings.from_dict(data if isinstance(data, dict) else {})

    def set_anomaly_settings(self, value: AnomalySettings) -> None:
        self.anomaly_config = json.dumps(value.to_dict(), ensure_ascii=False)

    @property
    def telegram_alert_settings(self) -> TelegramAlertSettings:
        try:
            data = json.loads(self.telegram_config or "{}")
        except Exception:
            data = {}
        return TelegramAlertSettings.from_dict(data if isinstance(data, dict) else {})

    def set_telegram_alert_settings(self, value: TelegramAlertSettings) -> None:
        self.telegram_config = json.dumps(value.to_dict(), ensure_ascii=False)


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
class MarketValuation:
    id: str
    item_definition_id: str
    manual_target_price_rub: float | None = None
    target_net_roi_percent: float | None = None
    min_buy_price_rub: float | None = None
    max_buy_price_rub: float | None = None
    liquidity_note: str = ""
    confidence_level: str = "medium"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


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
    analysis_mode: str = "legacy"
    alert_level: str = ""
    anomaly_score: float | None = None
    fair_price_rub: float | None = None
    local_median_rub: float | None = None
    float_peer_median_rub: float | None = None
    historical_baseline_rub: float | None = None
    local_discount_percent: float | None = None
    float_peer_discount_percent: float | None = None
    historical_discount_percent: float | None = None
    robust_z: float | None = None
    float_bucket: str = ""
    exact_item_match: bool = False
    sample_size: int = 0
    neighbor_count: int = 0
    anomaly_reasons: str = ""
    parsed_at: str = ""
    status: str = "new"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
