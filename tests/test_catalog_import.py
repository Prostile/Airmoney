from airmoney.config.catalog_import import import_catalog_text
from airmoney.storage.repositories import Repository


CATALOG = """
collections:
  - id: active_drop
    name: Active Drop
    enabled: true
    items:
      - base_name: "UMP-45 | Green Swirl"
        market_name_prefix: Souvenir
        rarity: Restricted
        exteriors:
          - Factory New
          - Minimal Wear
items:
  - collection_id: active_drop
    market_hash_name: "AK-47 | Test (Field-Tested)"
    display_name: "AK-47 | Test"
"""


def test_catalog_import_adds_collections_and_items_without_replacing_settings(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    old_roi = repo.get_settings().default_roi_percent
    result = import_catalog_text(repo, CATALOG)
    assert result.valid
    assert result.collections_count == 1
    assert result.items_count == 3
    assert len(repo.list_collections()) == 1
    assert len(repo.list_items()) == 3
    assert repo.get_settings().default_roi_percent == old_roi


def test_catalog_import_validate_only_does_not_apply(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    result = import_catalog_text(repo, CATALOG, apply=False)
    assert result.valid
    assert len(repo.list_collections()) == 0


def test_catalog_import_rejects_unknown_exterior(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    result = import_catalog_text(repo, CATALOG.replace("Minimal Wear", "Broken Wear"))
    assert not result.valid
    assert any("неизвест" in error.lower() for error in result.errors)
