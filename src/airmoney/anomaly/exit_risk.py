from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from airmoney.anomaly.models import ParsedListing
from airmoney.config.models import CapitalSettings, MarketRiskSettings, PackDetectionSettings


LEVEL_ORDER = {"skip": 0, "watch": 1, "good": 2, "critical": 3}
ORDER_LEVEL = {value: key for key, value in LEVEL_ORDER.items()}


@dataclass
class SubstituteContext:
    floor_rub: float | None = None
    median_rub: float | None = None
    sample_size: int = 0
    cap_rub: float | None = None


@dataclass
class PackCandidate:
    item_id: str
    pack_id: str
    listings: list[ParsedListing]
    pack_size: int
    pack_cost_rub: float
    min_buy_price_rub: float
    max_buy_price_rub: float
    min_float: float | None
    max_float: float | None
    next_floor_after_pack_rub: float
    gap_percent: float
    estimated_net_profit_rub: float
    estimated_roi_percent: float
    requires_sweep: bool
    confidence: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class ExitPriceResult:
    anomaly_baseline_price_rub: float | None
    conservative_exit_price_rub: float | None
    solo_exit_price_rub: float | None
    sweep_exit_price_rub: float | None
    exit_price_model: str
    exit_confidence: str
    nearest_competitor_price_rub: float | None
    q25_price_rub: float | None
    median_price_rub: float | None
    requires_sweep: bool
    capital_required_rub: float | None
    substitute_floor_rub: float | None
    substitute_cap_rub: float | None
    pack_id: str = ""
    pack_size: int = 0
    pack_cost_rub: float | None = None
    pack_floor_after_rub: float | None = None
    manual_review_required: bool = False
    liquidity_score: float | None = None
    reasons: list[str] = field(default_factory=list)


def detect_price_packs(
    sorted_listings: list[ParsedListing],
    settings: PackDetectionSettings,
    fee_percent: float = 15.0,
) -> list[PackCandidate]:
    if not settings.enabled:
        return []
    listings = [listing for listing in sorted_listings if listing.price_rub > 0]
    packs: list[PackCandidate] = []
    for index in range(len(listings) - 1):
        left = listings[index].price_rub
        right = listings[index + 1].price_rub
        if left <= 0:
            continue
        gap_percent = round((right / left - 1) * 100, 2)
        pack = listings[: index + 1]
        if gap_percent < settings.min_gap_percent:
            continue
        if not settings.min_pack_size <= len(pack) <= settings.max_pack_size:
            continue
        pack_cost = round(sum(listing.price_rub for listing in pack), 2)
        fee_multiplier = max(0.0, 1 - fee_percent / 100)
        net_resale = right * len(pack) * fee_multiplier
        profit = round(net_resale - pack_cost, 2)
        roi = round(profit / pack_cost * 100, 2) if pack_cost > 0 else 0.0
        floats = [listing.wear_rating for listing in pack if listing.wear_rating is not None]
        pack_id = f"pack_{pack[0].item_id}_{index + 1}_{round(right)}"
        packs.append(
            PackCandidate(
                item_id=pack[0].item_id,
                pack_id=pack_id,
                listings=pack,
                pack_size=len(pack),
                pack_cost_rub=pack_cost,
                min_buy_price_rub=pack[0].price_rub,
                max_buy_price_rub=pack[-1].price_rub,
                min_float=min(floats) if floats else None,
                max_float=max(floats) if floats else None,
                next_floor_after_pack_rub=right,
                gap_percent=gap_percent,
                estimated_net_profit_rub=profit,
                estimated_roi_percent=roi,
                requires_sweep=True,
                confidence="medium",
                reasons=[f"price pack before {gap_percent:.1f}% gap"],
            )
        )
    return packs


def estimate_exit_prices(
    candidate: ParsedListing,
    sorted_listings: list[ParsedListing],
    anomaly_baseline_price_rub: float | None,
    settings: MarketRiskSettings,
    pack_settings: PackDetectionSettings,
    capital_settings: CapitalSettings,
    fee_percent: float,
    target_resale_price_cap: float | None = None,
    substitute_context: SubstituteContext | None = None,
) -> ExitPriceResult:
    values = [listing for listing in sorted_listings if listing.price_rub > 0]
    prices = [listing.price_rub for listing in values]
    candidate_index = _candidate_index(candidate, values)
    nearest = _nearest_competitor_price(candidate_index, values)
    q25 = _q25(prices)
    sample_median = median(prices) if prices else None
    packs = detect_price_packs(values, pack_settings, fee_percent=fee_percent)
    pack = _pack_for_candidate(candidate, packs)
    substitute_floor = substitute_context.floor_rub if substitute_context else None
    substitute_cap = substitute_context.cap_rub if substitute_context else None
    solo_exit = nearest
    if candidate_index is not None and candidate_index > 0 and values:
        solo_exit = min(values[0].price_rub, nearest) if nearest is not None else values[0].price_rub
    candidates = [solo_exit, q25, sample_median, target_resale_price_cap, substitute_cap]
    conservative = min(value for value in candidates if value is not None and value > 0) if any(
        value is not None and value > 0 for value in candidates
    ) else None
    model = "conservative" if settings.conservative_exit_enabled else "baseline"
    if not settings.conservative_exit_enabled:
        conservative = target_resale_price_cap or anomaly_baseline_price_rub
    reasons: list[str] = []
    if solo_exit is not None:
        reasons.append(f"solo exit {solo_exit:.2f}")
    if substitute_cap is not None:
        reasons.append(f"substitute cap {substitute_cap:.2f}")
    requires_sweep = pack is not None and candidate_index is not None and candidate_index > 0
    capital_required = candidate.price_rub
    pack_id = ""
    pack_size = 0
    pack_cost = None
    pack_floor_after = None
    sweep_exit = None
    manual_review = False
    if pack is not None:
        pack_id = pack.pack_id
        pack_size = pack.pack_size
        pack_cost = pack.pack_cost_rub
        pack_floor_after = pack.next_floor_after_pack_rub
        sweep_exit = pack.next_floor_after_pack_rub
        if requires_sweep:
            capital_required = pack.pack_cost_rub
            reasons.append("requires sweep to reach next floor")
        else:
            reasons.append("price pack detected; solo exit uses nearest competitor")
    if capital_settings.enabled:
        if candidate.price_rub > capital_settings.max_single_buy_rub:
            manual_review = True
            reasons.append("single buy exceeds capital limit")
        if pack is not None and pack.pack_cost_rub > capital_settings.max_bundle_cost_rub:
            manual_review = True
            reasons.append("pack cost exceeds capital limit")
        if pack is not None and pack.pack_size > capital_settings.max_units_per_item:
            manual_review = True
            reasons.append("pack size exceeds unit limit")
    liquidity_score = _liquidity_score(len(values), pack_size, pack_settings.max_pack_to_sample_ratio)
    return ExitPriceResult(
        anomaly_baseline_price_rub=anomaly_baseline_price_rub,
        conservative_exit_price_rub=round(conservative, 2) if conservative is not None else None,
        solo_exit_price_rub=round(solo_exit, 2) if solo_exit is not None else None,
        sweep_exit_price_rub=round(sweep_exit, 2) if sweep_exit is not None else None,
        exit_price_model=model,
        exit_confidence="medium",
        nearest_competitor_price_rub=round(nearest, 2) if nearest is not None else None,
        q25_price_rub=round(q25, 2) if q25 is not None else None,
        median_price_rub=round(sample_median, 2) if sample_median is not None else None,
        requires_sweep=requires_sweep,
        capital_required_rub=round(capital_required, 2) if capital_required is not None else None,
        substitute_floor_rub=substitute_floor,
        substitute_cap_rub=substitute_cap,
        pack_id=pack_id,
        pack_size=pack_size,
        pack_cost_rub=pack_cost,
        pack_floor_after_rub=pack_floor_after,
        manual_review_required=manual_review,
        liquidity_score=liquidity_score,
        reasons=reasons,
    )


def calculate_market_confidence(
    sample_size: int,
    neighbor_count: int,
    pack_to_sample_ratio: float | None,
    settings: MarketRiskSettings,
) -> str:
    if not settings.enabled:
        return "high"
    if sample_size < settings.min_sample_for_good:
        return "very_low"
    if sample_size < settings.min_sample_for_critical:
        return "low"
    if neighbor_count < settings.min_neighbor_for_good:
        return "low"
    if neighbor_count < settings.min_neighbor_for_critical:
        return "medium"
    if pack_to_sample_ratio is not None and pack_to_sample_ratio > 0.5:
        return "low"
    return "high"


def apply_market_risk_caps(
    alert_level: str,
    confidence: str,
    requires_sweep: bool,
    settings: MarketRiskSettings,
) -> str:
    if not settings.enabled:
        return alert_level
    if confidence == "very_low":
        return min_level(alert_level, settings.very_thin_market_max_level)
    if confidence == "low":
        return min_level(alert_level, settings.thin_market_max_level)
    if requires_sweep and settings.downgrade_if_requires_sweep:
        return min_level(alert_level, settings.sweep_max_level_without_capital)
    return alert_level


def apply_capital_caps(
    alert_level: str,
    exit_result: ExitPriceResult,
    settings: CapitalSettings,
) -> str:
    if not settings.enabled or not exit_result.manual_review_required:
        return alert_level
    return min_level(alert_level, "watch")


def min_level(left: str, right: str) -> str:
    value = min(LEVEL_ORDER.get(left, 0), LEVEL_ORDER.get(right, 0))
    return ORDER_LEVEL.get(value, "skip")


def _candidate_index(candidate: ParsedListing, listings: list[ParsedListing]) -> int | None:
    for index, listing in enumerate(listings):
        if listing is candidate:
            return index
        if candidate.listing_id and listing.listing_id == candidate.listing_id:
            return index
    return None


def _nearest_competitor_price(index: int | None, listings: list[ParsedListing]) -> float | None:
    if not listings:
        return None
    if index is None:
        return listings[0].price_rub
    if index + 1 < len(listings):
        return listings[index + 1].price_rub
    if index > 0:
        return listings[index - 1].price_rub
    return None


def _q25(prices: list[float]) -> float | None:
    values = sorted(price for price in prices if price > 0)
    if not values:
        return None
    index = int((len(values) - 1) * 0.25)
    return values[index]


def _pack_for_candidate(candidate: ParsedListing, packs: list[PackCandidate]) -> PackCandidate | None:
    for pack in packs:
        for listing in pack.listings:
            if listing is candidate:
                return pack
            if candidate.listing_id and listing.listing_id == candidate.listing_id:
                return pack
    return None


def _liquidity_score(sample_size: int, pack_size: int, max_pack_ratio: float) -> float:
    if sample_size <= 0:
        return 0.0
    pack_ratio = pack_size / sample_size if pack_size else 0.0
    score = min(100.0, sample_size / 20 * 100)
    if pack_ratio > max_pack_ratio:
        score *= 0.6
    return round(score, 2)
