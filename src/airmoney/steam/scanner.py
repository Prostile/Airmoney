from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

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
)
from airmoney.steam.collections import build_market_listing_url
from airmoney.steam.parser import (
    ItemScanTarget,
    extract_visible_cards_raw,
    get_page_item_name,
    parse_card,
)


@dataclass
class ScanResult:
    total_items: int = 0
    scanned_items: int = 0
    listings_saved: int = 0
    candidates_saved: int = 0
    alert_candidates: list[Candidate] | None = None
    message: str = ""


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


def scan_once(
    repo: Repository | None = None,
    collection_id: str | None = None,
    item_id: str | None = None,
    progress: ProgressCallback | None = None,
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
                response = page.goto(target.steam_market_url, wait_until="domcontentloaded", timeout=30000)
                check_steam_access(page, response=response)
                close_cookie_banner(page)
                page.wait_for_timeout(900)
                check_steam_access(page)

                page_item_name = get_page_item_name(page)
                seen_cards: set[str] = set()
                previous_seen_count = -1
                item_listings = []
                anomaly_settings = settings.anomaly_settings

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
                            continue
                        item_listings.append(listing)
                        if len(item_listings) >= anomaly_settings.sample.max_listings:
                            break

                    if len(seen_cards) == previous_seen_count:
                        break
                    previous_seen_count = len(seen_cards)
                    if len(item_listings) >= anomaly_settings.sample.target_listings:
                        break
                    if scroll_index < settings.max_scrolls:
                        page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.85));")
                        page.wait_for_timeout(250)

                rule = repository.get_rule_for_item(target.id)
                historical_baselines = (
                    repository.list_market_baselines(target.id)
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
                    repository.save_candidate(candidate)
                    result.listings_saved += 1
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
