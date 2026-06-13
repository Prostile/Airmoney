from __future__ import annotations

from airmoney.anomaly.models import AnomalyResult, parsed_at_iso
from airmoney.config.models import Candidate


def candidate_from_anomaly_result(
    result: AnomalyResult,
    listing_id: str,
    rule_id: str | None,
    market_fee_percent: float,
) -> Candidate:
    resale = result.exit_price_rub or result.fair_price_rub or result.listing.price_rub
    net_resale = result.net_resale_rub if result.net_resale_rub is not None else 0.0
    profit = result.net_profit_rub if result.net_profit_rub is not None else 0.0
    roi = result.roi_percent if result.roi_percent is not None else 0.0
    reasons_text = "; ".join(result.reasons)
    return Candidate(
        id=f"cand_{listing_id.replace('listing_', '')}",
        listing_id=listing_id,
        rule_id=rule_id,
        buy_price_rub=round(result.listing.price_rub, 2),
        estimated_resale_price_rub=round(resale, 2),
        estimated_net_resale_rub=round(net_resale, 2),
        estimated_profit_rub=round(profit, 2),
        estimated_roi_percent=round(roi, 2),
        market_fee_percent=round(float(market_fee_percent), 2),
        recommendation_level=result.alert_level,
        recommendation_score=result.anomaly_score,
        recommendation_reason=reasons_text,
        analysis_mode=result.analysis_mode,
        alert_level=result.alert_level,
        anomaly_score=result.anomaly_score,
        fair_price_rub=result.fair_price_rub,
        local_median_rub=result.local_median_rub,
        float_peer_median_rub=result.float_peer_median_rub,
        historical_baseline_rub=result.historical_baseline_rub,
        local_discount_percent=result.local_discount_percent,
        float_peer_discount_percent=result.float_peer_discount_percent,
        historical_discount_percent=result.historical_discount_percent,
        robust_z=result.robust_z,
        float_bucket=result.float_bucket or "",
        exact_item_match=result.exact_item_match,
        sample_size=result.sample_size,
        neighbor_count=result.neighbor_count,
        anomaly_reasons=reasons_text,
        anomaly_baseline_price_rub=result.anomaly_baseline_price_rub,
        exit_price_rub=result.exit_price_rub,
        exit_price_model=result.exit_price_model,
        solo_exit_price_rub=result.solo_exit_price_rub,
        sweep_exit_price_rub=result.sweep_exit_price_rub,
        market_confidence=result.market_confidence,
        liquidity_score=result.liquidity_score,
        requires_sweep=result.requires_sweep,
        manual_review_required=result.manual_review_required,
        pack_id=result.pack_id,
        pack_size=result.pack_size,
        pack_cost_rub=result.pack_cost_rub,
        pack_floor_after_rub=result.pack_floor_after_rub,
        capital_required_rub=result.capital_required_rub,
        substitute_floor_rub=result.substitute_floor_rub,
        substitute_cap_rub=result.substitute_cap_rub,
        raw_anomaly_score=result.raw_anomaly_score,
        risk_adjusted_score=result.risk_adjusted_score,
        parsed_at=parsed_at_iso(result.listing),
    )
