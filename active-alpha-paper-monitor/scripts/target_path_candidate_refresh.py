#!/usr/bin/env python3
"""Refresh lightweight target-path research candidates.

Research-only. This script reads cached public Binance klines, runs a bounded
two-step scan, and emits a pressure retest queue for future paper-only forward
checks. It never opens paper positions and never uses private trading APIs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent

import sys

sys.path.insert(0, str(SCRIPT_DIR))
from weekly_goal_strategy_lab import DEFAULT_CACHE_DIRS, evaluate, load_cached_frames  # noqa: E402


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def stamp_minute(now: dt.datetime) -> str:
    return now.strftime("%Y%m%d-%H%M")


def write_json(path: Path, payload: Any, dry_run: bool = False) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str, dry_run: bool = False) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def default_cache_dirs() -> list[str]:
    candidates: list[Path] = []
    for pattern in [
        "/private/tmp/binance_klines_cache_dynamic_*",
        "/private/tmp/binance_klines_cache_validation_*",
        "/private/tmp/binance_klines_cache_v*",
    ]:
        candidates.extend(sorted(Path("/").glob(pattern.lstrip("/"))))
    candidates.extend(Path(path) for path in DEFAULT_CACHE_DIRS)
    seen: set[str] = set()
    existing: list[str] = []
    for path in candidates:
        key = str(path)
        if key in seen or not path.exists():
            continue
        seen.add(key)
        existing.append(key)
    return existing


def frame_prefilter_score(symbol: str, interval: str, df: pd.DataFrame) -> dict[str, Any]:
    closes = pd.to_numeric(df["c"], errors="coerce")
    qv = pd.to_numeric(df["qv"], errors="coerce") if "qv" in df else pd.Series([0.0] * len(df))
    ret_lookback = min(24, max(2, len(df) // 12))
    ret = (closes.iloc[-1] / closes.iloc[-ret_lookback] - 1.0) * 100.0 if len(closes) > ret_lookback and closes.iloc[-ret_lookback] else 0.0
    vol = closes.pct_change().tail(min(96, len(closes))).std(ddof=0) * 100.0
    recent_qv = qv.tail(min(24, len(qv))).mean() if len(qv) else 0.0
    score = abs(float(ret)) * 2.5 + safe_float(vol) * 3.0 + math.log10(max(float(recent_qv), 1.0)) * 2.0
    return {
        "symbol": symbol,
        "interval": interval,
        "bars": int(len(df)),
        "recent_return_pct": round(float(ret), 6),
        "recent_volatility_pct": round(safe_float(vol), 6),
        "recent_quote_volume_avg": round(float(recent_qv), 6),
        "prefilter_score": round(score, 6),
    }


def select_frames(frames: dict[tuple[str, str], pd.DataFrame], max_loaded_frames: int, prefilter_top_frames: int) -> tuple[dict[tuple[str, str], pd.DataFrame], list[dict[str, Any]]]:
    scored = [
        frame_prefilter_score(symbol, interval, df)
        for (symbol, interval), df in frames.items()
        if len(df) >= 500
    ]
    scored.sort(key=lambda row: row["prefilter_score"], reverse=True)
    limit = max_loaded_frames if max_loaded_frames > 0 else prefilter_top_frames
    limit = min(max(limit, 1), max(prefilter_top_frames, 1), len(scored)) if scored else 0
    selected_keys = {(row["symbol"], row["interval"]) for row in scored[:limit]}
    return {key: df for key, df in frames.items() if key in selected_keys}, scored


def stage_rank(stage: str) -> int:
    if str(stage).startswith("target_research_pass"):
        return 4
    if str(stage).startswith("paper_forward_candidate"):
        return 3
    if "paper_only" in str(stage):
        return 2
    if str(stage) == "research_watch":
        return 1
    return 0


def quality_flags(best: dict[str, Any], initial: float) -> list[str]:
    flags: list[str] = []
    train = best.get("train_summary") or {}
    if int(best.get("trade_count") or 0) < 8:
        flags.append("oos_trade_sample_below_8")
    if safe_float(best.get("final_capital")) <= initial:
        flags.append("oos_final_capital_not_above_initial")
    if safe_float(best.get("max_drawdown_pct")) < -45.0:
        flags.append("oos_drawdown_deeper_than_45_pct")
    if int(train.get("trade_count") or 0) < 8:
        flags.append("train_trade_sample_below_8")
    if safe_float(train.get("final_capital")) <= initial:
        flags.append("train_final_capital_not_above_initial")
    return flags


def row_score(row: dict[str, Any], initial: float) -> float:
    best = row.get("best_oos") or {}
    train = best.get("train_summary") or {}
    return round(
        safe_float(best.get("net_return_pct"))
        + safe_float(best.get("win_rate_pct")) * 0.3
        + safe_float(train.get("net_return_pct")) * 0.25
        + stage_rank(best.get("stage")) * 25.0
        + (20.0 if best.get("current_signal") else 0.0)
        + min(int(best.get("trade_count") or 0), 80) * 0.4
        + safe_float(best.get("max_drawdown_pct")) * 1.15,
        6,
    )


def build_pressure_queue(results: list[dict[str, Any]], initial: float, max_queue: int) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    ranked = [row for row in results if isinstance(row.get("best_oos"), dict)]
    ranked.sort(key=lambda row: row_score(row, initial), reverse=True)
    for row in ranked:
        best = row.get("best_oos") or {}
        strategy = best.get("strategy") or {}
        flags = quality_flags(best, initial)
        stage = str(best.get("stage") or "failed")
        if stage == "failed":
            continue
        if "oos_final_capital_not_above_initial" in flags:
            continue
        candidate = {
            "queue_id": f"pressure:{row.get('symbol')}:{row.get('interval')}:{strategy.get('family')}:{strategy.get('lookback')}:{strategy.get('hold_bars')}",
            "symbol": str(row.get("symbol") or "").upper(),
            "interval": row.get("interval"),
            "strategy_family": strategy.get("family"),
            "paper_entry_mode": "pressure_retest_probe",
            "stage": stage,
            "current_signal": bool(best.get("current_signal")),
            "strategy": strategy,
            "train_summary": best.get("train_summary") or {},
            "oos_summary": {
                key: best.get(key)
                for key in [
                    "trade_count",
                    "win_rate_pct",
                    "weekly_double_trade_count",
                    "weekly_double_hit_rate_pct",
                    "final_capital",
                    "net_return_pct",
                    "max_drawdown_pct",
                    "avg_win_pct",
                    "avg_loss_pct",
                ]
            },
            "quality_flags": flags,
            "score": row_score(row, initial),
            "recommended_next_action": "pressure_retest_watch",
            "operator_note": "Research-only pressure queue; future sampler must re-check current signal, liquidity, capacity, and recovery gates.",
        }
        queue.append(candidate)
        if len(queue) >= max_queue:
            break
    return queue


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Target Path Candidate Refresh | {payload['run_id']}",
        "",
        "Research-only. No paper positions or live orders were opened.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| status | `{payload.get('status')}` |",
        f"| live_orders_enabled | `{payload.get('live_orders_enabled')}` |",
        f"| private_api_used | `{payload.get('private_api_used')}` |",
        f"| frames_loaded | `{payload.get('frames_loaded')}` |",
        f"| frames_selected | `{payload.get('frames_selected')}` |",
        f"| strategies_scanned | `{payload.get('strategies_scanned')}` |",
        f"| pressure_queue_count | `{len(payload.get('pressure_retest_queue') or [])}` |",
        "",
        "## Pressure Retest Queue",
        "",
        "| Symbol | Interval | Family | Stage | Current Signal | OOS Return | OOS DD | Action |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for item in payload.get("pressure_retest_queue") or []:
        oos = item.get("oos_summary") or {}
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('interval')}` | `{item.get('strategy_family')}` | "
            f"`{item.get('stage')}` | `{item.get('current_signal')}` | {oos.get('net_return_pct')} | "
            f"{oos.get('max_drawdown_pct')} | `{item.get('recommended_next_action')}` |"
        )
    if not payload.get("pressure_retest_queue"):
        lines.append("| none | - | - | - | - | - | - | - |")
    lines.extend(["", "## Notes", "", payload.get("operator_note", "")])
    return "\n".join(lines) + "\n"


def synthetic_rows(count: int = 620) -> list[dict[str, Any]]:
    start = int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    rows = []
    price = 100.0
    for i in range(count):
        drift = 1.0 + 0.0015 + (0.015 if i % 55 == 0 else 0.0) - (0.008 if i % 83 == 0 else 0.0)
        open_price = price
        close = max(1.0, price * drift)
        high = max(open_price, close) * 1.015
        low = min(open_price, close) * 0.985
        rows.append({"t": start + i * 3600000, "o": open_price, "h": high, "l": low, "c": close, "v": 1000 + i, "ct": start + (i + 1) * 3600000 - 1, "qv": (1000 + i) * close, "n": 100 + i})
        price = close
    return rows


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="target_path_refresh_", dir="/private/tmp") as tmp:
        cache = Path(tmp) / "cache"
        cache.mkdir()
        rows = synthetic_rows()
        for symbol in ["BTCUSDT", "TESTUSDT"]:
            (cache / f"{symbol}_1h_{rows[0]['t']}_{rows[-1]['t']}.json").write_text(json.dumps(rows), encoding="utf-8")
        payload = build_payload(
            argparse.Namespace(
                cache_dirs=str(cache),
                symbols="",
                intervals="1h",
                initial=500.0,
                top_train=4,
                max_loaded_frames=2,
                prefilter_top_frames=2,
                max_pressure_queue=5,
                dry_run=True,
                output="",
                report="",
                compact_output=True,
            )
        )
        ok = payload.get("status") == "ok" and payload.get("frames_loaded", 0) >= 1 and payload.get("live_orders_enabled") is False
        return {"status": "ok" if ok else "failed", "frames_loaded": payload.get("frames_loaded"), "queue_count": len(payload.get("pressure_retest_queue") or [])}


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    now = utc_now()
    run_id = f"{stamp_minute(now)}-lightweight-extended-history-lab"
    cache_dirs = [s.strip() for s in (args.cache_dirs or "").split(",") if s.strip()] or default_cache_dirs()
    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None
    intervals = {s.strip() for s in args.intervals.split(",") if s.strip()} or None
    frames = load_cached_frames(cache_dirs, symbols=symbols, intervals=intervals)
    selected, prefilter = select_frames(frames, args.max_loaded_frames, args.prefilter_top_frames)
    results = evaluate(selected, initial=args.initial, top_train=args.top_train) if selected else []
    stage_counts: dict[str, int] = {}
    for row in results:
        stage = ((row.get("best_oos") or {}).get("stage")) or "failed"
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    top_ranked = [row for row in results if row.get("best_oos")]
    top_ranked.sort(key=lambda row: row_score(row, args.initial), reverse=True)
    pressure_queue = build_pressure_queue(top_ranked, args.initial, args.max_pressure_queue)
    status = "ok" if selected else "degraded_no_frames"
    return {
        "run_id": run_id,
        "generated_at": now.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "lab_version": "lightweight-extended-history-lab-v1",
        "status": status,
        "live_orders_enabled": False,
        "private_api_used": False,
        "private_api_keys_used": False,
        "allow_real_orders": False,
        "cache_dirs": cache_dirs,
        "frames_loaded": len(frames),
        "frames_selected": len(selected),
        "prefilter_top_frames": args.prefilter_top_frames,
        "max_loaded_frames": args.max_loaded_frames,
        "strategies_scanned": sum(len((row.get("top_oos") or [])) for row in results),
        "stage_counts": stage_counts,
        "prefilter_top": prefilter[:25],
        "results": results,
        "top_ranked": top_ranked[:25],
        "pressure_retest_queue": pressure_queue,
        "operator_note": "This artifact is research-only. The queue cannot open paper positions until pressure_retest_queue_sampler re-checks current signal, liquidity, capacity, and recovery gates.",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dirs", default="")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--intervals", default="1h,4h,1d")
    ap.add_argument("--initial", type=float, default=500.0)
    ap.add_argument("--top-train", type=int, default=12)
    ap.add_argument("--max-loaded-frames", type=int, default=24)
    ap.add_argument("--prefilter-top-frames", type=int, default=24)
    ap.add_argument("--max-pressure-queue", type=int, default=12)
    ap.add_argument("--output", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--compact-output", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return

    payload = build_payload(args)
    now = utc_now()
    output = Path(args.output) if args.output else ACTIVE_ROOT / "experiments" / f"{payload['run_id']}.json"
    report = Path(args.report) if args.report else ACTIVE_ROOT / "reports" / f"{now.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()}-target-path-refresh-{stamp_minute(now)}.md"
    payload["outputs"] = {"experiment": str(output), "report": str(report)}
    write_json(output, payload, dry_run=args.dry_run)
    write_text(report, render_report(payload), dry_run=args.dry_run)
    if args.compact_output:
        print(json.dumps({k: payload.get(k) for k in ["run_id", "status", "live_orders_enabled", "frames_loaded", "frames_selected", "strategies_scanned", "stage_counts", "outputs"]} | {"pressure_queue_count": len(payload.get("pressure_retest_queue") or [])}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
