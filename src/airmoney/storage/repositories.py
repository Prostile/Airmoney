from __future__ import annotations

import json
import sqlite3
import uuid
import random
from statistics import median
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from airmoney.anomaly.history import MarketSnapshot, ewma
from airmoney.config.models import (
    Candidate,
    Collection,
    ItemDefinition,
    MarketListing,
    ParserSettings,
    SnipingRule,
    parse_dt,
    to_bool,
    utc_now,
    utc_now_iso,
)
from airmoney.storage.db import connect, initialize_database
from airmoney.steam.extractor import value_in_ranges


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _nullable_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _nullable_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _date_start(value: str) -> str:
    text = str(value).strip()
    if len(text) == 10:
        return text + "T00:00:00"
    return text


def _date_end(value: str) -> str:
    text = str(value).strip()
    if len(text) == 10:
        return text + "T23:59:59"
    return text


def _optional_float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _listing_prices(listings: list[dict[str, Any]]) -> list[float]:
    prices: list[float] = []
    for listing in listings:
        price = _optional_float(listing, "buy_price_rub")
        if price is not None and price > 0:
            prices.append(price)
    return prices


def _listing_matches_hard_rule(listing: dict[str, Any], rule: dict[str, Any]) -> bool:
    if rule.get("rule_enabled") is not None and not bool(rule.get("rule_enabled")):
        return False
    price = _optional_float(listing, "buy_price_rub")
    if price is None or price <= 0:
        return False
    max_buy = _optional_float(rule, "max_buy_price_rub")
    if max_buy is not None and price > max_buy:
        return False
    float_value = _optional_float(listing, "float_value")
    float_min = _optional_float(rule, "float_min")
    float_max = _optional_float(rule, "float_max")
    if float_min is not None and (float_value is None or float_value < float_min):
        return False
    if float_max is not None and (float_value is None or float_value > float_max):
        return False
    pattern_ranges = str(rule.get("pattern_ranges") or "")
    if pattern_ranges and not value_in_ranges(listing.get("pattern"), pattern_ranges):
        return False
    return True


def _listing_matches_target_float(listing: dict[str, Any], rule: dict[str, Any]) -> bool:
    target_min = _optional_float(rule, "target_float_min")
    target_max = _optional_float(rule, "target_float_max")
    if target_min is None and target_max is None:
        return False
    float_value = _optional_float(listing, "float_value")
    if float_value is None:
        return False
    left = target_min if target_min is not None else float("-inf")
    right = target_max if target_max is not None else float("inf")
    return left <= float_value <= right


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value[:2000]
    try:
        return json.dumps(value, ensure_ascii=False)[:2000]
    except TypeError:
        return "{}"


@dataclass
class ScanTargetSelection:
    targets: list[dict[str, Any]]
    selected_targets_count: int = 0
    skipped_by_queue_count: int = 0
    skipped_by_item_cooldown_count: int = 0
    skipped_by_collection_cooldown_count: int = 0


class Repository:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = db_path
        initialize_database(db_path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = connect(self.db_path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get_settings(self) -> ParserSettings:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        if row is None:
            return ParserSettings()
        data = dict(row)
        return ParserSettings(
            enabled=to_bool(data["enabled"]),
            check_interval_seconds=int(data["check_interval_seconds"]),
            headless=to_bool(data["headless"]),
            max_scrolls=int(data["max_scrolls"]),
            request_delay_seconds=float(data["request_delay_seconds"]),
            steam_block_pause_seconds=int(data["steam_block_pause_seconds"]),
            currency_provider=data["currency_provider"],
            currency_cache_ttl_seconds=int(data["currency_cache_ttl_seconds"]),
            fallback_usd_to_rub=float(data["fallback_usd_to_rub"]),
            fallback_eur_to_rub=float(data["fallback_eur_to_rub"]),
            telegram_alerts_enabled=to_bool(data["telegram_alerts_enabled"]),
            telegram_min_alert_level=data["telegram_min_alert_level"],
            web_table_limit=int(data["web_table_limit"]),
            default_roi_percent=float(data["default_roi_percent"]),
            default_market_fee_percent=float(data["default_market_fee_percent"]),
            default_min_profit_rub=float(data["default_min_profit_rub"]),
            default_min_roi_percent=float(data["default_min_roi_percent"]),
            selected_exteriors=data.get("selected_exteriors") or ParserSettings().selected_exteriors,
            anomaly_config=data.get("anomaly_config") or ParserSettings().anomaly_config,
            telegram_config=data.get("telegram_config") or ParserSettings().telegram_config,
            scan_queue_config=data.get("scan_queue_config") or ParserSettings().scan_queue_config,
            browser_optimization_config=(
                data.get("browser_optimization_config") or ParserSettings().browser_optimization_config
            ),
            scan_optimization_config=data.get("scan_optimization_config") or ParserSettings().scan_optimization_config,
            history_optimization_config=(
                data.get("history_optimization_config") or ParserSettings().history_optimization_config
            ),
            steam_guard_config=data.get("steam_guard_config") or ParserSettings().steam_guard_config,
            market_risk_config=data.get("market_risk_config") or ParserSettings().market_risk_config,
            pack_detection_config=data.get("pack_detection_config") or ParserSettings().pack_detection_config,
            capital_config=data.get("capital_config") or ParserSettings().capital_config,
            craft_context_config=data.get("craft_context_config") or ParserSettings().craft_context_config,
            updated_at=data["updated_at"],
        )

    def save_settings(self, settings: ParserSettings) -> None:
        settings.updated_at = utc_now_iso()
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE settings SET
                    enabled = :enabled,
                    check_interval_seconds = :check_interval_seconds,
                    headless = :headless,
                    max_scrolls = :max_scrolls,
                    request_delay_seconds = :request_delay_seconds,
                    steam_block_pause_seconds = :steam_block_pause_seconds,
                    currency_provider = :currency_provider,
                    currency_cache_ttl_seconds = :currency_cache_ttl_seconds,
                    fallback_usd_to_rub = :fallback_usd_to_rub,
                    fallback_eur_to_rub = :fallback_eur_to_rub,
                    telegram_alerts_enabled = :telegram_alerts_enabled,
                    telegram_min_alert_level = :telegram_min_alert_level,
                    web_table_limit = :web_table_limit,
                    default_roi_percent = :default_roi_percent,
                    default_market_fee_percent = :default_market_fee_percent,
                    default_min_profit_rub = :default_min_profit_rub,
                    default_min_roi_percent = :default_min_roi_percent,
                    selected_exteriors = :selected_exteriors,
                    anomaly_config = :anomaly_config,
                    telegram_config = :telegram_config,
                    scan_queue_config = :scan_queue_config,
                    browser_optimization_config = :browser_optimization_config,
                    scan_optimization_config = :scan_optimization_config,
                    history_optimization_config = :history_optimization_config,
                    steam_guard_config = :steam_guard_config,
                    market_risk_config = :market_risk_config,
                    pack_detection_config = :pack_detection_config,
                    capital_config = :capital_config,
                    craft_context_config = :craft_context_config,
                    updated_at = :updated_at
                WHERE id = 1
                """,
                self._settings_params(settings),
            )

    def _settings_params(self, settings: ParserSettings) -> dict[str, Any]:
        data = asdict(settings)
        data["enabled"] = int(settings.enabled)
        data["headless"] = int(settings.headless)
        data["telegram_alerts_enabled"] = int(settings.telegram_alerts_enabled)
        return data

    def list_collections(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT c.*,
                       COUNT(i.id) AS item_count,
                       MAX(i.last_parsed_at) AS last_parsed_at
                FROM collections c
                LEFT JOIN items i ON i.collection_id = c.id
                GROUP BY c.id
                ORDER BY c.name
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_collection(self, collection_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM collections WHERE id = ?", (collection_id,)
            ).fetchone()
        return row_to_dict(row)

    def save_collection(self, collection: Collection) -> None:
        collection.updated_at = utc_now_iso()
        if not collection.created_at:
            collection.created_at = collection.updated_at
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO collections (
                    id, name, steam_collection_url, enabled, created_at, updated_at
                )
                VALUES (:id, :name, :steam_collection_url, :enabled, :created_at, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    steam_collection_url = excluded.steam_collection_url,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                {
                    **asdict(collection),
                    "enabled": int(collection.enabled),
                },
            )

    def delete_collection(self, collection_id: str) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM collections WHERE id = ?", (collection_id,))

    def list_items(self, collection_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if collection_id:
            where = "WHERE i.collection_id = ?"
            params.append(collection_id)

        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT i.*,
                       c.name AS collection_name,
                       r.id AS rule_id,
                       r.enabled AS rule_enabled,
                       r.max_buy_price_rub,
                       r.target_resale_price_rub,
                       r.custom_roi_percent,
                       r.min_profit_rub,
                       r.min_roi_percent,
                       r.float_min,
                       r.float_max,
                       r.target_float_min,
                       r.target_float_max,
                       r.pattern_ranges,
                       r.priority,
                       r.telegram_alert_enabled,
                       (
                           SELECT MIN(ml.buy_price_rub)
                           FROM market_listings ml
                           WHERE ml.item_definition_id = i.id
                             AND ml.is_active = 1
                       ) AS current_price_rub,
                       (
                           SELECT ml.currency_source
                           FROM market_listings ml
                           WHERE ml.item_definition_id = i.id
                             AND ml.is_active = 1
                           ORDER BY ml.buy_price_rub ASC
                           LIMIT 1
                       ) AS currency_source
                FROM items i
                JOIN collections c ON c.id = i.collection_id
                LEFT JOIN sniping_rules r ON r.item_definition_id = i.id
                {where}
                ORDER BY c.name, i.display_name, i.market_hash_name
                """,
                params,
            ).fetchall()
            items = [dict(row) for row in rows]
            self._add_item_price_metrics(connection, items)
        return items

    def _add_item_price_metrics(self, connection: sqlite3.Connection, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        placeholders = ",".join("?" for _ in items)
        item_by_id = {str(item["id"]): item for item in items}
        rows = connection.execute(
            f"""
            SELECT item_definition_id, buy_price_rub, float_value, pattern, currency_source
            FROM market_listings
            WHERE is_active = 1
              AND item_definition_id IN ({placeholders})
            ORDER BY item_definition_id, buy_price_rub ASC, id ASC
            """,
            list(item_by_id.keys()),
        ).fetchall()
        listings_by_item: dict[str, list[dict[str, Any]]] = {item_id: [] for item_id in item_by_id}
        for row in rows:
            listings_by_item[str(row["item_definition_id"])].append(dict(row))

        for item_id, item in item_by_id.items():
            listings = listings_by_item.get(item_id, [])
            hard_rows = [listing for listing in listings if _listing_matches_hard_rule(listing, item)]
            target_rows = [listing for listing in hard_rows if _listing_matches_target_float(listing, item)]
            all_prices = _listing_prices(listings)
            hard_prices = _listing_prices(hard_rows)
            target_prices = _listing_prices(target_rows)
            floats = [
                float(listing["float_value"])
                for listing in listings
                if listing.get("float_value") is not None
            ]
            item["current_price_rub"] = min(all_prices) if all_prices else None
            item["median_price_rub"] = float(median(all_prices)) if all_prices else None
            item["rule_floor_rub"] = min(hard_prices) if hard_prices else None
            item["target_floor_rub"] = min(target_prices) if target_prices else None
            item["active_listing_count"] = len(listings)
            item["rule_listing_count"] = len(hard_rows)
            item["target_listing_count"] = len(target_rows)
            item["best_float_seen"] = min(floats) if floats else None
            if listings:
                item["currency_source"] = str(listings[0].get("currency_source") or "")

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return row_to_dict(row)

    def save_item(self, item: ItemDefinition) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO items (
                    id, collection_id, market_hash_name, display_name, weapon_type,
                    rarity, quality, exterior, is_souvenir, is_stattrak,
                    steam_market_url, enabled, last_parsed_at, last_scanned_at
                )
                VALUES (
                    :id, :collection_id, :market_hash_name, :display_name, :weapon_type,
                    :rarity, :quality, :exterior, :is_souvenir, :is_stattrak,
                    :steam_market_url, :enabled, :last_parsed_at, :last_scanned_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    collection_id = excluded.collection_id,
                    market_hash_name = excluded.market_hash_name,
                    display_name = excluded.display_name,
                    weapon_type = excluded.weapon_type,
                    rarity = excluded.rarity,
                    quality = excluded.quality,
                    exterior = excluded.exterior,
                    is_souvenir = excluded.is_souvenir,
                    is_stattrak = excluded.is_stattrak,
                    steam_market_url = excluded.steam_market_url,
                    enabled = excluded.enabled,
                    last_parsed_at = excluded.last_parsed_at,
                    last_scanned_at = excluded.last_scanned_at
                """,
                {
                    **asdict(item),
                    "enabled": int(item.enabled),
                    "is_souvenir": int(item.is_souvenir),
                    "is_stattrak": int(item.is_stattrak),
                },
            )
        self.ensure_rule_for_item(item.id)

    def update_item_enabled(self, item_id: str, enabled: bool) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE items SET enabled = ? WHERE id = ?", (int(enabled), item_id)
            )

    def delete_item(self, item_id: str) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM items WHERE id = ?", (item_id,))

    def ensure_rule_for_item(self, item_id: str) -> str:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT id FROM sniping_rules WHERE item_definition_id = ? LIMIT 1",
                (item_id,),
            ).fetchone()
            if row:
                return str(row["id"])
            rule = SnipingRule(id=f"{item_id}_rule", item_definition_id=item_id)
            connection.execute(
                """
                INSERT INTO sniping_rules (
                    id, item_definition_id, enabled, max_buy_price_rub,
                    target_resale_price_rub, custom_roi_percent, min_profit_rub,
                    min_roi_percent, float_min, float_max, target_float_min,
                    target_float_max, pattern_ranges, priority,
                    telegram_alert_enabled, notes
                )
                VALUES (
                    :id, :item_definition_id, :enabled, :max_buy_price_rub,
                    :target_resale_price_rub, :custom_roi_percent, :min_profit_rub,
                    :min_roi_percent, :float_min, :float_max, :target_float_min,
                    :target_float_max, :pattern_ranges, :priority,
                    :telegram_alert_enabled, :notes
                )
                """,
                self._rule_params(rule),
            )
            return rule.id

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM sniping_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        return row_to_dict(row)

    def get_rule_for_item(self, item_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM sniping_rules WHERE item_definition_id = ? LIMIT 1",
                (item_id,),
            ).fetchone()
        return row_to_dict(row)

    def list_rules(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM sniping_rules ORDER BY priority DESC, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_rule(self, rule: SnipingRule) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO sniping_rules (
                    id, item_definition_id, enabled, max_buy_price_rub,
                    target_resale_price_rub, custom_roi_percent, min_profit_rub,
                    min_roi_percent, float_min, float_max, target_float_min,
                    target_float_max, pattern_ranges, priority,
                    telegram_alert_enabled, notes
                )
                VALUES (
                    :id, :item_definition_id, :enabled, :max_buy_price_rub,
                    :target_resale_price_rub, :custom_roi_percent, :min_profit_rub,
                    :min_roi_percent, :float_min, :float_max, :target_float_min,
                    :target_float_max, :pattern_ranges, :priority,
                    :telegram_alert_enabled, :notes
                )
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    max_buy_price_rub = excluded.max_buy_price_rub,
                    target_resale_price_rub = excluded.target_resale_price_rub,
                    custom_roi_percent = excluded.custom_roi_percent,
                    min_profit_rub = excluded.min_profit_rub,
                    min_roi_percent = excluded.min_roi_percent,
                    float_min = excluded.float_min,
                    float_max = excluded.float_max,
                    target_float_min = excluded.target_float_min,
                    target_float_max = excluded.target_float_max,
                    pattern_ranges = excluded.pattern_ranges,
                    priority = excluded.priority,
                    telegram_alert_enabled = excluded.telegram_alert_enabled,
                    notes = excluded.notes
                """,
                self._rule_params(rule),
            )

    def _rule_params(self, rule: SnipingRule) -> dict[str, Any]:
        data = asdict(rule)
        data["enabled"] = int(rule.enabled)
        data["telegram_alert_enabled"] = int(rule.telegram_alert_enabled)
        return data

    def build_scan_targets(
        self,
        collection_id: str | None = None,
        item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["i.enabled = 1", "c.enabled = 1"]
        params: list[Any] = []
        if collection_id:
            clauses.append("c.id = ?")
            params.append(collection_id)
        if item_id:
            clauses.append("i.id = ?")
            params.append(item_id)
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT i.*, c.name AS collection_name, r.id AS rule_id,
                       r.enabled AS rule_enabled, r.priority,
                       r.max_buy_price_rub, r.float_min, r.float_max, r.pattern_ranges
                FROM items i
                JOIN collections c ON c.id = i.collection_id
                LEFT JOIN sniping_rules r ON r.item_definition_id = i.id
                WHERE {' AND '.join(clauses)}
                ORDER BY c.name, i.market_hash_name
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def select_scan_targets(
        self,
        settings: ParserSettings,
        collection_id: str | None = None,
        item_id: str | None = None,
    ) -> ScanTargetSelection:
        rows = self.build_scan_targets(collection_id=collection_id, item_id=item_id)
        if item_id:
            return ScanTargetSelection(targets=rows, selected_targets_count=len(rows))

        queue = settings.scan_queue_settings
        if not queue.enabled:
            return ScanTargetSelection(targets=rows, selected_targets_count=len(rows))

        now = utc_now()
        eligible: list[dict[str, Any]] = []
        skipped_item = 0
        skipped_collection = 0
        collection_latest: dict[str, Any] = {}
        for row in rows:
            collection_key = str(row.get("collection_id") or "")
            last = parse_dt(row.get("last_scanned_at") or row.get("last_parsed_at"))
            current_latest = collection_latest.get(collection_key)
            if last is not None and (current_latest is None or last > current_latest):
                collection_latest[collection_key] = last

        for row in rows:
            last = parse_dt(row.get("last_scanned_at") or row.get("last_parsed_at"))
            if (
                queue.item_cooldown_seconds > 0
                and last is not None
                and now - last < timedelta(seconds=queue.item_cooldown_seconds)
            ):
                skipped_item += 1
                continue
            collection_key = str(row.get("collection_id") or "")
            collection_last = collection_latest.get(collection_key)
            if (
                queue.collection_cooldown_seconds > 0
                and collection_last is not None
                and now - collection_last < timedelta(seconds=queue.collection_cooldown_seconds)
            ):
                skipped_collection += 1
                continue
            eligible.append(row)

        def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
            priority = int(row.get("priority") or 0)
            last = parse_dt(row.get("last_scanned_at") or row.get("last_parsed_at"))
            last_key = last.timestamp() if last is not None else 0
            return (
                -priority if queue.priority_first else 0,
                last_key if queue.rotate_by_last_parsed_at else 0,
                random.random() if queue.random_jitter else 0,
                str(row.get("market_hash_name") or ""),
            )

        ordered = sorted(eligible, key=sort_key)
        selected = ordered[: queue.max_items_per_cycle]
        skipped_queue = max(0, len(eligible) - len(selected))
        return ScanTargetSelection(
            targets=selected,
            selected_targets_count=len(selected),
            skipped_by_queue_count=skipped_queue,
            skipped_by_item_cooldown_count=skipped_item,
            skipped_by_collection_cooldown_count=skipped_collection,
        )

    def scan_target_summary(
        self,
        collection_id: str | None = None,
        item_id: str | None = None,
    ) -> dict[str, int]:
        item_clauses: list[str] = []
        item_params: list[Any] = []
        collection_clauses: list[str] = []
        collection_params: list[Any] = []

        if collection_id:
            item_clauses.append("i.collection_id = ?")
            item_params.append(collection_id)
            collection_clauses.append("id = ?")
            collection_params.append(collection_id)
        if item_id:
            item_clauses.append("i.id = ?")
            item_params.append(item_id)

        item_where = f"WHERE {' AND '.join(item_clauses)}" if item_clauses else ""
        collection_where = f"WHERE {' AND '.join(collection_clauses)}" if collection_clauses else ""
        and_item_where = f"AND {' AND '.join(item_clauses)}" if item_clauses else ""

        with self.connection() as connection:
            total_collections = connection.execute(
                f"SELECT COUNT(*) AS count FROM collections {collection_where}",
                collection_params,
            ).fetchone()["count"]
            enabled_collections = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM collections
                {collection_where + (' AND' if collection_where else 'WHERE')} enabled = 1
                """,
                collection_params,
            ).fetchone()["count"]
            total_items = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM items i
                {item_where}
                """,
                item_params,
            ).fetchone()["count"]
            enabled_items = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM items i
                WHERE i.enabled = 1
                {and_item_where}
                """,
                item_params,
            ).fetchone()["count"]
            scan_targets = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM items i
                JOIN collections c ON c.id = i.collection_id
                WHERE i.enabled = 1
                  AND c.enabled = 1
                {and_item_where}
                """,
                item_params,
            ).fetchone()["count"]
            items_blocked_by_disabled_collection = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM items i
                JOIN collections c ON c.id = i.collection_id
                WHERE i.enabled = 1
                  AND c.enabled = 0
                {and_item_where}
                """,
                item_params,
            ).fetchone()["count"]
            items_blocked_by_disabled_item = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM items i
                JOIN collections c ON c.id = i.collection_id
                WHERE i.enabled = 0
                  AND c.enabled = 1
                {and_item_where}
                """,
                item_params,
            ).fetchone()["count"]

        return {
            "total_collections": int(total_collections),
            "enabled_collections": int(enabled_collections),
            "total_items": int(total_items),
            "enabled_items": int(enabled_items),
            "scan_targets": int(scan_targets),
            "items_blocked_by_disabled_collection": int(items_blocked_by_disabled_collection),
            "items_blocked_by_disabled_item": int(items_blocked_by_disabled_item),
        }

    def mark_listings_inactive_for_items(self, item_ids: list[str]) -> None:
        if not item_ids:
            return
        placeholders = ",".join("?" for _ in item_ids)
        with self.connection() as connection:
            connection.execute(
                f"""
                UPDATE market_listings
                SET is_active = 0
                WHERE item_definition_id IN ({placeholders})
                """,
                item_ids,
            )

    def expire_candidates_for_inactive_listings(self) -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE candidates
                SET status = 'expired', updated_at = ?
                WHERE status = 'new'
                  AND listing_id IN (
                      SELECT id FROM market_listings WHERE is_active = 0
                  )
                """,
                (utc_now_iso(),),
            )
            return cursor.rowcount

    def save_listing(self, listing: MarketListing) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO market_listings (
                    id, item_definition_id, rule_id, skin_name, market_hash_name,
                    listing_url, search_url, buy_price_rub, buy_price_original,
                    currency_original, currency_rate, currency_source, currency_fetched_at, float_value,
                    pattern, wear_name, raw_text, first_seen_at, last_seen_at,
                    is_active, parse_status
                )
                VALUES (
                    :id, :item_definition_id, :rule_id, :skin_name, :market_hash_name,
                    :listing_url, :search_url, :buy_price_rub, :buy_price_original,
                    :currency_original, :currency_rate, :currency_source, :currency_fetched_at, :float_value,
                    :pattern, :wear_name, :raw_text, :first_seen_at, :last_seen_at,
                    :is_active, :parse_status
                )
                ON CONFLICT(id) DO UPDATE SET
                    rule_id = excluded.rule_id,
                    buy_price_rub = excluded.buy_price_rub,
                    buy_price_original = excluded.buy_price_original,
                    currency_original = excluded.currency_original,
                    currency_rate = excluded.currency_rate,
                    currency_source = excluded.currency_source,
                    currency_fetched_at = excluded.currency_fetched_at,
                    float_value = excluded.float_value,
                    pattern = excluded.pattern,
                    wear_name = excluded.wear_name,
                    raw_text = excluded.raw_text,
                    last_seen_at = excluded.last_seen_at,
                    is_active = excluded.is_active,
                    parse_status = excluded.parse_status
                """,
                {
                    **asdict(listing),
                    "is_active": int(listing.is_active),
                },
            )
            connection.execute(
                "UPDATE items SET last_parsed_at = ?, last_scanned_at = ? WHERE id = ?",
                (listing.last_seen_at, listing.last_seen_at, listing.item_definition_id),
            )

    def save_candidate(self, candidate: Candidate) -> None:
        with self.connection() as connection:
            self._upsert_candidate_in_connection(connection, candidate)

    def _upsert_candidate_in_connection(
        self,
        connection: sqlite3.Connection,
        candidate: Candidate,
        now: str | None = None,
    ) -> None:
        candidate.updated_at = now or utc_now_iso()
        existing = connection.execute(
            "SELECT created_at, status FROM candidates WHERE listing_id = ?",
            (candidate.listing_id,),
        ).fetchone()
        if existing:
            candidate.created_at = existing["created_at"]
            if existing["status"] != "new":
                candidate.status = existing["status"]
        params = asdict(candidate)
        params["exact_item_match"] = int(candidate.exact_item_match)
        params["requires_sweep"] = int(candidate.requires_sweep)
        params["manual_review_required"] = int(candidate.manual_review_required)
        columns = [
            "id",
            "listing_id",
            "rule_id",
            "buy_price_rub",
            "estimated_resale_price_rub",
            "estimated_net_resale_rub",
            "estimated_profit_rub",
            "estimated_roi_percent",
            "market_fee_percent",
            "recommendation_level",
            "recommendation_score",
            "recommendation_reason",
            "analysis_mode",
            "alert_level",
            "anomaly_score",
            "fair_price_rub",
            "local_median_rub",
            "float_peer_median_rub",
            "historical_baseline_rub",
            "local_discount_percent",
            "float_peer_discount_percent",
            "historical_discount_percent",
            "robust_z",
            "float_bucket",
            "exact_item_match",
            "sample_size",
            "neighbor_count",
            "anomaly_reasons",
            "anomaly_baseline_price_rub",
            "exit_price_rub",
            "exit_price_model",
            "solo_exit_price_rub",
            "sweep_exit_price_rub",
            "market_confidence",
            "liquidity_score",
            "requires_sweep",
            "manual_review_required",
            "pack_id",
            "pack_size",
            "pack_cost_rub",
            "pack_floor_after_rub",
            "capital_required_rub",
            "substitute_floor_rub",
            "substitute_cap_rub",
            "raw_anomaly_score",
            "risk_adjusted_score",
            "parsed_at",
            "status",
            "created_at",
            "updated_at",
        ]
        placeholders = ", ".join(f":{column}" for column in columns)
        assignments = ",\n                    ".join(
            f"{column} = excluded.{column}"
            for column in columns
            if column not in {"id", "listing_id", "created_at"}
        )
        connection.execute(
            f"""
            INSERT INTO candidates (
                {", ".join(columns)}
            )
            VALUES (
                {placeholders}
            )
            ON CONFLICT(listing_id) DO UPDATE SET
                    {assignments}
            """,
            params,
        )

    def save_item_scan_success(
        self,
        run_id: str,
        item_id: str,
        listings: list[MarketListing],
        candidates: list[Candidate],
        snapshots: list[MarketSnapshot] | None = None,
        snapshot_alpha: float = 0.25,
        item_result: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        now = utc_now_iso()
        snapshots = snapshots or []
        item_result = item_result or {}
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE market_listings
                SET is_active = 0
                WHERE item_definition_id = ?
                """,
                (item_id,),
            )
            for listing in listings:
                connection.execute(
                    """
                    INSERT INTO market_listings (
                        id, item_definition_id, rule_id, skin_name, market_hash_name,
                        listing_url, search_url, buy_price_rub, buy_price_original,
                        currency_original, currency_rate, currency_source, currency_fetched_at, float_value,
                        pattern, wear_name, raw_text, first_seen_at, last_seen_at,
                        is_active, parse_status
                    )
                    VALUES (
                        :id, :item_definition_id, :rule_id, :skin_name, :market_hash_name,
                        :listing_url, :search_url, :buy_price_rub, :buy_price_original,
                        :currency_original, :currency_rate, :currency_source, :currency_fetched_at, :float_value,
                        :pattern, :wear_name, :raw_text, :first_seen_at, :last_seen_at,
                        :is_active, :parse_status
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        rule_id = excluded.rule_id,
                        buy_price_rub = excluded.buy_price_rub,
                        buy_price_original = excluded.buy_price_original,
                        currency_original = excluded.currency_original,
                        currency_rate = excluded.currency_rate,
                        currency_source = excluded.currency_source,
                        currency_fetched_at = excluded.currency_fetched_at,
                        float_value = excluded.float_value,
                        pattern = excluded.pattern,
                        wear_name = excluded.wear_name,
                        raw_text = excluded.raw_text,
                        last_seen_at = excluded.last_seen_at,
                        is_active = excluded.is_active,
                        parse_status = excluded.parse_status
                    """,
                    {
                        **asdict(listing),
                        "is_active": int(listing.is_active),
                    },
                )
            for candidate in candidates:
                self._upsert_candidate_in_connection(connection, candidate, now=now)
            current_listing_ids = {listing.id for listing in listings}
            analyzed_listing_ids = {candidate.listing_id for candidate in candidates}
            stale_candidate_listing_ids = sorted(current_listing_ids - analyzed_listing_ids)
            if stale_candidate_listing_ids:
                placeholders = ",".join("?" for _ in stale_candidate_listing_ids)
                connection.execute(
                    f"""
                    UPDATE candidates
                    SET recommendation_level = 'skip',
                        alert_level = 'skip',
                        recommendation_reason = 'hard rule filters no longer match current scan',
                        anomaly_reasons = CASE
                            WHEN COALESCE(anomaly_reasons, '') = ''
                                THEN 'hard rule filters no longer match current scan'
                            ELSE anomaly_reasons
                        END,
                        updated_at = ?
                    WHERE listing_id IN ({placeholders})
                      AND recommendation_level != 'skip'
                    """,
                    [now, *stale_candidate_listing_ids],
                )
            self._save_market_snapshots_in_connection(connection, snapshots, snapshot_alpha)
            last_seen_at = max((listing.last_seen_at for listing in listings), default=now)
            connection.execute(
                "UPDATE items SET last_parsed_at = ?, last_scanned_at = ? WHERE id = ?",
                (last_seen_at, now, item_id),
            )
            self._insert_scan_item_result(connection, run_id, item_id, item_result, now)
        skip_candidates_saved = sum(
            1 for candidate in candidates if candidate.recommendation_level == "skip"
        )
        analysis_rows_saved = len(candidates)
        actionable_candidates_saved = analysis_rows_saved - skip_candidates_saved
        return {
            "listings_saved": len(listings),
            "candidates_saved": actionable_candidates_saved,
            "analysis_rows_saved": analysis_rows_saved,
            "skip_candidates_saved": skip_candidates_saved,
        }

    def save_item_scan_failure(
        self,
        run_id: str,
        item_id: str,
        item_result: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        with self.connection() as connection:
            connection.execute(
                "UPDATE items SET last_scanned_at = ? WHERE id = ?",
                (now, item_id),
            )
            self._insert_scan_item_result(connection, run_id, item_id, item_result or {}, now)

    def _insert_scan_item_result(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        item_id: str,
        item_result: dict[str, Any],
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO scan_item_results (
                scan_run_id, item_id, status, cards_seen, exact_cards,
                target_listings_reached, early_stop_reason, shallow_gap_percent,
                deep_scan_performed, used_historical_baseline, rule_eligible_cards,
                target_float_cards, best_float_seen, hard_filter_rejection_counts,
                duration_ms, error, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                item_id,
                str(item_result.get("status") or "success"),
                int(item_result.get("cards_seen") or 0),
                int(item_result.get("exact_cards") or 0),
                int(bool(item_result.get("target_listings_reached"))),
                str(item_result.get("early_stop_reason") or ""),
                item_result.get("shallow_gap_percent"),
                int(bool(item_result.get("deep_scan_performed"))),
                int(bool(item_result.get("used_historical_baseline"))),
                int(item_result.get("rule_eligible_cards") or 0),
                int(item_result.get("target_float_cards") or 0),
                item_result.get("best_float_seen"),
                _json_text(item_result.get("hard_filter_rejection_counts") or {}),
                int(item_result.get("duration_ms") or 0),
                str(item_result.get("error") or "")[:2000],
                now,
                now,
            ),
        )

    def list_candidates(
        self,
        only_new: bool = False,
        level: str | None = None,
        status: str | None = None,
        collection_id: str | None = None,
        item_id: str | None = None,
        min_profit: float | None = None,
        min_roi: float | None = None,
        min_score: float | None = None,
        min_risk_adjusted_score: float | None = None,
        float_bucket: str | None = None,
        requires_sweep: bool | None = None,
        market_confidence: str | None = None,
        manual_review_required: bool | None = None,
        max_capital_required: float | None = None,
        souvenir_only: bool = False,
        exact_item_only: bool = False,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 200,
        sort: str = "time",
    ) -> list[dict[str, Any]]:
        clauses = ["c.recommendation_level != 'skip'"]
        params: list[Any] = []
        if only_new:
            clauses.append("c.status = 'new'")
        if level:
            clauses.append("c.recommendation_level = ?")
            params.append(level)
        if status:
            clauses.append("c.status = ?")
            params.append(status)
        if collection_id:
            clauses.append("col.id = ?")
            params.append(collection_id)
        if item_id:
            clauses.append("i.id = ?")
            params.append(item_id)
        if min_profit is not None:
            clauses.append("c.estimated_profit_rub >= ?")
            params.append(min_profit)
        if min_roi is not None:
            clauses.append("c.estimated_roi_percent >= ?")
            params.append(min_roi)
        if min_score is not None:
            clauses.append("COALESCE(c.risk_adjusted_score, c.anomaly_score, c.recommendation_score) >= ?")
            params.append(min_score)
        if min_risk_adjusted_score is not None:
            clauses.append("COALESCE(c.risk_adjusted_score, c.anomaly_score, c.recommendation_score) >= ?")
            params.append(min_risk_adjusted_score)
        if float_bucket:
            clauses.append("c.float_bucket = ?")
            params.append(float_bucket)
        if requires_sweep is not None:
            clauses.append("c.requires_sweep = ?")
            params.append(int(requires_sweep))
        if market_confidence:
            clauses.append("c.market_confidence = ?")
            params.append(market_confidence)
        if manual_review_required is not None:
            clauses.append("c.manual_review_required = ?")
            params.append(int(manual_review_required))
        if max_capital_required is not None:
            clauses.append("(c.capital_required_rub IS NULL OR c.capital_required_rub <= ?)")
            params.append(max_capital_required)
        if souvenir_only:
            clauses.append("i.is_souvenir = 1")
        if exact_item_only:
            clauses.append("c.exact_item_match = 1")
        if date_from:
            clauses.append("c.created_at >= ?")
            params.append(_date_start(date_from))
        if date_to:
            clauses.append("c.created_at <= ?")
            params.append(_date_end(date_to))

        order_by = {
            "profit": "c.estimated_profit_rub DESC",
            "roi": "c.estimated_roi_percent DESC",
            "price": "c.buy_price_rub ASC",
            "score": "COALESCE(c.risk_adjusted_score, c.anomaly_score, c.recommendation_score) DESC",
            "risk_score": "COALESCE(c.risk_adjusted_score, c.anomaly_score, c.recommendation_score) DESC",
            "capital": "COALESCE(c.capital_required_rub, c.buy_price_rub) ASC",
            "level": "CASE c.recommendation_level WHEN 'critical' THEN 1 WHEN 'good' THEN 2 WHEN 'watch' THEN 3 ELSE 4 END",
            "time": "c.created_at DESC",
        }.get(sort, "c.created_at DESC")

        params.append(limit)
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT c.*,
                       ml.skin_name, ml.market_hash_name, ml.listing_url, ml.search_url,
                       ml.float_value, ml.pattern, ml.currency_source, ml.currency_fetched_at, ml.last_seen_at,
                       i.id AS item_id, i.display_name, i.exterior, i.rarity, i.quality,
                       i.is_souvenir, i.is_stattrak,
                       col.id AS collection_id, col.name AS collection_name
                FROM candidates c
                JOIN market_listings ml ON ml.id = c.listing_id
                JOIN items i ON i.id = ml.item_definition_id
                JOIN collections col ON col.id = i.collection_id
                WHERE {' AND '.join(clauses)}
                ORDER BY {order_by}
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def update_candidate_status(self, candidate_id: str, status: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE candidates SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now_iso(), candidate_id),
            )

    def get_candidate_details(self, candidate_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT c.*,
                       ml.skin_name, ml.market_hash_name, ml.listing_url, ml.search_url,
                       ml.float_value, ml.pattern, ml.currency_source, ml.currency_fetched_at,
                       ml.buy_price_original, ml.currency_original, ml.currency_rate,
                       ml.raw_text, ml.first_seen_at, ml.last_seen_at, ml.parse_status,
                       i.id AS item_id, i.display_name, i.exterior, i.rarity, i.quality,
                       i.weapon_type, i.is_souvenir, i.is_stattrak,
                       col.id AS collection_id, col.name AS collection_name
                FROM candidates c
                JOIN market_listings ml ON ml.id = c.listing_id
                JOIN items i ON i.id = ml.item_definition_id
                JOIN collections col ON col.id = i.collection_id
                WHERE c.id = ?
                """,
                (candidate_id,),
            ).fetchone()
        return row_to_dict(row)

    def update_candidate_statuses(self, candidate_ids: list[str], status: str) -> int:
        if not candidate_ids:
            return 0
        placeholders = ",".join("?" for _ in candidate_ids)
        with self.connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE candidates
                SET status = ?, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [status, utc_now_iso(), *candidate_ids],
            )
            return cursor.rowcount

    def list_market_listings(
        self,
        collection_id: str | None = None,
        item_id: str | None = None,
        active_only: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if collection_id:
            clauses.append("c.id = ?")
            params.append(collection_id)
        if item_id:
            clauses.append("i.id = ?")
            params.append(item_id)
        if active_only:
            clauses.append("ml.is_active = 1")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT ml.*,
                       i.display_name, i.exterior,
                       c.name AS collection_name
                FROM market_listings ml
                JOIN items i ON i.id = ml.item_definition_id
                JOIN collections c ON c.id = i.collection_id
                {where}
                ORDER BY ml.last_seen_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_market_baselines(self, item_id: str, min_snapshots: int = 1) -> dict[str, float]:
        min_snapshots = max(1, int(min_snapshots or 1))
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT float_bucket, rolling_median_rub
                FROM market_baseline
                WHERE item_id = ?
                  AND rolling_median_rub IS NOT NULL
                  AND snapshot_count >= ?
                """,
                (item_id, min_snapshots),
            ).fetchall()
        return {str(row["float_bucket"]): float(row["rolling_median_rub"]) for row in rows}

    def list_market_baseline_rows(self, item_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM market_baseline
                WHERE item_id = ?
                ORDER BY float_bucket
                """,
                (item_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def substitute_price_context(
        self,
        item: dict[str, Any],
        target_float_max: float,
        premium_multiplier: float,
        min_sample: int = 3,
        same_collection_same_rarity: bool = True,
    ) -> dict[str, Any]:
        clauses = [
            "ml.is_active = 1",
            "i.id != ?",
            "i.collection_id = ?",
            "i.exterior = ?",
            "i.is_souvenir = ?",
            "ml.float_value IS NOT NULL",
            "ml.float_value <= ?",
            "ml.buy_price_rub > 0",
        ]
        params: list[Any] = [
            item.get("id"),
            item.get("collection_id"),
            item.get("exterior") or "",
            int(bool(item.get("is_souvenir"))),
            target_float_max,
        ]
        if same_collection_same_rarity and str(item.get("rarity") or "").strip():
            clauses.append("i.rarity = ?")
            params.append(str(item.get("rarity") or ""))
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT ml.buy_price_rub
                FROM market_listings ml
                JOIN items i ON i.id = ml.item_definition_id
                WHERE {' AND '.join(clauses)}
                ORDER BY ml.buy_price_rub ASC
                """,
                params,
            ).fetchall()
        prices = [float(row["buy_price_rub"]) for row in rows if row["buy_price_rub"] is not None]
        sample_size = len(prices)
        if not prices:
            return {"floor_rub": None, "median_rub": None, "sample_size": 0, "cap_rub": None}
        floor = min(prices)
        cap = round(floor * premium_multiplier, 2) if sample_size >= min_sample else None
        return {
            "floor_rub": round(floor, 2),
            "median_rub": round(float(median(prices)), 2),
            "sample_size": sample_size,
            "cap_rub": cap,
        }

    def list_market_snapshots(self, item_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM market_snapshot
                WHERE item_id = ?
                ORDER BY scan_time DESC, id DESC
                LIMIT ?
                """,
                (item_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_market_snapshots(
        self,
        snapshots: list[MarketSnapshot],
        alpha: float = 0.25,
    ) -> None:
        if not snapshots:
            return
        with self.connection() as connection:
            self._save_market_snapshots_in_connection(connection, snapshots, alpha)

    def _save_market_snapshots_in_connection(
        self,
        connection: sqlite3.Connection,
        snapshots: list[MarketSnapshot],
        alpha: float = 0.25,
    ) -> None:
        if not snapshots:
            return
        now = utc_now_iso()
        for snapshot in snapshots:
            connection.execute(
                """
                INSERT INTO market_snapshot (
                    item_id, scan_time, float_bucket, sample_size,
                    floor_price_rub, q10_price_rub, q25_price_rub,
                    median_price_rub, q75_price_rub
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.item_id,
                    now,
                    snapshot.float_bucket,
                    snapshot.sample_size,
                    snapshot.floor_price_rub,
                    snapshot.q10_price_rub,
                    snapshot.q25_price_rub,
                    snapshot.median_price_rub,
                    snapshot.q75_price_rub,
                ),
            )
            existing = connection.execute(
                """
                SELECT *
                FROM market_baseline
                WHERE item_id = ? AND float_bucket = ?
                """,
                (snapshot.item_id, snapshot.float_bucket),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE market_baseline
                    SET rolling_median_rub = ?,
                        rolling_q25_rub = ?,
                        rolling_floor_rub = ?,
                        sample_count = sample_count + ?,
                        snapshot_count = snapshot_count + 1,
                        updated_at = ?
                    WHERE item_id = ? AND float_bucket = ?
                    """,
                    (
                        ewma(existing["rolling_median_rub"], snapshot.median_price_rub, alpha),
                        ewma(existing["rolling_q25_rub"], snapshot.q25_price_rub, alpha),
                        ewma(existing["rolling_floor_rub"], snapshot.floor_price_rub, alpha),
                        snapshot.sample_size,
                        now,
                        snapshot.item_id,
                        snapshot.float_bucket,
                    ),
                )
                continue
            connection.execute(
                """
                INSERT INTO market_baseline (
                    item_id, float_bucket, rolling_median_rub,
                    rolling_q25_rub, rolling_floor_rub, sample_count, snapshot_count, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.item_id,
                    snapshot.float_bucket,
                    snapshot.median_price_rub,
                    snapshot.q25_price_rub,
                    snapshot.floor_price_rub,
                    snapshot.sample_size,
                    1,
                    now,
                ),
            )


    def rule_stats(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    r.id AS rule_id,
                    r.item_definition_id,
                    i.display_name,
                    i.market_hash_name,
                    COUNT(DISTINCT ml.id) AS listings_found,
                    COUNT(DISTINCT c.id) AS candidates_created,
                    SUM(CASE WHEN c.status = 'opened' THEN 1 ELSE 0 END) AS opened_count,
                    SUM(CASE WHEN c.status = 'bought_manually' THEN 1 ELSE 0 END) AS bought_count,
                    SUM(CASE WHEN c.status = 'skipped' THEN 1 ELSE 0 END) AS skipped_count,
                    SUM(CASE WHEN c.status = 'expired' THEN 1 ELSE 0 END) AS expired_count,
                    AVG(c.estimated_roi_percent) AS avg_roi_percent,
                    AVG(c.estimated_profit_rub) AS avg_profit_rub
                FROM sniping_rules r
                JOIN items i ON i.id = r.item_definition_id
                LEFT JOIN market_listings ml ON ml.rule_id = r.id
                LEFT JOIN candidates c ON c.rule_id = r.id
                GROUP BY r.id
                ORDER BY candidates_created DESC, listings_found DESC, r.priority DESC, r.id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def dashboard_stats(self) -> dict[str, Any]:
        with self.connection() as connection:
            candidates_total = connection.execute(
                "SELECT COUNT(*) AS count FROM candidates WHERE recommendation_level != 'skip'"
            ).fetchone()["count"]
            new_total = connection.execute(
                "SELECT COUNT(*) AS count FROM candidates WHERE status = 'new' AND recommendation_level != 'skip'"
            ).fetchone()["count"]
            by_level = {
                row["recommendation_level"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT recommendation_level, COUNT(*) AS count
                    FROM candidates
                    GROUP BY recommendation_level
                    """
                ).fetchall()
            }
            latest_parse = connection.execute(
                "SELECT MAX(last_seen_at) AS value FROM market_listings"
            ).fetchone()["value"]
            active_items = connection.execute(
                "SELECT COUNT(*) AS count FROM items WHERE enabled = 1"
            ).fetchone()["count"]
            active_collections = connection.execute(
                "SELECT COUNT(*) AS count FROM collections WHERE enabled = 1"
            ).fetchone()["count"]
        return {
            "candidates_total": candidates_total,
            "new_total": new_total,
            "critical_total": by_level.get("critical", 0),
            "good_total": by_level.get("good", 0),
            "watch_total": by_level.get("watch", 0),
            "latest_parse": latest_parse,
            "active_items": active_items,
            "active_collections": active_collections,
        }

    def save_currency_rate(
        self,
        usd_to_rub: float,
        eur_to_rub: float,
        source: str,
        fetched_at: str,
        is_fallback: bool,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO currency_rates (
                    usd_to_rub, eur_to_rub, source, fetched_at, is_fallback
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (usd_to_rub, eur_to_rub, source, fetched_at, int(is_fallback)),
            )

    def latest_currency_rate(self) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM currency_rates ORDER BY fetched_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return row_to_dict(row)

    def log_telegram_alert(self, candidate_id: str, status: str, error: str = "") -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO telegram_alerts (id, candidate_id, sent_at, status, error)
                VALUES (?, ?, ?, ?, ?)
                """,
                (new_id("tg"), candidate_id, utc_now_iso(), status, error),
            )

    def list_unsent_alert_candidates(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT c.*,
                       ml.skin_name, ml.market_hash_name, ml.listing_url, ml.search_url,
                       ml.float_value, ml.pattern, ml.currency_source, ml.currency_fetched_at,
                       i.display_name, col.name AS collection_name,
                       r.telegram_alert_enabled
                FROM candidates c
                JOIN market_listings ml ON ml.id = c.listing_id
                JOIN items i ON i.id = ml.item_definition_id
                JOIN collections col ON col.id = i.collection_id
                LEFT JOIN sniping_rules r ON r.id = c.rule_id
                LEFT JOIN telegram_alerts ta
                    ON ta.candidate_id = c.id AND ta.status = 'sent'
                WHERE c.status = 'new'
                  AND c.recommendation_level IN ('critical', 'good', 'watch')
                  AND COALESCE(r.telegram_alert_enabled, 1) = 1
                  AND ta.id IS NULL
                ORDER BY COALESCE(c.risk_adjusted_score, c.recommendation_score) DESC, c.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def log_user_action(self, entity_type: str, entity_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO user_actions (id, entity_type, entity_id, action, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("act"),
                    entity_type,
                    entity_id,
                    action,
                    json.dumps(payload or {}, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )

    def get_app_state(self, key: str, default: str = "") -> str:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else default

    def set_app_state(self, values: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self.connection() as connection:
            for key, value in values.items():
                connection.execute(
                    """
                    INSERT INTO app_state (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (str(key), str(value or ""), now),
                )

    def get_steam_guard_state(self) -> dict[str, Any]:
        keys = [
            "steam_cooldown_until",
            "steam_cooldown_reason",
            "steam_consecutive_blocks",
            "last_steam_error_at",
        ]
        with self.connection() as connection:
            rows = connection.execute(
                f"SELECT key, value FROM app_state WHERE key IN ({','.join('?' for _ in keys)})",
                keys,
            ).fetchall()
        state = {
            "steam_cooldown_until": "",
            "steam_cooldown_reason": "",
            "steam_consecutive_blocks": 0,
            "last_steam_error_at": "",
        }
        for row in rows:
            state[str(row["key"])] = row["value"]
        try:
            state["steam_consecutive_blocks"] = int(state.get("steam_consecutive_blocks") or 0)
        except Exception:
            state["steam_consecutive_blocks"] = 0
        return state

    def set_steam_guard_state(
        self,
        cooldown_until: str = "",
        reason: str = "",
        consecutive_blocks: int | None = None,
        last_error_at: str = "",
    ) -> None:
        values: dict[str, Any] = {
            "steam_cooldown_until": cooldown_until,
            "steam_cooldown_reason": reason,
            "last_steam_error_at": last_error_at,
        }
        if consecutive_blocks is not None:
            values["steam_consecutive_blocks"] = int(consecutive_blocks)
        self.set_app_state(values)

    def reset_steam_guard_state(self) -> None:
        self.set_steam_guard_state(
            cooldown_until="",
            reason="",
            consecutive_blocks=0,
            last_error_at="",
        )

    def start_scan_run(
        self,
        trigger: str,
        collection_id: str | None = None,
        item_id: str | None = None,
    ) -> str:
        run_id = new_id("scan")
        now = utc_now_iso()
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO scan_runs (
                    id, trigger, collection_id, item_id, status, started_at,
                    progress_message, updated_at
                )
                VALUES (?, ?, ?, ?, 'running', ?, 'Подготовка скана', ?)
                """,
                (run_id, trigger, collection_id, item_id, now, now),
            )
        return run_id

    def update_scan_run_progress(
        self,
        run_id: str,
        total_items: int | None = None,
        current_item_index: int | None = None,
        current_item_name: str | None = None,
        progress_message: str | None = None,
        scanned_items: int | None = None,
        listings_saved: int | None = None,
        candidates_saved: int | None = None,
        analysis_rows_saved: int | None = None,
        skip_candidates_saved: int | None = None,
    ) -> None:
        updates: list[str] = ["updated_at = ?"]
        params: list[Any] = [utc_now_iso()]
        values = {
            "total_items": total_items,
            "current_item_index": current_item_index,
            "current_item_name": current_item_name[:500] if current_item_name is not None else None,
            "progress_message": progress_message[:1000] if progress_message is not None else None,
            "scanned_items": scanned_items,
            "listings_saved": listings_saved,
            "candidates_saved": candidates_saved,
            "analysis_rows_saved": analysis_rows_saved,
            "skip_candidates_saved": skip_candidates_saved,
        }
        for column, value in values.items():
            if value is None:
                continue
            updates.append(f"{column} = ?")
            params.append(value)
        params.append(run_id)
        with self.connection() as connection:
            connection.execute(
                f"UPDATE scan_runs SET {', '.join(updates)} WHERE id = ?",
                params,
            )

    def finish_scan_run(
        self,
        run_id: str,
        status: str,
        scanned_items: int = 0,
        listings_saved: int = 0,
        candidates_saved: int = 0,
        analysis_rows_saved: int = 0,
        skip_candidates_saved: int = 0,
        alerts_sent: int = 0,
        error: str = "",
        selected_targets_count: int = 0,
        skipped_by_queue_count: int = 0,
        skipped_by_item_cooldown_count: int = 0,
        skipped_by_collection_cooldown_count: int = 0,
        early_stop_count: int = 0,
        resource_blocked_count: int = 0,
        shallow_skipped_count: int = 0,
        deep_scan_count: int = 0,
        steam_cooldown_active: bool = False,
        steam_cooldown_until: str = "",
    ) -> None:
        message = error or ("Скан завершён." if status == "success" else f"Скан: {status}.")
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE scan_runs
                SET status = ?,
                    finished_at = ?,
                    current_item_index = CASE
                        WHEN ? = 'success' AND total_items > 0 THEN total_items
                        ELSE current_item_index
                    END,
                    progress_message = ?,
                    updated_at = ?,
                    scanned_items = ?,
                    listings_saved = ?,
                    candidates_saved = ?,
                    analysis_rows_saved = ?,
                    skip_candidates_saved = ?,
                    alerts_sent = ?,
                    selected_targets_count = ?,
                    skipped_by_queue_count = ?,
                    skipped_by_item_cooldown_count = ?,
                    skipped_by_collection_cooldown_count = ?,
                    early_stop_count = ?,
                    resource_blocked_count = ?,
                    shallow_skipped_count = ?,
                    deep_scan_count = ?,
                    steam_cooldown_active = ?,
                    steam_cooldown_until = ?,
                    error = ?
                WHERE id = ?
                """,
                (
                    status,
                    utc_now_iso(),
                    status,
                    message[:1000],
                    utc_now_iso(),
                    int(scanned_items),
                    int(listings_saved),
                    int(candidates_saved),
                    int(analysis_rows_saved),
                    int(skip_candidates_saved),
                    int(alerts_sent),
                    int(selected_targets_count),
                    int(skipped_by_queue_count),
                    int(skipped_by_item_cooldown_count),
                    int(skipped_by_collection_cooldown_count),
                    int(early_stop_count),
                    int(resource_blocked_count),
                    int(shallow_skipped_count),
                    int(deep_scan_count),
                    int(steam_cooldown_active),
                    steam_cooldown_until,
                    error[:2000],
                    run_id,
                ),
            )

    def latest_scan_run(self) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return row_to_dict(row)

    def list_scan_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_scan_item_results(self, scan_run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if scan_run_id:
            clauses.append("sir.scan_run_id = ?")
            params.append(scan_run_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT sir.*, i.display_name, i.market_hash_name
                FROM scan_item_results sir
                LEFT JOIN items i ON i.id = sir.item_id
                {where}
                ORDER BY sir.created_at DESC, sir.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_config(
        self,
        settings: ParserSettings,
        collections: list[Collection],
        items: list[ItemDefinition],
        rules: list[SnipingRule],
    ) -> None:
        with self.connection() as connection:
            connection.execute("BEGIN")
            self._replace_config_in_connection(connection, settings, collections, items, rules)

    def _replace_config_in_connection(
        self,
        connection: sqlite3.Connection,
        settings: ParserSettings,
        collections: list[Collection],
        items: list[ItemDefinition],
        rules: list[SnipingRule],
    ) -> None:
        settings.updated_at = utc_now_iso()
        connection.execute("DELETE FROM sniping_rules")
        connection.execute("DELETE FROM items")
        connection.execute("DELETE FROM collections")
        connection.execute(
            """
            UPDATE settings SET
                enabled = :enabled,
                check_interval_seconds = :check_interval_seconds,
                headless = :headless,
                max_scrolls = :max_scrolls,
                request_delay_seconds = :request_delay_seconds,
                steam_block_pause_seconds = :steam_block_pause_seconds,
                currency_provider = :currency_provider,
                currency_cache_ttl_seconds = :currency_cache_ttl_seconds,
                fallback_usd_to_rub = :fallback_usd_to_rub,
                fallback_eur_to_rub = :fallback_eur_to_rub,
                telegram_alerts_enabled = :telegram_alerts_enabled,
                telegram_min_alert_level = :telegram_min_alert_level,
                web_table_limit = :web_table_limit,
                default_roi_percent = :default_roi_percent,
                default_market_fee_percent = :default_market_fee_percent,
                default_min_profit_rub = :default_min_profit_rub,
                default_min_roi_percent = :default_min_roi_percent,
                selected_exteriors = :selected_exteriors,
                anomaly_config = :anomaly_config,
                telegram_config = :telegram_config,
                scan_queue_config = :scan_queue_config,
                browser_optimization_config = :browser_optimization_config,
                scan_optimization_config = :scan_optimization_config,
                history_optimization_config = :history_optimization_config,
                steam_guard_config = :steam_guard_config,
                updated_at = :updated_at
            WHERE id = 1
            """,
            self._settings_params(settings),
        )
        for collection in collections:
            connection.execute(
                """
                INSERT INTO collections (
                    id, name, steam_collection_url, enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    collection.id,
                    collection.name,
                    collection.steam_collection_url,
                    int(collection.enabled),
                    collection.created_at or utc_now_iso(),
                    collection.updated_at or utc_now_iso(),
                ),
            )
        for item in items:
            connection.execute(
                """
                INSERT INTO items (
                    id, collection_id, market_hash_name, display_name, weapon_type,
                    rarity, quality, exterior, is_souvenir, is_stattrak,
                    steam_market_url, enabled, last_parsed_at, last_scanned_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.collection_id,
                    item.market_hash_name,
                    item.display_name,
                    item.weapon_type,
                    item.rarity,
                    item.quality,
                    item.exterior,
                    int(item.is_souvenir),
                    int(item.is_stattrak),
                    item.steam_market_url,
                    int(item.enabled),
                    item.last_parsed_at,
                    item.last_scanned_at,
                ),
            )
        for rule in rules:
            connection.execute(
                """
                INSERT INTO sniping_rules (
                    id, item_definition_id, enabled, max_buy_price_rub,
                    target_resale_price_rub, custom_roi_percent, min_profit_rub,
                    min_roi_percent, float_min, float_max, target_float_min,
                    target_float_max, pattern_ranges, priority,
                    telegram_alert_enabled, notes
                )
                VALUES (
                    :id, :item_definition_id, :enabled, :max_buy_price_rub,
                    :target_resale_price_rub, :custom_roi_percent, :min_profit_rub,
                    :min_roi_percent, :float_min, :float_max, :target_float_min,
                    :target_float_max, :pattern_ranges, :priority,
                    :telegram_alert_enabled, :notes
                )
                """,
                self._rule_params(rule),
            )
