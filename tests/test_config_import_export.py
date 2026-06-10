from airmoney.config.import_export import export_config, import_config_text, validate_config
from airmoney.storage.repositories import Repository


VALID_CONFIG = """
version: 1
parser:
  enabled: true
  check_interval_seconds: 300
  max_scrolls: 1
  request_delay_seconds: 2
  steam_block_pause_seconds: 1800
  selected_exteriors:
    - Factory New
    - Minimal Wear
currency:
  provider: steam_currency
  cache_ttl_seconds: 21600
  fallback_usd_to_rub: 72.0
  fallback_eur_to_rub: 86.0
profit:
  global_roi_percent: 12
  market_fee_percent: 15
  min_profit_rub: 300
  min_roi_percent: 7
telegram:
  enabled: false
  min_alert_level: good
collections:
  - id: active_drop_2026
    name: Active Drop 2026
    steam_collection_url: https://steamcommunity.com/market/search?appid=730
    enabled: true
items:
  - id: ump_green_swirl_fn
    collection_id: active_drop_2026
    market_hash_name: Souvenir UMP-45 | Green Swirl (Factory New)
    display_name: UMP-45 | Green Swirl
    exterior: Factory New
    quality: Souvenir
    rarity: Restricted
    enabled: true
    steam_market_url: https://steamcommunity.com/market/listings/730/test
rules:
  - id: ump_green_swirl_fn_rule
    item_id: ump_green_swirl_fn
    enabled: true
    max_buy_price_rub: 4400
    target_resale_price_rub: 5600
    custom_roi_percent:
    min_profit_rub: 400
    min_roi_percent: 8
    float_min: 0.014
    float_max: 0.016
    pattern_ranges: ""
    telegram_alert_enabled: true
"""


def test_import_valid_config_and_export(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    result = import_config_text(repo, VALID_CONFIG)
    assert result.valid
    assert len(repo.list_collections()) == 1
    assert len(repo.list_items()) == 1
    exported = export_config(repo)
    assert "ump_green_swirl_fn" in exported
    assert "Factory New" in exported


def test_invalid_import_does_not_replace_current_config(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    assert import_config_text(repo, VALID_CONFIG).valid
    invalid = VALID_CONFIG.replace("collection_id: active_drop_2026", "collection_id: missing_collection")
    result = import_config_text(repo, invalid)
    assert not result.valid
    assert len(repo.list_collections()) == 1
    assert repo.list_items()[0]["collection_id"] == "active_drop_2026"


def test_validate_rejects_duplicate_ids():
    import yaml

    data = yaml.safe_load(VALID_CONFIG)
    data["collections"].append(dict(data["collections"][0]))
    result = validate_config(data)
    assert not result.valid
    assert any("уникальным" in error for error in result.errors)


def test_validate_rejects_unknown_exterior():
    import yaml

    data = yaml.safe_load(VALID_CONFIG)
    data["parser"]["selected_exteriors"] = ["Factory New", "Broken Wear"]
    result = validate_config(data)
    assert not result.valid
    assert any("unknown" in error.lower() or "неизвест" in error.lower() for error in result.errors)


def test_validate_rejects_reversed_float_range():
    import yaml

    data = yaml.safe_load(VALID_CONFIG)
    data["rules"][0]["float_min"] = 0.2
    data["rules"][0]["float_max"] = 0.1
    result = validate_config(data)
    assert not result.valid
    assert any("float_min" in error for error in result.errors)
