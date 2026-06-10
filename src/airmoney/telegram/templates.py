from __future__ import annotations


def format_money(value) -> str:
    try:
        return f"{float(value):,.0f}".replace(",", " ")
    except Exception:
        return str(value or "-")


def format_percent(value) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return str(value or "-")


def candidate_alert(candidate: dict, details_url: str) -> str:
    float_value = candidate.get("float_value")
    if float_value is None or float_value == "":
        float_text = "-"
    else:
        float_text = f"{float(float_value):.6f}"

    pattern = candidate.get("pattern")
    pattern_text = "-" if pattern in {None, ""} else str(pattern)

    return "\n".join(
        [
            "Найден кандидат",
            "",
            f"Скин: {candidate.get('skin_name') or candidate.get('display_name') or '-'}",
            f"Покупка: {format_money(candidate.get('buy_price_rub'))} руб.",
            f"Ожид. продажа: {format_money(candidate.get('estimated_resale_price_rub'))} руб.",
            f"Профит: +{format_money(candidate.get('estimated_profit_rub'))} руб.",
            f"ROI: {format_percent(candidate.get('estimated_roi_percent'))}%",
            "",
            f"Float: {float_text}",
            f"Pattern: {pattern_text}",
            "",
            f"Курс: {candidate.get('currency_rate_source') or candidate.get('currency_source') or '-'}",
            f"Обновлён: {candidate.get('currency_fetched_at') or '-'}",
            "",
            "Подробнее:",
            details_url,
        ]
    )
