from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass
class CurrencyRates:
    usd_to_rub: float
    eur_to_rub: float
    source: str
    fetched_at: datetime
    is_fallback: bool = False

    @property
    def fetched_at_iso(self) -> str:
        return self.fetched_at.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class CurrencyProvider(Protocol):
    def fetch(self) -> CurrencyRates:
        ...
