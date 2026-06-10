from __future__ import annotations

import time
from dataclasses import dataclass

from airmoney.config.models import Candidate
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
    scanned_items: int = 0
    listings_saved: int = 0
    candidates_saved: int = 0
    alert_candidates: list[Candidate] | None = None


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
) -> ScanResult:
    repository = repo or Repository()
    settings = repository.get_settings()
    targets = repository.build_scan_targets(collection_id=collection_id, item_id=item_id)
    result = ScanResult(alert_candidates=[])
    if not targets:
        return result
    repository.mark_listings_inactive_for_items([row["id"] for row in targets])

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
        browser = playwright.chromium.launch(headless=settings.headless)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        context.route("**/*", block_unneeded_requests)
        page = context.new_page()

        try:
            for row in targets:
                target = target_from_row(row)
                response = page.goto(target.steam_market_url, wait_until="domcontentloaded", timeout=30000)
                check_steam_access(page, response=response)
                close_cookie_banner(page)
                page.wait_for_timeout(900)
                check_steam_access(page)

                page_item_name = get_page_item_name(page)
                seen_cards: set[str] = set()
                previous_seen_count = -1

                for scroll_index in range(settings.max_scrolls + 1):
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
                        repository.save_listing(listing)
                        rule = repository.get_rule_for_item(target.id)
                        candidate = evaluate_listing(
                            listing_id=listing.id,
                            buy_price_rub=listing.buy_price_rub,
                            float_value=listing.float_value,
                            pattern=listing.pattern,
                            rule=rule,
                            settings=settings,
                        )
                        repository.save_candidate(candidate)
                        result.listings_saved += 1
                        result.candidates_saved += 1
                        if candidate.recommendation_level in {"critical", "good"}:
                            result.alert_candidates.append(candidate)

                    if len(seen_cards) == previous_seen_count:
                        break
                    previous_seen_count = len(seen_cards)
                    if scroll_index < settings.max_scrolls:
                        page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.85));")
                        page.wait_for_timeout(250)

                result.scanned_items += 1
                if settings.request_delay_seconds > 0:
                    time.sleep(settings.request_delay_seconds)

        except SteamAccessLimited:
            raise
        finally:
            context.close()
            browser.close()

    repository.expire_candidates_for_inactive_listings()
    return result
