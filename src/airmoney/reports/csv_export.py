from __future__ import annotations

import csv
from pathlib import Path

from airmoney.storage.repositories import Repository


def export_candidates_csv(path: str | Path, repo: Repository | None = None) -> Path:
    repository = repo or Repository()
    rows = repository.list_candidates(limit=100000)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    default_fieldnames = [
        "id",
        "status",
        "recommendation_level",
        "recommendation_score",
        "recommendation_reason",
        "analysis_mode",
        "alert_level",
        "anomaly_score",
        "skin_name",
        "collection_name",
        "item_id",
        "rule_id",
        "buy_price_rub",
        "estimated_resale_price_rub",
        "estimated_net_resale_rub",
        "estimated_profit_rub",
        "estimated_roi_percent",
        "fair_price_rub",
        "local_median_rub",
        "float_peer_median_rub",
        "historical_baseline_rub",
        "local_discount_percent",
        "float_peer_discount_percent",
        "historical_discount_percent",
        "robust_z",
        "float_value",
        "float_bucket",
        "sample_size",
        "neighbor_count",
        "pattern",
        "anomaly_reasons",
        "listing_url",
        "search_url",
        "currency_source",
        "currency_fetched_at",
        "created_at",
        "updated_at",
    ]
    fieldnames = list(rows[0].keys()) if rows else default_fieldnames
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return output
