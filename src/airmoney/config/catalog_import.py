from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from airmoney.config.import_export import load_config_text
from airmoney.config.models import Collection, EXTERIORS, ItemDefinition, SnipingRule, to_bool
from airmoney.storage.repositories import Repository
from airmoney.steam.collections import build_exterior_variants, build_market_listing_url, slugify


@dataclass
class CatalogImportResult:
    valid: bool
    errors: list[str]
    collections_count: int = 0
    items_count: int = 0
    rules_count: int = 0


def import_catalog_text(repo: Repository, text: str, apply: bool = True) -> CatalogImportResult:
    try:
        data = load_config_text(text)
    except Exception as error:
        return CatalogImportResult(False, [f"Не удалось прочитать каталог: {error}"])

    result, collections, items, rules = parse_catalog(data)
    if result.valid and apply:
        for collection in collections:
            repo.save_collection(collection)
        for item in items:
            repo.save_item(item)
        for rule in rules:
            repo.save_rule(rule)
    return result


def parse_catalog(data: dict[str, Any]) -> tuple[CatalogImportResult, list[Collection], list[ItemDefinition], list[SnipingRule]]:
    errors: list[str] = []
    collections: list[Collection] = []
    items: list[ItemDefinition] = []
    rules: list[SnipingRule] = []

    raw_collections = data.get("collections", [])
    if raw_collections and not isinstance(raw_collections, list):
        errors.append("collections должен быть списком.")
        raw_collections = []

    collection_ids: set[str] = set()
    for index, row in enumerate(raw_collections, start=1):
        if not isinstance(row, dict):
            errors.append(f"collections[{index}] должен быть объектом.")
            continue
        collection = _collection_from_row(row, f"collections[{index}]", errors)
        if collection:
            collections.append(collection)
            collection_ids.add(collection.id)
            nested_items = row.get("items", [])
            if nested_items:
                if not isinstance(nested_items, list):
                    errors.append(f"collections[{index}].items должен быть списком.")
                else:
                    items.extend(_items_from_rows(nested_items, collection.id, f"collections[{index}].items", errors))

    top_items = data.get("items", [])
    if top_items:
        if not isinstance(top_items, list):
            errors.append("items должен быть списком.")
        else:
            items.extend(_items_from_rows(top_items, None, "items", errors))

    for item in items:
        if item.collection_id not in collection_ids:
            errors.append(f"Предмет {item.id!r} ссылается на неизвестную коллекцию {item.collection_id!r}.")

    raw_rules = data.get("rules", [])
    item_ids = {item.id for item in items}
    if raw_rules:
        if not isinstance(raw_rules, list):
            errors.append("rules должен быть списком.")
        else:
            for index, row in enumerate(raw_rules, start=1):
                if not isinstance(row, dict):
                    errors.append(f"rules[{index}] должен быть объектом.")
                    continue
                rule = _rule_from_row(row, f"rules[{index}]", errors)
                if rule:
                    if rule.item_definition_id not in item_ids:
                        errors.append(f"rules[{index}].item_id ссылается на неизвестный предмет {rule.item_definition_id!r}.")
                    rules.append(rule)

    _unique([collection.id for collection in collections], "collections.id", errors)
    _unique([item.id for item in items], "items.id", errors)
    _unique([rule.id for rule in rules], "rules.id", errors)

    result = CatalogImportResult(
        valid=not errors,
        errors=errors,
        collections_count=len(collections),
        items_count=len(items),
        rules_count=len(rules),
    )
    return result, collections, items, rules


def _collection_from_row(row: dict[str, Any], label: str, errors: list[str]) -> Collection | None:
    collection_id = str(row.get("id") or slugify(str(row.get("name", "")))).strip()
    name = str(row.get("name") or "").strip()
    if not collection_id:
        errors.append(f"{label}.id обязателен.")
    if not name:
        errors.append(f"{label}.name обязателен.")
    if not collection_id or not name:
        return None
    return Collection(
        id=collection_id,
        name=name,
        steam_collection_url=str(row.get("steam_collection_url", "") or ""),
        enabled=to_bool(row.get("enabled", True)),
    )


def _items_from_rows(
    rows: list[dict[str, Any]],
    default_collection_id: str | None,
    label: str,
    errors: list[str],
) -> list[ItemDefinition]:
    result: list[ItemDefinition] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"{label}[{index}] должен быть объектом.")
            continue
        collection_id = str(row.get("collection_id") or default_collection_id or "").strip()
        if not collection_id:
            errors.append(f"{label}[{index}].collection_id обязателен.")
            continue
        market_hash_name = str(row.get("market_hash_name") or "").strip()
        if market_hash_name:
            result.append(_item_from_market_name(row, collection_id, market_hash_name))
            continue
        base_name = str(row.get("base_name") or row.get("display_name") or "").strip()
        if not base_name:
            errors.append(f"{label}[{index}].market_hash_name или base_name обязателен.")
            continue
        exteriors = _exteriors_from_row(row, errors, f"{label}[{index}].exteriors")
        prefix = str(row.get("market_name_prefix") or "").strip()
        for variant in build_exterior_variants(base_name, exteriors):
            name = f"{prefix} {variant}" if prefix else variant
            variant_row = dict(row)
            variant_row.pop("id", None)
            result.append(_item_from_market_name(variant_row, collection_id, name, display_name=base_name))
    return result


def _item_from_market_name(
    row: dict[str, Any],
    collection_id: str,
    market_hash_name: str,
    display_name: str | None = None,
) -> ItemDefinition:
    item_id = str(row.get("id") or slugify(f"{collection_id}_{market_hash_name}"))
    prefix = str(row.get("market_name_prefix") or "").strip()
    return ItemDefinition(
        id=item_id,
        collection_id=collection_id,
        market_hash_name=market_hash_name,
        display_name=display_name or str(row.get("display_name") or market_hash_name),
        weapon_type=str(row.get("weapon_type", "") or ""),
        rarity=str(row.get("rarity", "") or ""),
        quality=str(row.get("quality", "") or prefix),
        exterior=_extract_exterior(market_hash_name),
        is_souvenir=to_bool(row.get("is_souvenir", False)) or prefix.lower() == "souvenir",
        is_stattrak=to_bool(row.get("is_stattrak", False)) or prefix.lower().startswith("stattrak"),
        steam_market_url=str(row.get("steam_market_url") or build_market_listing_url(market_hash_name)),
        enabled=to_bool(row.get("enabled", True)),
    )


def _rule_from_row(row: dict[str, Any], label: str, errors: list[str]) -> SnipingRule | None:
    item_id = str(row.get("item_id") or row.get("item_definition_id") or "").strip()
    if not item_id:
        errors.append(f"{label}.item_id обязателен.")
        return None
    rule_id = str(row.get("id") or f"{item_id}_rule")
    return SnipingRule(
        id=rule_id,
        item_definition_id=item_id,
        enabled=to_bool(row.get("enabled", True)),
        max_buy_price_rub=_optional_float(row.get("max_buy_price_rub")),
        target_resale_price_rub=_optional_float(row.get("target_resale_price_rub")),
        custom_roi_percent=_optional_float(row.get("custom_roi_percent")),
        min_profit_rub=_optional_float(row.get("min_profit_rub")),
        min_roi_percent=_optional_float(row.get("min_roi_percent")),
        float_min=_optional_float(row.get("float_min")),
        float_max=_optional_float(row.get("float_max")),
        target_float_min=_optional_float(row.get("target_float_min")),
        target_float_max=_optional_float(row.get("target_float_max")),
        pattern_ranges=str(row.get("pattern_ranges", "") or ""),
        priority=int(row.get("priority", 0) or 0),
        telegram_alert_enabled=to_bool(row.get("telegram_alert_enabled", True)),
        notes=str(row.get("notes", "") or ""),
    )


def _exteriors_from_row(row: dict[str, Any], errors: list[str], label: str) -> list[str]:
    raw = row.get("exteriors") or row.get("selected_exteriors") or EXTERIORS
    if not isinstance(raw, list):
        errors.append(f"{label} должен быть списком.")
        return EXTERIORS
    selected = [str(value) for value in raw]
    unknown = [value for value in selected if value not in EXTERIORS]
    if unknown:
        errors.append(f"{label} содержит неизвестные состояния: {', '.join(unknown)}.")
    return [value for value in selected if value in EXTERIORS]


def _extract_exterior(market_hash_name: str) -> str:
    for exterior in EXTERIORS:
        if market_hash_name.endswith(f"({exterior})"):
            return exterior
    return ""


def _optional_float(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(str(value).replace(",", "."))


def _unique(values: list[str], label: str, errors: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            errors.append(f"{label} должен быть уникальным: {value!r}.")
        seen.add(value)
