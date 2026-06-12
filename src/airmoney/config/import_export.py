from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from airmoney.config.models import (
    AnomalySettings,
    BrowserOptimizationSettings,
    Collection,
    EXTERIORS,
    HistoryOptimizationSettings,
    ItemDefinition,
    ParserSettings,
    ScanOptimizationSettings,
    ScanQueueSettings,
    SnipingRule,
    SteamGuardSettings,
    TelegramAlertSettings,
    to_bool,
    utc_now_iso,
)
from airmoney.storage.repositories import Repository


SUPPORTED_VERSION = 1


@dataclass
class ConfigBundle:
    settings: ParserSettings
    collections: list[Collection]
    items: list[ItemDefinition]
    rules: list[SnipingRule]


@dataclass
class ImportResult:
    valid: bool
    errors: list[str]
    bundle: ConfigBundle | None = None


def load_config_text(text: str) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(text)
    except ImportError:
        data = json.loads(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Корень конфига должен быть объектом.")
    return data


def dump_config(data: dict[str, Any]) -> str:
    try:
        import yaml

        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    except ImportError:
        return json.dumps(data, ensure_ascii=False, indent=2)


def export_config(repo: Repository) -> str:
    settings = repo.get_settings()
    collections = repo.list_collections()
    items = repo.list_items()
    rules = repo.list_rules()
    payload = {
        "version": SUPPORTED_VERSION,
        "parser": {
            "enabled": settings.enabled,
            "check_interval_seconds": settings.check_interval_seconds,
            "headless": settings.headless,
            "max_scrolls": settings.max_scrolls,
            "request_delay_seconds": settings.request_delay_seconds,
            "steam_block_pause_seconds": settings.steam_block_pause_seconds,
            "selected_exteriors": settings.selected_exterior_list,
        },
        "currency": {
            "provider": settings.currency_provider,
            "cache_ttl_seconds": settings.currency_cache_ttl_seconds,
            "fallback_usd_to_rub": settings.fallback_usd_to_rub,
            "fallback_eur_to_rub": settings.fallback_eur_to_rub,
        },
        "profit": {
            "global_roi_percent": settings.default_roi_percent,
            "market_fee_percent": settings.default_market_fee_percent,
            "min_profit_rub": settings.default_min_profit_rub,
            "min_roi_percent": settings.default_min_roi_percent,
        },
        "anomaly": settings.anomaly_settings.to_dict(),
        "scan_queue": settings.scan_queue_settings.to_dict(),
        "browser_optimization": settings.browser_optimization_settings.to_dict(),
        "scan_optimization": settings.scan_optimization_settings.to_dict(),
        "history_optimization": settings.history_optimization_settings.to_dict(),
        "steam_guard": settings.steam_guard_settings.to_dict(),
        "telegram": {
            "enabled": settings.telegram_alerts_enabled,
            "min_alert_level": settings.telegram_min_alert_level,
            **settings.telegram_alert_settings.to_dict(),
        },
        "collections": [
            {
                "id": row["id"],
                "name": row["name"],
                "steam_collection_url": row["steam_collection_url"],
                "enabled": bool(row["enabled"]),
            }
            for row in collections
        ],
        "items": [
            {
                "id": row["id"],
                "collection_id": row["collection_id"],
                "market_hash_name": row["market_hash_name"],
                "display_name": row["display_name"],
                "weapon_type": row["weapon_type"],
                "rarity": row["rarity"],
                "quality": row["quality"],
                "exterior": row["exterior"],
                "is_souvenir": bool(row["is_souvenir"]),
                "is_stattrak": bool(row["is_stattrak"]),
                "enabled": bool(row["enabled"]),
                "steam_market_url": row["steam_market_url"],
            }
            for row in items
        ],
        "rules": [
            {
                "id": row["id"],
                "item_id": row["item_definition_id"],
                "enabled": bool(row["enabled"]),
                "max_buy_price_rub": row["max_buy_price_rub"],
                "target_resale_price_rub": row["target_resale_price_rub"],
                "custom_roi_percent": row["custom_roi_percent"],
                "min_profit_rub": row["min_profit_rub"],
                "min_roi_percent": row["min_roi_percent"],
                "float_min": row["float_min"],
                "float_max": row["float_max"],
                "target_float_min": row["target_float_min"],
                "target_float_max": row["target_float_max"],
                "pattern_ranges": row["pattern_ranges"],
                "priority": row["priority"],
                "telegram_alert_enabled": bool(row["telegram_alert_enabled"]),
                "notes": row["notes"],
            }
            for row in rules
        ],
    }
    return dump_config(payload)


def import_config_text(repo: Repository, text: str, apply: bool = True) -> ImportResult:
    try:
        data = load_config_text(text)
    except Exception as error:
        return ImportResult(False, [f"Не удалось прочитать конфиг: {error}"])

    result = validate_config(data)
    if result.valid and apply and result.bundle:
        repo.replace_config(
            result.bundle.settings,
            result.bundle.collections,
            result.bundle.items,
            result.bundle.rules,
        )
    return result


def import_config_file(repo: Repository, path: str | Path, apply: bool = True) -> ImportResult:
    text = Path(path).read_text(encoding="utf-8")
    return import_config_text(repo, text, apply=apply)


def validate_config(data: dict[str, Any]) -> ImportResult:
    errors: list[str] = []
    if data.get("version") != SUPPORTED_VERSION:
        errors.append(f"version должен быть {SUPPORTED_VERSION}.")

    settings = _parse_settings(data, errors)
    collections = _parse_collections(data.get("collections", []), errors)
    collection_ids = {collection.id for collection in collections}
    items = _parse_items(data.get("items", []), collection_ids, errors)
    item_ids = {item.id for item in items}
    rules = _parse_rules(data.get("rules", []), item_ids, errors)

    _validate_unique([collection.id for collection in collections], "collections.id", errors)
    _validate_unique([item.id for item in items], "items.id", errors)
    _validate_unique([rule.id for rule in rules], "rules.id", errors)

    if errors:
        return ImportResult(False, errors)
    return ImportResult(True, [], ConfigBundle(settings, collections, items, rules))


def _parse_settings(data: dict[str, Any], errors: list[str]) -> ParserSettings:
    parser = _section(data, "parser", errors)
    currency = _section(data, "currency", errors)
    profit = _section(data, "profit", errors)
    telegram = _section(data, "telegram", errors)
    anomaly = _section(data, "anomaly", errors)
    scan_queue = _section(data, "scan_queue", errors)
    browser_optimization = _section(data, "browser_optimization", errors)
    scan_optimization = _section(data, "scan_optimization", errors)
    history_optimization = _section(data, "history_optimization", errors)
    steam_guard = _section(data, "steam_guard", errors)
    settings = ParserSettings()
    settings.enabled = to_bool(parser.get("enabled", settings.enabled))
    settings.check_interval_seconds = _positive_int(parser.get("check_interval_seconds", settings.check_interval_seconds), "parser.check_interval_seconds", errors)
    settings.headless = to_bool(parser.get("headless", settings.headless))
    settings.max_scrolls = _non_negative_int(parser.get("max_scrolls", settings.max_scrolls), "parser.max_scrolls", errors)
    settings.request_delay_seconds = _non_negative_float(parser.get("request_delay_seconds", settings.request_delay_seconds), "parser.request_delay_seconds", errors)
    settings.steam_block_pause_seconds = _non_negative_int(parser.get("steam_block_pause_seconds", settings.steam_block_pause_seconds), "parser.steam_block_pause_seconds", errors)
    selected_exteriors = parser.get("selected_exteriors", settings.selected_exterior_list)
    if not isinstance(selected_exteriors, list):
        errors.append("parser.selected_exteriors должен быть списком.")
        selected_exteriors = settings.selected_exterior_list
    unknown_exteriors = [str(value) for value in selected_exteriors if str(value) not in EXTERIORS]
    if unknown_exteriors:
        errors.append(f"parser.selected_exteriors содержит неизвестные состояния: {', '.join(unknown_exteriors)}.")
    settings.set_selected_exteriors([str(value) for value in selected_exteriors])
    settings.currency_provider = str(currency.get("provider", settings.currency_provider))
    settings.currency_cache_ttl_seconds = _positive_int(currency.get("cache_ttl_seconds", settings.currency_cache_ttl_seconds), "currency.cache_ttl_seconds", errors)
    settings.fallback_usd_to_rub = _positive_float(currency.get("fallback_usd_to_rub", settings.fallback_usd_to_rub), "currency.fallback_usd_to_rub", errors)
    settings.fallback_eur_to_rub = _positive_float(currency.get("fallback_eur_to_rub", settings.fallback_eur_to_rub), "currency.fallback_eur_to_rub", errors)
    settings.default_roi_percent = _non_negative_float(profit.get("global_roi_percent", settings.default_roi_percent), "profit.global_roi_percent", errors)
    settings.default_market_fee_percent = _fee(profit.get("market_fee_percent", settings.default_market_fee_percent), "profit.market_fee_percent", errors)
    settings.default_min_profit_rub = _non_negative_float(profit.get("min_profit_rub", settings.default_min_profit_rub), "profit.min_profit_rub", errors)
    settings.default_min_roi_percent = _non_negative_float(profit.get("min_roi_percent", settings.default_min_roi_percent), "profit.min_roi_percent", errors)
    settings.telegram_alerts_enabled = to_bool(telegram.get("enabled", settings.telegram_alerts_enabled))
    settings.telegram_min_alert_level = str(telegram.get("min_alert_level", settings.telegram_min_alert_level))
    if settings.telegram_min_alert_level not in {"critical", "good", "watch", "skip"}:
        errors.append("telegram.min_alert_level должен быть critical/good/watch/skip.")
    anomaly_settings = _parse_anomaly(anomaly, errors)
    settings.set_anomaly_settings(anomaly_settings)
    settings.set_scan_queue_settings(_parse_scan_queue(scan_queue, errors))
    settings.set_browser_optimization_settings(_parse_browser_optimization(browser_optimization, errors))
    settings.set_scan_optimization_settings(_parse_scan_optimization(scan_optimization, errors))
    settings.set_history_optimization_settings(_parse_history_optimization(history_optimization, errors))
    settings.set_steam_guard_settings(_parse_steam_guard(steam_guard, errors))
    telegram_settings = _parse_telegram_alert_settings(telegram, errors)
    settings.set_telegram_alert_settings(telegram_settings)
    settings.updated_at = utc_now_iso()
    return settings


def _parse_anomaly(data: dict[str, Any], errors: list[str]) -> AnomalySettings:
    settings = AnomalySettings.from_dict(data)
    _validate_anomaly_sample_settings(settings, errors)
    if settings.sample.sort_by not in {"price_asc", "none"}:
        errors.append("anomaly.sample.sort_by должен быть price_asc или none.")
    if settings.sample.target_listings > settings.sample.max_listings:
        errors.append("anomaly.sample.target_listings не должен быть больше max_listings.")
    if settings.sample.min_listings > settings.sample.max_listings:
        errors.append("anomaly.sample.min_listings не должен быть больше max_listings.")
    if settings.thresholds.critical_score < settings.thresholds.good_score:
        errors.append("anomaly.thresholds.critical_score не должен быть меньше good_score.")
    if settings.thresholds.good_score < settings.thresholds.watch_score:
        errors.append("anomaly.thresholds.good_score не должен быть меньше watch_score.")
    seen_buckets: set[str] = set()
    for bucket in settings.float_buckets:
        if bucket.id in seen_buckets:
            errors.append(f"anomaly.float_buckets содержит дубликат id: {bucket.id!r}.")
        seen_buckets.add(bucket.id)
        if bucket.min < 0 or bucket.max < 0:
            errors.append(f"anomaly.float_buckets[{bucket.id}].min/max должны быть неотрицательными.")
        if bucket.min > bucket.max:
            errors.append(f"anomaly.float_buckets[{bucket.id}].min не должен быть больше max.")
    return settings


def _validate_anomaly_sample_settings(settings: AnomalySettings, errors: list[str]) -> None:
    if settings.sample.min_listings < 3:
        errors.append("anomaly.sample.min_listings must be >= 3.")
    if settings.sample.target_listings < settings.sample.min_listings:
        errors.append("anomaly.sample.target_listings must be >= min_listings.")
    if settings.sample.max_listings < settings.sample.target_listings:
        errors.append("anomaly.sample.max_listings must be >= target_listings.")
    if settings.sample.max_listings > 100:
        errors.append("anomaly.sample.max_listings must be <= 100.")


def _parse_scan_queue(data: dict[str, Any], errors: list[str]) -> ScanQueueSettings:
    settings = ScanQueueSettings.from_dict(data)
    if settings.max_items_per_cycle < 1:
        errors.append("scan_queue.max_items_per_cycle must be >= 1.")
    return settings


def _parse_browser_optimization(data: dict[str, Any], errors: list[str]) -> BrowserOptimizationSettings:
    settings = BrowserOptimizationSettings.from_dict(data)
    allowed = {"image", "media", "font", "stylesheet"}
    unknown = [value for value in settings.blocked_resource_types if value not in allowed]
    if unknown:
        errors.append(f"browser_optimization.blocked_resource_types contains unsupported values: {', '.join(unknown)}.")
    return settings


def _parse_scan_optimization(data: dict[str, Any], errors: list[str]) -> ScanOptimizationSettings:
    settings = ScanOptimizationSettings.from_dict(data)
    if settings.shallow_target_listings < 4:
        errors.append("scan_optimization.shallow_target_listings must be >= 4.")
    return settings


def _parse_history_optimization(data: dict[str, Any], errors: list[str]) -> HistoryOptimizationSettings:
    return HistoryOptimizationSettings.from_dict(data)


def _parse_steam_guard(data: dict[str, Any], errors: list[str]) -> SteamGuardSettings:
    settings = SteamGuardSettings.from_dict(data)
    if settings.max_cooldown_seconds < settings.cooldown_on_limit_seconds:
        errors.append("steam_guard.max_cooldown_seconds must be >= cooldown_on_limit_seconds.")
    return settings


def _parse_telegram_alert_settings(data: dict[str, Any], errors: list[str]) -> TelegramAlertSettings:
    settings = TelegramAlertSettings.from_dict(data)
    if settings.message_format != "compact":
        errors.append("telegram.message_format пока поддерживает только compact.")
    return settings


def _section(data: dict[str, Any], name: str, errors: list[str]) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        errors.append(f"{name} должен быть объектом.")
        return {}
    return value


def _parse_collections(raw_collections: Any, errors: list[str]) -> list[Collection]:
    if not isinstance(raw_collections, list):
        errors.append("collections должен быть списком.")
        return []
    result: list[Collection] = []
    for index, row in enumerate(raw_collections, start=1):
        if not isinstance(row, dict):
            errors.append(f"collections[{index}] должен быть объектом.")
            continue
        collection_id = _required_text(row, "id", f"collections[{index}]", errors)
        name = _required_text(row, "name", f"collections[{index}]", errors)
        url = str(row.get("steam_collection_url", "") or "")
        if url and not _looks_like_url(url):
            errors.append(f"collections[{index}].steam_collection_url некорректен.")
        if collection_id and name:
            result.append(
                Collection(
                    id=collection_id,
                    name=name,
                    steam_collection_url=url,
                    enabled=to_bool(row.get("enabled", True)),
                )
            )
    return result


def _parse_items(raw_items: Any, collection_ids: set[str], errors: list[str]) -> list[ItemDefinition]:
    if not isinstance(raw_items, list):
        errors.append("items должен быть списком.")
        return []
    result: list[ItemDefinition] = []
    for index, row in enumerate(raw_items, start=1):
        if not isinstance(row, dict):
            errors.append(f"items[{index}] должен быть объектом.")
            continue
        item_id = _required_text(row, "id", f"items[{index}]", errors)
        collection_id = _required_text(row, "collection_id", f"items[{index}]", errors)
        market_hash_name = _required_text(row, "market_hash_name", f"items[{index}]", errors)
        if collection_id and collection_id not in collection_ids:
            errors.append(f"items[{index}].collection_id ссылается на неизвестную коллекцию {collection_id!r}.")
        url = str(row.get("steam_market_url", "") or "")
        if url and not _looks_like_url(url):
            errors.append(f"items[{index}].steam_market_url некорректен.")
        if item_id and collection_id and market_hash_name:
            result.append(
                ItemDefinition(
                    id=item_id,
                    collection_id=collection_id,
                    market_hash_name=market_hash_name,
                    display_name=str(row.get("display_name", "") or market_hash_name),
                    weapon_type=str(row.get("weapon_type", "") or ""),
                    rarity=str(row.get("rarity", "") or ""),
                    quality=str(row.get("quality", "") or ""),
                    exterior=str(row.get("exterior", "") or ""),
                    is_souvenir=to_bool(row.get("is_souvenir", False)),
                    is_stattrak=to_bool(row.get("is_stattrak", False)),
                    steam_market_url=url,
                    enabled=to_bool(row.get("enabled", True)),
                )
            )
    return result


def _parse_rules(raw_rules: Any, item_ids: set[str], errors: list[str]) -> list[SnipingRule]:
    if not isinstance(raw_rules, list):
        errors.append("rules должен быть списком.")
        return []
    result: list[SnipingRule] = []
    for index, row in enumerate(raw_rules, start=1):
        if not isinstance(row, dict):
            errors.append(f"rules[{index}] должен быть объектом.")
            continue
        rule_id = _required_text(row, "id", f"rules[{index}]", errors)
        item_id = _required_text(row, "item_id", f"rules[{index}]", errors)
        if item_id and item_id not in item_ids:
            errors.append(f"rules[{index}].item_id ссылается на неизвестный предмет {item_id!r}.")
        custom_roi = _optional_non_negative_float(row.get("custom_roi_percent"), f"rules[{index}].custom_roi_percent", errors)
        min_roi = _optional_non_negative_float(row.get("min_roi_percent"), f"rules[{index}].min_roi_percent", errors)
        float_min = _optional_non_negative_float(row.get("float_min"), f"rules[{index}].float_min", errors)
        float_max = _optional_non_negative_float(row.get("float_max"), f"rules[{index}].float_max", errors)
        target_float_min = _optional_non_negative_float(row.get("target_float_min"), f"rules[{index}].target_float_min", errors)
        target_float_max = _optional_non_negative_float(row.get("target_float_max"), f"rules[{index}].target_float_max", errors)
        _validate_range_order(float_min, float_max, f"rules[{index}].float", errors)
        _validate_range_order(target_float_min, target_float_max, f"rules[{index}].target_float", errors)
        if rule_id and item_id:
            result.append(
                SnipingRule(
                    id=rule_id,
                    item_definition_id=item_id,
                    enabled=to_bool(row.get("enabled", True)),
                    max_buy_price_rub=_optional_positive_float(row.get("max_buy_price_rub"), f"rules[{index}].max_buy_price_rub", errors),
                    target_resale_price_rub=_optional_positive_float(row.get("target_resale_price_rub"), f"rules[{index}].target_resale_price_rub", errors),
                    custom_roi_percent=custom_roi,
                    min_profit_rub=_optional_non_negative_float(row.get("min_profit_rub"), f"rules[{index}].min_profit_rub", errors),
                    min_roi_percent=min_roi,
                    float_min=float_min,
                    float_max=float_max,
                    target_float_min=target_float_min,
                    target_float_max=target_float_max,
                    pattern_ranges=_validate_pattern_ranges(str(row.get("pattern_ranges", "") or ""), f"rules[{index}].pattern_ranges", errors),
                    priority=_int(row.get("priority", 0), f"rules[{index}].priority", errors),
                    telegram_alert_enabled=to_bool(row.get("telegram_alert_enabled", True)),
                    notes=str(row.get("notes", "") or ""),
                )
            )
    return result


def _required_text(row: dict[str, Any], key: str, prefix: str, errors: list[str]) -> str:
    value = str(row.get(key, "") or "").strip()
    if not value:
        errors.append(f"{prefix}.{key} обязателен.")
    return value


def _validate_unique(values: list[str], label: str, errors: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            errors.append(f"{label} должен быть уникальным: {value!r}.")
        seen.add(value)


def _looks_like_url(value: str) -> bool:
    return value.startswith("https://") or value.startswith("http://")


def _int(value: Any, label: str, errors: list[str]) -> int:
    try:
        return int(value)
    except Exception:
        errors.append(f"{label} должен быть целым числом.")
        return 0


def _positive_int(value: Any, label: str, errors: list[str]) -> int:
    result = _int(value, label, errors)
    if result <= 0:
        errors.append(f"{label} должен быть больше 0.")
    return max(result, 1)


def _non_negative_int(value: Any, label: str, errors: list[str]) -> int:
    result = _int(value, label, errors)
    if result < 0:
        errors.append(f"{label} должен быть не меньше 0.")
    return max(result, 0)


def _float(value: Any, label: str, errors: list[str]) -> float:
    try:
        return float(value)
    except Exception:
        errors.append(f"{label} должен быть числом.")
        return 0.0


def _positive_float(value: Any, label: str, errors: list[str]) -> float:
    result = _float(value, label, errors)
    if result <= 0:
        errors.append(f"{label} должен быть больше 0.")
    return max(result, 0.01)


def _non_negative_float(value: Any, label: str, errors: list[str]) -> float:
    result = _float(value, label, errors)
    if result < 0:
        errors.append(f"{label} должен быть не меньше 0.")
    return max(result, 0.0)


def _fee(value: Any, label: str, errors: list[str]) -> float:
    result = _non_negative_float(value, label, errors)
    if result >= 100:
        errors.append(f"{label} должен быть меньше 100.")
    return min(result, 99.0)


def _optional_positive_float(value: Any, label: str, errors: list[str]) -> float | None:
    if value is None or value == "":
        return None
    return _positive_float(value, label, errors)


def _optional_non_negative_float(value: Any, label: str, errors: list[str]) -> float | None:
    if value is None or value == "":
        return None
    return _non_negative_float(value, label, errors)


def _validate_pattern_ranges(value: str, label: str, errors: list[str]) -> str:
    if not value.strip():
        return ""
    parts = value.replace("–", "-").replace("—", "-").split(";")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not re.fullmatch(r"\d+(?:\s*-\s*\d+)?", part):
            errors.append(f"{label} содержит неверный диапазон: {part!r}.")
            break
    return value


def _validate_range_order(left: float | None, right: float | None, label: str, errors: list[str]) -> None:
    if left is not None and right is not None and left > right:
        errors.append(f"{label}_min не должен быть больше {label}_max.")
