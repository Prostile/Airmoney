from __future__ import annotations

from typing import Any

from airmoney.config.models import Candidate, ParserSettings
from airmoney.recommendation.profit import calculate_profit, choose_roi_percent
from airmoney.recommendation.scoring import recommendation_level, recommendation_score
from airmoney.steam.extractor import value_in_ranges


def _optional_float(row: dict[str, Any] | None, key: str) -> float | None:
    if not row:
        return None
    value = row.get(key)
    if value is None or value == "":
        return None
    return float(value)


def _optional_bool(row: dict[str, Any] | None, key: str, default: bool = True) -> bool:
    if not row or row.get(key) is None:
        return default
    return bool(row.get(key))


def evaluate_listing(
    listing_id: str,
    buy_price_rub: float,
    float_value: float | None,
    pattern: int | None,
    rule: dict[str, Any] | None,
    settings: ParserSettings,
) -> Candidate:
    reasons: list[str] = []
    hard_skip = False

    rule_id = rule.get("id") if rule else None
    if rule and not _optional_bool(rule, "enabled"):
        hard_skip = True
        reasons.append("правило выключено")

    max_buy = _optional_float(rule, "max_buy_price_rub")
    if max_buy is not None and buy_price_rub > max_buy:
        hard_skip = True
        reasons.append(f"цена выше лимита {max_buy:g} ₽")

    float_min = _optional_float(rule, "float_min")
    float_max = _optional_float(rule, "float_max")
    if float_min is not None and (float_value is None or float_value < float_min):
        hard_skip = True
        reasons.append(f"float ниже {float_min:g}")
    if float_max is not None and (float_value is None or float_value > float_max):
        hard_skip = True
        reasons.append(f"float выше {float_max:g}")

    pattern_ranges = str(rule.get("pattern_ranges", "") if rule else "")
    if pattern_ranges and not value_in_ranges(pattern, pattern_ranges):
        hard_skip = True
        reasons.append("pattern вне правила")

    roi_percent = choose_roi_percent(
        _optional_float(rule, "custom_roi_percent"),
        settings.default_roi_percent,
    )
    min_profit = _optional_float(rule, "min_profit_rub")
    if min_profit is None:
        min_profit = settings.default_min_profit_rub
    min_roi = _optional_float(rule, "min_roi_percent")
    if min_roi is None:
        min_roi = settings.default_min_roi_percent

    estimate = calculate_profit(
        buy_price_rub=buy_price_rub,
        roi_percent=roi_percent,
        market_fee_percent=settings.default_market_fee_percent,
        target_resale_price_rub=_optional_float(rule, "target_resale_price_rub"),
    )
    level = "skip" if hard_skip else recommendation_level(
        estimate.profit,
        estimate.roi_percent,
        min_profit,
        min_roi,
    )
    score = recommendation_score(estimate.profit, estimate.roi_percent, min_profit, min_roi)

    if not reasons:
        if level in {"critical", "good"}:
            reasons.append("проходит минимальную прибыль и ROI")
        elif level == "watch":
            reasons.append("есть потенциал, но ниже минимальных условий")
        else:
            reasons.append("прибыль или ROI не проходят фильтр")

    return Candidate(
        id=f"cand_{listing_id.replace('listing_', '')}",
        listing_id=listing_id,
        rule_id=rule_id,
        buy_price_rub=round(float(buy_price_rub), 2),
        estimated_resale_price_rub=estimate.target_resale_price,
        estimated_net_resale_rub=estimate.net_resale_price,
        estimated_profit_rub=estimate.profit,
        estimated_roi_percent=estimate.roi_percent,
        market_fee_percent=estimate.market_fee_percent,
        recommendation_level=level,
        recommendation_score=score,
        recommendation_reason="; ".join(reasons),
    )
