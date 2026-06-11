from __future__ import annotations

from dataclasses import dataclass

from airmoney.anomaly.baselines import assign_float_bucket
from airmoney.anomaly.models import ParsedListing
from airmoney.config.models import FloatBucketConfig


@dataclass
class MarketSnapshot:
    item_id: str
    float_bucket: str
    sample_size: int
    floor_price_rub: float | None
    q10_price_rub: float | None
    q25_price_rub: float | None
    median_price_rub: float | None
    q75_price_rub: float | None


def build_market_snapshots(
    item_id: str,
    listings: list[ParsedListing],
    buckets: list[FloatBucketConfig],
) -> list[MarketSnapshot]:
    grouped: dict[str, list[float]] = {}
    for listing in listings:
        if listing.price_rub <= 0:
            continue
        bucket = assign_float_bucket(listing.wear_rating, buckets)
        grouped.setdefault(bucket, []).append(listing.price_rub)

    snapshots = []
    for bucket, prices in sorted(grouped.items()):
        values = sorted(prices)
        snapshots.append(
            MarketSnapshot(
                item_id=item_id,
                float_bucket=bucket,
                sample_size=len(values),
                floor_price_rub=_quantile(values, 0.0),
                q10_price_rub=_quantile(values, 0.10),
                q25_price_rub=_quantile(values, 0.25),
                median_price_rub=_quantile(values, 0.50),
                q75_price_rub=_quantile(values, 0.75),
            )
        )
    return snapshots


def ewma(old_value: float | None, current_value: float | None, alpha: float) -> float | None:
    if current_value is None:
        return old_value
    if old_value is None:
        return current_value
    return round(alpha * current_value + (1 - alpha) * old_value, 2)


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    position = (len(values) - 1) * q
    left = int(position)
    right = min(left + 1, len(values) - 1)
    fraction = position - left
    value = values[left] * (1 - fraction) + values[right] * fraction
    return round(value, 2)
