from __future__ import annotations

from datetime import datetime, timezone

from airmoney.anomaly.analyzer import analyze_listing
from airmoney.anomaly.baselines import (
    assign_float_bucket,
    calculate_float_peer_baseline,
    calculate_local_baseline,
    get_float_neighbors,
)
from airmoney.anomaly.matching import is_exact_item_match, passes_item_match
from airmoney.anomaly.models import ParsedListing
from airmoney.anomaly.history import build_market_snapshots, ewma
from airmoney.anomaly.scoring import calculate_real_profit, estimate_fair_price, resolve_alert_level
from airmoney.config.models import AnomalySettings, Collection, ItemDefinition, MarketListing, ParserSettings
from airmoney.storage.repositories import Repository
from airmoney.steam.scanner import _evaluate_item_listings


def listing(
    price: float,
    wear: float | None = 0.0115,
    title: str = "Souvenir UMP-45 | Mechanism (Factory New)",
) -> ParsedListing:
    return ParsedListing(
        item_id="ump",
        expected_market_hash_name="Souvenir UMP-45 | Mechanism (Factory New)",
        actual_title=title,
        listing_url="https://steamcommunity.com/market/listings/730/example",
        raw_text=title,
        price_rub=price,
        wear_rating=wear,
        pattern_template=None,
        is_souvenir=title.lower().startswith("souvenir "),
        is_stattrak="stattrak" in title.lower(),
        exterior="Factory New",
        parsed_at=datetime.now(timezone.utc),
    )


def test_exact_item_filter_souvenir_vs_normal():
    souvenir = listing(472)
    normal = listing(472, title="UMP-45 | Mechanism (Factory New)")
    item = {
        "market_hash_name": "Souvenir UMP-45 | Mechanism (Factory New)",
        "is_souvenir": True,
        "is_stattrak": False,
        "exterior": "Factory New",
    }

    assert is_exact_item_match(souvenir, item["market_hash_name"]) is True
    assert passes_item_match(souvenir, item, require_exact_item_match=True) is True
    assert passes_item_match(normal, item, require_exact_item_match=True) is False
    assert passes_item_match(normal, item, require_exact_item_match=False) is False


def test_local_median_discount_and_mad_robust_z():
    prices = [472, 690, 797, 816, 869, 916, 1081]
    listings = [listing(price) for price in prices]
    result = calculate_local_baseline(listings[0], listings, min_samples=5)

    assert result.median == 842.5
    assert result.discount_percent == 43.98
    assert result.robust_z is not None
    assert result.robust_z > 1


def test_float_bucket_assignment():
    settings = AnomalySettings()
    assert assign_float_bucket(0.0049, settings.float_buckets) == "micro"
    assert assign_float_bucket(0.0115, settings.float_buckets) == "craft_low"
    assert assign_float_bucket(0.05, settings.float_buckets) == "normal_fn"
    assert assign_float_bucket(0.2, settings.float_buckets) == "other"


def test_float_peer_neighbors_and_discount():
    candidate = listing(472, 0.0115)
    peers = [
        candidate,
        listing(690, 0.0109),
        listing(797, 0.0121),
        listing(816, 0.0130),
        listing(869, 0.0140),
        listing(1500, 0.05),
    ]

    neighbors = get_float_neighbors(candidate, peers, k=4, max_float_distance=0.025)
    result = calculate_float_peer_baseline(candidate, peers, k=4, min_neighbors=4, max_float_distance=0.025)

    assert len(neighbors) == 4
    assert result.median == 806.5
    assert result.discount_percent == 41.48


def test_fair_price_estimation_and_override():
    settings = AnomalySettings()
    assert estimate_fair_price(800, None, None, None, settings.scoring) == 800
    assert estimate_fair_price(800, 900, None, None, settings.scoring) == 843.75
    assert estimate_fair_price(800, 900, None, {"target_resale_price_rub": 1000}, settings.scoring) == 1000


def test_real_profit_calculation():
    net, profit, roi = calculate_real_profit(472, 816, 15)

    assert net == 693.6
    assert profit == 221.6
    assert roi == 46.95


def test_alert_level_resolution():
    thresholds = AnomalySettings().thresholds

    assert resolve_alert_level(90, 300, 40, thresholds) == "critical"
    assert resolve_alert_level(75, 300, 40, thresholds) == "good"
    assert resolve_alert_level(60, 300, 40, thresholds) == "watch"
    assert resolve_alert_level(90, 10, 40, thresholds) == "skip"


def test_anomaly_analyzer_result_uses_real_profit_and_reasons():
    settings = ParserSettings()
    anomaly = settings.anomaly_settings
    anomaly.sample.min_listings = 5
    anomaly.nearest_neighbors.min_neighbors = 4
    settings.set_anomaly_settings(anomaly)
    candidate = listing(472, 0.0115)
    sample = [
        candidate,
        listing(690, 0.0109),
        listing(797, 0.0121),
        listing(816, 0.0130),
        listing(869, 0.0140),
        listing(916, 0.02),
        listing(1081, 0.03),
    ]

    result = analyze_listing(candidate, sample, {"enabled": True}, {"enabled": True}, settings)

    assert result.fair_price_rub is not None
    assert result.net_profit_rub is not None
    assert result.roi_percent is not None
    assert result.alert_level in {"critical", "good", "watch"}
    assert any("медианы" in reason for reason in result.reasons)


def test_target_resale_price_override_in_analyzer():
    settings = ParserSettings()
    anomaly = settings.anomaly_settings
    anomaly.sample.min_listings = 5
    settings.set_anomaly_settings(anomaly)
    sample = [listing(price) for price in [472, 690, 797, 816, 869, 916]]

    result = analyze_listing(sample[0], sample, {"enabled": True}, {"target_resale_price_rub": 1000}, settings)

    assert result.fair_price_rub == 1000


def test_target_float_and_priority_increase_anomaly_score():
    settings = ParserSettings()
    anomaly = settings.anomaly_settings
    anomaly.sample.min_listings = 5
    anomaly.nearest_neighbors.min_neighbors = 4
    settings.set_anomaly_settings(anomaly)
    sample = [
        listing(472, 0.0115),
        listing(690, 0.0109),
        listing(797, 0.0121),
        listing(816, 0.0130),
        listing(869, 0.0140),
        listing(916, 0.02),
    ]

    plain = analyze_listing(sample[0], sample, {"enabled": True}, {"enabled": True}, settings)
    boosted = analyze_listing(
        sample[0],
        sample,
        {"enabled": True},
        {
            "enabled": True,
            "target_float_min": 0.011,
            "target_float_max": 0.012,
            "priority": 5,
            "notes": "trade-up filler",
        },
        settings,
    )

    assert boosted.anomaly_score > plain.anomaly_score
    assert any("целевой диапазон" in reason for reason in boosted.reasons)
    assert any("priority" in reason for reason in boosted.reasons)
    assert any("trade-up filler" in reason for reason in boosted.reasons)


def test_anomaly_disabled_uses_legacy_profit_logic():
    settings = ParserSettings(default_roi_percent=12, default_market_fee_percent=15)
    anomaly = settings.anomaly_settings
    anomaly.enabled = False
    settings.set_anomaly_settings(anomaly)
    listing_row = MarketListing(
        id="listing_legacy",
        item_definition_id="item_1",
        rule_id="rule_1",
        skin_name="UMP-45 | Mechanism (Factory New)",
        buy_price_rub=100,
    )

    pairs = _evaluate_item_listings(
        [listing_row],
        {"id": "item_1", "market_hash_name": "UMP-45 | Mechanism (Factory New)", "enabled": True},
        {"id": "rule_1", "enabled": True, "target_resale_price_rub": 200, "min_profit_rub": 50, "min_roi_percent": 20},
        settings,
    )

    candidate = pairs[0][1]
    assert candidate.analysis_mode == "legacy"
    assert candidate.estimated_resale_price_rub == 200
    assert candidate.recommendation_level in {"good", "critical"}


def test_history_snapshots_update_repository_baseline(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="col", name="Collection"))
    repo.save_item(
        ItemDefinition(
            id="ump",
            collection_id="col",
            market_hash_name="Souvenir UMP-45 | Mechanism (Factory New)",
        )
    )
    settings = AnomalySettings()
    snapshots = build_market_snapshots(
        "ump",
        [listing(690, 0.0109), listing(797, 0.0121), listing(816, 0.0130)],
        settings.float_buckets,
    )

    repo.save_market_snapshots(snapshots, alpha=0.25)
    baselines = repo.list_market_baselines("ump")

    assert "craft_low" in baselines
    assert baselines["craft_low"] == 797
    assert ewma(100, 200, 0.25) == 125
