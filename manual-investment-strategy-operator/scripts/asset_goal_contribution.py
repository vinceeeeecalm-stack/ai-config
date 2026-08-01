#!/usr/bin/env python3
"""Build asset-level goal contribution and staking compounding panel.

The script converts the current portfolio snapshot plus the screenshot-derived
ledger into a report-ready panel. It answers: which assets help the 5y/10y 10x
path, which assets slow it down, and how much price appreciation is still
needed after staking compounding.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER_CANDIDATES = [
    ROOT / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json",
    Path.cwd() / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json",
    Path.home() / "Documents" / "investing" / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json",
]
DEFAULT_LEDGER = next((path for path in DEFAULT_LEDGER_CANDIDATES if path.exists()), DEFAULT_LEDGER_CANDIDATES[0])

STAKE_APY_OVERRIDES = {
    "ADA": {
        "apy": 0.03,
        "source": "user_stated_latest_apy",
        "note": "User stated ADA APY is approximately 3%; older Ledger screenshot/ledger value may show a lower realized display APY.",
    }
}

STAKING_ACTIVATION_DELAY_DEFAULTS_DAYS = {
    "ADA": 21,
    "SOL": 3,
    "ETH": 0,
}

ZERO_HOLDING_CANDIDATES = {
    "BTC": {
        "display": "BTC candidate",
        "rail": "crypto",
        "bucket": "opportunistic_liquidity_anchor",
        "quantity": 0.0,
        "current_value": 0.0,
        "current_weight_pct": 0.0,
        "data_quality": "not_in_current_holdings",
    }
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str | None) -> Any:
    if not path:
        return None
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100.0, 4)


def money_round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def snapshot_holdings(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    holdings: dict[str, dict[str, Any]] = {}
    for item in snapshot.get("holdings", []):
        symbol = item.get("symbol")
        if not symbol:
            continue
        values = item.get("values") or {}
        weights = item.get("weights") or {}
        holdings[symbol] = {
            "symbol": symbol,
            "display": item.get("display"),
            "rail": item.get("rail"),
            "bucket": item.get("bucket"),
            "quantity": item.get("quantity"),
            "current_price": (item.get("prices") or {}).get("current"),
            "current_value": values.get("current"),
            "current_weight_pct": weights.get("current"),
            "data_quality": item.get("data_quality"),
            "market": item.get("market") or {},
        }
    return holdings


def ledger_holdings(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    holdings: dict[str, dict[str, Any]] = {}
    for item in ledger.get("holdings", []):
        symbol = item.get("symbol")
        if not symbol:
            continue
        holdings[symbol] = item
    return holdings


def ledger_for_snapshot_symbol(symbol: str, ledger: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if symbol == "ETH":
        return ledger.get("lcETH") or ledger.get("ETH") or {}
    return ledger.get(symbol) or {}


def staking_summary(symbol: str, ledger_item: dict[str, Any]) -> dict[str, Any]:
    staking = ledger_item.get("staking") or {}
    ledger_apy = staking.get("estimated_apy")
    apy_source = "ledger"
    apy_note = None
    apy_quality = "verified" if ledger_apy is not None else "missing"

    if symbol in STAKE_APY_OVERRIDES:
        override = STAKE_APY_OVERRIDES[symbol]
        apy = float(override["apy"])
        apy_source = override["source"]
        apy_note = override["note"]
        apy_quality = "degraded_user_stated_override" if ledger_apy is not None and abs(float(ledger_apy) - apy) > 0.005 else "verified"
    elif ledger_apy is not None:
        apy = float(ledger_apy)
    else:
        apy = None

    def multiplier(years: int) -> float | None:
        if apy is None:
            return None
        return (1.0 + apy) ** years

    five_multiplier = multiplier(5)
    ten_multiplier = multiplier(10)
    activation_delay_days = staking.get("staking_activation_delay_days")
    if activation_delay_days is None and bool(staking.get("staking_allowed", False)):
        activation_delay_days = STAKING_ACTIVATION_DELAY_DEFAULTS_DAYS.get(symbol)
    return {
        "staking_allowed": bool(staking.get("staking_allowed", False)),
        "provider_or_protocol": staking.get("staking_provider"),
        "apy_used": apy,
        "apy_used_pct": pct(apy),
        "ledger_apy": ledger_apy,
        "apy_source": apy_source,
        "apy_data_quality": apy_quality,
        "apy_note": apy_note,
        "lock_status": staking.get("lock_status"),
        "unlock_or_delay": staking.get("staking_unlock_delay_days") or staking.get("unlock_available_at"),
        "staking_activation_delay_days": activation_delay_days,
        "count_as_immediate_liquidity": staking.get("count_as_immediate_liquidity"),
        "five_year_compounding_multiplier": round(five_multiplier, 4) if five_multiplier is not None else None,
        "ten_year_compounding_multiplier": round(ten_multiplier, 4) if ten_multiplier is not None else None,
        "five_year_price_multiple_required_for_10x_after_staking": round(10.0 / five_multiplier, 4) if five_multiplier else None,
        "ten_year_price_multiple_required_for_10x_after_staking": round(10.0 / ten_multiplier, 4) if ten_multiplier else None,
        "staking_risk_notes": [
            item for item in [
                staking.get("slash_risk"),
                staking.get("provider_risk"),
                "valuation disputed" if "disputed" in str(staking.get("staking_valuation_status")) else None,
            ]
            if item
        ],
    }


def classify_asset(
    symbol: str,
    holding: dict[str, Any],
    ledger_item: dict[str, Any],
    total_value: float,
    crypto_value: float,
    staking: dict[str, Any],
) -> dict[str, Any]:
    value = float(holding.get("current_value") or 0.0)
    weight_pct = float(holding.get("current_weight_pct") or 0.0)
    crypto_weight_pct = value / crypto_value * 100.0 if crypto_value and holding.get("rail") == "crypto" else None
    target_weight = ledger_item.get("target_weight")
    target_weight_pct = pct(float(target_weight)) if target_weight is not None else None
    bucket = str(holding.get("bucket") or ledger_item.get("bucket") or "")
    data_quality = str(holding.get("data_quality") or ledger_item.get("data_quality_status") or "missing")

    label = "neutral"
    action = "hold_weight"
    dca_implication = "not_relevant"
    rationale = []
    missing_data = []
    score = 50

    if symbol in {"USD_US_EQUITY", "USDT"}:
        label = "neutral"
        action = "hold_weight"
        dca_implication = "reserve_or_relay"
        score = 35 if symbol == "USDT" and value < max(total_value * 0.02, 500) else 45
        rationale.append("Cash/stablecoin improves survival and optionality but cannot compound toward 10x.")
    elif symbol == "ETH":
        label = "drag" if weight_pct >= 35 else "neutral"
        action = "reduce_new_dca"
        dca_implication = "avoid_new_dca_until_concentration_falls"
        score = 25 if label == "drag" else 45
        rationale.append("ETH/lcETH is quality core exposure, but current concentration and low staking yield slow the 10x path.")
        missing_data.append("lcETH receipt conversion, redemption, fees and unlock confirmation")
    elif symbol == "SOL":
        label = "accelerator"
        action = "increase_weight"
        dca_implication = "primary_growth_dca_candidate"
        score = 82 if weight_pct < 8 else 72
        rationale.append("SOL combines high beta, staking compounding and low current portfolio weight versus the growth role.")
        if staking.get("unlock_or_delay"):
            rationale.append("Staked SOL has cooldown/friction, so DCA should remain staged rather than all-in.")
    elif symbol == "ADA":
        label = "accelerator" if crypto_weight_pct is not None and crypto_weight_pct < 22 else "neutral"
        action = "increase_weight" if label == "accelerator" else "hold_weight"
        dca_implication = "secondary_deep_value_staking_dca" if label == "accelerator" else "hold_or_small_only"
        score = 68 if label == "accelerator" else 56
        rationale.append("ADA can be a deep-value staking satellite, but lower ecosystem momentum keeps it below SOL as primary engine.")
        if staking.get("apy_data_quality") != "verified":
            missing_data.append("ADA live staking APY/provider display should be rechecked before increasing size aggressively")
    elif symbol == "NIGHT":
        label = "tail_convexity"
        action = "watch_only"
        dca_implication = "tail_only_no_large_dca_without_unlock_liquidity_verification"
        score = 42
        rationale.append("NIGHT has high convexity but no staking compounding and still depends on verified liquidity, float/unlock and ecosystem traction.")
        missing_data.extend(["NIGHT official circulating float/unlock schedule", "NIGHT order-book depth and multi-source price confirmation"])
    elif symbol == "BTC":
        label = "neutral"
        action = "hold_weight"
        dca_implication = "opportunistic_liquidity_anchor_only"
        score = 38
        rationale.append("BTC is the highest quality liquidity anchor but lacks staking yield and likely has lower 10x convexity than SOL/selected satellites.")
    elif symbol == "CRCL":
        label = "tail_convexity"
        action = "hold_weight"
        dca_implication = "protected_long_term_us_equity_no_regular_tactical_funding"
        score = 58
        rationale.append("CRCL can contribute equity upside through stablecoin infrastructure, but it is not crypto DCA and remains crypto-beta correlated.")
    elif symbol == "COIN":
        label = "tail_convexity"
        action = "hold_weight"
        dca_implication = "small_crypto_beta_equity_no_add_without_stronger_signal"
        score = 52
        rationale.append("COIN is high-beta crypto equity exposure; useful as small satellite, not a DCA core.")
    elif symbol == "SOXL":
        label = "drag"
        action = "tactical_only"
        dca_implication = "excluded_from_long_term_dca"
        score = 10
        rationale.append("SOXL can pursue tactical monthly/quarterly gains, but leveraged ETF decay excludes it from long-term 10x DCA logic.")
    else:
        label = "neutral"
        action = "watch_only"
        dca_implication = "insufficient_policy_mapping"
        score = 40
        rationale.append("No asset-specific policy mapping found.")

    if "degraded" in data_quality or "missing" in data_quality:
        missing_data.append(f"{symbol} current valuation data quality is {data_quality}")

    return {
        "symbol": symbol,
        "display": holding.get("display"),
        "rail": holding.get("rail"),
        "bucket": holding.get("bucket") or bucket,
        "current_value_usd": money_round(value),
        "current_weight_pct": round(weight_pct, 4),
        "crypto_rail_weight_pct": round(crypto_weight_pct, 4) if crypto_weight_pct is not None else None,
        "target_weight_pct": target_weight_pct,
        "goal_gap_contribution": label,
        "action_implication": action,
        "dca_weight_implication": dca_implication,
        "goal_alignment_score_points": score,
        "staking_compounding_summary": staking,
        "data_quality_status": data_quality,
        "rationale": rationale,
        "missing_data": missing_data,
        "what_would_change_this_label": change_conditions(symbol, label),
    }


def change_conditions(symbol: str, label: str) -> list[str]:
    mapping = {
        "ETH": [
            "ETH/lcETH concentration falls below 30% of total portfolio.",
            "lcETH redemption and conversion data becomes verified and ETH ecosystem momentum improves.",
        ],
        "SOL": [
            "Network reliability or ecosystem metrics deteriorate materially.",
            "SOL weight rises enough that marginal DCA no longer improves 10x odds.",
        ],
        "ADA": [
            "ADA ecosystem TVL/developer/liquidity data fails to improve despite low valuation.",
            "Live staking APY or liquidity is worse than the current assumption.",
        ],
        "NIGHT": [
            "Official float/unlock/liquidity data becomes verified and ecosystem usage grows.",
            "NIGHT loses exchange liquidity or official thesis weakens.",
        ],
        "BTC": [
            "Market enters extreme fear or portfolio needs a higher liquidity anchor.",
            "BTC dominance and risk-adjusted setup improves versus SOL/ADA.",
        ],
        "SOXL": [
            "SOXL is exited or replaced by another tactical sleeve; it remains excluded from long-term DCA.",
        ],
    }
    return mapping.get(symbol, [f"New verified data changes the {symbol} thesis, liquidity, valuation, or risk profile."])


def long_horizon_timing_decision(
    asset: dict[str, Any],
    suggested_range: list[float],
    planned_wait_days: int = 14,
) -> dict[str, Any]:
    """Estimate whether waiting for a better DCA price is worth the staking/time cost.

    This is intentionally conservative: it does not claim a long-term low is
    verified unless the asset role and data support it. The output is a report
    prompt for human review, not a trading instruction.
    """

    symbol = asset.get("symbol")
    label = asset.get("goal_gap_contribution")
    implication = str(asset.get("dca_weight_implication") or "")
    staking = asset.get("staking_compounding_summary") or {}
    data_quality = str(asset.get("data_quality_status") or "")
    amount = float(suggested_range[-1] if suggested_range else 0.0)
    apy = staking.get("apy_used")
    staking_allowed = bool(staking.get("staking_allowed"))
    apy_verified = staking.get("apy_data_quality") == "verified"
    apy_float = float(apy) if isinstance(apy, (int, float)) else 0.0
    staking_wait_cost_usd = amount * apy_float * planned_wait_days / 365.0 if amount and apy_float else 0.0
    staking_wait_cost_pct = apy_float * planned_wait_days / 365.0 * 100.0 if apy_float else 0.0
    activation_delay_raw = staking.get("staking_activation_delay_days")
    try:
        staking_activation_delay_days = int(float(activation_delay_raw)) if activation_delay_raw is not None else 0
    except (TypeError, ValueError):
        staking_activation_delay_days = 0
    staking_activation_delay_cost_usd = (
        amount * apy_float * staking_activation_delay_days / 365.0
        if amount and apy_float and staking_activation_delay_days
        else 0.0
    )
    staking_activation_delay_cost_pct = (
        apy_float * staking_activation_delay_days / 365.0 * 100.0
        if apy_float and staking_activation_delay_days
        else 0.0
    )
    today = dt.datetime.now(dt.timezone.utc).date()
    estimated_staking_start_if_buy_now = (
        (today + dt.timedelta(days=staking_activation_delay_days)).isoformat()
        if staking_allowed and staking_activation_delay_days >= 0
        else None
    )
    estimated_staking_start_if_wait = (
        (today + dt.timedelta(days=planned_wait_days + staking_activation_delay_days)).isoformat()
        if staking_allowed and staking_activation_delay_days >= 0
        else None
    )

    missed_upside_buffer_pct = 0.0
    if label == "accelerator":
        missed_upside_buffer_pct = 2.0
    elif label == "tail_convexity":
        missed_upside_buffer_pct = 3.0
    elif "staking" in implication:
        missed_upside_buffer_pct = 1.5

    execution_uncertainty_buffer_pct = 0.75 if amount > 0 else 0.0
    required_pullback_to_wait_pct = (
        staking_wait_cost_pct
        + staking_activation_delay_cost_pct
        + missed_upside_buffer_pct
        + execution_uncertainty_buffer_pct
    )
    current_weight_pct = float(asset.get("current_weight_pct") or 0.0)

    long_term_value_zone_status = "unknown_requires_price_location"
    if symbol == "SOL" and label == "accelerator" and (asset.get("current_weight_pct") or 0) < 8:
        long_term_value_zone_status = "likely_attractive_if_market_data_not_deteriorating"
    elif symbol == "ADA" and label == "accelerator":
        long_term_value_zone_status = "deep_value_candidate_if_live_ecosystem_and_liquidity_hold"
    elif symbol == "ETH":
        long_term_value_zone_status = "quality_hold_but_overweight_for_new_dca"
    elif symbol == "NIGHT":
        long_term_value_zone_status = "tail_convexity_requires_unlock_and_liquidity_confirmation"
    elif symbol == "USDT":
        long_term_value_zone_status = "reserve_not_growth_asset"

    low_value_evidence_summary = {
        "likely_attractive_if_market_data_not_deteriorating": "SOL is an underweight growth accelerator; long-term entry can be attractive if market/liquidity data are not deteriorating.",
        "deep_value_candidate_if_live_ecosystem_and_liquidity_hold": "ADA is treated as a deep-value staking satellite when live ecosystem and liquidity checks remain acceptable.",
        "quality_hold_but_overweight_for_new_dca": "ETH/lcETH can remain a quality hold, but current concentration makes new DCA less efficient for the 10x path.",
        "tail_convexity_requires_unlock_and_liquidity_confirmation": "NIGHT may have long-duration upside, but low-value status needs verified unlock, float and liquidity data.",
        "reserve_not_growth_asset": "USDT is only an opportunity bucket; it does not compound toward the long-term target unless deployed.",
    }.get(long_term_value_zone_status, "Long-term low-value status needs fresh price-location, liquidity, supply and thesis evidence.")

    missing = asset.get("missing_data") or []
    has_blocking_missing = any(
        keyword in str(item).lower()
        for item in missing
        for keyword in ["unlock", "liquidity", "depth", "receipt", "redemption"]
    )
    bad_data = any(token in data_quality for token in ["disputed", "stale", "missing"])
    low_value_candidate_statuses = {
        "likely_attractive_if_market_data_not_deteriorating",
        "deep_value_candidate_if_live_ecosystem_and_liquidity_hold",
    }
    is_long_term_low_value_candidate = long_term_value_zone_status in low_value_candidate_statuses
    time_in_market_bias_score = 0
    if is_long_term_low_value_candidate:
        time_in_market_bias_score += 30
    if staking_allowed and apy_verified:
        time_in_market_bias_score += 25
    if label in {"accelerator", "tail_convexity"}:
        time_in_market_bias_score += 20
    if current_weight_pct < 12.0:
        time_in_market_bias_score += 15
    if required_pullback_to_wait_pct <= 4.5:
        time_in_market_bias_score += 10
    if bad_data:
        time_in_market_bias_score -= 40
    if has_blocking_missing:
        time_in_market_bias_score -= 30
    time_in_market_bias_score = max(0, min(100, time_in_market_bias_score))
    wait_requires_specific_pullback_trigger = bool(time_in_market_bias_score >= 60 and amount > 0)
    time_horizon_years = [5, 10]
    waiting_burden_of_proof_comment = (
        "Waiting must show a clear, time-bounded pullback advantage. If the asset is a verified "
        "long-term low-value, underweight, stakeable candidate, idle cash is treated as a drag "
        "unless the expected discount beats staking delay, missed recovery risk and execution uncertainty."
    )

    if symbol == "USDT":
        decision = "hold_stablecoin_until_trigger"
        reason = "Stablecoin is an opportunity bucket; deploy it when verified long-term assets reach timing/quality triggers."
    elif symbol == "NIGHT":
        decision = "limit_order_wait" if has_blocking_missing else "near_price_entry"
        reason = "NIGHT has no staking yield, so early-entry benefit depends on verified liquidity, unlock and thesis rather than compounding."
    elif symbol == "ETH":
        decision = "hold_stablecoin_until_trigger"
        reason = "ETH/lcETH is quality exposure but current concentration blocks new DCA even though staking exists."
    elif bad_data or has_blocking_missing:
        decision = "limit_order_wait"
        reason = "Material data is missing or degraded, so wait for a better price and fresh verification before increasing size."
    elif (
        staking_allowed
        and apy_verified
        and label == "accelerator"
        and is_long_term_low_value_candidate
        and current_weight_pct < 12.0
        and (required_pullback_to_wait_pct <= 4.5 or time_in_market_bias_score >= 75)
    ):
        decision = "accelerated_dca"
        reason = "Underweight long-term low-value accelerator with verified staking; waiting must beat compounding, time-in-market, and missed-recovery risk."
    elif staking_allowed and label in {"accelerator", "neutral"}:
        decision = "near_price_entry"
        reason = "Stakeable long-term asset; buy the first tranche near price, keep the second tranche for pullback confirmation."
    else:
        decision = "limit_order_wait"
        reason = "No verified staking compounding edge; waiting for a better entry has lower opportunity cost."

    front_load_extra_months = 0.0
    if decision == "accelerated_dca":
        if time_in_market_bias_score >= 85 and current_weight_pct < 8.0:
            front_load_extra_months = 1.0
        else:
            front_load_extra_months = 0.5 if current_weight_pct < 12.0 else 0.25
    elif (
        decision == "near_price_entry"
        and is_long_term_low_value_candidate
        and staking_allowed
        and apy_verified
        and current_weight_pct < 8.0
    ):
        front_load_extra_months = 0.25
    front_load_amount_usd = amount * front_load_extra_months
    if front_load_extra_months >= 1.0:
        front_load_reason = "Strong low-value, verified staking and underweight status justify pulling one future DCA month forward if the cash rail is confirmed."
    elif front_load_extra_months > 0:
        front_load_reason = "Partial front-load is allowed because early staking/time-in-market may be more valuable than waiting for a small extra discount."
    else:
        front_load_reason = "No front-load because data, weight, or timing evidence does not justify using future DCA now."
    early_entry_benefit_summary = (
        "Earlier entry starts staking/time-in-market now and may reduce missed recovery and time-cost risk."
        if staking_allowed and apy_float
        else "Early-entry benefit comes from long-term price exposure; no verified staking compounding edge is counted."
    )
    if symbol == "USDT":
        cash_idle_drag_comment = "Cash helps only as a short waiting bucket; held too long, it does not help the 5-10y 10x path."
    elif decision in {"accelerated_dca", "near_price_entry"}:
        cash_idle_drag_comment = "Idle stablecoin can drag the goal path if the asset is verified low-value and underweight."
    else:
        cash_idle_drag_comment = "Cash wait is acceptable only because the discount/data trigger is not yet strong enough."
    minimum_extra_discount_required_to_wait_pct = round(required_pullback_to_wait_pct, 4)
    if decision == "accelerated_dca":
        dynamic_buy_strategy = "front_load_plus_near_price_first_tranche"
    elif decision == "near_price_entry":
        dynamic_buy_strategy = "near_price_first_tranche_then_better_pullback_second_tranche"
    elif decision == "limit_order_wait":
        dynamic_buy_strategy = "limit_order_wait_with_explicit_deadline_and_recheck"
    else:
        dynamic_buy_strategy = "hold_stablecoin_until_data_or_price_trigger"
    low_value_entry_panel = {
        "time_horizon_years": time_horizon_years,
        "long_term_low_value_zone_status": long_term_value_zone_status,
        "low_value_evidence_summary": low_value_evidence_summary,
        "early_entry_or_staking_benefit": early_entry_benefit_summary,
        "staking_activation_delay_days": staking_activation_delay_days,
        "staking_activation_delay_cost_usd": money_round(staking_activation_delay_cost_usd),
        "estimated_staking_start_if_buy_now": estimated_staking_start_if_buy_now,
        "estimated_staking_start_if_wait": estimated_staking_start_if_wait,
        "time_in_market_bias_score": time_in_market_bias_score,
        "wait_requires_specific_pullback_trigger": wait_requires_specific_pullback_trigger,
        "minimum_extra_discount_required_to_wait_pct": minimum_extra_discount_required_to_wait_pct,
        "cash_idle_drag_comment": cash_idle_drag_comment,
        "waiting_burden_of_proof_comment": waiting_burden_of_proof_comment,
        "dynamic_buy_strategy": dynamic_buy_strategy,
        "final_timing_decision": decision,
    }

    return {
        "symbol": symbol,
        "decision": decision,
        "time_horizon_years": time_horizon_years,
        "planned_wait_days": planned_wait_days,
        "planned_amount_usd": money_round(amount),
        "apy_used": apy,
        "apy_used_pct": staking.get("apy_used_pct"),
        "staking_wait_cost_usd": money_round(staking_wait_cost_usd),
        "staking_wait_cost_pct": round(staking_wait_cost_pct, 4),
        "staking_activation_delay_days": staking_activation_delay_days,
        "staking_activation_delay_cost_usd": money_round(staking_activation_delay_cost_usd),
        "staking_activation_delay_cost_pct": round(staking_activation_delay_cost_pct, 4),
        "estimated_staking_start_if_buy_now": estimated_staking_start_if_buy_now,
        "estimated_staking_start_if_wait": estimated_staking_start_if_wait,
        "front_load_extra_months": round(front_load_extra_months, 4),
        "front_load_amount_usd": money_round(front_load_amount_usd),
        "near_term_total_budget_usd": money_round(amount + front_load_amount_usd),
        "missed_upside_buffer_pct": missed_upside_buffer_pct,
        "execution_uncertainty_buffer_pct": execution_uncertainty_buffer_pct,
        "required_pullback_to_wait_pct": round(required_pullback_to_wait_pct, 4),
        "long_term_value_zone_status": long_term_value_zone_status,
        "long_term_low_value_zone_status": long_term_value_zone_status,
        "low_value_evidence_summary": low_value_evidence_summary,
        "early_entry_benefit_summary": early_entry_benefit_summary,
        "time_in_market_bias_score": time_in_market_bias_score,
        "wait_requires_specific_pullback_trigger": wait_requires_specific_pullback_trigger,
        "front_load_reason": front_load_reason,
        "minimum_extra_discount_required_to_wait_pct": minimum_extra_discount_required_to_wait_pct,
        "cash_idle_drag_comment": cash_idle_drag_comment,
        "waiting_burden_of_proof_comment": waiting_burden_of_proof_comment,
        "dynamic_buy_strategy": dynamic_buy_strategy,
        "final_timing_decision": decision,
        "long_term_low_value_entry_panel": low_value_entry_panel,
        "reason": reason,
        "plain_language_note": "If the expected discount from waiting is smaller than this required pullback, earlier DCA plus staking/time-in-market is usually favored.",
    }


def aggregate_dca_guidance(assets: list[dict[str, Any]], monthly_dca: float) -> dict[str, Any]:
    by_symbol = {item["symbol"]: item for item in assets}
    sol = by_symbol.get("SOL")
    ada = by_symbol.get("ADA")
    night = by_symbol.get("NIGHT")
    usdt = by_symbol.get("USDT")

    guidance = []
    if sol:
        guidance.append({
            "symbol": "SOL",
            "role": "primary_growth_engine",
            "suggested_monthly_range_usd": [round(monthly_dca * 0.40, 2), round(monthly_dca * 0.55, 2)],
            "condition": "Use staged entries; avoid all-in if BTC/ETH risk appetite remains weak.",
        })
    if ada:
        guidance.append({
            "symbol": "ADA",
            "role": "secondary_deep_value_staking_satellite",
            "suggested_monthly_range_usd": [round(monthly_dca * 0.15, 2), round(monthly_dca * 0.22, 2)],
            "condition": "Use only if live APY and liquidity remain acceptable; keep below SOL unless ecosystem data improves.",
        })
    if night:
        guidance.append({
            "symbol": "NIGHT",
            "role": "tail_convexity_watch",
            "suggested_monthly_range_usd": [0, round(monthly_dca * 0.10, 2)],
            "condition": "Only small tail DCA after float/unlock/liquidity and official thesis are verified.",
        })
    if usdt:
        guidance.append({
            "symbol": "USDT",
            "role": "opportunity_reserve",
            "suggested_monthly_range_usd": [round(monthly_dca * 0.05, 2), round(monthly_dca * 0.20, 2)],
            "condition": "Reserve is useful when macro or prices are not attractive; do not hold idle cash indefinitely when high-quality pullbacks appear.",
        })

    timing_decisions = []
    for item in guidance:
        symbol = item.get("symbol")
        asset = by_symbol.get(symbol)
        if asset:
            timing = long_horizon_timing_decision(asset, item.get("suggested_monthly_range_usd") or [])
            item["long_horizon_timing_decision"] = timing
            timing_decisions.append(timing)

    avoid = []
    for symbol in ["ETH", "BTC"]:
        item = by_symbol.get(symbol)
        if item:
            avoid.append({
                "symbol": symbol,
                "reason": item.get("dca_weight_implication"),
            })
    return {
        "monthly_dca_usd": monthly_dca,
        "primary_pair": "SOLUSDT" if sol else None,
        "secondary_pair": "ADAUSDT" if ada else None,
        "satellite_pair": "NIGHTUSDT conditional" if night else None,
        "suggested_ranges": guidance,
        "long_horizon_timing_decisions": timing_decisions,
        "avoid_or_zero_new_dca": avoid,
    }


def build_panel(
    snapshot: dict[str, Any],
    ledger: dict[str, Any],
    goal_projection: dict[str, Any] | None,
    monthly_dca: float,
    include_candidates: list[str],
) -> dict[str, Any]:
    holdings = snapshot_holdings(snapshot)
    for symbol in include_candidates:
        if symbol not in holdings and symbol in ZERO_HOLDING_CANDIDATES:
            holdings[symbol] = dict(ZERO_HOLDING_CANDIDATES[symbol])
    ledger_by_symbol = ledger_holdings(ledger)
    totals = snapshot.get("totals", {}).get("current", {})
    total_value = float(totals.get("total_value") or 0.0)
    crypto_value = float(totals.get("crypto_value") or 0.0)
    goal_required = (goal_projection or {}).get("required", {})

    order = ["ETH", "SOL", "ADA", "NIGHT", "BTC", "USDT", "CRCL", "COIN", "SOXL", "USD_US_EQUITY"]
    assets = []
    for symbol in order:
        if symbol not in holdings:
            continue
        ledger_item = ledger_for_snapshot_symbol(symbol, ledger_by_symbol)
        staking = staking_summary(symbol, ledger_item)
        assets.append(classify_asset(symbol, holdings[symbol], ledger_item, total_value, crypto_value, staking))

    accelerator_value = sum(item["current_value_usd"] or 0 for item in assets if item["goal_gap_contribution"] == "accelerator")
    drag_value = sum(item["current_value_usd"] or 0 for item in assets if item["goal_gap_contribution"] == "drag")
    tail_value = sum(item["current_value_usd"] or 0 for item in assets if item["goal_gap_contribution"] == "tail_convexity")
    neutral_value = sum(item["current_value_usd"] or 0 for item in assets if item["goal_gap_contribution"] == "neutral")

    missing = []
    for item in assets:
        for missing_item in item.get("missing_data") or []:
            if missing_item not in missing:
                missing.append(missing_item)

    concentration_flags = []
    eth = next((item for item in assets if item["symbol"] == "ETH"), None)
    if eth and eth["current_weight_pct"] >= 35:
        concentration_flags.append("ETH/lcETH concentration is above 35%; new ETH/lcETH DCA is blocked.")
    if drag_value / total_value > 0.45 if total_value else False:
        concentration_flags.append("Drag-labeled assets exceed 45% of total value; new DCA must favor accelerators or reserves.")

    data_quality = "verified"
    if missing:
        data_quality = "degraded"

    return {
        "generated_at": utc_now(),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "ledger_as_of": ledger.get("as_of"),
        "monthly_dca_usd": monthly_dca,
        "portfolio_value_usd": money_round(total_value),
        "crypto_value_usd": money_round(crypto_value),
        "goal_required_returns": {
            "five_year_current_principal_required_annual_pct": pct((goal_required.get("5") or {}).get("current_principal_required_annual_rate")),
            "ten_year_current_principal_required_annual_pct": pct((goal_required.get("10") or {}).get("current_principal_required_annual_rate")),
            "five_year_cumulative_capital_required_annual_pct": pct((goal_required.get("5") or {}).get("cumulative_capital_required_annual_rate")),
            "ten_year_cumulative_capital_required_annual_pct": pct((goal_required.get("10") or {}).get("cumulative_capital_required_annual_rate")),
        },
        "allocation_by_goal_label": {
            "accelerator_value_usd": money_round(accelerator_value),
            "accelerator_weight_pct": round(accelerator_value / total_value * 100.0, 4) if total_value else None,
            "tail_convexity_value_usd": money_round(tail_value),
            "tail_convexity_weight_pct": round(tail_value / total_value * 100.0, 4) if total_value else None,
            "neutral_value_usd": money_round(neutral_value),
            "neutral_weight_pct": round(neutral_value / total_value * 100.0, 4) if total_value else None,
            "drag_value_usd": money_round(drag_value),
            "drag_weight_pct": round(drag_value / total_value * 100.0, 4) if total_value else None,
        },
        "assets": assets,
        "dca_guidance": aggregate_dca_guidance(assets, monthly_dca),
        "concentration_flags": concentration_flags,
        "missing_data": missing,
        "data_quality": data_quality,
        "max_allowed_action": "conditional_action" if data_quality == "degraded" else "conditional_action_or_hold",
        "interpretation": interpret_panel(accelerator_value, drag_value, tail_value, total_value),
    }


def interpret_panel(accelerator_value: float, drag_value: float, tail_value: float, total_value: float) -> list[str]:
    if total_value <= 0:
        return ["Portfolio value is missing; cannot classify goal contribution."]
    notes = []
    accelerator_pct = accelerator_value / total_value * 100.0
    drag_pct = drag_value / total_value * 100.0
    tail_pct = tail_value / total_value * 100.0
    if drag_pct > 50:
        notes.append("Current allocation is too concentrated in drag-labeled assets for a 5y 10x path; new DCA must favor accelerator assets.")
    elif accelerator_pct < 15:
        notes.append("Accelerator exposure is still small relative to the 5y/10y 10x target; staged SOL/ADA DCA has higher goal relevance than adding BTC/ETH.")
    if tail_pct > 20:
        notes.append("Tail-convexity exposure is material; avoid adding more unverified high-risk tails until liquidity and thesis data improve.")
    else:
        notes.append("Tail exposure is not excessive, but new tail DCA still requires liquidity/unlock verification.")
    notes.append("Staking yield helps but does not replace price appreciation; even 5% APY still requires multi-x price gains to reach 10x.")
    return notes


def render_markdown(panel: dict[str, Any]) -> str:
    def cell(value: Any) -> Any:
        return "n/a" if value is None else value

    lines = [
        "### Asset Goal Contribution & Staking Panel",
        f"- Portfolio: `${panel.get('portfolio_value_usd')}`; monthly DCA: `${panel.get('monthly_dca_usd')}`",
        f"- 5y required annual: `{panel.get('goal_required_returns', {}).get('five_year_current_principal_required_annual_pct')}%`; 10y required annual: `{panel.get('goal_required_returns', {}).get('ten_year_current_principal_required_annual_pct')}%`",
        f"- Data quality: `{panel.get('data_quality')}`; max action: `{panel.get('max_allowed_action')}`",
        "",
        "| Asset | Weight | Label | APY | 5y stake x | 10y stake x | 5y price x needed | 10y price x needed | DCA implication |",
        "|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in panel.get("assets", []):
        staking = item.get("staking_compounding_summary") or {}
        lines.append(
            f"| {item.get('symbol')} | {item.get('current_weight_pct')}% | {item.get('goal_gap_contribution')} | "
            f"{cell(staking.get('apy_used_pct'))}% | {cell(staking.get('five_year_compounding_multiplier'))} | "
            f"{cell(staking.get('ten_year_compounding_multiplier'))} | "
            f"{cell(staking.get('five_year_price_multiple_required_for_10x_after_staking'))} | "
            f"{cell(staking.get('ten_year_price_multiple_required_for_10x_after_staking'))} | "
            f"{item.get('dca_weight_implication')} |"
        )
    if panel.get("interpretation"):
        lines.append("")
        for note in panel["interpretation"]:
            lines.append(f"- {note}")
    timing = (panel.get("dca_guidance") or {}).get("long_horizon_timing_decisions") or []
    if timing:
        lines.extend([
            "",
            "| Asset | Timing decision | Strategy | Low-value status | Wait days | Amount | Front-load | Near-term budget | Staking wait cost | Required pullback to wait | Cash idle drag | Reason |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|",
        ])
        for item in timing:
            lines.append(
                f"| {item.get('symbol')} | {item.get('decision')} | {item.get('dynamic_buy_strategy')} | "
                f"{item.get('long_term_low_value_zone_status') or item.get('long_term_value_zone_status')} | {item.get('planned_wait_days')} | "
                f"{cell(item.get('planned_amount_usd'))} | {cell(item.get('front_load_amount_usd'))} | {cell(item.get('near_term_total_budget_usd'))} | "
                f"{cell(item.get('staking_wait_cost_usd'))} | "
                f"{cell(item.get('required_pullback_to_wait_pct'))}% | {item.get('cash_idle_drag_comment')} | {item.get('reason')} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build asset-level goal contribution panel")
    parser.add_argument("--portfolio-snapshot-json", required=True)
    parser.add_argument("--ledger-json", default=str(DEFAULT_LEDGER))
    parser.add_argument("--goal-projection-json")
    parser.add_argument("--monthly-dca", type=float, default=1000.0)
    parser.add_argument("--include-candidates", default="BTC")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    snapshot = load_json(args.portfolio_snapshot_json)
    ledger = load_json(args.ledger_json)
    goal_projection = load_json(args.goal_projection_json)
    candidates = [item.strip().upper() for item in args.include_candidates.split(",") if item.strip()]
    panel = build_panel(snapshot, ledger, goal_projection, args.monthly_dca, candidates)
    if args.format == "markdown":
        sys.stdout.write(render_markdown(panel))
    else:
        print(json.dumps(panel, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
