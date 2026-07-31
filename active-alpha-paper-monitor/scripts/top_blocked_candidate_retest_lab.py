#!/usr/bin/env python3
"""Retest top blocked paper candidates with stricter alternative entry modes.

Research-only:
- reads latest validation runner no-entry diagnostics
- fetches public Binance klines for the blocked symbols/intervals
- simulates simple alternative paper-only entry modes with friction
- writes a report/experiment; never mutates the paper ledger or opens orders
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
EXPERIMENTS_DIR = ACTIVE_ROOT / "experiments"
REPORTS_DIR = ACTIVE_ROOT / "reports"
UA = "Mozilla/5.0 (compatible; active-alpha-paper-monitor/top-blocked-retest; paper-only)"
CHINA_TZ = dt.timezone(dt.timedelta(hours=8))


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def now_local() -> dt.datetime:
    return now_utc().astimezone(CHINA_TZ)


def stamp(value: dt.datetime) -> str:
    return value.strftime("%Y%m%d-%H%M%S")


def rel(path: Path | None) -> str | None:
    if not path:
        return None
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path | None, default: Any = None) -> Any:
    if not path or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def latest(pattern: str) -> Path | None:
    files = sorted(EXPERIMENTS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def has_top_blocked_candidates(payload: dict[str, Any]) -> bool:
    no_entry = payload.get("no_entry_summary") if isinstance(payload.get("no_entry_summary"), dict) else {}
    candidates = no_entry.get("top_blocked_candidates") if isinstance(no_entry, dict) else []
    return bool([item for item in (candidates or []) if isinstance(item, dict)])


def runner_is_safe_paper_source(payload: dict[str, Any]) -> bool:
    if payload.get("live_orders_enabled") is True:
        return False
    if payload.get("private_api_used") is True:
        return False
    if payload.get("allow_real_orders") is True:
        return False
    if payload.get("safety_errors"):
        return False
    return True


def latest_candidate_runner(directory: Path = EXPERIMENTS_DIR, scan_limit: int = 80) -> tuple[Path | None, dict[str, Any], dict[str, Any]]:
    files = sorted(directory.glob("*validation-progress-runner.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest_any = files[0] if files else None
    skipped: list[dict[str, Any]] = []
    for path in files[: max(1, scan_limit)]:
        payload = read_json(path, {})
        if not runner_is_safe_paper_source(payload):
            skipped.append({"artifact": rel(path), "reason": "unsafe_or_live_flags"})
            continue
        if has_top_blocked_candidates(payload):
            no_entry = payload.get("no_entry_summary") or {}
            return path, payload, {
                "status": "candidate_bearing_runner_selected",
                "artifact": rel(path),
                "candidate_count": len(no_entry.get("top_blocked_candidates") or []),
                "scanned_runner_count": min(len(files), scan_limit),
                "latest_any_runner": rel(latest_any),
                "skipped": skipped[:10],
            }
        skipped.append({"artifact": rel(path), "reason": "no_top_blocked_candidates"})
    fallback_payload = read_json(latest_any, {}) if latest_any else {}
    return latest_any, fallback_payload, {
        "status": "fallback_no_candidate_runner",
        "artifact": rel(latest_any),
        "candidate_count": 0,
        "scanned_runner_count": min(len(files), scan_limit),
        "latest_any_runner": rel(latest_any),
        "skipped": skipped[:10],
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def fetch_klines(symbol: str, interval: str, limit: int) -> tuple[list[dict[str, float]], dict[str, Any]]:
    params = urllib.parse.urlencode({"symbol": symbol.upper(), "interval": interval, "limit": limit})
    urls = [
        f"https://data-api.binance.vision/api/v3/klines?{params}",
        f"https://api.binance.com/api/v3/klines?{params}",
    ]
    attempts: list[dict[str, Any]] = []
    last_error = ""
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            rows = [
                {
                    "t": float(row[0]),
                    "o": float(row[1]),
                    "h": float(row[2]),
                    "l": float(row[3]),
                    "c": float(row[4]),
                    "v": float(row[5]),
                    "ct": float(row[6]),
                    "qv": float(row[7]),
                    "n": float(row[8]),
                }
                for row in raw
            ]
            return rows, {"status": "ok", "url": url, "attempts": attempts, "bars": len(rows)}
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            attempts.append({"url": url, "error": last_error[-300:]})
    return [], {"status": "failed", "attempts": attempts, "error": last_error[-300:]}


def max_drawdown_pct(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = min(worst, (value / peak - 1.0) * 100.0)
    return round(worst, 6)


def pct_return(a: float, b: float) -> float:
    return (b / a - 1.0) * 100.0 if a else 0.0


def median(values: list[float], default: float = 0.0) -> float:
    return statistics.median(values) if values else default


def simulate_breakout(rows: list[dict[str, float]], lookback: int, hold_bars: int, stop_pct: float, take_pct: float, volume_multiple: float, friction_pct: float) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    i = max(lookback + 1, 25)
    while i < len(rows) - 2:
        prev_high = max(row["h"] for row in rows[i - lookback:i])
        vol_med = median([row["qv"] for row in rows[max(0, i - lookback):i]], 1.0)
        row = rows[i]
        if row["c"] > prev_high and row["qv"] >= vol_med * volume_multiple:
            entry = row["c"] * (1.0 + friction_pct / 200.0)
            stop = entry * (1.0 + stop_pct / 100.0)
            take = entry * (1.0 + take_pct / 100.0)
            exit_price = rows[min(i + hold_bars, len(rows) - 1)]["c"]
            exit_idx = min(i + hold_bars, len(rows) - 1)
            reason = "time_exit"
            for j in range(i + 1, min(i + hold_bars + 1, len(rows))):
                if rows[j]["l"] <= stop:
                    exit_price = stop
                    exit_idx = j
                    reason = "stop_loss"
                    break
                if rows[j]["h"] >= take:
                    exit_price = take
                    exit_idx = j
                    reason = "take_profit"
                    break
            exit_net = exit_price * (1.0 - friction_pct / 200.0)
            ret = pct_return(entry, exit_net)
            trades.append({"entry_idx": i, "exit_idx": exit_idx, "return_pct": round(ret, 6), "exit_reason": reason})
            i = exit_idx + 1
        else:
            i += 1
    return trades


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(out[-1] * (1.0 - alpha) + value * alpha)
    return out


def simulate_pullback_reclaim(rows: list[dict[str, float]], hold_bars: int, stop_pct: float, take_pct: float, friction_pct: float) -> list[dict[str, Any]]:
    closes = [row["c"] for row in rows]
    ema20 = ema(closes, 20)
    trades: list[dict[str, Any]] = []
    i = 25
    while i < len(rows) - 2:
        prior = rows[i - 1]
        row = rows[i]
        reclaim = prior["c"] < ema20[i - 1] and row["c"] > ema20[i] and row["c"] > row["o"]
        recent_trend = pct_return(rows[max(0, i - 24)]["c"], row["c"]) > 0
        if reclaim and recent_trend:
            entry = row["c"] * (1.0 + friction_pct / 200.0)
            stop = entry * (1.0 + stop_pct / 100.0)
            take = entry * (1.0 + take_pct / 100.0)
            exit_price = rows[min(i + hold_bars, len(rows) - 1)]["c"]
            exit_idx = min(i + hold_bars, len(rows) - 1)
            reason = "time_exit"
            for j in range(i + 1, min(i + hold_bars + 1, len(rows))):
                if rows[j]["l"] <= stop:
                    exit_price = stop
                    exit_idx = j
                    reason = "stop_loss"
                    break
                if rows[j]["h"] >= take:
                    exit_price = take
                    exit_idx = j
                    reason = "take_profit"
                    break
            exit_net = exit_price * (1.0 - friction_pct / 200.0)
            ret = pct_return(entry, exit_net)
            trades.append({"entry_idx": i, "exit_idx": exit_idx, "return_pct": round(ret, 6), "exit_reason": reason})
            i = exit_idx + 1
        else:
            i += 1
    return trades


def summarize_trades(trades: list[dict[str, Any]], split_start: int = 0) -> dict[str, Any]:
    subset = [trade for trade in trades if trade.get("entry_idx", 0) >= split_start]
    returns = [safe_float(trade.get("return_pct")) for trade in subset]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    equity = [100.0]
    for value in returns:
        equity.append(equity[-1] * (1.0 + value / 100.0))
    return {
        "trade_count": len(returns),
        "win_rate_pct": round(len(wins) / len(returns) * 100.0, 4) if returns else 0.0,
        "net_return_pct": round((equity[-1] / 100.0 - 1.0) * 100.0, 6) if equity else 0.0,
        "avg_return_pct": round(statistics.mean(returns), 6) if returns else 0.0,
        "avg_win_pct": round(statistics.mean(wins), 6) if wins else 0.0,
        "avg_loss_pct": round(statistics.mean(losses), 6) if losses else 0.0,
        "max_drawdown_pct": max_drawdown_pct(equity),
        "take_profit_count": len([trade for trade in subset if trade.get("exit_reason") == "take_profit"]),
        "stop_loss_count": len([trade for trade in subset if trade.get("exit_reason") == "stop_loss"]),
    }


def interval_params(interval: str) -> dict[str, Any]:
    if interval == "15m":
        return {"lookback": 32, "hold_bars": 16, "stop_pct": -3.5, "take_pct": 7.0}
    if interval == "1h":
        return {"lookback": 24, "hold_bars": 18, "stop_pct": -4.5, "take_pct": 9.0}
    return {"lookback": 20, "hold_bars": 12, "stop_pct": -5.0, "take_pct": 10.0}


def current_setup(rows: list[dict[str, float]], lookback: int) -> dict[str, Any]:
    if len(rows) <= lookback + 2:
        return {"status": "insufficient_rows"}
    current = rows[-1]
    prev = rows[-2]
    recent = rows[-lookback - 1:-1]
    high = max(row["h"] for row in recent)
    vol_med = median([row["qv"] for row in recent], 1.0)
    volume_ratio = current["qv"] / vol_med if vol_med else 0.0
    return {
        "status": "ok",
        "current_price": round(current["c"], 10),
        "breakout_level": round(high, 10),
        "distance_to_breakout_pct": round(pct_return(current["c"], high), 6),
        "current_volume_ratio": round(volume_ratio, 6),
        "current_candle_return_pct": round(pct_return(prev["c"], current["c"]), 6) if prev["c"] else 0.0,
        "recent_return_pct": round(pct_return(rows[max(0, len(rows) - lookback)]["c"], current["c"]), 6),
        "recent_max_drawdown_pct": max_drawdown_pct([row["c"] for row in rows[-lookback:]]),
    }


def retest_decision(best_variant: dict[str, Any], setup: dict[str, Any], source_block: str) -> tuple[str, list[str]]:
    oos = best_variant.get("oos") or {}
    reasons: list[str] = []
    if oos.get("trade_count", 0) < 3:
        reasons.append("oos_trade_count_below_3")
    if safe_float(oos.get("win_rate_pct")) < 55.0:
        reasons.append("oos_win_rate_below_55")
    if safe_float(oos.get("net_return_pct")) <= 0.0:
        reasons.append("oos_net_return_not_positive")
    if safe_float(oos.get("max_drawdown_pct")) < -15.0:
        reasons.append("oos_drawdown_deeper_than_15")
    if setup.get("status") != "ok":
        reasons.append("current_setup_unavailable")
    elif setup.get("distance_to_breakout_pct", 999.0) > 2.5 and "15m" in source_block:
        reasons.append("not_near_breakout_for_weak_interval_retest")
    if reasons:
        return "keep_blocked_retest_failed", reasons
    return "candidate_for_min_quality_scout_after_current_signal", ["strict_retest_passed_but_still_paper_only"]


def build_candidate_result(candidate: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    symbol = str(candidate.get("symbol") or "").upper()
    interval = str(candidate.get("interval") or "1h")
    params = interval_params(interval)
    rows, meta = fetch_klines(symbol, interval, args.kline_limit)
    result: dict[str, Any] = {
        "symbol": symbol,
        "stage": candidate.get("stage"),
        "interval": interval,
        "strategy_family": candidate.get("strategy_family"),
        "entry_mode_estimate": candidate.get("entry_mode_estimate"),
        "source_block_reason": candidate.get("primary_block_reason"),
        "selection_score": candidate.get("selection_score"),
        "source_oos_win_rate_pct": candidate.get("oos_win_rate_pct"),
        "source_oos_net_return_pct": candidate.get("oos_net_return_pct"),
        "market_regime": candidate.get("market_regime") or "unknown_or_not_attached",
        "market_atmosphere": candidate.get("market_atmosphere") or "unknown_or_not_attached",
        "short_term_state": candidate.get("short_term_state") or "unknown_or_not_attached",
        "sentiment_state": candidate.get("sentiment_state") or "unknown_or_not_attached",
        "market_context_source": candidate.get("market_context_source") or "top_blocked_candidate_or_unknown",
        "market_context": candidate.get("market_context") or {
            "market_regime": candidate.get("market_regime") or "unknown_or_not_attached",
            "market_atmosphere": candidate.get("market_atmosphere") or "unknown_or_not_attached",
            "short_term_state": candidate.get("short_term_state") or "unknown_or_not_attached",
            "sentiment_state": candidate.get("sentiment_state") or "unknown_or_not_attached",
            "source": candidate.get("market_context_source") or "top_blocked_candidate_or_unknown",
        },
        "data_status": meta.get("status"),
        "kline_meta": meta,
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    if len(rows) < max(120, params["lookback"] + 50):
        result.update({"decision": "keep_blocked_data_insufficient", "decision_reasons": ["insufficient_kline_rows"], "variants": []})
        return result
    friction_pct = args.commission_bps / 100.0 + args.slippage_bps / 100.0 + args.spread_bps / 100.0
    split_start = int(len(rows) * 0.7)
    variants = []
    breakout = simulate_breakout(
        rows,
        lookback=params["lookback"],
        hold_bars=params["hold_bars"],
        stop_pct=params["stop_pct"],
        take_pct=params["take_pct"],
        volume_multiple=args.volume_multiple,
        friction_pct=friction_pct,
    )
    pullback = simulate_pullback_reclaim(
        rows,
        hold_bars=params["hold_bars"],
        stop_pct=params["stop_pct"],
        take_pct=params["take_pct"],
        friction_pct=friction_pct,
    )
    for name, trades in [("strict_breakout_retest", breakout), ("pullback_reclaim_retest", pullback)]:
        variants.append(
            {
                "variant": name,
                "params": params | {"volume_multiple": args.volume_multiple, "friction_pct": round(friction_pct, 6)},
                "all": summarize_trades(trades, 0),
                "oos": summarize_trades(trades, split_start),
            }
        )
    variants.sort(key=lambda item: (safe_float((item.get("oos") or {}).get("net_return_pct")), safe_float((item.get("oos") or {}).get("win_rate_pct"))), reverse=True)
    setup = current_setup(rows, params["lookback"])
    decision, reasons = retest_decision(variants[0], setup, str(candidate.get("primary_block_reason") or ""))
    result.update(
        {
            "bars_loaded": len(rows),
            "current_setup": setup,
            "variants": variants,
            "best_variant": variants[0],
            "decision": decision,
            "decision_reasons": reasons,
            "recommended_next_action": (
                "paper_only_quality_scout_review"
                if decision == "candidate_for_min_quality_scout_after_current_signal"
                else "keep_blocked_and_collect_more_evidence"
            ),
        }
    )
    return result


def latest_runner_payload(path: Path | None = None) -> tuple[Path | None, dict[str, Any], dict[str, Any]]:
    if path:
        payload = read_json(path, {})
        no_entry = payload.get("no_entry_summary") if isinstance(payload.get("no_entry_summary"), dict) else {}
        return path, payload, {
            "status": "explicit_runner_json",
            "artifact": rel(path),
            "candidate_count": len(no_entry.get("top_blocked_candidates") or []),
            "latest_any_runner": rel(latest("*validation-progress-runner.json")),
        }
    return latest_candidate_runner()


def render_report(record: dict[str, Any]) -> str:
    lines = [
        f"# Top Blocked Candidate Retest Lab | {record['run_id']}",
        "",
        "- scope: `paper_only_research`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        "- ledger_mutated: `false`",
        f"- source_runner: `{record.get('source_runner')}`",
        f"- source_selection_status: `{(record.get('source_selection') or {}).get('status')}`",
        "",
        "## Summary",
        "",
        f"- status: `{record.get('status')}`",
        f"- candidates_seen: `{record.get('candidates_seen')}`",
        f"- quality_scout_review_count: `{record.get('quality_scout_review_count')}`",
        f"- keep_blocked_count: `{record.get('keep_blocked_count')}`",
        "",
        "## Retest Results",
        "",
        "| Symbol | Interval | Block | Best Variant | OOS Trades | OOS Win % | OOS Net % | OOS DD % | Current Setup | Decision |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for item in record.get("results") or []:
        best = item.get("best_variant") or {}
        oos = best.get("oos") or {}
        setup = item.get("current_setup") or {}
        setup_text = (
            f"dist {setup.get('distance_to_breakout_pct')}%, vol {setup.get('current_volume_ratio')}x"
            if setup.get("status") == "ok"
            else setup.get("status")
        )
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('interval')}` | `{item.get('source_block_reason')}` | "
            f"`{best.get('variant')}` | `{oos.get('trade_count')}` | `{oos.get('win_rate_pct')}` | "
            f"`{oos.get('net_return_pct')}` | `{oos.get('max_drawdown_pct')}` | "
            f"{setup_text} | `{item.get('decision')}` |"
        )
    if not record.get("results"):
        lines.append("| - | - | - | - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "This lab does not open paper positions. A `paper_only_quality_scout_review` result only means a future sampler may consider a tiny paper scout after fresh current-signal, liquidity, capacity and recovery gates pass.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_record(args: argparse.Namespace) -> dict[str, Any]:
    created = now_local()
    runner_path, runner, source_selection = latest_runner_payload(args.runner_json)
    no_entry = runner.get("no_entry_summary") if isinstance(runner.get("no_entry_summary"), dict) else {}
    candidates = [item for item in (no_entry.get("top_blocked_candidates") or []) if isinstance(item, dict)]
    candidates = candidates[: max(1, args.max_candidates)]
    results = [build_candidate_result(candidate, args) for candidate in candidates]
    quality = [item for item in results if item.get("decision") == "candidate_for_min_quality_scout_after_current_signal"]
    blocked = [item for item in results if item.get("decision") != "candidate_for_min_quality_scout_after_current_signal"]
    return {
        "run_id": f"{stamp(created)}-top-blocked-candidate-retest-lab",
        "created_at": created.isoformat(),
        "scope": "paper_only_top_blocked_candidate_retest",
        "status": "ok",
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "source_runner": rel(runner_path),
        "source_selection": source_selection,
        "candidates_seen": len(candidates),
        "quality_scout_review_count": len(quality),
        "keep_blocked_count": len(blocked),
        "results": results,
        "operator_note": "Research-only retest evidence. It does not mutate config, open paper trades, or authorize live trading.",
    }


def synthetic_rows(count: int = 260) -> list[list[Any]]:
    start = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    rows = []
    price = 100.0
    for i in range(count):
        jump = 1.08 if i % 30 == 0 and i > 40 else 1.0
        drift = 1.002 * jump
        o = price
        c = price * drift
        h = max(o, c) * 1.015
        l = min(o, c) * 0.985
        qv = (1000 + i * 10) * c * (2.0 if jump > 1 else 1.0)
        rows.append([start + i * 3600000, str(o), str(h), str(l), str(c), "100", start + (i + 1) * 3600000 - 1, str(qv), 100])
        price = c
    return rows


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="top-blocked-retest-") as tmp:
        tmp_path = Path(tmp)
        runner_path = tmp_path / "runner.json"
        experiments_dir = tmp_path / "experiments"
        experiments_dir.mkdir(parents=True, exist_ok=True)
        newest_empty = experiments_dir / "20260627-200000-validation-progress-runner.json"
        older_candidate = experiments_dir / "20260627-190000-validation-progress-runner.json"
        newest_empty.write_text(json.dumps({"run_id": "newest-empty", "no_entry_summary": {"top_blocked_candidates": []}}), encoding="utf-8")
        older_candidate_payload = {
            "run_id": "older-candidate",
            "no_entry_summary": {"top_blocked_candidates": [{"symbol": "TESTUSDT"}]},
            "live_orders_enabled": False,
            "private_api_used": False,
        }
        older_candidate.write_text(json.dumps(older_candidate_payload), encoding="utf-8")
        selected_path, _selected_payload, selected_meta = latest_candidate_runner(experiments_dir)
        candidate_source_selection_ok = selected_path == older_candidate and selected_meta.get("status") == "candidate_bearing_runner_selected"
        runner = {
            "run_id": "self-test-runner",
            "no_entry_summary": {
                "top_blocked_candidates": [
                    {
                        "symbol": "TESTUSDT",
                        "stage": "paper_only",
                        "interval": "1h",
                        "strategy_family": "momentum",
                        "entry_mode_estimate": "info_exploratory_probe",
                        "selection_score": 123,
                        "oos_win_rate_pct": 60,
                        "oos_net_return_pct": 25,
                        "primary_block_reason": "validation_recovery_plan_block:info_exploratory_probe",
                        "market_regime": "risk_on_momentum",
                        "market_atmosphere": "broad_risk_appetite",
                        "short_term_state": "neutral",
                        "sentiment_state": "positive_catalyst_cluster",
                        "market_context_source": "self_test_dynamic_scan",
                        "market_context": {
                            "market_regime": "risk_on_momentum",
                            "market_atmosphere": "broad_risk_appetite",
                            "short_term_state": "neutral",
                            "sentiment_state": "positive_catalyst_cluster",
                            "source": "self_test_dynamic_scan",
                        },
                    }
                ]
            },
        }
        write_json(runner_path, runner)
        original = urllib.request.urlopen

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(synthetic_rows()).encode("utf-8")

        def fake_urlopen(_req, timeout=20):  # noqa: ARG001
            return FakeResp()

        urllib.request.urlopen = fake_urlopen
        try:
            args = argparse.Namespace(
                runner_json=runner_path,
                max_candidates=1,
                kline_limit=260,
                commission_bps=10.0,
                slippage_bps=8.0,
                spread_bps=4.0,
                volume_multiple=1.1,
            )
            record = build_record(args)
        finally:
            urllib.request.urlopen = original
        result = record["results"][0]
        context_ok = (
            result.get("market_regime") == "risk_on_momentum"
            and (result.get("market_context") or {}).get("source") == "self_test_dynamic_scan"
        )
        ok = record.get("candidates_seen") == 1 and result.get("data_status") == "ok" and context_ok and candidate_source_selection_ok
        return {
            "status": "ok" if ok else "failed",
            "candidates_seen": record.get("candidates_seen"),
            "market_context_preserved": context_ok,
            "candidate_source_selection_verified": candidate_source_selection_ok,
            "live_orders_enabled": False,
            "private_api_used": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Retest top blocked paper candidates without opening trades.")
    parser.add_argument("--runner-json", type=Path)
    parser.add_argument("--max-candidates", type=int, default=6)
    parser.add_argument("--kline-limit", type=int, default=500)
    parser.add_argument("--commission-bps", type=float, default=10.0)
    parser.add_argument("--slippage-bps", type=float, default=8.0)
    parser.add_argument("--spread-bps", type=float, default=4.0)
    parser.add_argument("--volume-multiple", type=float, default=1.2)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record(args)
    stamp_text = record["run_id"].removesuffix("-top-blocked-candidate-retest-lab")
    report_path = REPORTS_DIR / f"{now_local().strftime('%Y-%m-%d')}-top-blocked-candidate-retest-{stamp_text}.md"
    experiment_path = EXPERIMENTS_DIR / f"{stamp_text}-top-blocked-candidate-retest-lab.json"
    record["outputs"] = {"report": rel(report_path), "experiment": rel(experiment_path)}
    write_json(experiment_path, record)
    write_text(report_path, render_report(record))
    if args.compact_output:
        print(json.dumps({
            "status": record.get("status"),
            "run_id": record.get("run_id"),
            "candidates_seen": record.get("candidates_seen"),
            "quality_scout_review_count": record.get("quality_scout_review_count"),
            "keep_blocked_count": record.get("keep_blocked_count"),
            "source_runner": record.get("source_runner"),
            "source_selection": record.get("source_selection"),
            "live_orders_enabled": False,
            "private_api_used": False,
            "outputs": record.get("outputs"),
        }, ensure_ascii=False, indent=2))
    else:
        print(render_report(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
