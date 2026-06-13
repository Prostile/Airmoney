from datetime import timedelta

import pytest

from airmoney.config.import_export import validate_config
from airmoney.config.models import (
    CandidatePack,
    CandidatePackItem,
    BrowserOptimizationSettings,
    Collection,
    ItemDefinition,
    MarketListing,
    SnipingRule,
    utc_now,
    utc_now_iso,
)
from airmoney.scheduler import monitor as monitor_module
from airmoney.steam.browser import ResourceBlocker, SteamAccessLimited
from airmoney.steam.parser import JS_EXTRACT_MARKET_CARDS
from airmoney.steam.scanner import _sorted_market_url, calculate_floor_gap, looks_price_sorted
from airmoney.storage.repositories import Repository


def _repo_with_items(tmp_path, count=4):
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="c1", name="Collection"))
    for index in range(count):
        item_id = f"i{index}"
        repo.save_item(ItemDefinition(id=item_id, collection_id="c1", market_hash_name=f"Skin {index}"))
        repo.save_rule(SnipingRule(id=f"{item_id}_rule", item_definition_id=item_id, priority=index))
    return repo


def test_market_card_extractor_matches_real_currency_symbols():
    assert "\\u20bd" in JS_EXTRACT_MARKET_CARDS
    assert "\\u20ac" in JS_EXTRACT_MARKET_CARDS
    assert "\\u0440\\u0443\\u0431" in JS_EXTRACT_MARKET_CARDS


def test_scan_queue_limits_and_prioritizes_items(tmp_path):
    repo = _repo_with_items(tmp_path, count=4)
    settings = repo.get_settings()
    queue = settings.scan_queue_settings
    queue.max_items_per_cycle = 2
    queue.random_jitter = False
    queue.collection_cooldown_seconds = 0
    settings.set_scan_queue_settings(queue)

    selected = repo.select_scan_targets(settings)

    assert [row["id"] for row in selected.targets] == ["i3", "i2"]
    assert selected.skipped_by_queue_count == 2


def test_scan_queue_rotates_by_last_parsed_at_with_equal_priority(tmp_path):
    repo = _repo_with_items(tmp_path, count=2)
    old = (utc_now() - timedelta(hours=3)).replace(microsecond=0).isoformat()
    recent = (utc_now() - timedelta(minutes=10)).replace(microsecond=0).isoformat()
    repo.save_item(ItemDefinition(id="i0", collection_id="c1", market_hash_name="Skin 0", last_parsed_at=recent))
    repo.save_item(ItemDefinition(id="i1", collection_id="c1", market_hash_name="Skin 1", last_parsed_at=old))
    repo.save_rule(SnipingRule(id="i0_rule", item_definition_id="i0", priority=1))
    repo.save_rule(SnipingRule(id="i1_rule", item_definition_id="i1", priority=1))
    settings = repo.get_settings()
    queue = settings.scan_queue_settings
    queue.max_items_per_cycle = 1
    queue.item_cooldown_seconds = 0
    queue.collection_cooldown_seconds = 0
    queue.random_jitter = False
    settings.set_scan_queue_settings(queue)

    selected = repo.select_scan_targets(settings)

    assert [row["id"] for row in selected.targets] == ["i1"]


def test_scan_queue_respects_item_and_collection_cooldowns(tmp_path):
    repo = _repo_with_items(tmp_path, count=3)
    recent = (utc_now() - timedelta(minutes=5)).replace(microsecond=0).isoformat()
    repo.save_item(ItemDefinition(id="i0", collection_id="c1", market_hash_name="Skin 0", last_parsed_at=recent))
    settings = repo.get_settings()
    queue = settings.scan_queue_settings
    queue.item_cooldown_seconds = 1800
    queue.collection_cooldown_seconds = 0
    queue.random_jitter = False
    settings.set_scan_queue_settings(queue)

    selected = repo.select_scan_targets(settings)
    assert "i0" not in [row["id"] for row in selected.targets]
    assert selected.skipped_by_item_cooldown_count == 1

    queue.item_cooldown_seconds = 0
    queue.collection_cooldown_seconds = 3600
    settings.set_scan_queue_settings(queue)
    selected = repo.select_scan_targets(settings)
    assert selected.targets == []
    assert selected.skipped_by_collection_cooldown_count == 3


def test_scan_queue_respects_last_scanned_at_for_failed_scans(tmp_path):
    repo = _repo_with_items(tmp_path, count=2)
    recent = (utc_now() - timedelta(minutes=5)).replace(microsecond=0).isoformat()
    repo.save_item(ItemDefinition(id="i0", collection_id="c1", market_hash_name="Skin 0", last_scanned_at=recent))
    settings = repo.get_settings()
    queue = settings.scan_queue_settings
    queue.item_cooldown_seconds = 1800
    queue.collection_cooldown_seconds = 0
    queue.random_jitter = False
    settings.set_scan_queue_settings(queue)

    selected = repo.select_scan_targets(settings)

    assert "i0" not in [row["id"] for row in selected.targets]
    assert selected.skipped_by_item_cooldown_count == 1


def test_scan_queue_item_id_bypasses_queue(tmp_path):
    repo = _repo_with_items(tmp_path, count=2)
    recent = utc_now_iso()
    repo.save_item(ItemDefinition(id="i0", collection_id="c1", market_hash_name="Skin 0", last_parsed_at=recent))
    settings = repo.get_settings()
    queue = settings.scan_queue_settings
    queue.max_items_per_cycle = 1
    queue.item_cooldown_seconds = 999999
    queue.collection_cooldown_seconds = 999999
    settings.set_scan_queue_settings(queue)

    selected = repo.select_scan_targets(settings, item_id="i0")

    assert [row["id"] for row in selected.targets] == ["i0"]


def test_anomaly_sample_validation_rejects_bad_bounds():
    data = {
        "version": 1,
        "anomaly": {"sample": {"min_listings": 8, "target_listings": 6, "max_listings": 20}},
    }

    result = validate_config(data)

    assert not result.valid
    assert any("target_listings" in error for error in result.errors)


class _Request:
    def __init__(self, resource_type):
        self.resource_type = resource_type


class _Route:
    def __init__(self, resource_type):
        self.request = _Request(resource_type)
        self.aborted = False
        self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


def test_resource_blocking_blocks_heavy_resources_only():
    blocker = ResourceBlocker(BrowserOptimizationSettings(blocked_resource_types=["image", "media", "font", "stylesheet"]))
    image = _Route("image")
    xhr = _Route("xhr")
    script = _Route("script")
    stylesheet = _Route("stylesheet")

    blocker(image)
    blocker(xhr)
    blocker(script)
    blocker(stylesheet)

    assert image.aborted
    assert xhr.continued
    assert script.continued
    assert stylesheet.continued
    assert blocker.blocked_count == 1


def test_gap_and_sort_helpers():
    assert looks_price_sorted([610, 620, 635, 650])
    assert not looks_price_sorted([650, 610, 635, 620])
    assert calculate_floor_gap([610, 620, 635, 650, 660]) < 10
    assert calculate_floor_gap([410, 690, 720, 760, 800]) >= 10


def test_sorted_market_url_adds_rule_float_assetproperty_filter():
    url = _sorted_market_url(
        "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Test%20%28Factory%20New%29",
        "price_asc",
        {"exterior": "Factory New", "is_souvenir": False, "is_stattrak": False},
        {"float_min": 0.0, "float_max": 0.015},
    )

    assert "assetproperty=CAIVAAAAAB2PwnU8" in url
    assert "sort_column=price" in url
    assert "sort_dir=asc" in url


def test_steam_guard_skips_scan_during_active_cooldown(tmp_path):
    repo = _repo_with_items(tmp_path, count=1)
    cooldown_until = (utc_now() + timedelta(hours=1)).replace(microsecond=0).isoformat()
    repo.set_steam_guard_state(cooldown_until=cooldown_until, reason="test", consecutive_blocks=1)

    result = monitor_module.run_scan_cycle(repo, trigger="test")
    latest = repo.latest_scan_run()

    assert result.steam_cooldown_active
    assert latest["status"] == "skipped"
    assert latest["steam_cooldown_active"] == 1


def test_steam_guard_sets_cooldown_on_access_limited(tmp_path, monkeypatch):
    repo = _repo_with_items(tmp_path, count=1)
    settings = repo.get_settings()
    guard = settings.steam_guard_settings
    guard.jitter_percent = 0
    settings.set_steam_guard_settings(guard)
    repo.save_settings(settings)

    def fail_scan(*args, **kwargs):
        raise SteamAccessLimited("please try again later")

    monkeypatch.setattr(monitor_module, "scan_once", fail_scan)

    with pytest.raises(SteamAccessLimited):
        monitor_module.run_scan_cycle(repo, trigger="test")

    state = repo.get_steam_guard_state()
    assert state["steam_cooldown_until"]
    assert state["steam_consecutive_blocks"] == 1
    assert "please try again later" in state["steam_cooldown_reason"]


def test_item_failure_does_not_inactivate_old_listings(tmp_path):
    repo = _repo_with_items(tmp_path, count=1)
    now = utc_now_iso()
    old = MarketListing(id="old", item_definition_id="i0", rule_id="i0_rule", skin_name="Skin", buy_price_rub=100, first_seen_at=now, last_seen_at=now)
    repo.save_listing(old)
    run_id = repo.start_scan_run("test")

    repo.save_item_scan_failure(run_id, "i0", {"status": "failed", "error": "boom"})

    rows = repo.list_market_listings(item_id="i0", active_only=True, limit=10)
    item = repo.get_item("i0")
    assert [row["id"] for row in rows] == ["old"]
    assert item["last_parsed_at"] == now
    assert item["last_scanned_at"]


def test_item_success_inactivates_old_listings_and_saves_new_batch(tmp_path):
    repo = _repo_with_items(tmp_path, count=1)
    now = utc_now_iso()
    old = MarketListing(id="old", item_definition_id="i0", rule_id="i0_rule", skin_name="Skin", buy_price_rub=100, first_seen_at=now, last_seen_at=now)
    new = MarketListing(id="new", item_definition_id="i0", rule_id="i0_rule", skin_name="Skin", buy_price_rub=90, first_seen_at=now, last_seen_at=now)
    repo.save_listing(old)
    run_id = repo.start_scan_run("test")

    saved = repo.save_item_scan_success(run_id, "i0", [new], [], item_result={"status": "success", "exact_cards": 1})

    rows = repo.list_market_listings(item_id="i0", active_only=True, limit=10)
    assert saved["listings_saved"] == 1
    assert [row["id"] for row in rows] == ["new"]


def test_repository_saves_pack_tables_without_candidate_rows(tmp_path):
    repo = _repo_with_items(tmp_path, count=1)
    run_id = repo.start_scan_run("test")
    now = utc_now_iso()
    listings = [
        MarketListing(
            id=f"listing_{index}",
            item_definition_id="i0",
            rule_id="i0_rule",
            skin_name="Skin 0",
            market_hash_name="Skin 0",
            buy_price_rub=price,
            float_value=0.01 + index * 0.001,
            first_seen_at=now,
            last_seen_at=now,
        )
        for index, price in enumerate([100, 110, 180])
    ]
    pack = CandidatePack(
        pack_id="pack_i0_2_180",
        item_id="i0",
        collection_id="c1",
        market_hash_name="Skin 0",
        display_name="Skin 0",
        listing_ids=["listing_0", "listing_1"],
        pack_size=2,
        pack_cost_rub=210,
        min_buy_price_rub=100,
        max_buy_price_rub=110,
        next_floor_after_pack_rub=180,
        gap_percent=63.64,
        gross_resale_rub=360,
        net_resale_rub=306,
        estimated_profit_rub=96,
        estimated_roi_percent=45.71,
        capital_required_rub=210,
        alert_level="good",
        market_confidence="high",
        pack_confidence="high",
        sample_size=3,
    )
    pack_items = [
        CandidatePackItem(
            pack_id=pack.pack_id,
            listing_id="listing_0",
            item_id="i0",
            buy_price_rub=100,
            position_in_pack=1,
            solo_alert_level="skip",
        ),
        CandidatePackItem(
            pack_id=pack.pack_id,
            listing_id="listing_1",
            item_id="i0",
            buy_price_rub=110,
            position_in_pack=2,
            solo_alert_level="skip",
        ),
    ]

    saved = repo.save_item_scan_success(
        run_id,
        "i0",
        listings,
        [],
        packs=[pack],
        pack_items=pack_items,
        item_result={"status": "success", "exact_cards": 3},
    )

    packs = repo.list_candidate_packs()
    items = repo.list_candidate_pack_items(pack.pack_id)
    unsent = repo.list_unsent_alert_packs()
    assert saved["candidates_saved"] == 0
    assert len(packs) == 1
    assert packs[0]["pack_cost_rub"] == 210
    assert packs[0]["capital_required_rub"] == 210
    assert len(items) == 2
    assert all(row["candidate_id"] is None for row in items)
    assert [row["pack_id"] for row in unsent] == [pack.pack_id]

    repo.save_candidate_packs("i0", [], [])

    assert repo.list_candidate_packs() == []
    inactive = repo.list_candidate_packs(active_only=False)
    assert len(inactive) == 1
    assert inactive[0]["is_active"] == 0


def test_substitute_context_reports_stale_metadata(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    old_scanned = (utc_now() - timedelta(days=2)).replace(microsecond=0).isoformat()
    repo.save_collection(Collection(id="c1", name="Collection"))
    repo.save_item(
        ItemDefinition(
            id="target",
            collection_id="c1",
            market_hash_name="Target",
            rarity="Covert",
            exterior="Factory New",
        )
    )
    repo.save_item(
        ItemDefinition(
            id="sub",
            collection_id="c1",
            market_hash_name="Substitute",
            rarity="Covert",
            exterior="Factory New",
            last_scanned_at=old_scanned,
        )
    )
    repo.save_listing(
        MarketListing(
            id="sub_listing",
            item_definition_id="sub",
            rule_id=None,
            skin_name="Substitute",
            buy_price_rub=1000,
            float_value=0.01,
            first_seen_at=old_scanned,
            last_seen_at=old_scanned,
        )
    )

    context = repo.substitute_price_context(
        repo.get_item("target"),
        target_float_max=0.015,
        premium_multiplier=1.1,
        min_sample=1,
        stale_after_seconds=60,
    )

    assert context["floor_rub"] == 1000
    assert context["sample_size"] == 1
    assert context["item_count"] == 1
    assert context["cap_rub"] == 1100
    assert context["stale"] is True
    assert context["last_scanned_at"]
    assert any("stale" in reason for reason in context["reasons"])
