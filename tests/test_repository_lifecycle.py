from airmoney.config.models import Collection, ItemDefinition, MarketListing, SnipingRule, utc_now_iso
from airmoney.recommendation.engine import evaluate_listing
from airmoney.scheduler.monitor import run_scan_cycle
from airmoney.storage.repositories import Repository


def test_inactive_listing_expires_new_candidate(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="c1", name="Collection"))
    repo.save_item(
        ItemDefinition(
            id="i1",
            collection_id="c1",
            market_hash_name="AK-47 | Test (Factory New)",
            display_name="AK-47 | Test",
            steam_market_url="https://steamcommunity.com/market/listings/730/test",
        )
    )
    rule = repo.get_rule_for_item("i1")
    now = utc_now_iso()
    listing = MarketListing(
        id="listing_1",
        item_definition_id="i1",
        rule_id=rule["id"],
        skin_name="AK-47 | Test",
        buy_price_rub=1000,
        first_seen_at=now,
        last_seen_at=now,
    )
    repo.save_listing(listing)
    candidate = evaluate_listing(
        listing_id=listing.id,
        buy_price_rub=1000,
        float_value=None,
        pattern=None,
        rule={**rule, "target_resale_price_rub": 1500},
        settings=repo.get_settings(),
    )
    repo.save_candidate(candidate)

    repo.mark_listings_inactive_for_items(["i1"])
    assert repo.expire_candidates_for_inactive_listings() == 1
    rows = repo.list_candidates(status="expired", limit=10)
    assert rows[0]["status"] == "expired"


def test_scan_run_lifecycle_and_bulk_candidate_status(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    run_id = repo.start_scan_run("test", collection_id="c1")
    repo.update_scan_run_progress(
        run_id,
        total_items=3,
        current_item_index=1,
        current_item_name="Skin A",
        progress_message="Читаем карточки",
        scanned_items=0,
        listings_saved=1,
        candidates_saved=1,
    )
    repo.finish_scan_run(
        run_id,
        "success",
        scanned_items=1,
        listings_saved=2,
        candidates_saved=3,
        analysis_rows_saved=5,
        skip_candidates_saved=2,
        alerts_sent=1,
    )
    latest = repo.latest_scan_run()
    assert latest["status"] == "success"
    assert latest["trigger"] == "test"
    assert latest["total_items"] == 3
    assert latest["current_item_index"] == 3
    assert latest["current_item_name"] == "Skin A"
    assert latest["progress_message"] == "Скан завершён."
    assert latest["listings_saved"] == 2
    assert latest["candidates_saved"] == 3
    assert latest["analysis_rows_saved"] == 5
    assert latest["skip_candidates_saved"] == 2

    repo.save_collection(Collection(id="c1", name="Collection"))
    repo.save_item(ItemDefinition(id="i1", collection_id="c1", market_hash_name="Skin A"))
    rule = repo.get_rule_for_item("i1")
    now = utc_now_iso()
    for index in range(2):
        listing = MarketListing(
            id=f"listing_bulk_{index}",
            item_definition_id="i1",
            rule_id=rule["id"],
            skin_name=f"Skin {index}",
            buy_price_rub=1000,
            first_seen_at=now,
            last_seen_at=now,
        )
        repo.save_listing(listing)
        repo.save_candidate(
            evaluate_listing(
                listing_id=listing.id,
                buy_price_rub=1000,
                float_value=None,
                pattern=None,
                rule={**rule, "target_resale_price_rub": 1500},
                settings=repo.get_settings(),
            )
        )
    rows = repo.list_candidates(date_from=now[:10], date_to=now[:10], limit=10)
    assert len(rows) == 2
    assert repo.update_candidate_statuses([row["id"] for row in rows], "checked") == 2
    assert len(repo.list_candidates(status="checked", limit=10)) == 2


def test_exact_item_only_filters_stored_exact_matches(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="c1", name="Collection"))
    repo.save_item(ItemDefinition(id="i1", collection_id="c1", market_hash_name="Skin A"))
    rule = repo.get_rule_for_item("i1")
    now = utc_now_iso()

    for index, exact in enumerate([True, False]):
        listing = MarketListing(
            id=f"listing_exact_{index}",
            item_definition_id="i1",
            rule_id=rule["id"],
            skin_name="Skin A",
            buy_price_rub=1000,
            first_seen_at=now,
            last_seen_at=now,
        )
        repo.save_listing(listing)
        candidate = evaluate_listing(
            listing_id=listing.id,
            buy_price_rub=1000,
            float_value=None,
            pattern=None,
            rule={**rule, "target_resale_price_rub": 1500},
            settings=repo.get_settings(),
        )
        candidate.exact_item_match = exact
        repo.save_candidate(candidate)

    rows = repo.list_candidates(exact_item_only=True, limit=10)

    assert len(rows) == 1
    assert rows[0]["exact_item_match"] == 1


def test_list_items_returns_price_context_for_rule_and_target_filters(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="c1", name="Collection"))
    repo.save_item(ItemDefinition(id="i1", collection_id="c1", market_hash_name="Skin A"))
    rule = repo.get_rule_for_item("i1")
    repo.save_rule(
        SnipingRule(
            id=rule["id"],
            item_definition_id="i1",
            float_min=0.0,
            float_max=0.02,
            target_float_min=0.0,
            target_float_max=0.01,
        )
    )
    now = utc_now_iso()
    for index, (price, float_value) in enumerate([(100, 0.005), (120, 0.015), (200, 0.05)]):
        repo.save_listing(
            MarketListing(
                id=f"listing_price_{index}",
                item_definition_id="i1",
                rule_id=rule["id"],
                skin_name="Skin A",
                buy_price_rub=price,
                float_value=float_value,
                first_seen_at=now,
                last_seen_at=now,
            )
        )

    item = repo.list_items()[0]

    assert item["current_price_rub"] == 100
    assert item["median_price_rub"] == 120
    assert item["rule_floor_rub"] == 100
    assert item["target_floor_rub"] == 100
    assert item["active_listing_count"] == 3
    assert item["rule_listing_count"] == 2
    assert item["target_listing_count"] == 1
    assert item["best_float_seen"] == 0.005


def test_item_scan_success_hides_stale_candidates_for_current_non_analyzed_listings(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="c1", name="Collection"))
    repo.save_item(ItemDefinition(id="i1", collection_id="c1", market_hash_name="Skin A"))
    rule = repo.get_rule_for_item("i1")
    now = utc_now_iso()
    listing = MarketListing(
        id="listing_stale",
        item_definition_id="i1",
        rule_id=rule["id"],
        skin_name="Skin A",
        buy_price_rub=1000,
        first_seen_at=now,
        last_seen_at=now,
    )
    repo.save_listing(listing)
    candidate = evaluate_listing(
        listing_id=listing.id,
        buy_price_rub=1000,
        float_value=None,
        pattern=None,
        rule={**rule, "target_resale_price_rub": 2000},
        settings=repo.get_settings(),
    )
    repo.save_candidate(candidate)
    assert len(repo.list_candidates(limit=10)) == 1

    run_id = repo.start_scan_run("test")
    repo.save_item_scan_success(
        run_id,
        "i1",
        [listing],
        [],
        item_result={"status": "scanned_no_rule_matches", "exact_cards": 1},
    )

    assert repo.list_candidates(limit=10) == []
    assert repo.get_candidate_details(candidate.id)["recommendation_level"] == "skip"


def test_scan_without_enabled_collections_is_skipped_with_diagnostic(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="c1", name="Collection", enabled=False))
    repo.save_item(ItemDefinition(id="i1", collection_id="c1", market_hash_name="Skin A"))

    summary = repo.scan_target_summary()
    assert summary["enabled_items"] == 1
    assert summary["enabled_collections"] == 0
    assert summary["scan_targets"] == 0
    assert summary["items_blocked_by_disabled_collection"] == 1

    result = run_scan_cycle(repo, trigger="test")
    latest = repo.latest_scan_run()
    assert result.scanned_items == 0
    assert latest["status"] == "skipped"
    assert latest["total_items"] == 0
    assert latest["progress_message"] == latest["error"]
    assert "коллекции выключены" in latest["error"]
