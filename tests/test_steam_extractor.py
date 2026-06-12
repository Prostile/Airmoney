from datetime import datetime, timezone

from airmoney.currency.provider import CurrencyRates
from airmoney.steam.extractor import listing_identity, parse_name_from_card_text, parse_price_values


def rates():
    return CurrencyRates(
        usd_to_rub=72.0,
        eur_to_rub=86.0,
        source="test",
        fetched_at=datetime.now(timezone.utc),
    )


def test_parse_price_rub_prefix():
    assert parse_price_values("Buy\nRUB 472.85", rates()).buy_price_rub == 472.85


def test_parse_price_rub_suffix():
    assert parse_price_values("Buy\n472.85 RUB", rates()).buy_price_rub == 472.85
    assert parse_price_values("Buy\n\u20bd472.85", rates()).buy_price_rub == 472.85
    assert parse_price_values("Buy\n472,85 \u20bd", rates()).buy_price_rub == 472.85
    assert parse_price_values("Buy\n\u20ac2.50", rates()).buy_price_rub == 215
    assert parse_price_values("Buy\n2,50 \u20ac", rates()).buy_price_rub == 215
    assert parse_price_values("Купить\n472,85 руб", rates()).buy_price_rub == 472.85


def test_parse_name_with_following_exterior():
    text = "Souvenir UMP-45 | Mechanism\n(Factory New)\nRUB 472.85"

    assert parse_name_from_card_text(text) == "Souvenir UMP-45 | Mechanism (Factory New)"


def test_parse_name_with_following_russian_exterior():
    text = "Souvenir UMP-45 | Mechanism\n(Прямо с завода)\nRUB 472.85"

    assert parse_name_from_card_text(text) == "Souvenir UMP-45 | Mechanism (Factory New)"


def test_parse_name_without_exterior():
    assert parse_name_from_card_text("Souvenir UMP-45 | Mechanism\nRUB 472.85") == "Souvenir UMP-45 | Mechanism"


def test_parse_name_ignores_price_lines():
    assert parse_name_from_card_text("RUB 472.85\nBuy\nSouvenir UMP-45 | Mechanism") == "Souvenir UMP-45 | Mechanism"


def test_listing_identity_differs_by_price_and_raw_text():
    one = listing_identity("item", "name", "url", 7, 0.0115, 472.85, "raw-a")
    two = listing_identity("item", "name", "url", 7, 0.0115, 690.0, "raw-b")

    assert one != two
