from __future__ import annotations

import os
import time
import urllib.parse
from dataclasses import dataclass
from statistics import median
from typing import Any, Callable

from airmoney.anomaly.baselines import assign_float_bucket
from airmoney.anomaly.candidates import candidate_from_anomaly_result
from airmoney.anomaly.history import build_market_snapshots
from airmoney.anomaly.matching import passes_item_match
from airmoney.anomaly.models import parsed_listing_from_market_listing
from airmoney.anomaly.analyzer import analyze_listings
from airmoney.config.models import Candidate, MarketListing, ParserSettings
from airmoney.currency.steam_currency import CurrencyService
from airmoney.recommendation.engine import evaluate_listing
from airmoney.storage.repositories import Repository
from airmoney.steam.browser import (
    SteamAccessLimited,
    block_unneeded_requests,
    check_steam_access,
    close_cookie_banner,
    install_resource_blocking,
)
from airmoney.steam.collections import build_market_listing_url
from airmoney.steam.parser import (
    ItemScanTarget,
    extract_visible_cards_raw,
    get_page_item_name,
    parse_card,
)
from airmoney.steam.extractor import parse_price_values


@dataclass
class ScanResult:
    total_items: int = 0
    scanned_items: int = 0
    listings_saved: int = 0
    candidates_saved: int = 0
    alert_candidates: list[Candidate] | None = None
    message: str = ""
    selected_targets_count: int = 0
    skipped_by_queue_count: int = 0
    skipped_by_item_cooldown_count: int = 0
    skipped_by_collection_cooldown_count: int = 0
    early_stop_count: int = 0
    resource_blocked_count: int = 0
    shallow_skipped_count: int = 0
    deep_scan_count: int = 0
    steam_cooldown_active: bool = False
    steam_cooldown_until: str = ""


ProgressCallback = Callable[..., None]


def target_from_row(row: dict) -> ItemScanTarget:
    market_hash_name = row.get("market_hash_name") or row.get("display_name") or row["id"]
    url = row.get("steam_market_url") or build_market_listing_url(market_hash_name)
    return ItemScanTarget(
        id=row["id"],
        market_hash_name=market_hash_name,
        display_name=row.get("display_name") or market_hash_name,
        steam_market_url=url,
        rule_id=row.get("rule_id"),
        collection_name=row.get("collection_name", ""),
    )


def _legacy_scan_once(
    repo: Repository | None = None,
    collection_id: str | None = None,
    item_id: str | None = None,
    progress: ProgressCallback | None = None,
    run_id: str | None = None,
) -> ScanResult:
    repository = repo or Repository()
    settings = repository.get_settings()
    targets = repository.build_scan_targets(collection_id=collection_id, item_id=item_id)
    result = ScanResult(alert_candidates=[])
    result.total_items = len(targets)
    _emit_progress(
        progress,
        total_items=result.total_items,
        current_item_index=0,
        current_item_name="",
        progress_message="Формируем список целей скана",
    )
    if not targets:
        result.message = _empty_targets_message(
            repository.scan_target_summary(collection_id=collection_id, item_id=item_id)
        )
        _emit_progress(progress, progress_message=result.message)
        return result
    repository.mark_listings_inactive_for_items([row["id"] for row in targets])

    _emit_progress(progress, progress_message="Обновляем курсы валют")
    rates = CurrencyService(settings).get_rates()
    repository.save_currency_rate(
        rates.usd_to_rub,
        rates.eur_to_rub,
        rates.source,
        rates.fetched_at_iso,
        rates.is_fallback,
    )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("Для сканирования нужен playwright. Установи зависимости из requirements.txt.") from error

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=settings.headless,
            proxy=_browser_proxy_config(),
        )
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        context.route("**/*", block_unneeded_requests)
        page = context.new_page()

        try:
            for index, row in enumerate(targets, start=1):
                target = target_from_row(row)
                _emit_progress(
                    progress,
                    current_item_index=index,
                    current_item_name=target.display_name,
                    progress_message=f"Открываем Steam Market: {target.display_name}",
                    scanned_items=result.scanned_items,
                    listings_saved=result.listings_saved,
                    candidates_saved=result.candidates_saved,
                )
                anomaly_settings = settings.anomaly_settings
                response = page.goto(
                    _sorted_market_url(target.steam_market_url, anomaly_settings.sample.sort_by),
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                check_steam_access(page, response=response)
                close_cookie_banner(page)
                _ensure_price_ascending_sort(page, anomaly_settings.sample.sort_by)
                page.wait_for_timeout(900)
                check_steam_access(page)

                page_item_name = get_page_item_name(page)
                seen_cards: set[str] = set()
                previous_seen_count = -1
                item_listings = []
                rejected_exact_match: list[dict[str, Any]] = []
                rejected_exact_match_count = 0

                for scroll_index in range(settings.max_scrolls + 1):
                    _emit_progress(
                        progress,
                        current_item_index=index,
                        current_item_name=target.display_name,
                        progress_message=(
                            f"Читаем карточки: {target.display_name} "
                            f"({scroll_index + 1}/{settings.max_scrolls + 1})"
                        ),
                        scanned_items=result.scanned_items,
                        listings_saved=result.listings_saved,
                        candidates_saved=result.candidates_saved,
                    )
                    check_steam_access(page)
                    cards = extract_visible_cards_raw(page)
                    for card in cards:
                        text = str(card.get("text", "")).strip()
                        if not text or text in seen_cards:
                            continue
                        seen_cards.add(text)
                        listing = parse_card(card, target, rates, page_item_name)
                        if listing is None:
                            continue
                        parsed_listing = parsed_listing_from_market_listing(listing, row)
                        if not passes_item_match(
                            parsed_listing,
                            row,
                            require_exact_item_match=anomaly_settings.sample.require_exact_item_match,
                        ):
                            rejected_exact_match_count += 1
                            if (
                                anomaly_settings.debug.log_rejected_exact_match
                                and len(rejected_exact_match) < anomaly_settings.debug.max_rejected_exact_match_log
                            ):
                                rejected_exact_match.append(_rejected_exact_match_row(listing))
                            continue
                        item_listings.append(listing)
                        if len(item_listings) >= anomaly_settings.sample.max_listings:
                            break

                    if len(seen_cards) == previous_seen_count:
                        break
                    previous_seen_count = len(seen_cards)
                    if len(item_listings) >= anomaly_settings.sample.max_listings:
                        break
                    if scroll_index < settings.max_scrolls:
                        page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.85));")
                        page.wait_for_timeout(250)

                item_listings = _prepare_item_listings(
                    item_listings,
                    anomaly_settings.sample.target_listings,
                )
                _record_exact_match_debug(
                    repository,
                    target,
                    item_listings,
                    rejected_exact_match,
                    rejected_exact_match_count,
                    settings,
                    result,
                )
                rule = repository.get_rule_for_item(target.id)
                historical_baselines = (
                    repository.list_market_baselines(
                        target.id,
                        min_snapshots=settings.anomaly_settings.history.min_snapshots,
                    )
                    if settings.anomaly_settings.history.enabled
                    else {}
                )
                candidates = _evaluate_item_listings(
                    item_listings,
                    row,
                    rule,
                    settings,
                    historical_baselines=historical_baselines,
                )
                for listing, candidate in candidates:
                    repository.save_listing(listing)
                    result.listings_saved += 1
                    if not _should_save_candidate(candidate, anomaly_settings):
                        continue
                    repository.save_candidate(candidate)
                    result.candidates_saved += 1
                    if candidate.recommendation_level in {"critical", "good"}:
                        result.alert_candidates.append(candidate)
                if settings.anomaly_settings.history.enabled and item_listings:
                    parsed_for_history = [
                        parsed_listing_from_market_listing(listing, row)
                        for listing in item_listings
                    ]
                    repository.save_market_snapshots(
                        build_market_snapshots(
                            target.id,
                            parsed_for_history,
                            settings.anomaly_settings.float_buckets,
                        ),
                        alpha=settings.anomaly_settings.history.ewma_alpha,
                    )

                result.scanned_items += 1
                _emit_progress(
                    progress,
                    current_item_index=index,
                    current_item_name=target.display_name,
                    progress_message=f"Готово: {target.display_name}",
                    scanned_items=result.scanned_items,
                    listings_saved=result.listings_saved,
                    candidates_saved=result.candidates_saved,
                )
                if settings.request_delay_seconds > 0:
                    time.sleep(settings.request_delay_seconds)

        except SteamAccessLimited:
            raise
        finally:
            context.close()
            browser.close()

    repository.expire_candidates_for_inactive_listings()
    return result


def scan_once(
    repo: Repository | None = None,
    collection_id: str | None = None,
    item_id: str | None = None,
    progress: ProgressCallback | None = None,
    run_id: str | None = None,
) -> ScanResult:
    repository = repo or Repository()
    settings = repository.get_settings()
    selection = repository.select_scan_targets(settings, collection_id=collection_id, item_id=item_id)
    targets = selection.targets
    result = ScanResult(alert_candidates=[])
    result.total_items = len(targets)
    result.selected_targets_count = selection.selected_targets_count
    result.skipped_by_queue_count = selection.skipped_by_queue_count
    result.skipped_by_item_cooldown_count = selection.skipped_by_item_cooldown_count
    result.skipped_by_collection_cooldown_count = selection.skipped_by_collection_cooldown_count
    _emit_progress(
        progress,
        total_items=result.total_items,
        current_item_index=0,
        current_item_name="",
        progress_message="Р¤РѕСЂРјРёСЂСѓРµРј СЃРїРёСЃРѕРє С†РµР»РµР№ СЃРєР°РЅР°",
    )
    if not targets:
        result.message = _empty_targets_message(
            repository.scan_target_summary(collection_id=collection_id, item_id=item_id)
        )
        _emit_progress(progress, progress_message=result.message)
        return result

    _emit_progress(progress, progress_message="РћР±РЅРѕРІР»СЏРµРј РєСѓСЂС‹ РІР°Р»СЋС‚")
    rates = CurrencyService(settings).get_rates()
    repository.save_currency_rate(
        rates.usd_to_rub,
        rates.eur_to_rub,
        rates.source,
        rates.fetched_at_iso,
        rates.is_fallback,
    )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("Р”Р»СЏ СЃРєР°РЅРёСЂРѕРІР°РЅРёСЏ РЅСѓР¶РµРЅ playwright. РЈСЃС‚Р°РЅРѕРІРё Р·Р°РІРёСЃРёРјРѕСЃС‚Рё РёР· requirements.txt.") from error

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=settings.headless,
            proxy=_browser_proxy_config(),
        )
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        resource_blocker = install_resource_blocking(context, settings.browser_optimization_settings)
        page = context.new_page()
        try:
            for index, row in enumerate(targets, start=1):
                target = target_from_row(row)
                _emit_progress(
                    progress,
                    current_item_index=index,
                    current_item_name=target.display_name,
                    progress_message=f"РћС‚РєСЂС‹РІР°РµРј Steam Market: {target.display_name}",
                    scanned_items=result.scanned_items,
                    listings_saved=result.listings_saved,
                    candidates_saved=result.candidates_saved,
                )
                blocked_before = resource_blocker.blocked_count
                try:
                    item_result = _scan_item_with_retry(
                        page,
                        repository,
                        run_id,
                        row,
                        target,
                        rates,
                        settings,
                        result,
                        index,
                        progress,
                    )
                finally:
                    result.resource_blocked_count += resource_blocker.blocked_count - blocked_before
                if item_result.get("early_stop_reason"):
                    result.early_stop_count += 1
                if item_result.get("deep_scan_performed"):
                    result.deep_scan_count += 1
                if item_result.get("status") == "scanned_without_anomaly":
                    result.shallow_skipped_count += 1
                result.scanned_items += 1
                _emit_progress(
                    progress,
                    current_item_index=index,
                    current_item_name=target.display_name,
                    progress_message=f"Р“РѕС‚РѕРІРѕ: {target.display_name}",
                    scanned_items=result.scanned_items,
                    listings_saved=result.listings_saved,
                    candidates_saved=result.candidates_saved,
                )
                if settings.request_delay_seconds > 0:
                    time.sleep(settings.request_delay_seconds)
        finally:
            context.close()
            browser.close()

    repository.expire_candidates_for_inactive_listings()
    return result


def _scan_item_with_retry(
    page,
    repository: Repository,
    run_id: str | None,
    row: dict[str, Any],
    target: ItemScanTarget,
    rates,
    settings: ParserSettings,
    result: ScanResult,
    index: int,
    progress: ProgressCallback | None,
) -> dict[str, Any]:
    guard = settings.steam_guard_settings
    max_retries = guard.max_network_retries if guard.retry_network_errors else 0
    attempt = 0
    while True:
        try:
            return _scan_item_once(
                page,
                repository,
                run_id,
                row,
                target,
                rates,
                settings,
                result,
                index,
                progress,
            )
        except SteamAccessLimited:
            if run_id:
                repository.save_item_scan_failure(
                    run_id,
                    target.id,
                    {"status": "failed", "error": "Steam access limited"},
                )
            raise
        except Exception as error:
            if _is_network_error(error) and attempt < max_retries:
                attempt += 1
                if guard.network_error_retry_delay_seconds > 0:
                    time.sleep(guard.network_error_retry_delay_seconds)
                continue
            if run_id:
                repository.save_item_scan_failure(
                    run_id,
                    target.id,
                    {"status": "failed", "error": str(error)},
                )
            raise


def _scan_item_once(
    page,
    repository: Repository,
    run_id: str | None,
    row: dict[str, Any],
    target: ItemScanTarget,
    rates,
    settings: ParserSettings,
    result: ScanResult,
    index: int,
    progress: ProgressCallback | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    anomaly_settings = settings.anomaly_settings
    scan_optimization = settings.scan_optimization_settings
    history_optimization = settings.history_optimization_settings
    historical_baselines = (
        repository.list_market_baselines(
            target.id,
            min_snapshots=history_optimization.mature_history_min_snapshots,
        )
        if settings.anomaly_settings.history.enabled
        else {}
    )
    used_historical_baseline = (
        bool(historical_baselines)
        and settings.anomaly_settings.history.enabled
        and history_optimization.use_mature_history_for_shallow_scan
    )
    effective_target = anomaly_settings.sample.target_listings
    if used_historical_baseline:
        effective_target = min(effective_target, history_optimization.mature_history_target_listings)
    effective_target = max(
        anomaly_settings.sample.min_listings,
        min(effective_target, anomaly_settings.sample.max_listings),
    )
    shallow_target = max(4, min(scan_optimization.shallow_target_listings, effective_target))

    response = page.goto(
        _sorted_market_url(target.steam_market_url, anomaly_settings.sample.sort_by),
        wait_until="domcontentloaded",
        timeout=30000,
    )
    check_steam_access(page, response=response)
    close_cookie_banner(page)
    first_prices = _first_card_prices(page, rates)
    if anomaly_settings.sample.sort_by == "price_asc" and not looks_price_sorted(first_prices):
        _ensure_price_ascending_sort(page, anomaly_settings.sample.sort_by)
        page.wait_for_timeout(700)
    else:
        page.wait_for_timeout(250)
    check_steam_access(page)

    page_item_name = get_page_item_name(page)
    seen_cards: set[str] = set()
    previous_seen_count = -1
    item_listings: list[MarketListing] = []
    rejected_exact_match: list[dict[str, Any]] = []
    rejected_exact_match_count = 0
    shallow_gap_percent: float | None = None
    deep_scan_performed = not scan_optimization.two_stage_scan
    early_stop_reason = ""

    for scroll_index in range(settings.max_scrolls + 1):
        _emit_progress(
            progress,
            current_item_index=index,
            current_item_name=target.display_name,
            progress_message=(
                f"Р§РёС‚Р°РµРј РєР°СЂС‚РѕС‡РєРё: {target.display_name} "
                f"({scroll_index + 1}/{settings.max_scrolls + 1})"
            ),
            scanned_items=result.scanned_items,
            listings_saved=result.listings_saved,
            candidates_saved=result.candidates_saved,
        )
        check_steam_access(page)
        cards = extract_visible_cards_raw(page)
        for card in cards:
            text = str(card.get("text", "")).strip()
            if not text or text in seen_cards:
                continue
            seen_cards.add(text)
            listing = parse_card(card, target, rates, page_item_name)
            if listing is None:
                continue
            parsed_listing = parsed_listing_from_market_listing(listing, row)
            if not passes_item_match(
                parsed_listing,
                row,
                require_exact_item_match=anomaly_settings.sample.require_exact_item_match,
            ):
                rejected_exact_match_count += 1
                if (
                    anomaly_settings.debug.log_rejected_exact_match
                    and len(rejected_exact_match) < anomaly_settings.debug.max_rejected_exact_match_log
                ):
                    rejected_exact_match.append(_rejected_exact_match_row(listing))
                continue
            item_listings.append(listing)
            if len(item_listings) >= anomaly_settings.sample.max_listings:
                break

        if (
            scan_optimization.two_stage_scan
            and shallow_gap_percent is None
            and len(item_listings) >= shallow_target
        ):
            shallow_gap_percent = calculate_floor_gap([listing.buy_price_rub for listing in item_listings])
            historical_gap = _historical_gap_percent(
                item_listings,
                row,
                settings,
                historical_baselines,
            )
            gap = max(value for value in [shallow_gap_percent, historical_gap] if value is not None) if (
                shallow_gap_percent is not None or historical_gap is not None
            ) else None
            if scan_optimization.deep_scan_on_gap and (
                gap is None or gap < scan_optimization.shallow_min_gap_percent
            ):
                early_stop_reason = "shallow_gap_below_threshold"
                break
            deep_scan_performed = True

        if len(item_listings) >= effective_target:
            early_stop_reason = "target_listings_reached"
            break
        if len(seen_cards) == previous_seen_count:
            break
        previous_seen_count = len(seen_cards)
        if len(item_listings) >= anomaly_settings.sample.max_listings:
            break
        if scroll_index < settings.max_scrolls:
            page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.85));")
            page.wait_for_timeout(250)

    target_for_prepare = shallow_target if early_stop_reason == "shallow_gap_below_threshold" else effective_target
    item_listings = _prepare_item_listings(item_listings, target_for_prepare)
    _record_exact_match_debug(
        repository,
        target,
        item_listings,
        rejected_exact_match,
        rejected_exact_match_count,
        settings,
        result,
    )
    if not item_listings:
        item_result = {
            "status": "no_exact_cards",
            "cards_seen": len(seen_cards),
            "exact_cards": 0,
            "duration_ms": _duration_ms(started),
            "error": "No exact-match cards parsed",
        }
        if run_id:
            repository.save_item_scan_failure(run_id, target.id, item_result)
        return item_result

    rule = repository.get_rule_for_item(target.id)
    candidates: list[Candidate] = []
    if deep_scan_performed:
        all_candidates = _evaluate_item_listings(
            item_listings,
            row,
            rule,
            settings,
            historical_baselines=historical_baselines,
        )
        for _, candidate in all_candidates:
            if _should_save_candidate(candidate, anomaly_settings):
                candidates.append(candidate)
            if candidate.recommendation_level in {"critical", "good"}:
                result.alert_candidates.append(candidate)

    snapshots = []
    if settings.anomaly_settings.history.enabled and item_listings:
        parsed_for_history = [parsed_listing_from_market_listing(listing, row) for listing in item_listings]
        snapshots = build_market_snapshots(
            target.id,
            parsed_for_history,
            settings.anomaly_settings.float_buckets,
        )

    item_result = {
        "status": "success" if deep_scan_performed else "scanned_without_anomaly",
        "cards_seen": len(seen_cards),
        "exact_cards": len(item_listings),
        "target_listings_reached": len(item_listings) >= effective_target,
        "early_stop_reason": early_stop_reason,
        "shallow_gap_percent": shallow_gap_percent,
        "deep_scan_performed": deep_scan_performed,
        "used_historical_baseline": used_historical_baseline,
        "duration_ms": _duration_ms(started),
    }
    if run_id:
        saved = repository.save_item_scan_success(
            run_id,
            target.id,
            item_listings,
            candidates,
            snapshots=snapshots,
            snapshot_alpha=settings.anomaly_settings.history.ewma_alpha,
            item_result=item_result,
        )
        result.listings_saved += saved["listings_saved"]
        result.candidates_saved += saved["candidates_saved"]
    else:
        repository.mark_listings_inactive_for_items([target.id])
        for listing in item_listings:
            repository.save_listing(listing)
            result.listings_saved += 1
        for candidate in candidates:
            repository.save_candidate(candidate)
            result.candidates_saved += 1
        if snapshots:
            repository.save_market_snapshots(snapshots, alpha=settings.anomaly_settings.history.ewma_alpha)
    return item_result


def looks_price_sorted(prices: list[float]) -> bool:
    if len(prices) < 4:
        return False
    inversions = 0
    for left, right in zip(prices, prices[1:]):
        if left > right:
            inversions += 1
    return inversions <= 1


def calculate_floor_gap(prices: list[float]) -> float | None:
    values = sorted(price for price in prices if price and price > 0)
    if len(values) < 4:
        return None
    candidate = values[0]
    baseline = median(values[1 : min(len(values), 8)])
    if baseline <= 0:
        return None
    return round((1 - candidate / baseline) * 100, 2)


def _first_card_prices(page, rates, limit: int = 8) -> list[float]:
    prices: list[float] = []
    try:
        for card in extract_visible_cards_raw(page):
            parsed = parse_price_values(str(card.get("text", "")), rates)
            if parsed is None:
                continue
            prices.append(parsed.buy_price_rub)
            if len(prices) >= limit:
                break
    except Exception:
        return []
    return prices


def _historical_gap_percent(
    listings: list[MarketListing],
    item: dict[str, Any],
    settings: ParserSettings,
    historical_baselines: dict[str, float],
) -> float | None:
    if not listings or not historical_baselines:
        return None
    best_gap: float | None = None
    for listing in listings:
        parsed = parsed_listing_from_market_listing(listing, item)
        bucket = assign_float_bucket(parsed.wear_rating, settings.anomaly_settings.float_buckets)
        baseline = historical_baselines.get(bucket)
        if not baseline or baseline <= 0:
            continue
        gap = round((1 - parsed.price_rub / baseline) * 100, 2)
        best_gap = gap if best_gap is None else max(best_gap, gap)
    return best_gap


def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _is_network_error(error: Exception) -> bool:
    text = str(error)
    markers = [
        "ERR_NETWORK_CHANGED",
        "ERR_TIMED_OUT",
        "ERR_CONNECTION",
        "Timeout",
        "Page.goto",
    ]
    return any(marker in text for marker in markers)


def _evaluate_item_listings(
    listings: list[MarketListing],
    item: dict[str, Any],
    rule: dict[str, Any] | None,
    settings: ParserSettings,
    historical_baselines: dict[str, float] | None = None,
) -> list[tuple[MarketListing, Candidate]]:
    if not listings:
        return []

    if not settings.anomaly_settings.enabled:
        return [
            (
                listing,
                evaluate_listing(
                    listing_id=listing.id,
                    buy_price_rub=listing.buy_price_rub,
                    float_value=listing.float_value,
                    pattern=listing.pattern,
                    rule=rule,
                    settings=settings,
                ),
            )
            for listing in listings
        ]

    parsed_listings = [parsed_listing_from_market_listing(listing, item) for listing in listings]
    anomaly_results = analyze_listings(
        parsed_listings,
        item,
        rule,
        settings,
        historical_baselines=historical_baselines,
    )
    pairs: list[tuple[MarketListing, Candidate]] = []
    for listing, anomaly_result in zip(listings, anomaly_results, strict=False):
        pairs.append(
            (
                listing,
                candidate_from_anomaly_result(
                    anomaly_result,
                    listing_id=listing.id,
                    rule_id=listing.rule_id or (rule.get("id") if rule else None),
                    market_fee_percent=settings.default_market_fee_percent,
                ),
            )
        )
    return pairs


def _prepare_item_listings(
    listings: list[MarketListing],
    target_listings: int,
) -> list[MarketListing]:
    return sorted(
        listings,
        key=lambda listing: (
            listing.buy_price_rub if listing.buy_price_rub is not None else float("inf"),
            listing.id,
        ),
    )[:target_listings]


def _should_save_candidate(candidate: Candidate, anomaly_settings: Any) -> bool:
    return candidate.recommendation_level != "skip" or anomaly_settings.debug.save_skip_candidates


def _sorted_market_url(url: str, sort_by: str) -> str:
    if sort_by != "price_asc":
        return url
    parsed = urllib.parse.urlsplit(url)
    params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    params.update({"sort_column": "price", "sort_dir": "asc"})
    params.setdefault("start", "0")
    params.setdefault("l", "english")
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(params),
            "p1_price_asc",
        )
    )


def _ensure_price_ascending_sort(page, sort_by: str) -> None:
    if sort_by != "price_asc":
        return
    try:
        clicked = page.evaluate(
            """
            () => {
                const labels = [
                    "price: low", "price low", "low to high", "lowest price",
                    "цена: по возрастанию", "цена по возрастанию", "сначала дешевые",
                    "сначала дешёвые", "дешевле"
                ];
                const elements = Array.from(document.querySelectorAll("button, [role=button], a"));
                const target = elements.find((element) => {
                    const text = (element.innerText || element.textContent || "").toLowerCase();
                    return text && labels.some((label) => text.includes(label));
                });
                if (!target) return false;
                target.click();
                return true;
            }
            """
        )
        if clicked:
            page.wait_for_timeout(700)
    except Exception:
        return


def _rejected_exact_match_row(listing: MarketListing) -> dict[str, Any]:
    return {
        "skin_name": listing.skin_name,
        "price_rub": listing.buy_price_rub,
        "listing_url": listing.listing_url,
        "raw_text": listing.raw_text[:500],
    }


def _record_exact_match_debug(
    repository: Repository,
    target: ItemScanTarget,
    item_listings: list[MarketListing],
    rejected_exact_match: list[dict[str, Any]],
    rejected_exact_match_count: int,
    settings: ParserSettings,
    result: ScanResult,
) -> None:
    anomaly_settings = settings.anomaly_settings
    if not anomaly_settings.debug.log_rejected_exact_match:
        return
    if len(item_listings) >= anomaly_settings.sample.min_listings or not rejected_exact_match_count:
        return
    message = (
        f"Мало exact-match карточек для {target.display_name}: "
        f"{len(item_listings)} из {anomaly_settings.sample.min_listings}; "
        f"отклонено {rejected_exact_match_count}."
    )
    result.message = f"{result.message}\n{message}".strip() if result.message else message
    repository.log_user_action(
        "steam_scan",
        target.id,
        "exact_match_rejected",
        {
            "target": target.display_name,
            "exact_sample_size": len(item_listings),
            "min_listings": anomaly_settings.sample.min_listings,
            "rejected_count": rejected_exact_match_count,
            "examples": rejected_exact_match,
        },
    )


def _emit_progress(progress: ProgressCallback | None, **payload: Any) -> None:
    if progress is None:
        return
    progress(**payload)


def _browser_proxy_config() -> dict[str, str] | None:
    server = os.getenv("AIRMONEY_BROWSER_PROXY", "").strip()
    if not server:
        return None
    return {"server": server}


def _empty_targets_message(summary: dict[str, int]) -> str:
    if summary["total_items"] == 0:
        return "Нет целей сканирования: добавьте предметы или импортируйте каталог."
    if summary["enabled_collections"] == 0:
        return "Нет целей сканирования: все коллекции выключены. Включите хотя бы одну коллекцию."
    if summary["enabled_items"] == 0:
        return "Нет целей сканирования: все предметы выключены. Включите хотя бы один предмет."
    if summary["items_blocked_by_disabled_collection"] > 0:
        return "Нет целей сканирования: предметы включены, но их коллекции выключены."
    return "Нет целей сканирования: проверьте включённые коллекции, предметы и фильтры."
