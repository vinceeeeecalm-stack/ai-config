#!/usr/bin/env python3
"""Forward-sample the latest pressure retest queue into tiny paper probes.

This script is paper-only. It reads research candidates from
target_path_candidate_refresh.py, re-checks current signals from cached public
klines, applies validation capacity and recovery gates, then may open at most
one tiny paper position. It never places live orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
DEFAULT_CACHE_DIRS = [
    str(ACTIVE_ROOT / "cache" / "binance_klines"),
    "/private/tmp/binance_klines_cache_v216",
    "/private/tmp/binance_klines_cache_v216_retry",
]

sys.path.insert(0, str(SCRIPT_DIR))
import paper_portfolio_engine as engine  # noqa: E402
import paper_testnet_risk_control_auditor as ptrc  # noqa: E402
from weekly_goal_strategy_lab import Strategy, load_cached_frames, signal_array  # noqa: E402


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


def stamp(now: dt.datetime) -> str:
    return now.strftime("%Y%m%d-%H%M%S")


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def latest_pressure_payload(experiment_dir: Path) -> tuple[dict[str, Any] | None, Path | None]:
    files = sorted(experiment_dir.glob("*lightweight-extended-history-lab*.json")) + sorted(experiment_dir.glob("*target-path*.json"))
    candidates: list[Path] = []
    for path in files:
        try:
            payload = load_json(path)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, dict) and isinstance(payload.get("pressure_retest_queue"), list):
            candidates.append(path)
    if not candidates:
        return None, None
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    return load_json(latest), latest


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "validation_sample_auditor.py"),
        "--format",
        "json",
        "--paper-dir",
        str(Path(args.ledger).parent),
        "--experiment-dir",
        str(ACTIVE_ROOT / "experiments"),
        "--recommendation-ledger",
        str(WORKSPACE_ROOT / "manual-investment-strategy-operator" / "recommendations" / "recommendation_history.json"),
    ]
    result = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), text=True, capture_output=True, check=False, timeout=args.audit_timeout_seconds)
    if result.returncode != 0:
        return {"status": "failed", "error": result.stderr[-1000:] or result.stdout[-1000:]}
    return json.loads(result.stdout)


def normalized(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().lower().split())


def recovery_items(plan: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in keys:
        for item in plan.get(key) or []:
            if isinstance(item, dict):
                out.append({**item, "_source_key": key})
    return out


def recovery_match(plan: dict[str, Any], names: list[Any], keys: list[str], statuses: set[str]) -> dict[str, Any] | None:
    wanted = {normalized(name) for name in names if name}
    for item in recovery_items(plan, keys):
        if str(item.get("status")) in statuses and normalized(item.get("name")) in wanted:
            return item
    return None


def recovery_gate(candidate: dict[str, Any], audit: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    plan = audit.get("validation_recovery_plan") or {}
    statuses = {"retire_from_new_samples", "cooldown_until_retested"}
    checks = [
        ("entry_mode", [candidate.get("paper_entry_mode")], ["retired_entry_modes", "cooldown_entry_modes"]),
        ("strategy_family", [candidate.get("strategy_family"), f"{candidate.get('strategy_family')} {candidate.get('interval')}", f"{candidate.get('interval')} {candidate.get('strategy_family')}"], ["retired_strategy_families", "cooldown_strategy_families"]),
        ("interval", [candidate.get("interval")], ["weak_intervals"]),
        ("symbol", [candidate.get("symbol")], ["weak_symbols"]),
    ]
    for group, names, keys in checks:
        match = recovery_match(plan, names, keys, statuses)
        if match:
            return False, {
                "decision": "block_validation_recovery_plan",
                "matched_group": group,
                "matched_name": match.get("name"),
                "matched_status": match.get("status"),
                "reason": match.get("reason"),
                "source_key": match.get("_source_key"),
            }
    return True, {"decision": "allow_validation_recovery_plan", "new_sample_policy": plan.get("new_sample_policy")}


def capacity_gate(ledger: dict[str, Any], audit: dict[str, Any], args: argparse.Namespace) -> tuple[bool, dict[str, Any]]:
    cap = audit.get("validation_capacity_state") or {}
    open_positions = ledger.get("open_positions") or []
    cash = float(ledger.get("cash_usd") or 0.0)
    blocked_actions = {"pause_new_samples", "hold_new_samples_temporarily"}
    if str(cap.get("sample_action")) in blocked_actions:
        return False, {"decision": "block_capacity_state", "sample_action": cap.get("sample_action")}
    if len(open_positions) >= args.max_open:
        return False, {"decision": "block_max_open", "open_count": len(open_positions), "max_open": args.max_open}
    if cash - args.notional < args.min_cash_reserve_usd:
        return False, {"decision": "block_cash_reserve", "cash_usd": cash, "notional": args.notional, "min_cash_reserve_usd": args.min_cash_reserve_usd}
    return True, {"decision": "allow_capacity", "sample_action": cap.get("sample_action"), "cash_usd": cash}


def existing_keys(ledger: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for trade in (ledger.get("open_positions") or []) + (ledger.get("closed_trades") or []):
        for key in ["pressure_retest_sample_key", "paper_trade_id"]:
            if trade.get(key):
                out.add(str(trade.get(key)))
    return out


def has_open_symbol(ledger: dict[str, Any], symbol: str) -> bool:
    return any(str(pos.get("symbol") or "").upper() == symbol for pos in ledger.get("open_positions") or [])


def current_signal_from_cache(candidate: dict[str, Any], cache_dirs: list[str]) -> tuple[bool, dict[str, Any]]:
    symbol = str(candidate.get("symbol") or "").upper()
    interval = str(candidate.get("interval") or "")
    frames = load_cached_frames(cache_dirs, symbols={symbol}, intervals={interval})
    frame = frames.get((symbol, interval))
    if frame is None:
        return False, {"status": "missing_frame", "symbol": symbol, "interval": interval}
    strategy = candidate.get("strategy") or {}
    try:
        s = Strategy(**strategy)
        signals = signal_array(frame, s)
        active = bool(len(signals) and signals[-1])
    except Exception as exc:  # noqa: BLE001
        return False, {"status": "signal_error", "error": str(exc), "symbol": symbol, "interval": interval}
    return active, {
        "status": "ok",
        "symbol": symbol,
        "interval": interval,
        "bars": len(frame),
        "latest_bar_time": int(frame["t"].iloc[-1]),
    }


def effective_cache_dirs(args: argparse.Namespace, source_payload: dict[str, Any] | None) -> list[str]:
    explicit = [s.strip() for s in str(args.cache_dirs or "").split(",") if s.strip()]
    if explicit:
        return explicit
    source_dirs = []
    if isinstance(source_payload, dict):
        source_dirs = [str(s).strip() for s in source_payload.get("cache_dirs") or [] if str(s).strip()]
    return source_dirs or DEFAULT_CACHE_DIRS


def make_trade_id(now: dt.datetime, symbol: str, interval: str) -> str:
    root = symbol.replace("USDT", "")
    return f"paper-{stamp(now)}-pressure-retest-{root}-{interval}"


def record_order(ledger: dict[str, Any], now: dt.datetime, position: dict[str, Any]) -> None:
    orders = ledger.setdefault("paper_orders", [])
    order = {
        "paper_order_id": f"pord-{stamp(now)}-buy-{position['symbol']}-{len(orders) + 1:04d}",
        "run_id": position.get("run_id"),
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
        "quote_status": position.get("entry_quote_status"),
        "quote_reasons": position.get("entry_quote_reasons", []),
        "reason": "pressure_retest_probe_forward_validation",
        "order_reason": "pressure_retest_probe_forward_validation",
        "live_orders_enabled": False,
        "private_api_used": False,
        "notes": "simulated paper order only; no private or live endpoint called",
    }
    orders.append(order)
    position["entry_order_id"] = order["paper_order_id"]
    position["simulated_api_order_lifecycle"] = {
        "entry_order_id": order["paper_order_id"],
        "entry_status": "FILLED",
        "exit_order_id": None,
        "exit_status": None,
        "live_orders_enabled": False,
    }


def open_pressure_position(ledger: dict[str, Any], candidate: dict[str, Any], args: argparse.Namespace, gates: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    symbol = str(candidate["symbol"]).upper()
    interval = str(candidate.get("interval") or "UNKNOWN")
    raw_price = engine.fetch_binance_price(symbol)
    commission_bps = float(args.commission_bps)
    slippage_bps = float(args.slippage_bps)
    commission_usd = round(args.notional * commission_bps / 10000.0, 6)
    entry_price = raw_price * (1.0 + slippage_bps / 10000.0)
    quantity = max(args.notional - commission_usd, 0.0) / entry_price
    expires_at = now + engine.parse_holding_window(args.max_holding)
    sample_key = candidate.get("queue_id") or f"pressure:{symbol}:{interval}:{candidate.get('strategy_family')}"
    position = {
        "paper_trade_id": make_trade_id(now, symbol, interval),
        "pressure_retest_sample_key": sample_key,
        "symbol": symbol,
        "side": "long_spot_paper",
        "strategy_family": str(candidate.get("strategy_family") or "pressure_retest"),
        "signal_source": "pressure_retest_queue_sampler",
        "paper_entry_mode": "pressure_retest_probe",
        "run_id": f"{stamp(now)}-pressure-retest-queue-sampler",
        "opened_at": iso(now),
        "expires_at": iso(expires_at),
        "entry_price": round(entry_price, 12),
        "entry_mark_price": round(raw_price, 12),
        "quantity": round(quantity, 12),
        "notional_usd": round(args.notional, 6),
        "gross_quote_usd": round(args.notional, 6),
        "entry_commission_usd": commission_usd,
        "commission_bps": commission_bps,
        "entry_slippage_bps": slippage_bps,
        "entry_spread_bps": None,
        "entry_depth_1pct_usd": None,
        "entry_quote_status": "verified_without_order_book",
        "entry_quote_reasons": ["pressure_retest_public_ticker_price_plus_configured_slippage"],
        "stop_pct": float(args.stop_pct),
        "take_profit_pct": float(args.take_profit_pct),
        "stop_price": round(entry_price * (1.0 + args.stop_pct / 100.0), 12),
        "take_profit_price": round(entry_price * (1.0 + args.take_profit_pct / 100.0), 12),
        "max_holding_window": args.max_holding,
        "status": "open",
        "outcome": "pending",
        "realistic_execution_enabled": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "gates": gates,
        "strategy_candidate": candidate,
        "execution_model": {
            "type": "realistic_spot_paper",
            "uses_commission": True,
            "uses_slippage": True,
            "uses_bid_ask_spread": False,
            "uses_depth_1pct": False,
            "operator_note": "Pressure retest sampler uses public ticker plus configured friction; no live or private endpoint is called.",
        },
    }
    ledger["cash_usd"] = round(float(ledger.get("cash_usd") or 0.0) - args.notional, 6)
    ledger.setdefault("open_positions", []).append(position)
    record_order(ledger, now, position)
    ledger.setdefault("events", []).append(
        {
            "event_type": "paper_open",
            "created_at": iso(now),
            "paper_trade_id": position["paper_trade_id"],
            "symbol": symbol,
            "notional_usd": position["notional_usd"],
            "paper_entry_mode": "pressure_retest_probe",
            "notes": "pressure retest paper sample only; no live order placed",
        }
    )
    engine.mark_to_market(ledger, {symbol: entry_price})
    return position


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Pressure Retest Queue Sampler | {payload['run_id']}",
        "",
        "Paper-only. No live orders were placed.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| status | `{payload.get('status')}` |",
        f"| dry_run | `{payload.get('dry_run')}` |",
        f"| candidates_seen | `{payload.get('candidates_seen')}` |",
        f"| opened_count | `{len(payload.get('opened_positions') or [])}` |",
        f"| skipped_count | `{len(payload.get('skipped_candidates') or [])}` |",
        f"| live_orders_enabled | `{payload.get('live_orders_enabled')}` |",
        "",
        "## Opened",
        "",
        "| Symbol | Interval | Trade | Notional | Entry |",
        "|---|---|---|---:|---:|",
    ]
    for pos in payload.get("opened_positions") or []:
        lines.append(f"| `{pos.get('symbol')}` | `{(pos.get('strategy_candidate') or {}).get('interval')}` | `{pos.get('paper_trade_id')}` | {pos.get('notional_usd')} | {pos.get('entry_price')} |")
    if not payload.get("opened_positions"):
        lines.append("| none | - | - | - | - |")
    lines.extend(["", "## Skipped", "", "| Symbol | Interval | Reason |", "|---|---|---|"])
    for item in payload.get("skipped_candidates") or []:
        lines.append(f"| `{item.get('symbol')}` | `{item.get('interval')}` | {item.get('reason')} |")
    if not payload.get("skipped_candidates"):
        lines.append("| none | - | - |")
    return "\n".join(lines) + "\n"


def build_run(args: argparse.Namespace) -> dict[str, Any]:
    now = utc_now()
    run_id = f"{stamp(now)}-pressure-retest-queue-sampler"
    source_payload, source_path = latest_pressure_payload(ACTIVE_ROOT / "experiments") if not args.queue_json else (load_json(args.queue_json), Path(args.queue_json))
    ledger = engine.load_ledger(args.ledger, args.capital)
    paper_risk_control = ptrc.evaluate_from_paths(ledger, now=now)
    requested_notional = args.notional
    paper_risk_notional_gate = ptrc.requested_notional_gate(paper_risk_control, requested_notional)
    if paper_risk_notional_gate.get("allow"):
        args.notional = float(paper_risk_notional_gate["effective_notional_usd"])
    audit = run_audit(args)
    opened: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    queue = (source_payload or {}).get("pressure_retest_queue") or []
    cache_dirs = effective_cache_dirs(args, source_payload)
    cap_ok, cap_info = capacity_gate(ledger, audit, args)
    seen = existing_keys(ledger)
    for candidate in queue[: args.max_candidates]:
        symbol = str(candidate.get("symbol") or "").upper()
        interval = str(candidate.get("interval") or "UNKNOWN")
        item = {"symbol": symbol, "interval": interval, "candidate": candidate}
        sample_key = candidate.get("queue_id") or f"pressure:{symbol}:{interval}:{candidate.get('strategy_family')}"
        if not paper_risk_notional_gate.get("allow"):
            skipped.append(
                {
                    **item,
                    "reason": paper_risk_notional_gate.get("reason"),
                    "paper_testnet_risk_control": paper_risk_control,
                    "paper_risk_notional_gate": paper_risk_notional_gate,
                }
            )
            continue
        if not cap_ok:
            skipped.append({**item, "reason": f"capacity_gate:{cap_info.get('decision')}", "capacity_gate": cap_info})
            continue
        if sample_key in seen:
            skipped.append({**item, "reason": "sample_already_exists"})
            continue
        if has_open_symbol(ledger, symbol) and not args.allow_existing_symbol:
            skipped.append({**item, "reason": "symbol_already_open"})
            continue
        rec_ok, rec_info = recovery_gate(candidate, audit)
        if not rec_ok:
            skipped.append({**item, "reason": "validation_recovery_plan_block", "recovery_gate": rec_info})
            continue
        current_signal, signal_info = current_signal_from_cache(candidate, cache_dirs)
        if not current_signal:
            skipped.append({**item, "reason": f"current_signal_false:{signal_info.get('status')}", "signal_check": signal_info})
            continue
        if len(opened) >= args.max_new_positions:
            skipped.append({**item, "reason": "max_new_positions_reached"})
            continue
        gates = {"capacity_gate": cap_info, "recovery_gate": rec_info, "signal_check": signal_info}
        if args.dry_run:
            skipped.append({**item, "reason": "dry_run_would_open", "gates": gates})
            continue
        position = open_pressure_position(ledger, candidate, args, gates, now)
        position["paper_testnet_risk_control"] = paper_risk_control
        position["paper_risk_notional_gate"] = paper_risk_notional_gate
        opened.append(position)
        seen.add(sample_key)
    if opened and not args.dry_run:
        engine.save_ledger(args.ledger, ledger)
    status = "ok" if source_payload else "degraded_missing_queue"
    return {
        "run_id": run_id,
        "created_at": iso(now),
        "sampler_version": "pressure-retest-queue-sampler-v1",
        "status": status,
        "dry_run": args.dry_run,
        "live_orders_enabled": False,
        "private_api_used": False,
        "private_api_keys_used": False,
        "allow_real_orders": False,
        "source_queue_path": str(source_path) if source_path else None,
        "cache_dirs": cache_dirs,
        "audit_status": audit.get("status"),
        "paper_testnet_risk_control": paper_risk_control,
        "paper_risk_notional_gate": paper_risk_notional_gate,
        "requested_notional_usd": requested_notional,
        "effective_notional_usd": args.notional if paper_risk_notional_gate.get("allow") else 0.0,
        "candidates_seen": len(queue),
        "opened_positions": opened,
        "skipped_candidates": skipped,
        "operator_note": "Pressure sampler is paper-only. Opened samples, if any, are forward evidence and not live-trading permission.",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue-json", default="")
    ap.add_argument(
        "--cache-dirs",
        default="",
        help="Comma-separated kline cache dirs. Defaults to the source queue artifact cache_dirs, then legacy fallback caches.",
    )
    ap.add_argument("--ledger", default=str(LEDGER_PATH))
    ap.add_argument("--capital", type=float, default=500.0)
    ap.add_argument("--notional", type=float, default=25.0)
    ap.add_argument("--stop-pct", type=float, default=-8.0)
    ap.add_argument("--take-profit-pct", type=float, default=20.0)
    ap.add_argument("--max-holding", default="72h")
    ap.add_argument("--max-open", type=int, default=12)
    ap.add_argument("--max-new-positions", type=int, default=1)
    ap.add_argument("--max-candidates", type=int, default=12)
    ap.add_argument("--min-cash-reserve-usd", type=float, default=25.0)
    ap.add_argument("--commission-bps", type=float, default=10.0)
    ap.add_argument("--slippage-bps", type=float, default=8.0)
    ap.add_argument("--allow-existing-symbol", action="store_true")
    ap.add_argument("--audit-timeout-seconds", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--compact-output", action="store_true")
    args = ap.parse_args()

    payload = build_run(args)
    now = utc_now()
    output = Path(args.output) if args.output else ACTIVE_ROOT / "experiments" / f"{payload['run_id']}.json"
    report = Path(args.report) if args.report else ACTIVE_ROOT / "reports" / f"{now.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()}-pressure-retest-sampler-{stamp(now)}.md"
    payload["outputs"] = {"experiment": str(output), "report": str(report)}
    write_json(output, payload, dry_run=args.dry_run)
    write_text(report, render_report(payload), dry_run=args.dry_run)
    if args.compact_output:
        print(json.dumps({k: payload.get(k) for k in ["run_id", "status", "dry_run", "live_orders_enabled", "source_queue_path", "candidates_seen", "outputs"]} | {"opened_count": len(payload.get("opened_positions") or []), "skipped_count": len(payload.get("skipped_candidates") or [])}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
