from __future__ import annotations

from dataclasses import asdict, dataclass, field
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

RARITIES = [
    "Consumer Grade",
    "Industrial Grade",
    "Mil-Spec Grade",
    "Restricted",
    "Classified",
    "Covert",
    "Contraband",
]

QUALITIES = [
    "Normal",
    "Souvenir",
    "StatTrak",
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
    min_listings: int = 5
    target_listings: int = 12
    max_listings: int = 20
    exclude_candidate_from_baseline: bool = True
    require_exact_item_match: bool = True
    sort_by: str = "price_asc"


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
class AnomalyDebugSettings:
    save_skip_candidates: bool = False
    log_rejected_exact_match: bool = True
    max_rejected_exact_match_log: int = 5


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
    debug: AnomalyDebugSettings = field(default_factory=AnomalyDebugSettings)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AnomalySettings":
        raw = data or {}
        sample = _json_dict(raw.get("sample"), {})
        thresholds = _json_dict(raw.get("thresholds"), {})
        scoring = _json_dict(raw.get("scoring"), {})
        nearest = _json_dict(raw.get("nearest_neighbors"), {})
        history = _json_dict(raw.get("history"), {})
        debug = _json_dict(raw.get("debug"), {})
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
                min_listings=max(1, _safe_int(sample.get("min_listings"), 5)),
                target_listings=max(1, _safe_int(sample.get("target_listings"), 12)),
                max_listings=max(1, _safe_int(sample.get("max_listings"), 20)),
                exclude_candidate_from_baseline=to_bool(sample.get("exclude_candidate_from_baseline", True)),
                require_exact_item_match=to_bool(sample.get("require_exact_item_match", True)),
                sort_by=str(sample.get("sort_by") or "price_asc"),
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
            debug=AnomalyDebugSettings(
                save_skip_candidates=to_bool(debug.get("save_skip_candidates", False)),
                log_rejected_exact_match=to_bool(debug.get("log_rejected_exact_match", True)),
                max_rejected_exact_match_log=max(0, _safe_int(debug.get("max_rejected_exact_match_log"), 5)),
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
                "sort_by": self.sample.sort_by,
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
            "debug": {
                "save_skip_candidates": self.debug.save_skip_candidates,
                "log_rejected_exact_match": self.debug.log_rejected_exact_match,
                "max_rejected_exact_match_log": self.debug.max_rejected_exact_match_log,
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
class ScanQueueSettings:
    enabled: bool = True
    max_items_per_cycle: int = 5
    item_cooldown_seconds: int = 1800
    collection_cooldown_seconds: int = 3600
    priority_first: bool = True
    rotate_by_last_parsed_at: bool = True
    random_jitter: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ScanQueueSettings":
        raw = data or {}
        return cls(
            enabled=to_bool(raw.get("enabled", True)),
            max_items_per_cycle=max(1, _safe_int(raw.get("max_items_per_cycle"), 5)),
            item_cooldown_seconds=max(0, _safe_int(raw.get("item_cooldown_seconds"), 1800)),
            collection_cooldown_seconds=max(0, _safe_int(raw.get("collection_cooldown_seconds"), 3600)),
            priority_first=to_bool(raw.get("priority_first", True)),
            rotate_by_last_parsed_at=to_bool(raw.get("rotate_by_last_parsed_at", True)),
            random_jitter=to_bool(raw.get("random_jitter", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BrowserOptimizationSettings:
    block_heavy_resources: bool = True
    blocked_resource_types: list[str] = field(default_factory=lambda: ["image", "media", "font"])
    block_stylesheets: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BrowserOptimizationSettings":
        raw = data or {}
        raw_types = raw.get("blocked_resource_types", ["image", "media", "font"])
        if not isinstance(raw_types, list):
            raw_types = ["image", "media", "font"]
        allowed = {"image", "media", "font", "stylesheet"}
        blocked = [str(value) for value in raw_types if str(value) in allowed]
        if not blocked:
            blocked = ["image", "media", "font"]
        return cls(
            block_heavy_resources=to_bool(raw.get("block_heavy_resources", True)),
            blocked_resource_types=blocked,
            block_stylesheets=to_bool(raw.get("block_stylesheets", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanOptimizationSettings:
    two_stage_scan: bool = True
    shallow_target_listings: int = 8
    shallow_min_gap_percent: float = 10.0
    deep_scan_on_gap: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ScanOptimizationSettings":
        raw = data or {}
        return cls(
            two_stage_scan=to_bool(raw.get("two_stage_scan", True)),
            shallow_target_listings=max(4, _safe_int(raw.get("shallow_target_listings"), 8)),
            shallow_min_gap_percent=max(0.0, _safe_float(raw.get("shallow_min_gap_percent"), 10.0)),
            deep_scan_on_gap=to_bool(raw.get("deep_scan_on_gap", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HistoryOptimizationSettings:
    use_mature_history_for_shallow_scan: bool = True
    mature_history_min_snapshots: int = 5
    mature_history_target_listings: int = 8
    use_stale_baseline_on_scan_failure: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "HistoryOptimizationSettings":
        raw = data or {}
        return cls(
            use_mature_history_for_shallow_scan=to_bool(raw.get("use_mature_history_for_shallow_scan", True)),
            mature_history_min_snapshots=max(1, _safe_int(raw.get("mature_history_min_snapshots"), 5)),
            mature_history_target_listings=max(3, _safe_int(raw.get("mature_history_target_listings"), 8)),
            use_stale_baseline_on_scan_failure=to_bool(raw.get("use_stale_baseline_on_scan_failure", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SteamGuardSettings:
    enabled: bool = True
    cooldown_on_limit_seconds: int = 7200
    max_cooldown_seconds: int = 21600
    backoff_multiplier: float = 2.0
    jitter_percent: int = 20
    retry_network_errors: bool = True
    network_error_retry_delay_seconds: int = 30
    max_network_retries: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SteamGuardSettings":
        raw = data or {}
        cooldown = max(1, _safe_int(raw.get("cooldown_on_limit_seconds"), 7200))
        max_cooldown = max(cooldown, _safe_int(raw.get("max_cooldown_seconds"), 21600))
        return cls(
            enabled=to_bool(raw.get("enabled", True)),
            cooldown_on_limit_seconds=cooldown,
            max_cooldown_seconds=max_cooldown,
            backoff_multiplier=max(1.0, _safe_float(raw.get("backoff_multiplier"), 2.0)),
            jitter_percent=max(0, _safe_int(raw.get("jitter_percent"), 20)),
            retry_network_errors=to_bool(raw.get("retry_network_errors", True)),
            network_error_retry_delay_seconds=max(0, _safe_int(raw.get("network_error_retry_delay_seconds"), 30)),
            max_network_retries=max(0, _safe_int(raw.get("max_network_retries"), 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketRiskSettings:
    enabled: bool = True
    conservative_exit_enabled: bool = True
    exit_price_strategy: str = "conservative"
    min_sample_for_good: int = 8
    min_sample_for_critical: int = 15
    min_neighbor_for_good: int = 5
    min_neighbor_for_critical: int = 10
    thin_market_max_level: str = "good"
    very_thin_market_max_level: str = "watch"
    downgrade_if_requires_sweep: bool = True
    sweep_max_level_without_capital: str = "good"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MarketRiskSettings":
        raw = data or {}
        return cls(
            enabled=to_bool(raw.get("enabled", True)),
            conservative_exit_enabled=to_bool(raw.get("conservative_exit_enabled", True)),
            exit_price_strategy=str(raw.get("exit_price_strategy") or "conservative"),
            min_sample_for_good=max(1, _safe_int(raw.get("min_sample_for_good"), 8)),
            min_sample_for_critical=max(1, _safe_int(raw.get("min_sample_for_critical"), 15)),
            min_neighbor_for_good=max(1, _safe_int(raw.get("min_neighbor_for_good"), 5)),
            min_neighbor_for_critical=max(1, _safe_int(raw.get("min_neighbor_for_critical"), 10)),
            thin_market_max_level=str(raw.get("thin_market_max_level") or "good"),
            very_thin_market_max_level=str(raw.get("very_thin_market_max_level") or "watch"),
            downgrade_if_requires_sweep=to_bool(raw.get("downgrade_if_requires_sweep", True)),
            sweep_max_level_without_capital=str(raw.get("sweep_max_level_without_capital") or "good"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PackDetectionSettings:
    enabled: bool = True
    min_gap_percent: float = 30.0
    min_pack_size: int = 2
    max_pack_size: int = 5
    alert_as_single_pack: bool = True
    max_pack_to_sample_ratio: float = 0.5

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PackDetectionSettings":
        raw = data or {}
        min_pack = max(1, _safe_int(raw.get("min_pack_size"), 2))
        max_pack = max(min_pack, _safe_int(raw.get("max_pack_size"), 5))
        return cls(
            enabled=to_bool(raw.get("enabled", True)),
            min_gap_percent=max(0.0, _safe_float(raw.get("min_gap_percent"), 30.0)),
            min_pack_size=min_pack,
            max_pack_size=max_pack,
            alert_as_single_pack=to_bool(raw.get("alert_as_single_pack", True)),
            max_pack_to_sample_ratio=max(0.0, min(1.0, _safe_float(raw.get("max_pack_to_sample_ratio"), 0.5))),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapitalSettings:
    enabled: bool = True
    max_single_buy_rub: float = 5000.0
    max_bundle_cost_rub: float = 15000.0
    max_units_per_item: int = 3
    warn_if_sweep_required: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CapitalSettings":
        raw = data or {}
        return cls(
            enabled=to_bool(raw.get("enabled", True)),
            max_single_buy_rub=max(0.0, _safe_float(raw.get("max_single_buy_rub"), 5000.0)),
            max_bundle_cost_rub=max(0.0, _safe_float(raw.get("max_bundle_cost_rub"), 15000.0)),
            max_units_per_item=max(1, _safe_int(raw.get("max_units_per_item"), 3)),
            warn_if_sweep_required=to_bool(raw.get("warn_if_sweep_required", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CraftContextSettings:
    enabled: bool = True
    substitute_cap_enabled: bool = True
    substitute_premium_multiplier: float = 1.10
    same_collection_same_rarity: bool = True
    target_float_max: float = 0.015
    min_substitute_sample: int = 3
    substitute_stale_after_seconds: int = 86400

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CraftContextSettings":
        raw = data or {}
        return cls(
            enabled=to_bool(raw.get("enabled", True)),
            substitute_cap_enabled=to_bool(raw.get("substitute_cap_enabled", True)),
            substitute_premium_multiplier=max(0.0, _safe_float(raw.get("substitute_premium_multiplier"), 1.10)),
            same_collection_same_rarity=to_bool(raw.get("same_collection_same_rarity", True)),
            target_float_max=max(0.0, _safe_float(raw.get("target_float_max"), 0.015)),
            min_substitute_sample=max(1, _safe_int(raw.get("min_substitute_sample"), 3)),
            substitute_stale_after_seconds=max(1, _safe_int(raw.get("substitute_stale_after_seconds"), 86400)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    last_scanned_at: str | None = None


@dataclass
class ParserSettings:
    enabled: bool = False
    check_interval_seconds: int = 1200
    headless: bool = True
    max_scrolls: int = 0
    request_delay_seconds: float = 10.0
    steam_block_pause_seconds: int = 7200
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
    scan_queue_config: str = field(default_factory=lambda: json.dumps(ScanQueueSettings().to_dict(), ensure_ascii=False))
    browser_optimization_config: str = field(default_factory=lambda: json.dumps(BrowserOptimizationSettings().to_dict(), ensure_ascii=False))
    scan_optimization_config: str = field(default_factory=lambda: json.dumps(ScanOptimizationSettings().to_dict(), ensure_ascii=False))
    history_optimization_config: str = field(default_factory=lambda: json.dumps(HistoryOptimizationSettings().to_dict(), ensure_ascii=False))
    steam_guard_config: str = field(default_factory=lambda: json.dumps(SteamGuardSettings().to_dict(), ensure_ascii=False))
    market_risk_config: str = field(default_factory=lambda: json.dumps(MarketRiskSettings().to_dict(), ensure_ascii=False))
    pack_detection_config: str = field(default_factory=lambda: json.dumps(PackDetectionSettings().to_dict(), ensure_ascii=False))
    capital_config: str = field(default_factory=lambda: json.dumps(CapitalSettings().to_dict(), ensure_ascii=False))
    craft_context_config: str = field(default_factory=lambda: json.dumps(CraftContextSettings().to_dict(), ensure_ascii=False))
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

    @property
    def scan_queue_settings(self) -> ScanQueueSettings:
        try:
            data = json.loads(self.scan_queue_config or "{}")
        except Exception:
            data = {}
        return ScanQueueSettings.from_dict(data if isinstance(data, dict) else {})

    def set_scan_queue_settings(self, value: ScanQueueSettings) -> None:
        self.scan_queue_config = json.dumps(value.to_dict(), ensure_ascii=False)

    @property
    def browser_optimization_settings(self) -> BrowserOptimizationSettings:
        try:
            data = json.loads(self.browser_optimization_config or "{}")
        except Exception:
            data = {}
        return BrowserOptimizationSettings.from_dict(data if isinstance(data, dict) else {})

    def set_browser_optimization_settings(self, value: BrowserOptimizationSettings) -> None:
        self.browser_optimization_config = json.dumps(value.to_dict(), ensure_ascii=False)

    @property
    def scan_optimization_settings(self) -> ScanOptimizationSettings:
        try:
            data = json.loads(self.scan_optimization_config or "{}")
        except Exception:
            data = {}
        return ScanOptimizationSettings.from_dict(data if isinstance(data, dict) else {})

    def set_scan_optimization_settings(self, value: ScanOptimizationSettings) -> None:
        self.scan_optimization_config = json.dumps(value.to_dict(), ensure_ascii=False)

    @property
    def history_optimization_settings(self) -> HistoryOptimizationSettings:
        try:
            data = json.loads(self.history_optimization_config or "{}")
        except Exception:
            data = {}
        return HistoryOptimizationSettings.from_dict(data if isinstance(data, dict) else {})

    def set_history_optimization_settings(self, value: HistoryOptimizationSettings) -> None:
        self.history_optimization_config = json.dumps(value.to_dict(), ensure_ascii=False)

    @property
    def steam_guard_settings(self) -> SteamGuardSettings:
        try:
            data = json.loads(self.steam_guard_config or "{}")
        except Exception:
            data = {}
        return SteamGuardSettings.from_dict(data if isinstance(data, dict) else {})

    def set_steam_guard_settings(self, value: SteamGuardSettings) -> None:
        self.steam_guard_config = json.dumps(value.to_dict(), ensure_ascii=False)

    @property
    def market_risk_settings(self) -> MarketRiskSettings:
        try:
            data = json.loads(self.market_risk_config or "{}")
        except Exception:
            data = {}
        return MarketRiskSettings.from_dict(data if isinstance(data, dict) else {})

    def set_market_risk_settings(self, value: MarketRiskSettings) -> None:
        self.market_risk_config = json.dumps(value.to_dict(), ensure_ascii=False)

    @property
    def pack_detection_settings(self) -> PackDetectionSettings:
        try:
            data = json.loads(self.pack_detection_config or "{}")
        except Exception:
            data = {}
        return PackDetectionSettings.from_dict(data if isinstance(data, dict) else {})

    def set_pack_detection_settings(self, value: PackDetectionSettings) -> None:
        self.pack_detection_config = json.dumps(value.to_dict(), ensure_ascii=False)

    @property
    def capital_settings(self) -> CapitalSettings:
        try:
            data = json.loads(self.capital_config or "{}")
        except Exception:
            data = {}
        return CapitalSettings.from_dict(data if isinstance(data, dict) else {})

    def set_capital_settings(self, value: CapitalSettings) -> None:
        self.capital_config = json.dumps(value.to_dict(), ensure_ascii=False)

    @property
    def craft_context_settings(self) -> CraftContextSettings:
        try:
            data = json.loads(self.craft_context_config or "{}")
        except Exception:
            data = {}
        return CraftContextSettings.from_dict(data if isinstance(data, dict) else {})

    def set_craft_context_settings(self, value: CraftContextSettings) -> None:
        self.craft_context_config = json.dumps(value.to_dict(), ensure_ascii=False)


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
    anomaly_baseline_price_rub: float | None = None
    exit_price_rub: float | None = None
    exit_price_model: str = ""
    solo_exit_price_rub: float | None = None
    sweep_exit_price_rub: float | None = None
    market_confidence: str = ""
    liquidity_score: float | None = None
    requires_sweep: bool = False
    solo_requires_sweep: bool = False
    belongs_to_pack: bool = False
    manual_review_required: bool = False
    pack_id: str = ""
    pack_size: int = 0
    pack_cost_rub: float | None = None
    pack_floor_after_rub: float | None = None
    capital_required_rub: float | None = None
    substitute_floor_rub: float | None = None
    substitute_cap_rub: float | None = None
    substitute_sample_size: int | None = None
    substitute_last_scanned_at: str = ""
    substitute_stale: bool | None = None
    raw_anomaly_score: float | None = None
    risk_adjusted_score: float | None = None
    parsed_at: str = ""
    status: str = "new"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class CandidatePack:
    pack_id: str
    item_id: str
    collection_id: str | None
    market_hash_name: str
    display_name: str | None
    listing_ids: list[str]
    pack_size: int
    pack_cost_rub: float
    min_buy_price_rub: float | None = None
    max_buy_price_rub: float | None = None
    min_float: float | None = None
    max_float: float | None = None
    next_floor_after_pack_rub: float | None = None
    gap_percent: float | None = None
    gross_resale_rub: float | None = None
    net_resale_rub: float | None = None
    estimated_profit_rub: float | None = None
    estimated_roi_percent: float | None = None
    capital_required_rub: float = 0.0
    capital_status: str = "ok"
    market_confidence: str = ""
    pack_confidence: str = ""
    requires_sweep: bool = True
    manual_review_required: bool = False
    alert_level: str = "watch"
    sample_size: int = 0
    neighbor_count: int | None = None
    substitute_floor_rub: float | None = None
    substitute_cap_rub: float | None = None
    substitute_sample_size: int | None = None
    substitute_last_scanned_at: str = ""
    substitute_stale: bool | None = None
    reasons: list[str] = field(default_factory=list)
    is_active: bool = True
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class CandidatePackItem:
    pack_id: str
    listing_id: str
    item_id: str
    buy_price_rub: float
    position_in_pack: int
    candidate_id: str | None = None
    wear_rating: float | None = None
    pattern_template: int | None = None
    solo_exit_price_rub: float | None = None
    solo_net_profit_rub: float | None = None
    solo_roi_percent: float | None = None
    solo_alert_level: str = "skip"
    solo_is_actionable: bool = False
    created_at: str = field(default_factory=utc_now_iso)
