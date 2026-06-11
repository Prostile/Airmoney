from __future__ import annotations

from statistics import median

from airmoney.anomaly.models import BaselineResult, ParsedListing
from airmoney.config.models import FloatBucketConfig


def assign_float_bucket(wear: float | None, buckets: list[FloatBucketConfig]) -> str:
    if wear is None:
        return "unknown"
    for bucket in buckets:
        if bucket.min <= wear <= bucket.max:
            return bucket.id
    return "other"


def calculate_local_baseline(
    candidate: ParsedListing,
    listings: list[ParsedListing],
    min_samples: int = 5,
    exclude_candidate: bool = True,
) -> BaselineResult:
    prices = [
        listing.price_rub
        for listing in listings
        if listing.price_rub > 0 and (not exclude_candidate or listing is not candidate)
    ]
    if len(prices) < min_samples:
        return BaselineResult(sample_size=len(prices))

    med = median(prices)
    deviations = [abs(price - med) for price in prices]
    mad = median(deviations)
    discount = (1 - candidate.price_rub / med) * 100 if med > 0 else None
    robust_z = None
    if mad > 0:
        robust_z = (med - candidate.price_rub) / (1.4826 * mad)
    return BaselineResult(
        median=round(float(med), 2),
        discount_percent=round(float(discount), 2) if discount is not None else None,
        robust_z=round(float(robust_z), 4) if robust_z is not None else None,
        sample_size=len(prices),
    )


def calculate_bucket_baseline(
    candidate: ParsedListing,
    listings: list[ParsedListing],
    buckets: list[FloatBucketConfig],
    min_samples: int = 3,
    exclude_candidate: bool = True,
) -> BaselineResult:
    bucket = assign_float_bucket(candidate.wear_rating, buckets)
    if candidate.wear_rating is None:
        return BaselineResult(bucket=bucket)

    prices = []
    for listing in listings:
        if exclude_candidate and listing is candidate:
            continue
        if listing.wear_rating is None:
            continue
        if assign_float_bucket(listing.wear_rating, buckets) == bucket:
            prices.append(listing.price_rub)
    if len(prices) < min_samples:
        return BaselineResult(bucket=bucket, sample_size=len(prices))

    med = median(prices)
    discount = (1 - candidate.price_rub / med) * 100 if med > 0 else None
    return BaselineResult(
        median=round(float(med), 2),
        discount_percent=round(float(discount), 2) if discount is not None else None,
        sample_size=len(prices),
        bucket=bucket,
    )


def get_float_neighbors(
    candidate: ParsedListing,
    listings: list[ParsedListing],
    k: int = 7,
    max_float_distance: float = 0.025,
) -> list[ParsedListing]:
    if candidate.wear_rating is None:
        return []
    neighbors: list[tuple[float, ParsedListing]] = []
    for listing in listings:
        if listing is candidate or listing.wear_rating is None:
            continue
        distance = abs(candidate.wear_rating - listing.wear_rating)
        if distance <= max_float_distance:
            neighbors.append((distance, listing))
    neighbors.sort(key=lambda row: row[0])
    return [listing for _, listing in neighbors[:k]]


def calculate_float_peer_baseline(
    candidate: ParsedListing,
    listings: list[ParsedListing],
    k: int = 7,
    min_neighbors: int = 5,
    max_float_distance: float = 0.025,
) -> BaselineResult:
    neighbors = get_float_neighbors(
        candidate,
        listings,
        k=k,
        max_float_distance=max_float_distance,
    )
    if len(neighbors) < min_neighbors:
        return BaselineResult(neighbor_count=len(neighbors), sample_size=len(neighbors))

    prices = [listing.price_rub for listing in neighbors if listing.price_rub > 0]
    if len(prices) < min_neighbors:
        return BaselineResult(neighbor_count=len(neighbors), sample_size=len(prices))
    med = median(prices)
    discount = (1 - candidate.price_rub / med) * 100 if med > 0 else None
    return BaselineResult(
        median=round(float(med), 2),
        discount_percent=round(float(discount), 2) if discount is not None else None,
        sample_size=len(prices),
        neighbor_count=len(neighbors),
    )
