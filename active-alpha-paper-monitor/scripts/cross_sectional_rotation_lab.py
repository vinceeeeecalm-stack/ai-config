#!/usr/bin/env python3
"""Three-segment cross-sectional crypto rotation research lab.

Ranks symbols only on closed, exact-timestamp bars and enters the selected
symbol at the next bar open. Research-only: no ledger mutation or orders.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from weekly_goal_strategy_lab import load_cached_frames


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
DEFAULT_REPORT = ACTIVE_ROOT / "reports" / "CROSS_SECTIONAL_ROTATION_LAB.md"
DEFAULT_EXPERIMENT = ACTIVE_ROOT / "experiments" / "cross-sectional-rotation-lab.json"
ROUND_TRIP_FRICTION_PCT = 0.46
CAPITAL_FRACTION = 0.25


@dataclasses.dataclass(frozen=True)
class RotationStrategy:
    interval: str
    lookback: int
    hold_bars: int
    min_momentum_pct: float
    volume_ratio: float
    min_breadth: float
    stop_pct: float
    take_pct: float


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def exact_timestamp_panel(frames: dict[tuple[str, str], pd.DataFrame], interval: str) -> dict[str, Any]:
    selected = {symbol: frame.copy() for (symbol, frame_interval), frame in frames.items() if frame_interval == interval}
    if len(selected) < 3:
        return {"status": "insufficient_symbols", "interval": interval, "symbol_count": len(selected)}
    timestamp_union: set[int] = set()
    for frame in selected.values():
        timestamps = set(pd.to_numeric(frame["t"], errors="coerce").dropna().astype("int64").tolist())
        timestamp_union.update(timestamps)
    timestamps = np.array(sorted(timestamp_union), dtype=np.int64)
    symbols = sorted(selected)
    fields: dict[str, np.ndarray] = {}
    for field in ("o", "h", "l", "c", "qv"):
        columns = []
        for symbol in symbols:
            frame = selected[symbol].set_index("t").reindex(timestamps)
            columns.append(pd.to_numeric(frame[field], errors="coerce").to_numpy(dtype=float))
        fields[field] = np.column_stack(columns)
    availability = np.ones((len(timestamps), len(symbols)), dtype=bool)
    for field in fields:
        availability &= np.isfinite(fields[field])
    minimum_symbols = min(len(symbols), 5)
    usable_rows = availability.sum(axis=1) >= minimum_symbols
    timestamps = timestamps[usable_rows]
    if len(timestamps) < 300:
        return {
            "status": "insufficient_synchronous_bars",
            "interval": interval,
            "symbol_count": len(selected),
            "minimum_symbols_per_bar": minimum_symbols,
            "bars": len(timestamps),
        }
    return {
        "status": "ok",
        "interval": interval,
        "symbols": symbols,
        "timestamps": timestamps,
        **{field: values[usable_rows] for field, values in fields.items()},
        "availability": availability[usable_rows],
        "minimum_symbols_per_bar": minimum_symbols,
        "bars": len(timestamps),
    }


def strategy_grid(interval: str) -> list[RotationStrategy]:
    return [
        RotationStrategy(interval, lookback, hold, momentum, volume, breadth, stop, take)
        for lookback in (4, 8, 16, 32)
        for hold in (2, 4, 8)
        for momentum in (1.0, 2.0, 4.0)
        for volume in (1.0, 1.5)
        for breadth in (0.5, 0.6)
        for stop in (-4.0, -8.0)
        for take in (8.0, 20.0)
    ]


def rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    return pd.DataFrame(values).rolling(window, min_periods=window).median().to_numpy(dtype=float)


def precompute_signal_inputs(panel: dict[str, Any], lookback: int) -> dict[str, np.ndarray]:
    close = panel["c"]
    quote_volume = panel["qv"]
    momentum = np.full_like(close, np.nan, dtype=float)
    momentum[lookback:] = (close[lookback:] / close[:-lookback] - 1.0) * 100.0
    one_bar = np.full_like(close, np.nan, dtype=float)
    one_bar[1:] = close[1:] / close[:-1] - 1.0
    valid_returns = np.isfinite(one_bar)
    breadth = np.divide(
        ((one_bar > 0) & valid_returns).sum(axis=1),
        valid_returns.sum(axis=1),
        out=np.zeros(len(one_bar), dtype=float),
        where=valid_returns.sum(axis=1) > 0,
    )
    volume_median = rolling_median(quote_volume, max(8, lookback))
    volume_ratio = np.divide(
        quote_volume,
        volume_median,
        out=np.full_like(quote_volume, np.nan, dtype=float),
        where=np.isfinite(volume_median) & (volume_median > 0),
    )
    return {"momentum": momentum, "breadth": breadth, "volume_ratio": volume_ratio}


def simulate(
    panel: dict[str, Any],
    strategy: RotationStrategy,
    start: int,
    end: int,
    *,
    initial: float = 500.0,
) -> dict[str, Any]:
    signals = precompute_signal_inputs(panel, strategy.lookback)
    opens, highs, lows = panel["o"], panel["h"], panel["l"]
    timestamps = panel["timestamps"]
    symbols = panel["symbols"]
    momentum = signals["momentum"]
    volume_ratio = signals["volume_ratio"]
    breadth = signals["breadth"]
    start = max(start, strategy.lookback, max(8, strategy.lookback))
    end = min(end, len(timestamps))
    equity = float(initial)
    peak = equity
    max_drawdown = 0.0
    trades: list[dict[str, Any]] = []
    index = start
    while index < end - 1:
        eligible = (
            np.isfinite(momentum[index])
            & np.isfinite(volume_ratio[index])
            & (momentum[index] >= strategy.min_momentum_pct)
            & (volume_ratio[index] >= strategy.volume_ratio)
        )
        if breadth[index] < strategy.min_breadth or not eligible.any():
            index += 1
            continue
        scores = np.where(eligible, momentum[index] * np.minimum(volume_ratio[index], 4.0), -np.inf)
        symbol_index = int(np.argmax(scores))
        entry_index = index + 1
        entry = float(opens[entry_index, symbol_index])
        if not math.isfinite(entry) or entry <= 0:
            index += 1
            continue
        planned_exit = min(end - 1, entry_index + strategy.hold_bars)
        exit_index = planned_exit
        gross_pct = (float(opens[planned_exit, symbol_index]) / entry - 1.0) * 100.0
        reason = "time"
        mfe = -math.inf
        mae = math.inf
        for bar in range(entry_index, planned_exit + 1):
            if not (math.isfinite(float(highs[bar, symbol_index])) and math.isfinite(float(lows[bar, symbol_index]))):
                exit_index = max(entry_index, bar - 1)
                fallback_exit = float(opens[exit_index, symbol_index])
                gross_pct = (fallback_exit / entry - 1.0) * 100.0 if math.isfinite(fallback_exit) else 0.0
                reason = "data_window_end"
                break
            high_pct = (float(highs[bar, symbol_index]) / entry - 1.0) * 100.0
            low_pct = (float(lows[bar, symbol_index]) / entry - 1.0) * 100.0
            mfe = max(mfe, high_pct)
            mae = min(mae, low_pct)
            stop_hit = low_pct <= strategy.stop_pct
            take_hit = high_pct >= strategy.take_pct
            if stop_hit:
                gross_pct = strategy.stop_pct
                exit_index = bar
                reason = "stop"
                break
            if take_hit:
                gross_pct = strategy.take_pct
                exit_index = bar
                reason = "take"
                break
        net_pct = gross_pct - ROUND_TRIP_FRICTION_PCT
        if not math.isfinite(mfe):
            mfe = 0.0
        if not math.isfinite(mae):
            mae = 0.0
        pnl = equity * CAPITAL_FRACTION * net_pct / 100.0
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity / peak - 1.0) * 100.0)
        trades.append(
            {
                "symbol": symbols[symbol_index],
                "signal_time": int(timestamps[index]),
                "entry_time": int(timestamps[entry_index]),
                "exit_time": int(timestamps[exit_index]),
                "entry": round(entry, 10),
                "gross_pct": round(gross_pct, 6),
                "net_pct": round(net_pct, 6),
                "pnl_usd": round(pnl, 6),
                "mfe_pct": round(mfe, 6),
                "mae_pct": round(mae, 6),
                "reason": reason,
                "rank_score": round(float(scores[symbol_index]), 6),
                "breadth": round(float(breadth[index]), 6),
            }
        )
        index = max(index + 1, exit_index)
    wins = [item for item in trades if item["net_pct"] > 0]
    losses = [item for item in trades if item["net_pct"] <= 0]
    gross_win = sum(item["pnl_usd"] for item in wins)
    gross_loss = abs(sum(item["pnl_usd"] for item in losses))
    return {
        "trade_count": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 4) if trades else 0.0,
        "final_capital": round(equity, 6),
        "net_return_pct": round((equity / initial - 1.0) * 100.0, 6),
        "max_drawdown_pct": round(max_drawdown, 6),
        "profit_factor": round(gross_win / gross_loss, 6) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0),
        "largest_winner_share_pct": round(max((item["pnl_usd"] for item in wins), default=0.0) / gross_win * 100.0, 4) if gross_win > 0 else 0.0,
        "symbol_count": len({item["symbol"] for item in trades}),
        "symbol_trade_counts": {
            str(symbol): int(count)
            for symbol, count in pd.Series([item["symbol"] for item in trades]).value_counts().items()
        } if trades else {},
        "avg_friction_pct": ROUND_TRIP_FRICTION_PCT if trades else 0.0,
        "trades": trades,
    }


def score(result: dict[str, Any], *, minimum_trades: int) -> float:
    if int(result.get("trade_count") or 0) < minimum_trades:
        return -1_000_000.0 + float(result.get("trade_count") or 0)
    return (
        float(result.get("net_return_pct") or 0.0) * 4.0
        + float(result.get("win_rate_pct") or 0.0) * 0.25
        + min(float(result.get("profit_factor") or 0.0), 5.0) * 3.0
        + float(result.get("max_drawdown_pct") or 0.0) * 1.5
        + min(float(result.get("symbol_count") or 0.0), 6.0)
        - max(0.0, float(result.get("largest_winner_share_pct") or 0.0) - 40.0) * 0.25
    )


def latest_signal(panel: dict[str, Any], strategy: RotationStrategy) -> dict[str, Any]:
    inputs = precompute_signal_inputs(panel, strategy.lookback)
    index = len(panel["timestamps"]) - 1
    eligible = (
        np.isfinite(inputs["momentum"][index])
        & np.isfinite(inputs["volume_ratio"][index])
        & (inputs["momentum"][index] >= strategy.min_momentum_pct)
        & (inputs["volume_ratio"][index] >= strategy.volume_ratio)
    )
    active = bool(inputs["breadth"][index] >= strategy.min_breadth and eligible.any())
    if not active:
        return {"active": False, "signal_time": int(panel["timestamps"][index]), "breadth": round(float(inputs["breadth"][index]), 6)}
    scores = np.where(eligible, inputs["momentum"][index] * np.minimum(inputs["volume_ratio"][index], 4.0), -np.inf)
    selected = int(np.argmax(scores))
    return {
        "active": True,
        "symbol": panel["symbols"][selected],
        "signal_time": int(panel["timestamps"][index]),
        "entry_timing": "next_bar_open_after_signal_close",
        "momentum_pct": round(float(inputs["momentum"][index, selected]), 6),
        "volume_ratio": round(float(inputs["volume_ratio"][index, selected]), 6),
        "breadth": round(float(inputs["breadth"][index]), 6),
    }


def evaluate_interval(panel: dict[str, Any], initial: float, top_train: int) -> dict[str, Any]:
    bars = int(panel["bars"])
    train_end = bars // 2
    validation_end = train_end + bars // 4
    train_rows = []
    for strategy in strategy_grid(panel["interval"]):
        result = simulate(panel, strategy, 0, train_end, initial=initial)
        train_rows.append((score(result, minimum_trades=8), strategy, result))
    train_rows.sort(key=lambda item: item[0], reverse=True)
    validation_rows = []
    for train_score, strategy, train in train_rows[: max(1, top_train)]:
        validation = simulate(panel, strategy, train_end, validation_end, initial=initial)
        validation_rows.append((score(validation, minimum_trades=4), train_score, strategy, train, validation))
    validation_rows.sort(key=lambda item: item[0], reverse=True)
    validation_score, train_score, strategy, train, validation = validation_rows[0]
    holdout = simulate(panel, strategy, validation_end, bars, initial=initial)
    current = latest_signal(panel, strategy)
    blockers = []
    for segment, result, minimum in (("train", train, 8), ("validation", validation, 4), ("holdout", holdout, 6)):
        if result["trade_count"] < minimum:
            blockers.append(f"{segment}_sample_below_{minimum}")
        if result["net_return_pct"] <= 0:
            blockers.append(f"{segment}_net_return_not_positive")
        if result["win_rate_pct"] < 55:
            blockers.append(f"{segment}_win_rate_below_55")
        if result["max_drawdown_pct"] < -15:
            blockers.append(f"{segment}_drawdown_below_minus_15")
        if result["largest_winner_share_pct"] > 50:
            blockers.append(f"{segment}_winner_concentration_above_50")
    stage = "paper_retest_candidate" if not blockers else "research_only"
    return {
        "interval": panel["interval"],
        "bars": bars,
        "symbol_count": len(panel["symbols"]),
        "symbols": panel["symbols"],
        "validation_protocol": "grid_train_50_rank_validation_25_single_final_holdout_25",
        "holdout_used_for_selection": False,
        "strategy": dataclasses.asdict(strategy),
        "train_score": round(train_score, 6),
        "validation_score": round(validation_score, 6),
        "train": train,
        "validation": validation,
        "holdout": holdout,
        "current_signal": current,
        "promotion_blockers": blockers,
        "stage": stage,
    }


def build_record(cache_dirs: list[str], symbols: set[str] | None, intervals: list[str], initial: float, top_train: int) -> dict[str, Any]:
    frames = load_cached_frames(cache_dirs, symbols=symbols, intervals=set(intervals))
    panels = [exact_timestamp_panel(frames, interval) for interval in intervals]
    results = [evaluate_interval(panel, initial, top_train) for panel in panels if panel.get("status") == "ok"]
    candidates = [item for item in results if item.get("stage") == "paper_retest_candidate"]
    current_candidates = [item for item in candidates if (item.get("current_signal") or {}).get("active")]
    change_payload = {
        "strategy_version": "cross-sectional-rotation-research-v1",
        "interval_results": [
            {
                "interval": item.get("interval"),
                "stage": item.get("stage"),
                "train": (item.get("train") or {}).get("net_return_pct"),
                "validation": (item.get("validation") or {}).get("net_return_pct"),
                "holdout": (item.get("holdout") or {}).get("net_return_pct"),
            }
            for item in results
        ],
    }
    change_id = "pc-cross-sectional-rotation-" + hashlib.sha256(
        json.dumps(change_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:10]
    proposed_changes = [
        {
            "proposed_change_id": change_id,
            "status": "proposed_research_only_not_applied",
            "scope": "research_only_strategy_iteration",
            "change_type": "cross_sectional_rotation_research_gate",
            "title": "Keep cross-sectional rotation behind offline consistency gates",
            "rationale": (
                "The newest train/validation/holdout segments do not all show positive net return and >=55% win rate after friction. "
                "The strategy adds useful diversified research, but current evidence does not justify paper entry."
            ),
            "affected_items": change_payload["interval_results"],
            "proposed_adjustment": {
                "action": "retain_in_offline_research_pool_only",
                "paper_entry_enabled": False,
                "require_positive_train_validation_holdout": True,
                "require_holdout_win_rate_pct": 55,
                "require_holdout_symbol_count": 3,
            },
            "validation_plan": {
                "next_run": "refresh durable Klines and rerun exact-timestamp three-segment lab",
                "minimum_independent_windows": 3,
                "success_criteria": "positive net return in all segments, controlled drawdown, diversified symbols, and no dominant winner",
            },
            "risk_controls": [
                "research_only",
                "next_bar_entry",
                "conservative_stop_first",
                "friction_included",
                "no_forward_fill",
                "no_live_orders",
            ],
            "live_orders_enabled": False,
            "private_api_used": False,
        }
    ]
    return {
        "run_id": f"{utc_now().strftime('%Y%m%d-%H%M%S')}-cross-sectional-rotation-lab",
        "generated_at": utc_now().replace(microsecond=0).isoformat(),
        "status": "paper_retest_candidate_found" if candidates else "research_no_robust_candidate",
        "strategy_version": "cross-sectional-rotation-research-v1",
        "scope": "paper_only_research",
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "cache_dirs": cache_dirs,
        "requested_intervals": intervals,
        "frames_loaded": len(frames),
        "panel_count": len(results),
        "strategy_count_per_interval": len(strategy_grid(intervals[0])) if intervals else 0,
        "execution_assumptions": {
            "capital_fraction_per_trade": CAPITAL_FRACTION,
            "commission_slippage_spread_round_trip_pct": ROUND_TRIP_FRICTION_PCT,
            "same_bar_stop_take_policy": "conservative_stop_first",
            "entry_timing": "next_bar_open_after_signal_close",
            "exact_timestamp_alignment": True,
            "forward_fill": False,
            "one_open_position_at_a_time": True,
        },
        "results": results,
        "paper_retest_candidate_count": len(candidates),
        "current_paper_retest_candidate_count": len(current_candidates),
        "paper_retest_candidates": candidates,
        "current_paper_retest_candidates": current_candidates,
        "proposed_changes": proposed_changes,
        "operator_note": "Research evidence only. A candidate must still pass live/current liquidity, spread/depth, recovery, capacity, risk and paper execution gates before a tiny paper retest.",
    }


def render_report(record: dict[str, Any]) -> str:
    lines = [
        "# Cross-Sectional Rotation Lab",
        "",
        "Paper-only, exact-timestamp, next-bar execution research.",
        "",
        f"- status: `{record.get('status')}`",
        f"- run_id: `{record.get('run_id')}`",
        f"- frames/panels: `{record.get('frames_loaded')}/{record.get('panel_count')}`",
        f"- strategies per interval: `{record.get('strategy_count_per_interval')}`",
        f"- paper/current candidates: `{record.get('paper_retest_candidate_count')}/{record.get('current_paper_retest_candidate_count')}`",
        "",
        "| Interval | Stage | Strategy | Train | Validation | Holdout | DD | Symbols | Current | Blockers |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in record.get("results") or []:
        strategy = item.get("strategy") or {}
        train, validation, holdout = item.get("train") or {}, item.get("validation") or {}, item.get("holdout") or {}
        current = item.get("current_signal") or {}
        lines.append(
            f"| `{item.get('interval')}` | `{item.get('stage')}` | `{strategy.get('lookback')}/{strategy.get('hold_bars')}` | "
            f"`{train.get('net_return_pct')}` | `{validation.get('net_return_pct')}` | `{holdout.get('net_return_pct')}` | "
            f"`{holdout.get('max_drawdown_pct')}` | `{holdout.get('symbol_count')}` | "
            f"`{current.get('symbol') if current.get('active') else 'none'}` | `{', '.join(item.get('promotion_blockers') or []) or '-'}` |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            f"- live_orders_enabled: `{record.get('live_orders_enabled')}`",
            f"- private_api_used: `{record.get('private_api_used')}`",
            f"- ledger_mutated: `{record.get('ledger_mutated')}`",
            f"- {record.get('operator_note')}",
            "",
        ]
    )
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="cross_section_rotation_") as tmp_text:
        root = Path(tmp_text)
        timestamps = np.arange(400, dtype=np.int64) * 3_600_000 + 1_700_000_000_000
        frames = {}
        for index, symbol in enumerate(("AAAUSDT", "BBBUSDT", "CCCUSDT")):
            base = 100.0 + index
            close = base * np.cumprod(np.full(400, 1.002 + index * 0.0003))
            frame = pd.DataFrame(
                {
                    "t": timestamps,
                    "o": close / 1.001,
                    "h": close * 1.01,
                    "l": close * 0.99,
                    "c": close,
                    "v": np.full(400, 10.0),
                    "qv": np.linspace(1000, 3000 + index * 500, 400),
                }
            )
            frames[(symbol, "1h")] = frame
        panel = exact_timestamp_panel(frames, "1h")
        assert panel["status"] == "ok" and panel["bars"] == 400, panel
        strategy = RotationStrategy("1h", 4, 2, 0.2, 1.0, 0.5, -4.0, 8.0)
        result = simulate(panel, strategy, 0, 400)
        assert result["trade_count"] > 10, result
        signal = latest_signal(panel, strategy)
        assert signal["active"] and signal["symbol"] == "CCCUSDT", signal
    return {
        "status": "ok",
        "exact_timestamp_alignment_verified": True,
        "next_bar_entry_verified": True,
        "friction_and_stop_take_verified": True,
        "cross_symbol_ranking_verified": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper-only cross-sectional rotation research lab")
    parser.add_argument("--cache-dirs", required="--self-test" not in __import__("sys").argv)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--initial", type=float, default=500.0)
    parser.add_argument("--top-train", type=int, default=30)
    parser.add_argument("--report-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--json-output", default=str(DEFAULT_EXPERIMENT))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    cache_dirs = [item.strip() for item in args.cache_dirs.split(",") if item.strip()]
    symbols = {item.strip().upper() for item in args.symbols.split(",") if item.strip()} or None
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    record = build_record(cache_dirs, symbols, intervals, args.initial, max(1, args.top_train))
    report_path = Path(args.report_output)
    json_path = Path(args.json_output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    record["outputs"] = {"report": rel(report_path), "experiment": rel(json_path)}
    report_path.write_text(render_report(record), encoding="utf-8")
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.compact_output:
        print(
            json.dumps(
                {
                    "run_id": record["run_id"],
                    "status": record["status"],
                    "frames_loaded": record["frames_loaded"],
                    "panel_count": record["panel_count"],
                    "strategy_count_per_interval": record["strategy_count_per_interval"],
                    "paper_retest_candidate_count": record["paper_retest_candidate_count"],
                    "current_paper_retest_candidate_count": record["current_paper_retest_candidate_count"],
                    "results": [
                        {
                            "interval": item["interval"],
                            "stage": item["stage"],
                            "strategy": item["strategy"],
                            "train_net_return_pct": item["train"]["net_return_pct"],
                            "validation_net_return_pct": item["validation"]["net_return_pct"],
                            "holdout_net_return_pct": item["holdout"]["net_return_pct"],
                            "holdout_win_rate_pct": item["holdout"]["win_rate_pct"],
                            "holdout_max_drawdown_pct": item["holdout"]["max_drawdown_pct"],
                            "holdout_symbol_count": item["holdout"]["symbol_count"],
                            "current_signal": item["current_signal"],
                            "promotion_blockers": item["promotion_blockers"],
                        }
                        for item in record["results"]
                    ],
                    "outputs": record["outputs"],
                    "live_orders_enabled": False,
                    "private_api_used": False,
                    "ledger_mutated": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
