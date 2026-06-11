from __future__ import annotations

from dataclasses import asdict
from typing import Any

from airmoney.anomaly.baselines import (
    assign_float_bucket,
    calculate_bucket_baseline,
    calculate_float_peer_baseline,
    calculate_local_baseline,
)
from airmoney.anomaly.models import AnomalyResult, ParsedListing
from airmoney.anomaly.scoring import (
    calculate_anomaly_score,
    calculate_real_profit,
    estimate_fair_price,
    resolve_alert_level,
)
from airmoney.config.models import AnomalySettings, ParserSettings
from airmoney.steam.extractor import value_in_ranges


def analyze_listings(
    listings: list[ParsedListing],
    item: dict[str, Any],
    rule: dict[str, Any] | None,
    settings: ParserSettings,
    historical_baselines: dict[str, float] | None = None,
) -> list[AnomalyResult]:
    anomaly_settings = settings.anomaly_settings
    if not listings:
        return []
    return [
        analyze_listing(
            candidate,
            listings,
            item,
            rule,
            settings,
            anomaly_settings,
            historical_baselines=historical_baselines,
        )
        for candidate in listings
    ]


def analyze_listing(
    candidate: ParsedListing,
    listings: list[ParsedListing],
    item: dict[str, Any],
    rule: dict[str, Any] | None,
    settings: ParserSettings,
    anomaly_settings: AnomalySettings | None = None,
    historical_baselines: dict[str, float] | None = None,
) -> AnomalyResult:
    anomaly_settings = anomaly_settings or settings.anomaly_settings
    reasons: list[str] = []
    hard_skip_reasons = rule_filter_reasons(candidate, item, rule)

    sample_min = max(5, anomaly_settings.sample.min_listings)
    local = calculate_local_baseline(
        candidate,
        listings,
        min_samples=sample_min,
        exclude_candidate=anomaly_settings.sample.exclude_candidate_from_baseline,
    )
    bucket = assign_float_bucket(candidate.wear_rating, anomaly_settings.float_buckets)
    bucket_baseline = calculate_bucket_baseline(
        candidate,
        listings,
        anomaly_settings.float_buckets,
        exclude_candidate=anomaly_settings.sample.exclude_candidate_from_baseline,
    )
    peer = calculate_float_peer_baseline(
        candidate,
        listings,
        k=anomaly_settings.nearest_neighbors.k,
        min_neighbors=anomaly_settings.nearest_neighbors.min_neighbors,
        max_float_distance=anomaly_settings.nearest_neighbors.max_float_distance,
    ) if anomaly_settings.nearest_neighbors.enabled else bucket_baseline

    float_peer_median = peer.median or bucket_baseline.median
    float_peer_discount = peer.discount_percent if peer.discount_percent is not None else bucket_baseline.discount_percent
    neighbor_count = peer.neighbor_count or bucket_baseline.sample_size
    historical_baselines = historical_baselines or {}
    historical_baseline = historical_baselines.get(bucket)
    historical_discount = (
        round((1 - candidate.price_rub / historical_baseline) * 100, 2)
        if historical_baseline and historical_baseline > 0
        else None
    )

    fair_price = estimate_fair_price(
        local.median,
        float_peer_median,
        historical_baseline,
        rule,
        anomaly_settings.scoring,
    )
    net_resale, net_profit, roi = calculate_real_profit(
        candidate.price_rub,
        fair_price,
        settings.default_market_fee_percent,
    )
    score = calculate_anomaly_score(
        local.discount_percent,
        float_peer_discount,
        historical_discount,
        candidate.wear_rating,
        anomaly_settings.scoring,
    )
    target_bonus = _target_float_bonus(candidate, rule)
    if target_bonus > 0:
        score = min(100.0, round(score + target_bonus, 2))
        reasons.append("float входит в целевой диапазон правила")
    elif _has_target_float(rule):
        reasons.append("float не входит в целевой диапазон правила")

    priority_boost = _priority_boost(rule)
    if priority_boost > 0:
        score = min(100.0, round(score + priority_boost, 2))
        reasons.append(f"priority повышает score на {priority_boost:g}")

    level = resolve_alert_level(score, net_profit, roi, anomaly_settings.thresholds)

    if hard_skip_reasons:
        level = "skip"
        reasons.extend(hard_skip_reasons)
    elif len(listings) < anomaly_settings.sample.min_listings:
        level = "watch" if fair_price is not None and (net_profit or 0) > 0 else "skip"
        reasons.append(f"маленькая выборка: {len(listings)} из {anomaly_settings.sample.min_listings}")

    if local.discount_percent is not None:
        reasons.append(f"цена на {local.discount_percent:.1f}% ниже медианы первых {local.sample_size} лотов")
    if candidate.wear_rating is not None:
        reasons.append(f"float {candidate.wear_rating:.4f} попадает в {bucket} bucket")
    if float_peer_discount is not None:
        reasons.append(f"цена на {float_peer_discount:.1f}% ниже медианы похожих float-лотов")
    if historical_discount is not None:
        reasons.append(f"цена на {historical_discount:.1f}% ниже исторического baseline")
    if net_profit is not None:
        reasons.append(f"ожидаемая прибыль после комиссии: {net_profit:.1f} ₽")
    if roi is not None:
        reasons.append(f"ROI после комиссии: {roi:.1f}%")
    notes = str(rule.get("notes", "") if rule else "").strip()
    if notes:
        reasons.append(f"заметка: {notes}")

    passes_discount = (
        (local.discount_percent is not None and local.discount_percent >= anomaly_settings.thresholds.min_local_discount_percent)
        or (
            float_peer_discount is not None
            and float_peer_discount >= anomaly_settings.thresholds.min_float_peer_discount_percent
        )
    )
    if not hard_skip_reasons and not passes_discount and level != "skip":
        level = "watch"
        reasons.append("скидка ниже порога сильного anomaly-сигнала")

    if not reasons:
        reasons.append("недостаточно данных для anomaly-оценки")

    return AnomalyResult(
        listing=candidate,
        fair_price_rub=fair_price,
        local_median_rub=local.median,
        float_peer_median_rub=float_peer_median,
        historical_baseline_rub=historical_baseline,
        local_discount_percent=local.discount_percent,
        float_peer_discount_percent=float_peer_discount,
        historical_discount_percent=historical_discount,
        net_resale_rub=net_resale,
        net_profit_rub=net_profit,
        roi_percent=roi,
        anomaly_score=score,
        alert_level=level,
        reasons=reasons,
        robust_z=local.robust_z,
        float_bucket=bucket,
        sample_size=len(listings),
        neighbor_count=neighbor_count,
    )


def rule_filter_reasons(
    listing: ParsedListing,
    item: dict[str, Any],
    rule: dict[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    if not bool(item.get("enabled", True)):
        reasons.append("предмет выключен")
    if rule and not bool(rule.get("enabled", True)):
        reasons.append("правило выключено")
    max_buy = _optional_float(rule, "max_buy_price_rub")
    if max_buy is not None and listing.price_rub > max_buy:
        reasons.append(f"цена выше ручного лимита {max_buy:g} ₽")
    float_min = _optional_float(rule, "float_min")
    float_max = _optional_float(rule, "float_max")
    if float_min is not None and (listing.wear_rating is None or listing.wear_rating < float_min):
        reasons.append(f"float ниже {float_min:g}")
    if float_max is not None and (listing.wear_rating is None or listing.wear_rating > float_max):
        reasons.append(f"float выше {float_max:g}")
    pattern_ranges = str(rule.get("pattern_ranges", "") if rule else "")
    if pattern_ranges and not value_in_ranges(listing.pattern_template, pattern_ranges):
        reasons.append("pattern вне правила")
    if listing.price_rub <= 0:
        reasons.append("цена не распознана")
    return reasons


def _optional_float(row: dict[str, Any] | None, key: str) -> float | None:
    if not row:
        return None
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _optional_int(row: dict[str, Any] | None, key: str) -> int | None:
    if not row:
        return None
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _has_target_float(rule: dict[str, Any] | None) -> bool:
    return _optional_float(rule, "target_float_min") is not None or _optional_float(rule, "target_float_max") is not None


def _target_float_bonus(candidate: ParsedListing, rule: dict[str, Any] | None) -> float:
    if candidate.wear_rating is None or not _has_target_float(rule):
        return 0.0
    left = _optional_float(rule, "target_float_min")
    right = _optional_float(rule, "target_float_max")
    min_value = left if left is not None else float("-inf")
    max_value = right if right is not None else float("inf")
    return 8.0 if min_value <= candidate.wear_rating <= max_value else 0.0


def _priority_boost(rule: dict[str, Any] | None) -> float:
    priority = _optional_int(rule, "priority")
    if priority is None or priority <= 0:
        return 0.0
    return float(min(priority, 20))


def anomaly_result_debug(result: AnomalyResult) -> dict[str, Any]:
    data = asdict(result)
    data["listing"] = asdict(result.listing)
    return data
