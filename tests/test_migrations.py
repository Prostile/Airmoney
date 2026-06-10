import sqlite3

from airmoney.storage.db import initialize_database


def test_initialize_database_adds_currency_fetched_at_to_existing_market_listings(tmp_path):
    db_path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL,
            check_interval_seconds INTEGER NOT NULL,
            headless INTEGER NOT NULL,
            max_scrolls INTEGER NOT NULL,
            request_delay_seconds REAL NOT NULL,
            steam_block_pause_seconds INTEGER NOT NULL,
            currency_provider TEXT NOT NULL,
            currency_cache_ttl_seconds INTEGER NOT NULL,
            fallback_usd_to_rub REAL NOT NULL,
            fallback_eur_to_rub REAL NOT NULL,
            telegram_alerts_enabled INTEGER NOT NULL,
            telegram_min_alert_level TEXT NOT NULL,
            web_table_limit INTEGER NOT NULL,
            default_roi_percent REAL NOT NULL,
            default_market_fee_percent REAL NOT NULL,
            default_min_profit_rub REAL NOT NULL,
            default_min_roi_percent REAL NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO settings VALUES (
            1, 0, 300, 1, 1, 2, 1800, 'steam_currency', 21600,
            72, 86, 0, 'good', 200, 12, 15, 300, 7, '2026-06-11T00:00:00+00:00'
        );
        CREATE TABLE market_listings (
            id TEXT PRIMARY KEY,
            item_definition_id TEXT NOT NULL,
            rule_id TEXT,
            skin_name TEXT NOT NULL,
            market_hash_name TEXT NOT NULL DEFAULT '',
            listing_url TEXT NOT NULL DEFAULT '',
            search_url TEXT NOT NULL DEFAULT '',
            buy_price_rub REAL NOT NULL,
            buy_price_original REAL,
            currency_original TEXT NOT NULL DEFAULT 'RUB',
            currency_rate REAL,
            currency_source TEXT NOT NULL DEFAULT '',
            float_value REAL,
            pattern INTEGER,
            wear_name TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            parse_status TEXT NOT NULL DEFAULT 'ok'
        );
        """
    )
    connection.close()

    initialize_database(db_path)

    connection = sqlite3.connect(db_path)
    settings_columns = {row[1] for row in connection.execute("PRAGMA table_info(settings)")}
    listing_columns = {row[1] for row in connection.execute("PRAGMA table_info(market_listings)")}
    connection.close()
    assert "selected_exteriors" in settings_columns
    assert "currency_fetched_at" in listing_columns
