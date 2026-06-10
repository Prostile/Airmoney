from datetime import datetime, timezone

from airmoney.config.models import ParserSettings
from airmoney.currency.cache import is_cache_fresh, load_cached_rates, save_cached_rates
from airmoney.currency.provider import CurrencyRates
from airmoney.currency import steam_currency
from airmoney.currency.steam_currency import CurrencyService, SteamCurrencyProvider


class BrokenProvider:
    def fetch(self):
        raise RuntimeError("offline")


def test_currency_cache_roundtrip(tmp_path):
    path = tmp_path / "currency.json"
    rates = CurrencyRates(usd_to_rub=70, eur_to_rub=80, source="test", fetched_at=datetime.now(timezone.utc))
    save_cached_rates(rates, path)
    loaded = load_cached_rates(path)
    assert loaded.usd_to_rub == 70
    assert loaded.eur_to_rub == 80
    assert is_cache_fresh(loaded, 60)


def test_currency_service_uses_fallback_without_cache(tmp_path):
    settings = ParserSettings(fallback_usd_to_rub=72, fallback_eur_to_rub=86)
    service = CurrencyService(settings, provider=BrokenProvider(), cache_path=str(tmp_path / "missing.json"))
    rates = service.get_rates(force_refresh=True)
    assert rates.usd_to_rub == 72
    assert rates.eur_to_rub == 86
    assert rates.is_fallback is True


def test_steam_currency_provider_parses_widget_rates(monkeypatch):
    def fake_read_url(url):
        if "USD%3ARUB" in url:
            return '<div class="pair">Курс Steam USD → RUB</div><div class="rate">72.01</div>', False
        if "EUR%3ARUB" in url:
            return '<div class="pair">Курс Steam EUR → RUB</div><div class="rate">89.68</div>', True
        raise AssertionError(url)

    monkeypatch.setattr(steam_currency, "_read_url", fake_read_url)
    rates = SteamCurrencyProvider().fetch()
    assert rates.usd_to_rub == 72.01
    assert rates.eur_to_rub == 89.68
    assert rates.source == "steam-currency.ru/widget:tls_unverified"
