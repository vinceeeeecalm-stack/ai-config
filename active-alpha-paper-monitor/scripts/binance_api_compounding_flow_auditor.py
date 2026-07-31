#!/usr/bin/env python3
"""Aggregate Binance API paper/testnet flow and compounding target evidence.

This script is intentionally read-only with respect to paper ledgers. It does
not fetch market data, place orders, call private APIs, or mutate positions. It
summarizes the latest local artifacts into one answer:

- Can the system simulate an API-style paper/testnet flow?
- Has the paper ledger completed or proven the monthly compounding target?
- What blocks promotion beyond paper-only?
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import multiprocessing as mp
import os
import queue
from pathlib import Path
from typing import Any, Callable

from artifact_shadow import write_shadow_json, write_shadow_text


ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
EXPERIMENTS_DIR = ROOT / "experiments"
LEDGER_PATH = ROOT / "paper_trades" / "paper_portfolio_ledger.json"
TARGET_SPRINT_LEDGER_PATH = ROOT / "paper_trades" / "target_sprint_sandbox_ledger.json"
SHADOW_ROOT = Path("/private/tmp/active-alpha-paper-monitor-shadow") / ROOT.name
READ_WARNINGS: list[dict[str, Any]] = []
SHADOW_ONLY = False


def now_shanghai() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def _read_json_worker(path_str: str, result_queue: mp.Queue) -> None:
    try:
        payload = json.loads(Path(path_str).read_text(encoding="utf-8"))
        result_queue.put(("ok", payload))
    except Exception as exc:
        result_queue.put(("error", str(exc)))


def shadow_path_for(path: Path) -> Path | None:
    try:
        rel = path.resolve().relative_to(ROOT)
    except Exception:
        return None
    return SHADOW_ROOT / rel


def _read_json_once(path: Path, timeout_seconds: float = 3.0) -> dict[str, Any] | None:
    if not path.exists():
        return None
    ctx = mp.get_context("fork")
    result_queue: mp.Queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_read_json_worker, args=(str(path), result_queue))
    proc.daemon = True
    proc.start()
    try:
        status, payload = result_queue.get(timeout=timeout_seconds)
        return payload if status == "ok" and isinstance(payload, dict) else None
    except queue.Empty:
        READ_WARNINGS.append(
            {
                "path": str(path),
                "warning": "file_read_timeout",
                "timeout_seconds": timeout_seconds,
            }
        )
        return None
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=0.5)
            if proc.is_alive():
                proc.kill()
        else:
            proc.join(timeout=0.5)


def read_json(path: Path | None, timeout_seconds: float = 3.0) -> dict[str, Any] | None:
    if not path:
        return None
    candidates: list[Path] = []
    shadow = shadow_path_for(path)
    if shadow and shadow != path and shadow.exists():
        candidates.append(shadow)
    if not SHADOW_ONLY or not candidates:
        candidates.append(path)
    for candidate in candidates:
        payload = _read_json_once(candidate, timeout_seconds=timeout_seconds)
        if payload is not None:
            return payload
    return None


def latest(pattern: str, directory: Path = EXPERIMENTS_DIR) -> Path | None:
    if not directory.exists():
        return None
    files = sorted(directory.glob(pattern))
    return files[-1] if files else None


def latest_readable_json(
    pattern: str,
    directory: Path = EXPERIMENTS_DIR,
    max_candidates: int = 2,
    timeout_seconds: float = 2.5,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[Path | None, dict[str, Any] | None]:
    directories = [directory]
    shadow_dir = shadow_path_for(directory)
    if shadow_dir and shadow_dir != directory and shadow_dir.exists():
        directories = [shadow_dir] if SHADOW_ONLY else [shadow_dir, directory]
    elif SHADOW_ONLY:
        directories = []
    files: list[Path] = []
    for item in directories:
        if item.exists():
            files.extend(item.glob(pattern))
    files = sorted(set(files), key=lambda item: item.name, reverse=True)
    for path in files[:max_candidates]:
        payload = read_json(path, timeout_seconds=timeout_seconds)
        if payload is not None and (predicate is None or predicate(payload)):
            return path, payload
    return (files[0] if files else None), None


def is_clean_validation_runner(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("status") not in {"ok", "completed", "success"}:
        return False
    if payload.get("live_orders_enabled") is True or payload.get("private_api_used") is True:
        return False
    if payload.get("safety_errors"):
        return False
    return bool(payload.get("child_runs") or payload.get("dynamic_scan_pool_enabled"))


def round_or_none(value: Any, digits: int = 6) -> float | None:
    try:
        return round(float(value), digits)
    except Exception:
        return None


def summarize_ledger(ledger: dict[str, Any] | None) -> dict[str, Any]:
    if not ledger:
        return {
            "status": "missing_or_unreadable",
            "path": str(LEDGER_PATH),
            "cash_usd": None,
            "equity_usd": None,
            "open_count": 0,
            "closed_count": 0,
            "open_symbols": [],
        }
    orders = ledger.get("paper_orders") or []
    api_style_orders = [
        order
        for order in orders
        if isinstance(order, dict)
        and str(order.get("status", "")).upper() == "FILLED"
        and str(order.get("side", "")).upper() in {"BUY", "SELL"}
        and order.get("average_fill_price") is not None
        and order.get("executed_quantity") is not None
        and order.get("commission_usd") is not None
        and order.get("live_orders_enabled") is False
        and order.get("private_api_used") is False
    ]
    sides = {str(order.get("side", "")).upper() for order in api_style_orders}
    return {
        "status": "ok",
        "path": str(LEDGER_PATH),
        "updated_at": ledger.get("updated_at"),
        "cash_usd": round_or_none(ledger.get("cash_usd")),
        "equity_usd": round_or_none(ledger.get("equity_usd")),
        "live_orders_enabled": ledger.get("live_orders_enabled") is True,
        "private_api_used": ledger.get("private_api_used") is True,
        "open_count": len(ledger.get("open_positions") or []),
        "closed_count": len(ledger.get("closed_trades") or []),
        "paper_order_count": len(ledger.get("paper_orders") or []),
        "api_style_order_lifecycle_present": len(api_style_orders) > 0,
        "api_style_order_side_coverage": sorted(sides),
        "api_style_buy_sell_lifecycle_present": {"BUY", "SELL"}.issubset(sides),
        "open_symbols": [str(p.get("symbol", "")).upper() for p in ledger.get("open_positions") or []],
    }


def summarize_target_sprint_ledger(ledger: dict[str, Any] | None) -> dict[str, Any]:
    if not ledger:
        return {
            "status": "missing_or_unreadable",
            "path": str(TARGET_SPRINT_LEDGER_PATH),
            "cash_usd": None,
            "equity_usd": None,
            "open_count": 0,
            "closed_count": 0,
            "paper_order_count": 0,
            "api_style_order_lifecycle_present": False,
            "target_complete": False,
        }
    orders = ledger.get("paper_orders") or []
    api_style_orders = [
        order
        for order in orders
        if isinstance(order, dict)
        and str(order.get("status", "")).upper() == "FILLED"
        and str(order.get("side", "")).lower() in {"buy", "sell"}
        and order.get("live_orders_enabled") is False
        and order.get("private_api_used") is False
    ]
    monthly = ledger.get("monthly_goal_baselines") or {}
    target = None
    for value in monthly.values():
        if isinstance(value, dict) and value.get("target_equity_usd") is not None:
            target = round_or_none(value.get("target_equity_usd"))
    target = target if target is not None else 1000.0
    equity = round_or_none(ledger.get("equity_usd"))
    target_complete = bool(equity is not None and target is not None and equity >= target)
    excluded_events = [
        event for event in ledger.get("events") or [] if isinstance(event, dict) and event.get("excluded_from_drawdown")
    ]
    open_positions = ledger.get("open_positions") or []
    binance_market_evidence = any(
        isinstance(pos, dict)
        and (
            ((pos.get("last_market") or {}).get("source") == "binance_public_market_data")
            or bool(pos.get("market_at_open"))
        )
        for pos in open_positions
    )
    return {
        "status": "ok",
        "path": str(TARGET_SPRINT_LEDGER_PATH),
        "updated_at": ledger.get("updated_at"),
        "cash_usd": round_or_none(ledger.get("cash_usd")),
        "open_value_usd": round_or_none(ledger.get("open_value_usd")),
        "equity_usd": equity,
        "target_equity_usd": target,
        "gap_to_target_usd": round(target - equity, 6) if equity is not None and target is not None else None,
        "net_return_pct": round_or_none(ledger.get("net_return_pct")),
        "max_drawdown_pct": round_or_none(ledger.get("max_drawdown_pct")),
        "realized_pnl_usd": round_or_none(ledger.get("realized_pnl_usd")),
        "live_orders_enabled": ledger.get("live_orders_enabled") is True,
        "private_api_used": ledger.get("private_api_used") is True,
        "main_ledger_mutated": ledger.get("main_ledger_mutated") is True,
        "open_count": len(open_positions),
        "closed_count": len(ledger.get("closed_trades") or []),
        "paper_order_count": len(orders),
        "api_style_order_lifecycle_present": len(api_style_orders) > 0,
        "binance_public_market_data_evidence": binance_market_evidence,
        "target_complete": target_complete,
        "excluded_drawdown_event_count": len(excluded_events),
        "open_symbols": [str(p.get("symbol", "")).upper() for p in open_positions if isinstance(p, dict)],
    }


def validation_runner_summary(run: dict[str, Any] | None) -> dict[str, Any]:
    if not run:
        return {"status": "missing"}
    child_runs = run.get("child_runs") or []
    child_failures = run.get("child_failures") or []
    progress = run.get("progress_delta") or {}
    post_validation = ((run.get("post_validation") or {}).get("stdout_json") or {})
    pre_validation = ((run.get("pre_validation") or {}).get("stdout_json") or {})
    recovery = (
        run.get("validation_recovery_plan")
        or post_validation.get("validation_recovery_plan")
        or pre_validation.get("validation_recovery_plan")
        or {}
    )
    return {
        "status": run.get("status"),
        "run_id": run.get("run_id"),
        "max_allowed_action_after": progress.get("max_allowed_action_after"),
        "failed_gates_after": progress.get("failed_gates_after") or [],
        "monthly_target": recovery.get("monthly_target") or {},
        "new_sample_policy": recovery.get("new_sample_policy"),
        "child_runs": [
            {
                "label": c.get("label"),
                "status": c.get("status"),
                "stdout_status": c.get("stdout_status") or ((c.get("stdout_json") or {}).get("status") if isinstance(c.get("stdout_json"), dict) else None),
            }
            for c in child_runs
        ],
        "child_failures": child_failures,
    }


def paper_loop_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    scan = payload.get("scan_summary") or payload.get("scan") or {}
    kline = payload.get("kline_cache_audit") or {}
    capacity = payload.get("validation_capacity_gate") or {}
    portfolio = payload.get("paper_portfolio") or ((payload.get("ledger") or {}).get("portfolio") if isinstance(payload.get("ledger"), dict) else {}) or {}
    ledger = payload.get("ledger") or {}
    opened = payload.get("new_paper_trades") or payload.get("opened_positions") or []
    return {
        "status": payload.get("status") or ("ok" if payload.get("run_id") else "unknown"),
        "run_id": payload.get("run_id"),
        "loop_kind": payload.get("loop_kind"),
        "data_status": payload.get("data_status"),
        "live_orders_enabled": payload.get("live_orders_enabled") is True,
        "private_api_used": payload.get("private_api_used") is True,
        "max_allowed_action": payload.get("max_allowed_action"),
        "frames_loaded": scan.get("frames_loaded"),
        "strategies_scanned": scan.get("strategies_scanned"),
        "current_signal_strategies": scan.get("current_signal_strategies"),
        "candidate_count": scan.get("candidate_count"),
        "deduplicated_candidate_count": scan.get("deduplicated_candidate_count"),
        "kline_cache_status": kline.get("status"),
        "kline_verified_count": kline.get("verified_count"),
        "kline_missing_count": kline.get("missing_count"),
        "kline_stale_count": kline.get("stale_count"),
        "reviewed_position_count": len(payload.get("reviewed_positions") or []),
        "new_paper_trade_count": len(opened),
        "paper_orders_total": payload.get("paper_orders_total") or len(ledger.get("paper_orders") or []),
        "open_symbols": payload.get("open_symbols") or [],
        "cash_usd": portfolio.get("cash_usd"),
        "equity_usd": portfolio.get("equity_usd"),
        "capacity_decision": capacity.get("decision"),
        "capacity_sample_action": capacity.get("sample_action"),
    }


def capability_summary(capability: dict[str, Any] | None) -> dict[str, Any]:
    if not capability:
        return {"status": "missing"}
    verdict = capability.get("verdict") or {}
    target = capability.get("monthly_target") or {}
    closed = capability.get("closed_summary") or {}
    sandbox = capability.get("aggressive_sandbox_evidence") or {}
    return {
        "status": "ok",
        "run_id": capability.get("run_id"),
        "capability_status": verdict.get("capability_status"),
        "can_simulate_execution_now": verdict.get("can_simulate_execution_now") is True,
        "target_complete_now": verdict.get("target_complete_now") is True,
        "target_path_proven": verdict.get("target_path_proven") is True,
        "max_allowed_action": verdict.get("max_allowed_action"),
        "blockers": verdict.get("blockers") or [],
        "monthly_target": {
            "target_equity_usd": target.get("target_equity_usd"),
            "current_equity_usd": target.get("current_equity_usd"),
            "gap_to_target_usd": target.get("gap_to_target_usd"),
            "progress_pct": target.get("progress_pct"),
        },
        "closed_summary": closed,
        "aggressive_sandbox": {
            "status": sandbox.get("status"),
            "counts_as_target_proof": sandbox.get("counts_as_target_proof") is True,
            "equity_usd": sandbox.get("equity_usd"),
            "net_return_pct": sandbox.get("net_return_pct"),
            "open_count": sandbox.get("open_count"),
            "closed_count": sandbox.get("closed_count"),
            "realized_pnl_usd": sandbox.get("realized_pnl_usd"),
        },
    }


def target_auditor_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    target = payload.get("monthly_target") or {}
    closed = payload.get("closed_trade_stats") or {}
    capability = payload.get("capability") or {}
    return {
        "status": "ok",
        "run_id": payload.get("run_id"),
        "target_complete": target.get("target_complete") is True,
        "monthly_target": {
            "target_equity_usd": target.get("target_equity_usd"),
            "current_equity_usd": target.get("current_equity_usd"),
            "gap_to_target_usd": target.get("gap_to_target_usd"),
            "progress_pct": target.get("progress_pct"),
            "required_return_from_current_pct": target.get("required_return_from_current_pct"),
        },
        "closed_summary": {
            "closed_count": closed.get("closed_count"),
            "wins": closed.get("wins"),
            "losses": closed.get("losses"),
            "win_rate_pct": closed.get("win_rate_pct"),
            "realized_pnl_usd": closed.get("realized_pnl_usd"),
        },
        "capability": {
            "simulated_order_ledger_present": capability.get("simulated_order_ledger_present") is True,
            "simulated_compounding_enabled": capability.get("simulated_compounding_enabled") is True,
            "binance_public_market_data_flow": capability.get("binance_public_market_data_flow"),
            "binance_live_order_flow": capability.get("binance_live_order_flow"),
        },
        "verdict": payload.get("verdict"),
    }


def testnet_summary(testnet: dict[str, Any] | None, testnet_path: Path | None) -> dict[str, Any]:
    if not testnet:
        return {"status": "missing"}
    safety = testnet.get("safety") or {}
    result = testnet.get("result") or {}
    safe = (
        safety.get("live_orders_enabled") is False
        and safety.get("testnet_only") is True
        and safety.get("production_endpoint_blocked") is True
        and safety.get("paper_ledger_mutated") is False
        and safety.get("margin_futures_perpetuals_enabled") is False
        and safety.get("withdrawals_enabled") is False
    )
    return {
        "status": "ok",
        "run_id": testnet.get("run_id"),
        "mode": testnet.get("mode"),
        "result_status": result.get("status"),
        "request_sent": result.get("request_sent") is True,
        "safe_testnet_or_dry_run_path": safe,
        "report_path": str((testnet.get("artifacts") or {}).get("report_path") or ""),
        "experiment_path": str((testnet.get("artifacts") or {}).get("experiment_path") or testnet_path or ""),
    }


def target_path_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    queue = payload.get("pressure_retest_queue") or []
    top_queue = queue[0] if queue and isinstance(queue[0], dict) else {}
    top_ranked = payload.get("top_ranked") or []
    top = top_ranked[0] if top_ranked and isinstance(top_ranked[0], dict) else {}
    top_best = top.get("best_oos") if isinstance(top.get("best_oos"), dict) else {}
    top_strategy = top_best.get("strategy") if isinstance(top_best.get("strategy"), dict) else {}
    target_path_count = payload.get("target_path_candidate_count")
    if target_path_count is None:
        target_path_count = sum(
            1
            for row in top_ranked
            if isinstance(row, dict)
            and str(((row.get("best_oos") or {}).get("stage") or "")).startswith("target_research_pass")
        )
    pressure_count = payload.get("pressure_retest_queue_count")
    if pressure_count is None:
        pressure_count = len(queue)
    return {
        "status": "ok",
        "run_id": payload.get("run_id"),
        "data_status": payload.get("data_status"),
        "frames_loaded": payload.get("frames_loaded"),
        "strategies_scanned": payload.get("strategies_scanned"),
        "target_path_candidate_count": target_path_count,
        "eligible_retest_count": payload.get("eligible_retest_count"),
        "current_signal_ready_count": payload.get("current_signal_ready_count"),
        "pressure_retest_queue_count": pressure_count,
        "top_ranked": {
            "symbol": top.get("symbol"),
            "interval": top.get("interval"),
            "family": top.get("family") or top_strategy.get("family"),
            "stage": top.get("stage") or top_best.get("stage"),
            "current_signal": top.get("current_signal") if "current_signal" in top else top_best.get("current_signal"),
            "quality_flags": top.get("quality_flags") or top_best.get("quality_flags") or [],
            "score": top.get("score"),
            "oos_summary": top.get("oos_summary") or {
                key: top_best.get(key)
                for key in [
                    "trade_count",
                    "win_rate_pct",
                    "final_capital",
                    "net_return_pct",
                    "max_drawdown_pct",
                    "weekly_double_trade_count",
                ]
            },
        },
        "top_pressure_retest_queue": {
            "queue_id": top_queue.get("queue_id"),
            "symbol": top_queue.get("symbol"),
            "interval": top_queue.get("interval"),
            "family": top_queue.get("family") or top_queue.get("strategy_family"),
            "score": top_queue.get("score"),
            "current_signal": top_queue.get("current_signal"),
            "recommended_max_action": top_queue.get("recommended_max_action"),
            "why_not_strict": top_queue.get("why_not_strict"),
            "failed_quality_flags": top_queue.get("failed_quality_flags") or [],
            "oos_summary": top_queue.get("oos_summary") or {},
        },
    }


def pressure_sampler_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    checks = payload.get("candidate_checks") or []
    first = checks[0] if checks and isinstance(checks[0], dict) else {}
    signal = first.get("current_signal") or {}
    recovery_gate = first.get("recovery_gate") or {}
    execution_quote = first.get("execution_quote") or {}
    capacity_gate = payload.get("capacity_gate") or {}
    return {
        "status": payload.get("status") or "unknown",
        "run_id": payload.get("run_id"),
        "source_queue_run_id": payload.get("source_queue_run_id"),
        "candidates_seen": payload.get("candidates_seen"),
        "opened_positions_count": len(payload.get("opened_positions") or []),
        "skipped_candidates_count": len(payload.get("skipped_candidates") or []),
        "capacity_gate": {
            "decision": capacity_gate.get("decision"),
            "sample_action": capacity_gate.get("sample_action"),
            "recommended_runner_mode": capacity_gate.get("recommended_runner_mode"),
            "reason": capacity_gate.get("reason"),
            "open_count": capacity_gate.get("open_count"),
            "cash_usd": capacity_gate.get("cash_usd"),
            "equity_usd": capacity_gate.get("equity_usd"),
        },
        "top_candidate": {
            "queue_id": first.get("queue_id"),
            "symbol": first.get("symbol"),
            "interval": first.get("interval"),
            "family": first.get("family"),
            "action": first.get("action"),
            "reason": first.get("reason"),
            "score": first.get("score"),
            "oos_summary": first.get("oos_summary") or {},
            "current_signal": {
                "status": signal.get("status"),
                "source": signal.get("source"),
                "triggered": signal.get("triggered"),
                "latest_closed_at_ms": signal.get("latest_closed_at_ms"),
                "latest_close": signal.get("latest_close"),
                "prefilter_score": (signal.get("prefilter") or {}).get("score"),
            },
            "recovery_gate": {
                "decision": recovery_gate.get("decision"),
                "matched_group": recovery_gate.get("matched_group"),
                "matched_name": recovery_gate.get("matched_name"),
                "matched_status": recovery_gate.get("matched_status"),
                "reason": recovery_gate.get("reason"),
            },
            "execution_quote": {
                "status": execution_quote.get("status"),
                "allow": execution_quote.get("allow"),
                "spread_bps": execution_quote.get("spread_bps"),
                "depth_1pct_usd": execution_quote.get("depth_1pct_usd"),
                "quote_volume_24h_usd": execution_quote.get("quote_volume_24h_usd"),
                "execution_price": execution_quote.get("execution_price"),
                "commission_usd": execution_quote.get("commission_usd"),
                "total_slippage_bps": execution_quote.get("total_slippage_bps"),
            },
        },
    }


def realtime_scanner_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    rows = payload.get("rows") or []
    paper_candidates = payload.get("paper_candidates") or []
    near_misses = payload.get("near_misses") or []
    top = rows[0] if rows and isinstance(rows[0], dict) else {}
    pegged_rows = [
        row.get("symbol")
        for row in rows
        if isinstance(row, dict) and row.get("decision") == "liquidity_context_pegged_asset"
    ][:8]
    return {
        "status": payload.get("source_status") or "unknown",
        "run_id": payload.get("run_id"),
        "run_at": payload.get("run_at"),
        "source_error": payload.get("source_error"),
        "universe_status": payload.get("universe_status"),
        "selected_symbol_count": payload.get("selected_symbol_count"),
        "scored_symbol_count": payload.get("scored_symbol_count"),
        "paper_candidate_count": len(paper_candidates),
        "near_miss_count": len(near_misses),
        "live_orders_enabled": payload.get("live_orders_enabled") is True,
        "private_api_used": payload.get("private_api_used") is True,
        "top_row": {
            "symbol": top.get("symbol"),
            "decision": top.get("decision"),
            "data_status": top.get("data_status"),
            "score": top.get("score"),
            "ret_1h_pct": top.get("ret_1h_pct"),
            "ret_4h_pct": top.get("ret_4h_pct"),
            "spread_bps": top.get("spread_bps"),
            "depth_usd_50bps": top.get("depth_usd_50bps"),
        },
        "pegged_context_symbols": pegged_rows,
    }


def aggressive_sandbox_summary(payload: dict[str, Any] | None, path: Path | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    ledger = payload.get("ledger_summary") or {}
    sandbox_ledger_path = payload.get("sandbox_ledger_path")
    sandbox_ledger = read_json(Path(sandbox_ledger_path), timeout_seconds=1.0) if sandbox_ledger_path else None
    sandbox_ledger_status = "ok" if sandbox_ledger else "unavailable_using_report_summary"
    opened = payload.get("opened_positions") or []
    closed = payload.get("closed_positions") or []
    ledger_open = (sandbox_ledger or {}).get("open_positions") or []
    ledger_closed = (sandbox_ledger or {}).get("closed_trades") or []
    latest_open = (
        ledger_open[-1]
        if ledger_open and isinstance(ledger_open[-1], dict)
        else opened[-1]
        if opened and isinstance(opened[-1], dict)
        else {}
    )
    latest_closed = (
        ledger_closed[-1]
        if ledger_closed and isinstance(ledger_closed[-1], dict)
        else closed[-1]
        if closed and isinstance(closed[-1], dict)
        else {}
    )
    return {
        "status": "ok",
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "path": str(path or ""),
        "counts_as_target_proof": False,
        "live_orders_enabled": payload.get("live_orders_enabled") is True,
        "private_api_used": payload.get("private_api_used") is True,
        "main_ledger_mutated": payload.get("main_ledger_mutated") is True,
        "candidate_source": payload.get("candidate_source"),
        "sandbox_ledger_path": sandbox_ledger_path,
        "sandbox_ledger_status": sandbox_ledger_status,
        "equity_usd": (sandbox_ledger or {}).get("equity_usd", ledger.get("equity_usd")),
        "cash_usd": (sandbox_ledger or {}).get("cash_usd", ledger.get("cash_usd")),
        "net_return_pct": (sandbox_ledger or {}).get("net_return_pct", ledger.get("net_return_pct")),
        "open_count": len(ledger_open) if sandbox_ledger else ledger.get("open_count"),
        "closed_count": len(ledger_closed) if sandbox_ledger else ledger.get("closed_count"),
        "opened_positions_count": len(opened),
        "closed_positions_count": len(closed),
        "latest_open": {
            "paper_trade_id": latest_open.get("paper_trade_id"),
            "symbol": latest_open.get("symbol"),
            "strategy_family": latest_open.get("strategy_family"),
            "source_current_signal_stage": latest_open.get("source_current_signal_stage"),
            "entry_price": latest_open.get("entry_price"),
            "last_price": latest_open.get("last_price"),
            "unrealized_pnl_usd": latest_open.get("unrealized_pnl_usd"),
            "unrealized_pnl_pct": latest_open.get("unrealized_pnl_pct"),
            "spread_bps": latest_open.get("spread_bps"),
            "quote_volume_24h_usd": latest_open.get("quote_volume_24h_usd"),
            "price_change_24h_pct": latest_open.get("price_change_24h_pct"),
        },
        "latest_closed": {
            "paper_trade_id": latest_closed.get("paper_trade_id"),
            "symbol": latest_closed.get("symbol"),
            "outcome": latest_closed.get("outcome"),
            "exit_reason": latest_closed.get("exit_reason"),
            "realized_pnl_usd": latest_closed.get("realized_pnl_usd"),
            "realized_pnl_pct": latest_closed.get("realized_pnl_pct"),
        },
    }


def render_markdown(record: dict[str, Any]) -> str:
    verdict = record["verdict"]
    capability = record["capability_evidence"]
    target_auditor = record.get("target_auditor") or {}
    # The capability audit can be older than the latest ledger review. Prefer
    # the dedicated target auditor for human-readable current equity/gap.
    target_view = target_auditor.get("monthly_target") or capability.get("monthly_target") or {}
    closed_view = target_auditor.get("closed_summary") or capability.get("closed_summary") or {}
    ledger = record["ledger"]
    target_sprint = record.get("target_sprint_sandbox") or {}
    testnet = record["binance_testnet_flow"]
    runner = record["validation_runner"]
    target_path = record.get("target_path_evidence") or {}
    pressure = record.get("pressure_sampler") or {}
    realtime = record.get("realtime_scanner") or {}
    fast_loop = record.get("latest_fast_paper_loop") or {}
    pressure_top = pressure.get("top_candidate") or {}
    signal = pressure_top.get("current_signal") or {}
    recovery_gate = pressure_top.get("recovery_gate") or {}
    execution_quote = pressure_top.get("execution_quote") or {}
    sandbox = record.get("latest_aggressive_sandbox") or {}
    sandbox_open = sandbox.get("latest_open") or {}
    sandbox_closed = sandbox.get("latest_closed") or {}
    lines = [
        f"# Binance API Compounding Flow Audit | {record['run_id']}",
        "",
        "## Verdict",
        "",
        f"- API-style simulation path available: `{str(verdict['api_style_simulation_available']).lower()}`",
        f"- Binance testnet/dry-run path verified: `{str(verdict['binance_testnet_or_dry_run_verified']).lower()}`",
        f"- Monthly target complete now: `{str(verdict['monthly_target_complete_now']).lower()}`",
        f"- Repeatable compounding target path proven: `{str(verdict['repeatable_compounding_path_proven']).lower()}`",
        f"- Max allowed action: `{verdict['max_allowed_action']}`",
        f"- Overall status: `{verdict['overall_status']}`",
        "",
        "## Main Paper Ledger",
        "",
        f"- Ledger status: `{ledger['status']}`",
        f"- Equity: `${ledger['equity_usd']}`",
        f"- Cash: `${ledger['cash_usd']}`",
        f"- Open / closed trades: `{ledger['open_count']} / {ledger['closed_count']}`",
        f"- Paper API-style orders: `{ledger.get('paper_order_count')}`",
        f"- API-style lifecycle present: `{str(ledger.get('api_style_order_lifecycle_present')).lower()}`",
        f"- BUY/SELL lifecycle coverage: `{str(ledger.get('api_style_buy_sell_lifecycle_present')).lower()}`",
        f"- Open symbols: `{', '.join(ledger['open_symbols']) or 'none'}`",
        "",
        "## Latest Fast Paper Loop",
        "",
        f"- Loop status: `{fast_loop.get('status')}`",
        f"- Run ID: `{fast_loop.get('run_id')}`",
        f"- Data status: `{fast_loop.get('data_status')}`",
        f"- Kline cache status: `{fast_loop.get('kline_cache_status')}`",
        f"- Frames loaded / strategies scanned: `{fast_loop.get('frames_loaded')} / {fast_loop.get('strategies_scanned')}`",
        f"- Reviewed positions: `{fast_loop.get('reviewed_position_count')}`",
        f"- New paper trades: `{fast_loop.get('new_paper_trade_count')}`",
        f"- Paper orders total: `{fast_loop.get('paper_orders_total')}`",
        f"- Capacity decision: `{fast_loop.get('capacity_decision')}` / `{fast_loop.get('capacity_sample_action')}`",
        "",
        "## Target-Sprint Sandbox Ledger",
        "",
        f"- Sandbox status: `{target_sprint.get('status')}`",
        f"- Sandbox equity: `${target_sprint.get('equity_usd')}`",
        f"- Sandbox cash: `${target_sprint.get('cash_usd')}`",
        f"- Open / closed sandbox trades: `{target_sprint.get('open_count')} / {target_sprint.get('closed_count')}`",
        f"- Paper API-style orders: `{target_sprint.get('paper_order_count')}`",
        f"- API-style order lifecycle present: `{str(target_sprint.get('api_style_order_lifecycle_present')).lower()}`",
        f"- Binance public market-data evidence: `{str(target_sprint.get('binance_public_market_data_evidence')).lower()}`",
        f"- Target gap: `${target_sprint.get('gap_to_target_usd')}`",
        f"- Excluded bad drawdown events: `{target_sprint.get('excluded_drawdown_event_count')}`",
        f"- Open symbols: `{', '.join(target_sprint.get('open_symbols') or []) or 'none'}`",
        "",
        "## Compounding Evidence",
        "",
        f"- Capability status: `{capability.get('capability_status')}`",
        f"- Target auditor status: `{target_auditor.get('status')}`",
        f"- Target equity: `${target_view.get('target_equity_usd')}`",
        f"- Current equity: `${target_view.get('current_equity_usd')}`",
        f"- Gap: `${target_view.get('gap_to_target_usd')}`",
        f"- Closed sample win rate: `{closed_view.get('win_rate_pct')}%`",
        f"- Closed realized PnL: `${closed_view.get('realized_pnl_usd')}`",
        "",
        "## Binance Flow Evidence",
        "",
        f"- Testnet flow status: `{testnet['status']}`",
        f"- Testnet/dry-run result: `{testnet.get('result_status')}`",
        f"- Request sent: `{str(testnet.get('request_sent')).lower()}`",
        f"- Safe testnet or dry-run path: `{str(testnet.get('safe_testnet_or_dry_run_path')).lower()}`",
        "",
        "## Validation Runner Evidence",
        "",
        f"- Runner status: `{runner['status']}`",
        f"- New sample policy: `{runner.get('new_sample_policy')}`",
        f"- Max allowed action after runner: `{runner.get('max_allowed_action_after')}`",
        "",
        "## Latest Target-Path / Pressure Evidence",
        "",
        f"- Target-path refresh status: `{target_path.get('status')}` / data `{target_path.get('data_status')}`",
        f"- Frames loaded / strategies scanned: `{target_path.get('frames_loaded')} / {target_path.get('strategies_scanned')}`",
        f"- Strict target candidates: `{target_path.get('target_path_candidate_count')}`",
        f"- Pressure retest queue count: `{target_path.get('pressure_retest_queue_count')}`",
        f"- Pressure sampler status: `{pressure.get('status')}`",
        f"- Realtime scanner status: `{realtime.get('status')}` / universe `{realtime.get('universe_status')}`",
        f"- Realtime symbols scored / selected: `{realtime.get('scored_symbol_count')} / {realtime.get('selected_symbol_count')}`",
        f"- Realtime paper candidates / near misses: `{realtime.get('paper_candidate_count')} / {realtime.get('near_miss_count')}`",
        f"- Realtime top row: `{(realtime.get('top_row') or {}).get('symbol')}` `{(realtime.get('top_row') or {}).get('decision')}` score `{(realtime.get('top_row') or {}).get('score')}`",
        f"- Pegged context filtered: `{', '.join(realtime.get('pegged_context_symbols') or []) or 'none'}`",
        f"- Pressure candidate: `{pressure_top.get('symbol')} {pressure_top.get('interval')} {pressure_top.get('family')}`",
        f"- Current signal triggered: `{signal.get('triggered')}`",
        f"- Recovery gate: `{recovery_gate.get('decision')}` / `{recovery_gate.get('matched_group')}:{recovery_gate.get('matched_name')}`",
        f"- Execution quote: status `{execution_quote.get('status')}`, spread `{execution_quote.get('spread_bps')}` bps, depth 1% `${execution_quote.get('depth_1pct_usd')}`",
        "",
        "## Latest Aggressive Sandbox Evidence",
        "",
        f"- Sandbox status: `{sandbox.get('status')}`",
        f"- Counts as target proof: `{str(sandbox.get('counts_as_target_proof')).lower()}`",
        f"- Main ledger mutated: `{str(sandbox.get('main_ledger_mutated')).lower()}`",
        f"- Sandbox equity: `${sandbox.get('equity_usd')}`",
        f"- Sandbox net return: `{sandbox.get('net_return_pct')}%`",
        f"- Open / closed sandbox trades: `{sandbox.get('open_count')} / {sandbox.get('closed_count')}`",
        f"- Latest open: `{sandbox_open.get('symbol')}` unrealized `{sandbox_open.get('unrealized_pnl_pct')}%`, spread `{sandbox_open.get('spread_bps')}` bps",
        f"- Latest closed: `{sandbox_closed.get('symbol')}` `{sandbox_closed.get('outcome')}` `{sandbox_closed.get('realized_pnl_pct')}%` via `{sandbox_closed.get('exit_reason')}`",
        "",
        "## Blockers",
        "",
    ]
    for blocker in verdict["blockers"]:
        lines.append(f"- {blocker}")
    if not verdict["blockers"]:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Artifact Sources",
            "",
            f"- Capability audit: `{record['sources']['capability_audit']}`",
            f"- Validation runner: `{record['sources']['validation_runner']}`",
            f"- Binance testnet flow: `{record['sources']['binance_testnet_flow']}`",
            f"- Target auditor: `{record['sources'].get('target_auditor')}`",
            f"- Target-path evidence: `{record['sources'].get('target_path_evidence')}`",
            f"- Pressure sampler: `{record['sources'].get('pressure_sampler')}`",
            f"- Realtime scanner: `{record['sources'].get('realtime_scanner')}`",
            f"- Fast paper loop: `{record['sources'].get('fast_paper_loop')}`",
            f"- Aggressive sandbox: `{record['sources'].get('aggressive_sandbox')}`",
            f"- Ledger: `{record['sources']['ledger']}`",
            f"- Target-sprint sandbox ledger: `{record['sources'].get('target_sprint_sandbox_ledger')}`",
            "",
            "Safety: this report is read-only. It does not place live orders, use private trading APIs, mutate the paper ledger, or count paper/testnet PnL as real revenue.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_record() -> dict[str, Any]:
    now = now_shanghai()
    run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-binance-api-compounding-flow-audit"
    capability_path, capability = latest_readable_json("*paper-compounding-capability-audit.json")
    runner_path, runner = latest_readable_json(
        "*validation-progress-runner.json",
        max_candidates=10,
        predicate=is_clean_validation_runner,
    )
    testnet_path, testnet = latest_readable_json("*binance-spot-testnet-order-flow.json")
    target_path, target_path_payload = latest_readable_json("*lightweight-extended-history-lab.json")
    pressure_sampler_path, pressure_sampler_payload = latest_readable_json("*pressure-retest-queue-sampler.json")
    realtime_scanner_path, realtime_scanner_payload = latest_readable_json("*binance-realtime-opportunity-scanner.json")
    fast_loop_path, fast_loop_payload = latest_readable_json("*fast-crypto-paper-auto-trader.json")
    target_auditor_path, target_auditor_payload = latest_readable_json("*paper-compounding-target-auditor*.json")
    sandbox_path, sandbox_payload = latest_readable_json("*aggressive-paper-sandbox.json")
    ledger = read_json(LEDGER_PATH)
    target_sprint_ledger = read_json(TARGET_SPRINT_LEDGER_PATH)

    cap = capability_summary(capability)
    runner_sum = validation_runner_summary(runner)
    testnet_sum = testnet_summary(testnet, testnet_path)
    ledger_sum = summarize_ledger(ledger)
    target_sprint_sum = summarize_target_sprint_ledger(target_sprint_ledger)
    target_path_sum = target_path_summary(target_path_payload)
    pressure_sampler_sum = pressure_sampler_summary(pressure_sampler_payload)
    realtime_scanner_sum = realtime_scanner_summary(realtime_scanner_payload)
    fast_loop_sum = paper_loop_summary(fast_loop_payload)
    target_auditor_sum = target_auditor_summary(target_auditor_payload)
    sandbox_sum = aggressive_sandbox_summary(sandbox_payload, sandbox_path)

    main_ledger_api_lifecycle_available = (
        ledger_sum.get("status") == "ok"
        and ledger_sum.get("live_orders_enabled") is False
        and ledger_sum.get("private_api_used") is False
        and ledger_sum.get("api_style_order_lifecycle_present") is True
        and ledger_sum.get("api_style_buy_sell_lifecycle_present") is True
    )
    latest_runner_or_fast_loop_clean = (
        runner_sum.get("status") in {"ok", "completed", "success"}
        or fast_loop_sum.get("status") in {"ok", "missing"}
    )
    latest_loop_safety_clean = (
        fast_loop_sum.get("live_orders_enabled") is not True
        and fast_loop_sum.get("private_api_used") is not True
    )
    target_sprint_api_lifecycle_available = (
        target_sprint_sum.get("status") == "ok"
        and target_sprint_sum.get("live_orders_enabled") is False
        and target_sprint_sum.get("private_api_used") is False
        and target_sprint_sum.get("api_style_order_lifecycle_present") is True
        and target_sprint_sum.get("binance_public_market_data_evidence") is True
    )
    api_style_simulation_available = (
        cap.get("can_simulate_execution_now") is True
        or (main_ledger_api_lifecycle_available and latest_runner_or_fast_loop_clean and latest_loop_safety_clean)
        or target_sprint_api_lifecycle_available
    )
    binance_testnet_or_dry_run_verified = (
        testnet_sum.get("status") == "ok"
        and testnet_sum.get("result_status") in {"dry_run", "order_test_attempted"}
        and testnet_sum.get("safe_testnet_or_dry_run_path") is True
    )
    target_complete = cap.get("target_complete_now") is True or target_auditor_sum.get("target_complete") is True
    target_complete = target_complete or target_sprint_sum.get("target_complete") is True
    target_path_proven = cap.get("target_path_proven") is True
    blockers = list(cap.get("blockers") or [])
    if runner_sum.get("status") not in {"ok", "completed", "success"}:
        blockers.append(f"validation_runner_not_clean:{runner_sum.get('status')}")
    if fast_loop_sum.get("status") not in {"ok", "missing"}:
        blockers.append(f"latest_fast_loop_not_clean:{fast_loop_sum.get('status')}")
    if fast_loop_sum.get("status") != "missing" and fast_loop_sum.get("kline_cache_status") != "verified":
        blockers.append(f"latest_fast_loop_kline_not_verified:{fast_loop_sum.get('kline_cache_status')}")
    if not binance_testnet_or_dry_run_verified:
        blockers.append("binance_testnet_or_dry_run_not_verified")
    if not api_style_simulation_available:
        blockers.append("api_style_simulation_not_available")
    if not target_complete:
        blockers.append("monthly_target_not_complete")
    if target_sprint_sum.get("status") == "ok":
        if target_sprint_sum.get("live_orders_enabled") is True:
            blockers.append("target_sprint_live_orders_flag_true")
        if target_sprint_sum.get("private_api_used") is True:
            blockers.append("target_sprint_private_api_used")
        if target_sprint_sum.get("main_ledger_mutated") is True:
            blockers.append("target_sprint_mutated_main_ledger")
        if target_sprint_sum.get("target_complete") is not True:
            blockers.append("target_sprint_monthly_target_not_complete")
    if not target_path_proven:
        blockers.append("repeatable_compounding_path_not_proven")
    if sandbox_sum.get("status") == "ok":
        if sandbox_sum.get("live_orders_enabled") is True:
            blockers.append("latest_sandbox_live_orders_flag_true")
        if sandbox_sum.get("private_api_used") is True:
            blockers.append("latest_sandbox_private_api_used")
        if sandbox_sum.get("main_ledger_mutated") is True:
            blockers.append("latest_sandbox_mutated_main_ledger")
        if sandbox_sum.get("counts_as_target_proof") is not True:
            blockers.append("latest_sandbox_not_target_proof")
    if target_path_sum.get("status") == "ok":
        if not target_path_sum.get("target_path_candidate_count"):
            blockers.append("latest_no_strict_target_path_candidate")
        if target_path_sum.get("pressure_retest_queue_count"):
            blockers.append("latest_pressure_queue_watch_only_not_target_proof")
    elif pressure_sampler_sum.get("status") not in {None, "missing"}:
        blockers.append("latest_target_path_unreadable_but_pressure_sampler_available")
    if pressure_sampler_sum.get("status") not in {None, "missing"}:
        if pressure_sampler_sum.get("opened_positions_count") == 0:
            blockers.append("latest_pressure_sampler_opened_no_positions")
        pressure_top = pressure_sampler_sum.get("top_candidate") or {}
        if ((pressure_top.get("current_signal") or {}).get("triggered") is False):
            blockers.append("latest_pressure_current_signal_false")
        if ((pressure_sampler_sum.get("capacity_gate") or {}).get("decision") == "block_validation_capacity"):
            blockers.append("latest_pressure_capacity_gate_block")
        if ((pressure_top.get("recovery_gate") or {}).get("decision") == "block_validation_recovery_plan"):
            blockers.append("latest_pressure_recovery_gate_block")
    if realtime_scanner_sum.get("status") not in {None, "missing"}:
        if realtime_scanner_sum.get("live_orders_enabled") is True:
            blockers.append("latest_realtime_scanner_live_orders_flag_true")
        if realtime_scanner_sum.get("private_api_used") is True:
            blockers.append("latest_realtime_scanner_private_api_used")
        if realtime_scanner_sum.get("status") != "verified":
            blockers.append(f"latest_realtime_scanner_not_verified:{realtime_scanner_sum.get('status')}")
        if not realtime_scanner_sum.get("paper_candidate_count"):
            blockers.append("latest_realtime_scanner_no_paper_candidate")

    if api_style_simulation_available and binance_testnet_or_dry_run_verified and target_path_proven:
        overall_status = "api_paper_flow_and_compounding_target_proven"
    elif (
        api_style_simulation_available
        and binance_testnet_or_dry_run_verified
        and (target_path_sum.get("pressure_retest_queue_count") or pressure_sampler_sum.get("candidates_seen"))
    ):
        overall_status = "api_paper_flow_available_with_pressure_watch_but_target_not_proven"
    elif api_style_simulation_available and binance_testnet_or_dry_run_verified:
        overall_status = "api_paper_flow_available_but_compounding_target_not_proven"
    elif api_style_simulation_available:
        overall_status = "paper_simulation_available_but_binance_testnet_or_target_incomplete"
    else:
        overall_status = "degraded_or_unproven"

    return {
        "run_id": run_id,
        "created_at": now.isoformat(timespec="seconds"),
        "source_skill": "active-alpha-paper-monitor",
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "sources": {
            "capability_audit": str(capability_path or ""),
            "validation_runner": str(runner_path or ""),
            "binance_testnet_flow": str(testnet_path or ""),
            "target_auditor": str(target_auditor_path or ""),
            "target_path_evidence": str(target_path or ""),
            "pressure_sampler": str(pressure_sampler_path or ""),
            "realtime_scanner": str(realtime_scanner_path or ""),
            "fast_paper_loop": str(fast_loop_path or ""),
            "aggressive_sandbox": str(sandbox_path or ""),
            "ledger": str(LEDGER_PATH),
            "target_sprint_sandbox_ledger": str(TARGET_SPRINT_LEDGER_PATH),
        },
        "artifact_read_warnings": list(READ_WARNINGS),
        "ledger": ledger_sum,
        "target_sprint_sandbox": target_sprint_sum,
        "api_style_simulation_evidence": {
            "main_ledger_api_lifecycle_available": main_ledger_api_lifecycle_available,
            "latest_runner_or_fast_loop_clean": latest_runner_or_fast_loop_clean,
            "latest_loop_safety_clean": latest_loop_safety_clean,
            "target_sprint_api_lifecycle_available": target_sprint_api_lifecycle_available,
        },
        "capability_evidence": cap,
        "validation_runner": runner_sum,
        "binance_testnet_flow": testnet_sum,
        "target_auditor": target_auditor_sum,
        "target_path_evidence": target_path_sum,
        "pressure_sampler": pressure_sampler_sum,
        "realtime_scanner": realtime_scanner_sum,
        "latest_fast_paper_loop": fast_loop_sum,
        "latest_aggressive_sandbox": sandbox_sum,
        "verdict": {
            "api_style_simulation_available": api_style_simulation_available,
            "binance_testnet_or_dry_run_verified": binance_testnet_or_dry_run_verified,
            "monthly_target_complete_now": target_complete,
            "repeatable_compounding_path_proven": target_path_proven,
            "max_allowed_action": "paper_only",
            "overall_status": overall_status,
            "blockers": sorted(set(blockers)),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate Binance API compounding flow evidence")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--dry-run", action="store_true", help="Do not write report/experiment artifacts")
    parser.add_argument(
        "--shadow-only",
        action="store_true",
        help="Read only /private/tmp shadow artifacts; avoids slow primary workspace glob/read operations.",
    )
    parser.add_argument(
        "--skip-primary-writes",
        action="store_true",
        help="Write report/experiment only to the shadow artifact store, not the primary workspace.",
    )
    parser.add_argument("--compact-output", action="store_true", help="Print a compact JSON summary")
    return parser.parse_args()


def main() -> int:
    global SHADOW_ONLY
    args = parse_args()
    SHADOW_ONLY = args.shadow_only or os.environ.get("ACTIVE_ALPHA_SHADOW_ONLY") == "1"
    record = build_record()
    now = now_shanghai()
    report_path = REPORTS_DIR / f"{now.strftime('%Y-%m-%d-%H%M')}-binance-api-compounding-flow-audit.md"
    experiment_path = EXPERIMENTS_DIR / f"{record['run_id']}.json"
    record["outputs"] = {"report": str(report_path), "experiment": str(experiment_path)}
    skip_primary_writes = args.skip_primary_writes or os.environ.get("ACTIVE_ALPHA_SKIP_PRIMARY_WRITES") == "1"
    if not args.dry_run:
        report_text = render_markdown(record)
        write_shadow_text(report_path, report_text)
        write_shadow_json(experiment_path, record)
        if not skip_primary_writes:
            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_text, encoding="utf-8")
            experiment_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.format == "markdown":
        print(render_markdown(record), end="")
    elif args.compact_output:
        compact = {
            "run_id": record.get("run_id"),
            "created_at": record.get("created_at"),
            "live_orders_enabled": record.get("live_orders_enabled"),
            "private_api_used": record.get("private_api_used"),
            "ledger": record.get("ledger"),
            "target_sprint_sandbox": record.get("target_sprint_sandbox"),
            "verdict": record.get("verdict"),
            "artifact_read_warnings": record.get("artifact_read_warnings"),
            "sources": record.get("sources"),
            "outputs": record.get("outputs"),
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
