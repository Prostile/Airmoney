from __future__ import annotations

from airmoney.telegram.templates import batch_alert, candidate_alert


def candidate(level: str = "good") -> dict:
    return {
        "recommendation_level": level,
        "skin_name": "Souvenir UMP-45 | Mechanism FN",
        "buy_price_rub": 472,
        "float_value": 0.0115,
        "float_peer_median_rub": 816,
        "estimated_profit_rub": 221.6,
        "estimated_roi_percent": 46.9,
        "float_peer_discount_percent": 42.1,
        "anomaly_score": 88,
        "sample_size": 8,
        "neighbor_count": 5,
        "anomaly_reasons": "local discount; float peer discount",
        "listing_url": "https://steamcommunity.com/market/listings/730/example",
    }


def test_compact_candidate_alert_contains_only_signal_fields():
    text = candidate_alert(candidate("critical"), "https://example.test/candidates")

    assert "[CRITICAL]" in text
    assert "Buy: 472 ₽" in text
    assert "Float: 0.0115" in text
    assert "Avg: 816 ₽" in text
    assert "Profit: +222 ₽ / +46.9%" in text
    assert "Discount: -42%" in text
    assert "Score: 88" in text
    assert "raw" not in text.lower()


def test_candidate_alert_can_include_optional_diagnostics():
    text = candidate_alert(
        candidate("critical"),
        "https://example.test/candidates",
        include_sample_stats=True,
        include_reasons=True,
    )

    assert "Sample: 8 / nn 5" in text
    assert "Reasons:" in text
    assert "- local discount" in text
    assert "- float peer discount" in text


def test_batch_alert_uses_single_dashboard_link():
    text = batch_alert([candidate("critical"), candidate("good")], "https://example.test/candidates")

    assert "Airmoney: 2 anomalies" in text
    assert "[CRIT]" in text
    assert "[GOOD]" in text
    assert text.count("https://example.test/candidates") == 1
