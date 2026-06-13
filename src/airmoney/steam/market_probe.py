from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from airmoney.anomaly.matching import passes_item_match
from airmoney.anomaly.models import parsed_listing_from_market_listing
from airmoney.currency.provider import CurrencyRates
from airmoney.steam.browser import check_steam_access, close_cookie_banner, install_resource_blocking
from airmoney.steam.collections import EXTERIOR_FILTER_TAGS, build_market_listing_url, steam_market_filter_params
from airmoney.steam.parser import ItemScanTarget, extract_visible_cards_raw, get_page_item_name, parse_card
from airmoney.storage.repositories import Repository


@dataclass
class ProbeUrl:
    name: str
    url: str
    mode: str = "dom"
    action: str = ""


def probe_item_filters(
    repo: Repository,
    item_id: str,
    limit: int = 20,
    delay_seconds: float = 8.0,
) -> dict[str, Any]:
    item = repo.get_item(item_id)
    if not item:
        raise ValueError(f"Unknown item_id: {item_id}")
    settings = repo.get_settings()
    rates = _latest_rates(repo, settings)
    target = ItemScanTarget(
        id=item["id"],
        market_hash_name=item["market_hash_name"],
        display_name=item.get("display_name") or item["market_hash_name"],
        steam_market_url=item.get("steam_market_url") or build_market_listing_url(item["market_hash_name"]),
        rule_id=item.get("rule_id"),
        collection_name=item.get("collection_name", ""),
    )
    urls = _probe_urls(item)
    results: list[dict[str, Any]] = []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("Для Steam probe нужен playwright. Установи зависимости из requirements.txt.") from error

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings.headless)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        blocker = install_resource_blocking(context, settings.browser_optimization_settings)
        page = context.new_page()
        try:
            for index, probe_url in enumerate(urls):
                if index > 0 and delay_seconds > 0:
                    time.sleep(delay_seconds)
                requests: list[dict[str, str]] = []

                def record_request(request) -> None:
                    if request.resource_type not in {"document", "xhr", "fetch"}:
                        return
                    if len(requests) >= 30:
                        return
                    requests.append(
                        {
                            "type": request.resource_type,
                            "method": request.method,
                            "url": _sanitize_url(request.url),
                        }
                    )

                page.on("request", record_request)
                blocked_before = blocker.blocked_count
                result: dict[str, Any] = {"name": probe_url.name, "url": probe_url.url, "mode": probe_url.mode}
                try:
                    response = page.goto(probe_url.url, wait_until="domcontentloaded", timeout=30000)
                    check_steam_access(page, response=response)
                    close_cookie_banner(page)
                    page.wait_for_timeout(1500)
                    check_steam_access(page)
                    result["final_url"] = page.url
                    if probe_url.action == "click_exterior":
                        clicked = _click_exterior_filter(page, str(item.get("exterior") or ""))
                        result["clicked_filter"] = clicked
                        page.wait_for_timeout(1200)
                        check_steam_access(page)
                    if probe_url.mode == "render":
                        result.update(_probe_render_response(response))
                    else:
                        result.update(_probe_dom_page(page, target, item, rates, limit))
                except Exception as error:
                    result.update({"error": str(error), "cards_seen": 0, "parsed_cards": 0, "exact_cards": 0})
                finally:
                    page.remove_listener("request", record_request)
                result["requests"] = requests
                result["resource_blocked"] = blocker.blocked_count - blocked_before
                results.append(result)
        finally:
            context.close()
            browser.close()

    best = max(results, key=lambda row: (int(row.get("exact_cards") or 0), int(row.get("parsed_cards") or 0)), default={})
    return {
        "item": {
            "id": item["id"],
            "market_hash_name": item["market_hash_name"],
            "exterior": item.get("exterior"),
            "is_souvenir": bool(item.get("is_souvenir")),
            "is_stattrak": bool(item.get("is_stattrak")),
        },
        "best_variant": best.get("name", ""),
        "results": results,
    }


def render_probe_report(report: dict[str, Any]) -> str:
    item = report["item"]
    lines = [
        "Steam market filter probe",
        f"item={item['id']} | {item['market_hash_name']}",
        f"exterior={item.get('exterior')}; souvenir={item.get('is_souvenir')}; stattrak={item.get('is_stattrak')}",
        f"best_variant={report.get('best_variant') or 'none'}",
    ]
    for result in report["results"]:
        lines.append("")
        lines.append(f"{result['name']} [{result['mode']}]")
        lines.append(result["url"])
        if result.get("error"):
            lines.append(f"error={result['error']}")
        if result.get("final_url"):
            lines.append(f"final_url={result['final_url']}")
        if result["mode"] == "render":
            lines.append(
                f"render_success={result.get('render_success')} total_count={result.get('total_count')} "
                f"results_html_len={result.get('results_html_len')}"
            )
        else:
            lines.append(
                f"cards_seen={result.get('cards_seen', 0)} parsed={result.get('parsed_cards', 0)} "
                f"exact={result.get('exact_cards', 0)} rejected={result.get('rejected_cards', 0)}"
            )
        if result.get("page_item_name"):
            lines.append(f"page_item_name={result['page_item_name']}")
        if result.get("clicked_filter"):
            lines.append(f"clicked_filter={result['clicked_filter']}")
        for example in result.get("exact_examples", [])[:3]:
            lines.append(f"+ exact {example.get('skin_name')} {example.get('price_rub')} RUB")
        for example in result.get("rejected_examples", [])[:3]:
            lines.append(f"- rejected {example.get('skin_name')} {example.get('price_rub')} RUB")
        for hint in result.get("filter_hints", [])[:8]:
            lines.append(f"filter_hint: {hint}")
        for link in result.get("market_links", [])[:5]:
            lines.append(f"market_link: {link.get('text') or '?'} -> {link.get('href')}")
        if result.get("requests"):
            lines.append("requests:")
            for request in result["requests"][:8]:
                lines.append(f"  {request['type']} {request['method']} {request['url']}")
    return "\n".join(lines)


def _probe_dom_page(page, target: ItemScanTarget, item: dict[str, Any], rates: CurrencyRates, limit: int) -> dict[str, Any]:
    cards = extract_visible_cards_raw(page)
    page_item_name = get_page_item_name(page)
    exact_examples: list[dict[str, Any]] = []
    rejected_examples: list[dict[str, Any]] = []
    parsed_cards = 0
    exact_cards = 0
    rejected_cards = 0
    seen: set[str] = set()
    for card in cards:
        text = str(card.get("text", "")).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        listing = parse_card(card, target, rates, page_item_name)
        if listing is None:
            continue
        parsed_cards += 1
        parsed = parsed_listing_from_market_listing(listing, item)
        if passes_item_match(parsed, item, require_exact_item_match=True):
            exact_cards += 1
            if len(exact_examples) < 5:
                exact_examples.append(_listing_example(listing))
        else:
            rejected_cards += 1
            if len(rejected_examples) < 5:
                rejected_examples.append(_listing_example(listing))
        if parsed_cards >= limit:
            break
    return {
        "final_url": page.url,
        "page_item_name": page_item_name,
        "filter_hints": _extract_filter_hints(page),
        "market_links": _extract_market_links(page),
        "cards_seen": len(seen),
        "parsed_cards": parsed_cards,
        "exact_cards": exact_cards,
        "rejected_cards": rejected_cards,
        "exact_examples": exact_examples,
        "rejected_examples": rejected_examples,
    }


def _probe_render_response(response) -> dict[str, Any]:
    if response is None:
        return {"render_success": False, "total_count": None, "results_html_len": 0}
    try:
        data = json.loads(response.text())
    except Exception as error:
        return {"render_success": False, "total_count": None, "results_html_len": 0, "render_error": str(error)}
    return {
        "render_success": bool(data.get("success")),
        "total_count": data.get("total_count"),
        "results_html_len": len(str(data.get("results_html") or "")),
    }


def _probe_urls(item: dict[str, Any]) -> list[ProbeUrl]:
    listing_url = item.get("steam_market_url") or build_market_listing_url(item["market_hash_name"])
    base_name = _base_market_name(item["market_hash_name"])
    return [
        ProbeUrl("listing_current", _with_listing_params(listing_url)),
        ProbeUrl("listing_direct_item_filters", _with_listing_params(listing_url, steam_market_filter_params(item))),
        ProbeUrl("listing_click_exterior_filter", _with_listing_params(listing_url), action="click_exterior"),
        ProbeUrl("listing_filter_exact_name", _with_listing_params(listing_url, {"filter": item["market_hash_name"]})),
        ProbeUrl("listing_filter_base_name", _with_listing_params(listing_url, {"filter": base_name})),
        ProbeUrl("listing_render", _render_url(listing_url), mode="render"),
        ProbeUrl("search_exact_with_item_filters", _search_url(item, item["market_hash_name"])),
        ProbeUrl("search_base_with_item_filters", _search_url(item, base_name)),
    ]


def _click_exterior_filter(page, exterior: str) -> str:
    if not exterior:
        return ""
    try:
        return str(
            page.evaluate(
                """
                (exterior) => {
                    const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
                    const isVisible = (element) => {
                        const rect = element.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.top < window.innerHeight;
                    };
                    const candidates = Array.from(document.querySelectorAll("button,[role=button],a,label,span,div"))
                        .filter(isVisible)
                        .map((element) => ({element, text: normalize(element.innerText || element.textContent || "")}))
                        .filter((entry) => entry.text === exterior || entry.text.startsWith(exterior + " "));
                    candidates.sort((left, right) => {
                        const lr = left.element.getBoundingClientRect();
                        const rr = right.element.getBoundingClientRect();
                        return (lr.width * lr.height) - (rr.width * rr.height);
                    });
                    for (const candidate of candidates) {
                        const clickable = candidate.element.closest("button,[role=button],a,label") || candidate.element;
                        try {
                            clickable.scrollIntoView({block: "center", inline: "center"});
                            clickable.click();
                            return candidate.text;
                        } catch (_) {
                        }
                    }
                    return "";
                }
                """,
                exterior,
            )
        )
    except Exception:
        return ""


def _extract_filter_hints(page) -> list[str]:
    try:
        return page.evaluate(
            r"""
            () => {
                const normalize = (text) => (text || "").replace(/\s+/g, " ").trim();
                const re = /(Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred|Exterior|Wear|Condition|Souvenir|StatTrak|Filter)/i;
                const values = [];
                for (const element of Array.from(document.querySelectorAll("button,a,label,option,input,select,span,div"))) {
                    const text = normalize(element.innerText || element.textContent || element.getAttribute("aria-label") || element.getAttribute("placeholder") || element.value || "");
                    if (!text || text.length > 160 || !re.test(text)) continue;
                    values.push(text);
                }
                return Array.from(new Set(values)).slice(0, 30);
            }
            """
        )
    except Exception:
        return []


def _extract_market_links(page) -> list[dict[str, str]]:
    try:
        return page.evaluate(
            r"""
            () => {
                const normalize = (text) => (text || "").replace(/\s+/g, " ").trim();
                const links = [];
                for (const link of Array.from(document.querySelectorAll('a[href*="/market/"]'))) {
                    const text = normalize(link.innerText || link.textContent || link.getAttribute("aria-label") || "");
                    const href = link.href || "";
                    if (!href) continue;
                    if (!/(Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred|Souvenir|market\/search|market\/listings)/i.test(text + " " + href)) continue;
                    links.push({text, href});
                }
                const seen = new Set();
                return links.filter((item) => {
                    const key = item.text + "|" + item.href;
                    if (seen.has(key)) return false;
                    seen.add(key);
                    return true;
                }).slice(0, 20);
            }
            """
        )
    except Exception:
        return []


def _with_listing_params(url: str, extra: dict[str, str] | None = None) -> str:
    parsed = urllib.parse.urlsplit(url)
    params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    params.update(extra or {})
    params.update({"sort_column": "price", "sort_dir": "asc"})
    params.setdefault("start", "0")
    params.setdefault("l", "english")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(params), "p1_price_asc"))


def _render_url(listing_url: str) -> str:
    parsed = urllib.parse.urlsplit(listing_url)
    path = parsed.path.rstrip("/") + "/render/"
    params = {
        "query": "",
        "start": "0",
        "count": "20",
        "country": "US",
        "language": "english",
        "currency": "3",
    }
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, urllib.parse.urlencode(params), ""))


def _search_url(item: dict[str, Any], query: str) -> str:
    params: list[tuple[str, str]] = [("appid", "730"), ("q", query), ("l", "english")]
    exterior_tag = EXTERIOR_FILTER_TAGS.get(str(item.get("exterior") or ""))
    if exterior_tag:
        params.append(("category_730_Exterior[]", exterior_tag))
    if item.get("is_souvenir"):
        params.append(("category_730_Quality[]", "tag_tournament"))
    if item.get("is_stattrak"):
        params.append(("category_730_Quality[]", "tag_strange"))
    return "https://steamcommunity.com/market/search?" + urllib.parse.urlencode(params)


def _base_market_name(market_hash_name: str) -> str:
    return re.sub(r"\s+\((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)\s*$", "", market_hash_name)


def _listing_example(listing) -> dict[str, Any]:
    return {
        "skin_name": listing.skin_name,
        "price_rub": listing.buy_price_rub,
        "float_value": listing.float_value,
        "pattern": listing.pattern,
    }


def _latest_rates(repo: Repository, settings) -> CurrencyRates:
    latest = repo.latest_currency_rate()
    if latest:
        return CurrencyRates(
            usd_to_rub=float(latest["usd_to_rub"]),
            eur_to_rub=float(latest["eur_to_rub"]),
            source=str(latest["source"]),
            fetched_at=_parse_datetime(str(latest["fetched_at"])),
            is_fallback=bool(latest["is_fallback"]),
        )
    return CurrencyRates(
        usd_to_rub=settings.fallback_usd_to_rub,
        eur_to_rub=settings.fallback_eur_to_rub,
        source="settings-fallback",
        fetched_at=datetime.now(timezone.utc),
        is_fallback=True,
    )


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _sanitize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    params = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if any(marker in key.lower() for marker in ["session", "token", "auth"]):
            params.append((key, "<redacted>"))
        else:
            params.append((key, value))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(params), parsed.fragment))


def report_to_dict(report: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(report, default=lambda value: asdict(value) if hasattr(value, "__dataclass_fields__") else str(value)))
