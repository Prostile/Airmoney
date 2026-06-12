from datetime import timedelta

import pytest

from airmoney.config.import_export import validate_config
from airmoney.config.models import (
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
from airmoney.steam.scanner import calculate_floor_gap, looks_price_sorted
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
    assert [row["id"] for row in rows] == ["old"]


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
