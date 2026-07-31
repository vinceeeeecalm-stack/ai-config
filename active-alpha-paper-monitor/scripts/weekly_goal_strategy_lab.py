#!/usr/bin/env python3
"""
Weekly goal strategy lab for active-alpha-paper-monitor.

Research-only. No private keys. No live orders. Uses cached Binance public
klines when available and evaluates multiple long-only spot strategy families
with train/test splits. The weekly-double target is treated as a validation
gate, not a promise.
"""

import argparse
import glob
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ACTIVE_ROOT = Path(__file__).resolve().parent.parent
DURABLE_KLINE_CACHE_ROOT = ACTIVE_ROOT / "cache" / "binance_klines"

DEFAULT_CACHE_DIRS = [
    str(DURABLE_KLINE_CACHE_ROOT),
    "/private/tmp/binance_klines_cache_v216",
    "/private/tmp/binance_klines_cache_v216_retry",
]


@dataclass(frozen=True)
class Strategy:
    family: str
    interval: str
    lookback: int
    hold_bars: int
    stop_pct: float
    take_pct: float
    volume_mult: float = 1.0
    threshold_pct: float = 5.0
    ma_fast: int = 20
    ma_slow: int = 80
    rsi_max: float = 40.0
    drawdown_pct: float = -10.0
    squeeze_ratio: float = 0.55


def load_cached_frames(cache_dirs, symbols=None, intervals=None):
    files = []
    for d in cache_dirs:
        files.extend(glob.glob(os.path.join(d, "*.json")))
        files.extend(glob.glob(os.path.join(d, "**", "*.json"), recursive=True))
    candidates = {}
    for path in files:
        name = Path(path).stem
        parts = name.split("_")
        if len(parts) < 4:
            continue
        symbol, interval = parts[0], parts[1]
        if symbols and symbol not in symbols:
            continue
        if intervals and interval not in intervals:
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                rows = json.load(handle)
        except Exception:
            continue
        if not isinstance(rows, list) or len(rows) < 300:
            continue
        end_timestamp = max((int(row.get("t") or 0) for row in rows if isinstance(row, dict)), default=0)
        candidate_rank = (end_timestamp, len(rows), Path(path).stat().st_mtime_ns)
        key = (symbol, interval)
        existing = candidates.get(key)
        if existing and candidate_rank <= existing[0]:
            continue
        candidates[key] = (candidate_rank, rows)

    frames = {}
    for key, (_, rows) in candidates.items():
        df = pd.DataFrame(rows).sort_values("t").drop_duplicates("t").reset_index(drop=True)
        for col in ["o", "h", "l", "c", "qv"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["o", "h", "l", "c", "qv"]).reset_index(drop=True)
        frames[key] = prepare_frame(df)
    return frames


def prepare_frame(df):
    df = df.copy()
    df["ret1_pct"] = df["c"].pct_change() * 100.0
    delta = df["c"].diff()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    avg_gain = gains.rolling(14).sum()
    avg_loss = losses.rolling(14).sum()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi14"] = 100.0 - (100.0 / (1.0 + rs))
    df.loc[avg_loss == 0, "rsi14"] = 100.0
    for n in [6, 12, 24, 42, 72, 84, 96, 168]:
        if n < len(df):
            df[f"ret{n}_pct"] = (df["c"] / df["c"].shift(n) - 1.0) * 100.0
            df[f"high{n}"] = df["h"].rolling(n).max().shift(1)
            df[f"qv_avg{n}"] = df["qv"].rolling(n).mean().shift(1)
    for n in [20, 50, 80, 120, 200]:
        if n < len(df):
            df[f"ma{n}"] = df["c"].rolling(n).mean()
    return df


def pct(a, b):
    return (b / a - 1.0) * 100.0 if a else 0.0


def max_drawdown(curve):
    peak = curve[0]
    dd = 0.0
    for v in curve:
        peak = max(peak, v)
        dd = min(dd, (v / peak - 1.0) * 100.0)
    return dd


def aligned_benchmark_close(df, benchmark_df):
    """Align benchmark closes by exact timestamp without forward filling."""
    if benchmark_df is None or "t" not in benchmark_df or "c" not in benchmark_df:
        return pd.Series(np.nan, index=df.index, dtype=float)
    benchmark = (
        pd.Series(
            pd.to_numeric(benchmark_df["c"], errors="coerce").to_numpy(),
            index=pd.to_numeric(benchmark_df["t"], errors="coerce").to_numpy(),
        )
        .groupby(level=0)
        .last()
    )
    timestamps = pd.to_numeric(df["t"], errors="coerce")
    return pd.Series(timestamps.map(benchmark).to_numpy(), index=df.index, dtype=float)


def signal_array(df, s: Strategy, btc_df=None):
    n = len(df)
    lb = min(s.lookback, n - 1)
    if lb < 5:
        return np.zeros(n, dtype=bool)
    qv_col = f"qv_avg{lb}"
    if qv_col in df:
        vol_ok = df["qv"] >= df[qv_col] * s.volume_mult
    else:
        vol_ok = pd.Series(True, index=df.index)

    if s.family == "breakout":
        sig = df["c"] > df.get(f"high{lb}", df["h"].rolling(lb).max().shift(1))
    elif s.family == "momentum":
        sig = df.get(f"ret{lb}_pct", (df["c"] / df["c"].shift(lb) - 1) * 100) >= s.threshold_pct
    elif s.family == "pump_continuation":
        short_lb = 16 if s.interval == "15m" else 24 if s.interval == "1h" else 6 if s.interval == "4h" else 1
        ret = df.get(f"ret{short_lb}_pct", (df["c"] / df["c"].shift(short_lb) - 1) * 100)
        qv_avg = df.get(f"qv_avg{lb}", df["qv"].rolling(lb).mean().shift(1))
        sig = (ret >= s.threshold_pct) & (df["qv"] >= qv_avg * max(s.volume_mult, 1.5))
    elif s.family == "squeeze_breakout":
        recent_vol = df["ret1_pct"].rolling(max(3, lb // 4)).std(ddof=0).shift(1)
        prior_vol = df["ret1_pct"].rolling(lb).std(ddof=0).shift(2)
        recent_high = df["h"].rolling(max(3, lb // 6)).max().shift(1)
        sig = (recent_vol < prior_vol * s.squeeze_ratio) & (df["c"] > recent_high)
    elif s.family == "dip_reversal":
        recent_high = df.get(f"high{lb}", df["h"].rolling(lb).max().shift(1))
        dd = (df["c"] / recent_high - 1.0) * 100.0
        sig = (dd <= s.drawdown_pct) & (df["rsi14"] <= s.rsi_max) & (df["c"] > df["c"].shift(1))
    elif s.family == "crash_rebound":
        short_lb = max(3, lb // 4)
        recent_high = df.get(f"high{lb}", df["h"].rolling(lb).max().shift(1))
        recent_low = df["l"].rolling(short_lb).min()
        dd = (recent_low / recent_high - 1.0) * 100.0
        intrabar_reclaim = (df["c"] / df["l"].replace(0, np.nan) - 1.0) * 100.0
        prior_oversold = df["rsi14"].shift(1) <= s.rsi_max
        close_reclaim = (df["c"] > df["o"]) & (df["c"] > df["c"].shift(1))
        sig = (dd <= s.drawdown_pct) & prior_oversold & close_reclaim & (intrabar_reclaim >= max(1.5, s.threshold_pct * 0.35))
    elif s.family == "liquidation_wick_reversal":
        short_lb = max(3, lb // 3)
        recent_high = df.get(f"high{lb}", df["h"].rolling(lb).max().shift(1))
        recent_low = df["l"].rolling(short_lb).min()
        dd = (recent_low / recent_high - 1.0) * 100.0
        candle_range = (df["h"] - df["l"]).replace(0, np.nan)
        lower_wick = pd.concat([df["o"], df["c"]], axis=1).min(axis=1) - df["l"]
        lower_wick_ratio = lower_wick / candle_range
        reclaim = (df["c"] > df["o"]) & (df["c"] > df["c"].shift(1))
        sig = (dd <= s.drawdown_pct) & (lower_wick_ratio >= s.squeeze_ratio) & reclaim & (df["rsi14"].shift(1) <= s.rsi_max + 10)
    elif s.family == "trend_pullback":
        ma = df.get(f"ma{s.ma_slow}", df["c"].rolling(s.ma_slow).mean())
        recent_high = df.get(f"high{lb}", df["h"].rolling(lb).max().shift(1))
        dd = (df["c"] / recent_high - 1.0) * 100.0
        sig = (df["c"] > ma) & (dd <= s.drawdown_pct) & (df["rsi14"] <= 50)
    elif s.family == "relative_strength" and btc_df is not None:
        ret = df.get(f"ret{lb}_pct", (df["c"] / df["c"].shift(lb) - 1) * 100)
        benchmark_close = aligned_benchmark_close(df, btc_df)
        btc_ret = (benchmark_close / benchmark_close.shift(lb) - 1.0) * 100.0
        ma = df.get("ma50", df["c"].rolling(50).mean())
        sig = (ret >= btc_ret + s.threshold_pct) & (df["c"] > ma)
    elif s.family == "ensemble_breakout_momentum":
        high = df.get(f"high{lb}", df["h"].rolling(lb).max().shift(1))
        ret = df.get(f"ret{max(3, lb // 2)}_pct", (df["c"] / df["c"].shift(max(3, lb // 2)) - 1) * 100)
        sig = (df["c"] > high) & (ret >= s.threshold_pct * 0.5)
    else:
        sig = pd.Series(False, index=df.index)

    sig = (sig & vol_ok).fillna(False)
    warmup = min(n, max(220, s.lookback + 5, s.ma_slow + 5))
    sig.iloc[:warmup] = False
    return sig.to_numpy(dtype=bool)


def summarize(trades, initial=500.0, capital_fraction=0.25):
    if not trades:
        return {
            "trade_count": 0,
            "win_rate_pct": 0.0,
            "weekly_double_trade_count": 0,
            "weekly_double_hit_rate_pct": 0.0,
            "final_capital": initial,
            "net_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "expectancy_pct": 0.0,
            "profit_factor": 0.0,
            "largest_winner_share_pct": 0.0,
            "avg_friction_pct": 0.0,
            "capital_fraction_per_trade": capital_fraction,
            "trades": [],
        }
    wins = [t["net_pct"] for t in trades if t["net_pct"] > 0]
    losses = [t["net_pct"] for t in trades if t["net_pct"] <= 0]
    doubles = [t for t in trades if t["net_pct"] >= 100.0]
    curve = [initial]
    for t in trades:
        deployed = curve[-1] * capital_fraction
        curve.append(curve[-1] + deployed * t["net_pct"] / 100.0)
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else (999.0 if gross_wins > 0 else 0.0)
    largest_winner_share = (max(wins) / gross_wins * 100.0) if wins and gross_wins > 0 else 0.0
    intratrade_dd = min(
        (float(t.get("mae_pct") or 0.0) * capital_fraction for t in trades),
        default=0.0,
    )
    return {
        "trade_count": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 2),
        "weekly_double_trade_count": len(doubles),
        "weekly_double_hit_rate_pct": round(len(doubles) / len(trades) * 100.0, 2),
        "final_capital": round(curve[-1], 2),
        "net_return_pct": round((curve[-1] / curve[0] - 1.0) * 100.0, 2),
        "max_drawdown_pct": round(min(max_drawdown(curve), intratrade_dd), 2),
        "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "expectancy_pct": round(sum(t["net_pct"] for t in trades) / len(trades), 4),
        "profit_factor": round(profit_factor, 4),
        "largest_winner_share_pct": round(largest_winner_share, 2),
        "avg_friction_pct": round(sum(float(t.get("friction_pct") or 0.0) for t in trades) / len(trades), 4),
        "capital_fraction_per_trade": capital_fraction,
        "trades": trades,
    }


def backtest(
    df,
    s: Strategy,
    start_i,
    end_i,
    btc_df=None,
    initial=500.0,
    fee_bps=10,
    slippage_bps=8,
    spread_bps=10,
    capital_fraction=0.25,
):
    sig = signal_array(df, s, btc_df=btc_df)
    commission_pct = fee_bps / 100.0 * 2.0
    slippage_pct = slippage_bps / 100.0 * 2.0
    spread_pct = spread_bps / 100.0
    friction = commission_pct + slippage_pct + spread_pct
    o = df["o"].to_numpy()
    h = df["h"].to_numpy()
    l = df["l"].to_numpy()
    c = df["c"].to_numpy()
    t = df["t"].to_numpy()
    trades = []
    i = max(start_i, 220)
    while i < end_i - 2:
        if not sig[i]:
            i += 1
            continue
        entry_i = i + 1
        entry = o[entry_i]
        exit_i = min(entry_i + s.hold_bars, end_i - 1)
        reason = "time"
        gross = pct(entry, c[exit_i])
        for j in range(entry_i, min(entry_i + s.hold_bars, end_i - 1) + 1):
            if pct(entry, l[j]) <= s.stop_pct:
                exit_i = j
                gross = s.stop_pct
                reason = "stop"
                break
            if pct(entry, h[j]) >= s.take_pct:
                exit_i = j
                gross = s.take_pct
                reason = "take"
                break
        window_end = max(entry_i, exit_i)
        mae_pct = min((pct(entry, value) for value in l[entry_i : window_end + 1]), default=0.0)
        mfe_pct = max((pct(entry, value) for value in h[entry_i : window_end + 1]), default=0.0)
        exit_price = entry * (1.0 + gross / 100.0) if reason in {"stop", "take"} else c[exit_i]
        trades.append(
            {
                "signal_time": int(t[i]),
                "entry_time": int(t[entry_i]),
                "exit_time": int(t[exit_i]),
                "entry": float(entry),
                "exit": float(exit_price),
                "gross_pct": float(gross),
                "net_pct": float(gross - friction),
                "mae_pct": float(mae_pct),
                "mfe_pct": float(mfe_pct),
                "holding_bars": int(exit_i - entry_i + 1),
                "commission_round_trip_pct": float(commission_pct),
                "slippage_round_trip_pct": float(slippage_pct),
                "spread_round_trip_pct": float(spread_pct),
                "friction_pct": float(friction),
                "same_bar_stop_first_policy": True,
                "reason": reason,
            }
        )
        i = exit_i + 1
    return summarize(trades, initial=initial, capital_fraction=capital_fraction)


def param_grid(interval):
    if interval == "15m":
        lookbacks = [16, 48, 96]
        holds = [4, 16, 48]
    elif interval == "1h":
        lookbacks = [24, 72, 168]
        holds = [24, 72, 168]
    elif interval == "4h":
        lookbacks = [12, 42, 84]
        holds = [6, 18, 42]
    else:
        lookbacks = [7, 21, 60]
        holds = [2, 4, 7]
    stops = [-8.0, -12.0, -20.0]
    takes = [20.0, 35.0, 50.0, 100.0, 150.0]
    volume_mults = [1.0, 1.5, 2.0]
    strategies = []
    families = [
        "breakout",
        "momentum",
        "pump_continuation",
        "squeeze_breakout",
        "dip_reversal",
        "crash_rebound",
        "liquidation_wick_reversal",
        "trend_pullback",
        "relative_strength",
        "ensemble_breakout_momentum",
    ]
    for family in families:
        selloff_family = family in ["crash_rebound", "liquidation_wick_reversal"]
        family_lookbacks = lookbacks[:2] if selloff_family else lookbacks
        family_holds = holds[:2] if selloff_family else holds
        family_stops = [-8.0, -12.0] if selloff_family else stops
        family_takes = [20.0, 35.0] if selloff_family else takes
        family_volume_mults = [1.0, 1.5] if selloff_family else volume_mults
        for lb in family_lookbacks:
            for hold in family_holds:
                for stop in family_stops:
                    for take in family_takes:
                        for vm in family_volume_mults:
                            threshold_values = (
                                [5.0, 10.0, 20.0]
                                if family in ["momentum", "pump_continuation", "relative_strength", "ensemble_breakout_momentum"]
                                else [3.0, 5.0]
                                if family in ["crash_rebound", "liquidation_wick_reversal"]
                                else [5.0]
                            )
                            drawdown_values = (
                                [-5.0, -8.0, -12.0]
                                if family in ["crash_rebound", "liquidation_wick_reversal"]
                                else [-8.0, -15.0, -25.0]
                                if family in ["dip_reversal", "trend_pullback"]
                                else [-10.0]
                            )
                            rsi_values = [45.0, 55.0] if family in ["crash_rebound", "liquidation_wick_reversal"] else [40.0]
                            squeeze_values = [0.55, 0.65] if family == "liquidation_wick_reversal" else [0.55]
                            for th in threshold_values:
                                for dd in drawdown_values:
                                    for rsi in rsi_values:
                                        for squeeze in squeeze_values:
                                            strategies.append(
                                                Strategy(
                                                    family=family,
                                                    interval=interval,
                                                    lookback=lb,
                                                    hold_bars=hold,
                                                    stop_pct=stop,
                                                    take_pct=take,
                                                    volume_mult=vm,
                                                    threshold_pct=th,
                                                    drawdown_pct=dd,
                                                    rsi_max=rsi,
                                                    squeeze_ratio=squeeze,
                                                )
                                            )
    return strategies


def train_score(res):
    tc = res["trade_count"]
    if tc < 8:
        return -999999
    return (
        res["net_return_pct"]
        + res["win_rate_pct"] * 0.10
        + res.get("expectancy_pct", 0.0) * 6.0
        + min(res.get("profit_factor", 0.0), 3.0) * 5.0
        + min(tc, 100) * 0.1
        + res["max_drawdown_pct"] * 2.0
        - max(0.0, res.get("largest_winner_share_pct", 0.0) - 40.0) * 0.5
    )


def segment_quality(summary, min_trades, min_win_rate=0.0):
    return (
        summary.get("trade_count", 0) >= min_trades
        and summary.get("net_return_pct", 0.0) > 0.0
        and summary.get("expectancy_pct", 0.0) > 0.0
        and summary.get("profit_factor", 0.0) > 1.0
        and summary.get("max_drawdown_pct", -999.0) >= -15.0
        and summary.get("win_rate_pct", 0.0) >= min_win_rate
    )


def selection_score(train, validation):
    if train.get("trade_count", 0) < 8 or validation.get("trade_count", 0) < 4:
        return -999999.0
    worst_net = min(train.get("net_return_pct", 0.0), validation.get("net_return_pct", 0.0))
    worst_expectancy = min(train.get("expectancy_pct", 0.0), validation.get("expectancy_pct", 0.0))
    worst_win = min(train.get("win_rate_pct", 0.0), validation.get("win_rate_pct", 0.0))
    worst_dd = min(train.get("max_drawdown_pct", 0.0), validation.get("max_drawdown_pct", 0.0))
    concentration = max(
        train.get("largest_winner_share_pct", 0.0),
        validation.get("largest_winner_share_pct", 0.0),
    )
    return (
        worst_net
        + worst_expectancy * 8.0
        + worst_win * 0.15
        + worst_dd * 2.0
        - max(0.0, concentration - 40.0) * 0.75
    )


def classify(train, test, current_signal, initial=500.0, validation=None):
    validation = validation or train
    train_ok = segment_quality(train, 8, 45.0)
    validation_ok = segment_quality(validation, 4, 45.0)
    test_ok = segment_quality(test, 6, 50.0)
    robust_positive = train_ok and validation_ok and test_ok
    cap_double = (
        robust_positive
        and test.get("net_return_pct", 0.0) >= 100.0
        and test.get("trade_count", 0) >= 20
        and test.get("win_rate_pct", 0.0) >= 55.0
        and test.get("largest_winner_share_pct", 100.0) <= 40.0
    )
    high_quality = robust_positive and test.get("trade_count", 0) >= 10 and test.get("win_rate_pct", 0.0) >= 55.0
    if cap_double and current_signal and high_quality:
        return "target_research_pass_current_signal"
    if robust_positive and current_signal:
        return "paper_forward_candidate_current_signal"
    if cap_double and high_quality:
        return "target_research_pass_wait_signal"
    if test.get("net_return_pct", 0.0) >= 100.0:
        return "paper_only_goal_history_not_robust"
    if test.get("final_capital", 0.0) > initial and test.get("trade_count", 0) >= 6:
        return "paper_only"
    if test.get("trade_count", 0) > 0:
        return "research_watch"
    return "failed"


def validate_strategy_segments(df, strategy, btc_df=None, initial=500.0):
    train_end = int(len(df) * 0.50)
    validation_end = int(len(df) * 0.75)
    train = backtest(df, strategy, 0, train_end, btc_df=btc_df, initial=initial)
    validation = backtest(df, strategy, train_end, validation_end, btc_df=btc_df, initial=initial)
    holdout = backtest(df, strategy, validation_end, len(df), btc_df=btc_df, initial=initial)
    current_signal = bool(signal_array(df, strategy, btc_df=btc_df)[len(df) - 1])
    return {
        "train": train,
        "validation": validation,
        "holdout": holdout,
        "current_signal": current_signal,
        "selection_score": selection_score(train, validation),
        "stage": classify(train, holdout, current_signal, initial=initial, validation=validation),
        "train_end": train_end,
        "validation_end": validation_end,
        "holdout_used_for_selection": False,
    }


def evaluate(frames, initial=500.0, top_train=25):
    results = []
    for (symbol, interval), df in frames.items():
        if len(df) < 500:
            continue
        train_end = int(len(df) * 0.50)
        validation_end = int(len(df) * 0.75)
        btc_df = frames.get(("BTCUSDT", interval))
        train_results = []
        for s in param_grid(interval):
            if s.family == "relative_strength" and btc_df is None:
                continue
            tr = backtest(df, s, 0, train_end, btc_df=btc_df, initial=initial)
            tr["strategy"] = s.__dict__
            tr["score"] = train_score(tr)
            train_results.append(tr)
        train_results.sort(key=lambda x: x["score"], reverse=True)
        validation_results = []
        for tr in train_results[:top_train]:
            s = Strategy(**tr["strategy"])
            validation = backtest(df, s, train_end, validation_end, btc_df=btc_df, initial=initial)
            validation_results.append(
                {
                    "strategy": s.__dict__,
                    "train": tr,
                    "validation": validation,
                    "selection_score": selection_score(tr, validation),
                }
            )
        validation_results.sort(key=lambda item: item["selection_score"], reverse=True)
        selected = validation_results[0] if validation_results else None
        best = None
        if selected:
            s = Strategy(**selected["strategy"])
            test = backtest(df, s, validation_end, len(df), btc_df=btc_df, initial=initial)
            current = bool(signal_array(df, s, btc_df=btc_df)[len(df) - 1])
            train = selected["train"]
            validation = selected["validation"]
            test["strategy"] = s.__dict__
            test["train_summary"] = {
                key: train[key]
                for key in [
                    "trade_count", "win_rate_pct", "weekly_double_trade_count", "final_capital",
                    "net_return_pct", "max_drawdown_pct", "expectancy_pct", "profit_factor",
                    "largest_winner_share_pct", "score",
                ]
            }
            test["validation_summary"] = {
                key: validation[key]
                for key in [
                    "trade_count", "win_rate_pct", "final_capital", "net_return_pct",
                    "max_drawdown_pct", "expectancy_pct", "profit_factor", "largest_winner_share_pct",
                ]
            }
            test["selection_score"] = round(float(selected["selection_score"]), 4)
            test["current_signal"] = current
            test["stage"] = classify(train, test, current, initial=initial, validation=validation)
            test["holdout_used_for_selection"] = False
            best = test
        results.append(
            {
                "symbol": symbol,
                "interval": interval,
                "bars": len(df),
                "window": [
                    datetime.fromtimestamp(int(df["t"].iloc[0]) / 1000, timezone.utc).isoformat(),
                    datetime.fromtimestamp(int(df["t"].iloc[-1]) / 1000, timezone.utc).isoformat(),
                ],
                "train_end_time": datetime.fromtimestamp(int(df["t"].iloc[train_end]) / 1000, timezone.utc).isoformat(),
                "validation_end_time": datetime.fromtimestamp(int(df["t"].iloc[validation_end]) / 1000, timezone.utc).isoformat(),
                "selection_protocol": "grid_on_train_rank_on_validation_single_final_holdout",
                "best_oos": best,
                "top_oos": [best] if best else [],
            }
        )
    return results


def self_test():
    with tempfile.TemporaryDirectory(prefix="weekly_goal_cache_selection_") as tmp_text:
        root = Path(tmp_text)
        old_dir = root / "old"
        new_dir = root / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        def rows(start, close):
            return [
                {
                    "t": start + index * 3_600_000,
                    "o": close,
                    "h": close * 1.01,
                    "l": close * 0.99,
                    "c": close,
                    "qv": 1_000_000.0,
                }
                for index in range(320)
            ]

        old_path = old_dir / "BTCUSDT_1h_1_2.json"
        new_path = new_dir / "BTCUSDT_1h_3_4.json"
        old_path.write_text(json.dumps(rows(1_700_000_000_000, 10.0)), encoding="utf-8")
        new_path.write_text(json.dumps(rows(1_800_000_000_000, 20.0)), encoding="utf-8")
        frames = load_cached_frames([str(root)], symbols={"BTCUSDT"}, intervals={"1h"})
        selected = frames.get(("BTCUSDT", "1h"))
        assert selected is not None and len(selected) == 320, frames.keys()
        assert float(selected["c"].iloc[-1]) == 20.0, selected["c"].iloc[-1]

    bars = []
    start = 1_700_000_000_000
    for index in range(320):
        row = {
            "t": start + index * 3_600_000,
            "o": 100.0,
            "h": 101.0,
            "l": 99.0,
            "c": 100.0,
            "qv": 1_000_000.0,
        }
        bars.append(row)
    bars[230].update({"o": 100.0, "h": 111.0, "l": 99.0, "c": 110.0, "qv": 2_000_000.0})
    bars[231].update({"o": 110.0, "h": 140.0, "l": 100.0, "c": 120.0, "qv": 2_000_000.0})
    frame = prepare_frame(pd.DataFrame(bars))
    strategy = Strategy(
        family="breakout",
        interval="1h",
        lookback=24,
        hold_bars=24,
        stop_pct=-8.0,
        take_pct=20.0,
    )
    execution = backtest(frame, strategy, 220, 250, initial=500.0)
    assert execution["trade_count"] == 1, execution
    trade = execution["trades"][0]
    assert trade["signal_time"] == bars[230]["t"] and trade["entry_time"] == bars[231]["t"], trade
    assert trade["reason"] == "stop" and trade["gross_pct"] == -8.0, trade
    assert round(trade["friction_pct"], 4) == 0.46 and trade["net_pct"] < trade["gross_pct"], trade

    allocation = summarize(
        [{"net_pct": 10.0, "mae_pct": -5.0, "friction_pct": 0.0}],
        initial=500.0,
        capital_fraction=0.25,
    )
    assert allocation["final_capital"] == 512.5, allocation

    benchmark_df = pd.DataFrame({"t": [10, 20], "c": [100.0, 101.0]})
    target_df = pd.DataFrame({"t": [11, 20], "c": [5.0, 6.0]})
    aligned = aligned_benchmark_close(target_df, benchmark_df)
    assert pd.isna(aligned.iloc[0]) and aligned.iloc[1] == 101.0, aligned

    weak_validation = {
        "trade_count": 8, "net_return_pct": -2.0, "expectancy_pct": -0.2,
        "win_rate_pct": 40.0, "max_drawdown_pct": -4.0, "largest_winner_share_pct": 30.0,
    }
    robust_segment = {
        "trade_count": 8, "net_return_pct": 5.0, "expectancy_pct": 0.5,
        "win_rate_pct": 55.0, "max_drawdown_pct": -4.0, "largest_winner_share_pct": 30.0,
    }
    assert selection_score(robust_segment, robust_segment) > selection_score(robust_segment, weak_validation)

    return {
        "status": "ok",
        "recursive_durable_cache_discovery_verified": True,
        "latest_window_deterministic_selection_verified": True,
        "next_bar_entry_verified": True,
        "same_bar_stop_first_verified": True,
        "friction_verified": True,
        "fractional_capital_compounding_verified": True,
        "timestamp_aligned_benchmark_verified": True,
        "validation_selection_score_verified": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dirs", default=",".join(DEFAULT_CACHE_DIRS))
    ap.add_argument("--symbols", default="")
    ap.add_argument("--intervals", default="1h,4h,1d")
    ap.add_argument("--initial", type=float, default=500.0)
    ap.add_argument("--top-train", type=int, default=25)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None
    intervals = {s.strip() for s in args.intervals.split(",") if s.strip()} or None
    cache_dirs = [s.strip() for s in args.cache_dirs.split(",") if s.strip()]
    frames = load_cached_frames(cache_dirs, symbols=symbols, intervals=intervals)
    results = evaluate(frames, initial=args.initial, top_train=args.top_train)
    stage_counts = {}
    for r in results:
        stage = (r.get("best_oos") or {}).get("stage", "failed")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    ranked = [r for r in results if r.get("best_oos")]
    ranked.sort(
        key=lambda r: (
            r["best_oos"].get("stage") in {"target_research_pass_current_signal", "target_research_pass_wait_signal"},
            r["best_oos"].get("stage") == "paper_forward_candidate_current_signal",
            r["best_oos"].get("selection_score", -999999.0),
            r["best_oos"].get("net_return_pct", -999999.0),
        ),
        reverse=True,
    )
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "private_api_keys_used": False,
        "live_orders_enabled": False,
        "initial_capital": args.initial,
        "weekly_double_target_capital": args.initial * 2,
        "validation_protocol": "grid_on_first_50pct_rank_on_next_25pct_single_final_25pct_holdout",
        "holdout_used_for_selection": False,
        "execution_assumptions": {
            "capital_fraction_per_trade": 0.25,
            "commission_bps_per_side": 10,
            "slippage_bps_per_side": 8,
            "round_trip_spread_bps": 10,
            "same_bar_stop_take_policy": "conservative_stop_first",
            "entry_timing": "next_bar_open_after_signal_close",
            "overlapping_positions_per_strategy": False,
        },
        "cache_dirs": cache_dirs,
        "frames_loaded": len(frames),
        "strategy_families": [
            "breakout",
            "momentum",
            "pump_continuation",
            "squeeze_breakout",
            "dip_reversal",
            "crash_rebound",
            "liquidation_wick_reversal",
            "trend_pullback",
            "relative_strength",
            "ensemble_breakout_momentum",
        ],
        "stage_counts": stage_counts,
        "results": results,
        "top_ranked": ranked[:25],
        "decision_rule": "Final holdout never selects parameters. Target research pass requires positive train/validation/holdout quality, <=15% drawdown, diversified wins, and remains paper-only.",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
