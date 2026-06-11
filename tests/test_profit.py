from airmoney.recommendation.profit import calculate_profit, choose_roi_percent


def test_calculate_profit_with_manual_target_price():
    estimate = calculate_profit(
        buy_price_rub=1000,
        roi_percent=12,
        market_fee_percent=15,
        target_resale_price_rub=1500,
    )
    assert estimate.target_resale_price == 1500
    assert estimate.net_resale_price == 1275
    assert estimate.profit == 275
    assert estimate.roi_percent == 27.5


def test_calculate_profit_with_roi_target():
    estimate = calculate_profit(buy_price_rub=1000, roi_percent=20, market_fee_percent=10)
    assert estimate.target_resale_price == 1333.33
    assert estimate.net_resale_price == 1200
    assert estimate.profit == 200
    assert estimate.roi_percent == 20


def test_choose_custom_roi_over_global_roi():
    assert choose_roi_percent(18, 12) == 18
    assert choose_roi_percent(None, 12) == 12
