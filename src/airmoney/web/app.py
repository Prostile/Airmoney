from __future__ import annotations

import os
import csv
import io
import secrets
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from airmoney.config.catalog_import import import_catalog_text
from airmoney.config.import_export import export_config, import_config_text
from airmoney.config.models import (
    CANDIDATE_STATUSES,
    Collection,
    EXTERIORS,
    ItemDefinition,
    ParserSettings,
    QUALITIES,
    RARITIES,
    SnipingRule,
    to_bool,
)
from airmoney.currency.cache import load_cached_rates
from airmoney.currency.steam_currency import CurrencyService
from airmoney.paths import PACKAGE_ROOT
from airmoney.scheduler.monitor import run_scan_cycle
from airmoney.scheduler.service import BackgroundMonitor
from airmoney.storage.repositories import Repository
from airmoney.steam.collections import build_exterior_variants, build_market_listing_url, slugify
from airmoney.telegram.notifier import load_dotenv


security = HTTPBasic()
templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "web" / "templates"))
WEB_PAGE_SIZE = 100


def create_app(repo: Repository | None = None) -> FastAPI:
    repository = repo or Repository()
    monitor = BackgroundMonitor(repository)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        monitor.start()
        try:
            yield
        finally:
            monitor.stop()

    app = FastAPI(
        title="Airmoney",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.repo = repository
    app.state.monitor = monitor
    app.mount(
        "/static",
        StaticFiles(directory=str(PACKAGE_ROOT / "web" / "static")),
        name="static",
    )

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/dashboard")

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, _: str = Depends(require_auth)):
        settings = repository.get_settings()
        cached_rates = load_cached_rates()
        if cached_rates is None:
            cached_rates = CurrencyService(settings).get_rates()
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "request": request,
                "active": "dashboard",
                "settings": settings,
                "stats": repository.dashboard_stats(),
                "latest_scan": repository.latest_scan_run(),
                "scan_runs": repository.list_scan_runs(limit=8),
                "scan_summary": repository.scan_target_summary(),
                "monitor": app.state.monitor.snapshot(),
                "rates": cached_rates,
                "message": request.query_params.get("message", ""),
            },
        )

    @app.get("/api/status")
    def api_status(_: str = Depends(require_auth)) -> dict[str, Any]:
        settings = repository.get_settings()
        monitor = app.state.monitor.snapshot()
        latest_scan = repository.latest_scan_run()
        stats = repository.dashboard_stats()
        rates = repository.latest_currency_rate()
        return {
            "parser_enabled": settings.enabled,
            "monitor_thread_alive": monitor.thread_alive,
            "scan_running": monitor.scan_running,
            "last_monitor_loop_at": monitor.last_loop_at,
            "last_monitor_error": monitor.last_error,
            "latest_scan": latest_scan,
            "stats": stats,
            "scan_summary": repository.scan_target_summary(),
            "currency": rates,
        }

    @app.post("/scan")
    def scan_now(_: str = Depends(require_auth)) -> RedirectResponse:
        try:
            result = run_scan_cycle(repository, trigger="web")
            message = result.message or f"scan: {result.scanned_items} items, {result.listings_saved} listings"
        except Exception as error:
            message = f"scan error: {error}"
        return RedirectResponse(f"/dashboard?message={urllib.parse.quote(message)}", status_code=303)

    @app.post("/monitor/start")
    def monitor_start(_: str = Depends(require_auth)) -> RedirectResponse:
        app.state.monitor.start()
        repository.log_user_action("monitor", "background", "start")
        return RedirectResponse("/dashboard?message=monitor_started", status_code=303)

    @app.post("/monitor/stop")
    def monitor_stop(_: str = Depends(require_auth)) -> RedirectResponse:
        app.state.monitor.stop()
        repository.log_user_action("monitor", "background", "stop")
        return RedirectResponse("/dashboard?message=monitor_stopped", status_code=303)

    @app.post("/currency/refresh")
    def refresh_currency(_: str = Depends(require_auth)) -> RedirectResponse:
        try:
            settings = repository.get_settings()
            rates = CurrencyService(settings).get_rates(force_refresh=True)
            repository.save_currency_rate(
                rates.usd_to_rub,
                rates.eur_to_rub,
                rates.source,
                rates.fetched_at_iso,
                rates.is_fallback,
            )
            message = "currency_refreshed"
        except Exception as error:
            message = "currency_error_" + urllib.parse.quote(str(error)[:80])
        return RedirectResponse(f"/dashboard?message={message}", status_code=303)

    @app.get("/scan-runs", response_class=HTMLResponse)
    def scan_runs_page(request: Request, page: int = 1, _: str = Depends(require_auth)):
        scan_runs, pagination = _paginate_rows(repository.list_scan_runs(limit=100000), page, request)
        return templates.TemplateResponse(
            request,
            "scan_runs.html",
            {
                "request": request,
                "active": "scan_runs",
                "scan_runs": scan_runs,
                "pagination": pagination,
                "scan_summary": repository.scan_target_summary(),
                "message": request.query_params.get("message", ""),
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, _: str = Depends(require_auth)):
        settings = repository.get_settings()
        return templates.TemplateResponse(
            request,
            "parser_settings.html",
            {
                "request": request,
                "active": "settings",
                "settings": settings,
                "anomaly": settings.anomaly_settings,
                "scan_queue": settings.scan_queue_settings,
                "browser_optimization": settings.browser_optimization_settings,
                "scan_optimization": settings.scan_optimization_settings,
                "history_optimization": settings.history_optimization_settings,
                "steam_guard": settings.steam_guard_settings,
                "market_risk": settings.market_risk_settings,
                "pack_detection": settings.pack_detection_settings,
                "capital": settings.capital_settings,
                "craft_context": settings.craft_context_settings,
                "steam_guard_state": repository.get_steam_guard_state(),
                "telegram_alert": settings.telegram_alert_settings,
                "exteriors": EXTERIORS,
                "exterior_field": _exterior_field,
                "message": request.query_params.get("message", ""),
            },
        )

    @app.post("/settings/steam-guard/reset")
    def reset_steam_guard(_: str = Depends(require_auth)) -> RedirectResponse:
        repository.reset_steam_guard_state()
        repository.log_user_action("steam_guard", "global", "reset")
        return RedirectResponse("/settings?message=steam_guard_reset", status_code=303)

    @app.post("/settings")
    async def save_settings(request: Request, _: str = Depends(require_auth)) -> RedirectResponse:
        form = await _form(request)
        current_settings = repository.get_settings()
        settings = ParserSettings(
            enabled=_checkbox(form, "enabled"),
            check_interval_seconds=_int(form, "check_interval_seconds", 300),
            headless=_checkbox(form, "headless"),
            max_scrolls=_int(form, "max_scrolls", 1),
            request_delay_seconds=_float(form, "request_delay_seconds", 2),
            steam_block_pause_seconds=_int(form, "steam_block_pause_seconds", 1800),
            currency_provider=str(form.get("currency_provider", "steam_currency")),
            currency_cache_ttl_seconds=_int(form, "currency_cache_ttl_seconds", 21600),
            fallback_usd_to_rub=_float(form, "fallback_usd_to_rub", 72),
            fallback_eur_to_rub=_float(form, "fallback_eur_to_rub", 86),
            telegram_alerts_enabled=_checkbox(form, "telegram_alerts_enabled"),
            telegram_min_alert_level=str(form.get("telegram_min_alert_level", "good")),
            web_table_limit=_int(form, "web_table_limit", 200),
            default_roi_percent=_float(form, "default_roi_percent", 12),
            default_market_fee_percent=_float(form, "default_market_fee_percent", 15),
            default_min_profit_rub=_float(form, "default_min_profit_rub", 300),
            default_min_roi_percent=_float(form, "default_min_roi_percent", 7),
        )
        settings.set_selected_exteriors(
            [exterior for exterior in EXTERIORS if _checkbox(form, _exterior_field(exterior))]
        )
        anomaly = current_settings.anomaly_settings
        anomaly.enabled = _checkbox(form, "anomaly_enabled")
        anomaly.sample.min_listings = _int(form, "anomaly_min_listings", anomaly.sample.min_listings)
        anomaly.sample.target_listings = _int(form, "anomaly_target_listings", anomaly.sample.target_listings)
        anomaly.sample.max_listings = _int(form, "anomaly_max_listings", anomaly.sample.max_listings)
        anomaly.sample.exclude_candidate_from_baseline = _checkbox(form, "anomaly_exclude_candidate")
        anomaly.sample.require_exact_item_match = _checkbox(form, "anomaly_require_exact_match")
        anomaly.sample.sort_by = str(form.get("anomaly_sort_by", anomaly.sample.sort_by) or "price_asc")
        if anomaly.sample.sort_by not in {"price_asc", "none"}:
            anomaly.sample.sort_by = "price_asc"
        anomaly.debug.save_skip_candidates = _checkbox(form, "anomaly_save_skip_candidates")
        anomaly.debug.log_rejected_exact_match = _checkbox(form, "anomaly_log_rejected_exact_match")
        anomaly.debug.max_rejected_exact_match_log = _int(
            form,
            "anomaly_max_rejected_exact_match_log",
            anomaly.debug.max_rejected_exact_match_log,
        )
        anomaly.thresholds.min_local_discount_percent = _float(
            form, "anomaly_min_local_discount_percent", anomaly.thresholds.min_local_discount_percent
        )
        anomaly.thresholds.min_float_peer_discount_percent = _float(
            form, "anomaly_min_float_peer_discount_percent", anomaly.thresholds.min_float_peer_discount_percent
        )
        anomaly.thresholds.min_net_profit_rub = _float(
            form, "anomaly_min_net_profit_rub", anomaly.thresholds.min_net_profit_rub
        )
        anomaly.thresholds.min_roi_percent = _float(
            form, "anomaly_min_roi_percent", anomaly.thresholds.min_roi_percent
        )
        anomaly.thresholds.critical_score = _float(form, "anomaly_critical_score", anomaly.thresholds.critical_score)
        anomaly.thresholds.good_score = _float(form, "anomaly_good_score", anomaly.thresholds.good_score)
        anomaly.thresholds.watch_score = _float(form, "anomaly_watch_score", anomaly.thresholds.watch_score)
        anomaly.scoring.local_discount_weight = _float(
            form, "anomaly_local_discount_weight", anomaly.scoring.local_discount_weight
        )
        anomaly.scoring.float_peer_discount_weight = _float(
            form, "anomaly_float_peer_discount_weight", anomaly.scoring.float_peer_discount_weight
        )
        anomaly.scoring.historical_discount_weight = _float(
            form, "anomaly_historical_discount_weight", anomaly.scoring.historical_discount_weight
        )
        anomaly.scoring.float_quality_weight = _float(
            form, "anomaly_float_quality_weight", anomaly.scoring.float_quality_weight
        )
        anomaly.nearest_neighbors.enabled = _checkbox(form, "anomaly_neighbors_enabled")
        anomaly.nearest_neighbors.k = _int(form, "anomaly_neighbors_k", anomaly.nearest_neighbors.k)
        anomaly.nearest_neighbors.min_neighbors = _int(
            form, "anomaly_neighbors_min_neighbors", anomaly.nearest_neighbors.min_neighbors
        )
        anomaly.nearest_neighbors.max_float_distance = _float(
            form, "anomaly_neighbors_max_float_distance", anomaly.nearest_neighbors.max_float_distance
        )
        anomaly_errors = _anomaly_sample_errors(anomaly)
        if anomaly_errors:
            message = "invalid_settings_" + "; ".join(anomaly_errors)
            return RedirectResponse(f"/settings?message={urllib.parse.quote(message[:180])}", status_code=303)
        settings.set_anomaly_settings(anomaly)

        scan_queue = current_settings.scan_queue_settings
        scan_queue.enabled = _checkbox(form, "scan_queue_enabled")
        scan_queue.max_items_per_cycle = _int(form, "scan_queue_max_items_per_cycle", scan_queue.max_items_per_cycle)
        scan_queue.item_cooldown_seconds = _int(form, "scan_queue_item_cooldown_seconds", scan_queue.item_cooldown_seconds)
        scan_queue.collection_cooldown_seconds = _int(
            form,
            "scan_queue_collection_cooldown_seconds",
            scan_queue.collection_cooldown_seconds,
        )
        scan_queue.priority_first = _checkbox(form, "scan_queue_priority_first")
        scan_queue.rotate_by_last_parsed_at = _checkbox(form, "scan_queue_rotate_by_last_parsed_at")
        scan_queue.random_jitter = _checkbox(form, "scan_queue_random_jitter")
        settings.set_scan_queue_settings(scan_queue)

        browser_optimization = current_settings.browser_optimization_settings
        browser_optimization.block_heavy_resources = _checkbox(form, "browser_block_heavy_resources")
        blocked_types = []
        if _checkbox(form, "browser_block_images"):
            blocked_types.append("image")
        if _checkbox(form, "browser_block_media"):
            blocked_types.append("media")
        if _checkbox(form, "browser_block_fonts"):
            blocked_types.append("font")
        browser_optimization.blocked_resource_types = blocked_types
        browser_optimization.block_stylesheets = _checkbox(form, "browser_block_stylesheets")
        settings.set_browser_optimization_settings(browser_optimization)

        scan_optimization = current_settings.scan_optimization_settings
        scan_optimization.two_stage_scan = _checkbox(form, "scan_optimization_two_stage_scan")
        scan_optimization.shallow_target_listings = _int(
            form,
            "scan_optimization_shallow_target_listings",
            scan_optimization.shallow_target_listings,
        )
        scan_optimization.shallow_min_gap_percent = _float(
            form,
            "scan_optimization_shallow_min_gap_percent",
            scan_optimization.shallow_min_gap_percent,
        )
        scan_optimization.deep_scan_on_gap = _checkbox(form, "scan_optimization_deep_scan_on_gap")
        settings.set_scan_optimization_settings(scan_optimization)

        history_optimization = current_settings.history_optimization_settings
        history_optimization.use_mature_history_for_shallow_scan = _checkbox(
            form,
            "history_use_mature_history_for_shallow_scan",
        )
        history_optimization.mature_history_min_snapshots = _int(
            form,
            "history_mature_history_min_snapshots",
            history_optimization.mature_history_min_snapshots,
        )
        history_optimization.mature_history_target_listings = _int(
            form,
            "history_mature_history_target_listings",
            history_optimization.mature_history_target_listings,
        )
        history_optimization.use_stale_baseline_on_scan_failure = _checkbox(
            form,
            "history_use_stale_baseline_on_scan_failure",
        )
        settings.set_history_optimization_settings(history_optimization)

        steam_guard = current_settings.steam_guard_settings
        steam_guard.enabled = _checkbox(form, "steam_guard_enabled")
        steam_guard.cooldown_on_limit_seconds = _int(
            form,
            "steam_guard_cooldown_on_limit_seconds",
            steam_guard.cooldown_on_limit_seconds,
        )
        steam_guard.max_cooldown_seconds = _int(
            form,
            "steam_guard_max_cooldown_seconds",
            steam_guard.max_cooldown_seconds,
        )
        steam_guard.backoff_multiplier = _float(form, "steam_guard_backoff_multiplier", steam_guard.backoff_multiplier)
        steam_guard.jitter_percent = _int(form, "steam_guard_jitter_percent", steam_guard.jitter_percent)
        steam_guard.retry_network_errors = _checkbox(form, "steam_guard_retry_network_errors")
        steam_guard.network_error_retry_delay_seconds = _int(
            form,
            "steam_guard_network_error_retry_delay_seconds",
            steam_guard.network_error_retry_delay_seconds,
        )
        steam_guard.max_network_retries = _int(form, "steam_guard_max_network_retries", steam_guard.max_network_retries)
        settings.set_steam_guard_settings(steam_guard)

        market_risk = current_settings.market_risk_settings
        market_risk.enabled = _checkbox(form, "market_risk_enabled")
        market_risk.conservative_exit_enabled = _checkbox(form, "market_risk_conservative_exit_enabled")
        market_risk.exit_price_strategy = str(form.get("market_risk_exit_price_strategy", market_risk.exit_price_strategy) or "conservative")
        market_risk.min_sample_for_good = _int(form, "market_risk_min_sample_for_good", market_risk.min_sample_for_good)
        market_risk.min_sample_for_critical = _int(
            form,
            "market_risk_min_sample_for_critical",
            market_risk.min_sample_for_critical,
        )
        market_risk.min_neighbor_for_good = _int(form, "market_risk_min_neighbor_for_good", market_risk.min_neighbor_for_good)
        market_risk.min_neighbor_for_critical = _int(
            form,
            "market_risk_min_neighbor_for_critical",
            market_risk.min_neighbor_for_critical,
        )
        market_risk.thin_market_max_level = str(form.get("market_risk_thin_market_max_level", market_risk.thin_market_max_level) or "good")
        market_risk.very_thin_market_max_level = str(
            form.get("market_risk_very_thin_market_max_level", market_risk.very_thin_market_max_level) or "watch"
        )
        market_risk.downgrade_if_requires_sweep = _checkbox(form, "market_risk_downgrade_if_requires_sweep")
        market_risk.sweep_max_level_without_capital = str(
            form.get("market_risk_sweep_max_level_without_capital", market_risk.sweep_max_level_without_capital) or "good"
        )
        settings.set_market_risk_settings(market_risk)

        pack_detection = current_settings.pack_detection_settings
        pack_detection.enabled = _checkbox(form, "pack_detection_enabled")
        pack_detection.min_gap_percent = _float(form, "pack_detection_min_gap_percent", pack_detection.min_gap_percent)
        pack_detection.min_pack_size = _int(form, "pack_detection_min_pack_size", pack_detection.min_pack_size)
        pack_detection.max_pack_size = _int(form, "pack_detection_max_pack_size", pack_detection.max_pack_size)
        pack_detection.alert_as_single_pack = _checkbox(form, "pack_detection_alert_as_single_pack")
        pack_detection.max_pack_to_sample_ratio = _float(
            form,
            "pack_detection_max_pack_to_sample_ratio",
            pack_detection.max_pack_to_sample_ratio,
        )
        settings.set_pack_detection_settings(pack_detection)

        capital = current_settings.capital_settings
        capital.enabled = _checkbox(form, "capital_enabled")
        capital.max_single_buy_rub = _float(form, "capital_max_single_buy_rub", capital.max_single_buy_rub)
        capital.max_bundle_cost_rub = _float(form, "capital_max_bundle_cost_rub", capital.max_bundle_cost_rub)
        capital.max_units_per_item = _int(form, "capital_max_units_per_item", capital.max_units_per_item)
        capital.warn_if_sweep_required = _checkbox(form, "capital_warn_if_sweep_required")
        settings.set_capital_settings(capital)

        craft_context = current_settings.craft_context_settings
        craft_context.enabled = _checkbox(form, "craft_context_enabled")
        craft_context.substitute_cap_enabled = _checkbox(form, "craft_context_substitute_cap_enabled")
        craft_context.substitute_premium_multiplier = _float(
            form,
            "craft_context_substitute_premium_multiplier",
            craft_context.substitute_premium_multiplier,
        )
        craft_context.same_collection_same_rarity = _checkbox(form, "craft_context_same_collection_same_rarity")
        craft_context.target_float_max = _float(form, "craft_context_target_float_max", craft_context.target_float_max)
        craft_context.min_substitute_sample = _int(
            form,
            "craft_context_min_substitute_sample",
            craft_context.min_substitute_sample,
        )
        settings.set_craft_context_settings(craft_context)

        telegram_alert = current_settings.telegram_alert_settings
        telegram_alert.message_format = str(form.get("telegram_message_format", telegram_alert.message_format) or "compact")
        telegram_alert.include_link = _checkbox(form, "telegram_include_link")
        telegram_alert.include_pattern = _checkbox(form, "telegram_include_pattern")
        telegram_alert.include_sample_stats = _checkbox(form, "telegram_include_sample_stats")
        telegram_alert.include_reasons = _checkbox(form, "telegram_include_reasons")
        telegram_alert.batch_alerts = _checkbox(form, "telegram_batch_alerts")
        telegram_alert.batch_interval_seconds = _int(
            form, "telegram_batch_interval_seconds", telegram_alert.batch_interval_seconds
        )
        telegram_alert.max_alerts_per_message = _int(
            form, "telegram_max_alerts_per_message", telegram_alert.max_alerts_per_message
        )
        telegram_alert.max_message_length = _int(
            form, "telegram_max_message_length", telegram_alert.max_message_length
        )
        settings.set_telegram_alert_settings(telegram_alert)
        repository.save_settings(settings)
        repository.log_user_action("settings", "1", "update")
        return RedirectResponse("/settings?message=saved", status_code=303)

    @app.get("/collections", response_class=HTMLResponse)
    def collections_page(request: Request, page: int = 1, _: str = Depends(require_auth)):
        collections, pagination = _paginate_rows(repository.list_collections(), page, request)
        return templates.TemplateResponse(
            request,
            "collections.html",
            {
                "request": request,
                "active": "collections",
                "collections": collections,
                "pagination": pagination,
                "message": request.query_params.get("message", ""),
            },
        )

    @app.post("/collections")
    async def add_collection(request: Request, _: str = Depends(require_auth)) -> RedirectResponse:
        form = await _form(request)
        name = str(form.get("name", "")).strip()
        if not name:
            return RedirectResponse("/collections?message=name_required", status_code=303)
        collection_id = str(form.get("id", "")).strip() or slugify(name)
        repository.save_collection(
            Collection(
                id=collection_id,
                name=name,
                steam_collection_url=str(form.get("steam_collection_url", "") or "").strip(),
                enabled=_checkbox(form, "enabled"),
            )
        )
        repository.log_user_action("collection", collection_id, "save")
        return RedirectResponse("/collections?message=saved", status_code=303)

    @app.post("/collections/{collection_id}/toggle")
    async def toggle_collection(request: Request, collection_id: str, _: str = Depends(require_auth)) -> RedirectResponse:
        form = await _form(request)
        enabled = _checkbox(form, "enabled")
        row = repository.get_collection(collection_id)
        if row:
            repository.save_collection(
                Collection(
                    id=row["id"],
                    name=row["name"],
                    steam_collection_url=row["steam_collection_url"],
                    enabled=enabled,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return RedirectResponse("/collections", status_code=303)

    @app.post("/collections/{collection_id}/delete")
    def delete_collection(collection_id: str, _: str = Depends(require_auth)) -> RedirectResponse:
        repository.delete_collection(collection_id)
        repository.log_user_action("collection", collection_id, "delete")
        return RedirectResponse("/collections?message=deleted", status_code=303)

    @app.post("/collections/{collection_id}/scan")
    def scan_collection(collection_id: str, _: str = Depends(require_auth)) -> RedirectResponse:
        try:
            result = run_scan_cycle(repository, collection_id=collection_id, trigger="web_collection")
            message = result.message or f"scan_{result.scanned_items}_{result.listings_saved}"
        except Exception as error:
            message = "scan_error_" + str(error)[:80]
        return RedirectResponse(f"/collections?message={urllib.parse.quote(message)}", status_code=303)

    @app.get("/items", response_class=HTMLResponse)
    def items_page(
        request: Request,
        collection_id: str | None = None,
        page: int = 1,
        _: str = Depends(require_auth),
    ):
        items, pagination = _paginate_rows(repository.list_items(collection_id), page, request)
        return templates.TemplateResponse(
            request,
            "items.html",
            {
                "request": request,
                "active": "items",
                "items": items,
                "collections": repository.list_collections(),
                "pagination": pagination,
                "exteriors": EXTERIORS,
                "rarities": RARITIES,
                "qualities": QUALITIES,
                "settings": repository.get_settings(),
                "selected_collection": collection_id or "",
                "exterior_field": _item_exterior_field,
                "message": request.query_params.get("message", ""),
            },
        )

    @app.post("/items")
    async def add_item(request: Request, _: str = Depends(require_auth)) -> RedirectResponse:
        form = await _form(request)
        collection_id = str(form.get("collection_id", "")).strip()
        display_name = str(form.get("display_name", "")).strip()
        exterior = str(form.get("exterior", "")).strip()
        market_hash_name = str(form.get("market_hash_name", "")).strip()
        if not market_hash_name and display_name:
            market_hash_name = f"{display_name} ({exterior})" if exterior else display_name
        if not collection_id or not market_hash_name:
            return RedirectResponse("/items?message=item_required", status_code=303)
        item_id = str(form.get("id", "")).strip() or slugify(f"{collection_id}_{market_hash_name}")
        url = str(form.get("steam_market_url", "")).strip() or build_market_listing_url(market_hash_name)
        repository.save_item(
            ItemDefinition(
                id=item_id,
                collection_id=collection_id,
                market_hash_name=market_hash_name,
                display_name=display_name or market_hash_name,
                weapon_type=str(form.get("weapon_type", "") or ""),
                rarity=str(form.get("rarity", "") or ""),
                quality=str(form.get("quality", "") or ""),
                exterior=exterior,
                is_souvenir=_checkbox(form, "is_souvenir"),
                is_stattrak=_checkbox(form, "is_stattrak"),
                steam_market_url=url,
                enabled=_checkbox(form, "enabled"),
            )
        )
        repository.log_user_action("item", item_id, "save")
        return RedirectResponse("/items?message=saved", status_code=303)

    @app.post("/items/generate-exteriors")
    async def generate_exterior_items(request: Request, _: str = Depends(require_auth)) -> RedirectResponse:
        form = await _form(request)
        settings = repository.get_settings()
        collection_id = str(form.get("collection_id", "")).strip()
        base_name = str(form.get("base_name", "")).strip()
        prefix = str(form.get("market_name_prefix", "") or "").strip()
        selected = [exterior for exterior in EXTERIORS if _checkbox(form, _item_exterior_field(exterior))]
        if not selected:
            selected = settings.selected_exterior_list
        if not collection_id or not base_name or not selected:
            return RedirectResponse("/items?message=generate_required", status_code=303)

        created = 0
        for market_hash_name in build_exterior_variants(base_name, selected):
            if prefix:
                market_hash_name = f"{prefix} {market_hash_name}"
            exterior = _extract_exterior(market_hash_name)
            item_id = slugify(f"{collection_id}_{market_hash_name}")
            repository.save_item(
                ItemDefinition(
                    id=item_id,
                    collection_id=collection_id,
                    market_hash_name=market_hash_name,
                    display_name=base_name,
                    weapon_type=str(form.get("weapon_type", "") or ""),
                    rarity=str(form.get("rarity", "") or ""),
                    quality=str(form.get("quality", "") or prefix),
                    exterior=exterior,
                    is_souvenir=_checkbox(form, "is_souvenir") or prefix.lower() == "souvenir",
                    is_stattrak=_checkbox(form, "is_stattrak") or prefix.lower().startswith("stattrak"),
                    steam_market_url=build_market_listing_url(market_hash_name),
                    enabled=True,
                )
            )
            created += 1
        repository.log_user_action("items", collection_id, "generate_exteriors", {"count": created})
        return RedirectResponse(f"/items?collection_id={urllib.parse.quote(collection_id)}&message=generated_{created}", status_code=303)

    @app.post("/items/{item_id}/rule")
    async def update_item_rule(request: Request, item_id: str, _: str = Depends(require_auth)) -> RedirectResponse:
        form = await _form(request)
        item = repository.get_item(item_id)
        if not item:
            return RedirectResponse("/items?message=item_not_found", status_code=303)
        repository.save_item(
            ItemDefinition(
                id=item["id"],
                collection_id=item["collection_id"],
                market_hash_name=item["market_hash_name"],
                display_name=str(form.get("display_name", item["display_name"]) or item["display_name"]),
                weapon_type=item["weapon_type"],
                rarity=str(form.get("rarity", item["rarity"]) or item["rarity"]),
                quality=str(form.get("quality", item["quality"]) or item["quality"]),
                exterior=str(form.get("exterior", item["exterior"]) or item["exterior"]),
                is_souvenir=bool(item["is_souvenir"]),
                is_stattrak=bool(item["is_stattrak"]),
                steam_market_url=str(form.get("steam_market_url", item["steam_market_url"]) or item["steam_market_url"]),
                enabled=_checkbox(form, "item_enabled"),
                last_parsed_at=item["last_parsed_at"],
            )
        )
        rule = repository.get_rule_for_item(item_id)
        rule_id = rule["id"] if rule else f"{item_id}_rule"
        repository.save_rule(
            SnipingRule(
                id=rule_id,
                item_definition_id=item_id,
                enabled=_checkbox(form, "rule_enabled"),
                max_buy_price_rub=_optional_float(form, "max_buy_price_rub"),
                target_resale_price_rub=_optional_float(form, "target_resale_price_rub"),
                custom_roi_percent=_optional_float(form, "custom_roi_percent"),
                min_profit_rub=_optional_float(form, "min_profit_rub"),
                min_roi_percent=_optional_float(form, "min_roi_percent"),
                float_min=_optional_float(form, "float_min"),
                float_max=_optional_float(form, "float_max"),
                target_float_min=_optional_float(form, "target_float_min"),
                target_float_max=_optional_float(form, "target_float_max"),
                pattern_ranges=str(form.get("pattern_ranges", "") or ""),
                priority=_int(form, "priority", 0),
                telegram_alert_enabled=_checkbox(form, "telegram_alert_enabled"),
                notes=str(form.get("notes", "") or ""),
            )
        )
        repository.log_user_action("item", item_id, "update_rule")
        return RedirectResponse("/items?message=saved", status_code=303)

    @app.post("/items/{item_id}/scan")
    def scan_item(item_id: str, _: str = Depends(require_auth)) -> RedirectResponse:
        try:
            result = run_scan_cycle(repository, item_id=item_id, trigger="web_item")
            message = result.message or f"scan_{result.scanned_items}_{result.listings_saved}"
        except Exception as error:
            message = "scan_error_" + str(error)[:80]
        return RedirectResponse(f"/items?message={urllib.parse.quote(message)}", status_code=303)

    @app.get("/listings", response_class=HTMLResponse)
    def listings_page(
        request: Request,
        collection_id: str | None = None,
        item_id: str | None = None,
        active_only: bool = False,
        page: int = 1,
        _: str = Depends(require_auth),
    ):
        listings, pagination = _paginate_rows(
            repository.list_market_listings(
                collection_id=collection_id,
                item_id=item_id,
                active_only=active_only,
                limit=100000,
            ),
            page,
            request,
        )
        return templates.TemplateResponse(
            request,
            "listings.html",
            {
                "request": request,
                "active": "listings",
                "listings": listings,
                "collections": repository.list_collections(),
                "pagination": pagination,
                "filters": dict(request.query_params),
                "message": request.query_params.get("message", ""),
            },
        )

    @app.get("/candidates", response_class=HTMLResponse)
    def candidates_page(
        request: Request,
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
        sort: str = "time",
        page: int = 1,
        _: str = Depends(require_auth),
    ):
        settings = repository.get_settings()
        candidates, pagination = _paginate_rows(
            repository.list_candidates(
                only_new=only_new,
                level=level,
                status=status,
                collection_id=collection_id,
                item_id=item_id,
                min_profit=min_profit,
                min_roi=min_roi,
                min_score=min_score,
                min_risk_adjusted_score=min_risk_adjusted_score,
                float_bucket=float_bucket,
                requires_sweep=requires_sweep,
                market_confidence=market_confidence,
                manual_review_required=manual_review_required,
                max_capital_required=max_capital_required,
                souvenir_only=souvenir_only,
                exact_item_only=exact_item_only,
                date_from=date_from,
                date_to=date_to,
                limit=100000,
                sort=sort,
            ),
            page,
            request,
        )
        return templates.TemplateResponse(
            request,
            "candidates.html",
            {
                "request": request,
                "active": "candidates",
                "candidates": candidates,
                "collections": repository.list_collections(),
                "items": repository.list_items(collection_id),
                "float_buckets": settings.anomaly_settings.float_buckets,
                "pagination": pagination,
                "filters": dict(request.query_params),
                "message": request.query_params.get("message", ""),
            },
        )

    @app.get("/candidates/{candidate_id}", response_class=HTMLResponse)
    def candidate_details_page(
        request: Request,
        candidate_id: str,
        _: str = Depends(require_auth),
    ):
        candidate = repository.get_candidate_details(candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
        return templates.TemplateResponse(
            request,
            "candidate_detail.html",
            {
                "request": request,
                "active": "candidates",
                "row": candidate,
                "reasons": _split_reasons(
                    candidate.get("anomaly_reasons") or candidate.get("recommendation_reason") or ""
                ),
                "market_baselines": repository.list_market_baseline_rows(candidate["item_id"]),
                "market_snapshots": repository.list_market_snapshots(candidate["item_id"], limit=30),
            },
        )

    @app.post("/candidates/{candidate_id}/status")
    async def update_candidate(request: Request, candidate_id: str, _: str = Depends(require_auth)) -> RedirectResponse:
        form = await _form(request)
        new_status = str(form.get("new_status", "checked"))
        if new_status not in CANDIDATE_STATUSES:
            new_status = "checked"
        repository.update_candidate_status(candidate_id, new_status)
        repository.log_user_action("candidate", candidate_id, "status", {"status": new_status})
        return RedirectResponse("/candidates?message=status_saved", status_code=303)

    @app.get("/candidates/{candidate_id}/open")
    def open_candidate(candidate_id: str, target: str = "listing", _: str = Depends(require_auth)) -> RedirectResponse:
        row = repository.get_candidate_details(candidate_id)
        if not row:
            return RedirectResponse("/candidates?message=candidate_not_found", status_code=303)
        if row["status"] == "new":
            repository.update_candidate_status(candidate_id, "opened")
        repository.log_user_action("candidate", candidate_id, "open", {"target": target})
        url = row["search_url"] if target == "search" else row["listing_url"]
        if not url:
            url = row["search_url"] or row["listing_url"] or "/candidates"
        return RedirectResponse(url, status_code=303)

    @app.post("/candidates/bulk-status")
    async def bulk_update_candidates(request: Request, _: str = Depends(require_auth)) -> RedirectResponse:
        form = await _form_multi(request)
        new_status = str((form.get("new_status") or ["checked"])[-1])
        if new_status not in CANDIDATE_STATUSES:
            new_status = "checked"
        candidate_ids = [value for value in form.get("candidate_id", []) if value]
        count = repository.update_candidate_statuses(candidate_ids, new_status)
        repository.log_user_action("candidate", "bulk", "status", {"status": new_status, "count": count})
        return RedirectResponse(f"/candidates?message=bulk_{count}", status_code=303)

    @app.get("/reports/candidates.csv")
    def candidates_csv_report(_: str = Depends(require_auth)) -> PlainTextResponse:
        rows = repository.list_candidates(limit=100000)
        output = io.StringIO()
        fieldnames = list(rows[0].keys()) if rows else ["id", "skin_name", "buy_price_rub", "estimated_profit_rub", "estimated_roi_percent", "status"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
        return PlainTextResponse(
            output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=airmoney-candidates.csv"},
        )

    @app.get("/reports/candidates.html", response_class=HTMLResponse)
    def candidates_html_report(request: Request, _: str = Depends(require_auth)):
        return templates.TemplateResponse(
            request,
            "candidates_report.html",
            {
                "request": request,
                "active": "candidates",
                "candidates": repository.list_candidates(limit=100000),
            },
        )


    @app.get("/rule-stats", response_class=HTMLResponse)
    def rule_stats_page(request: Request, page: int = 1, _: str = Depends(require_auth)):
        rows, pagination = _paginate_rows(repository.rule_stats(limit=100000), page, request)
        return templates.TemplateResponse(
            request,
            "rule_stats.html",
            {
                "request": request,
                "active": "rule_stats",
                "rows": rows,
                "pagination": pagination,
                "message": request.query_params.get("message", ""),
            },
        )

    @app.get("/import-export", response_class=HTMLResponse)
    def import_export_page(request: Request, _: str = Depends(require_auth)):
        return templates.TemplateResponse(
            request,
            "import_export.html",
            {
                "request": request,
                "active": "import_export",
                "config_text": export_config(repository),
                "errors": [],
                "message": request.query_params.get("message", ""),
            },
        )

    @app.get("/export-config")
    def download_config(_: str = Depends(require_auth)) -> PlainTextResponse:
        return PlainTextResponse(
            export_config(repository),
            media_type="application/x-yaml",
            headers={"Content-Disposition": "attachment; filename=airmoney-config.yaml"},
        )

    @app.post("/import-export", response_class=HTMLResponse)
    async def import_export_action(request: Request, _: str = Depends(require_auth)):
        form = await _form(request)
        text = str(form.get("config_text", "") or "")
        action = str(form.get("action", "validate"))
        if action in {"catalog_validate", "catalog_import"}:
            result = import_catalog_text(repository, text, apply=action == "catalog_import")
            message = (
                f"catalog_imported_{result.collections_count}_{result.items_count}"
                if result.valid and action == "catalog_import"
                else ("catalog_valid" if result.valid else "invalid")
            )
        else:
            result = import_config_text(repository, text, apply=action == "import")
            message = "imported" if result.valid and action == "import" else ("valid" if result.valid else "invalid")
        return templates.TemplateResponse(
            request,
            "import_export.html",
            {
                "request": request,
                "active": "import_export",
                "config_text": text,
                "errors": result.errors,
                "message": message,
            },
        )

    return app


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    load_dotenv()
    expected_user = os.getenv("AIRMONEY_WEB_USER")
    expected_password = os.getenv("AIRMONEY_WEB_PASSWORD")
    if not expected_user or not expected_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="AIRMONEY_WEB_USER и AIRMONEY_WEB_PASSWORD не настроены в .env",
            headers={"WWW-Authenticate": "Basic"},
        )
    correct_user = secrets.compare_digest(credentials.username, expected_user)
    correct_password = secrets.compare_digest(credentials.password, expected_password)
    if not (correct_user and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _checkbox(form: Any, name: str) -> bool:
    return to_bool(form.get(name))


async def _form(request: Request) -> dict[str, str]:
    body = await request.body()
    parsed = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


async def _form_multi(request: Request) -> dict[str, list[str]]:
    body = await request.body()
    return urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)


def _int(form: Any, name: str, default: int) -> int:
    try:
        return int(form.get(name, default))
    except Exception:
        return default


def _float(form: Any, name: str, default: float) -> float:
    try:
        return float(str(form.get(name, default)).replace(",", "."))
    except Exception:
        return default


def _anomaly_sample_errors(anomaly: Any) -> list[str]:
    errors: list[str] = []
    if anomaly.sample.min_listings < 3:
        errors.append("anomaly.sample.min_listings must be >= 3")
    if anomaly.sample.target_listings < anomaly.sample.min_listings:
        errors.append("anomaly.sample.target_listings must be >= min_listings")
    if anomaly.sample.max_listings < anomaly.sample.target_listings:
        errors.append("anomaly.sample.max_listings must be >= target_listings")
    if anomaly.sample.max_listings > 100:
        errors.append("anomaly.sample.max_listings must be <= 100")
    return errors


def _optional_float(form: Any, name: str) -> float | None:
    value = form.get(name)
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _exterior_field(exterior: str) -> str:
    return "selected_exterior_" + exterior.lower().replace("-", "_").replace(" ", "_")


def _item_exterior_field(exterior: str) -> str:
    return "item_exterior_" + exterior.lower().replace("-", "_").replace(" ", "_")


def _extract_exterior(market_hash_name: str) -> str:
    for exterior in EXTERIORS:
        if market_hash_name.endswith(f"({exterior})"):
            return exterior
    return ""


def _split_reasons(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def _paginate_rows(
    rows: list[dict[str, Any]],
    page: int,
    request: Request,
    per_page: int = WEB_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    total = len(rows)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), pages))
    start_index = (page - 1) * per_page
    end_index = min(start_index + per_page, total)
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "start": start_index + 1 if total else 0,
        "end": end_index,
        "has_prev": page > 1,
        "has_next": page < pages,
        "prev_url": _page_url(request, page - 1),
        "next_url": _page_url(request, page + 1),
    }
    return rows[start_index:end_index], pagination


def _page_url(request: Request, page: int) -> str:
    params = dict(request.query_params)
    params["page"] = str(max(1, page))
    query = urllib.parse.urlencode(params)
    return f"{request.url.path}?{query}" if query else str(request.url.path)


app = create_app()
