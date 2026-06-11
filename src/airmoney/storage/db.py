from __future__ import annotations

import sqlite3
from pathlib import Path

from airmoney.config.models import ParserSettings
from airmoney.paths import DEFAULT_DB_PATH


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: str | Path | None = None) -> None:
    connection = connect(db_path)
    try:
        apply_schema(connection)
        apply_lightweight_migrations(connection)
        ensure_default_settings(connection)
    finally:
        connection.close()


def apply_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
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
            selected_exteriors TEXT NOT NULL,
            anomaly_config TEXT NOT NULL DEFAULT '{}',
            telegram_config TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS collections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            steam_collection_url TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL,
            market_hash_name TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            weapon_type TEXT NOT NULL DEFAULT '',
            rarity TEXT NOT NULL DEFAULT '',
            quality TEXT NOT NULL DEFAULT '',
            exterior TEXT NOT NULL DEFAULT '',
            is_souvenir INTEGER NOT NULL DEFAULT 0,
            is_stattrak INTEGER NOT NULL DEFAULT 0,
            steam_market_url TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_parsed_at TEXT,
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sniping_rules (
            id TEXT PRIMARY KEY,
            item_definition_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            max_buy_price_rub REAL,
            target_resale_price_rub REAL,
            custom_roi_percent REAL,
            min_profit_rub REAL,
            min_roi_percent REAL,
            float_min REAL,
            float_max REAL,
            target_float_min REAL,
            target_float_max REAL,
            pattern_ranges TEXT NOT NULL DEFAULT '',
            priority INTEGER NOT NULL DEFAULT 0,
            telegram_alert_enabled INTEGER NOT NULL DEFAULT 1,
            notes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (item_definition_id) REFERENCES items(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS market_listings (
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
            currency_fetched_at TEXT NOT NULL DEFAULT '',
            float_value REAL,
            pattern INTEGER,
            wear_name TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            parse_status TEXT NOT NULL DEFAULT 'ok',
            FOREIGN KEY (item_definition_id) REFERENCES items(id) ON DELETE CASCADE,
            FOREIGN KEY (rule_id) REFERENCES sniping_rules(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_market_listings_item_seen
            ON market_listings(item_definition_id, last_seen_at);

        CREATE TABLE IF NOT EXISTS candidates (
            id TEXT PRIMARY KEY,
            listing_id TEXT NOT NULL UNIQUE,
            rule_id TEXT,
            buy_price_rub REAL NOT NULL,
            estimated_resale_price_rub REAL NOT NULL,
            estimated_net_resale_rub REAL NOT NULL,
            estimated_profit_rub REAL NOT NULL,
            estimated_roi_percent REAL NOT NULL,
            market_fee_percent REAL NOT NULL,
            recommendation_level TEXT NOT NULL,
            recommendation_score REAL NOT NULL,
            recommendation_reason TEXT NOT NULL,
            analysis_mode TEXT NOT NULL DEFAULT 'legacy',
            alert_level TEXT NOT NULL DEFAULT '',
            anomaly_score REAL,
            fair_price_rub REAL,
            local_median_rub REAL,
            float_peer_median_rub REAL,
            historical_baseline_rub REAL,
            local_discount_percent REAL,
            float_peer_discount_percent REAL,
            historical_discount_percent REAL,
            robust_z REAL,
            float_bucket TEXT NOT NULL DEFAULT '',
            exact_item_match INTEGER NOT NULL DEFAULT 0,
            sample_size INTEGER NOT NULL DEFAULT 0,
            neighbor_count INTEGER NOT NULL DEFAULT 0,
            anomaly_reasons TEXT NOT NULL DEFAULT '',
            parsed_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (listing_id) REFERENCES market_listings(id) ON DELETE CASCADE,
            FOREIGN KEY (rule_id) REFERENCES sniping_rules(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_candidates_level_status
            ON candidates(recommendation_level, status, created_at);

        CREATE TABLE IF NOT EXISTS currency_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usd_to_rub REAL NOT NULL,
            eur_to_rub REAL NOT NULL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            is_fallback INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS telegram_alerts (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_actions (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scan_runs (
            id TEXT PRIMARY KEY,
            trigger TEXT NOT NULL,
            collection_id TEXT,
            item_id TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            total_items INTEGER NOT NULL DEFAULT 0,
            current_item_index INTEGER NOT NULL DEFAULT 0,
            current_item_name TEXT NOT NULL DEFAULT '',
            progress_message TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            scanned_items INTEGER NOT NULL DEFAULT 0,
            listings_saved INTEGER NOT NULL DEFAULT 0,
            candidates_saved INTEGER NOT NULL DEFAULT 0,
            alerts_sent INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_scan_runs_started
            ON scan_runs(started_at DESC);

        CREATE TABLE IF NOT EXISTS market_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            scan_time TEXT NOT NULL,
            float_bucket TEXT NOT NULL,
            sample_size INTEGER NOT NULL,
            floor_price_rub REAL,
            q10_price_rub REAL,
            q25_price_rub REAL,
            median_price_rub REAL,
            q75_price_rub REAL,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_market_snapshot_item_bucket
            ON market_snapshot(item_id, float_bucket, scan_time);

        CREATE TABLE IF NOT EXISTS market_baseline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            float_bucket TEXT NOT NULL,
            rolling_median_rub REAL,
            rolling_q25_rub REAL,
            rolling_floor_rub REAL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            UNIQUE(item_id, float_bucket),
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        );
        """
    )


def apply_lightweight_migrations(connection: sqlite3.Connection) -> None:
    settings_columns = _table_columns(connection, "settings")
    if "selected_exteriors" not in settings_columns:
        default = ParserSettings().selected_exteriors.replace("'", "''")
        connection.execute(
            f"ALTER TABLE settings ADD COLUMN selected_exteriors TEXT NOT NULL DEFAULT '{default}'"
        )
    settings_defaults = {
        "anomaly_config": ParserSettings().anomaly_config,
        "telegram_config": ParserSettings().telegram_config,
    }
    settings_columns = _table_columns(connection, "settings")
    for column, default_value in settings_defaults.items():
        if column not in settings_columns:
            escaped = default_value.replace("'", "''")
            connection.execute(
                f"ALTER TABLE settings ADD COLUMN {column} TEXT NOT NULL DEFAULT '{escaped}'"
            )
    listing_columns = _table_columns(connection, "market_listings")
    if "currency_fetched_at" not in listing_columns:
        connection.execute(
            "ALTER TABLE market_listings ADD COLUMN currency_fetched_at TEXT NOT NULL DEFAULT ''"
        )
    scan_run_columns = _table_columns(connection, "scan_runs")
    scan_run_defaults = {
        "total_items": "INTEGER NOT NULL DEFAULT 0",
        "current_item_index": "INTEGER NOT NULL DEFAULT 0",
        "current_item_name": "TEXT NOT NULL DEFAULT ''",
        "progress_message": "TEXT NOT NULL DEFAULT ''",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
    }
    for column, definition in scan_run_defaults.items():
        if column not in scan_run_columns:
            connection.execute(f"ALTER TABLE scan_runs ADD COLUMN {column} {definition}")

    candidate_columns = _table_columns(connection, "candidates")
    candidate_defaults = {
        "analysis_mode": "TEXT NOT NULL DEFAULT 'legacy'",
        "alert_level": "TEXT NOT NULL DEFAULT ''",
        "anomaly_score": "REAL",
        "fair_price_rub": "REAL",
        "local_median_rub": "REAL",
        "float_peer_median_rub": "REAL",
        "historical_baseline_rub": "REAL",
        "local_discount_percent": "REAL",
        "float_peer_discount_percent": "REAL",
        "historical_discount_percent": "REAL",
        "robust_z": "REAL",
        "float_bucket": "TEXT NOT NULL DEFAULT ''",
        "exact_item_match": "INTEGER NOT NULL DEFAULT 0",
        "sample_size": "INTEGER NOT NULL DEFAULT 0",
        "neighbor_count": "INTEGER NOT NULL DEFAULT 0",
        "anomaly_reasons": "TEXT NOT NULL DEFAULT ''",
        "parsed_at": "TEXT NOT NULL DEFAULT ''",
    }
    for column, definition in candidate_defaults.items():
        if column not in candidate_columns:
            connection.execute(f"ALTER TABLE candidates ADD COLUMN {column} {definition}")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_candidates_anomaly
            ON candidates(anomaly_score, float_bucket, estimated_profit_rub)
        """
    )


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def ensure_default_settings(connection: sqlite3.Connection) -> None:
    exists = connection.execute("SELECT 1 FROM settings WHERE id = 1").fetchone()
    if exists:
        return

    settings = ParserSettings()
    connection.execute(
        """
        INSERT INTO settings (
            id, enabled, check_interval_seconds, headless, max_scrolls,
            request_delay_seconds, steam_block_pause_seconds, currency_provider,
            currency_cache_ttl_seconds, fallback_usd_to_rub, fallback_eur_to_rub,
            telegram_alerts_enabled, telegram_min_alert_level, web_table_limit,
            default_roi_percent, default_market_fee_percent, default_min_profit_rub,
            default_min_roi_percent, selected_exteriors, anomaly_config,
            telegram_config, updated_at
        )
        VALUES (
            1, :enabled, :check_interval_seconds, :headless, :max_scrolls,
            :request_delay_seconds, :steam_block_pause_seconds, :currency_provider,
            :currency_cache_ttl_seconds, :fallback_usd_to_rub, :fallback_eur_to_rub,
            :telegram_alerts_enabled, :telegram_min_alert_level, :web_table_limit,
            :default_roi_percent, :default_market_fee_percent, :default_min_profit_rub,
            :default_min_roi_percent, :selected_exteriors, :anomaly_config,
            :telegram_config, :updated_at
        )
        """,
        {
            "enabled": int(settings.enabled),
            "check_interval_seconds": settings.check_interval_seconds,
            "headless": int(settings.headless),
            "max_scrolls": settings.max_scrolls,
            "request_delay_seconds": settings.request_delay_seconds,
            "steam_block_pause_seconds": settings.steam_block_pause_seconds,
            "currency_provider": settings.currency_provider,
            "currency_cache_ttl_seconds": settings.currency_cache_ttl_seconds,
            "fallback_usd_to_rub": settings.fallback_usd_to_rub,
            "fallback_eur_to_rub": settings.fallback_eur_to_rub,
            "telegram_alerts_enabled": int(settings.telegram_alerts_enabled),
            "telegram_min_alert_level": settings.telegram_min_alert_level,
            "web_table_limit": settings.web_table_limit,
            "default_roi_percent": settings.default_roi_percent,
            "default_market_fee_percent": settings.default_market_fee_percent,
            "default_min_profit_rub": settings.default_min_profit_rub,
            "default_min_roi_percent": settings.default_min_roi_percent,
            "selected_exteriors": settings.selected_exteriors,
            "anomaly_config": settings.anomaly_config,
            "telegram_config": settings.telegram_config,
            "updated_at": settings.updated_at,
        },
    )
