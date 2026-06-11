from __future__ import annotations

from typing import Any

from airmoney.config.models import AnomalyScoringSettings, AnomalyThresholdSettings


def normalize_discount(discount_percent: float | None, cap: float = 40.0) -> float:
    if discount_percent is None or discount_percent <= 0:
        return 0.0
    return min(discount_percent / cap * 100, 100.0)


def calculate_float_quality_score(wear: float | None) -> float:
    if wear is None:
        return 0.0
    if wear <= 0.005:
        return 100.0
    if wear <= 0.010:
        return 90.0
    if wear <= 0.015:
        return 78.0
    if wear <= 0.030:
        return 62.0
    if wear <= 0.070:
        return 42.0
    return 10.0


def calculate_anomaly_score(
    local_discount_percent: float | None,
    float_peer_discount_percent: float | None,
    historical_discount_percent: float | None,
    wear_rating: float | None,
    weights: AnomalyScoringSettings,
) -> float:
    local_score = normalize_discount(local_discount_percent)
    float_peer_score = normalize_discount(float_peer_discount_percent)
    history_score = normalize_discount(historical_discount_percent)
    float_score = calculate_float_quality_score(wear_rating)
    score = (
        weights.local_discount_weight * local_score
        + weights.float_peer_discount_weight * float_peer_score
        + weights.historical_discount_weight * history_score
        + weights.float_quality_weight * float_score
    )
    return round(max(0.0, min(score, 100.0)), 2)


def resolve_alert_level(
    score: float,
    net_profit: float | None,
    roi: float | None,
    thresholds: AnomalyThresholdSettings,
) -> str:
    if net_profit is None or net_profit < thresholds.min_net_profit_rub:
        return "skip"
    if roi is None or roi < thresholds.min_roi_percent:
        return "skip"
    if score >= thresholds.critical_score:
        return "critical"
    if score >= thresholds.good_score:
        return "good"
    if score >= thresholds.watch_score:
        return "watch"
    return "skip"


def estimate_fair_price(
    local_median: float | None,
    float_peer_median: float | None,
    historical_baseline: float | None,
    rule: dict[str, Any] | None,
    weights: AnomalyScoringSettings,
) -> float | None:
    target = _optional_float(rule, "target_resale_price_rub")
    if target is not None:
        return round(target, 2)

    weighted_prices: list[tuple[float, float]] = []
    if local_median is not None:
        weighted_prices.append((local_median, weights.local_discount_weight))
    if float_peer_median is not None:
        weighted_prices.append((float_peer_median, weights.float_peer_discount_weight))
    if historical_baseline is not None:
        weighted_prices.append((historical_baseline, weights.historical_discount_weight))
    if not weighted_prices:
        return None

    total_weight = sum(weight for _, weight in weighted_prices)
    if total_weight <= 0:
        return None
    return round(sum(price * weight for price, weight in weighted_prices) / total_weight, 2)


def calculate_real_profit(
    buy_price_rub: float,
    fair_price_rub: float | None,
    market_fee_percent: float,
) -> tuple[float | None, float | None, float | None]:
    if fair_price_rub is None or buy_price_rub <= 0:
        return None, None, None
    fee_multiplier = 1 - market_fee_percent / 100
    if fee_multiplier <= 0:
        return None, None, None
    net_resale = fair_price_rub * fee_multiplier
    profit = net_resale - buy_price_rub
    roi = profit / buy_price_rub * 100 if buy_price_rub > 0 else 0.0
    return round(net_resale, 2), round(profit, 2), round(roi, 2)


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
