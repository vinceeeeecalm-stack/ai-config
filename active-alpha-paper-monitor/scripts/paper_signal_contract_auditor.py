#!/usr/bin/env python3
"""Audit paper/watch signal contract completeness.

The audit answers whether the active-alpha paper system is producing signals
that are explicit enough to be executed in paper mode: direction, entry trigger,
stop, take profit, time window, confidence and failure conditions.

Read-only: no market fetch, no ledger mutation, no orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
REPORTS_DIR = ACTIVE_ROOT / "reports"
EXPERIMENTS_DIR = ACTIVE_ROOT / "experiments"
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


EXECUTABLE_REQUIRED_FIELDS = {
    "symbol": ("symbol",),
    "direction": ("direction", "side"),
    "entry": ("entry_zone", "entry_trigger", "breakout_level", "entry_price"),
    "stop_loss": ("stop_loss", "stop_price"),
    "take_profit": ("take_profit", "take_profit_price"),
    "time_window": ("time_window", "expires_at", "max_holding_window"),
    "confidence": ("forecast_probability_pct", "confidence", "selection_score"),
    "failure_conditions": ("failure_conditions", "exit_rules", "risk_state"),
}

DIAGNOSTIC_REQUIRED_FIELDS = {
    "symbol": ("symbol",),
    "decision": ("decision", "recommended_next_action", "recommended_max_action"),
    "block_reason": ("block_reason", "decision_reasons", "reasons", "primary_block_reason"),
    "current_setup": ("current_setup", "last_price", "current_price"),
}

NON_EXECUTABLE_WATCH_ACTIONS = {"watch", "no_deploy", "blocked", "risk_alert"}


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path | None, default: Any = None) -> Any:
    if path is None:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def latest_file(pattern: str) -> Path | None:
    paths = list(ACTIVE_ROOT.glob(pattern))
    if not paths:
        return None
    return max(paths, key=lambda item: item.stat().st_mtime)


def has_any(payload: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple, dict, str)) and len(value) == 0:
            continue
        return True
    return False


def missing_fields(payload: dict[str, Any], required: dict[str, tuple[str, ...]]) -> list[str]:
    return [field for field, keys in required.items() if not has_any(payload, keys)]


def signal_status(missing: list[str], signal_type: str) -> str:
    if not missing:
        return "complete"
    if signal_type == "candidate_diagnostic" and len(missing) <= 1:
        return "diagnostic_partial"
    return "incomplete"


def is_non_executable_watch(item: dict[str, Any]) -> bool:
    action = str(item.get("recommended_max_action") or item.get("action") or "").strip().lower()
    data_quality = str(item.get("data_quality_status") or "").strip().lower()
    has_block = bool(item.get("block_reason") or item.get("reasons") or item.get("decision_reasons"))
    return action in NON_EXECUTABLE_WATCH_ACTIONS and (has_block or data_quality in {"missing", "stale", "disputed"})


def collect_watchlist_signal(item: dict[str, Any], source_path: Path | None) -> dict[str, Any]:
    if is_non_executable_watch(item):
        return {
            "signal_id": f"watchlist:{item.get('symbol')}:{item.get('interval')}:{item.get('paper_entry_mode')}",
            "signal_type": "non_executable_watch",
            "source": rel(source_path),
            "symbol": item.get("symbol"),
            "status": "blocked_non_executable",
            "missing_fields": [],
            "action": item.get("recommended_max_action") or item.get("action") or "watch",
            "direction": None,
            "entry": None,
            "stop_loss": None,
            "take_profit": None,
            "time_window": None,
            "confidence": item.get("forecast_probability_pct"),
            "failure_conditions": item.get("failure_conditions") or item.get("reasons") or item.get("block_reason"),
            "data_quality_status": item.get("data_quality_status"),
            "block_reason": item.get("block_reason") or item.get("reasons") or item.get("decision_reasons"),
            "contract_required": False,
            "operator_note": "Non-executable blocked watch item; it is not a paper entry contract until data and trigger fields exist.",
            "live_orders_enabled": item.get("live_orders_enabled", False),
            "private_api_used": item.get("private_api_used", False),
        }
    missing = missing_fields(item, EXECUTABLE_REQUIRED_FIELDS)
    return {
        "signal_id": f"watchlist:{item.get('symbol')}:{item.get('interval')}:{item.get('paper_entry_mode')}",
        "signal_type": "conditional_watchlist",
        "source": rel(source_path),
        "symbol": item.get("symbol"),
        "status": signal_status(missing, "conditional_watchlist"),
        "missing_fields": missing,
        "action": item.get("recommended_max_action"),
        "direction": item.get("direction"),
        "entry": item.get("entry_zone"),
        "stop_loss": item.get("stop_loss"),
        "take_profit": item.get("take_profit"),
        "time_window": item.get("time_window"),
        "confidence": item.get("forecast_probability_pct"),
        "failure_conditions": item.get("failure_conditions"),
        "data_quality_status": item.get("data_quality_status"),
        "block_reason": item.get("block_reason"),
        "contract_required": True,
        "live_orders_enabled": item.get("live_orders_enabled", False),
        "private_api_used": item.get("private_api_used", False),
    }


def collect_retest_signal(item: dict[str, Any], source_path: Path | None) -> dict[str, Any]:
    missing = missing_fields(item, DIAGNOSTIC_REQUIRED_FIELDS)
    best = item.get("best_variant") if isinstance(item.get("best_variant"), dict) else {}
    current = item.get("current_setup") if isinstance(item.get("current_setup"), dict) else {}
    return {
        "signal_id": f"retest:{item.get('symbol')}:{item.get('interval')}:{item.get('entry_mode_estimate')}",
        "signal_type": "candidate_diagnostic",
        "source": rel(source_path),
        "symbol": item.get("symbol"),
        "status": signal_status(missing, "candidate_diagnostic"),
        "missing_fields": missing,
        "action": item.get("recommended_next_action"),
        "decision": item.get("decision"),
        "direction": "research_retest_long_spot_only",
        "entry": {"breakout_level": current.get("breakout_level")},
        "stop_loss": ((best.get("params") or {}).get("stop_pct")),
        "take_profit": ((best.get("params") or {}).get("take_pct")),
        "time_window": ((best.get("params") or {}).get("hold_bars")),
        "confidence": (best.get("oos") or {}).get("win_rate_pct"),
        "failure_conditions": item.get("decision_reasons"),
        "data_quality_status": item.get("data_status"),
        "block_reason": item.get("source_block_reason") or item.get("decision_reasons"),
        "contract_required": item.get("recommended_next_action") == "paper_only_quality_scout_review",
        "live_orders_enabled": item.get("live_orders_enabled", False),
        "private_api_used": item.get("private_api_used", False),
    }


def collect_sampler_decision(item: dict[str, Any], source_path: Path | None) -> dict[str, Any]:
    missing = missing_fields(item, DIAGNOSTIC_REQUIRED_FIELDS)
    return {
        "signal_id": f"sampler:{item.get('symbol')}:{item.get('decision')}",
        "signal_type": "sampler_decision",
        "source": rel(source_path),
        "symbol": item.get("symbol"),
        "status": signal_status(missing, "candidate_diagnostic"),
        "missing_fields": missing,
        "action": item.get("decision"),
        "direction": "paper_sampler_long_only_gate",
        "entry": {"breakout_level": item.get("breakout_level"), "last_price": item.get("last_price")},
        "stop_loss": None,
        "take_profit": None,
        "time_window": None,
        "confidence": (item.get("oos") or {}).get("win_rate_pct"),
        "failure_conditions": item.get("reasons"),
        "data_quality_status": "fresh_public_binance_gate" if item.get("last_price") else "missing_price",
        "block_reason": item.get("reasons"),
        "contract_required": item.get("decision") not in {"blocked", "skipped"},
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def collect_open_position_signal(item: dict[str, Any]) -> dict[str, Any]:
    confidence = ((item.get("strategy_candidate") or {}).get("oos_summary") or {}).get("win_rate_pct")
    normalized = dict(item)
    if confidence is not None:
        normalized["confidence"] = confidence
    missing = missing_fields(normalized, EXECUTABLE_REQUIRED_FIELDS)
    return {
        "signal_id": f"open_position:{item.get('paper_trade_id')}",
        "signal_type": "open_paper_position",
        "source": item.get("signal_source") or "paper_portfolio_ledger",
        "symbol": item.get("symbol"),
        "status": signal_status(missing, "open_paper_position"),
        "missing_fields": missing,
        "action": "monitor_until_exit_rule",
        "direction": item.get("side"),
        "entry": item.get("entry_price"),
        "stop_loss": item.get("stop_price"),
        "take_profit": item.get("take_profit_price"),
        "time_window": item.get("expires_at") or item.get("max_holding_window"),
        "confidence": confidence,
        "failure_conditions": item.get("risk_state") or item.get("exit_rules"),
        "data_quality_status": item.get("mark_quality") or "ledger_position",
        "block_reason": None,
        "contract_required": True,
        "live_orders_enabled": item.get("live_orders_enabled", False),
        "private_api_used": item.get("private_api_used", False),
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    created = now_local()
    run_id = f"{created.strftime('%Y%m%d-%H%M%S')}-paper-signal-contract-audit"
    watchlist_path = latest_file("experiments/*recovery-watchlist-monitor.json")
    retest_path = latest_file("experiments/*top-blocked-candidate-retest-lab.json")
    sampler_path = latest_file("experiments/*top-blocked-retest-quality-scout-sampler.json")
    recovery_sampler_path = latest_file("experiments/*recovery-watchlist-paper-sampler.json")
    ledger = read_json(Path(args.ledger), {})
    signals: list[dict[str, Any]] = []

    watchlist_payload = read_json(watchlist_path, {})
    if isinstance(watchlist_payload, dict):
        for item in (watchlist_payload.get("watchlist") or [])[: args.max_items_per_source]:
            if isinstance(item, dict):
                signals.append(collect_watchlist_signal(item, watchlist_path))

    retest_payload = read_json(retest_path, {})
    if isinstance(retest_payload, dict):
        for item in (retest_payload.get("results") or [])[: args.max_items_per_source]:
            if isinstance(item, dict):
                signals.append(collect_retest_signal(item, retest_path))

    sampler_payload = read_json(sampler_path, {})
    if isinstance(sampler_payload, dict):
        for item in (sampler_payload.get("decisions") or [])[: args.max_items_per_source]:
            if isinstance(item, dict):
                signals.append(collect_sampler_decision(item, sampler_path))

    recovery_sampler_payload = read_json(recovery_sampler_path, {})
    if isinstance(recovery_sampler_payload, dict):
        for item in (recovery_sampler_payload.get("decisions") or [])[: args.max_items_per_source]:
            if isinstance(item, dict):
                signals.append(collect_sampler_decision(item, recovery_sampler_path))

    if isinstance(ledger, dict):
        for item in ledger.get("open_positions") or []:
            if isinstance(item, dict):
                signals.append(collect_open_position_signal(item))

    incomplete = [item for item in signals if item.get("status") == "incomplete"]
    partial = [item for item in signals if item.get("status") == "diagnostic_partial"]
    complete = [item for item in signals if item.get("status") == "complete"]
    non_executable = [item for item in signals if item.get("status") == "blocked_non_executable"]
    incomplete_required = [item for item in incomplete if item.get("contract_required") is not False]
    unsafe = [
        item for item in signals
        if item.get("live_orders_enabled") is True or item.get("private_api_used") is True
    ]
    status = "blocked" if unsafe else ("warning" if incomplete_required else "pass")
    date = created.strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"{date}-{created.strftime('%H%M%S')}-paper-signal-contract-audit.md"
    experiment_path = EXPERIMENTS_DIR / f"{run_id}.json"
    payload = {
        "run_id": run_id,
        "created_at": created.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "script": "scripts/paper_signal_contract_auditor.py",
        "scope": "paper_signal_contract_read_only",
        "status": status,
        "summary": {
            "signal_count": len(signals),
            "complete_count": len(complete),
            "partial_count": len(partial),
            "incomplete_count": len(incomplete),
            "incomplete_required_count": len(incomplete_required),
            "non_executable_count": len(non_executable),
            "unsafe_count": len(unsafe),
            "by_type": count_by(signals, "signal_type"),
            "missing_field_counts": missing_field_counts(signals),
        },
        "sources": {
            "recovery_watchlist": rel(watchlist_path),
            "top_blocked_retest": rel(retest_path),
            "top_blocked_retest_sampler": rel(sampler_path),
            "recovery_watchlist_sampler": rel(recovery_sampler_path),
            "ledger": rel(Path(args.ledger)),
        },
        "signals": signals,
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": False,
        "outputs": {"report": rel(report_path), "experiment": rel(experiment_path)},
    }
    if not args.no_write:
        write_json(experiment_path, payload)
        write_text(report_path, render_report(payload))
    return payload


def count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "missing")
        counts[value] = counts.get(value, 0) + 1
    return counts


def missing_field_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for field in row.get("missing_fields") or []:
            counts[field] = counts.get(field, 0) + 1
    return counts


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Paper Signal Contract Audit",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- created_at: `{payload.get('created_at')}`",
        f"- status: `{payload.get('status')}`",
        f"- scope: `{payload.get('scope')}`",
        f"- signal_count: `{summary.get('signal_count')}`",
        f"- complete/partial/incomplete: `{summary.get('complete_count')}/{summary.get('partial_count')}/{summary.get('incomplete_count')}`",
        f"- incomplete_required_count: `{summary.get('incomplete_required_count')}`",
        f"- non_executable_count: `{summary.get('non_executable_count')}`",
        f"- unsafe_count: `{summary.get('unsafe_count')}`",
        f"- live_orders_enabled: `{payload.get('live_orders_enabled')}`",
        f"- private_api_used: `{payload.get('private_api_used')}`",
        f"- ledger_mutated: `{payload.get('ledger_mutated')}`",
        "",
        "## Missing Field Counts",
        "",
    ]
    missing = summary.get("missing_field_counts") or {}
    if missing:
        lines.extend(f"- `{key}`: `{value}`" for key, value in sorted(missing.items()))
    else:
        lines.append("- `none`: `0`")
    lines.extend(
        [
            "",
            "## Signals",
            "",
            "| Type | Symbol | Status | Action | Missing | Entry | Stop | Take | Window | Confidence |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in payload.get("signals") or []:
        lines.append(
            f"| `{item.get('signal_type')}` | `{item.get('symbol')}` | `{item.get('status')}` | "
            f"`{item.get('action')}` | `{', '.join(item.get('missing_fields') or []) or '-'}` | "
            f"`{shorten(item.get('entry'))}` | `{shorten(item.get('stop_loss'))}` | "
            f"`{shorten(item.get('take_profit'))}` | `{shorten(item.get('time_window'))}` | "
            f"`{shorten(item.get('confidence'))}` |"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This audit is read-only and never mutates the paper ledger.",
            "- `blocked_non_executable` watch rows are not paper entry contracts; they are blocked diagnostics until data and trigger fields exist.",
            "- Incomplete contracts are not trade approvals; they are strategy instrumentation gaps.",
            "- Any true live/private flag would block the audit status.",
            "",
        ]
    )
    return "\n".join(lines)


def shorten(value: Any, limit: int = 72) -> str:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    if text == "None":
        return "-"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def compact(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") or {}
    return {
        "status": payload.get("status"),
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "signal_count": summary.get("signal_count"),
        "complete_count": summary.get("complete_count"),
        "partial_count": summary.get("partial_count"),
        "incomplete_count": summary.get("incomplete_count"),
        "incomplete_required_count": summary.get("incomplete_required_count"),
        "non_executable_count": summary.get("non_executable_count"),
        "unsafe_count": summary.get("unsafe_count"),
        "missing_field_counts": summary.get("missing_field_counts"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "allow_real_orders": payload.get("allow_real_orders"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "outputs": payload.get("outputs"),
    }


def self_test() -> dict[str, Any]:
    complete = {
        "symbol": "BTCUSDT",
        "direction": "long_spot_paper_only_after_trigger",
        "entry_zone": {"breakout_confirm_above": 100},
        "stop_loss": 95,
        "take_profit": [110, 120],
        "time_window": "72h",
        "forecast_probability_pct": 55,
        "failure_conditions": ["breakout fails"],
    }
    incomplete = {"symbol": "ETHUSDT", "direction": "long"}
    blocked_watch = {
        "symbol": "ZECUSDT",
        "recommended_max_action": "watch",
        "block_reason": "binance_public_fetch_failed:/ticker/24hr:URLError",
        "data_quality_status": "missing",
    }
    assert not missing_fields(complete, EXECUTABLE_REQUIRED_FIELDS), complete
    assert "stop_loss" in missing_fields(incomplete, EXECUTABLE_REQUIRED_FIELDS), incomplete
    blocked_signal = collect_watchlist_signal(blocked_watch, None)
    assert blocked_signal["status"] == "blocked_non_executable", blocked_signal
    assert blocked_signal["contract_required"] is False, blocked_signal
    with tempfile.TemporaryDirectory(prefix="paper_signal_contract_", dir="/private/tmp") as tmp_text:
        tmp = Path(tmp_text)
        report = tmp / "report.md"
        payload = {
            "run_id": "self-test",
            "created_at": now_local().isoformat(),
            "status": "warning",
            "scope": "paper_signal_contract_read_only",
            "summary": {
                "signal_count": 3,
                "complete_count": 1,
                "partial_count": 0,
                "incomplete_count": 1,
                "incomplete_required_count": 1,
                "non_executable_count": 1,
                "unsafe_count": 0,
                "missing_field_counts": {"stop_loss": 1},
            },
            "signals": [
                collect_watchlist_signal(complete, None),
                collect_watchlist_signal(incomplete, None),
                blocked_signal,
            ],
            "live_orders_enabled": False,
            "private_api_used": False,
            "ledger_mutated": False,
        }
        write_text(report, render_report(payload))
        report_ok = report.exists() and "Paper Signal Contract Audit" in report.read_text(encoding="utf-8")
    assert report_ok
    return {
        "status": "ok",
        "complete_contract_verified": True,
        "missing_contract_verified": True,
        "non_executable_watch_verified": True,
        "report_render_verified": True,
        "uses_temporary_files_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    parser.add_argument("--max-items-per-source", type=int, default=8)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    payload = build_payload(args)
    print(json.dumps(compact(payload) if args.compact_output else payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
