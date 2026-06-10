from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from airmoney.config.models import ParserSettings
from airmoney.currency.cache import is_cache_fresh, load_cached_rates, save_cached_rates
from airmoney.currency.provider import CurrencyRates


class SteamCurrencyProvider:
    url = "https://steam-currency.ru/"
    widget_url = "https://steam-currency.ru/widget?pair={pair}&theme=light"

    def fetch(self) -> CurrencyRates:
        usd = self._fetch_pair("USD:RUB")
        eur = self._fetch_pair("EUR:RUB")
        source = "steam-currency.ru/widget"
        return CurrencyRates(
            usd_to_rub=usd,
            eur_to_rub=eur,
            source=source,
            fetched_at=datetime.now(timezone.utc),
            is_fallback=False,
        )

    def _fetch_pair(self, pair: str) -> float:
        url = self.widget_url.format(pair=urllib.request.quote(pair, safe=""))
        body = _read_url(url)
        rate = _extract_widget_rate(body, pair)
        if rate is None:
            rate = _extract_currency_rate(body, [pair, pair.replace(":", " → "), pair.split(":")[0]])
        if rate is None:
            raise ValueError(f"Не удалось найти курс {pair} на steam-currency.ru")
        return rate


def _read_url(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; Airmoney/0.1; +local)",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def _extract_widget_rate(text: str, pair: str) -> float | None:
    if pair not in text:
        return None
    match = re.search(
        r'<div\s+class="rate">\s*([0-9]{1,3}(?:[.,][0-9]{1,6})?)\s*</div>',
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = _to_float(match.group(1))
    if 20 <= value <= 300:
        return value
    return None


def _extract_currency_rate(text: str, markers: list[str]) -> float | None:
    normalized = re.sub(r"\s+", " ", text.replace("\xa0", " "))
    number = r"([0-9]{1,3}(?:[.,][0-9]{1,4})?)"

    for marker in markers:
        escaped = re.escape(marker)
        patterns = [
            rf"{escaped}[^0-9]{{0,80}}{number}",
            rf"{number}[^0-9]{{0,40}}{escaped}",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
                value = _to_float(match.group(1))
                if 20 <= value <= 300:
                    return value
    return None


def _to_float(value: str) -> float:
    return float(value.replace(",", "."))


def fallback_rates(settings: ParserSettings, source: str = "settings_fallback") -> CurrencyRates:
    return CurrencyRates(
        usd_to_rub=settings.fallback_usd_to_rub,
        eur_to_rub=settings.fallback_eur_to_rub,
        source=source,
        fetched_at=datetime.now(timezone.utc),
        is_fallback=True,
    )


class CurrencyService:
    def __init__(
        self,
        settings: ParserSettings,
        provider: SteamCurrencyProvider | None = None,
        cache_path: str | None = None,
    ):
        self.settings = settings
        self.provider = provider or SteamCurrencyProvider()
        self.cache_path = cache_path

    def get_rates(self, force_refresh: bool = False) -> CurrencyRates:
        cached = load_cached_rates(self.cache_path)
        if cached and not force_refresh and is_cache_fresh(cached, self.settings.currency_cache_ttl_seconds):
            return cached

        if self.settings.currency_provider == "fallback_only":
            rates = fallback_rates(self.settings, source="settings_fallback:forced")
            save_cached_rates(rates, self.cache_path)
            return rates

        try:
            rates = self.provider.fetch()
            save_cached_rates(rates, self.cache_path)
            return rates
        except Exception:
            if cached:
                cached.source = f"{cached.source}:stale_cache"
                return cached
            rates = fallback_rates(self.settings)
            save_cached_rates(rates, self.cache_path)
            return rates
