#!/usr/bin/env python3
"""Audit and evaluate paper/testnet-only portfolio risk controls.

The module is also imported by the shared crypto paper loop. It never fetches
market data, never mutates the ledger, and never authorizes live trading.
"""

from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
CONFIG_PATH = ROOT / "config" / "active_alpha_monitor_config.json"
LEDGER_PATH = ROOT / "paper_trades" / "paper_portfolio_ledger.json"
OVERLAY_PATH = ROOT / "config" / "paper_strategy_auto_overlay.json"
REPORTS_DIR = ROOT / "reports"
EXPERIMENTS_DIR = ROOT / "experiments"
LATEST_REPORT = REPORTS_DIR / "PAPER_TESTNET_RISK_CONTROL_AUDIT.md"
LATEST_EXPERIMENT = EXPERIMENTS_DIR / "paper-testnet-risk-control-audit.json"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def parse_dt(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rel(path: Path | None) -> str:
    if not path:
        return "-"
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def latest(pattern: str) -> Path | None:
    paths = list(EXPERIMENTS_DIR.glob(pattern))
    return max(paths, key=lambda item: item.stat().st_mtime) if paths else None


def duplicate_values(rows: list[dict[str, Any]], field: str) -> list[str]:
    counts = Counter(str(item.get(field)) for item in rows if item.get(field))
    return sorted(value for value, count in counts.items() if count > 1)


def contract_errors(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_positive = (
        "max_daily_realized_loss_pct",
        "max_portfolio_drawdown_pct",
        "loss_streak_lookback_hours",
        "reduce_after_consecutive_losses",
        "pause_after_consecutive_losses",
        "pause_hours",
        "max_single_position_pct_of_equity",
        "max_total_open_exposure_pct_of_equity",
        "max_open_positions",
    )
    if contract.get("enabled") is not True:
        errors.append("risk_control_disabled")
    if contract.get("scope") != "paper_and_spot_testnet_only":
        errors.append("scope_not_paper_and_spot_testnet_only")
    for field in required_positive:
        if as_float(contract.get(field), 0.0) <= 0:
            errors.append(f"invalid_or_missing:{field}")
    if contract.get("live_orders_enabled") is not False:
        errors.append("live_orders_enabled_not_false")
    if contract.get("withdrawals_enabled") is not False:
        errors.append("withdrawals_enabled_not_false")
    if contract.get("margin_futures_perpetuals_enabled") is not False:
        errors.append("margin_futures_perpetuals_enabled_not_false")
    if contract.get("duplicate_id_policy") != "block_new_entries":
        errors.append("duplicate_id_policy_not_blocking")
    rollback = contract.get("rollback_contract") if isinstance(contract.get("rollback_contract"), dict) else {}
    for field in (
        "strategy_snapshot_before_apply",
        "stable_change_id_required",
        "append_only_decision_log_required",
        "forward_degradation_rollback_required",
        "human_approval_before_live_required",
    ):
        if rollback.get(field) is not True:
            errors.append(f"rollback_contract_missing:{field}")
    return errors


def rollback_evidence(overlay: dict[str, Any], evolver: dict[str, Any]) -> dict[str, Any]:
    change_log = overlay.get("change_log") if isinstance(overlay.get("change_log"), list) else []
    change_ids = [str(item.get("change_id")) for item in change_log if isinstance(item, dict) and item.get("change_id")]
    rollback_checks = evolver.get("rollback_checks") if isinstance(evolver.get("rollback_checks"), list) else []
    safe_flags = (
        overlay.get("auto_apply_scope") == "paper_only"
        and overlay.get("live_orders_enabled") is False
        and overlay.get("private_api_used") is False
        and overlay.get("allow_real_orders") is False
    )
    complete = bool(
        overlay.get("strategy_version")
        and change_ids
        and len(change_ids) == len(set(change_ids))
        and rollback_checks
        and safe_flags
    )
    return {
        "status": "proven_offline" if complete else "incomplete",
        "strategy_version": overlay.get("strategy_version"),
        "change_log_count": len(change_log),
        "unique_change_id_count": len(set(change_ids)),
        "duplicate_change_ids": duplicate_values([{"id": value} for value in change_ids], "id"),
        "rollback_check_count": len(rollback_checks),
        "safe_flags": safe_flags,
        "complete": complete,
    }


def evaluate_risk_state(
    ledger: dict[str, Any],
    contract: dict[str, Any],
    now: dt.datetime | None = None,
    overlay: dict[str, Any] | None = None,
    evolver: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or now_local()
    if now.tzinfo is None:
        now = now.replace(tzinfo=LOCAL_TZ)
    closed = [item for item in ledger.get("closed_trades") or [] if isinstance(item, dict)]
    orders = [item for item in ledger.get("paper_orders") or [] if isinstance(item, dict)]
    open_positions = [item for item in ledger.get("open_positions") or [] if isinstance(item, dict)]
    closed = sorted(closed, key=lambda item: str(item.get("closed_at") or item.get("opened_at") or ""))

    local_day = now.astimezone(LOCAL_TZ).date()
    daily_trades = []
    for trade in closed:
        closed_at = parse_dt(trade.get("closed_at"))
        if closed_at and closed_at.astimezone(LOCAL_TZ).date() == local_day:
            daily_trades.append(trade)
    daily_realized = round(sum(as_float(item.get("realized_pnl_usd")) for item in daily_trades), 6)
    equity = as_float(ledger.get("equity_usd"), as_float(ledger.get("cash_usd")))
    day_start_equity = max(0.01, equity - daily_realized)
    daily_realized_pct = round(daily_realized / day_start_equity * 100.0, 6)

    lookback_hours = as_float(contract.get("loss_streak_lookback_hours"), 72.0)
    cutoff = now.astimezone(dt.timezone.utc) - dt.timedelta(hours=lookback_hours)
    recent = [
        item for item in closed
        if (parse_dt(item.get("closed_at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) >= cutoff
    ]
    consecutive_losses = 0
    for trade in reversed(recent):
        if as_float(trade.get("realized_pnl_usd")) < 0:
            consecutive_losses += 1
        else:
            break

    duplicate_trade_ids = duplicate_values([*closed, *open_positions], "paper_trade_id")
    duplicate_order_ids = duplicate_values(orders, "paper_order_id")
    duplicate_open_symbols = sorted(
        symbol for symbol, count in Counter(
            str(item.get("symbol") or "").upper() for item in open_positions if item.get("symbol")
        ).items() if count > int(contract.get("max_positions_per_symbol") or 1)
    )
    open_exposure = round(sum(as_float(item.get("notional_usd")) for item in open_positions), 6)
    exposure_pct = round(open_exposure / equity * 100.0, 6) if equity > 0 else 0.0
    max_symbol_exposure = 0.0
    by_symbol: dict[str, float] = {}
    for item in open_positions:
        symbol = str(item.get("symbol") or "").upper()
        by_symbol[symbol] = by_symbol.get(symbol, 0.0) + as_float(item.get("notional_usd"))
    if equity > 0 and by_symbol:
        max_symbol_exposure = max(by_symbol.values()) / equity * 100.0

    errors = contract_errors(contract)
    safety_errors = []
    if ledger.get("live_orders_enabled") is not False:
        safety_errors.append("ledger_live_orders_enabled_not_false")
    if ledger.get("private_api_used") is not False:
        safety_errors.append("ledger_private_api_used_not_false")
    integrity_errors = []
    if duplicate_trade_ids:
        integrity_errors.append("duplicate_paper_trade_ids")
    if duplicate_order_ids:
        integrity_errors.append("duplicate_paper_order_ids")
    if duplicate_open_symbols:
        integrity_errors.append("symbol_position_limit_exceeded")
    if len(open_positions) > int(contract.get("max_open_positions") or 0):
        integrity_errors.append("max_open_positions_exceeded")
    if exposure_pct > as_float(contract.get("max_total_open_exposure_pct_of_equity"), 0.0):
        integrity_errors.append("max_total_open_exposure_exceeded")
    if max_symbol_exposure > as_float(contract.get("max_single_position_pct_of_equity"), 0.0):
        integrity_errors.append("max_symbol_exposure_exceeded")

    triggers = []
    max_daily_loss_pct = abs(as_float(contract.get("max_daily_realized_loss_pct"), 5.0))
    max_drawdown_pct = abs(as_float(contract.get("max_portfolio_drawdown_pct"), 15.0))
    current_drawdown_pct = as_float(ledger.get("max_drawdown_pct"))
    if daily_realized_pct <= -max_daily_loss_pct:
        triggers.append("max_daily_realized_loss_breached")
    if current_drawdown_pct <= -max_drawdown_pct:
        triggers.append("max_portfolio_drawdown_breached")
    if consecutive_losses >= int(contract.get("pause_after_consecutive_losses") or 3):
        triggers.append("consecutive_loss_pause_triggered")

    size_multiplier = 1.0
    if consecutive_losses >= int(contract.get("reduce_after_consecutive_losses") or 2):
        size_multiplier = as_float(contract.get("reduced_size_multiplier"), 0.5)
    if errors or safety_errors or integrity_errors or triggers:
        decision = "block_new_entries"
        size_multiplier = 0.0
    elif size_multiplier < 1.0:
        decision = "reduce_new_entry_size"
    else:
        decision = "allow_paper_only"

    last_close = parse_dt(recent[-1].get("closed_at")) if recent else None
    cooldown_until = None
    if decision == "block_new_entries" and "consecutive_loss_pause_triggered" in triggers and last_close:
        cooldown_until = (last_close + dt.timedelta(hours=as_float(contract.get("pause_hours"), 24.0))).isoformat()

    rollback = rollback_evidence(overlay or {}, evolver or {})
    control_contract_proven = not errors and rollback.get("complete") is True
    audit_status = "pass" if control_contract_proven and not safety_errors and not integrity_errors else "blocked"
    max_notional = min(
        as_float(contract.get("max_single_trade_notional_usd"), 150.0),
        equity * as_float(contract.get("max_single_position_pct_of_equity"), 30.0) / 100.0,
    ) * size_multiplier
    return {
        "status": audit_status,
        "control_contract_status": "proven_offline" if control_contract_proven else "incomplete",
        "phase4_control_evidence_ready": audit_status == "pass",
        "risk_decision": decision,
        "recommended_new_entry_size_multiplier": round(size_multiplier, 6),
        "max_new_entry_notional_usd": round(max(0.0, max_notional), 6),
        "cooldown_until": cooldown_until,
        "triggers": triggers,
        "contract_errors": errors,
        "safety_errors": safety_errors,
        "integrity_errors": integrity_errors,
        "metrics": {
            "local_day": local_day.isoformat(),
            "equity_usd": round(equity, 6),
            "daily_closed_trade_count": len(daily_trades),
            "daily_realized_pnl_usd": daily_realized,
            "daily_realized_pnl_pct": daily_realized_pct,
            "max_drawdown_pct": current_drawdown_pct,
            "recent_closed_trade_count": len(recent),
            "consecutive_loss_streak": consecutive_losses,
            "open_position_count": len(open_positions),
            "open_exposure_usd": open_exposure,
            "open_exposure_pct": exposure_pct,
            "max_symbol_exposure_pct": round(max_symbol_exposure, 6),
        },
        "idempotency": {
            "duplicate_paper_trade_ids": duplicate_trade_ids,
            "duplicate_paper_order_ids": duplicate_order_ids,
            "symbols_over_position_limit": duplicate_open_symbols,
        },
        "rollback_evidence": rollback,
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": False,
        "max_allowed_action": "paper_only",
    }


def evaluate_from_paths(
    ledger: dict[str, Any],
    now: dt.datetime | None = None,
    config_path: Path = CONFIG_PATH,
    overlay_path: Path = OVERLAY_PATH,
) -> dict[str, Any]:
    config = read_json(config_path, {}) or {}
    overlay = read_json(overlay_path, {}) or {}
    evolver_path = latest("*paper-strategy-auto-evolver.json")
    evolver = read_json(evolver_path, {}) or {}
    return evaluate_risk_state(
        ledger,
        config.get("paper_testnet_risk_controls") or {},
        now,
        overlay,
        evolver,
    )


def requested_notional_gate(
    risk_state: dict[str, Any],
    requested_notional_usd: float,
    minimum_notional_usd: float = 25.0,
) -> dict[str, Any]:
    requested = max(0.0, as_float(requested_notional_usd))
    if risk_state.get("risk_decision") == "block_new_entries":
        return {
            "allow": False,
            "requested_notional_usd": requested,
            "effective_notional_usd": 0.0,
            "reason": "paper_testnet_risk_control_block_new_entries",
        }
    multiplier = as_float(risk_state.get("recommended_new_entry_size_multiplier"), 0.0)
    max_notional = as_float(risk_state.get("max_new_entry_notional_usd"), 0.0)
    effective = round(min(requested * multiplier, max_notional), 6)
    if effective < minimum_notional_usd:
        return {
            "allow": False,
            "requested_notional_usd": requested,
            "effective_notional_usd": effective,
            "reason": "paper_testnet_risk_reduced_notional_below_minimum",
        }
    return {
        "allow": True,
        "requested_notional_usd": requested,
        "effective_notional_usd": effective,
        "reason": "paper_testnet_risk_control_allow"
        if multiplier >= 1.0
        else "paper_testnet_risk_control_reduced_size",
    }


def self_test_contract() -> dict[str, Any]:
    return {
        "enabled": True,
        "scope": "paper_and_spot_testnet_only",
        "max_daily_realized_loss_pct": 5,
        "max_portfolio_drawdown_pct": 15,
        "loss_streak_lookback_hours": 72,
        "reduce_after_consecutive_losses": 2,
        "reduced_size_multiplier": 0.5,
        "pause_after_consecutive_losses": 3,
        "pause_hours": 24,
        "max_single_trade_notional_usd": 150,
        "max_single_position_pct_of_equity": 30,
        "max_total_open_exposure_pct_of_equity": 85,
        "max_open_positions": 8,
        "max_positions_per_symbol": 3,
        "duplicate_id_policy": "block_new_entries",
        "live_orders_enabled": False,
        "withdrawals_enabled": False,
        "margin_futures_perpetuals_enabled": False,
        "rollback_contract": {
            "strategy_snapshot_before_apply": True,
            "stable_change_id_required": True,
            "append_only_decision_log_required": True,
            "forward_degradation_rollback_required": True,
            "human_approval_before_live_required": True,
        },
    }


def self_test_evidence() -> tuple[dict[str, Any], dict[str, Any]]:
    overlay = {
        "strategy_version": "fixture-v1",
        "auto_apply_scope": "paper_only",
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "change_log": [{"change_id": "pc-fixture-1"}],
    }
    evolver = {"rollback_checks": [{"change_id": "pc-fixture-1", "status_after": "paper_ab_testing"}]}
    return overlay, evolver


def run_self_test() -> dict[str, Any]:
    now = dt.datetime(2026, 7, 11, 12, 0, tzinfo=LOCAL_TZ)
    base = {
        "equity_usd": 500.0,
        "cash_usd": 500.0,
        "max_drawdown_pct": -2.0,
        "open_positions": [],
        "closed_trades": [],
        "paper_orders": [],
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    contract = self_test_contract()
    overlay, evolver = self_test_evidence()
    allowed = evaluate_risk_state(base, contract, now, overlay, evolver)
    assert allowed["risk_decision"] == "allow_paper_only"
    assert allowed["control_contract_status"] == "proven_offline"

    two_losses = json.loads(json.dumps(base))
    two_losses["closed_trades"] = [
        {"paper_trade_id": "t1", "closed_at": "2026-07-11T01:00:00+00:00", "realized_pnl_usd": -1},
        {"paper_trade_id": "t2", "closed_at": "2026-07-11T02:00:00+00:00", "realized_pnl_usd": -1},
    ]
    reduced = evaluate_risk_state(two_losses, contract, now, overlay, evolver)
    assert reduced["risk_decision"] == "reduce_new_entry_size"
    assert reduced["recommended_new_entry_size_multiplier"] == 0.5

    three_losses = json.loads(json.dumps(two_losses))
    three_losses["closed_trades"].append(
        {"paper_trade_id": "t3", "closed_at": "2026-07-11T03:00:00+00:00", "realized_pnl_usd": -1}
    )
    paused = evaluate_risk_state(three_losses, contract, now, overlay, evolver)
    assert paused["risk_decision"] == "block_new_entries"
    assert "consecutive_loss_pause_triggered" in paused["triggers"]
    assert requested_notional_gate(paused, 25.0)["allow"] is False
    reduced_notional = requested_notional_gate(reduced, 75.0)
    assert reduced_notional["allow"] is True
    assert reduced_notional["effective_notional_usd"] == 37.5

    daily_breach = json.loads(json.dumps(base))
    daily_breach["equity_usd"] = 470.0
    daily_breach["closed_trades"] = [
        {"paper_trade_id": "loss", "closed_at": "2026-07-11T02:00:00+00:00", "realized_pnl_usd": -30}
    ]
    breached = evaluate_risk_state(daily_breach, contract, now, overlay, evolver)
    assert "max_daily_realized_loss_breached" in breached["triggers"]

    drawdown_breach = json.loads(json.dumps(base))
    drawdown_breach["max_drawdown_pct"] = -15.1
    drawdown_result = evaluate_risk_state(drawdown_breach, contract, now, overlay, evolver)
    assert "max_portfolio_drawdown_breached" in drawdown_result["triggers"]

    exposure_breach = json.loads(json.dumps(base))
    exposure_breach["cash_usd"] = 25.0
    exposure_breach["open_positions"] = [
        {"paper_trade_id": "exposure", "symbol": "BTCUSDT", "notional_usd": 475.0}
    ]
    exposure_result = evaluate_risk_state(exposure_breach, contract, now, overlay, evolver)
    assert "max_total_open_exposure_exceeded" in exposure_result["integrity_errors"]
    assert "max_symbol_exposure_exceeded" in exposure_result["integrity_errors"]

    duplicate = json.loads(json.dumps(base))
    duplicate["paper_orders"] = [{"paper_order_id": "o1"}, {"paper_order_id": "o1"}]
    duplicate_result = evaluate_risk_state(duplicate, contract, now, overlay, evolver)
    assert duplicate_result["status"] == "blocked"
    assert "duplicate_paper_order_ids" in duplicate_result["integrity_errors"]

    unsafe = json.loads(json.dumps(base))
    unsafe["live_orders_enabled"] = True
    unsafe_result = evaluate_risk_state(unsafe, contract, now, overlay, evolver)
    assert unsafe_result["status"] == "blocked"
    return {
        "status": "ok",
        "tests": [
            "allow_path_verified",
            "two_loss_size_reduction_verified",
            "three_loss_pause_verified",
            "requested_notional_gate_verified",
            "daily_loss_kill_switch_verified",
            "portfolio_drawdown_kill_switch_verified",
            "portfolio_and_symbol_exposure_limits_verified",
            "duplicate_id_block_verified",
            "live_order_flag_block_verified",
            "paper_only_rollback_contract_verified",
        ],
    }


def render_markdown(record: dict[str, Any]) -> str:
    result = record["risk_control"]
    metrics = result["metrics"]
    rollback = result["rollback_evidence"]
    lines = [
        f"# Paper/Testnet Risk Control Audit | {record['run_id']}",
        "",
        "Read-only paper/testnet audit. No market fetch, ledger mutation, private API, or live order.",
        "",
        "## Decision",
        "",
        f"- status: `{result['status']}`",
        f"- control_contract_status: `{result['control_contract_status']}`",
        f"- risk_decision: `{result['risk_decision']}`",
        f"- size_multiplier: `{result['recommended_new_entry_size_multiplier']}`",
        f"- max_new_entry_notional_usd: `${result['max_new_entry_notional_usd']}`",
        f"- cooldown_until: `{result.get('cooldown_until') or '-'}`",
        f"- triggers: `{result.get('triggers') or []}`",
        "",
        "## Portfolio Risk Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in metrics.items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend([
        "",
        "## Idempotency And Rollback",
        "",
        f"- duplicate_paper_trade_ids: `{result['idempotency']['duplicate_paper_trade_ids']}`",
        f"- duplicate_paper_order_ids: `{result['idempotency']['duplicate_paper_order_ids']}`",
        f"- symbols_over_position_limit: `{result['idempotency']['symbols_over_position_limit']}`",
        f"- rollback_status: `{rollback['status']}`",
        f"- strategy_version: `{rollback.get('strategy_version')}`",
        f"- change_log_count: `{rollback.get('change_log_count')}`",
        f"- rollback_check_count: `{rollback.get('rollback_check_count')}`",
        "",
        "Safety boundary: this control can only allow, reduce, or block paper/testnet entries. It cannot authorize live trading.",
        "",
    ])
    return "\n".join(lines)


def build_record(config_path: Path, ledger_path: Path) -> dict[str, Any]:
    created = now_local()
    config = read_json(config_path, {}) or {}
    ledger = read_json(ledger_path, {}) or {}
    overlay = read_json(OVERLAY_PATH, {}) or {}
    evolver_path = latest("*paper-strategy-auto-evolver.json")
    evolver = read_json(evolver_path, {}) or {}
    result = evaluate_risk_state(
        ledger,
        config.get("paper_testnet_risk_controls") or {},
        created,
        overlay,
        evolver,
    )
    return {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-paper-testnet-risk-control-audit",
        "created_at": created.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "config_version": config.get("version"),
        "sources": {
            "config": rel(config_path),
            "ledger": rel(ledger_path),
            "strategy_overlay": rel(OVERLAY_PATH),
            "strategy_evolver": rel(evolver_path),
        },
        "risk_control": result,
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": False,
        "outputs": {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit paper/testnet portfolio risk controls")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record(Path(args.config), Path(args.ledger))
    stamp = record["run_id"].removesuffix("-paper-testnet-risk-control-audit")
    dated_report = REPORTS_DIR / f"{now_local().strftime('%Y-%m-%d')}-paper-testnet-risk-control-{stamp}.md"
    dated_experiment = EXPERIMENTS_DIR / f"{stamp}-paper-testnet-risk-control-audit.json"
    record["outputs"] = {
        "report": rel(dated_report),
        "experiment": rel(dated_experiment),
        "latest_report": rel(LATEST_REPORT),
        "latest_experiment": rel(LATEST_EXPERIMENT),
    }
    if not args.dry_run:
        write_json(dated_experiment, record)
        write_json(LATEST_EXPERIMENT, record)
        markdown = render_markdown(record)
        dated_report.parent.mkdir(parents=True, exist_ok=True)
        dated_report.write_text(markdown, encoding="utf-8")
        LATEST_REPORT.write_text(markdown, encoding="utf-8")
    output = {
        "run_id": record["run_id"],
        "risk_control": record["risk_control"],
        "outputs": record["outputs"],
    } if args.compact_output else record
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
