from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airmoney.currency.provider import CurrencyRates
from airmoney.paths import CURRENCY_CACHE_PATH


def load_cached_rates(path: str | Path | None = None) -> CurrencyRates | None:
    cache_path = Path(path or CURRENCY_CACHE_PATH)
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(str(data["fetched_at"]))
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return CurrencyRates(
            usd_to_rub=float(data["usd_to_rub"]),
            eur_to_rub=float(data["eur_to_rub"]),
            source=str(data.get("source", "cache")),
            fetched_at=fetched_at,
            is_fallback=bool(data.get("is_fallback", False)),
        )
    except Exception:
        return None


def save_cached_rates(rates: CurrencyRates, path: str | Path | None = None) -> None:
    cache_path = Path(path or CURRENCY_CACHE_PATH)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "usd_to_rub": rates.usd_to_rub,
        "eur_to_rub": rates.eur_to_rub,
        "source": rates.source,
        "fetched_at": rates.fetched_at_iso,
        "is_fallback": rates.is_fallback,
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def is_cache_fresh(rates: CurrencyRates, ttl_seconds: int) -> bool:
    fetched_at = rates.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at <= timedelta(seconds=ttl_seconds)
