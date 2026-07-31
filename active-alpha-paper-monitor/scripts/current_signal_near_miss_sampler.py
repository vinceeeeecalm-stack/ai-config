#!/usr/bin/env python3
"""Open tiny paper samples for current-signal near-miss candidates.

Research-only. No private keys. No live orders. This script consumes the
read-only validation sample audit and turns eligible current-signal near misses
into small paper positions so the system can collect forward evidence instead
of hand-waving about attractive backtests.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
CONFIG_PATH = ACTIVE_ROOT / "config" / "active_alpha_monitor_config.json"
ROBUSTNESS_AUDIT_PATH = ACTIVE_ROOT / "experiments" / "current-signal-robustness-audit.json"
CHINA_TZ = dt.timezone(dt.timedelta(hours=8))
MARKET_CONTEXT_FIELDS = (
    "market_regime",
    "market_atmosphere",
    "short_term_state",
    "sentiment_state",
)

sys.path.insert(0, str(SCRIPT_DIR))
import paper_portfolio_engine as engine  # noqa: E402
import paper_strategy_overlay as pso  # noqa: E402
import paper_testnet_risk_control_auditor as ptrc  # noqa: E402
import binance_market_data_health_auditor as bmdh  # noqa: E402
from validation_sample_auditor import current_signal_candidate_summary  # noqa: E402


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


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


def run_audit(experiment_dir: str, paper_dir: str, recommendation_ledger: str) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "validation_sample_auditor.py"),
        "--format",
        "json",
        "--experiment-dir",
        experiment_dir,
        "--paper-dir",
        paper_dir,
        "--recommendation-ledger",
        recommendation_ledger,
    ]
    result = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), text=True, capture_output=True, check=False, timeout=120)
    if result.returncode != 0:
        raise SystemExit(f"validation_sample_auditor failed: {result.stderr[-1000:]}")
    return json.loads(result.stdout)


def load_ledger(path: Path, capital: float) -> dict[str, Any]:
    return engine.load_ledger(str(path), capital)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def three_segment_robustness_gate(candidate: dict[str, Any], audit_path: Path = ROBUSTNESS_AUDIT_PATH) -> tuple[bool, dict[str, Any]]:
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    except Exception:
        audit = {}
    candidate_audits = audit.get("candidate_audits") if isinstance(audit.get("candidate_audits"), list) else [audit]
    candidate_strategy = candidate.get("strategy") if isinstance(candidate.get("strategy"), dict) else {}
    matched_audit = next(
        (
            item for item in candidate_audits
            if isinstance(item, dict)
            and isinstance(item.get("reference_candidate"), dict)
            and str((item.get("reference_candidate") or {}).get("symbol") or "").upper() == str(candidate.get("symbol") or "").upper()
            and (item.get("reference_candidate") or {}).get("interval") == candidate.get("interval")
            and (item.get("reference_candidate") or {}).get("strategy") == candidate_strategy
        ),
        {},
    )
    reference = matched_audit.get("reference_candidate") if isinstance(matched_audit.get("reference_candidate"), dict) else {}
    source_probe = str(matched_audit.get("source_probe") or audit.get("source_probe") or "")
    evidence_path = str(candidate.get("evidence_path") or "")
    same_candidate = (
        str(reference.get("symbol") or "").upper() == str(candidate.get("symbol") or "").upper()
        and reference.get("interval") == candidate.get("interval")
    )
    reference_strategy = reference.get("strategy") if isinstance(reference.get("strategy"), dict) else {}
    same_strategy = bool(reference_strategy and candidate_strategy and reference_strategy == candidate_strategy)
    source_matches = not evidence_path or not source_probe or Path(source_probe).name == Path(evidence_path).name
    allowed = bool(
        same_candidate
        and same_strategy
        and source_matches
        and matched_audit.get("paper_retest_gate") == "allow_minimum_paper_retest"
        and matched_audit.get("reference_current_signal_active") is True
    )
    matched_failed_gates = matched_audit.get("failed_gates")
    if not isinstance(matched_failed_gates, list):
        matched_failed_gates = ["matching_candidate_robustness_audit_missing"]
    return allowed, {
        "batch_status": audit.get("status") or "missing",
        "status": matched_audit.get("status") or "matching_candidate_audit_missing",
        "paper_retest_gate": matched_audit.get("paper_retest_gate") or "block",
        "same_candidate": same_candidate,
        "same_strategy": same_strategy,
        "source_matches": source_matches,
        "reference_current_signal_active": matched_audit.get("reference_current_signal_active"),
        "failed_gates": matched_failed_gates,
        "artifact": str(audit_path),
    }


def current_microstructure_gate(candidate: dict[str, Any], notional_usd: float) -> tuple[bool, dict[str, Any]]:
    symbol = str(candidate.get("symbol") or "").upper()
    interval = str(candidate.get("interval") or "1h")
    try:
        audit = bmdh.audit_symbol(symbol, [interval], bmdh.BINANCE_BASE_URLS, timeout=5.0, kline_limit=120)
    except Exception as exc:
        return False, {"status": "fetch_failed", "symbol": symbol, "errors": [f"{type(exc).__name__}: {exc}"], "live_orders_enabled": False}
    price = audit.get("price") if isinstance(audit.get("price"), dict) else {}
    depth = audit.get("depth") if isinstance(audit.get("depth"), dict) else {}
    recent = audit.get("recent_trades") if isinstance(audit.get("recent_trades"), dict) else {}
    spread_bps = float(depth.get("spread_bps") or 999999.0)
    depth_1pct_bid = float(depth.get("depth_1pct_bid_usd") or 0.0)
    depth_1pct_ask = float(depth.get("depth_1pct_ask_usd") or 0.0)
    top20_ask_depth = float(depth.get("ask_depth_usd_20") or 0.0)
    quote_volume_24h = float(price.get("quote_volume_24h_usd") or 0.0)
    taker_buy_ratio = float(recent.get("taker_buy_quote_ratio") or 0.0)
    required_depth_1pct = max(2500.0, notional_usd * 100.0)
    required_top20_ask_depth = max(200.0, notional_usd * 8.0)
    failed = []
    if spread_bps > 15.0: failed.append("spread_above_15bps")
    if min(depth_1pct_bid, depth_1pct_ask) < required_depth_1pct: failed.append("depth_1pct_below_notional_scaled_floor")
    if top20_ask_depth < required_top20_ask_depth: failed.append("top20_ask_depth_below_8x_notional")
    if quote_volume_24h < 1_000_000.0: failed.append("quote_volume_24h_below_1m")
    if taker_buy_ratio < 0.55: failed.append("recent_taker_buy_ratio_below_0_55")
    return not failed, {
        "status": "pass" if not failed else "block",
        "symbol": symbol,
        "last_price": price.get("last_price"),
        "best_bid": depth.get("best_bid"),
        "best_ask": depth.get("best_ask"),
        "spread_bps": depth.get("spread_bps"),
        "depth_1pct_bid_usd": depth.get("depth_1pct_bid_usd"),
        "depth_1pct_ask_usd": depth.get("depth_1pct_ask_usd"),
        "top20_ask_depth_usd": depth.get("ask_depth_usd_20"),
        "quote_volume_24h_usd": price.get("quote_volume_24h_usd"),
        "recent_taker_buy_quote_ratio": recent.get("taker_buy_quote_ratio"),
        "required_depth_1pct_usd": required_depth_1pct,
        "required_top20_ask_depth_usd": required_top20_ask_depth,
        "failed_gates": failed,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def entry_mode_realized_stats(ledger: dict[str, Any], mode: str) -> dict[str, Any]:
    closed = [
        trade
        for trade in ledger.get("closed_trades", [])
        if trade.get("paper_entry_mode") == mode and trade.get("status") == "closed"
    ]
    realized = sum(float(trade.get("realized_pnl_usd") or 0.0) for trade in closed)
    notional = sum(float(trade.get("notional_usd") or 0.0) for trade in closed)
    hit_count = sum(1 for trade in closed if trade.get("outcome") == "hit")
    count = len(closed)
    win_rate = (hit_count / count * 100.0) if count else None
    return_on_notional = (realized / notional * 100.0) if notional else None
    return {
        "mode": mode,
        "closed_count": count,
        "hit_count": hit_count,
        "win_rate_pct": round(win_rate, 4) if win_rate is not None else None,
        "realized_pnl_usd": round(realized, 6),
        "closed_notional_usd": round(notional, 6),
        "realized_return_on_notional_pct": round(return_on_notional, 4) if return_on_notional is not None else None,
    }


def entry_mode_learning_gate(mode: str, ledger: dict[str, Any], config: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    guard = config.get("paper_entry_mode_learning_guard") or {}
    if not guard.get("enabled", False):
        return True, {"enabled": False, "mode": mode}
    mode_policy = (guard.get("modes") or {}).get(mode, {})
    stats = entry_mode_realized_stats(ledger, mode)
    min_closed = int(mode_policy.get("min_closed_count", guard.get("global_min_closed_count", 3)))
    if stats["closed_count"] < min_closed:
        return True, {
            "enabled": True,
            "mode": mode,
            "decision": "allow_insufficient_sample",
            "stats": stats,
            "min_closed_count": min_closed,
        }
    min_win = float(mode_policy.get("min_win_rate_pct", guard.get("global_min_win_rate_pct", 45.0)))
    min_return = float(
        mode_policy.get(
            "min_realized_return_on_notional_pct",
            guard.get("global_min_realized_return_on_notional_pct", 0.0),
        )
    )
    failed = []
    if stats["win_rate_pct"] is not None and stats["win_rate_pct"] < min_win:
        failed.append("win_rate_below_mode_gate")
    if stats["realized_return_on_notional_pct"] is not None and stats["realized_return_on_notional_pct"] < min_return:
        failed.append("realized_return_below_mode_gate")
    allow = not failed or bool(guard.get("report_only", False))
    return allow, {
        "enabled": True,
        "mode": mode,
        "decision": "allow" if allow and not failed else ("report_only_allow" if allow else "block"),
        "failed_gates": failed,
        "stats": stats,
        "thresholds": {
            "min_closed_count": min_closed,
            "min_win_rate_pct": min_win,
            "min_realized_return_on_notional_pct": min_return,
        },
        "cooldown_reason": mode_policy.get("cooldown_reason"),
    }


def entry_mode_for_sample_mode(sample_mode: str) -> str:
    mapping = {
        "current_signal_near_miss": "current_signal_near_miss_probe",
        "current_signal_quality_scout": "current_signal_quality_scout_probe",
        "three_segment_current_signal_retest": "three_segment_current_signal_retest",
    }
    return mapping.get(sample_mode, "current_signal_validation_probe")


def normalized_label(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().lower().split())


def recovery_plan_items(plan: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in keys:
        for item in plan.get(key) or []:
            if isinstance(item, dict):
                items.append({**item, "_source_key": key})
    return items


def find_recovery_match(
    plan: dict[str, Any],
    names: list[str | None],
    keys: list[str],
    block_statuses: set[str],
) -> dict[str, Any] | None:
    name_set = {normalized_label(name) for name in names if name}
    if not name_set:
        return None
    for item in recovery_plan_items(plan, keys):
        if normalized_label(item.get("name")) in name_set and str(item.get("status")) in block_statuses:
            return item
    return None


def validation_recovery_plan_gate(
    candidate: dict[str, Any],
    entry_mode: str,
    audit: dict[str, Any],
    config: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    gate_cfg = config.get("validation_recovery_plan_gate") or {}
    if not gate_cfg.get("enabled", True):
        return True, {"enabled": False, "decision": "disabled"}
    plan = audit.get("validation_recovery_plan") or {}
    if not isinstance(plan, dict) or not plan:
        return True, {"enabled": True, "decision": "allow_no_recovery_plan"}
    block_statuses = set(gate_cfg.get("block_statuses") or ["retire_from_new_samples", "cooldown_until_retested"])
    block_groups = set(gate_cfg.get("block_groups") or ["entry_mode", "strategy_family", "interval", "symbol"])
    symbol = str(candidate.get("symbol") or "").upper()
    interval = str(candidate.get("interval") or "")
    sample_mode = str(candidate.get("sample_mode") or "current_signal_near_miss")
    strategy_names = [
        f"{sample_mode} {interval}",
        f"{sample_mode.replace('_', ' ')} {interval}",
    ]
    checks = [
        ("entry_mode", [entry_mode], ["retired_entry_modes", "cooldown_entry_modes"]),
        ("strategy_family", strategy_names, ["retired_strategy_families", "cooldown_strategy_families"]),
        ("interval", [interval], ["weak_intervals"]),
        ("symbol", [symbol], ["weak_symbols"]),
    ]
    blocking_matches: list[tuple[str, dict[str, Any]]] = []
    for group, names, keys in checks:
        if group not in block_groups:
            continue
        match = find_recovery_match(plan, names, keys, block_statuses)
        if match:
            blocking_matches.append((group, match))
    interval_only_quality_scout_retest = (
        entry_mode == "current_signal_quality_scout_probe"
        and gate_cfg.get("allow_quality_scout_weak_interval_retest", True)
        and blocking_matches
        and all(group == "interval" for group, _match in blocking_matches)
    )
    if interval_only_quality_scout_retest:
        group, match = blocking_matches[0]
        return True, {
            "enabled": True,
            "decision": "allow_quality_scout_weak_interval_retest",
            "matched_group": group,
            "matched_name": match.get("name"),
            "matched_status": match.get("status"),
            "matched_source_key": match.get("_source_key"),
            "entry_mode": entry_mode,
            "symbol": symbol,
            "interval": interval,
            "sample_mode": sample_mode,
            "new_sample_policy": plan.get("new_sample_policy"),
            "operator_note": (
                "Quality scout may retest a weak interval with minimum paper size only; "
                "this does not bypass retired entry modes, weak symbols, or live-trading safety."
            ),
        }
    if blocking_matches:
        priority = {"entry_mode": 0, "strategy_family": 1, "symbol": 2, "interval": 3}
        group, match = sorted(blocking_matches, key=lambda item: priority.get(item[0], 9))[0]
        return False, {
            "enabled": True,
            "decision": "block_validation_recovery_plan",
            "matched_group": group,
            "matched_name": match.get("name"),
            "matched_status": match.get("status"),
            "matched_source_key": match.get("_source_key"),
            "reason": match.get("reason"),
            "closed_count": match.get("closed_count"),
            "win_rate_pct": match.get("win_rate_pct"),
            "realized_net_return_pct": match.get("realized_net_return_pct"),
            "new_sample_policy": plan.get("new_sample_policy"),
            "operator_note": "Recovery plan blocks this near-miss sampler entry before it can open paper exposure.",
        }
    return True, {
        "enabled": True,
        "decision": "allow_validation_recovery_plan",
        "entry_mode": entry_mode,
        "symbol": symbol,
        "interval": interval,
        "sample_mode": sample_mode,
        "new_sample_policy": plan.get("new_sample_policy"),
    }


def existing_sample_ids(ledger: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for trade in (ledger.get("open_positions") or []) + (ledger.get("closed_trades") or []):
        marker = trade.get("near_miss_sample_key") or trade.get("paper_trade_id")
        if marker:
            ids.add(str(marker))
    return ids


def symbol_open_notional(ledger: dict[str, Any], symbol: str) -> float:
    total = 0.0
    for pos in ledger.get("open_positions") or []:
        if pos.get("symbol") == symbol:
            total += float(pos.get("notional_usd") or 0.0)
    return total


def symbol_has_open_position(ledger: dict[str, Any], symbol: str) -> bool:
    symbol = str(symbol or "").upper()
    return any(str(pos.get("symbol") or "").upper() == symbol for pos in ledger.get("open_positions") or [])


def total_open_notional(ledger: dict[str, Any]) -> float:
    return sum(float(pos.get("notional_usd") or 0.0) for pos in ledger.get("open_positions") or [])


def paper_equity(ledger: dict[str, Any]) -> float:
    equity = ledger.get("equity_usd")
    if isinstance(equity, (int, float)) and equity > 0:
        return float(equity)
    return float(ledger.get("cash_usd") or 0.0) + total_open_notional(ledger)


def capacity_context(ledger: dict[str, Any], args: argparse.Namespace, audit: dict[str, Any]) -> dict[str, Any]:
    equity = paper_equity(ledger)
    cash = float(ledger.get("cash_usd") or 0.0)
    open_notional = total_open_notional(ledger)
    cash_ratio_pct = (cash / equity * 100.0) if equity else 0.0
    open_exposure_pct = (open_notional / equity * 100.0) if equity else 0.0
    sample_gaps = ((audit.get("validation_sample_plan") or {}).get("sample_gaps") or {})
    closed_needed = int(sample_gaps.get("closed_paper_trades_needed") or 0)
    boost_active = (
        not args.disable_target_sprint_open_boost
        and closed_needed > 0
        and cash_ratio_pct >= args.high_cash_ratio_pct
        and open_exposure_pct < args.max_total_open_exposure_pct
        and cash - args.notional >= args.min_cash_reserve_usd
    )
    effective_max_open = args.max_open
    effective_symbol_cap = args.max_symbol_open_notional
    if boost_active:
        effective_max_open = max(effective_max_open, args.max_boosted_open)
        effective_symbol_cap = max(
            effective_symbol_cap,
            equity * args.dynamic_max_symbol_open_notional_pct / 100.0,
        )
    return {
        "equity_usd": round(equity, 6),
        "cash_usd": round(cash, 6),
        "cash_ratio_pct": round(cash_ratio_pct, 4),
        "open_notional_usd": round(open_notional, 6),
        "open_exposure_pct": round(open_exposure_pct, 4),
        "closed_paper_trades_needed": closed_needed,
        "target_sprint_open_boost_active": boost_active,
        "base_max_open": args.max_open,
        "effective_max_open": effective_max_open,
        "base_max_symbol_open_notional": args.max_symbol_open_notional,
        "effective_max_symbol_open_notional": round(effective_symbol_cap, 6),
        "max_total_open_exposure_pct": args.max_total_open_exposure_pct,
        "min_cash_reserve_usd": args.min_cash_reserve_usd,
    }


def make_trade_id(now: dt.datetime, symbol: str, interval: str, seq: int) -> str:
    root = symbol.replace("USDT", "")
    stamp = now.strftime("%Y%m%d-%H%M%S")
    return f"paper-{stamp}-near-miss-{seq:02d}-{root}-{interval}"


def execution_cost_settings(config: dict[str, Any]) -> dict[str, float]:
    fast_cfg = config.get("fast_crypto_paper_auto_trader") or {}
    daily_cfg = config.get("daily_crypto_paper_auto_trader") or {}
    sunday_cfg = config.get("sunday_crypto_realistic_paper_loop") or {}
    source = fast_cfg or daily_cfg or sunday_cfg
    commission_bps = float(source.get("commission_bps", 10.0) or 10.0)
    slippage_bps = float(source.get("base_slippage_bps", 8.0) or 8.0)
    return {
        "commission_bps": commission_bps,
        "slippage_bps": slippage_bps,
    }


def record_paper_order(ledger: dict[str, Any], now: dt.datetime, position: dict[str, Any], reason: str) -> dict[str, Any]:
    orders = ledger.setdefault("paper_orders", [])
    order_id = f"pord-{now.strftime('%Y%m%d-%H%M%S')}-buy-{position['symbol']}-{len(orders) + 1:04d}"
    order = {
        "paper_order_id": order_id,
        "created_at": iso(now),
        "side": "BUY",
        "symbol": position["symbol"],
        "type": "MARKET",
        "time_in_force": "IOC",
        "status": "FILLED",
        "simulated_status": "PAPER_FILLED",
        "simulated_api": True,
        "paper_trade_id": position["paper_trade_id"],
        "requested_notional_usd": position["notional_usd"],
        "filled_notional_usd": position["notional_usd"],
        "executed_quantity": position["quantity"],
        "filled_quantity": position["quantity"],
        "average_fill_price": position["entry_price"],
        "fill_price": position["entry_price"],
        "commission_usd": position.get("entry_commission_usd"),
        "commission_bps": position.get("commission_bps"),
        "spread_bps": position.get("entry_spread_bps"),
        "slippage_bps": position.get("entry_slippage_bps"),
        "depth_1pct_usd": position.get("entry_depth_1pct_usd"),
        "quote_status": position.get("entry_quote_status", "estimated_without_order_book"),
        "quote_reasons": position.get("entry_quote_reasons", ["near_miss_sampler_last_price_estimate"]),
        "order_reason": reason,
        "reason": reason,
        "live_orders_enabled": False,
        "private_api_used": False,
        "notes": "simulated exchange API order only; no private or live endpoint called",
    }
    orders.append(order)
    return order


def open_near_miss_position(
    ledger: dict[str, Any],
    candidate: dict[str, Any],
    *,
    now: dt.datetime,
    notional: float,
    stop_pct: float,
    take_profit_pct: float,
    max_holding: str,
    seq: int,
    signal_source: str,
    entry_mode_learning_gate: dict[str, Any] | None = None,
    validation_recovery_gate: dict[str, Any] | None = None,
    paper_strategy_overlay_decision: dict[str, Any] | None = None,
    paper_strategy_overlay: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol = str(candidate["symbol"]).upper()
    interval = str(candidate.get("interval") or "UNKNOWN")
    sample_mode = str(candidate.get("sample_mode") or "current_signal_near_miss")
    entry_mode = entry_mode_for_sample_mode(sample_mode)
    candidate_source_by_mode = {
        "current_signal_near_miss": "current_signal_probe_near_miss",
        "current_signal_quality_scout": "current_signal_probe_quality_scout",
        "three_segment_current_signal_retest": "current_signal_probe_three_segment_clearance",
    }
    candidate_source = candidate_source_by_mode.get(sample_mode, "current_signal_probe_top_signal")
    microstructure = candidate.get("current_microstructure_gate") if isinstance(candidate.get("current_microstructure_gate"), dict) else {}
    mark_price = float(microstructure.get("last_price") or engine.fetch_binance_price(symbol))
    cost_settings = execution_cost_settings(config or {})
    commission_bps = cost_settings["commission_bps"]
    slippage_bps = cost_settings["slippage_bps"]
    executable_ask = float(microstructure.get("best_ask") or mark_price)
    entry_price = executable_ask * (1.0 + slippage_bps / 10000.0)
    entry_commission = notional * commission_bps / 10000.0
    quantity = max(notional - entry_commission, 0.0) / entry_price
    expires_at = now + engine.parse_holding_window(max_holding)
    sample_key = f"{sample_mode}:{symbol}:{interval}:{signal_source}"
    source_context = candidate.get("market_context") if isinstance(candidate.get("market_context"), dict) else {}
    market_context = {
        **source_context,
        "market_regime": source_context.get("market_regime") or candidate.get("market_regime") or "unknown_or_not_attached",
        "market_atmosphere": source_context.get("market_atmosphere") or candidate.get("market_atmosphere") or "unknown_or_not_attached",
        "short_term_state": source_context.get("short_term_state") or candidate.get("short_term_state") or "unknown_or_not_attached",
        "sentiment_state": source_context.get("sentiment_state") or candidate.get("sentiment_state") or "unknown_or_not_attached",
        "source": source_context.get("source") or candidate.get("market_context_source") or "current_signal_candidate_or_unknown",
    }
    position = {
        "paper_trade_id": make_trade_id(now, symbol, interval, seq),
        "near_miss_sample_key": sample_key,
        "symbol": symbol,
        "side": "long_spot_paper",
        "strategy_family": f"{sample_mode.replace('_', ' ')} {interval}",
        "signal_source": signal_source,
        "paper_entry_mode": entry_mode,
        "market_regime": market_context["market_regime"],
        "market_atmosphere": market_context["market_atmosphere"],
        "short_term_state": market_context["short_term_state"],
        "sentiment_state": market_context["sentiment_state"],
        "market_context_at_entry": market_context,
        "opened_at": iso(now),
        "expires_at": iso(expires_at),
        "entry_price": round(entry_price, 12),
        "entry_mark_price": round(mark_price, 12),
        "quantity": round(quantity, 12),
        "notional_usd": round(notional, 6),
        "entry_commission_usd": round(entry_commission, 6),
        "commission_bps": commission_bps,
        "entry_slippage_bps": slippage_bps,
        "entry_spread_bps": microstructure.get("spread_bps"),
        "entry_depth_1pct_usd": min(float(microstructure.get("depth_1pct_bid_usd") or 0.0), float(microstructure.get("depth_1pct_ask_usd") or 0.0)),
        "entry_quote_status": "verified_public_order_book" if microstructure.get("status") == "pass" else "estimated_without_order_book",
        "entry_quote_reasons": ["public_best_ask_plus_configured_slippage"] if microstructure.get("status") == "pass" else ["near_miss_sampler_last_price_plus_configured_slippage"],
        "realistic_execution_enabled": True,
        "execution_model": {
            "source": "binance_public_best_ask" if microstructure.get("status") == "pass" else "near_miss_sampler_last_price_estimate",
            "uses_commission": True,
            "uses_slippage": True,
            "uses_order_book_spread": False,
            "commission_bps": commission_bps,
            "slippage_bps": slippage_bps,
            "operator_note": "Near-miss sampler uses public last price plus configured commission/slippage; full book-depth checks belong to fast/daily scanner.",
        },
        "current_microstructure_gate": microstructure,
        "stop_pct": float(stop_pct),
        "take_profit_pct": float(take_profit_pct),
        "stop_price": round(entry_price * (1.0 + stop_pct / 100.0), 12),
        "take_profit_price": round(entry_price * (1.0 + take_profit_pct / 100.0), 12),
        "max_holding_window": max_holding,
        "status": "open",
        "outcome": "pending",
        "live_orders_enabled": False,
        "private_api_used": False,
        "entry_mode_learning_gate": entry_mode_learning_gate,
        "validation_recovery_gate": validation_recovery_gate,
        "paper_strategy_overlay_decision": paper_strategy_overlay_decision,
        "strategy_candidate": {
            "source": candidate_source,
            "sample_mode": sample_mode,
            "symbol": symbol,
            "interval": interval,
            "stage": candidate.get("stage"),
            "train_summary": {
                "trade_count": candidate.get("train_trade_count"),
                "win_rate_pct": candidate.get("train_win_rate_pct"),
                "net_return_pct": candidate.get("train_net_return_pct"),
                "final_capital": candidate.get("train_final_capital"),
                "max_drawdown_pct": candidate.get("train_max_drawdown_pct"),
            },
            "oos_summary": {
                "trade_count": candidate.get("oos_trade_count"),
                "win_rate_pct": candidate.get("oos_win_rate_pct"),
                "net_return_pct": candidate.get("oos_net_return_pct"),
                "final_capital": candidate.get("oos_final_capital"),
                "max_drawdown_pct": candidate.get("oos_max_drawdown_pct"),
                "weekly_double_trade_count": candidate.get("oos_weekly_double_trade_count"),
            },
            "promotion_blockers": candidate.get("promotion_blockers") or [],
            "recommended_max_action": candidate.get("recommended_max_action") or "paper_only",
            "entry_mode_learning_gate": entry_mode_learning_gate,
            "validation_recovery_gate": validation_recovery_gate,
            "paper_strategy_overlay_decision": paper_strategy_overlay_decision,
            "market_context_at_entry": market_context,
        },
    }
    pso.apply_overlay_to_position(position, paper_strategy_overlay, paper_strategy_overlay_decision)
    ledger["cash_usd"] = round(float(ledger.get("cash_usd") or 0.0) - notional, 6)
    ledger.setdefault("open_positions", []).append(position)
    entry_order = record_paper_order(ledger, now, position, f"{entry_mode}_forward_validation")
    position["entry_order_id"] = entry_order["paper_order_id"]
    ledger.setdefault("events", []).append(
        {
            "event_type": "paper_open",
            "created_at": iso(now),
            "paper_trade_id": position["paper_trade_id"],
            "symbol": symbol,
            "entry_price": position["entry_price"],
            "notional_usd": position["notional_usd"],
            "paper_entry_mode": position["paper_entry_mode"],
            "entry_mode_learning_gate": position.get("entry_mode_learning_gate"),
            "validation_recovery_gate": position.get("validation_recovery_gate"),
            "paper_strategy_overlay_decision": position.get("paper_strategy_overlay_decision"),
            "notes": "near-miss validation sample only; no live order placed",
        }
    )
    engine.mark_to_market(ledger, {symbol: mark_price})
    return position


def fallback_market_context(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "market_regime": getattr(args, "market_regime", None) or "unknown_or_not_attached",
        "market_atmosphere": getattr(args, "market_atmosphere", None) or "unknown_or_not_attached",
        "short_term_state": getattr(args, "short_term_state", None) or "unknown_or_not_attached",
        "sentiment_state": getattr(args, "sentiment_state", None) or "unknown_or_not_attached",
        "source": getattr(args, "market_context_source", None) or "current_signal_sampler_cli_or_unknown",
    }


def enrich_candidate(
    candidate: dict[str, Any],
    sample_mode: str,
    queue_priority: int,
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = dict(candidate)
    fallback = market_context or {}
    source_context = candidate.get("market_context") if isinstance(candidate.get("market_context"), dict) else {}
    merged_context = {**fallback, **source_context}
    for field in MARKET_CONTEXT_FIELDS:
        value = enriched.get(field) or merged_context.get(field)
        if value:
            enriched[field] = value
            merged_context[field] = value
    merged_context["source"] = (
        source_context.get("source")
        or enriched.get("market_context_source")
        or fallback.get("source")
        or "current_signal_candidate_or_unknown"
    )
    enriched["market_context"] = merged_context
    enriched["market_context_source"] = merged_context["source"]
    enriched["sample_mode"] = sample_mode
    enriched["queue_priority"] = queue_priority
    return enriched


def current_signal_candidate_passes_quality_scout_floor(candidate: dict[str, Any], args: argparse.Namespace) -> bool:
    if candidate.get("current_signal") is not True:
        return False
    if candidate.get("promotion_blockers"):
        return False
    try:
        train_trades = float(candidate.get("train_trade_count") or 0.0)
        train_win = float(candidate.get("train_win_rate_pct") or 0.0)
        train_return = float(candidate.get("train_net_return_pct") or 0.0)
        train_drawdown = float(candidate.get("train_max_drawdown_pct") or 0.0)
        oos_trades = float(candidate.get("oos_trade_count") or 0.0)
        oos_win = float(candidate.get("oos_win_rate_pct") or 0.0)
        oos_return = float(candidate.get("oos_net_return_pct") or 0.0)
        oos_drawdown = float(candidate.get("oos_max_drawdown_pct") or 0.0)
    except (TypeError, ValueError):
        return False
    return (
        train_trades >= args.min_quality_scout_train_trades
        and train_win >= args.min_quality_scout_train_win_rate_pct
        and train_return >= args.min_quality_scout_train_net_return_pct
        and train_drawdown >= args.max_quality_scout_train_drawdown_pct
        and oos_trades >= args.min_quality_scout_oos_trades
        and oos_win >= args.min_quality_scout_oos_win_rate_pct
        and oos_return >= args.min_quality_scout_oos_net_return_pct
        and oos_drawdown >= args.max_quality_scout_oos_drawdown_pct
    )


def current_signal_candidate_has_three_segment_clearance(candidate: dict[str, Any]) -> bool:
    return (
        candidate.get("current_signal") is True
        and candidate.get("authoritative_three_segment") is True
        and candidate.get("validation_protocol") == "train_50_validation_25_final_holdout_25"
        and candidate.get("holdout_used_for_selection") is False
        and not candidate.get("promotion_blockers")
        and candidate.get("recommended_max_action") in {
            "paper_only_research_priority",
            "paper_forward_candidate",
            "paper_only",
        }
    )


def current_signal_candidate_passes_probe_floor(candidate: dict[str, Any], args: argparse.Namespace) -> bool:
    if candidate.get("current_signal") is not True:
        return False
    try:
        oos_trades = float(candidate.get("oos_trade_count") or 0.0)
        oos_win = float(candidate.get("oos_win_rate_pct") or 0.0)
        oos_return = float(candidate.get("oos_net_return_pct") or 0.0)
        oos_drawdown = float(candidate.get("oos_max_drawdown_pct") or 0.0)
    except (TypeError, ValueError):
        return False
    return (
        oos_trades >= args.min_top_signal_oos_trades
        and oos_win >= args.min_top_signal_oos_win_rate_pct
        and oos_return >= args.min_top_signal_oos_net_return_pct
        and oos_drawdown >= args.max_top_signal_oos_drawdown_pct
    )


def robustness_batch_approved_candidates(context: dict[str, Any], evidence_path: str | None) -> list[dict[str, Any]]:
    try:
        batch = json.loads(ROBUSTNESS_AUDIT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    source_probe = Path(str(batch.get("source_probe") or ""))
    if not evidence_path or source_probe.name != Path(str(evidence_path)).name:
        return []
    try:
        probe = json.loads(source_probe.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw_candidates = probe.get("top_candidates") if isinstance(probe.get("top_candidates"), list) else []
    approved: list[dict[str, Any]] = []
    for audit in batch.get("candidate_audits") or []:
        if not isinstance(audit, dict) or audit.get("paper_retest_gate") != "allow_minimum_paper_retest":
            continue
        reference = audit.get("reference_candidate") if isinstance(audit.get("reference_candidate"), dict) else {}
        raw = next(
            (
                item for item in raw_candidates
                if isinstance(item, dict)
                and item.get("symbol") == reference.get("symbol")
                and item.get("interval") == reference.get("interval")
                and item.get("strategy") == reference.get("strategy")
            ),
            None,
        )
        if raw is None:
            continue
        candidate = current_signal_candidate_summary(raw)
        candidate["robustness_batch_source"] = str(ROBUSTNESS_AUDIT_PATH)
        candidate["current_signal_robustness_gate"] = {
            "status": audit.get("status"),
            "paper_retest_gate": audit.get("paper_retest_gate"),
            "reference_current_signal_active": audit.get("reference_current_signal_active"),
            "failed_gates": audit.get("failed_gates") or [],
        }
        approved.append(enrich_candidate(candidate, "three_segment_current_signal_retest", 0, context))
    return approved


def build_candidate_queue(current_signal: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    context = fallback_market_context(args)
    if not args.quality_scout_only:
        for candidate in current_signal.get("near_miss_target_research_candidates") or []:
            symbol = str(candidate.get("symbol") or "").upper()
            interval = str(candidate.get("interval") or "UNKNOWN")
            key = ("current_signal_near_miss", symbol, interval, "")
            if symbol and key not in seen:
                queue.append(enrich_candidate(candidate, "current_signal_near_miss", 0, context))
                seen.add(key)
    if args.include_top_current_signals:
        for candidate in current_signal.get("top_current_signals") or []:
            three_segment_clearance = current_signal_candidate_has_three_segment_clearance(candidate)
            quality_scout = current_signal_candidate_passes_quality_scout_floor(candidate, args)
            validation_probe = current_signal_candidate_passes_probe_floor(candidate, args)
            if args.quality_scout_only and not quality_scout and not three_segment_clearance:
                continue
            if not args.quality_scout_only and not quality_scout and not validation_probe and not three_segment_clearance:
                continue
            symbol = str(candidate.get("symbol") or "").upper()
            interval = str(candidate.get("interval") or "UNKNOWN")
            sample_mode = (
                "three_segment_current_signal_retest"
                if three_segment_clearance
                else "current_signal_quality_scout"
                if quality_scout
                else "current_signal_validation"
            )
            strategy_key = json.dumps(candidate.get("strategy") or {}, sort_keys=True) if sample_mode == "three_segment_current_signal_retest" else ""
            key = (sample_mode, symbol, interval, strategy_key)
            if symbol and key not in seen:
                queue.append(enrich_candidate(candidate, sample_mode, 0 if three_segment_clearance else 1 if quality_scout else 2, context))
                seen.add(key)
            if sum(1 for item in queue if item.get("sample_mode") in {"current_signal_validation", "current_signal_quality_scout", "three_segment_current_signal_retest"}) >= args.max_top_current_signal_candidates:
                break
    for candidate in robustness_batch_approved_candidates(context, current_signal.get("evidence_path")):
        symbol = str(candidate.get("symbol") or "").upper()
        interval = str(candidate.get("interval") or "UNKNOWN")
        strategy_key = json.dumps(candidate.get("strategy") or {}, sort_keys=True)
        key = ("three_segment_current_signal_retest", symbol, interval, strategy_key)
        if symbol and key not in seen:
            queue.append(candidate)
            seen.add(key)
    queue.sort(
        key=lambda item: (
            int(item.get("queue_priority") or 0),
            -float(item.get("oos_win_rate_pct") or 0.0),
            -float(item.get("oos_net_return_pct") or 0.0),
        )
    )
    return queue


def render_report(run: dict[str, Any]) -> str:
    recovery_summary = run.get("validation_recovery_plan_summary") or {}
    lines = [
        f"# Current-Signal Near-Miss Sampler | {run['run_id']}",
        "",
        "No live orders were placed. This script only opens tiny paper validation samples.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| status | `{run['status']}` |",
        f"| dry_run | `{run['dry_run']}` |",
        f"| candidates_seen | `{run['candidates_seen']}` |",
        f"| opened_count | `{len(run['opened_positions'])}` |",
        f"| skipped_count | `{len(run['skipped_candidates'])}` |",
        f"| live_orders_enabled | `{run['live_orders_enabled']}` |",
        "",
        "## Validation Recovery Gate",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| recovery_status | `{recovery_summary.get('status')}` |",
        f"| new_sample_policy | `{recovery_summary.get('new_sample_policy')}` |",
        f"| retired_entry_modes | `{', '.join(recovery_summary.get('retired_entry_modes') or []) or 'none'}` |",
        f"| cooldown_entry_modes | `{', '.join(recovery_summary.get('cooldown_entry_modes') or []) or 'none'}` |",
        f"| retired_strategy_families | `{', '.join(recovery_summary.get('retired_strategy_families') or []) or 'none'}` |",
        f"| blocked_by_recovery_gate | `{sum(1 for item in run['skipped_candidates'] if item.get('reason') == 'validation_recovery_plan_block')}` |",
        "",
        "## Entry Mode Learning Guard",
        "",
        "| Decision | Count |",
        "|---|---:|",
        f"| opened with guard | `{sum(1 for item in run['opened_positions'] if item.get('entry_mode_learning_gate'))}` |",
        f"| blocked by guard | `{sum(1 for item in run['skipped_candidates'] if item.get('reason') == 'entry_mode_learning_guard_block')}` |",
        "",
        "## Opened Paper Samples",
        "",
        "| Symbol | Interval | Trade | Notional | Entry | Guard | Stop | Take | Expiry |",
        "|---|---|---|---:|---:|---|---:|---:|---|",
    ]
    if run["opened_positions"]:
        for pos in run["opened_positions"]:
            interval = ((pos.get("strategy_candidate") or {}).get("interval")) or "-"
            lines.append(
                f"| `{pos.get('symbol')}` | `{interval}` | `{pos.get('paper_trade_id')}` | "
                f"{pos.get('notional_usd')} | {pos.get('entry_price')} | "
                f"{((pos.get('entry_mode_learning_gate') or {}).get('decision') or '-')} | {pos.get('stop_price')} | "
                f"{pos.get('take_profit_price')} | {pos.get('expires_at')} |"
            )
    else:
        lines.append("| none | - | - | - | - | - | - | - | - |")
    lines.extend(["", "## Skipped", "", "| Symbol | Interval | Reason |", "|---|---|---|"])
    if run["skipped_candidates"]:
        for item in run["skipped_candidates"]:
            gate = item.get("entry_mode_learning_gate") or {}
            recovery_gate = item.get("validation_recovery_gate") or {}
            reason = item.get("reason")
            if gate:
                reason = f"{reason}: {gate.get('decision')} {gate.get('failed_gates')}"
            if recovery_gate:
                reason = (
                    f"{reason}: {recovery_gate.get('matched_group')} "
                    f"{recovery_gate.get('matched_name')} -> {recovery_gate.get('matched_status')}"
                )
            lines.append(f"| `{item.get('symbol')}` | `{item.get('interval')}` | {reason} |")
    else:
        lines.append("| none | - | - |")
    lines.append("")
    return "\n".join(lines)


def summarize_recovery_plan(audit: dict[str, Any]) -> dict[str, Any]:
    plan = audit.get("validation_recovery_plan") or {}
    if not isinstance(plan, dict) or not plan:
        return {"status": "missing"}
    monthly = plan.get("monthly_target") or {}
    return {
        "status": plan.get("status"),
        "new_sample_policy": plan.get("new_sample_policy"),
        "target_gap_usd": monthly.get("target_gap_usd"),
        "required_return_pct_from_current_equity": monthly.get("required_return_pct_from_current_equity"),
        "retired_entry_modes": [item.get("name") for item in (plan.get("retired_entry_modes") or [])],
        "cooldown_entry_modes": [item.get("name") for item in (plan.get("cooldown_entry_modes") or [])],
        "eligible_entry_modes": [item.get("name") for item in (plan.get("eligible_entry_modes") or [])],
        "retired_strategy_families": [item.get("name") for item in (plan.get("retired_strategy_families") or [])],
        "cooldown_strategy_families": [item.get("name") for item in (plan.get("cooldown_strategy_families") or [])],
        "eligible_strategy_families": [item.get("name") for item in (plan.get("eligible_strategy_families") or [])],
    }


def self_test() -> dict[str, Any]:
    args = argparse.Namespace(
        quality_scout_only=True,
        include_top_current_signals=True,
        max_top_current_signal_candidates=3,
        min_top_signal_oos_trades=4.0,
        min_top_signal_oos_win_rate_pct=65.0,
        min_top_signal_oos_net_return_pct=25.0,
        max_top_signal_oos_drawdown_pct=-15.0,
        min_quality_scout_train_trades=12.0,
        min_quality_scout_train_win_rate_pct=55.0,
        min_quality_scout_train_net_return_pct=20.0,
        max_quality_scout_train_drawdown_pct=-25.0,
        min_quality_scout_oos_trades=10.0,
        min_quality_scout_oos_win_rate_pct=65.0,
        min_quality_scout_oos_net_return_pct=80.0,
        max_quality_scout_oos_drawdown_pct=-15.0,
        market_regime="risk_on_momentum",
        market_atmosphere="broad_risk_appetite",
        short_term_state="neutral",
        sentiment_state="positive_catalyst_cluster",
        market_context_source="self_test_runner_dynamic_scan",
    )
    current_signal = {
        "top_current_signals": [
            {
                "symbol": "TESTUSDT",
                "interval": "15m",
                "current_signal": True,
                "promotion_blockers": [],
                "train_trade_count": 12,
                "train_win_rate_pct": 60,
                "train_net_return_pct": 25,
                "train_max_drawdown_pct": -10,
                "oos_trade_count": 10,
                "oos_win_rate_pct": 70,
                "oos_net_return_pct": 85,
                "oos_max_drawdown_pct": -8,
            }
        ]
    }
    queue = build_candidate_queue(current_signal, args)
    context = (queue[0].get("market_context") if queue else {}) or {}
    context_ok = (
        len(queue) == 1
        and queue[0].get("market_regime") == "risk_on_momentum"
        and context.get("source") == "self_test_runner_dynamic_scan"
    )
    override = enrich_candidate(
        {
            "symbol": "OWNUSDT",
            "market_context": {
                "market_regime": "risk_off_rebound_watch",
                "source": "candidate_context",
            },
        },
        "current_signal_quality_scout",
        1,
        fallback_market_context(args),
    )
    override_ok = (
        override.get("market_regime") == "risk_off_rebound_watch"
        and (override.get("market_context") or {}).get("source") == "candidate_context"
    )
    clearance_queue = build_candidate_queue(
        {
            "top_current_signals": [
                {
                    "symbol": "CLEARUSDT",
                    "interval": "4h",
                    "current_signal": True,
                    "authoritative_three_segment": True,
                    "validation_protocol": "train_50_validation_25_final_holdout_25",
                    "holdout_used_for_selection": False,
                    "promotion_blockers": [],
                    "recommended_max_action": "paper_only_research_priority",
                    "train_trade_count": 8,
                    "train_win_rate_pct": 50,
                    "train_net_return_pct": 5,
                    "train_max_drawdown_pct": -6,
                    "oos_trade_count": 6,
                    "oos_win_rate_pct": 66.67,
                    "oos_net_return_pct": 8,
                    "oos_max_drawdown_pct": -6,
                }
            ]
        },
        args,
    )
    clearance_ok = (
        len(clearance_queue) == 1
        and clearance_queue[0].get("sample_mode") == "three_segment_current_signal_retest"
        and entry_mode_for_sample_mode(clearance_queue[0].get("sample_mode")) == "three_segment_current_signal_retest"
    )
    with tempfile.TemporaryDirectory() as tmp:
        audit_path = Path(tmp) / "robustness.json"
        audit_path.write_text(
            json.dumps(
                {
                    "status": "pass_minimum_paper_retest",
                    "paper_retest_gate": "allow_minimum_paper_retest",
                    "reference_current_signal_active": True,
                    "source_probe": "probe.json",
                    "reference_candidate": {"symbol": "MMTUSDT", "interval": "4h", "strategy": {"family": "pump_continuation"}},
                }
            ),
            encoding="utf-8",
        )
        robustness_allowed, _ = three_segment_robustness_gate(
            {"symbol": "MMTUSDT", "interval": "4h", "strategy": {"family": "pump_continuation"}, "evidence_path": "probe.json"}, audit_path
        )
        audit_path.write_text(
            json.dumps(
                {
                    "status": "insufficient_robustness",
                    "paper_retest_gate": "dry_run_only",
                    "reference_current_signal_active": False,
                    "source_probe": "probe.json",
                    "reference_candidate": {"symbol": "MMTUSDT", "interval": "4h", "strategy": {"family": "pump_continuation"}},
                    "failed_gates": ["reference_current_signal_not_active"],
                }
            ),
            encoding="utf-8",
        )
        robustness_blocked, _ = three_segment_robustness_gate(
            {"symbol": "MMTUSDT", "interval": "4h", "strategy": {"family": "pump_continuation"}, "evidence_path": "probe.json"}, audit_path
        )
        robustness_gate_ok = robustness_allowed and not robustness_blocked
    ok = context_ok and override_ok and clearance_ok
    return {
        "status": "ok" if ok else "failed",
        "context_from_runner_args": context_ok,
        "candidate_context_overrides_runner_args": override_ok,
        "three_segment_clearance_route_verified": clearance_ok,
        "three_segment_robustness_gate_verified": robustness_gate_ok,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample current-signal near misses into tiny paper positions")
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    parser.add_argument("--capital", type=float, default=500.0)
    parser.add_argument("--experiment-dir", default=str(ACTIVE_ROOT / "experiments"))
    parser.add_argument("--paper-dir", default=str(ACTIVE_ROOT / "paper_trades"))
    parser.add_argument(
        "--recommendation-ledger",
        default=str(WORKSPACE_ROOT / "manual-investment-strategy-operator" / "recommendations" / "recommendation_history.json"),
    )
    parser.add_argument("--notional", type=float, default=25.0)
    parser.add_argument("--max-open", type=int, default=8)
    parser.add_argument("--max-boosted-open", type=int, default=12)
    parser.add_argument("--max-new", type=int, default=1)
    parser.add_argument("--max-symbol-open-notional", type=float, default=75.0)
    parser.add_argument("--dynamic-max-symbol-open-notional-pct", type=float, default=22.0)
    parser.add_argument("--include-top-current-signals", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--quality-scout-only", action="store_true", help="Only consider strict top current-signal quality scouts; skip near-miss and legacy validation probes")
    parser.add_argument("--max-top-current-signal-candidates", type=int, default=3)
    parser.add_argument("--min-top-signal-oos-trades", type=float, default=4.0)
    parser.add_argument("--min-top-signal-oos-win-rate-pct", type=float, default=65.0)
    parser.add_argument("--min-top-signal-oos-net-return-pct", type=float, default=25.0)
    parser.add_argument("--max-top-signal-oos-drawdown-pct", type=float, default=-15.0)
    parser.add_argument("--min-quality-scout-train-trades", type=float, default=12.0)
    parser.add_argument("--min-quality-scout-train-win-rate-pct", type=float, default=55.0)
    parser.add_argument("--min-quality-scout-train-net-return-pct", type=float, default=20.0)
    parser.add_argument("--max-quality-scout-train-drawdown-pct", type=float, default=-25.0)
    parser.add_argument("--min-quality-scout-oos-trades", type=float, default=10.0)
    parser.add_argument("--min-quality-scout-oos-win-rate-pct", type=float, default=65.0)
    parser.add_argument("--min-quality-scout-oos-net-return-pct", type=float, default=80.0)
    parser.add_argument("--max-quality-scout-oos-drawdown-pct", type=float, default=-15.0)
    parser.add_argument("--high-cash-ratio-pct", type=float, default=45.0)
    parser.add_argument("--max-total-open-exposure-pct", type=float, default=85.0)
    parser.add_argument("--min-cash-reserve-usd", type=float, default=50.0)
    parser.add_argument("--disable-target-sprint-open-boost", action="store_true")
    parser.add_argument("--stop-pct", type=float, default=-12.0)
    parser.add_argument("--take-profit-pct", type=float, default=35.0)
    parser.add_argument("--max-holding", default="7d")
    parser.add_argument("--market-regime", default="unknown_or_not_attached")
    parser.add_argument("--market-atmosphere", default="unknown_or_not_attached")
    parser.add_argument("--short-term-state", default="unknown_or_not_attached")
    parser.add_argument("--sentiment-state", default="unknown_or_not_attached")
    parser.add_argument("--market-context-source", default="current_signal_sampler_cli_or_unknown")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--persist-dry-run-report",
        action="store_true",
        help="Persist the experiment/report for a dry run without mutating the paper ledger.",
    )
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0

    now = utc_now()
    run_id = f"{now.strftime('%Y%m%d-%H%M')}-current-signal-near-miss-sampler"
    ledger_path = Path(args.ledger)
    ledger = load_ledger(ledger_path, args.capital)
    config = load_config()
    overlay = pso.load_overlay()
    paper_risk_control = ptrc.evaluate_from_paths(ledger, now=now)
    requested_notional = args.notional
    paper_risk_notional_gate = ptrc.requested_notional_gate(paper_risk_control, requested_notional)
    if paper_risk_notional_gate.get("allow"):
        args.notional = float(paper_risk_notional_gate["effective_notional_usd"])
    audit = run_audit(args.experiment_dir, args.paper_dir, args.recommendation_ledger)
    capacity = capacity_context(ledger, args, audit)
    current_signal = audit.get("current_signal_probe_metrics") or {}
    candidates = build_candidate_queue(current_signal, args)
    existing = existing_sample_ids(ledger)
    opened: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    fetch_failures: list[dict[str, str]] = []
    signal_source = str(current_signal.get("evidence_path") or "current_signal_probe")

    if ledger.get("live_orders_enabled") is True:
        raise SystemExit("ledger live_orders_enabled=true; refusing to sample")

    for candidate in candidates:
        symbol = str(candidate.get("symbol") or "").upper()
        interval = str(candidate.get("interval") or "UNKNOWN")
        sample_mode = str(candidate.get("sample_mode") or "current_signal_near_miss")
        entry_mode = entry_mode_for_sample_mode(sample_mode)
        sample_key = f"{sample_mode}:{symbol}:{interval}:{signal_source}"
        if entry_mode == "three_segment_current_signal_retest":
            robustness_allowed, robustness_gate = three_segment_robustness_gate(candidate)
            if not robustness_allowed:
                skipped.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "reason": "current_signal_robustness_gate_block",
                        "paper_entry_mode": entry_mode,
                        "strategy_candidate": candidate,
                        "current_signal_robustness_gate": robustness_gate,
                    }
                )
                continue
            candidate["current_signal_robustness_gate"] = robustness_gate
            microstructure_allowed, microstructure_gate = current_microstructure_gate(candidate, args.notional)
            if not microstructure_allowed:
                skipped.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "reason": "current_microstructure_gate_block",
                        "paper_entry_mode": entry_mode,
                        "strategy_candidate": candidate,
                        "current_signal_robustness_gate": robustness_gate,
                        "current_microstructure_gate": microstructure_gate,
                    }
                )
                continue
            candidate["current_microstructure_gate"] = microstructure_gate
        if not paper_risk_notional_gate.get("allow"):
            skipped.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "reason": paper_risk_notional_gate.get("reason"),
                    "paper_testnet_risk_control": paper_risk_control,
                    "paper_risk_notional_gate": paper_risk_notional_gate,
                }
            )
            continue
        if not symbol:
            skipped.append({"symbol": symbol, "interval": interval, "reason": "missing_symbol"})
            continue
        recovery_allowed, recovery_gate = validation_recovery_plan_gate(candidate, entry_mode, audit, config)
        if not recovery_allowed:
            skipped.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "reason": "validation_recovery_plan_block",
                    "paper_entry_mode": entry_mode,
                    "validation_recovery_gate": recovery_gate,
                }
            )
            continue
        mode_allowed, mode_gate = entry_mode_learning_gate(entry_mode, ledger, config)
        if not mode_allowed:
            skipped.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "reason": "entry_mode_learning_guard_block",
                    "paper_entry_mode": entry_mode,
                    "entry_mode_learning_gate": mode_gate,
                }
            )
            continue
        overlay_allowed, overlay_decision = pso.combined_overlay_gate(
            entry_mode=entry_mode,
            strategy_family=f"{sample_mode.replace('_', ' ')} {interval}",
            interval=interval,
            overlay=overlay,
        )
        if not overlay_allowed:
            skipped.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "reason": "paper_strategy_overlay_block",
                    "paper_entry_mode": entry_mode,
                    "paper_strategy_overlay_decision": overlay_decision,
                }
            )
            continue
        if len(opened) >= args.max_new:
            skipped.append({"symbol": symbol, "interval": interval, "reason": "max_new_reached"})
            continue
        if sample_key in existing:
            skipped.append({"symbol": symbol, "interval": interval, "reason": "sample_already_exists"})
            continue
        if symbol_has_open_position(ledger, symbol):
            skipped.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "reason": "symbol_already_open",
                    "paper_entry_mode": entry_mode,
                    "operator_note": (
                        "Current-signal sampler does not duplicate open symbols. "
                        "Controlled same-symbol scale-in belongs to the fast/daily paper engine only."
                    ),
                }
            )
            continue
        if float(ledger.get("cash_usd") or 0.0) < args.notional:
            skipped.append({"symbol": symbol, "interval": interval, "reason": "insufficient_paper_cash"})
            continue
        symbol_after = symbol_open_notional(ledger, symbol) + args.notional
        if symbol_after > float(capacity["effective_max_symbol_open_notional"]):
            skipped.append({
                "symbol": symbol,
                "interval": interval,
                "reason": "symbol_open_notional_cap",
                "symbol_open_notional_after_usd": round(symbol_after, 6),
                "effective_symbol_cap_usd": capacity["effective_max_symbol_open_notional"],
            })
            continue
        if len(ledger.get("open_positions") or []) >= int(capacity["effective_max_open"]):
            skipped.append({
                "symbol": symbol,
                "interval": interval,
                "reason": "max_open_reached",
                "open_count": len(ledger.get("open_positions") or []),
                "effective_max_open": capacity["effective_max_open"],
                "target_sprint_open_boost_active": capacity["target_sprint_open_boost_active"],
            })
            continue
        if args.dry_run:
            preview = dict(candidate)
            preview["paper_entry_mode"] = entry_mode
            preview["planned_notional_usd"] = args.notional
            preview["planned_stop_pct"] = args.stop_pct
            preview["planned_take_profit_pct"] = args.take_profit_pct
            preview["planned_max_holding"] = args.max_holding
            opened.append(preview)
            continue
        try:
            position = open_near_miss_position(
                ledger,
                candidate,
                now=now,
                notional=args.notional,
                stop_pct=args.stop_pct,
                take_profit_pct=args.take_profit_pct,
                max_holding=args.max_holding,
                seq=len(opened) + 1,
                signal_source=signal_source,
                entry_mode_learning_gate=mode_gate,
                validation_recovery_gate=recovery_gate,
                paper_strategy_overlay_decision=overlay_decision,
                paper_strategy_overlay=overlay,
                config=config,
            )
            position["paper_testnet_risk_control"] = paper_risk_control
            position["paper_risk_notional_gate"] = paper_risk_notional_gate
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            skipped.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "reason": "entry_price_fetch_failed",
                    "error": error[:500],
                }
            )
            fetch_failures.append({"symbol": symbol, "interval": interval, "error": error[:500]})
            continue
        existing.add(sample_key)
        opened.append(position)

    if not args.dry_run and opened:
        engine.save_ledger(str(ledger_path), ledger)

    run = {
        "run_id": run_id,
        "created_at": iso(now),
        "source_skill": "active-alpha-paper-monitor",
        "sampler_version": "current-signal-near-miss-sampler-v4-quality-scout",
        "strategy_version": pso.strategy_version(overlay),
        "paper_strategy_overlay": {
            "path": str(pso.OVERLAY_PATH.relative_to(WORKSPACE_ROOT)),
            "updated_at": overlay.get("updated_at"),
            "auto_learning_enabled": overlay.get("auto_learning_enabled"),
        },
        "status": "degraded_price_fetch_failed" if fetch_failures and not opened else "ok",
        "dry_run": args.dry_run,
        "dry_run_artifact_persisted": bool(args.dry_run and args.persist_dry_run_report),
        "ledger_mutated": bool(not args.dry_run and opened),
        "live_orders_enabled": False,
        "private_api_keys_used": False,
        "candidates_seen": len(candidates),
        "opened_positions": opened,
        "skipped_candidates": skipped,
        "price_fetch_failures": fetch_failures,
        "capacity_context": capacity,
        "paper_testnet_risk_control": paper_risk_control,
        "paper_risk_notional_gate": paper_risk_notional_gate,
        "requested_notional_usd": requested_notional,
        "effective_notional_usd": args.notional if paper_risk_notional_gate.get("allow") else 0.0,
        "validation_recovery_plan_summary": summarize_recovery_plan(audit),
        "audit_evidence_path": current_signal.get("evidence_path"),
        "outputs": {},
    }
    date = now.astimezone(CHINA_TZ).date().isoformat()
    stamp = now.strftime("%Y%m%d-%H%M")
    experiment_path = ACTIVE_ROOT / "experiments" / f"{stamp}-current-signal-near-miss-sampler.json"
    report_path = ACTIVE_ROOT / "reports" / f"{date}-current-signal-near-miss-sampler-{now.astimezone(CHINA_TZ).strftime('%H%M')}.md"
    run["outputs"] = {
        "experiment": str(experiment_path.relative_to(WORKSPACE_ROOT)),
        "report": str(report_path.relative_to(WORKSPACE_ROOT)),
    }
    suppress_artifact_write = bool(args.dry_run and not args.persist_dry_run_report)
    write_json(experiment_path, run, dry_run=suppress_artifact_write)
    write_text(report_path, render_report(run), dry_run=suppress_artifact_write)
    if args.format == "markdown":
        print(render_report(run))
    else:
        print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
