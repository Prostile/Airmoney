from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from airmoney.anomaly.baselines import (
    assign_float_bucket,
    calculate_bucket_baseline,
    calculate_float_peer_baseline,
    calculate_local_baseline,
)
from airmoney.anomaly.exit_risk import (
    SubstituteContext,
    apply_capital_caps,
    apply_market_risk_caps,
    calculate_market_confidence,
    estimate_exit_prices,
)
from airmoney.anomaly.matching import is_exact_item_match
from airmoney.anomaly.models import AnomalyResult, ParsedListing
from airmoney.anomaly.scoring import (
    calculate_anomaly_score,
    calculate_real_profit,
    estimate_fair_price,
    resolve_alert_level,
)
from airmoney.config.models import AnomalySettings, AnomalyThresholdSettings, ParserSettings
from airmoney.steam.extractor import value_in_ranges


def analyze_listings(
    listings: list[ParsedListing],
    item: dict[str, Any],
    rule: dict[str, Any] | None,
    settings: ParserSettings,
    historical_baselines: dict[str, float] | None = None,
    substitute_context: SubstituteContext | None = None,
) -> list[AnomalyResult]:
    anomaly_settings = settings.anomaly_settings
    if not listings:
        return []
    sorted_listings = sorted(listings, key=lambda listing: (listing.price_rub, listing.listing_id))
    return [
        analyze_listing(
            candidate,
            sorted_listings,
            item,
            rule,
            settings,
            anomaly_settings,
            historical_baselines=historical_baselines,
            substitute_context=substitute_context,
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
    substitute_context: SubstituteContext | None = None,
) -> AnomalyResult:
    anomaly_settings = anomaly_settings or settings.anomaly_settings
    reasons: list[str] = []
    hard_skip_reasons = rule_filter_reasons(candidate, item, rule)
    thresholds = _thresholds_for_rule(anomaly_settings, rule)
    exact_item_match = is_exact_item_match(
        candidate,
        str(item.get("market_hash_name") or candidate.expected_market_hash_name),
    )
    if anomaly_settings.sample.require_exact_item_match and not exact_item_match:
        hard_skip_reasons.append("exact item mismatch")

    sample_min = _baseline_min_samples(anomaly_settings)
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
    peer = (
        calculate_float_peer_baseline(
            candidate,
            listings,
            k=anomaly_settings.nearest_neighbors.k,
            min_neighbors=anomaly_settings.nearest_neighbors.min_neighbors,
            max_float_distance=anomaly_settings.nearest_neighbors.max_float_distance,
        )
        if anomaly_settings.nearest_neighbors.enabled
        else bucket_baseline
    )

    float_peer_median = peer.median or bucket_baseline.median
    float_peer_discount = (
        peer.discount_percent
        if peer.discount_percent is not None
        else bucket_baseline.discount_percent
    )
    neighbor_count = peer.neighbor_count or bucket_baseline.sample_size
    historical_baselines = historical_baselines or {}
    historical_baseline = historical_baselines.get(bucket)
    historical_discount = (
        round((1 - candidate.price_rub / historical_baseline) * 100, 2)
        if historical_baseline and historical_baseline > 0
        else None
    )

    anomaly_baseline_price = estimate_fair_price(
        local.median,
        float_peer_median,
        historical_baseline,
        rule,
        anomaly_settings.scoring,
    )
    raw_score = calculate_anomaly_score(
        local.discount_percent,
        float_peer_discount,
        historical_discount,
        candidate.wear_rating,
        anomaly_settings.scoring,
    )
    risk_adjusted_score = raw_score
    target_bonus = _target_float_bonus(candidate, rule)
    if target_bonus > 0:
        risk_adjusted_score = min(100.0, round(risk_adjusted_score + target_bonus, 2))
        reasons.append("float is inside target float range")
    elif _has_target_float(rule):
        reasons.append("float is outside target float range")

    exit_result = estimate_exit_prices(
        candidate,
        listings,
        anomaly_baseline_price,
        settings.market_risk_settings,
        settings.pack_detection_settings,
        settings.capital_settings,
        settings.default_market_fee_percent,
        target_resale_price_cap=_optional_float(rule, "target_resale_price_rub"),
        substitute_context=substitute_context,
    )
    net_resale, net_profit, roi = calculate_real_profit(
        candidate.price_rub,
        exit_result.conservative_exit_price_rub,
        settings.default_market_fee_percent,
    )

    level = resolve_alert_level(risk_adjusted_score, net_profit, roi, thresholds)
    market_confidence = calculate_market_confidence(
        len(listings),
        neighbor_count,
        exit_result.pack_size / len(listings) if exit_result.pack_size and listings else None,
        settings.market_risk_settings,
    )
    uncapped_level = level
    level = apply_market_risk_caps(
        level,
        market_confidence,
        exit_result.requires_sweep,
        settings.market_risk_settings,
    )
    level = apply_capital_caps(level, exit_result, settings.capital_settings)
    if level != uncapped_level:
        reasons.append(f"risk cap lowered alert level: {uncapped_level} -> {level}")

    if hard_skip_reasons:
        level = "skip"
        reasons.extend(hard_skip_reasons)
    elif len(listings) < anomaly_settings.sample.min_listings:
        level = "watch" if exit_result.conservative_exit_price_rub is not None and (net_profit or 0) > 0 else "skip"
        reasons.append(f"small sample: {len(listings)} of {anomaly_settings.sample.min_listings}")

    if local.discount_percent is not None:
        reasons.append(f"price is {local.discount_percent:.1f}% below local median from {local.sample_size} listings")
    if candidate.wear_rating is not None:
        reasons.append(f"float {candidate.wear_rating:.4f} is in {bucket} bucket")
    if float_peer_discount is not None:
        reasons.append(f"price is {float_peer_discount:.1f}% below float-peer median")
    if historical_discount is not None:
        reasons.append(f"price is {historical_discount:.1f}% below historical baseline")
    if anomaly_baseline_price is not None:
        reasons.append(f"anomaly baseline price: {anomaly_baseline_price:.1f} RUB")
    if exit_result.conservative_exit_price_rub is not None:
        reasons.append(f"conservative exit price: {exit_result.conservative_exit_price_rub:.1f} RUB")
    if market_confidence:
        reasons.append(f"market confidence: {market_confidence}")
    reasons.extend(exit_result.reasons)
    if net_profit is not None:
        reasons.append(f"expected profit after fee by exit price: {net_profit:.1f} RUB")
    if roi is not None:
        reasons.append(f"ROI after fee by exit price: {roi:.1f}%")
    notes = str(rule.get("notes", "") if rule else "").strip()
    if notes:
        reasons.append(f"note: {notes}")

    passes_discount = (
        (local.discount_percent is not None and local.discount_percent >= thresholds.min_local_discount_percent)
        or (
            float_peer_discount is not None
            and float_peer_discount >= thresholds.min_float_peer_discount_percent
        )
    )
    if not hard_skip_reasons and not passes_discount and level != "skip":
        level = "watch"
        reasons.append("discount is below strong anomaly threshold")

    if not reasons:
        reasons.append("insufficient data for anomaly analysis")

    return AnomalyResult(
        listing=candidate,
        fair_price_rub=anomaly_baseline_price,
        exit_price_rub=exit_result.conservative_exit_price_rub,
        anomaly_baseline_price_rub=anomaly_baseline_price,
        local_median_rub=local.median,
        float_peer_median_rub=float_peer_median,
        historical_baseline_rub=historical_baseline,
        local_discount_percent=local.discount_percent,
        float_peer_discount_percent=float_peer_discount,
        historical_discount_percent=historical_discount,
        net_resale_rub=net_resale,
        net_profit_rub=net_profit,
        roi_percent=roi,
        raw_anomaly_score=raw_score,
        risk_adjusted_score=risk_adjusted_score,
        anomaly_score=risk_adjusted_score,
        alert_level=level,
        market_confidence=market_confidence,
        requires_sweep=exit_result.requires_sweep,
        pack_id=exit_result.pack_id,
        pack_size=exit_result.pack_size,
        pack_cost_rub=exit_result.pack_cost_rub,
        pack_floor_after_rub=exit_result.pack_floor_after_rub,
        capital_required_rub=exit_result.capital_required_rub,
        substitute_floor_rub=exit_result.substitute_floor_rub,
        substitute_cap_rub=exit_result.substitute_cap_rub,
        solo_exit_price_rub=exit_result.solo_exit_price_rub,
        sweep_exit_price_rub=exit_result.sweep_exit_price_rub,
        exit_price_model=exit_result.exit_price_model,
        liquidity_score=exit_result.liquidity_score,
        manual_review_required=exit_result.manual_review_required,
        reasons=reasons,
        robust_z=local.robust_z,
        float_bucket=bucket,
        exact_item_match=exact_item_match,
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
        reasons.append("item disabled")
    if rule and not bool(rule.get("enabled", True)):
        reasons.append("rule disabled")
    max_buy = _optional_float(rule, "max_buy_price_rub")
    if max_buy is not None and listing.price_rub > max_buy:
        reasons.append(f"price above rule max buy {max_buy:g} RUB")
    float_min = _optional_float(rule, "float_min")
    float_max = _optional_float(rule, "float_max")
    if float_min is not None and (listing.wear_rating is None or listing.wear_rating < float_min):
        reasons.append(f"float below {float_min:g}")
    if float_max is not None and (listing.wear_rating is None or listing.wear_rating > float_max):
        reasons.append(f"float above {float_max:g}")
    pattern_ranges = str(rule.get("pattern_ranges", "") if rule else "")
    if pattern_ranges and not value_in_ranges(listing.pattern_template, pattern_ranges):
        reasons.append("pattern outside rule")
    if listing.price_rub <= 0:
        reasons.append("price not parsed")
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


def _baseline_min_samples(anomaly_settings: AnomalySettings) -> int:
    if anomaly_settings.sample.exclude_candidate_from_baseline:
        return max(5, anomaly_settings.sample.min_listings - 1)
    return max(5, anomaly_settings.sample.min_listings)


def _thresholds_for_rule(
    anomaly_settings: AnomalySettings,
    rule: dict[str, Any] | None,
) -> AnomalyThresholdSettings:
    thresholds = anomaly_settings.thresholds
    min_profit = _optional_float(rule, "min_profit_rub")
    min_roi = _optional_float(rule, "min_roi_percent")
    if min_profit is None and min_roi is None:
        return thresholds
    return replace(
        thresholds,
        min_net_profit_rub=min_profit if min_profit is not None else thresholds.min_net_profit_rub,
        min_roi_percent=min_roi if min_roi is not None else thresholds.min_roi_percent,
    )


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


def anomaly_result_debug(result: AnomalyResult) -> dict[str, Any]:
    data = asdict(result)
    data["listing"] = asdict(result.listing)
    return data
