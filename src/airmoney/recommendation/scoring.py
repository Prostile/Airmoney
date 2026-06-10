from __future__ import annotations


ALERT_LEVEL_ORDER = {
    "critical": 3,
    "good": 2,
    "watch": 1,
    "skip": 0,
}


def recommendation_score(
    profit_rub: float,
    roi_percent: float,
    min_profit_rub: float,
    min_roi_percent: float,
) -> float:
    profit_part = profit_rub / min_profit_rub if min_profit_rub > 0 else 0
    roi_part = roi_percent / min_roi_percent if min_roi_percent > 0 else 0
    return round(max(0.0, profit_part) * 50 + max(0.0, roi_part) * 50, 2)


def recommendation_level(
    profit_rub: float,
    roi_percent: float,
    min_profit_rub: float,
    min_roi_percent: float,
) -> str:
    passes_profit = profit_rub >= min_profit_rub
    passes_roi = roi_percent >= min_roi_percent

    if passes_profit and passes_roi:
        if profit_rub >= min_profit_rub * 2 and roi_percent >= min_roi_percent * 1.75:
            return "critical"
        return "good"

    if profit_rub > 0 or roi_percent > 0:
        return "watch"

    return "skip"


def should_alert(level: str, min_alert_level: str = "good") -> bool:
    if level not in {"critical", "good"}:
        return False
    return ALERT_LEVEL_ORDER.get(level, 0) >= ALERT_LEVEL_ORDER.get(min_alert_level, 2)
