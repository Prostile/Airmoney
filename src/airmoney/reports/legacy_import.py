from __future__ import annotations

import csv
from pathlib import Path

from airmoney.config.models import Collection, ItemDefinition, MarketListing, utc_now_iso
from airmoney.recommendation.engine import evaluate_listing
from airmoney.storage.repositories import Repository
from airmoney.steam.collections import build_market_listing_url, slugify
from airmoney.steam.extractor import listing_identity, market_search_url


def import_legacy_matches_csv(
    path: str | Path,
    repo: Repository | None = None,
    collection_id: str = "legacy_csv",
) -> int:
    repository = repo or Repository()
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    repository.save_collection(
        Collection(
            id=collection_id,
            name="Legacy CSV",
            steam_collection_url="",
            enabled=True,
        )
    )

    imported = 0
    with input_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        for row in reader:
            source_label = str(row.get("source_label") or row.get("name") or "Legacy item").strip()
            market_hash_name = source_label
            item_id = slugify(f"{collection_id}_{market_hash_name}")
            source_url = str(row.get("source_url") or "").strip()
            repository.save_item(
                ItemDefinition(
                    id=item_id,
                    collection_id=collection_id,
                    market_hash_name=market_hash_name,
                    display_name=source_label,
                    steam_market_url=source_url or build_market_listing_url(market_hash_name),
                    enabled=True,
                )
            )
            price_rub = _float_or_none(row.get("price_rub"))
            if price_rub is None:
                continue
            now = _legacy_time(row.get("scan_time"))
            listing_url = str(row.get("href") or source_url or "").strip()
            listing = MarketListing(
                id=listing_identity(item_id, row.get("name"), listing_url, row.get("pattern"), row.get("wear")),
                item_definition_id=item_id,
                rule_id=repository.get_rule_for_item(item_id)["id"],
                skin_name=str(row.get("name") or source_label),
                market_hash_name=market_hash_name,
                listing_url=listing_url or source_url,
                search_url=market_search_url(market_hash_name),
                buy_price_rub=price_rub,
                buy_price_original=_float_or_none(row.get("price_usd")),
                currency_original=str(row.get("currency_source") or "RUB"),
                currency_rate=None,
                currency_source=str(row.get("currency_source") or "legacy_csv"),
                currency_fetched_at=now,
                float_value=_float_or_none(row.get("wear")),
                pattern=_int_or_none(row.get("pattern")),
                raw_text=str(row.get("raw_text") or ""),
                first_seen_at=now,
                last_seen_at=now,
                is_active=True,
                parse_status="legacy_csv",
            )
            repository.save_listing(listing)
            candidate = evaluate_listing(
                listing_id=listing.id,
                buy_price_rub=listing.buy_price_rub,
                float_value=listing.float_value,
                pattern=listing.pattern,
                rule=repository.get_rule_for_item(item_id),
                settings=repository.get_settings(),
            )
            repository.save_candidate(candidate)
            imported += 1
    return imported


def _float_or_none(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _int_or_none(value) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return None


def _legacy_time(value) -> str:
    if value and str(value).strip():
        return str(value).strip()
    return utc_now_iso()
