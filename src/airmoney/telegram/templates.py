from __future__ import annotations


def format_money(value, signed: bool = False) -> str:
    try:
        number = round(float(value))
    except Exception:
        return "—"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:,}".replace(",", " ") + " ₽"


def format_percent(value) -> str:
    try:
        number = float(value)
    except Exception:
        return "—"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.1f}%"


def format_float(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):.4f}"
    except Exception:
        return "—"


def resolve_display_baseline(candidate: dict) -> tuple[str, float | None]:
    if candidate.get("exit_price_rub") is not None:
        return "Exit", _to_float(candidate.get("exit_price_rub"))
    if candidate.get("historical_baseline_rub") is not None:
        return "Hist", _to_float(candidate.get("historical_baseline_rub"))
    if candidate.get("float_peer_median_rub") is not None:
        return "Avg", _to_float(candidate.get("float_peer_median_rub"))
    if candidate.get("local_median_rub") is not None:
        return "Avg", _to_float(candidate.get("local_median_rub"))
    if candidate.get("fair_price_rub") is not None:
        return "Fair", _to_float(candidate.get("fair_price_rub"))
    if candidate.get("estimated_resale_price_rub") is not None:
        return "Fair", _to_float(candidate.get("estimated_resale_price_rub"))
    return "Avg", None


def resolve_display_discount(candidate: dict) -> float | None:
    for key in ("float_peer_discount_percent", "historical_discount_percent", "local_discount_percent"):
        value = _to_float(candidate.get(key))
        if value is not None:
            return value
    return None


def candidate_alert(
    candidate: dict,
    details_url: str,
    include_link: bool = True,
    include_pattern: bool = False,
    include_sample_stats: bool = False,
    include_reasons: bool = False,
) -> str:
    baseline_label, baseline_value = resolve_display_baseline(candidate)
    discount = resolve_display_discount(candidate)
    title = candidate.get("skin_name") or candidate.get("display_name") or "-"
    level = str(candidate.get("recommendation_level") or candidate.get("alert_level") or "good").upper()
    lines = [
        f"[{level}] {title}",
        "",
        f"Buy: {format_money(candidate.get('buy_price_rub'))}",
        f"Float: {format_float(candidate.get('float_value'))}",
        f"{baseline_label}: {format_money(baseline_value)}",
        (
            f"Profit: {format_money(candidate.get('estimated_profit_rub'), signed=True)} / "
            f"{format_percent(candidate.get('estimated_roi_percent'))}"
        ),
        f"Discount: -{abs(discount):.0f}%" if discount is not None else "Discount: —",
        f"Score: {round(_to_float(candidate.get('anomaly_score')) or _to_float(candidate.get('recommendation_score')) or 0)}",
    ]
    if include_pattern and candidate.get("pattern") not in {None, ""}:
        lines.append(f"Pattern: {candidate.get('pattern')}")
    if include_sample_stats:
        lines.append(
            f"Sample: {candidate.get('sample_size') or 0} / nn {candidate.get('neighbor_count') or 0}"
        )
    if candidate.get("market_confidence"):
        lines.append(f"Conf: {candidate.get('market_confidence')}")
    if candidate.get("requires_sweep"):
        lines.append(
            "Sweep: yes"
            f" / pack {candidate.get('pack_size') or 0}"
            f" / capital {format_money(candidate.get('capital_required_rub'))}"
        )
    elif candidate.get("pack_id"):
        lines.append(f"Pack: {candidate.get('pack_size') or 0} near-floor listings")
    if candidate.get("manual_review_required"):
        lines.append("Manual review: yes")
    if include_reasons:
        reasons = _reason_lines(candidate)
        if reasons:
            lines.extend(["", "Reasons:", *reasons])
    if include_link:
        lines.extend(["", f"Link: {candidate.get('listing_url') or details_url}"])
    return "\n".join(lines)


def batch_alert(candidates: list[dict], dashboard_url: str) -> str:
    lines = [f"Airmoney: {len(candidates)} anomalies", ""]
    for index, candidate in enumerate(candidates, start=1):
        baseline_label, baseline_value = resolve_display_baseline(candidate)
        level = str(candidate.get("recommendation_level") or "good").upper()
        short_level = "CRIT" if level == "CRITICAL" else level
        title = candidate.get("skin_name") or candidate.get("display_name") or "-"
        lines.extend(
            [
                f"{index}. [{short_level}] {title}",
                (
                    f"{format_money(candidate.get('buy_price_rub'))} → "
                    f"{baseline_label.lower()} {format_money(baseline_value)} | "
                    f"{format_money(candidate.get('estimated_profit_rub'), signed=True)} / "
                    f"{format_percent(candidate.get('estimated_roi_percent'))} | "
                    f"fl {format_float(candidate.get('float_value'))}"
                ),
                "",
            ]
        )
    lines.append(f"Dashboard: {dashboard_url}")
    return "\n".join(lines).strip()


def pack_alert(pack: dict, items: list[dict], dashboard_url: str) -> str:
    title = pack.get("market_hash_name") or pack.get("display_name") or pack.get("item_display_name") or "-"
    pack_size = pack.get("pack_size") or len(items)
    lines = [
        f"[PACK] {title}",
        "",
        f"Level: {str(pack.get('alert_level') or 'watch').upper()}",
        f"Lots: {pack_size}",
        (
            f"Buy: {format_money(pack.get('min_buy_price_rub'))}"
            f"-{format_money(pack.get('max_buy_price_rub'))}"
        ),
        (
            f"Floats: {format_float(pack.get('min_float'))}"
            f"-{format_float(pack.get('max_float'))}"
        ),
        f"Cost: {format_money(pack.get('pack_cost_rub'))}",
        f"Next floor: {format_money(pack.get('next_floor_after_pack_rub'))}",
        (
            f"Pack profit: {format_money(pack.get('estimated_profit_rub'), signed=True)} / "
            f"{format_percent(pack.get('estimated_roi_percent'))}"
        ),
        f"Capital: {format_money(pack.get('capital_required_rub') or pack.get('pack_cost_rub'))}",
        f"Conf: {pack.get('market_confidence') or pack.get('pack_confidence') or '-'}",
        f"Mode: {'sweep required' if pack.get('requires_sweep') else 'solo'}",
        "",
        "Solo:",
    ]
    for item in items[:8]:
        solo_profit = item.get("solo_net_profit_rub")
        solo_roi = item.get("solo_roi_percent")
        solo_text = (
            f"{format_money(solo_profit, signed=True)} / {format_percent(solo_roi)}"
            if solo_profit is not None
            else "solo weak/negative"
        )
        lines.append(
            f"- {format_money(item.get('buy_price_rub'))} -> "
            f"exit {format_money(item.get('solo_exit_price_rub'))} | "
            f"{solo_text} | fl {format_float(item.get('wear_rating'))}"
        )
    lines.extend(["", f"Dashboard: {dashboard_url}/packs/{pack.get('pack_id')}"])
    return "\n".join(lines).strip()


def should_send_immediate(candidate: dict) -> bool:
    return (
        candidate.get("recommendation_level") == "critical"
        and not candidate.get("manual_review_required")
        and (_to_float(candidate.get("estimated_profit_rub")) or 0) >= 300
        and (_to_float(candidate.get("estimated_roi_percent")) or 0) >= 20
    )


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _reason_lines(candidate: dict) -> list[str]:
    text = str(candidate.get("anomaly_reasons") or candidate.get("recommendation_reason") or "").strip()
    if not text:
        return []
    return [f"- {part.strip()}" for part in text.split(";") if part.strip()]
