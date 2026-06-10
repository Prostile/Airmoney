from pathlib import Path

from airmoney.reports.legacy_import import import_legacy_matches_csv
from airmoney.storage.repositories import Repository


def test_import_legacy_matches_csv(tmp_path):
    csv_path = tmp_path / "matches.csv"
    csv_path.write_text(
        "source_label;source_url;name;pattern;wear;price_usd;price_rub;currency_source;href;scan_time;raw_text\n"
        "UMP-45 | Green Swirl;https://steamcommunity.com/market/listings/730/test;UMP-45 | Green Swirl;321;0.014;61.1;4400;RUB;https://steamcommunity.com/market/listings/730/test;2026-06-10 20:00:00;text\n",
        encoding="utf-8-sig",
    )
    repo = Repository(tmp_path / "test.sqlite3")
    count = import_legacy_matches_csv(csv_path, repo)
    assert count == 1
    assert len(repo.list_collections()) == 1
    assert len(repo.list_market_listings(limit=10)) == 1
