from datetime import datetime, timezone

from airmoney.currency.provider import CurrencyRates
from airmoney.steam.parser import ItemScanTarget, parse_card


def test_parse_card_stores_currency_fetch_time():
    fetched_at = datetime(2026, 6, 11, 1, 2, 3, tzinfo=timezone.utc)
    rates = CurrencyRates(
        usd_to_rub=70,
        eur_to_rub=80,
        source="test-rates",
        fetched_at=fetched_at,
    )
    listing = parse_card(
        {"text": "AK-47 | Test\nFloat Value: 0.01\nPattern: 7\n$10", "href": "https://example.test/listing"},
        ItemScanTarget(
            id="item1",
            market_hash_name="AK-47 | Test (Factory New)",
            display_name="AK-47 | Test",
            steam_market_url="https://example.test/search",
        ),
        rates,
    )
    assert listing is not None
    assert listing.currency_source == "test-rates"
    assert listing.currency_fetched_at == "2026-06-11T01:02:03+00:00"
