from airmoney.config.models import ParserSettings
from airmoney.recommendation.engine import evaluate_listing
from airmoney.recommendation.scoring import should_alert
from airmoney.telegram.templates import candidate_alert


def settings():
    return ParserSettings(
        default_roi_percent=20,
        default_market_fee_percent=10,
        default_min_profit_rub=50,
        default_min_roi_percent=5,
    )


def test_good_candidate_uses_global_roi_when_custom_is_missing():
    candidate = evaluate_listing(
        listing_id="listing_1",
        buy_price_rub=1000,
        float_value=0.02,
        pattern=10,
        rule={"id": "rule1", "enabled": 1, "target_resale_price_rub": 1170},
        settings=settings(),
    )
    assert candidate.recommendation_level == "good"
    assert candidate.estimated_profit_rub == 53
    assert should_alert(candidate.recommendation_level, "good")


def test_custom_roi_and_hard_filters_can_skip_candidate():
    candidate = evaluate_listing(
        listing_id="listing_2",
        buy_price_rub=5000,
        float_value=0.03,
        pattern=99,
        rule={
            "id": "rule1",
            "enabled": 1,
            "custom_roi_percent": 50,
            "max_buy_price_rub": 4000,
            "pattern_ranges": "1-10",
        },
        settings=settings(),
    )
    assert candidate.recommendation_level == "skip"
    assert "цена выше лимита" in candidate.recommendation_reason


def test_watch_candidate_is_saved_but_not_alerted():
    candidate = evaluate_listing(
        listing_id="listing_3",
        buy_price_rub=1000,
        float_value=None,
        pattern=None,
        rule={"id": "rule1", "enabled": 1, "target_resale_price_rub": 1120},
        settings=settings(),
    )
    assert candidate.recommendation_level == "watch"
    assert not should_alert(candidate.recommendation_level, "watch")


def test_telegram_alert_template_is_short():
    text = candidate_alert(
        {
            "skin_name": "UMP-45 | Green Swirl",
            "buy_price_rub": 4400,
            "estimated_resale_price_rub": 5600,
            "estimated_profit_rub": 360,
            "estimated_roi_percent": 8.18,
            "float_value": 0.0145,
            "pattern": 321,
        },
        "https://example.test/candidates",
    )
    assert "UMP-45 | Green Swirl" in text
    assert "https://example.test/candidates" in text
    assert "Buy:" in text


def test_target_float_adds_scoring_reason():
    candidate = evaluate_listing(
        listing_id="listing_target_float",
        buy_price_rub=1000,
        float_value=0.015,
        pattern=10,
        rule={
            "id": "rule1",
            "enabled": 1,
            "target_resale_price_rub": 1170,
            "float_min": 0.01,
            "float_max": 0.02,
            "target_float_min": 0.014,
            "target_float_max": 0.016,
        },
        settings=settings(),
    )
    assert "целевой диапазон" in candidate.recommendation_reason
    assert candidate.recommendation_score > 0
