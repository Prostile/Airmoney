from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass

from airmoney.currency.provider import CurrencyRates


PRICE_RUB_RE = re.compile(
    r"(?:\bRUB\s*([0-9][0-9,.\u00a0 ]*)|([0-9][0-9,.\u00a0 ]*)\s*(?:₽|руб\.?|RUB\b))",
    re.IGNORECASE,
)
PRICE_USD_RE = re.compile(
    r"(?:\$\s*([0-9][0-9,.\u00a0 ]*)|([0-9][0-9,.\u00a0 ]*)\s*USD)",
    re.IGNORECASE,
)
PRICE_EUR_RE = re.compile(
    r"(?:€\s*([0-9][0-9,.\u00a0 ]*)|([0-9][0-9,.\u00a0 ]*)\s*(?:€|EUR))",
    re.IGNORECASE,
)
PATTERN_RE = re.compile(
    r"(?:Шаблон\s+раскраски|Шаблон\s+брелка|Paint\s+Seed|Pattern\s+Template|Pattern|Template)"
    r"\s*[:#]?\s*([0-9][0-9 \u00a0]*)",
    re.IGNORECASE,
)
WEAR_RE = re.compile(
    r"(?:Степень\s+износа|Float\s+Value|Wear\s+Rating|Float)\s*[:#]?\s*([0-9]+(?:[,.][0-9]+)?)",
    re.IGNORECASE,
)
EXTERIOR_LINE_RE = re.compile(r"^\(([^()]+)\)$", re.IGNORECASE)
EXTERIOR_IN_NAME_RE = re.compile(r"\(([^()]+)\)", re.IGNORECASE)
EXTERIOR_CANONICAL = {
    "factory new": "Factory New",
    "minimal wear": "Minimal Wear",
    "field-tested": "Field-Tested",
    "well-worn": "Well-Worn",
    "battle-scarred": "Battle-Scarred",
    "прямо с завода": "Factory New",
    "немного поношенное": "Minimal Wear",
    "после полевых испытаний": "Field-Tested",
    "поношенное": "Well-Worn",
    "закаленное в боях": "Battle-Scarred",
    "закалённое в боях": "Battle-Scarred",
}


@dataclass
class ParsedPrice:
    buy_price_rub: float
    buy_price_original: float
    currency_original: str
    currency_rate: float
    currency_source: str


def parse_money_number(value: str) -> float:
    value = str(value).replace("\xa0", " ").strip()
    if "\n" in value:
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if lines:
            value = lines[-1]
    value = re.sub(r"[^0-9,.\s]", "", value)
    value = value.replace(" ", "").strip()
    if not value:
        raise ValueError("Пустое значение цены после очистки.")

    if "," in value and "." in value:
        last_comma = value.rfind(",")
        last_dot = value.rfind(".")
        if last_comma > last_dot:
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
        return float(value)
    if "," in value:
        return float(value.replace(",", "."))
    return float(value)


def to_float(value: str) -> float:
    match = re.search(r"[0-9]+(?:[,.][0-9]+)?", str(value).replace("\xa0", " "))
    if not match:
        raise ValueError(f"Не найдено число float в значении: {value!r}")
    return float(match.group(0).replace(",", "."))


def to_int(value: str) -> int:
    digits = re.sub(r"[^\d]", "", str(value))
    if not digits:
        raise ValueError(f"Не найдено цифр в значении: {value!r}")
    return int(digits)


def parse_price_values(text: str, rates: CurrencyRates) -> ParsedPrice | None:
    text = text.replace("\xa0", " ")
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in reversed(lines):
        upper_line = line.upper()
        if not any(marker in line for marker in ["₽", "руб", "$", "€"]) and not any(
            marker in upper_line for marker in ["RUB", "USD", "EUR"]
        ):
            continue
        rub_match = PRICE_RUB_RE.search(line)
        if rub_match:
            price = parse_money_number(rub_match.group(1) or rub_match.group(2))
            return ParsedPrice(
                buy_price_rub=round(price, 2),
                buy_price_original=round(price, 2),
                currency_original="RUB",
                currency_rate=1.0,
                currency_source=rates.source,
            )

        usd_match = PRICE_USD_RE.search(line)
        if usd_match:
            raw = usd_match.group(1) or usd_match.group(2)
            price = parse_money_number(raw)
            return ParsedPrice(
                buy_price_rub=round(price * rates.usd_to_rub, 2),
                buy_price_original=round(price, 4),
                currency_original="USD",
                currency_rate=rates.usd_to_rub,
                currency_source=rates.source,
            )

        eur_match = PRICE_EUR_RE.search(line)
        if eur_match:
            raw = eur_match.group(1) or eur_match.group(2)
            price = parse_money_number(raw)
            return ParsedPrice(
                buy_price_rub=round(price * rates.eur_to_rub, 2),
                buy_price_original=round(price, 4),
                currency_original="EUR",
                currency_rate=rates.eur_to_rub,
                currency_source=rates.source,
            )
    return None


def parse_pattern(text: str) -> int | None:
    match = PATTERN_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    try:
        return to_int(match.group(1))
    except ValueError:
        return None


def parse_wear(text: str) -> float | None:
    match = WEAR_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    try:
        return to_float(match.group(1))
    except ValueError:
        return None


def parse_ranges_int(ranges_text: str) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    if not str(ranges_text or "").strip():
        return ranges
    parts = str(ranges_text).replace("–", "-").replace("—", "-").split(";")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            number = to_int(part)
            ranges.append((number, number, str(number)))
            continue
        left, right = part.split("-", 1)
        min_value = to_int(left)
        max_value = to_int(right)
        if min_value > max_value:
            min_value, max_value = max_value, min_value
        ranges.append((min_value, max_value, f"{min_value}-{max_value}"))
    return ranges


def value_in_ranges(value: int | None, ranges_text: str) -> bool:
    ranges = parse_ranges_int(ranges_text)
    if not ranges:
        return True
    if value is None:
        return False
    return any(left <= value <= right for left, right, _ in ranges)


def parse_name_from_card_text(text: str) -> str:
    lines = [line.strip() for line in text.replace("\xa0", " ").splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if "|" in line and not is_bad_name_line(line):
            return _append_following_exterior(line, lines[index + 1 :])
    for line in lines:
        if not is_bad_name_line(line):
            return line
    return ""


def _append_following_exterior(name: str, following_lines: list[str]) -> str:
    if _name_has_exterior(name):
        return name
    for line in following_lines[:4]:
        if is_bad_name_line(line):
            continue
        match = EXTERIOR_LINE_RE.match(line)
        if match:
            exterior = _canonical_exterior(match.group(1))
            if exterior:
                return f"{name} ({exterior})"
    return name


def _name_has_exterior(name: str) -> bool:
    return any(_canonical_exterior(match.group(1)) for match in EXTERIOR_IN_NAME_RE.finditer(name))


def _canonical_exterior(value: str) -> str:
    return EXTERIOR_CANONICAL.get(_normalize_exterior_key(value), "")


def _normalize_exterior_key(value: str) -> str:
    return str(value or "").strip().lower().replace("ё", "е")


def is_bad_name_line(line: str) -> bool:
    lowered = line.lower()
    bad_markers = [
        "торговая площадка",
        "community market",
        "главная страница",
        "counter-strike",
        "шаблон",
        "степень износа",
        "pattern",
        "template",
        "paint seed",
        "float",
        "wear",
        "купить",
        "buy",
        "₽",
        "руб",
        "rub",
        "$",
        "usd",
        "€",
        "eur",
        "заявок",
        "заказать",
        "сортировать",
        "отфильтровать",
    ]
    return any(marker in lowered for marker in bad_markers)


def market_search_url(market_hash_name: str) -> str:
    query = urllib.parse.quote(market_hash_name)
    return f"https://steamcommunity.com/market/search?q={query}"


def listing_identity(*parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return "listing_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
