from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProfitEstimate:
    target_resale_price: float
    net_resale_price: float
    profit: float
    roi_percent: float
    roi_percent_used: float
    market_fee_percent: float


def choose_roi_percent(custom_roi_percent: float | None, global_roi_percent: float) -> float:
    if custom_roi_percent is not None:
        return float(custom_roi_percent)
    return float(global_roi_percent)


def calculate_profit(
    buy_price_rub: float,
    roi_percent: float,
    market_fee_percent: float,
    target_resale_price_rub: float | None = None,
) -> ProfitEstimate:
    if buy_price_rub <= 0:
        raise ValueError("Цена покупки должна быть больше 0.")
    if market_fee_percent < 0 or market_fee_percent >= 100:
        raise ValueError("Комиссия площадки должна быть от 0 до 100%.")

    if target_resale_price_rub is not None:
        target_resale_price = float(target_resale_price_rub)
    else:
        target_resale_price = float(buy_price_rub) * (1 + float(roi_percent) / 100)

    net_resale_price = target_resale_price * (1 - float(market_fee_percent) / 100)
    profit = net_resale_price - float(buy_price_rub)
    roi = profit / float(buy_price_rub) * 100

    return ProfitEstimate(
        target_resale_price=round(target_resale_price, 2),
        net_resale_price=round(net_resale_price, 2),
        profit=round(profit, 2),
        roi_percent=round(roi, 2),
        roi_percent_used=round(float(roi_percent), 2),
        market_fee_percent=round(float(market_fee_percent), 2),
    )
