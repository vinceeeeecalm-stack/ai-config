#!/usr/bin/env python3
"""Fast, research-only candidate discovery and committee-tier contracts.

The discovery phase is intentionally free of forecast probability, portfolio
cash, full financing audits, research committee work, and execution actions.
It only decides which market observations deserve the next validation budget.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime
from time import monotonic
from typing import Any, Callable, Iterable


DISCOVERY_SCHEMA_VERSION = "discovery-candidate-v1"
PHASE_BUDGET_SECONDS = {
    "discovery_top3_p95": 15.0,
    "validated_top1": 45.0,
    "deep_research_top1": 120.0,
    "end_to_end": 180.0,
}
FORBIDDEN_DISCOVERY_FIELDS = {
    "forecast_probability_pct",
    "probability_event",
    "deployable_cash",
    "execution_decision",
    "executable_amount",
    "research_panel",
    "financing_dilution_audit",
}


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _iso_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    return parsed.tzinfo is not None


@dataclass(frozen=True)
class DiscoveryCandidateV1:
    symbol: str
    asset_class: str
    market_time: str
    current_price: float
    turnover: float
    relative_volume: float | None
    trade_count: int | None
    vwap: float | None
    spread_bps: float | None
    depth_bid_usd: float | None
    depth_ask_usd: float | None
    anchor_state: dict[str, Any]
    confirmed_catalyst_present: bool | None
    discovery_score: float
    ranking_reason: tuple[str, ...]
    hard_rejection_reasons: tuple[str, ...] = ()
    return_1m_pct: float | None = None
    return_5m_pct: float | None = None
    return_15m_pct: float | None = None
    return_1d_pct: float | None = None
    return_5d_pct: float | None = None
    data_sources: tuple[str, ...] = ()
    data_quality_status: str = "verified"
    live_orders_enabled: bool = False
    private_api_used: bool = False
    schema_version: str = field(default=DISCOVERY_SCHEMA_VERSION, init=False)

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.symbol or not self.asset_class:
            errors.append("identity_required")
        if not _iso_datetime(self.market_time):
            errors.append("market_time_timezone_iso_required")
        if not _finite_number(self.current_price) or self.current_price <= 0:
            errors.append("current_price_positive_number_required")
        if not _finite_number(self.turnover) or self.turnover < 0:
            errors.append("turnover_non_negative_number_required")
        if not _finite_number(self.discovery_score) or not 0 <= self.discovery_score <= 100:
            errors.append("discovery_score_range_0_100_required")
        if not self.ranking_reason:
            errors.append("ranking_reason_required")
        short_horizon = any(
            value is not None
            for value in (self.return_1m_pct, self.return_5m_pct, self.return_15m_pct)
        )
        daily_horizon = self.return_1d_pct is not None and self.return_5d_pct is not None
        if not short_horizon and not daily_horizon:
            errors.append("short_or_daily_return_vector_required")
        if self.live_orders_enabled is not False or self.private_api_used is not False:
            errors.append("public_read_only_required")
        return errors

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        leaked = FORBIDDEN_DISCOVERY_FIELDS & set(payload)
        if leaked:
            raise ValueError(f"discovery_contract_forbidden_fields:{sorted(leaked)}")
        return payload


def candidate_from_payload(payload: dict[str, Any]) -> DiscoveryCandidateV1:
    allowed = {
        field_name
        for field_name, field_value in DiscoveryCandidateV1.__dataclass_fields__.items()
        if field_value.init
    }
    leaked = FORBIDDEN_DISCOVERY_FIELDS & set(payload)
    if leaked:
        raise ValueError(f"discovery_contract_forbidden_fields:{sorted(leaked)}")
    candidate = DiscoveryCandidateV1(
        **{key: value for key, value in payload.items() if key in allowed}
    )
    errors = candidate.validation_errors()
    if errors:
        raise ValueError(";".join(errors))
    return candidate


def rank_discovery_candidates(
    candidates: Iterable[DiscoveryCandidateV1 | dict[str, Any]],
    *,
    top_n: int = 3,
) -> dict[str, Any]:
    """Rank already-batched market observations without deep research work."""

    started = monotonic()
    accepted: list[DiscoveryCandidateV1] = []
    blocked: list[dict[str, Any]] = []
    for value in candidates:
        try:
            item = value if isinstance(value, DiscoveryCandidateV1) else candidate_from_payload(value)
        except (TypeError, ValueError) as exc:
            blocked.append(
                {
                    "symbol": value.get("symbol") if isinstance(value, dict) else None,
                    "hard_rejection_reasons": [f"invalid_discovery_contract:{exc}"],
                }
            )
            continue
        if item.hard_rejection_reasons:
            blocked.append(
                {
                    "symbol": item.symbol,
                    "hard_rejection_reasons": list(item.hard_rejection_reasons),
                    "discovery_score": item.discovery_score,
                }
            )
            continue
        accepted.append(item)
    accepted.sort(
        key=lambda item: (
            item.discovery_score,
            item.turnover,
            item.trade_count or 0,
            item.symbol,
        ),
        reverse=True,
    )
    elapsed = monotonic() - started
    return {
        "schema_version": "fast-candidate-funnel-v1",
        "phase": "discovery",
        "top_candidates": [item.to_dict() for item in accepted[: max(1, top_n)]],
        "blocked_candidates": blocked,
        "eligible_candidate_count": len(accepted),
        "elapsed_seconds": round(elapsed, 6),
        "budget_seconds": PHASE_BUDGET_SECONDS["discovery_top3_p95"],
        "within_budget": elapsed <= PHASE_BUDGET_SECONDS["discovery_top3_p95"],
        "committee_tier": committee_requirement(phase="discovery"),
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def committee_requirement(
    *,
    phase: str,
    request_mode: str | None = None,
    binary_event: bool = False,
    new_asset: bool = False,
    major_longterm_allocation: bool = False,
) -> dict[str, Any]:
    """Return the minimum research roles after discovery, never trade authority."""

    if phase == "discovery":
        tier = "none"
        roles: tuple[str, ...] = ()
    elif binary_event or new_asset or major_longterm_allocation:
        tier = "six_plus"
        roles = (
            "prior_thesis_challenge_agent",
            "portfolio_state_agent",
            "macro_regime_agent",
            "market_microstructure_agent",
            "official_event_and_fundamental_agent",
            "backtest_validation_agent",
        )
    elif request_mode == "intraday_scalp":
        tier = "two_role"
        roles = ("market_microstructure_agent", "portfolio_risk_agent")
    elif request_mode in {"tactical_1_7d", "event_trade_1_3w"}:
        tier = "four_role"
        roles = (
            "market_microstructure_agent",
            "official_event_and_fundamental_agent",
            "backtest_validation_agent",
            "portfolio_risk_agent",
        )
    else:
        tier = "two_role"
        roles = ("fundamental_quality_agent", "portfolio_risk_agent")
    return {
        "tier": tier,
        "required_role_count": len(roles),
        "required_roles": list(roles),
        "committee_does_not_authorize_execution": True,
    }


def phase_budget_status(started_at: float, phase: str) -> dict[str, Any]:
    key = {
        "discovery": "discovery_top3_p95",
        "validation": "validated_top1",
        "deep_research": "deep_research_top1",
        "end_to_end": "end_to_end",
    }[phase]
    elapsed = monotonic() - started_at
    budget = PHASE_BUDGET_SECONDS[key]
    return {
        "phase": phase,
        "elapsed_seconds": round(elapsed, 6),
        "budget_seconds": budget,
        "within_budget": elapsed <= budget,
    }


def ranked_historical_comparison(
    validated_candidates: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic Top3 comparison without inventing probability."""

    items = list(validated_candidates)[:3]
    rows: list[dict[str, Any]] = []
    primary_score = (
        float(items[0].get("setup_quality_score") or 0.0) if items else 0.0
    )
    primary_history = (
        items[0].get("historical_comparison")
        if items and isinstance(items[0].get("historical_comparison"), dict)
        else {}
    )
    for index, item in enumerate(items):
        historical = item.get("historical_comparison")
        historical = historical if isinstance(historical, dict) else {}
        sample_size = historical.get("sample_size")
        conservative_ev = historical.get("conservative_expected_value_pct")
        reward_risk = historical.get("reward_risk_ratio")
        profit_factor = historical.get("profit_factor")
        qualified = (
            isinstance(sample_size, int)
            and not isinstance(sample_size, bool)
            and sample_size >= 30
            and _finite_number(conservative_ev)
            and conservative_ev > 0
            and _finite_number(reward_risk)
            and reward_risk >= 2
            and _finite_number(profit_factor)
            and profit_factor > 1
        )
        setup_score = float(item.get("setup_quality_score") or 0.0)
        reasons = item.get("why_ranked_lower")
        if not isinstance(reasons, list) or not reasons:
            reasons = ["rank_1_highest_composite_history_and_current_setup"]
            if index > 0:
                reasons = []
                comparisons = (
                    ("conservative_expected_value_pct", "lower_conservative_ev"),
                    ("expected_return_pct", "lower_expected_return"),
                    ("profit_factor", "lower_profit_factor"),
                    ("max_drawdown_pct", "worse_max_drawdown"),
                )
                for field_name, reason_name in comparisons:
                    primary_value = primary_history.get(field_name)
                    alternative_value = historical.get(field_name)
                    if _finite_number(primary_value) and _finite_number(alternative_value):
                        delta = float(primary_value) - float(alternative_value)
                        if delta > 0:
                            reasons.append(f"{reason_name}_by_{round(delta, 4)}")
                primary_interval = primary_history.get(
                    "out_of_sample_win_rate_interval_pct"
                )
                alternative_interval = historical.get(
                    "out_of_sample_win_rate_interval_pct"
                )
                if (
                    isinstance(primary_interval, list)
                    and isinstance(alternative_interval, list)
                    and primary_interval
                    and alternative_interval
                    and _finite_number(primary_interval[0])
                    and _finite_number(alternative_interval[0])
                    and primary_interval[0] > alternative_interval[0]
                ):
                    reasons.append(
                        "lower_win_rate_interval_floor_by_"
                        f"{round(primary_interval[0] - alternative_interval[0], 4)}"
                    )
                if not qualified:
                    reasons.append("historical_quality_gate_not_passed")
                if setup_score < primary_score:
                    reasons.append(
                        f"setup_quality_score_lower_by_{round(primary_score - setup_score, 4)}"
                    )
                if not reasons:
                    reasons.append("lower_deterministic_tie_break_rank")
        rows.append(
            {
                "rank": index + 1,
                "symbol": item.get("symbol"),
                "setup_quality_score": setup_score,
                "historical_comparison_status": (
                    "qualified_history_summary"
                    if qualified
                    else "requires_manual_point_in_time_backfill_or_failed_quality_gate"
                ),
                "sample_size": sample_size,
                "out_of_sample_win_rate_interval_pct": historical.get(
                    "out_of_sample_win_rate_interval_pct"
                ),
                "conservative_expected_value_pct": conservative_ev,
                "expected_return_pct": historical.get("expected_return_pct"),
                "profit_factor": profit_factor,
                "max_drawdown_pct": historical.get("max_drawdown_pct"),
                "reward_risk_ratio": reward_risk,
                "walk_forward_positive_windows": historical.get(
                    "walk_forward_positive_windows"
                ),
                "walk_forward_total_windows": historical.get(
                    "walk_forward_total_windows"
                ),
                "evidence_ids": historical.get("evidence_ids") or [],
                "why_ranked_lower": reasons if index > 0 else [],
                "forecast_probability_emitted": False,
            }
        )
    return {
        "schema_version": "ranked-historical-comparison-v1",
        "primary_symbol": rows[0]["symbol"] if rows else None,
        "rows": rows,
        "qualified_alternative_symbols": [
            row["symbol"]
            for row in rows[1:]
            if row["historical_comparison_status"] == "qualified_history_summary"
        ],
        "history_simulation_is_not_forecast_probability": True,
        "manual_final_arbitration_required": True,
    }


def historical_rank_key(
    item: dict[str, Any], *, setup_score_field: str
) -> tuple[Any, ...]:
    """Rank by conservative history and return, then current setup quality."""

    historical = item.get("historical_comparison")
    historical = historical if isinstance(historical, dict) else {}
    sample_size = historical.get("sample_size")
    conservative_ev = historical.get("conservative_expected_value_pct")
    expected_return = historical.get("expected_return_pct")
    profit_factor = historical.get("profit_factor")
    reward_risk = historical.get("reward_risk_ratio")
    win_interval = historical.get("out_of_sample_win_rate_interval_pct")
    win_floor = (
        float(win_interval[0])
        if isinstance(win_interval, list)
        and win_interval
        and _finite_number(win_interval[0])
        else -999.0
    )
    qualified = (
        isinstance(sample_size, int)
        and not isinstance(sample_size, bool)
        and sample_size >= 30
        and _finite_number(conservative_ev)
        and conservative_ev > 0
        and _finite_number(reward_risk)
        and reward_risk >= 2
        and _finite_number(profit_factor)
        and profit_factor > 1
    )
    return (
        1 if qualified else 0,
        float(conservative_ev) if _finite_number(conservative_ev) else -999.0,
        float(expected_return) if _finite_number(expected_return) else -999.0,
        win_floor,
        float(profit_factor) if _finite_number(profit_factor) else -999.0,
        float(historical.get("max_drawdown_pct"))
        if _finite_number(historical.get("max_drawdown_pct"))
        else -999.0,
        float(item.get(setup_score_field) or 0.0),
        str(item.get("symbol") or ""),
    )


def historical_path_comparison(
    price_rows: Iterable[dict[str, Any]],
    *,
    target_return_pct: float,
    stop_loss_pct: float,
    horizon_bars: int,
    friction_pct: float = 0.25,
    evidence_id: str,
) -> dict[str, Any]:
    """Evaluate non-overlapping target/stop paths on a frozen holdout."""

    rows = [
        row
        for row in price_rows
        if row.get("is_closed") is not False
        and _finite_number(row.get("close"))
        and _finite_number(row.get("high", row.get("close")))
        and _finite_number(row.get("low", row.get("close")))
        and float(row["close"]) > 0
    ]
    target_return_pct = float(target_return_pct)
    stop_loss_pct = abs(float(stop_loss_pct))
    horizon_bars = max(1, int(horizon_bars))
    if (
        len(rows) < horizon_bars * 4
        or target_return_pct <= 0
        or stop_loss_pct <= 0
    ):
        return {
            "status": "insufficient_history",
            "sample_size": 0,
            "out_of_sample_win_rate_interval_pct": None,
            "conservative_expected_value_pct": None,
            "expected_return_pct": None,
            "profit_factor": None,
            "max_drawdown_pct": None,
            "reward_risk_ratio": (
                round(target_return_pct / stop_loss_pct, 6)
                if stop_loss_pct > 0
                else None
            ),
            "walk_forward_positive_windows": 0,
            "walk_forward_total_windows": 0,
            "untouched_holdout": True,
            "lookahead_free": True,
            "simulation_evidence_label": "walk_forward_out_of_sample",
            "evidence_ids": [evidence_id],
        }

    holdout_start = max(horizon_bars, int(len(rows) * 0.40))
    outcomes: list[float] = []
    target_first_count = 0
    stop_first_count = 0
    unresolved_count = 0
    last_start = len(rows) - horizon_bars - 1
    for start in range(holdout_start, last_start + 1, horizon_bars):
        entry = float(rows[start]["close"])
        target_price = entry * (1.0 + target_return_pct / 100.0)
        stop_price = entry * (1.0 - stop_loss_pct / 100.0)
        result: float | None = None
        for future in rows[start + 1 : start + horizon_bars + 1]:
            high = float(future.get("high", future["close"]))
            low = float(future.get("low", future["close"]))
            target_hit = high >= target_price
            stop_hit = low <= stop_price
            if target_hit and stop_hit:
                result = -stop_loss_pct - friction_pct
                stop_first_count += 1
                break
            if stop_hit:
                result = -stop_loss_pct - friction_pct
                stop_first_count += 1
                break
            if target_hit:
                result = target_return_pct - friction_pct
                target_first_count += 1
                break
        if result is None:
            final_close = float(rows[start + horizon_bars]["close"])
            result = (final_close / entry - 1.0) * 100.0 - friction_pct
            unresolved_count += 1
        outcomes.append(result)

    sample_size = len(outcomes)
    if sample_size == 0:
        return historical_path_comparison(
            [],
            target_return_pct=target_return_pct,
            stop_loss_pct=stop_loss_pct,
            horizon_bars=horizon_bars,
            friction_pct=friction_pct,
            evidence_id=evidence_id,
        )
    win_rate = target_first_count / sample_size
    z = 1.959963984540054
    denominator = 1.0 + z * z / sample_size
    center = (win_rate + z * z / (2.0 * sample_size)) / denominator
    margin = (
        z
        * math.sqrt(
            win_rate * (1.0 - win_rate) / sample_size
            + z * z / (4.0 * sample_size * sample_size)
        )
        / denominator
    )
    mean_return = statistics.fmean(outcomes)
    sample_std = statistics.stdev(outcomes) if sample_size > 1 else 0.0
    conservative_ev = mean_return - 1.645 * sample_std / math.sqrt(sample_size)
    gross_wins = sum(item for item in outcomes if item > 0)
    gross_losses = -sum(item for item in outcomes if item < 0)
    profit_factor = (
        gross_wins / gross_losses
        if gross_losses > 0
        else (999.0 if gross_wins > 0 else 0.0)
    )
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for outcome in outcomes:
        equity *= max(0.000001, 1.0 + outcome / 100.0)
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity / peak - 1.0) * 100.0)
    window_count = min(5, sample_size)
    positive_windows = 0
    for index in range(window_count):
        start = index * sample_size // window_count
        end = (index + 1) * sample_size // window_count
        if end > start and statistics.fmean(outcomes[start:end]) > 0:
            positive_windows += 1
    return {
        "status": "complete",
        "sample_size": sample_size,
        "target_first_count": target_first_count,
        "stop_first_count": stop_first_count,
        "unresolved_count": unresolved_count,
        "historical_win_rate_pct": round(win_rate * 100.0, 4),
        "out_of_sample_win_rate_interval_pct": [
            round(max(0.0, center - margin) * 100.0, 4),
            round(min(1.0, center + margin) * 100.0, 4),
        ],
        "conservative_expected_value_pct": round(conservative_ev, 6),
        "expected_return_pct": round(mean_return, 6),
        "profit_factor": round(profit_factor, 6),
        "max_drawdown_pct": round(max_drawdown, 6),
        "reward_risk_ratio": round(target_return_pct / stop_loss_pct, 6),
        "walk_forward_positive_windows": positive_windows,
        "walk_forward_total_windows": window_count,
        "target_return_pct": target_return_pct,
        "stop_loss_pct": stop_loss_pct,
        "horizon_bars": horizon_bars,
        "friction_pct": friction_pct,
        "data_start": rows[0].get("ts"),
        "data_end": rows[-1].get("ts"),
        "holdout_start_index": holdout_start,
        "untouched_holdout": True,
        "lookahead_free": True,
        "non_overlapping_observations": True,
        "same_bar_stop_first": True,
        "simulation_evidence_label": "walk_forward_out_of_sample",
        "evidence_ids": [evidence_id],
    }


def run_fast_funnel(
    candidates: Iterable[DiscoveryCandidateV1 | dict[str, Any]],
    *,
    validate_candidate: Callable[[dict[str, Any]], dict[str, Any]],
    deep_research_candidate: Callable[[dict[str, Any]], dict[str, Any]],
    top_n: int = 3,
    on_discovery: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Enforce Top3 validation and Top1 deep-research call boundaries."""

    end_to_end_started = monotonic()
    discovery = rank_discovery_candidates(candidates, top_n=top_n)
    if on_discovery is not None:
        on_discovery(discovery)

    validation_started = monotonic()
    validated: list[dict[str, Any]] = []
    for candidate in discovery["top_candidates"]:
        result = dict(validate_candidate(candidate))
        result.setdefault("symbol", candidate["symbol"])
        result.setdefault("setup_quality_score", candidate["discovery_score"])
        validated.append(result)
    validated.sort(
        key=lambda item: (
            float(item.get("setup_quality_score") or 0),
            str(item.get("symbol") or ""),
        ),
        reverse=True,
    )
    validation_timing = phase_budget_status(validation_started, "validation")

    deep_research_started = monotonic()
    top1 = validated[0] if validated else None
    deep_research = deep_research_candidate(top1) if top1 else None
    deep_timing = phase_budget_status(deep_research_started, "deep_research")
    return {
        "schema_version": "fast-candidate-funnel-v1",
        "discovery": discovery,
        "validated_top3": validated,
        "ranked_historical_comparison": ranked_historical_comparison(validated),
        "top1": top1,
        "deep_research_top1": deep_research,
        "stage_timings": {
            "discovery": {
                "elapsed_seconds": discovery["elapsed_seconds"],
                "budget_seconds": discovery["budget_seconds"],
                "within_budget": discovery["within_budget"],
            },
            "validation": validation_timing,
            "deep_research": deep_timing,
            "end_to_end": phase_budget_status(end_to_end_started, "end_to_end"),
        },
        "validated_symbols": [item.get("symbol") for item in validated],
        "deep_research_symbols": [top1.get("symbol")] if top1 else [],
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def benchmark_fixture(*, iterations: int = 30, candidate_count: int = 30) -> dict[str, Any]:
    candidates = [
        DiscoveryCandidateV1(
            symbol=f"ASSET{index}USDT",
            asset_class="crypto",
            market_time="2026-08-01T00:00:00Z",
            current_price=1.0 + index,
            turnover=1_000_000 + index,
            relative_volume=1.0 + index / 10,
            trade_count=10_000 + index,
            vwap=1.0 + index,
            spread_bps=5.0,
            depth_bid_usd=100_000,
            depth_ask_usd=100_000,
            anchor_state={"market": "supportive", "BTC": 0.2, "ETH": 0.3},
            confirmed_catalyst_present=False,
            discovery_score=float(index % 100),
            ranking_reason=("deterministic_performance_fixture",),
            return_1m_pct=0.1,
            return_5m_pct=0.2,
            return_15m_pct=0.3,
        )
        for index in range(candidate_count)
    ]
    phase_durations = {
        "discovery": [],
        "validation": [],
        "deep_research": [],
        "end_to_end": [],
    }
    last_result: dict[str, Any] | None = None
    for _ in range(iterations):
        result = run_fast_funnel(
            candidates,
            validate_candidate=lambda item: {
                "symbol": item["symbol"],
                "setup_quality_score": item["discovery_score"],
                "validation_status": "complete",
                "historical_comparison": {
                    "sample_size": 60,
                    "out_of_sample_win_rate_interval_pct": [55.0, 68.0],
                    "conservative_expected_value_pct": 1.0,
                    "expected_return_pct": 3.0,
                    "profit_factor": 1.3,
                    "max_drawdown_pct": -8.0,
                    "reward_risk_ratio": 2.2,
                    "walk_forward_positive_windows": 4,
                    "walk_forward_total_windows": 5,
                    "evidence_ids": ["deterministic-history-fixture"],
                },
            },
            deep_research_candidate=lambda item: {
                "schema_version": "top1-decision-card-v1",
                "completion_status": "complete_with_explicit_blockers",
                "best_candidate": item["symbol"],
                "research_decision": "watch",
                "current_direct_decision": "do_not_enter_now",
                "account_state": {"status": "missing", "deployable_cash": None},
                "execution_decision": "no_deploy_evidence",
                "executable_amount": 0.0,
                "missing_evidence": ["deterministic_fixture_no_external_committee"],
                "live_orders_enabled": False,
            },
            top_n=3,
        )
        last_result = result
        for phase in phase_durations:
            phase_durations[phase].append(
                result["stage_timings"][phase]["elapsed_seconds"]
            )
        if len(result["discovery"]["top_candidates"]) != 3:
            raise AssertionError("benchmark_top3_missing")
        if len(result["validated_symbols"]) != 3:
            raise AssertionError("benchmark_validated_top3_missing")
        if len(result["deep_research_symbols"]) != 1:
            raise AssertionError("benchmark_deep_research_top1_missing")

    def p95(values: list[float]) -> float:
        ordered = sorted(values)
        index = max(
            0,
            min(len(ordered) - 1, int(len(ordered) * 0.95 + 0.9999) - 1),
        )
        return ordered[index]

    phase_p95 = {phase: round(p95(values), 6) for phase, values in phase_durations.items()}
    phase_budgets = {
        "discovery": PHASE_BUDGET_SECONDS["discovery_top3_p95"],
        "validation": PHASE_BUDGET_SECONDS["validated_top1"],
        "deep_research": PHASE_BUDGET_SECONDS["deep_research_top1"],
        "end_to_end": PHASE_BUDGET_SECONDS["end_to_end"],
    }
    top1_card = (last_result or {}).get("deep_research_top1") or {}
    top1_card_complete = (
        top1_card.get("completion_status") == "complete_with_explicit_blockers"
        and top1_card.get("current_direct_decision") == "do_not_enter_now"
        and top1_card.get("execution_decision") == "no_deploy_evidence"
        and top1_card.get("executable_amount") == 0.0
    )
    passed = top1_card_complete and all(
        phase_p95[phase] <= phase_budgets[phase]
        for phase in phase_p95
    )
    return {
        "schema_version": "fast-funnel-performance-benchmark-v1",
        "iterations": iterations,
        "candidate_count": candidate_count,
        "median_seconds": round(statistics.median(phase_durations["discovery"]), 6),
        "p95_seconds": phase_p95["discovery"],
        "budget_seconds": PHASE_BUDGET_SECONDS["discovery_top3_p95"],
        "phase_p95_seconds": phase_p95,
        "phase_budget_seconds": phase_budgets,
        "validated_top3_count": len((last_result or {}).get("validated_symbols") or []),
        "ranked_historical_comparison_count": len(
            ((last_result or {}).get("ranked_historical_comparison") or {}).get("rows") or []
        ),
        "deep_research_top1_count": len(
            (last_result or {}).get("deep_research_symbols") or []
        ),
        "top1_decision_card": top1_card,
        "top1_card_complete": top1_card_complete,
        "passed": passed,
        "live_orders_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--candidate-count", type=int, default=30)
    args = parser.parse_args()
    if not args.self_test:
        parser.error("--self-test is required")
    result = benchmark_fixture(
        iterations=max(1, args.iterations),
        candidate_count=max(3, args.candidate_count),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
