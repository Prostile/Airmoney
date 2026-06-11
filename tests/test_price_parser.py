from datetime import datetime, timezone

from airmoney.currency.provider import CurrencyRates
from airmoney.steam.extractor import (
    parse_money_number,
    parse_name_from_card_text,
    parse_pattern,
    parse_price_values,
    parse_wear,
)


def rates():
    return CurrencyRates(
        usd_to_rub=72.0,
        eur_to_rub=86.0,
        source="test",
        fetched_at=datetime.now(timezone.utc),
    )


def test_parse_money_number_handles_spaces_and_commas():
    assert parse_money_number("4 400,50") == 4400.50
    assert parse_money_number("1,234.56") == 1234.56
    assert parse_money_number("1.234,56") == 1234.56


def test_parse_price_values_converts_currencies_to_rub():
    assert parse_price_values("Купить\n4 400 ₽", rates()).buy_price_rub == 4400
    assert parse_price_values("Buy\nRUB 472.85", rates()).buy_price_rub == 472.85
    assert parse_price_values("Buy\n472.85 RUB", rates()).buy_price_rub == 472.85
    assert parse_price_values("Buy\n$10.50", rates()).buy_price_rub == 756
    assert parse_price_values("Buy\n10 EUR", rates()).buy_price_rub == 860


def test_parse_name_from_card_text_combines_following_exterior_line():
    text = "\n".join(
        [
            "Souvenir UMP-45 | Mechanism",
            "(Factory New)",
            "Float Value: 0.0115",
            "Pattern Template: 321",
            "RUB 472.85",
        ]
    )

    assert parse_name_from_card_text(text) == "Souvenir UMP-45 | Mechanism (Factory New)"


def test_parse_pattern_and_wear():
    text = "Pattern Template: 321\nFloat Value: 0.0142"
    assert parse_pattern(text) == 321
    assert parse_wear(text) == 0.0142
