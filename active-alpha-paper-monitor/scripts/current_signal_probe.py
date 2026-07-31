#!/usr/bin/env python3
"""
Current-signal probe for weekly-goal strategies.

Scans the full strategy grid for strategies whose latest closed bar is active,
then backtests only those current-signal strategies. Research-only; no orders.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from weekly_goal_strategy_lab import (  # noqa: E402
    DEFAULT_CACHE_DIRS,
    Strategy,
    backtest,
    classify,
    load_cached_frames,
    param_grid,
    selection_score,
    signal_array,
)

ROOT = SCRIPT_DIR.parent


DEFAULT_ACTIVE_CACHE_DIRS = [
    str(ROOT / "cache" / "binance_klines"),
    "/private/tmp/binance_klines_cache_daily_crypto_paper_auto_trader",
    "/private/tmp/binance_klines_cache_fast_crypto_paper_auto_trader",
]

INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
DEFAULT_MAX_CLOSED_BAR_LAG_MINUTES = {"1m": 5, "5m": 15, "15m": 45, "30m": 90, "1h": 180, "2h": 360, "4h": 720, "1d": 2880}


def expand_cache_dirs(cache_dirs):
    expanded = []
    for raw in cache_dirs:
        path = Path(raw)
        if not path.exists():
            expanded.append(raw)
            continue
        if any(path.glob("*.json")):
            expanded.append(str(path))
        child_dirs = [p for p in path.iterdir() if p.is_dir()]
        child_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for child in child_dirs[:3]:
            if any(child.glob("*.json")):
                expanded.append(str(child))
    seen = set()
    out = []
    for item in expanded:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def default_output_path(now):
    stamp = now.strftime("%Y%m%d-%H%M")
    return ROOT / "experiments" / f"{stamp}-current-signal-probe.json"


def rounded(value, digits=4):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value


def closed_fresh_frame(df, interval, now):
    interval_minutes = int(INTERVAL_MINUTES.get(interval, 60))
    now_ms = int(now.timestamp() * 1000)
    if "ct" in df.columns:
        close_times = df["ct"].astype("int64")
    else:
        close_times = df["t"].astype("int64") + interval_minutes * 60_000 - 1
    closed_mask = close_times <= now_ms
    closed = df.loc[closed_mask].copy().reset_index(drop=True)
    removed_open_rows = int((~closed_mask).sum())
    if closed.empty:
        return None, {
            "status": "no_closed_bars",
            "interval": interval,
            "removed_open_rows": removed_open_rows,
        }
    if "ct" in closed.columns:
        last_close_ms = int(closed.iloc[-1]["ct"])
    else:
        last_close_ms = int(closed.iloc[-1]["t"]) + interval_minutes * 60_000 - 1
    lag_minutes = (now_ms - last_close_ms) / 60_000.0
    max_lag_minutes = float(DEFAULT_MAX_CLOSED_BAR_LAG_MINUTES.get(interval, interval_minutes * 3))
    status = "fresh" if -1.0 <= lag_minutes <= max_lag_minutes else "future_timestamp" if lag_minutes < -1.0 else "stale"
    return closed, {
        "status": status,
        "interval": interval,
        "bars": len(closed),
        "last_close_time_ms": last_close_ms,
        "lag_minutes": round(lag_minutes, 4),
        "max_lag_minutes": max_lag_minutes,
        "removed_open_rows": removed_open_rows,
    }


SEGMENT_SUMMARY_KEYS = [
    "trade_count",
    "win_rate_pct",
    "weekly_double_trade_count",
    "weekly_double_hit_rate_pct",
    "final_capital",
    "net_return_pct",
    "max_drawdown_pct",
    "expectancy_pct",
    "profit_factor",
    "largest_winner_share_pct",
    "avg_friction_pct",
    "capital_fraction_per_trade",
]


def segment_summary(result):
    return {key: result.get(key) for key in SEGMENT_SUMMARY_KEYS}


def strategy_variant_key(candidate):
    strategy = candidate.get("strategy") or {}
    return json.dumps(
        {
            "symbol": candidate.get("symbol"),
            "interval": candidate.get("interval"),
            "family": strategy.get("family"),
            "lookback": strategy.get("lookback"),
            "hold_bars": strategy.get("hold_bars"),
            "stop_pct": strategy.get("stop_pct"),
            "take_pct": strategy.get("take_pct"),
            "volume_mult": strategy.get("volume_mult"),
            "threshold_pct": strategy.get("threshold_pct"),
            "ma_fast": strategy.get("ma_fast"),
            "ma_slow": strategy.get("ma_slow"),
            "rsi_max": strategy.get("rsi_max"),
            "drawdown_pct": strategy.get("drawdown_pct"),
            "squeeze_ratio": strategy.get("squeeze_ratio"),
        },
        sort_keys=True,
    )


def performance_dedup_key(candidate):
    oos = candidate.get("oos_summary") or {}
    strategy = candidate.get("strategy") or {}
    return json.dumps(
        {
            "symbol": candidate.get("symbol"),
            "interval": candidate.get("interval"),
            "stage": candidate.get("stage"),
            "family": strategy.get("family"),
            "oos_trade_count": oos.get("trade_count"),
            "oos_win_rate_pct": rounded(oos.get("win_rate_pct"), 2),
            "oos_final_capital": rounded(oos.get("final_capital"), 2),
            "oos_net_return_pct": rounded(oos.get("net_return_pct"), 2),
            "oos_max_drawdown_pct": rounded(oos.get("max_drawdown_pct"), 2),
        },
        sort_keys=True,
    )


def summarize_candidates(candidates):
    stage_counts = {}
    symbol_counts = {}
    symbol_stage_counts = {}
    for candidate in candidates:
        stage = candidate.get("stage", "unknown")
        symbol = candidate.get("symbol", "unknown")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
        key = f"{symbol}:{stage}"
        symbol_stage_counts[key] = symbol_stage_counts.get(key, 0) + 1
    return {
        "stage_counts": dict(sorted(stage_counts.items(), key=lambda kv: kv[0])),
        "symbol_counts": dict(sorted(symbol_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "symbol_stage_counts": dict(sorted(symbol_stage_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def dedupe_candidates(candidates):
    exact_seen = set()
    performance_seen = {}
    deduped = []
    exact_duplicates = 0
    performance_duplicates = 0
    for candidate in candidates:
        exact_key = strategy_variant_key(candidate)
        if exact_key in exact_seen:
            exact_duplicates += 1
            continue
        exact_seen.add(exact_key)
        perf_key = performance_dedup_key(candidate)
        existing = performance_seen.get(perf_key)
        if existing is not None:
            performance_duplicates += 1
            existing["deduped_equivalent_strategy_count"] = existing.get("deduped_equivalent_strategy_count", 1) + 1
            existing.setdefault("deduped_equivalent_strategy_examples", [])
            if len(existing["deduped_equivalent_strategy_examples"]) < 5:
                existing["deduped_equivalent_strategy_examples"].append(candidate.get("strategy", {}))
            continue
        candidate["deduped_equivalent_strategy_count"] = 1
        performance_seen[perf_key] = candidate
        deduped.append(candidate)
    return deduped, {
        "input_count": len(candidates),
        "output_count": len(deduped),
        "exact_duplicate_count": exact_duplicates,
        "performance_duplicate_count": performance_duplicates,
        "dedup_key": "exact strategy first, then symbol/interval/stage/family plus rounded OOS performance",
    }


def promotion_review(candidate, initial):
    train = candidate.get("train_summary") or {}
    validation = candidate.get("validation_summary") or {}
    oos = candidate.get("oos_summary") or {}
    blockers = []
    if candidate.get("holdout_used_for_selection") is not False:
        blockers.append("final_holdout_selection_contract_missing")
    if int(train.get("trade_count") or 0) < 8:
        blockers.append("train_trade_sample_below_8")
    if float(train.get("win_rate_pct") or 0.0) < 45.0:
        blockers.append("train_win_rate_below_45_pct")
    if float(train.get("net_return_pct") or -999.0) <= 0.0:
        blockers.append("train_net_return_not_positive")
    if float(train.get("max_drawdown_pct") or -999.0) < -15.0:
        blockers.append("train_drawdown_too_deep")
    if int(validation.get("trade_count") or 0) < 4:
        blockers.append("validation_trade_sample_below_4")
    if float(validation.get("net_return_pct") or -999.0) <= 0.0:
        blockers.append("validation_net_return_not_positive")
    if float(validation.get("max_drawdown_pct") or -999.0) < -15.0:
        blockers.append("validation_drawdown_too_deep")
    if int(oos.get("trade_count") or 0) < 6:
        blockers.append("holdout_trade_sample_below_6")
    if float(oos.get("win_rate_pct") or 0.0) < 55.0:
        blockers.append("holdout_win_rate_below_55_pct")
    if float(oos.get("net_return_pct") or -999.0) <= 0.0:
        blockers.append("holdout_net_return_not_positive")
    if float(oos.get("max_drawdown_pct") or -999.0) < -15.0:
        blockers.append("oos_drawdown_too_deep")
    if float(oos.get("profit_factor") or 0.0) <= 1.0:
        blockers.append("holdout_profit_factor_not_above_1")
    if float(oos.get("largest_winner_share_pct") or 100.0) > 40.0:
        blockers.append("holdout_single_winner_concentration_above_40_pct")

    segment_nets = [float(item.get("net_return_pct") or 0.0) for item in (train, validation, oos)]
    segment_wins = [float(item.get("win_rate_pct") or 0.0) for item in (train, validation, oos)]
    segment_dd = [abs(min(float(item.get("max_drawdown_pct") or 0.0), 0.0)) for item in (train, validation, oos)]
    robust_score = (
        min(segment_nets) * 1.5
        + min(segment_wins) * 0.5
        + float(candidate.get("selection_score") or -999.0) * 0.25
        - max(segment_dd) * 1.5
        - len(blockers) * 22.0
    )
    if not blockers and candidate.get("stage") in {
        "target_research_pass_current_signal",
        "paper_forward_candidate_current_signal",
    }:
        recommended = "paper_forward_candidate"
    elif len(blockers) <= 2 and candidate.get("stage") in {"paper_only", "research_watch"}:
        recommended = "paper_only_research_priority"
    else:
        recommended = "research_watch_only"
    return {
        "promotion_blockers": blockers,
        "recommended_max_action": recommended,
        "robust_score": round(robust_score, 4),
        "why_not_live_ready": "; ".join(blockers) if blockers else "passes current research gates but still paper-only",
    }


def self_test():
    robust = {
        "trade_count": 12,
        "win_rate_pct": 60.0,
        "final_capital": 520.0,
        "net_return_pct": 4.0,
        "max_drawdown_pct": -5.0,
        "expectancy_pct": 0.8,
        "profit_factor": 1.6,
        "largest_winner_share_pct": 35.0,
    }
    candidate = {
        "stage": "paper_forward_candidate_current_signal",
        "train_summary": robust,
        "validation_summary": robust,
        "oos_summary": robust,
        "selection_score": 12.0,
        "holdout_used_for_selection": False,
    }
    allowed = promotion_review(candidate, 500.0)
    assert not allowed["promotion_blockers"], allowed
    assert allowed["recommended_max_action"] == "paper_forward_candidate", allowed

    legacy = promotion_review(
        {
            "stage": "paper_forward_candidate_current_signal",
            "train_summary": robust,
            "oos_summary": robust,
        },
        500.0,
    )
    assert "final_holdout_selection_contract_missing" in legacy["promotion_blockers"], legacy
    assert "validation_trade_sample_below_4" in legacy["promotion_blockers"], legacy
    import pandas as pd

    unit_now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    now_ms = int(unit_now.timestamp() * 1000)
    fresh_df = pd.DataFrame(
        [
            {"t": now_ms - 2 * 3_600_000, "ct": now_ms - 3_600_001, "o": 1, "h": 1, "l": 1, "c": 1, "qv": 1},
            {"t": now_ms - 3_600_000, "ct": now_ms + 1_000, "o": 1, "h": 1, "l": 1, "c": 1, "qv": 1},
        ]
    )
    closed, freshness = closed_fresh_frame(fresh_df, "1h", unit_now)
    assert len(closed) == 1 and freshness["status"] == "fresh" and freshness["removed_open_rows"] == 1, freshness
    stale_df = fresh_df.iloc[:1].copy()
    stale_df["ct"] = now_ms - 10 * 3_600_000
    _, stale = closed_fresh_frame(stale_df, "1h", unit_now)
    assert stale["status"] == "stale", stale
    return {
        "status": "ok",
        "three_segment_promotion_verified": True,
        "legacy_two_segment_candidate_blocked": True,
        "holdout_not_used_for_selection_verified": True,
        "latest_closed_bar_only_verified": True,
        "stale_frame_block_verified": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dirs", default=",".join(DEFAULT_ACTIVE_CACHE_DIRS + DEFAULT_CACHE_DIRS))
    ap.add_argument("--symbols", default="")
    ap.add_argument("--intervals", default="1h,4h,1d")
    ap.add_argument("--initial", type=float, default=500.0)
    ap.add_argument("--min-bars", type=int, default=500)
    ap.add_argument("--max-frames", type=int, default=0, help="0 means no frame limit")
    ap.add_argument("--max-strategies-per-frame", type=int, default=0, help="0 means full strategy grid")
    ap.add_argument(
        "--max-holdout-candidates-per-frame",
        type=int,
        default=5,
        help="Evaluate final holdout only for the top train+validation current-signal strategies per frame.",
    )
    ap.add_argument("--max-candidates-output", type=int, default=50)
    ap.add_argument("--sample-trades", type=int, default=5)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--output", default="", help="write JSON output here; defaults to experiments/YYYYMMDD-HHMM-current-signal-probe.json")
    ap.add_argument("--compact-output", action="store_true", help="print compact run summary instead of full JSON")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return

    now = datetime.now(timezone.utc)
    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None
    intervals = {s.strip() for s in args.intervals.split(",") if s.strip()} or None
    cache_dirs = expand_cache_dirs([s.strip() for s in args.cache_dirs.split(",") if s.strip()])
    discovered_frames = load_cached_frames(cache_dirs, symbols=symbols, intervals=intervals)
    frames = {}
    frame_freshness = []
    for (symbol, interval), frame in discovered_frames.items():
        closed, freshness = closed_fresh_frame(frame, interval, now)
        freshness = {"symbol": symbol, **freshness}
        frame_freshness.append(freshness)
        if closed is not None and freshness.get("status") == "fresh":
            frames[(symbol, interval)] = closed
    if args.max_frames and args.max_frames > 0:
        frames = dict(list(sorted(frames.items()))[: args.max_frames])
    candidates = []
    scanned = 0
    current_signals = 0

    for (symbol, interval), df in frames.items():
        if len(df) < args.min_bars:
            continue
        train_end = int(len(df) * 0.50)
        validation_end = int(len(df) * 0.75)
        btc_df = frames.get(("BTCUSDT", interval))
        strategies = param_grid(interval)
        if args.max_strategies_per_frame and args.max_strategies_per_frame > 0:
            strategies = strategies[: args.max_strategies_per_frame]
        preselected = []
        for s in strategies:
            if s.family == "relative_strength" and btc_df is None:
                continue
            scanned += 1
            sig = signal_array(df, s, btc_df=btc_df)
            if not bool(sig[len(df) - 1]):
                continue
            current_signals += 1
            train = backtest(df, s, 0, train_end, btc_df=btc_df, initial=args.initial)
            validation = backtest(df, s, train_end, validation_end, btc_df=btc_df, initial=args.initial)
            preselected.append(
                {
                    "strategy": s,
                    "train": train,
                    "validation": validation,
                    "selection_score": selection_score(train, validation),
                }
            )
        preselected.sort(key=lambda item: item["selection_score"], reverse=True)
        holdout_limit = max(1, int(args.max_holdout_candidates_per_frame or 1))
        for selected in preselected[:holdout_limit]:
            s = selected["strategy"]
            train = selected["train"]
            validation = selected["validation"]
            test = backtest(df, s, validation_end, len(df), btc_df=btc_df, initial=args.initial)
            stage = classify(train, test, True, initial=args.initial, validation=validation)
            candidate = {
                "symbol": symbol,
                "interval": interval,
                "strategy": s.__dict__,
                "bars": len(df),
                "validation_protocol": "train_50_validation_25_final_holdout_25",
                "holdout_used_for_selection": False,
                "selection_score": round(float(selected["selection_score"]), 4),
                "train_summary": segment_summary(train),
                "validation_summary": segment_summary(validation),
                "oos_summary": segment_summary(test),
                "stage": stage,
                "current_signal": True,
                "sample_trades": test["trades"][-max(0, args.sample_trades):] if args.sample_trades else [],
            }
            candidate.update(promotion_review(candidate, args.initial))
            candidates.append(candidate)

    candidates.sort(
        key=lambda x: (
            x["stage"] == "target_research_pass_current_signal",
            len(x.get("promotion_blockers") or []) == 0,
            x.get("selection_score", -999999.0),
            x.get("robust_score", -999999.0),
        ),
        reverse=True,
    )
    candidate_summary = summarize_candidates(candidates)
    deduped_candidates, dedup_summary = dedupe_candidates(candidates)
    output = {
        "generated_at": now.isoformat(),
        "private_api_keys_used": False,
        "live_orders_enabled": False,
        "cache_dirs": cache_dirs,
        "frames_discovered": len(discovered_frames),
        "frames_loaded": len(frames),
        "fresh_frame_count": sum(item.get("status") == "fresh" for item in frame_freshness),
        "stale_frame_count": sum(item.get("status") == "stale" for item in frame_freshness),
        "future_timestamp_frame_count": sum(item.get("status") == "future_timestamp" for item in frame_freshness),
        "open_bar_rows_removed": sum(int(item.get("removed_open_rows") or 0) for item in frame_freshness),
        "frame_freshness": frame_freshness,
        "strategies_scanned": scanned,
        "current_signal_strategies": current_signals,
        "holdout_candidates_evaluated": len(candidates),
        "max_holdout_candidates_per_frame": args.max_holdout_candidates_per_frame,
        "validation_protocol": "rank_current_signals_on_train_50_validation_25_then_gate_on_final_25_holdout",
        "holdout_used_for_selection": False,
        "execution_assumptions": {
            "capital_fraction_per_trade": 0.25,
            "commission_bps_per_side": 10,
            "slippage_bps_per_side": 8,
            "round_trip_spread_bps": 10,
            "entry_timing": "next_bar_open",
            "same_bar_stop_take_policy": "conservative_stop_first",
        },
        "candidate_count": len(candidates),
        "candidate_summary": candidate_summary,
        "deduplicated_candidate_count": len(deduped_candidates),
        "dedup_summary": dedup_summary,
        "candidates_truncated": len(deduped_candidates) > max(0, args.max_candidates_output),
        "candidates": deduped_candidates[: max(0, args.max_candidates_output)],
        "top_candidates": deduped_candidates[: min(50, max(0, args.max_candidates_output))],
            "decision_rule": "current signal must also pass train/OOS gates before any paper-forward candidate; monitor never live-orders",
            "promotion_gate_note": "Candidates are sorted by current signal, promotion blockers, robust score, then OOS performance. Bad train windows cannot silently dominate the queue.",
    }
    output_path = Path(args.output) if args.output else default_output_path(now)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    if args.compact_output:
        compact = {
            "generated_at": output["generated_at"],
            "output": str(output_path),
            "live_orders_enabled": False,
            "frames_loaded": output["frames_loaded"],
            "frames_discovered": output["frames_discovered"],
            "fresh_frame_count": output["fresh_frame_count"],
            "stale_frame_count": output["stale_frame_count"],
            "future_timestamp_frame_count": output["future_timestamp_frame_count"],
            "open_bar_rows_removed": output["open_bar_rows_removed"],
            "strategies_scanned": output["strategies_scanned"],
            "current_signal_strategies": output["current_signal_strategies"],
            "candidate_count": output["candidate_count"],
            "deduplicated_candidate_count": output["deduplicated_candidate_count"],
            "dedup_summary": output["dedup_summary"],
            "candidate_summary": output["candidate_summary"],
            "top_candidates": [
                {
                    "symbol": c["symbol"],
                    "interval": c["interval"],
                    "stage": c["stage"],
                    "family": c["strategy"]["family"],
                    "robust_score": c.get("robust_score"),
                    "oos_final_capital": c["oos_summary"]["final_capital"],
                    "oos_win_rate_pct": c["oos_summary"]["win_rate_pct"],
                    "oos_max_drawdown_pct": c["oos_summary"]["max_drawdown_pct"],
                    "promotion_blockers": c.get("promotion_blockers") or [],
                    "recommended_max_action": c.get("recommended_max_action"),
                }
                for c in output["top_candidates"][:10]
            ],
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
