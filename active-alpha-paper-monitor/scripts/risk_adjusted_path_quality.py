#!/usr/bin/env python3
"""Deterministic, research-only risk-adjusted path quality calculations.

The module deliberately does not infer forecast probabilities or fair value
from Sharpe.  It measures return-path quality and provides a bounded,
paper-only ranking adjustment for already discovered candidates.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from typing import Any, Iterable


CALCULATION_VERSION = "risk-adjusted-path-v1"
PROMOTION_STATUS = "research_only_paper_only"
DAILY_WINDOW_MIN_OBSERVATIONS = {20: 18, 60: 50, 126: 100}
CRYPTO_4H_WINDOW_MIN_OBSERVATIONS = {42: 36, 126: 108}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 6) -> Any:
    number = _finite(value)
    return round(number, digits) if number is not None else value


def _timestamp(row: dict[str, Any]) -> int | None:
    for key in ("ts", "timestamp", "open_time", "close_time"):
        value = row.get(key)
        if value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        return number // 1000 if number > 10_000_000_000 else number
    return None


def _closed_price_rows(
    rows: Iterable[dict[str, Any]],
    *,
    cutoff_ts: int | None = None,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        if row.get("is_closed") is False:
            continue
        close = _finite(row.get("adjusted_close", row.get("close")))
        ts = _timestamp(row)
        if close is None or close <= 0 or ts is None:
            continue
        if cutoff_ts is not None and ts > cutoff_ts:
            continue
        cleaned.append({"ts": ts, "close": close})
    cleaned.sort(key=lambda item: item["ts"])
    deduped: dict[int, dict[str, Any]] = {item["ts"]: item for item in cleaned}
    return [deduped[key] for key in sorted(deduped)]


def _simple_returns(prices: list[float]) -> list[float]:
    return [
        prices[idx] / prices[idx - 1] - 1.0
        for idx in range(1, len(prices))
        if prices[idx - 1] > 0
    ]


def _period_risk_free(annual_pct: float, annualization: int) -> float:
    annual = annual_pct / 100.0
    if annual <= -1:
        return 0.0
    return (1.0 + annual) ** (1.0 / annualization) - 1.0


def _sample_std(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) >= 2 else None


def _max_drawdown_pct(prices: list[float]) -> float | None:
    if not prices:
        return None
    peak = prices[0]
    worst = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak > 0:
            worst = min(worst, price / peak - 1.0)
    return worst * 100.0


def _aligned_returns(
    asset_rows: list[dict[str, Any]],
    benchmark_rows: list[dict[str, Any]],
    window: int,
) -> tuple[list[float], list[float]]:
    asset_map = {row["ts"]: row["close"] for row in asset_rows}
    benchmark_map = {row["ts"]: row["close"] for row in benchmark_rows}
    common = sorted(set(asset_map).intersection(benchmark_map))
    if len(common) < 2:
        return [], []
    common = common[-(window + 1) :]
    return (
        _simple_returns([asset_map[ts] for ts in common]),
        _simple_returns([benchmark_map[ts] for ts in common]),
    )


def calculate_window_metrics(
    asset_rows: Iterable[dict[str, Any]],
    benchmark_rows: Iterable[dict[str, Any]],
    *,
    window: int,
    min_observations: int,
    annualization: int,
    risk_free_annual_pct: float,
    cutoff_ts: int | None = None,
) -> dict[str, Any]:
    asset = _closed_price_rows(asset_rows, cutoff_ts=cutoff_ts)
    benchmark = _closed_price_rows(benchmark_rows, cutoff_ts=cutoff_ts)
    recent = asset[-(window + 1) :]
    prices = [row["close"] for row in recent]
    returns = _simple_returns(prices)
    rf_period = _period_risk_free(risk_free_annual_pct, annualization)
    excess = [value - rf_period for value in returns]
    result: dict[str, Any] = {
        "requested_window_returns": window,
        "observations": len(returns),
        "min_observations": min_observations,
        "annualization_factor": annualization,
        "start_at": (
            datetime.fromtimestamp(recent[0]["ts"], tz=timezone.utc).isoformat()
            if recent
            else None
        ),
        "end_at": (
            datetime.fromtimestamp(recent[-1]["ts"], tz=timezone.utc).isoformat()
            if recent
            else None
        ),
        "sharpe": None,
        "sortino": None,
        "information_ratio": None,
        "benchmark_total_return_pct": None,
        "relative_strength_pct": None,
        "max_drawdown_pct": _round(_max_drawdown_pct(prices)),
        "total_return_pct": (
            _round((prices[-1] / prices[0] - 1.0) * 100.0)
            if len(prices) >= 2 and prices[0] > 0
            else None
        ),
        "status": "insufficient_sample",
    }
    if len(returns) < min_observations:
        return result

    volatility = _sample_std(excess)
    if volatility and volatility > 0:
        result["sharpe"] = _round(statistics.mean(excess) / volatility * math.sqrt(annualization))

    downside = [min(value, 0.0) for value in excess]
    downside_deviation = math.sqrt(sum(value * value for value in downside) / len(downside))
    if downside_deviation > 0:
        result["sortino"] = _round(statistics.mean(excess) / downside_deviation * math.sqrt(annualization))

    asset_aligned, benchmark_aligned = _aligned_returns(asset, benchmark, window)
    if len(asset_aligned) >= min_observations and len(asset_aligned) == len(benchmark_aligned):
        asset_compounded = math.prod(1.0 + value for value in asset_aligned) - 1.0
        benchmark_compounded = math.prod(1.0 + value for value in benchmark_aligned) - 1.0
        result["benchmark_total_return_pct"] = _round(benchmark_compounded * 100.0)
        result["relative_strength_pct"] = _round(
            (asset_compounded - benchmark_compounded) * 100.0
        )
        active_returns = [
            asset_value - benchmark_value
            for asset_value, benchmark_value in zip(asset_aligned, benchmark_aligned)
        ]
        tracking_error = _sample_std(active_returns)
        if tracking_error and tracking_error > 0:
            result["information_ratio"] = _round(
                statistics.mean(active_returns) / tracking_error * math.sqrt(annualization)
            )

    result["status"] = "ok" if result["sharpe"] is not None else "zero_or_invalid_volatility"
    return result


def _persistence_label(windows: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    daily20 = windows.get("daily_20") or {}
    daily60 = windows.get("daily_60") or {}
    sharpe20 = _finite(daily20.get("sharpe"))
    sharpe60 = _finite(daily60.get("sharpe"))
    ir60 = _finite(daily60.get("information_ratio"))
    flags: list[str] = []
    if sharpe20 is None or sharpe60 is None:
        return "insufficient_path_evidence", ["insufficient_sharpe_history"]
    if sharpe20 > 3 and sharpe60 < 1:
        return "short_spike_not_persistent", ["sharpe20_gt_3_without_60d_persistence"]
    if sharpe20 > 3 and sharpe60 >= 1.5 and (ir60 is not None and ir60 >= 1):
        return "persistent_strong", ["sharpe20_gt_3_confirmed_by_60d_and_relative_path"]
    if sharpe20 > 0 and sharpe60 > 0:
        return "positive_but_not_exceptional", flags
    if sharpe20 > sharpe60:
        return "improving_from_weak_base", ["recent_path_improving"]
    return "weak_or_deteriorating", ["risk_adjusted_path_weak"]


def calculate_risk_adjusted_path(
    asset_daily_rows: Iterable[dict[str, Any]],
    benchmark_daily_rows: Iterable[dict[str, Any]],
    *,
    asset_class: str,
    lane: str,
    benchmark: str,
    risk_free_annual_pct: float = 0.0,
    risk_free_evidence_id: str | None = None,
    evidence_ids: Iterable[str] = (),
    asset_4h_rows: Iterable[dict[str, Any]] | None = None,
    benchmark_4h_rows: Iterable[dict[str, Any]] | None = None,
    value_catalyst_gate_status: str = "not_applicable",
    cutoff_at: datetime | int | float | None = None,
) -> dict[str, Any]:
    if isinstance(cutoff_at, datetime):
        cutoff_ts = int(cutoff_at.astimezone(timezone.utc).timestamp())
    elif cutoff_at is None:
        cutoff_ts = None
    else:
        cutoff_ts = int(cutoff_at)
    annualization = 252 if asset_class == "us_equity" else 365
    windows: dict[str, dict[str, Any]] = {}
    daily_rows = list(asset_daily_rows)
    daily_benchmark = list(benchmark_daily_rows)
    four_hour_rows = list(asset_4h_rows) if asset_4h_rows is not None else []
    four_hour_benchmark = (
        list(benchmark_4h_rows)
        if benchmark_4h_rows is not None
        else []
    )
    for window in (20, 60, 126):
        windows[f"daily_{window}"] = calculate_window_metrics(
            daily_rows,
            daily_benchmark,
            window=window,
            min_observations=DAILY_WINDOW_MIN_OBSERVATIONS[window],
            annualization=annualization,
            risk_free_annual_pct=risk_free_annual_pct,
            cutoff_ts=cutoff_ts,
        )
    if asset_class == "crypto" and four_hour_rows and four_hour_benchmark:
        for window in (42, 126):
            windows[f"crypto_4h_{window}"] = calculate_window_metrics(
                four_hour_rows,
                four_hour_benchmark,
                window=window,
                min_observations=CRYPTO_4H_WINDOW_MIN_OBSERVATIONS[window],
                annualization=6 * 365,
                risk_free_annual_pct=risk_free_annual_pct,
                cutoff_ts=cutoff_ts,
            )

    persistence_label, flags = _persistence_label(windows)
    data_quality = "verified"
    if not risk_free_evidence_id:
        data_quality = "degraded_risk_free_fallback_zero"
        flags.append("risk_free_evidence_missing_used_zero")
    if any((windows[key].get("status") != "ok") for key in ("daily_20", "daily_60")):
        data_quality = "degraded_insufficient_or_invalid_path_data"
    if lane == "value_repair" and value_catalyst_gate_status != "pass":
        flags.append("value_repair_ranking_blocked_until_value_catalyst_gate_passes")
    if cutoff_ts is not None:
        future_daily_count = sum(
            1
            for row in daily_rows
            if (_timestamp(row) or 0) > cutoff_ts
        )
        closed_daily = _closed_price_rows(daily_rows, cutoff_ts=cutoff_ts)
        daily_staleness = (
            cutoff_ts - closed_daily[-1]["ts"]
            if closed_daily
            else None
        )
        daily_stale_limit = 4 * 86_400 if asset_class == "us_equity" else 2 * 86_400
        if future_daily_count:
            flags.append("future_daily_bars_excluded")
        if daily_staleness is None or daily_staleness > daily_stale_limit:
            data_quality = "stale_or_missing_closed_daily_data"
            flags.append("stale_closed_daily_data")
        if asset_class == "crypto" and four_hour_rows:
            future_4h_count = sum(
                1
                for row in four_hour_rows
                if (_timestamp(row) or 0) > cutoff_ts
            )
            closed_4h = _closed_price_rows(
                four_hour_rows,
                cutoff_ts=cutoff_ts,
            )
            four_hour_staleness = (
                cutoff_ts - closed_4h[-1]["ts"]
                if closed_4h
                else None
            )
            if future_4h_count:
                flags.append("future_crypto_4h_bars_excluded")
            if four_hour_staleness is None or four_hour_staleness > 8 * 3_600:
                data_quality = "stale_or_missing_closed_crypto_4h_data"
                flags.append("stale_closed_crypto_4h_data")

    result = {
        "lane": lane,
        "calculation_version": CALCULATION_VERSION,
        "promotion_status": PROMOTION_STATUS,
        "benchmark": benchmark,
        "risk_free_rate": {
            "annual_pct": _round(risk_free_annual_pct),
            "evidence_id": risk_free_evidence_id,
            "fallback_used": not bool(risk_free_evidence_id),
        },
        "windows": windows,
        "sharpe": {
            key: value.get("sharpe")
            for key, value in windows.items()
        },
        "sortino": {
            key: value.get("sortino")
            for key, value in windows.items()
        },
        "information_ratio": {
            key: value.get("information_ratio")
            for key, value in windows.items()
        },
        "max_drawdown": {
            key: value.get("max_drawdown_pct")
            for key, value in windows.items()
        },
        "quality_score": None,
        "ranking_adjustment_points": 0.0,
        "persistence_label": persistence_label,
        "flags": sorted(set(flags)),
        "data_quality": data_quality,
        "cutoff_at": (
            datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat()
            if cutoff_ts is not None
            else None
        ),
        "value_catalyst_gate_status": value_catalyst_gate_status,
        "evidence_ids": sorted(set(str(item) for item in evidence_ids if item)),
        "probability_mapping_forbidden": True,
        "fair_value_mapping_forbidden": True,
        "live_gate_effect": "none_until_promotion",
    }
    result["dca_pacing_if_longterm_thesis_passes"] = dca_pacing_from_path(result)
    return result


def _metric(path: dict[str, Any], window: str, field: str) -> float | None:
    return _finite(((path.get("windows") or {}).get(window) or {}).get(field))


def _percentile_map(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1])
    denominator = max(1, len(ordered) - 1)
    result: dict[int, float] = {}
    position = 0
    while position < len(ordered):
        end = position
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[position][1]:
            end += 1
        percentile = ((position + end) / 2.0) / denominator * 100.0
        for idx in range(position, end + 1):
            result[ordered[idx][0]] = percentile
        position = end + 1
    return result


def _score_lane(paths: list[dict[str, Any]], lane: str) -> dict[int, float]:
    if lane == "trend_continuation":
        specifications = [
            ("daily_20", "sharpe", 0.25, False),
            ("daily_60", "sharpe", 0.25, False),
            ("daily_60", "relative_strength_pct", 0.20, False),
            ("daily_60", "sortino", 0.15, False),
            ("daily_60", "max_drawdown_pct", 0.15, False),
        ]
    else:
        specifications = [
            ("delta_sharpe", "value", 0.30, False),
            ("delta_ir", "value", 0.25, False),
            ("daily_20", "sortino", 0.20, False),
            ("daily_20", "max_drawdown_pct", 0.15, False),
            ("daily_20", "sharpe", 0.10, False),
        ]

    scores = {idx: 0.0 for idx in range(len(paths))}
    weights = {idx: 0.0 for idx in range(len(paths))}
    for window, field, weight, _ in specifications:
        values: dict[int, float] = {}
        for idx, path in enumerate(paths):
            if window == "delta_sharpe":
                recent = _metric(path, "daily_20", "sharpe")
                baseline = _metric(path, "daily_60", "sharpe")
                value = recent - baseline if recent is not None and baseline is not None else None
            elif window == "delta_ir":
                recent = _metric(path, "daily_20", "information_ratio")
                baseline = _metric(path, "daily_60", "information_ratio")
                value = recent - baseline if recent is not None and baseline is not None else None
            else:
                value = _metric(path, window, field)
            if value is not None:
                values[idx] = value
        percentiles = _percentile_map(values)
        for idx, percentile in percentiles.items():
            scores[idx] += percentile * weight
            weights[idx] += weight
    return {
        idx: (scores[idx] / weights[idx] if weights[idx] > 0 else 50.0)
        for idx in scores
    }


def apply_cross_sectional_adjustments(
    candidates: list[dict[str, Any]],
    *,
    base_score_field: str,
) -> list[dict[str, Any]]:
    """Attach bounded research-ranking adjustments in place and return candidates."""

    for lane in ("trend_continuation", "value_repair"):
        indexes = [
            idx
            for idx, candidate in enumerate(candidates)
            if (candidate.get("risk_adjusted_path") or {}).get("lane") == lane
        ]
        paths = [candidates[idx]["risk_adjusted_path"] for idx in indexes]
        if len(paths) < 3:
            for idx in indexes:
                path = candidates[idx]["risk_adjusted_path"]
                path["quality_score"] = 50.0
                path["ranking_adjustment_points"] = 0.0
                path["flags"] = sorted(set((path.get("flags") or []) + ["cross_section_cohort_lt_3"]))
                path["cohort_size"] = len(paths)
            continue
        lane_scores = _score_lane(paths, lane)
        for local_idx, candidate_idx in enumerate(indexes):
            candidate = candidates[candidate_idx]
            path = candidate["risk_adjusted_path"]
            quality_score = max(0.0, min(100.0, lane_scores[local_idx]))
            if path.get("persistence_label") == "short_spike_not_persistent":
                quality_score = min(quality_score, 60.0)
            multiplier = 0.15 if lane == "trend_continuation" else 0.08
            adjustment = (quality_score - 50.0) * multiplier
            if lane == "value_repair" and path.get("value_catalyst_gate_status") != "pass":
                adjustment = 0.0
            base_score = _finite(candidate.get(base_score_field)) or 0.0
            path["quality_score"] = _round(quality_score, 2)
            path["ranking_adjustment_points"] = _round(adjustment, 2)
            path["cohort_size"] = len(paths)
            path["base_score_points"] = _round(base_score, 2)
            path["adjusted_score_points"] = _round(base_score + adjustment, 2)
            candidate[f"{base_score_field}_before_risk_adjustment"] = _round(base_score, 2)
            candidate[base_score_field] = _round(base_score + adjustment, 2)
    return candidates


def dca_pacing_from_path(path: dict[str, Any]) -> dict[str, str]:
    label = path.get("persistence_label")
    sharpe20 = _metric(path, "daily_20", "sharpe")
    sharpe60 = _metric(path, "daily_60", "sharpe")
    if label == "short_spike_not_persistent":
        return {
            "pace": "delay_or_smaller_tranche",
            "reason": "20日路径过热但60日持续性不足；不改变长期价值，只降低当前批次追涨风险。",
        }
    if sharpe20 is not None and sharpe60 is not None and sharpe20 > sharpe60 and sharpe20 > 0:
        return {
            "pace": "normal_tranche_if_longterm_thesis_passes",
            "reason": "近期风险调整路径改善；仍需长期基本面和组合适配先通过。",
        }
    return {
        "pace": "split_more_or_wait_for_stabilization",
        "reason": "路径质量偏弱；不能把负Sharpe本身解释成越跌越买。",
    }


def run_self_test() -> dict[str, Any]:
    start = 1_700_000_000
    rising = [
        {"ts": start + idx * 86_400, "close": 100.0 * (1.004 ** idx), "is_closed": True}
        for idx in range(140)
    ]
    benchmark = [
        {"ts": start + idx * 86_400, "close": 100.0 * (1.001 ** idx), "is_closed": True}
        for idx in range(140)
    ]
    open_bar = {"ts": start + 141 * 86_400, "close": 10_000.0, "is_closed": False}
    path = calculate_risk_adjusted_path(
        rising + [open_bar],
        benchmark + [open_bar],
        asset_class="us_equity",
        lane="trend_continuation",
        benchmark="SPY",
        risk_free_annual_pct=4.0,
        risk_free_evidence_id="fixture-rf",
        evidence_ids=("fixture-price",),
    )
    flat = [{"ts": start + idx * 86_400, "close": 100.0, "is_closed": True} for idx in range(140)]
    flat_window = calculate_window_metrics(
        flat,
        benchmark,
        window=60,
        min_observations=50,
        annualization=252,
        risk_free_annual_pct=0.0,
    )
    reversal_candidates = []
    for idx, recent_growth in enumerate((0.99, 1.001, 1.008)):
        prices = [
            {"ts": start + day * 86_400, "close": 160 - day * 0.4, "is_closed": True}
            for day in range(80)
        ]
        for day in range(80, 140):
            prices.append(
                {
                    "ts": start + day * 86_400,
                    "close": prices[-1]["close"] * recent_growth,
                    "is_closed": True,
                }
            )
        reversal_candidates.append(
            {
                "score": 50.0,
                "risk_adjusted_path": calculate_risk_adjusted_path(
                    prices,
                    benchmark,
                    asset_class="us_equity",
                    lane="value_repair",
                    benchmark="SPY",
                    risk_free_evidence_id="fixture-rf",
                    value_catalyst_gate_status="pass",
                ),
            }
        )
    apply_cross_sectional_adjustments(reversal_candidates, base_score_field="score")
    checks = {
        "closed_bars_only": path["windows"]["daily_20"]["end_at"]
        != datetime.fromtimestamp(open_bar["ts"], tz=timezone.utc).isoformat(),
        "daily_windows_present": all(
            key in path["windows"] for key in ("daily_20", "daily_60", "daily_126")
        ),
        "flat_zero_volatility_not_infinite": flat_window["sharpe"] is None,
        "risk_free_provenance_present": path["risk_free_rate"]["evidence_id"] == "fixture-rf",
        "reversal_negative_or_weak_path_not_deleted": len(reversal_candidates) == 3,
        "reversal_adjustment_bounded": all(
            abs(item["risk_adjusted_path"]["ranking_adjustment_points"]) <= 4.0
            for item in reversal_candidates
        ),
        "probability_mapping_forbidden": path["probability_mapping_forbidden"] is True,
        "live_gate_unchanged": path["live_gate_effect"] == "none_until_promotion",
    }
    return {
        "status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "calculation_version": CALCULATION_VERSION,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Risk-adjusted path quality utilities.")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if not args.self_test:
        parser.error("--self-test is required for the standalone entry point")
    result = run_self_test()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
