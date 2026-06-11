from airmoney.config.models import Collection, ItemDefinition, MarketListing, utc_now_iso
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
    repo.finish_scan_run(run_id, "success", scanned_items=1, listings_saved=2, candidates_saved=3, alerts_sent=1)
    latest = repo.latest_scan_run()
    assert latest["status"] == "success"
    assert latest["trigger"] == "test"
    assert latest["total_items"] == 3
    assert latest["current_item_index"] == 3
    assert latest["current_item_name"] == "Skin A"
    assert latest["progress_message"] == "Скан завершён."
    assert latest["listings_saved"] == 2

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
