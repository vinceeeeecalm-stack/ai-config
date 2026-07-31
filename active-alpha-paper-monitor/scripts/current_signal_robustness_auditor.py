#!/usr/bin/env python3
"""Audit a current-signal candidate for parameter and friction robustness.

Research-only. Uses durable public Kline cache, never mutates the paper ledger,
and never treats a small holdout win rate as a calibrated forecast probability.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any

from current_signal_probe import closed_fresh_frame, expand_cache_dirs, promotion_review, segment_summary
from validation_sample_auditor import build_audit
from weekly_goal_strategy_lab import Strategy, backtest, load_cached_frames, param_grid, signal_array


ROOT = Path(__file__).resolve().parent.parent
EXPERIMENT = ROOT / "experiments" / "current-signal-robustness-audit.json"
REPORT = ROOT / "reports" / "CURRENT_SIGNAL_ROBUSTNESS_AUDIT.md"
FRICTION_STRESS_PCT = (0.46, 0.8, 1.2)
PARAMETER_FIELDS = (
    "lookback", "hold_bars", "stop_pct", "take_pct", "volume_mult",
    "threshold_pct", "ma_fast", "ma_slow", "rsi_max", "drawdown_pct", "squeeze_ratio",
)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def latest_probe() -> Path | None:
    paths = list((ROOT / "experiments").glob("*current-signal-probe.json"))
    return max(paths, key=lambda item: item.stat().st_mtime_ns) if paths else None


def hamming_distance(reference: Strategy, variant: Strategy) -> int:
    return sum(getattr(reference, field) != getattr(variant, field) for field in PARAMETER_FIELDS)


def wilson_interval(wins: int, count: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if count <= 0:
        return 0.0, 1.0
    p = wins / count
    denominator = 1.0 + z * z / count
    center = (p + z * z / (2.0 * count)) / denominator
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * count)) / count) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def stressed_summary(trades: list[dict[str, Any]], friction_pct: float, initial: float = 500.0) -> dict[str, Any]:
    equity = initial
    peak = initial
    max_drawdown = 0.0
    net_returns = []
    for trade in trades:
        gross = float(trade.get("gross_pct") or 0.0)
        net = gross - friction_pct
        net_returns.append(net)
        equity += equity * 0.25 * net / 100.0
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity / peak - 1.0) * 100.0)
    return {
        "friction_pct": friction_pct,
        "trade_count": len(net_returns),
        "win_rate_pct": round(sum(value > 0 for value in net_returns) / len(net_returns) * 100.0, 4) if net_returns else 0.0,
        "net_return_pct": round((equity / initial - 1.0) * 100.0, 6),
        "max_drawdown_pct": round(max_drawdown, 6),
    }


def bootstrap_positive_probability(trades: list[dict[str, Any]], iterations: int = 10000) -> float | None:
    values = [float(item.get("net_pct") or 0.0) for item in trades]
    if not values:
        return None
    rng = random.Random(20260711)
    positives = 0
    for _ in range(iterations):
        sample = [rng.choice(values) for _ in values]
        positives += sum(sample) > 0
    return round(positives / iterations * 100.0, 4)


def choose_candidate(payload: dict[str, Any], symbol: str | None, interval: str | None, candidate_rank: int = 0) -> dict[str, Any] | None:
    candidates = payload.get("top_candidates") if isinstance(payload.get("top_candidates"), list) else []
    filtered = [
        item for item in candidates
        if isinstance(item, dict)
        and item.get("current_signal") is True
        and not item.get("promotion_blockers")
        and (not symbol or str(item.get("symbol") or "").upper() == symbol.upper())
        and (not interval or item.get("interval") == interval)
    ]
    return filtered[candidate_rank] if 0 <= candidate_rank < len(filtered) else None


def choose_sampler_candidate(payload: dict[str, Any], symbol: str | None, interval: str | None) -> dict[str, Any] | None:
    audit = build_audit(root=ROOT.parent)
    metrics = audit.get("current_signal_probe_metrics") if isinstance(audit.get("current_signal_probe_metrics"), dict) else {}
    ordered = metrics.get("top_current_signals") if isinstance(metrics.get("top_current_signals"), list) else []
    probe_candidates = payload.get("top_candidates") if isinstance(payload.get("top_candidates"), list) else []
    for adapted in ordered:
        if not isinstance(adapted, dict) or adapted.get("current_signal") is not True or adapted.get("promotion_blockers"):
            continue
        if symbol and str(adapted.get("symbol") or "").upper() != symbol.upper():
            continue
        if interval and adapted.get("interval") != interval:
            continue
        for raw in probe_candidates:
            if not isinstance(raw, dict):
                continue
            if (
                raw.get("symbol") == adapted.get("symbol")
                and raw.get("interval") == adapted.get("interval")
                and raw.get("strategy") == adapted.get("strategy")
            ):
                return raw
    return None


def render(payload: dict[str, Any]) -> str:
    ref = payload.get("reference_candidate") or {}
    holdout = payload.get("holdout_uncertainty") or {}
    neighborhood = payload.get("parameter_neighborhood") or {}
    lines = [
        "# Current Signal Robustness Audit",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- status: `{payload.get('status')}`",
        f"- candidate: `{ref.get('symbol')}` `{ref.get('interval')}` `{ref.get('family')}`",
        f"- paper_retest_gate: `{payload.get('paper_retest_gate')}`",
        f"- ledger_mutated: `{payload.get('ledger_mutated')}`",
        "",
        "## Parameter Neighborhood",
        "",
        f"- evaluated: `{neighborhood.get('evaluated')}`",
        f"- current_signal_neighbors: `{neighborhood.get('current_signal_neighbors')}`",
        f"- all_segments_positive: `{neighborhood.get('all_segments_positive')}`",
        f"- no_blocker_neighbors: `{neighborhood.get('no_blocker_neighbors')}`",
        f"- no_blocker_share_pct: `{neighborhood.get('no_blocker_share_pct')}`",
        "",
        "## Holdout Uncertainty",
        "",
        f"- trades: `{holdout.get('trade_count')}` wins `{holdout.get('wins')}`",
        f"- observed_win_rate_pct: `{holdout.get('observed_win_rate_pct')}`",
        f"- wilson_95pct_interval: `{holdout.get('wilson_95pct_lower_pct')}%` to `{holdout.get('wilson_95pct_upper_pct')}%`",
        f"- bootstrap_positive_net_probability_pct: `{holdout.get('bootstrap_positive_net_probability_pct')}`",
        "- probability_note: this is historical resampling uncertainty, not a calibrated forward forecast probability.",
        "",
        "## Friction Stress",
        "",
        "| Friction | Train net | Validation net | Holdout net |",
        "|---:|---:|---:|---:|",
    ]
    for row in payload.get("friction_stress") or []:
        lines.append(
            f"| `{row.get('friction_pct')}%` | `{(row.get('train') or {}).get('net_return_pct')}%` | "
            f"`{(row.get('validation') or {}).get('net_return_pct')}%` | `{(row.get('holdout') or {}).get('net_return_pct')}%` |"
        )
    lines.extend(["", "## Decision", "", f"- {payload.get('decision_note')}", ""])
    return "\n".join(lines)


def audit(args: argparse.Namespace) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    probe_path = Path(args.probe) if args.probe else latest_probe()
    probe = read_json(probe_path, {}) if probe_path else {}
    candidate = (
        choose_sampler_candidate(probe if isinstance(probe, dict) else {}, args.symbol, args.interval)
        if args.candidate_rank < 0
        else choose_candidate(probe if isinstance(probe, dict) else {}, args.symbol, args.interval, args.candidate_rank)
    )
    base = {
        "run_id": f"{now.strftime('%Y%m%d-%H%M%S')}-current-signal-robustness-audit",
        "created_at": now.isoformat(),
        "source_probe": str(probe_path) if probe_path else None,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }
    if not candidate:
        return {**base, "status": "no_no_blocker_current_candidate", "paper_retest_gate": "block", "decision_note": "No fresh no-blocker current candidate is available."}

    symbol = str(candidate.get("symbol"))
    interval = str(candidate.get("interval"))
    reference = Strategy(**candidate["strategy"])
    cache_dirs = expand_cache_dirs([item for item in args.cache_dirs.split(",") if item])
    frames = load_cached_frames(cache_dirs, symbols={symbol, "BTCUSDT"}, intervals={interval})
    df, freshness = closed_fresh_frame(frames.get((symbol, interval)), interval, now) if (symbol, interval) in frames else (None, {"status": "missing"})
    if df is None or freshness.get("status") != "fresh":
        return {**base, "status": "candidate_frame_not_fresh", "paper_retest_gate": "block", "frame_freshness": freshness, "decision_note": "Candidate cache is missing or stale."}
    btc_df = frames.get(("BTCUSDT", interval))
    if btc_df is not None:
        btc_df, _ = closed_fresh_frame(btc_df, interval, now)
    train_end = int(len(df) * 0.50)
    validation_end = int(len(df) * 0.75)

    variants = [item for item in param_grid(interval) if item.family == reference.family and hamming_distance(reference, item) <= args.max_distance]
    rows = []
    reference_results = None
    for variant in variants:
        current = bool(signal_array(df, variant, btc_df=btc_df)[len(df) - 1])
        train = backtest(df, variant, 0, train_end, btc_df=btc_df, initial=args.initial)
        validation = backtest(df, variant, train_end, validation_end, btc_df=btc_df, initial=args.initial)
        holdout = backtest(df, variant, validation_end, len(df), btc_df=btc_df, initial=args.initial)
        review = promotion_review({
            "validation_protocol": "train_50_validation_25_final_holdout_25",
            "holdout_used_for_selection": False,
            "train_summary": segment_summary(train),
            "validation_summary": segment_summary(validation),
            "oos_summary": segment_summary(holdout),
        }, args.initial)
        row = {
            "strategy": variant.__dict__,
            "distance": hamming_distance(reference, variant),
            "current_signal": current,
            "train": segment_summary(train),
            "validation": segment_summary(validation),
            "holdout": segment_summary(holdout),
            "promotion_blockers": review.get("promotion_blockers") or [],
        }
        rows.append(row)
        if variant == reference:
            reference_results = {"train": train, "validation": validation, "holdout": holdout}

    current_neighbors = [row for row in rows if row["current_signal"]]
    reference_row = next((row for row in rows if row["strategy"] == reference.__dict__), None)
    reference_current_signal_active = bool(reference_row and reference_row.get("current_signal"))
    all_positive = [row for row in current_neighbors if all(float((row[key] or {}).get("net_return_pct") or 0.0) > 0 for key in ("train", "validation", "holdout"))]
    no_blocker = [row for row in current_neighbors if not row["promotion_blockers"]]
    if reference_results is None:
        raise RuntimeError("reference strategy missing from parameter grid")
    holdout_trades = reference_results["holdout"].get("trades") or []
    wins = sum(float(item.get("net_pct") or 0.0) > 0 for item in holdout_trades)
    lower, upper = wilson_interval(wins, len(holdout_trades))
    stress = []
    for friction in FRICTION_STRESS_PCT:
        stress.append({
            "friction_pct": friction,
            "train": stressed_summary(reference_results["train"].get("trades") or [], friction, args.initial),
            "validation": stressed_summary(reference_results["validation"].get("trades") or [], friction, args.initial),
            "holdout": stressed_summary(holdout_trades, friction, args.initial),
        })
    neighborhood_share = len(no_blocker) / len(current_neighbors) * 100.0 if current_neighbors else 0.0
    friction_pass = all(all(float((row.get(segment) or {}).get("net_return_pct") or 0.0) > 0 for segment in ("train", "validation", "holdout")) for row in stress)
    neighborhood_pass = (
        len(current_neighbors) >= args.min_current_neighbors
        and len(no_blocker) >= args.min_no_blocker_neighbors
        and neighborhood_share >= args.min_no_blocker_share_pct
    )
    holding_samples = [max(1, int(item.get("holding_bars") or 1)) for item in holdout_trades]
    median_holding_bars = float(statistics.median(holding_samples)) if holding_samples else float(reference.hold_bars)
    holdout_window_bars = max(1, len(df) - validation_end)
    estimated_independent_opportunities = holdout_window_bars / max(1.0, median_holding_bars)
    adaptive_min_holdout_trades = min(
        args.min_holdout_trades,
        max(args.absolute_min_holdout_trades, int(math.floor(estimated_independent_opportunities * args.opportunity_coverage_ratio))),
    )
    sample_pass = len(holdout_trades) >= adaptive_min_holdout_trades and lower * 100.0 >= args.min_wilson_lower_pct
    gate = "allow_minimum_paper_retest" if reference_current_signal_active and friction_pass and neighborhood_pass and sample_pass else "dry_run_only"
    failed = []
    if not reference_current_signal_active: failed.append("reference_current_signal_not_active")
    if not friction_pass: failed.append("friction_stress_not_positive_in_all_segments")
    if not neighborhood_pass: failed.append("parameter_neighborhood_not_stable")
    if not sample_pass: failed.append("holdout_uncertainty_too_wide")
    return {
        **base,
        "status": "pass_minimum_paper_retest" if gate.startswith("allow") else "insufficient_robustness",
        "paper_retest_gate": gate,
        "reference_candidate": {"symbol": symbol, "interval": interval, "family": reference.family, "strategy": reference.__dict__},
        "reference_current_signal_active": reference_current_signal_active,
        "frame_freshness": freshness,
        "parameter_neighborhood": {
            "max_hamming_distance": args.max_distance,
            "evaluated": len(rows),
            "current_signal_neighbors": len(current_neighbors),
            "all_segments_positive": len(all_positive),
            "no_blocker_neighbors": len(no_blocker),
            "no_blocker_share_pct": round(neighborhood_share, 4),
            "minimum_current_neighbors": args.min_current_neighbors,
            "minimum_no_blocker_neighbors": args.min_no_blocker_neighbors,
            "minimum_no_blocker_share_pct": args.min_no_blocker_share_pct,
            "top_rows": sorted(current_neighbors, key=lambda row: (not row["promotion_blockers"], row["holdout"].get("net_return_pct") or -999), reverse=True)[:20],
        },
        "holdout_uncertainty": {
            "trade_count": len(holdout_trades),
            "wins": wins,
            "observed_win_rate_pct": round(wins / len(holdout_trades) * 100.0, 4) if holdout_trades else 0.0,
            "wilson_95pct_lower_pct": round(lower * 100.0, 4),
            "wilson_95pct_upper_pct": round(upper * 100.0, 4),
            "bootstrap_positive_net_probability_pct": bootstrap_positive_probability(holdout_trades),
            "minimum_holdout_trades": args.min_holdout_trades,
            "absolute_min_holdout_trades": args.absolute_min_holdout_trades,
            "adaptive_min_holdout_trades": adaptive_min_holdout_trades,
            "holdout_window_bars": holdout_window_bars,
            "median_holding_bars": round(median_holding_bars, 4),
            "estimated_independent_opportunities": round(estimated_independent_opportunities, 4),
            "opportunity_coverage_ratio": args.opportunity_coverage_ratio,
            "minimum_wilson_lower_pct": args.min_wilson_lower_pct,
        },
        "friction_stress": stress,
        "failed_gates": failed,
        "decision_note": "Only a minimum paper retest is allowed; this is not live-trade permission." if not failed else "Keep the candidate as a persisted dry-run until the failed robustness gates are resolved.",
    }


def self_test() -> dict[str, Any]:
    lo, hi = wilson_interval(5, 7)
    stressed = stressed_summary([{"gross_pct": 10.0}, {"gross_pct": -5.0}], 1.0)
    assert 0 < lo < 5 / 7 < hi < 1
    assert stressed["net_return_pct"] > 0
    return {"status": "ok", "wilson_interval_verified": True, "friction_stress_verified": True, "live_orders_enabled": False, "ledger_mutated": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit current-signal parameter and friction robustness.")
    parser.add_argument("--probe", default="")
    parser.add_argument("--cache-dirs", default=str(ROOT / "cache" / "binance_klines" / "dynamic_20260711"))
    parser.add_argument("--symbol", default="")
    parser.add_argument("--interval", default="")
    parser.add_argument("--candidate-rank", type=int, default=-1, help="-1 follows sampler order; otherwise zero-based rank after filtering.")
    parser.add_argument("--initial", type=float, default=500.0)
    parser.add_argument("--max-distance", type=int, default=2)
    parser.add_argument("--min-current-neighbors", type=int, default=5)
    parser.add_argument("--min-no-blocker-neighbors", type=int, default=8)
    parser.add_argument("--min-no-blocker-share-pct", type=float, default=15.0)
    parser.add_argument("--min-holdout-trades", type=int, default=20)
    parser.add_argument("--absolute-min-holdout-trades", type=int, default=6)
    parser.add_argument("--opportunity-coverage-ratio", type=float, default=0.4)
    parser.add_argument("--min-wilson-lower-pct", type=float, default=45.0)
    parser.add_argument("--output", default=str(EXPERIMENT))
    parser.add_argument("--report", default=str(REPORT))
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    payload = audit(args)
    output = Path(args.output)
    report = Path(args.report)
    if not args.dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report.write_text(render(payload), encoding="utf-8")
    summary = {
        "status": payload.get("status"),
        "paper_retest_gate": payload.get("paper_retest_gate"),
        "candidate": payload.get("reference_candidate"),
        "failed_gates": payload.get("failed_gates"),
        "outputs": {"experiment": str(output), "report": str(report)},
        "live_orders_enabled": False,
        "ledger_mutated": False,
    }
    print(json.dumps(summary if args.compact_output else payload, ensure_ascii=False, indent=None if args.compact_output else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
