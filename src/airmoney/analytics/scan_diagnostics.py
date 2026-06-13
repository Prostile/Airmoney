from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any

from airmoney.storage.repositories import Repository


EXTERIOR_RE = re.compile(r"\((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)", re.IGNORECASE)


def build_scan_diagnostics(repo: Repository, limit: int = 2) -> dict[str, Any]:
    limit = max(1, int(limit))
    with repo.connection() as connection:
        runs = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, status, trigger, started_at, finished_at, total_items,
                       scanned_items, listings_saved, candidates_saved,
                       selected_targets_count, skipped_by_queue_count,
                       skipped_by_item_cooldown_count, skipped_by_collection_cooldown_count,
                       early_stop_count, resource_blocked_count, shallow_skipped_count,
                       deep_scan_count, steam_cooldown_active, steam_cooldown_until,
                       error, progress_message
                FROM scan_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        settings = dict(
            connection.execute(
                """
                SELECT check_interval_seconds, max_scrolls, request_delay_seconds,
                       anomaly_config, scan_queue_config
                FROM settings
                WHERE id = 1
                """
            ).fetchone()
        )

        diagnostics = []
        for run in runs:
            items = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT sir.item_id, i.market_hash_name, i.display_name, i.exterior,
                           i.is_souvenir, i.is_stattrak, i.last_parsed_at, i.last_scanned_at,
                           sir.status, sir.cards_seen, sir.exact_cards,
                           sir.target_listings_reached, sir.early_stop_reason,
                           sir.shallow_gap_percent, sir.deep_scan_performed,
                           sir.used_historical_baseline, sir.duration_ms, sir.error,
                           sir.created_at
                    FROM scan_item_results sir
                    LEFT JOIN items i ON i.id = sir.item_id
                    WHERE sir.scan_run_id = ?
                    ORDER BY sir.created_at
                    """,
                    (run["id"],),
                ).fetchall()
            ]
            rejections = _load_rejections(connection, run, [row["item_id"] for row in items])
            diagnostics.append(_diagnose_run(run, items, rejections))

    return {"settings": _settings_summary(settings), "runs": diagnostics}


def render_scan_diagnostics(report: dict[str, Any]) -> str:
    lines: list[str] = []
    settings = report.get("settings", {})
    lines.append("Scan diagnostics")
    lines.append(
        "Settings: "
        f"interval={settings.get('check_interval_seconds')}s, "
        f"max_scrolls={settings.get('max_scrolls')}, "
        f"request_delay={settings.get('request_delay_seconds')}s, "
        f"queue_max={settings.get('queue_max_items_per_cycle')}, "
        f"require_exact={settings.get('require_exact_item_match')}"
    )
    for run in report.get("runs", []):
        lines.append("")
        lines.append(
            f"{run['id']} [{run['status']}] {run['started_at']} -> {run.get('finished_at') or ''}"
        )
        lines.append(
            f"targets={run['total_items']} scanned={run['scanned_items']} "
            f"cards={run['cards_seen']} exact={run['exact_cards']} "
            f"listings={run['listings_saved']} candidates={run['candidates_saved']} "
            f"resources_blocked={run['resource_blocked_count']}"
        )
        lines.append(f"verdict: {run['verdict']}")
        if run["status_counts"]:
            lines.append("item statuses: " + _format_counts(run["status_counts"]))
        if run["rejected_count"]:
            lines.append(
                f"exact rejected={run['rejected_count']}; rejected exteriors="
                f"{_format_counts(run['rejected_exteriors'])}"
            )
        for example in run["examples"][:5]:
            price = example.get("price_rub")
            price_text = f", {price:.2f} RUB" if isinstance(price, int | float) else ""
            lines.append(f"- rejected {example.get('skin_name') or '?'}{price_text}")
    return "\n".join(lines)


def _settings_summary(row: dict[str, Any]) -> dict[str, Any]:
    anomaly = _loads(row.get("anomaly_config"))
    queue = _loads(row.get("scan_queue_config"))
    sample = anomaly.get("sample", {}) if isinstance(anomaly, dict) else {}
    return {
        "check_interval_seconds": row.get("check_interval_seconds"),
        "max_scrolls": row.get("max_scrolls"),
        "request_delay_seconds": row.get("request_delay_seconds"),
        "queue_max_items_per_cycle": queue.get("max_items_per_cycle") if isinstance(queue, dict) else None,
        "require_exact_item_match": sample.get("require_exact_item_match"),
    }


def _load_rejections(connection, run: dict[str, Any], item_ids: list[str]) -> dict[str, Any]:
    if not item_ids:
        return {"total": 0, "examples": []}
    params: list[Any] = [run["started_at"]]
    time_clause = "created_at >= ?"
    if run.get("finished_at"):
        time_clause += " AND created_at <= ?"
        params.append(run["finished_at"])
    placeholders = ",".join("?" for _ in item_ids)
    params.extend(item_ids)
    rows = connection.execute(
        f"""
        SELECT entity_id, payload, created_at
        FROM user_actions
        WHERE entity_type = 'steam_scan'
          AND action = 'exact_match_rejected'
          AND {time_clause}
          AND entity_id IN ({placeholders})
        ORDER BY created_at
        """,
        params,
    ).fetchall()
    total = 0
    examples: list[dict[str, Any]] = []
    for row in rows:
        payload = _loads(row["payload"])
        if not isinstance(payload, dict):
            continue
        total += int(payload.get("rejected_count") or 0)
        for example in payload.get("examples") or []:
            if isinstance(example, dict):
                examples.append(
                    {
                        "item_id": row["entity_id"],
                        "skin_name": example.get("skin_name"),
                        "price_rub": example.get("price_rub"),
                    }
                )
    return {"total": total, "examples": examples}


def _diagnose_run(run: dict[str, Any], items: list[dict[str, Any]], rejections: dict[str, Any]) -> dict[str, Any]:
    cards_seen = sum(int(row.get("cards_seen") or 0) for row in items)
    exact_cards = sum(int(row.get("exact_cards") or 0) for row in items)
    status_counts = Counter(str(row.get("status") or "") for row in items)
    rejected_exteriors = Counter()
    for example in rejections["examples"]:
        exterior = _extract_exterior(str(example.get("skin_name") or ""))
        if exterior:
            rejected_exteriors[exterior] += 1

    verdict = _verdict(run, cards_seen, exact_cards, int(rejections["total"] or 0))
    return {
        **run,
        "cards_seen": cards_seen,
        "exact_cards": exact_cards,
        "status_counts": dict(status_counts),
        "rejected_count": int(rejections["total"] or 0),
        "rejected_exteriors": dict(rejected_exteriors),
        "examples": rejections["examples"][:10],
        "verdict": verdict,
    }


def _verdict(run: dict[str, Any], cards_seen: int, exact_cards: int, rejected_count: int) -> str:
    if run.get("steam_cooldown_active"):
        return "Steam cooldown active; no Steam IO was attempted."
    if cards_seen == 0:
        return "Extractor did not see market cards; check page loading, resource blocking, or Steam markup."
    if exact_cards == 0 and rejected_count > 0:
        return "Parser sees cards, but strict exact-match rejects them. Current Steam cards do not match target item/exterior."
    if exact_cards == 0:
        return "Cards were seen, but none became exact listings; inspect parser output and target config."
    if int(run.get("listings_saved") or 0) == 0:
        return "Exact listings exist, but persistence did not save them."
    if int(run.get("candidates_saved") or 0) == 0:
        return "Listings were saved, but anomaly thresholds produced no candidate."
    return "Listings and candidates were produced."


def _extract_exterior(value: str) -> str:
    match = EXTERIOR_RE.search(value)
    return match.group(1) if match else ""


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _loads(value: Any) -> Any:
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}
