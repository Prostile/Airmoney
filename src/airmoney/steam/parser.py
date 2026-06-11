from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from airmoney.config.models import MarketListing, utc_now_iso
from airmoney.currency.provider import CurrencyRates
from airmoney.steam.extractor import (
    listing_identity,
    market_search_url,
    parse_name_from_card_text,
    parse_pattern,
    parse_price_values,
    parse_wear,
)


@dataclass
class ItemScanTarget:
    id: str
    market_hash_name: str
    display_name: str
    steam_market_url: str
    rule_id: str | None = None
    collection_name: str = ""


def extract_visible_cards_raw(page) -> list[dict[str, str]]:
    return page.evaluate(
        """
        () => {
            const normalize = (text) => {
                return (text || "")
                    .replace(/\\u00a0/g, " ")
                    .replace(/[ \\t]+/g, " ")
                    .replace(/\\n\\s+/g, "\\n")
                    .trim();
            };
            const hasPrice = (text) => /(₽|руб|RUB|\\$|USD|€|EUR)/i.test(text);
            const hasItemHints = (text) => /(Купить|Buy|Шаблон|Степень износа|Pattern|Template|Paint Seed|Float|Wear)/i.test(text);
            const hasLikelyName = (text) => /\\|/.test(text);
            const getHref = (element) => {
                const link = element.querySelector("a[href]");
                if (link) return link.href;
                if (element.tagName && element.tagName.toLowerCase() === "a") return element.href;
                return "";
            };
            const isReasonableCard = (element) => {
                const rect = element.getBoundingClientRect();
                if (rect.width < 120 || rect.height < 80) return false;
                if (rect.width > 700 || rect.height > 900) return false;
                const text = normalize(element.innerText || element.textContent || "");
                if (text.length < 20 || text.length > 2500) return false;
                return hasPrice(text) && hasItemHints(text);
            };
            const findBestCardAncestor = (element) => {
                let best = element;
                let current = element;
                for (let i = 0; i < 6; i++) {
                    if (!current) break;
                    const rect = current.getBoundingClientRect();
                    const text = normalize(current.innerText || current.textContent || "");
                    const goodSize = rect.width >= 120 && rect.height >= 80 && rect.width <= 700 && rect.height <= 900;
                    const goodText = text.length >= 20 && text.length <= 2500 && hasPrice(text) && hasItemHints(text);
                    if (goodSize && goodText) {
                        best = current;
                        if (hasLikelyName(text)) return current;
                    }
                    current = current.parentElement;
                }
                return best;
            };
            const all = Array.from(document.querySelectorAll("div, article, li, a"));
            const baseCandidates = all.filter(isReasonableCard);
            const result = [];
            const seen = new Set();
            for (const candidate of baseCandidates) {
                const card = findBestCardAncestor(candidate);
                const text = normalize(card.innerText || card.textContent || "");
                if (!text || !hasPrice(text) || !hasItemHints(text) || seen.has(text)) continue;
                seen.add(text);
                result.push({ text: text, href: getHref(card) });
            }
            return result;
        }
        """
    )


def get_page_item_name(page) -> str:
    try:
        name = page.evaluate(
            """
            () => {
                const normalize = (text) => (text || "").replace(/\\u00a0/g, " ").replace(/[ \\t]+/g, " ").trim();
                const isBadLine = (line) => {
                    const lowered = line.toLowerCase();
                    const badMarkers = [
                        "торговая площадка", "community market", "steam", "купить", "buy",
                        "шаблон", "степень износа", "pattern", "template", "paint seed",
                        "float", "wear", "руб", "₽", "rub", "$", "€", "usd", "eur",
                        "заявок", "заказать", "сортировать", "отфильтровать"
                    ];
                    return badMarkers.some(marker => lowered.includes(marker));
                };
                const elements = Array.from(document.querySelectorAll("div, span, h1, h2, h3, a"));
                const candidates = [];
                for (const element of elements) {
                    const rect = element.getBoundingClientRect();
                    if (rect.top < 0 || rect.top > 500) continue;
                    const text = normalize(element.innerText || element.textContent || "");
                    if (!text) continue;
                    for (const line of text.split("\\n").map(normalize).filter(Boolean)) {
                        if (!line.includes("|")) continue;
                        if (line.length < 5 || line.length > 140) continue;
                        if (isBadLine(line)) continue;
                        candidates.push(line);
                    }
                }
                if (candidates.length > 0) {
                    candidates.sort((a, b) => a.length - b.length);
                    return candidates[0];
                }
                return "";
            }
            """
        )
        if name:
            return str(name).strip()
    except Exception:
        pass

    try:
        title = page.title().strip()
        bad_titles = ["Торговая площадка Steam", "Steam Community Market", "Steam Community"]
        if title and title not in bad_titles:
            title = title.replace(" - Steam Community Market", "")
            title = title.replace(" :: Steam Community Market", "")
            title = title.replace("Steam Community Market :: Listings for ", "")
            title = title.replace("Steam Community Market :: ", "")
            return title.strip()
    except Exception:
        pass
    return ""


def parse_card(
    card: dict[str, Any],
    target: ItemScanTarget,
    rates: CurrencyRates,
    page_item_name: str = "",
) -> MarketListing | None:
    text = str(card.get("text", ""))
    href = str(card.get("href", ""))
    price = parse_price_values(text, rates)
    if price is None:
        return None

    name = parse_name_from_card_text(text) or page_item_name or target.display_name or target.market_hash_name
    pattern = parse_pattern(text)
    wear = parse_wear(text)
    now = utc_now_iso()
    listing_url = href or target.steam_market_url
    listing_id = listing_identity(
        target.id,
        name,
        listing_url,
        pattern,
        wear,
        price.buy_price_rub,
        _stable_text_fingerprint(text),
    )

    return MarketListing(
        id=listing_id,
        item_definition_id=target.id,
        rule_id=target.rule_id,
        skin_name=name,
        market_hash_name=target.market_hash_name,
        listing_url=listing_url,
        search_url=market_search_url(target.market_hash_name or name),
        buy_price_rub=price.buy_price_rub,
        buy_price_original=price.buy_price_original,
        currency_original=price.currency_original,
        currency_rate=price.currency_rate,
        currency_source=price.currency_source,
        currency_fetched_at=rates.fetched_at_iso,
        float_value=wear,
        pattern=pattern,
        wear_name="",
        raw_text=text,
        first_seen_at=now,
        last_seen_at=now,
        is_active=True,
        parse_status="ok",
    )


def _stable_text_fingerprint(text: str) -> str:
    normalized = " ".join(str(text or "").split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
