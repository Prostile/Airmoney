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
from airmoney.config.models import CANDIDATE_STATUSES, Collection, EXTERIORS, ItemDefinition, ParserSettings, SnipingRule, to_bool
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
            "currency": rates,
        }

    @app.post("/scan")
    def scan_now(_: str = Depends(require_auth)) -> RedirectResponse:
        try:
            result = run_scan_cycle(repository, trigger="web")
            message = f"scan: {result.scanned_items} items, {result.listings_saved} listings"
        except Exception as error:
            message = f"scan error: {error}"
        return RedirectResponse(f"/dashboard?message={message}", status_code=303)

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
    def scan_runs_page(request: Request, _: str = Depends(require_auth)):
        return templates.TemplateResponse(
            request,
            "scan_runs.html",
            {
                "request": request,
                "active": "scan_runs",
                "scan_runs": repository.list_scan_runs(limit=100),
                "message": request.query_params.get("message", ""),
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, _: str = Depends(require_auth)):
        return templates.TemplateResponse(
            request,
            "parser_settings.html",
            {
                "request": request,
                "active": "settings",
                "settings": repository.get_settings(),
                "exteriors": EXTERIORS,
                "exterior_field": _exterior_field,
                "message": request.query_params.get("message", ""),
            },
        )

    @app.post("/settings")
    async def save_settings(request: Request, _: str = Depends(require_auth)) -> RedirectResponse:
        form = await _form(request)
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
        repository.save_settings(settings)
        repository.log_user_action("settings", "1", "update")
        return RedirectResponse("/settings?message=saved", status_code=303)

    @app.get("/collections", response_class=HTMLResponse)
    def collections_page(request: Request, _: str = Depends(require_auth)):
        return templates.TemplateResponse(
            request,
            "collections.html",
            {
                "request": request,
                "active": "collections",
                "collections": repository.list_collections(),
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
            message = f"scan_{result.scanned_items}_{result.listings_saved}"
        except Exception as error:
            message = "scan_error_" + urllib.parse.quote(str(error)[:80])
        return RedirectResponse(f"/collections?message={message}", status_code=303)

    @app.get("/items", response_class=HTMLResponse)
    def items_page(request: Request, collection_id: str | None = None, _: str = Depends(require_auth)):
        return templates.TemplateResponse(
            request,
            "items.html",
            {
                "request": request,
                "active": "items",
                "items": repository.list_items(collection_id),
                "collections": repository.list_collections(),
                "exteriors": EXTERIORS,
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
            message = f"scan_{result.scanned_items}_{result.listings_saved}"
        except Exception as error:
            message = "scan_error_" + urllib.parse.quote(str(error)[:80])
        return RedirectResponse(f"/items?message={message}", status_code=303)

    @app.get("/listings", response_class=HTMLResponse)
    def listings_page(
        request: Request,
        collection_id: str | None = None,
        item_id: str | None = None,
        active_only: bool = False,
        _: str = Depends(require_auth),
    ):
        settings = repository.get_settings()
        return templates.TemplateResponse(
            request,
            "listings.html",
            {
                "request": request,
                "active": "listings",
                "listings": repository.list_market_listings(
                    collection_id=collection_id,
                    item_id=item_id,
                    active_only=active_only,
                    limit=settings.web_table_limit,
                ),
                "collections": repository.list_collections(),
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
        date_from: str | None = None,
        date_to: str | None = None,
        sort: str = "time",
        _: str = Depends(require_auth),
    ):
        settings = repository.get_settings()
        return templates.TemplateResponse(
            request,
            "candidates.html",
            {
                "request": request,
                "active": "candidates",
                "candidates": repository.list_candidates(
                    only_new=only_new,
                    level=level,
                    status=status,
                    collection_id=collection_id,
                    item_id=item_id,
                    min_profit=min_profit,
                    min_roi=min_roi,
                    date_from=date_from,
                    date_to=date_to,
                    limit=settings.web_table_limit,
                    sort=sort,
                ),
                "collections": repository.list_collections(),
                "items": repository.list_items(collection_id),
                "filters": dict(request.query_params),
                "message": request.query_params.get("message", ""),
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


app = create_app()
