#!/usr/bin/env python3
"""Read-only resume preflight for active-alpha-paper-monitor.

It verifies local safety and evidence state before an operator resumes the
hourly paper loop. It does not fetch market data, mutate the paper ledger,
open paper positions, or enable live orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_CONFIG = ACTIVE_ROOT / "config" / "active_alpha_monitor_config.json"
DEFAULT_LEDGER = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
DEFAULT_AUTOMATION = Path("/Users/vincentpan/.codex/automations/active-alpha-hourly-crypto-paper-loop/automation.toml")

sys.path.insert(0, str(SCRIPT_DIR))
from validation_sample_auditor import monthly_goal_state_from_ledger  # noqa: E402
from kline_cache_storage import inspect_kline_cache_storage  # noqa: E402


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def stamp(now: dt.datetime) -> str:
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def inspect_latest_kline_cache(now: dt.datetime, max_age_hours: float = 6.0) -> dict[str, Any]:
    paths = list((ACTIVE_ROOT / "experiments").glob("*binance-kline-cache-builder.json"))
    if not paths:
        return {
            "status": "missing_artifact",
            "storage_status": "missing_cache_dir_reference",
            "replay_available": False,
            "refresh_required": True,
            "current_signal_usable": False,
        }
    path = max(paths, key=lambda item: item.stat().st_mtime_ns)
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        payload = {}
    storage = inspect_kline_cache_storage(payload)
    completed_at = parse_dt(payload.get("completed_at") or payload.get("generated_at"))
    age_hours = (now - completed_at).total_seconds() / 3600.0 if completed_at else None
    freshness = "missing" if age_hours is None else ("fresh" if age_hours <= max_age_hours else "stale")
    current_signal_usable = bool(storage.get("replay_available")) and freshness == "fresh"
    return {
        "status": "usable" if current_signal_usable else "refresh_required",
        "artifact": rel(path),
        "artifact_status": payload.get("status") or "missing",
        "completed_at": payload.get("completed_at") or payload.get("generated_at"),
        "age_hours": round(max(0.0, age_hours), 4) if age_hours is not None else None,
        "freshness_status": freshness,
        "current_signal_usable": current_signal_usable,
        **storage,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_dt(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def run_json_command(label: str, command: list[str], timeout_seconds: int) -> dict[str, Any]:
    started = utc_now()
    try:
        result = subprocess.run(
            command,
            cwd=str(WORKSPACE_ROOT),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "label": label,
            "status": "timeout",
            "returncode": 124,
            "command": command,
            "started_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "timeout_seconds": timeout_seconds,
            "stdout_tail": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else "",
        }
    stdout = result.stdout.strip()
    payload = None
    parse_error = None
    if stdout:
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict):
                payload = parsed
            else:
                parse_error = "stdout_json_not_object"
        except json.JSONDecodeError as exc:
            parse_error = f"stdout_json_parse_error: {exc}"
    else:
        parse_error = "stdout_empty"
    return {
        "label": label,
        "status": "ok" if result.returncode == 0 and payload is not None else "failed",
        "returncode": result.returncode,
        "command": command,
        "started_at": started.isoformat(),
        "finished_at": utc_now().isoformat(),
        "stdout_json": payload,
        "stdout_parse_error": parse_error,
        "stderr_tail": result.stderr[-2000:] if result.stderr else "",
    }


def json_command_passed(result: dict[str, Any] | None, allowed_statuses: set[str] | None = None) -> bool:
    if not result:
        return True
    if result.get("status") != "ok":
        return False
    payload = result.get("stdout_json")
    if isinstance(payload, dict) and "status" in payload:
        return str(payload.get("status")) in (allowed_statuses or {"ok", "pass"})
    return True


def parse_toml_string_fields(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in (
        "id",
        "kind",
        "name",
        "prompt",
        "status",
        "rrule",
        "model",
        "reasoning_effort",
        "execution_environment",
    ):
        match = re.search(rf'^{key}\s*=\s*"([^"]*)"', text, re.MULTILINE)
        if match:
            fields[key] = match.group(1)
    for key in ("cwds",):
        match = re.search(rf"^{key}\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
        if match:
            fields[key] = re.findall(r'"([^"]*)"', match.group(1))
    return fields


def automation_contract_check(fields: dict[str, Any], text: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    prompt = str(fields.get("prompt") or "")
    prompt_lower = prompt.lower()
    all_text_lower = text.lower()

    if fields.get("id") != "active-alpha-hourly-crypto-paper-loop":
        errors.append("automation_id_mismatch")
    if fields.get("kind") != "cron":
        errors.append("automation_kind_not_cron")
    if fields.get("rrule") != "FREQ=HOURLY;INTERVAL=1":
        errors.append("automation_rrule_not_hourly_interval_1")
    if fields.get("execution_environment") != "local":
        errors.append("automation_execution_environment_not_local")
    cwds = fields.get("cwds") if isinstance(fields.get("cwds"), list) else []
    if str(WORKSPACE_ROOT) not in cwds:
        errors.append("automation_cwd_missing_investing_workspace")
    if fields.get("status") not in {"PAUSED", "ACTIVE"}:
        warnings.append("automation_status_unexpected")

    required_prompt_markers = {
        "active-alpha-paper-monitor": "prompt_missing_skill_name",
        "paper-only": "prompt_missing_paper_only",
        "read-only": "prompt_missing_read_only",
        "never place real orders": "prompt_missing_never_place_real_orders",
        "private trading apis": "prompt_missing_private_api_ban",
        "withdraw": "prompt_missing_withdraw_ban",
        "margin/futures/perpetual": "prompt_missing_margin_futures_perpetual_ban",
        "expose api keys": "prompt_missing_api_key_exposure_ban",
    }
    for marker, error in required_prompt_markers.items():
        if marker not in prompt_lower:
            errors.append(error)

    forbidden_literals = (
        "live_orders_enabled=true",
        "allow_real_orders=true",
        "private_api_keys_used=true",
        "private_trading_api_allowed=true",
        "withdrawal_allowed=true",
        "margin_enabled=true",
        "futures_enabled=true",
        "perpetual_enabled=true",
    )
    for marker in forbidden_literals:
        if marker in all_text_lower:
            errors.append(f"automation_forbidden_literal:{marker}")

    return {
        "status": "blocked" if errors else "pass",
        "errors": errors,
        "warnings": warnings,
        "checked_fields": {
            "id": fields.get("id"),
            "kind": fields.get("kind"),
            "rrule": fields.get("rrule"),
            "execution_environment": fields.get("execution_environment"),
            "cwds": cwds,
            "prompt_length": len(prompt),
        },
    }


def read_automation_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "is_paused": None,
            "contract": {"status": "blocked", "errors": ["automation_file_missing"], "warnings": []},
        }
    text = path.read_text(encoding="utf-8")
    fields = parse_toml_string_fields(text)
    status = fields.get("status") or "unknown"
    return {
        "status": status,
        "path": str(path),
        "is_paused": status == "PAUSED",
        "id": fields.get("id"),
        "name": fields.get("name"),
        "rrule": fields.get("rrule"),
        "kind": fields.get("kind"),
        "execution_environment": fields.get("execution_environment"),
        "cwds": fields.get("cwds") or [],
        "contract": automation_contract_check(fields, text),
    }


def inspect_ledger(path: Path, now: dt.datetime, max_age_hours: float, near_expiry_hours: float) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"status": "invalid", "path": str(path), "reason": "ledger_json_not_object"}

    updated_at = parse_dt(payload.get("updated_at"))
    ledger_age_hours = None
    if updated_at is not None:
        ledger_age_hours = max(0.0, (now - updated_at).total_seconds() / 3600.0)
    open_positions = payload.get("open_positions") if isinstance(payload.get("open_positions"), list) else []
    expired: list[dict[str, Any]] = []
    near_expiry: list[dict[str, Any]] = []
    for position in open_positions:
        if not isinstance(position, dict):
            continue
        expires_at = parse_dt(position.get("expires_at"))
        if expires_at is None:
            continue
        hours = (expires_at - now).total_seconds() / 3600.0
        risk = position.get("risk_state") or {}
        item = {
            "paper_trade_id": position.get("paper_trade_id"),
            "symbol": position.get("symbol"),
            "expires_at": expires_at.isoformat(),
            "hours_to_expiry": round(hours, 4),
            "unrealized_pnl_pct": position.get("unrealized_pnl_pct"),
            "profit_protection_armed": bool(risk.get("profit_protection_armed")),
            "trailing_floor_pct": risk.get("trailing_floor_pct"),
        }
        if hours <= 0:
            expired.append(item)
        elif hours <= near_expiry_hours:
            near_expiry.append(item)

    monthly = monthly_goal_state_from_ledger(payload, now=now)
    month_id = monthly.get("month_id")
    baselines = payload.get("monthly_goal_baselines")
    explicit_baseline_present = False
    if isinstance(baselines, dict):
        explicit_baseline_present = month_id in baselines
    elif isinstance(baselines, list):
        explicit_baseline_present = any(isinstance(item, dict) and str(item.get("month_id")) == month_id for item in baselines)

    stale_with_open = bool(open_positions) and (ledger_age_hours is None or ledger_age_hours >= max_age_hours)
    exit_review_required = bool(expired or near_expiry or stale_with_open)
    return {
        "status": "ok",
        "path": str(path),
        "updated_at": payload.get("updated_at"),
        "created_at": payload.get("created_at"),
        "ledger_age_hours": round(ledger_age_hours, 4) if ledger_age_hours is not None else None,
        "max_ledger_age_hours": max_age_hours,
        "live_orders_enabled": bool(payload.get("live_orders_enabled")),
        "cash_usd": payload.get("cash_usd"),
        "open_value_usd": payload.get("open_value_usd"),
        "equity_usd": payload.get("equity_usd"),
        "net_return_pct": payload.get("net_return_pct"),
        "max_drawdown_pct": payload.get("max_drawdown_pct"),
        "open_positions_count": len(open_positions),
        "closed_trades_count": len(payload.get("closed_trades") or []),
        "paper_orders_count": len(payload.get("paper_orders") or []),
        "expired_positions": expired,
        "near_expiry_positions": near_expiry,
        "stale_with_open_positions": stale_with_open,
        "exit_review_required": exit_review_required,
        "monthly_goal_state": monthly,
        "explicit_monthly_baseline_present": explicit_baseline_present,
        "baseline_will_be_persisted_on_next_ledger_save": not explicit_baseline_present,
    }


def safety_status(
    ledger: dict[str, Any],
    self_test: dict[str, Any] | None,
    audit: dict[str, Any] | None,
    automation: dict[str, Any] | None = None,
    safety_audit: dict[str, Any] | None = None,
    recovery_watchlist_monitor_self_test: dict[str, Any] | None = None,
    recovery_sampler_self_test: dict[str, Any] | None = None,
    blocked_retest_sampler_self_test: dict[str, Any] | None = None,
    capital_allocation_self_test: dict[str, Any] | None = None,
    ledger_integrity_self_test: dict[str, Any] | None = None,
    market_context_self_test: dict[str, Any] | None = None,
    binance_market_data_health_self_test: dict[str, Any] | None = None,
    binance_kline_cache_builder_self_test: dict[str, Any] | None = None,
    weekly_goal_strategy_lab_self_test: dict[str, Any] | None = None,
    kline_research_reproducibility_self_test: dict[str, Any] | None = None,
    current_signal_probe_self_test: dict[str, Any] | None = None,
    strategy_recovery_optimizer_self_test: dict[str, Any] | None = None,
    pipeline_freshness_self_test: dict[str, Any] | None = None,
    automation_recovery_planner_self_test: dict[str, Any] | None = None,
    goal_completion_auditor_self_test: dict[str, Any] | None = None,
    paper_testnet_risk_control_self_test: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if ledger.get("status") != "ok":
        errors.append(f"ledger_{ledger.get('status')}")
    if ledger.get("live_orders_enabled"):
        errors.append("ledger_live_orders_enabled_true")
    contract = (automation or {}).get("contract") or {}
    if contract.get("status") == "blocked":
        errors.extend([f"automation_contract:{item}" for item in contract.get("errors") or []])
    warnings.extend([f"automation_contract:{item}" for item in contract.get("warnings") or []])
    if safety_audit and safety_audit.get("status") != "ok":
        errors.append("safety_invariant_auditor_failed")
    safety_payload = (safety_audit or {}).get("stdout_json") or {}
    if isinstance(safety_payload, dict) and safety_payload.get("status") == "blocked":
        errors.append("safety_invariant_auditor_blocked")
    if not json_command_passed(self_test):
        errors.append("self_test_failed")
    if not json_command_passed(recovery_watchlist_monitor_self_test):
        errors.append("recovery_watchlist_monitor_self_test_failed")
    if not json_command_passed(recovery_sampler_self_test):
        errors.append("recovery_sampler_self_test_failed")
    if not json_command_passed(blocked_retest_sampler_self_test):
        errors.append("blocked_retest_sampler_self_test_failed")
    if not json_command_passed(capital_allocation_self_test):
        errors.append("capital_allocation_self_test_failed")
    if not json_command_passed(ledger_integrity_self_test):
        errors.append("ledger_integrity_self_test_failed")
    if not json_command_passed(market_context_self_test):
        errors.append("market_context_self_test_failed")
    if not json_command_passed(binance_market_data_health_self_test):
        errors.append("binance_market_data_health_self_test_failed")
    if not json_command_passed(binance_kline_cache_builder_self_test):
        errors.append("binance_kline_cache_builder_self_test_failed")
    if not json_command_passed(weekly_goal_strategy_lab_self_test):
        errors.append("weekly_goal_strategy_lab_self_test_failed")
    if not json_command_passed(kline_research_reproducibility_self_test):
        errors.append("kline_research_reproducibility_self_test_failed")
    if not json_command_passed(current_signal_probe_self_test):
        errors.append("current_signal_probe_self_test_failed")
    if not json_command_passed(strategy_recovery_optimizer_self_test):
        errors.append("strategy_recovery_optimizer_self_test_failed")
    if not json_command_passed(pipeline_freshness_self_test):
        errors.append("pipeline_freshness_self_test_failed")
    if not json_command_passed(automation_recovery_planner_self_test):
        errors.append("automation_recovery_planner_self_test_failed")
    if not json_command_passed(goal_completion_auditor_self_test):
        errors.append("goal_completion_auditor_self_test_failed")
    if not json_command_passed(paper_testnet_risk_control_self_test):
        errors.append("paper_testnet_risk_control_self_test_failed")
    if audit and audit.get("status") != "ok":
        warnings.append("validation_audit_failed_or_unparsed")
    if ledger.get("baseline_will_be_persisted_on_next_ledger_save"):
        warnings.append("monthly_goal_baseline_missing_but_will_persist_on_next_save")
    return {"status": "blocked" if errors else "pass", "errors": errors, "warnings": warnings}


def readiness_decision(
    ledger: dict[str, Any],
    automation: dict[str, Any],
    safety: dict[str, Any],
    kline_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resume_requirements = []
    if (kline_cache or {}).get("refresh_required"):
        resume_requirements.append("rebuild_durable_kline_cache_before_current_signal_or_new_paper_entry")
    if safety.get("status") != "pass":
        return {
            "status": "not_ready",
            "can_resume_hourly_new_sampling": False,
            "next_action": "fix_safety_errors_before_any_resume",
            "requires_user_confirmation": True,
            "resume_requirements": resume_requirements,
        }
    if ledger.get("exit_review_required"):
        return {
            "status": "exit_review_first",
            "can_resume_hourly_new_sampling": False,
            "next_action": "run_paper_exit_review_before_new_scans",
            "requires_user_confirmation": True,
            "resume_requirements": resume_requirements,
            "reason": {
                "stale_with_open_positions": ledger.get("stale_with_open_positions"),
                "expired_positions": len(ledger.get("expired_positions") or []),
                "near_expiry_positions": len(ledger.get("near_expiry_positions") or []),
            },
        }
    if automation.get("is_paused"):
        return {
            "status": "ready_but_paused",
            "can_resume_hourly_new_sampling": True,
            "next_action": "ask_user_before_setting_automation_active_or_running_hourly_loop",
            "requires_user_confirmation": True,
            "resume_requirements": resume_requirements,
        }
    return {
        "status": "ready_check_automation_state",
        "can_resume_hourly_new_sampling": True,
        "next_action": "automation_not_paused_or_status_unknown_review_before_changes",
        "requires_user_confirmation": True,
        "resume_requirements": resume_requirements,
    }


def render_report(run: dict[str, Any]) -> str:
    ledger = run.get("ledger") or {}
    monthly = ledger.get("monthly_goal_state") or {}
    readiness = run.get("readiness") or {}
    safety = run.get("safety") or {}
    kline_cache = run.get("kline_cache") or {}
    lines = [
        f"# Active Alpha Resume Preflight | {run['run_id']}",
        "",
        "Read-only preflight. No market scan, no paper trade, no ledger mutation, no live order.",
        "",
        "## Decision",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Safety | `{safety.get('status')}` |",
        f"| Readiness | `{readiness.get('status')}` |",
        f"| Can resume hourly new sampling | `{readiness.get('can_resume_hourly_new_sampling')}` |",
        f"| Next action | `{readiness.get('next_action')}` |",
        f"| User confirmation required | `{readiness.get('requires_user_confirmation')}` |",
        f"| Resume requirements | `{readiness.get('resume_requirements') or []}` |",
        "",
        "## Ledger",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Status | `{ledger.get('status')}` |",
        f"| Updated at | `{ledger.get('updated_at')}` |",
        f"| Ledger age hours | `{ledger.get('ledger_age_hours')}` |",
        f"| Equity | `{ledger.get('equity_usd')}` |",
        f"| Cash | `{ledger.get('cash_usd')}` |",
        f"| Open positions | `{ledger.get('open_positions_count')}` |",
        f"| Exit review required | `{ledger.get('exit_review_required')}` |",
        f"| Expired positions | `{len(ledger.get('expired_positions') or [])}` |",
        f"| Near-expiry positions | `{len(ledger.get('near_expiry_positions') or [])}` |",
        f"| Live orders enabled | `{ledger.get('live_orders_enabled')}` |",
        "",
        "## Monthly Target",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Target model | `{monthly.get('target_model')}` |",
        f"| Month | `{monthly.get('month_id')}` |",
        f"| Baseline source | `{monthly.get('baseline_source')}` |",
        f"| Explicit ledger baseline present | `{ledger.get('explicit_monthly_baseline_present')}` |",
        f"| Will persist on next ledger save | `{ledger.get('baseline_will_be_persisted_on_next_ledger_save')}` |",
        f"| Month-start equity | `{monthly.get('month_start_equity_usd')}` |",
        f"| Target equity | `{monthly.get('target_equity_usd')}` |",
        f"| Current equity | `{monthly.get('current_equity_usd')}` |",
        f"| Target gap | `{monthly.get('target_gap_usd')}` |",
        f"| Progress | `{monthly.get('progress_pct')}` |",
        f"| Required return from current equity | `{monthly.get('required_return_pct_from_current_equity')}` |",
        "",
        "## Automation",
        "",
        f"- Status: `{(run.get('automation') or {}).get('status')}`",
        f"- Contract: `{((run.get('automation') or {}).get('contract') or {}).get('status')}`",
        f"- Path: `{(run.get('automation') or {}).get('path')}`",
        "",
        "## Binance Kline Cache Storage",
        "",
        f"- Status: `{kline_cache.get('status')}`",
        f"- Artifact: `{kline_cache.get('artifact') or '-'}`",
        f"- Artifact freshness: `{kline_cache.get('freshness_status')}` age_hours `{kline_cache.get('age_hours')}`",
        f"- Storage: `{kline_cache.get('storage_status')}` directory_exists `{kline_cache.get('cache_dir_exists')}`",
        f"- Declared/actual files: `{kline_cache.get('declared_file_count')}/{kline_cache.get('actual_file_count')}`",
        f"- Historical replay available: `{kline_cache.get('replay_available')}`",
        f"- Current signal usable: `{kline_cache.get('current_signal_usable')}`",
        f"- Refresh required: `{kline_cache.get('refresh_required')}`",
        "",
        "## Checks",
        "",
        f"- Self-test: `{(run.get('self_test') or {}).get('status')}`",
        f"- Recovery watchlist monitor self-test: `{(run.get('recovery_watchlist_monitor_self_test') or {}).get('status')}`",
        f"- Recovery sampler self-test: `{(run.get('recovery_sampler_self_test') or {}).get('status')}`",
        f"- Top blocked retest sampler self-test: `{(run.get('blocked_retest_sampler_self_test') or {}).get('status')}`",
        f"- Capital allocation self-test: `{(run.get('capital_allocation_self_test') or {}).get('status')}`",
        f"- Ledger integrity self-test: `{(run.get('ledger_integrity_self_test') or {}).get('status')}`",
        f"- Market context self-test: `{(run.get('market_context_self_test') or {}).get('status')}`",
        f"- Binance market data health self-test: `{(run.get('binance_market_data_health_self_test') or {}).get('status')}`",
        f"- Binance Kline cache builder self-test: `{(run.get('binance_kline_cache_builder_self_test') or {}).get('status')}`",
        f"- Weekly strategy cache loader self-test: `{(run.get('weekly_goal_strategy_lab_self_test') or {}).get('status')}`",
        f"- Kline research reproducibility self-test: `{(run.get('kline_research_reproducibility_self_test') or {}).get('status')}`",
        f"- Current-signal three-segment validation self-test: `{(run.get('current_signal_probe_self_test') or {}).get('status')}`",
        f"- Strategy recovery reproducibility gate self-test: `{(run.get('strategy_recovery_optimizer_self_test') or {}).get('status')}`",
        f"- Pipeline current-market freshness self-test: `{(run.get('pipeline_freshness_self_test') or {}).get('status')}`",
        f"- Automation recovery planner self-test: `{(run.get('automation_recovery_planner_self_test') or {}).get('status')}`",
        f"- Goal completion auditor self-test: `{(run.get('goal_completion_auditor_self_test') or {}).get('status')}`",
        f"- Paper/testnet risk control self-test: `{(run.get('paper_testnet_risk_control_self_test') or {}).get('status')}`",
        f"- Automation recovery plan: `{(((run.get('automation_recovery_plan') or {}).get('stdout_json') or {}).get('status'))}`",
        f"- Automation recovery approval required: `{(((run.get('automation_recovery_plan') or {}).get('stdout_json') or {}).get('requires_explicit_user_approval'))}`",
        f"- Safety invariant audit: `{(run.get('safety_invariant_audit') or {}).get('status')}`",
        f"- Validation audit: `{(run.get('validation_audit') or {}).get('status')}`",
        f"- Safety errors: `{safety.get('errors')}`",
        f"- Safety warnings: `{safety.get('warnings')}`",
        "",
    ]
    return "\n".join(lines)


def build_run(args: argparse.Namespace) -> dict[str, Any]:
    now = utc_now()
    local = now.astimezone(LOCAL_TZ)
    run_id = f"{stamp(local)}-resume-preflight"
    config = read_json(Path(args.config), {})
    automation = read_automation_status(Path(args.automation))
    ledger = inspect_ledger(
        Path(args.ledger),
        now,
        max_age_hours=float(args.max_ledger_age_hours),
        near_expiry_hours=float(args.near_expiry_hours),
    )
    kline_cache = inspect_latest_kline_cache(now)
    self_test = None
    if not args.skip_self_test:
        self_test = run_json_command(
            "sunday_crypto_realistic_paper_loop_self_test",
            [sys.executable, str(SCRIPT_DIR / "sunday_crypto_realistic_paper_loop.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    recovery_watchlist_monitor_self_test = None
    if not args.skip_self_test:
        recovery_watchlist_monitor_self_test = run_json_command(
            "recovery_watchlist_monitor_self_test",
            [sys.executable, str(SCRIPT_DIR / "recovery_watchlist_monitor.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    recovery_sampler_self_test = None
    if not args.skip_self_test:
        recovery_sampler_self_test = run_json_command(
            "recovery_watchlist_paper_sampler_self_test",
            [sys.executable, str(SCRIPT_DIR / "recovery_watchlist_paper_sampler.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    blocked_retest_sampler_self_test = None
    if not args.skip_self_test:
        blocked_retest_sampler_self_test = run_json_command(
            "top_blocked_retest_quality_scout_sampler_self_test",
            [sys.executable, str(SCRIPT_DIR / "top_blocked_retest_quality_scout_sampler.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    capital_allocation_self_test = None
    if not args.skip_self_test:
        capital_allocation_self_test = run_json_command(
            "paper_capital_allocation_auditor_self_test",
            [sys.executable, str(SCRIPT_DIR / "paper_capital_allocation_auditor.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    ledger_integrity_self_test = None
    if not args.skip_self_test:
        ledger_integrity_self_test = run_json_command(
            "paper_ledger_integrity_auditor_self_test",
            [sys.executable, str(SCRIPT_DIR / "paper_ledger_integrity_auditor.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    market_context_self_test = None
    if not args.skip_self_test:
        market_context_self_test = run_json_command(
            "paper_market_context_auditor_self_test",
            [sys.executable, str(SCRIPT_DIR / "paper_market_context_auditor.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    binance_market_data_health_self_test = None
    if not args.skip_self_test:
        binance_market_data_health_self_test = run_json_command(
            "binance_market_data_health_auditor_self_test",
            [sys.executable, str(SCRIPT_DIR / "binance_market_data_health_auditor.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    binance_kline_cache_builder_self_test = None
    if not args.skip_self_test:
        binance_kline_cache_builder_self_test = run_json_command(
            "binance_kline_cache_builder_self_test",
            [sys.executable, str(SCRIPT_DIR / "binance_kline_cache_builder.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    weekly_goal_strategy_lab_self_test = None
    if not args.skip_self_test:
        weekly_goal_strategy_lab_self_test = run_json_command(
            "weekly_goal_strategy_lab_self_test",
            [sys.executable, str(SCRIPT_DIR / "weekly_goal_strategy_lab.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    kline_research_reproducibility_self_test = None
    if not args.skip_self_test:
        kline_research_reproducibility_self_test = run_json_command(
            "kline_research_reproducibility_self_test",
            [sys.executable, str(SCRIPT_DIR / "kline_research_reproducibility_auditor.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    current_signal_probe_self_test = None
    if not args.skip_self_test:
        current_signal_probe_self_test = run_json_command(
            "current_signal_probe_self_test",
            [sys.executable, str(SCRIPT_DIR / "current_signal_probe.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    strategy_recovery_optimizer_self_test = None
    if not args.skip_self_test:
        strategy_recovery_optimizer_self_test = run_json_command(
            "strategy_recovery_optimizer_self_test",
            [sys.executable, str(SCRIPT_DIR / "strategy_recovery_optimizer.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    pipeline_freshness_self_test = None
    if not args.skip_self_test:
        pipeline_freshness_self_test = run_json_command(
            "pipeline_freshness_auditor_self_test",
            [sys.executable, str(SCRIPT_DIR / "pipeline_freshness_auditor.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    automation_recovery_planner_self_test = None
    if not args.skip_self_test:
        automation_recovery_planner_self_test = run_json_command(
            "automation_recovery_planner_self_test",
            [sys.executable, str(SCRIPT_DIR / "automation_recovery_planner.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    goal_completion_auditor_self_test = None
    if not args.skip_self_test:
        goal_completion_auditor_self_test = run_json_command(
            "goal_completion_auditor_self_test",
            [sys.executable, str(SCRIPT_DIR / "phase_goal_readiness_auditor.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    paper_testnet_risk_control_self_test = None
    if not args.skip_self_test:
        paper_testnet_risk_control_self_test = run_json_command(
            "paper_testnet_risk_control_self_test",
            [sys.executable, str(SCRIPT_DIR / "paper_testnet_risk_control_auditor.py"), "--self-test"],
            timeout_seconds=args.self_test_timeout_seconds,
        )
    automation_recovery_plan = run_json_command(
        "automation_recovery_plan",
        [sys.executable, str(SCRIPT_DIR / "automation_recovery_planner.py"), "--compact-output"],
        timeout_seconds=args.self_test_timeout_seconds,
    )
    safety_audit = None
    if not args.skip_safety_invariant_audit:
        safety_audit = run_json_command(
            "safety_invariant_auditor",
            [
                sys.executable,
                str(SCRIPT_DIR / "safety_invariant_auditor.py"),
                "--format",
                "json",
                "--compact-output",
            ],
            timeout_seconds=args.safety_invariant_timeout_seconds,
        )
    audit = None
    if not args.skip_validation_audit:
        audit = run_json_command(
            "validation_sample_auditor",
            [sys.executable, str(SCRIPT_DIR / "validation_sample_auditor.py"), "--format", "json"],
            timeout_seconds=args.validation_timeout_seconds,
        )
    safety = safety_status(
        ledger,
        self_test,
        audit,
        automation,
        safety_audit,
        recovery_watchlist_monitor_self_test,
        recovery_sampler_self_test,
        blocked_retest_sampler_self_test,
        capital_allocation_self_test,
        ledger_integrity_self_test,
        market_context_self_test,
        binance_market_data_health_self_test,
        binance_kline_cache_builder_self_test,
        weekly_goal_strategy_lab_self_test,
        kline_research_reproducibility_self_test,
        current_signal_probe_self_test,
        strategy_recovery_optimizer_self_test,
        pipeline_freshness_self_test,
        automation_recovery_planner_self_test,
        goal_completion_auditor_self_test,
        paper_testnet_risk_control_self_test,
    )
    if kline_cache.get("refresh_required"):
        safety["warnings"].append("kline_cache_refresh_required_before_current_signal_or_new_paper_entry")
    readiness = readiness_decision(ledger, automation, safety, kline_cache)
    run = {
        "run_id": run_id,
        "created_at": now.isoformat(),
        "local_time": local.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "script": "scripts/resume_preflight.py",
        "config_version": config.get("version"),
        "live_orders_enabled": False,
        "market_scan_performed": False,
        "paper_trade_opened": False,
        "paper_trade_closed": False,
        "ledger_mutated": False,
        "automation": automation,
        "ledger": ledger,
        "kline_cache": kline_cache,
        "self_test": self_test,
        "recovery_watchlist_monitor_self_test": recovery_watchlist_monitor_self_test,
        "recovery_sampler_self_test": recovery_sampler_self_test,
        "blocked_retest_sampler_self_test": blocked_retest_sampler_self_test,
        "capital_allocation_self_test": capital_allocation_self_test,
        "ledger_integrity_self_test": ledger_integrity_self_test,
        "market_context_self_test": market_context_self_test,
        "binance_market_data_health_self_test": binance_market_data_health_self_test,
        "binance_kline_cache_builder_self_test": binance_kline_cache_builder_self_test,
        "weekly_goal_strategy_lab_self_test": weekly_goal_strategy_lab_self_test,
        "kline_research_reproducibility_self_test": kline_research_reproducibility_self_test,
        "current_signal_probe_self_test": current_signal_probe_self_test,
        "strategy_recovery_optimizer_self_test": strategy_recovery_optimizer_self_test,
        "pipeline_freshness_self_test": pipeline_freshness_self_test,
        "automation_recovery_planner_self_test": automation_recovery_planner_self_test,
        "goal_completion_auditor_self_test": goal_completion_auditor_self_test,
        "paper_testnet_risk_control_self_test": paper_testnet_risk_control_self_test,
        "automation_recovery_plan": automation_recovery_plan,
        "safety_invariant_audit": safety_audit,
        "validation_audit": audit,
        "safety": safety,
        "readiness": readiness,
        "outputs": {},
    }
    if not args.no_write:
        date = local.strftime("%Y-%m-%d")
        run_stamp = local.strftime("%Y%m%d-%H%M%S")
        report_path = ACTIVE_ROOT / "reports" / f"{date}-resume-preflight-{local.strftime('%H%M')}.md"
        experiment_path = ACTIVE_ROOT / "experiments" / f"{run_stamp}-resume-preflight.json"
        run["outputs"] = {"report": rel(report_path), "experiment": rel(experiment_path)}
        write_json(experiment_path, run)
        write_text(report_path, render_report(run))
    return run


def compact(run: dict[str, Any]) -> dict[str, Any]:
    ledger = run.get("ledger") or {}
    monthly = ledger.get("monthly_goal_state") or {}
    return {
        "run_id": run.get("run_id"),
        "config_version": run.get("config_version"),
        "safety": run.get("safety"),
        "readiness": run.get("readiness"),
        "automation_status": (run.get("automation") or {}).get("status"),
        "automation_contract": ((run.get("automation") or {}).get("contract") or {}).get("status"),
        "kline_cache": run.get("kline_cache"),
        "ledger": {
            "updated_at": ledger.get("updated_at"),
            "ledger_age_hours": ledger.get("ledger_age_hours"),
            "equity_usd": ledger.get("equity_usd"),
            "open_positions_count": ledger.get("open_positions_count"),
            "exit_review_required": ledger.get("exit_review_required"),
            "explicit_monthly_baseline_present": ledger.get("explicit_monthly_baseline_present"),
        },
        "monthly_target": {
            "month_id": monthly.get("month_id"),
            "baseline_source": monthly.get("baseline_source"),
            "month_start_equity_usd": monthly.get("month_start_equity_usd"),
            "target_equity_usd": monthly.get("target_equity_usd"),
            "current_equity_usd": monthly.get("current_equity_usd"),
            "target_gap_usd": monthly.get("target_gap_usd"),
            "progress_pct": monthly.get("progress_pct"),
            "required_return_pct_from_current_equity": monthly.get("required_return_pct_from_current_equity"),
        },
        "self_test_status": (run.get("self_test") or {}).get("status"),
        "recovery_watchlist_monitor_self_test_status": (run.get("recovery_watchlist_monitor_self_test") or {}).get("status"),
        "recovery_sampler_self_test_status": (run.get("recovery_sampler_self_test") or {}).get("status"),
        "blocked_retest_sampler_self_test_status": (run.get("blocked_retest_sampler_self_test") or {}).get("status"),
        "capital_allocation_self_test_status": (run.get("capital_allocation_self_test") or {}).get("status"),
        "ledger_integrity_self_test_status": (run.get("ledger_integrity_self_test") or {}).get("status"),
        "market_context_self_test_status": (run.get("market_context_self_test") or {}).get("status"),
        "binance_market_data_health_self_test_status": (run.get("binance_market_data_health_self_test") or {}).get("status"),
        "binance_kline_cache_builder_self_test_status": (run.get("binance_kline_cache_builder_self_test") or {}).get("status"),
        "weekly_goal_strategy_lab_self_test_status": (run.get("weekly_goal_strategy_lab_self_test") or {}).get("status"),
        "kline_research_reproducibility_self_test_status": (run.get("kline_research_reproducibility_self_test") or {}).get("status"),
        "current_signal_probe_self_test_status": (run.get("current_signal_probe_self_test") or {}).get("status"),
        "strategy_recovery_optimizer_self_test_status": (run.get("strategy_recovery_optimizer_self_test") or {}).get("status"),
        "pipeline_freshness_self_test_status": (run.get("pipeline_freshness_self_test") or {}).get("status"),
        "automation_recovery_planner_self_test_status": (run.get("automation_recovery_planner_self_test") or {}).get("status"),
        "goal_completion_auditor_self_test_status": (run.get("goal_completion_auditor_self_test") or {}).get("status"),
        "paper_testnet_risk_control_self_test_status": (run.get("paper_testnet_risk_control_self_test") or {}).get("status"),
        "automation_recovery_plan_status": (((run.get("automation_recovery_plan") or {}).get("stdout_json") or {}).get("status")),
        "automation_recovery_requires_approval": (((run.get("automation_recovery_plan") or {}).get("stdout_json") or {}).get("requires_explicit_user_approval")),
        "safety_invariant_audit_status": (run.get("safety_invariant_audit") or {}).get("status"),
        "safety_invariant_status": ((run.get("safety_invariant_audit") or {}).get("stdout_json") or {}).get("status"),
        "validation_audit_status": (run.get("validation_audit") or {}).get("status"),
        "outputs": run.get("outputs"),
    }


def self_test() -> dict[str, Any]:
    fixed_now = dt.datetime(2026, 6, 6, 2, 0, 0, tzinfo=dt.timezone.utc)
    with tempfile.TemporaryDirectory(prefix="active_alpha_resume_preflight_", dir="/private/tmp") as tmp_text:
        tmp = Path(tmp_text)
        automation_path = tmp / "automation.toml"
        automation_path.write_text(
            '\n'.join(
                [
                    "version = 1",
                    'id = "active-alpha-hourly-crypto-paper-loop"',
                    'kind = "cron"',
                    'name = "Active Alpha Hourly Crypto Paper Loop"',
                    'prompt = "Run the active-alpha-paper-monitor hourly crypto paper validation loop in paper-only mode. Use read-only public or authorized market data, review open paper positions, apply validation capacity and recovery gates, generate readable reports and handoffs, and never place real orders, use private trading APIs, withdraw, trade margin/futures/perpetuals, or expose API keys."',
                    'status = "PAUSED"',
                    'rrule = "FREQ=HOURLY;INTERVAL=1"',
                    'model = "gpt-5"',
                    'reasoning_effort = "medium"',
                    'execution_environment = "local"',
                    f'cwds = ["{WORKSPACE_ROOT}"]',
                    '',
                ]
            ),
            encoding="utf-8",
        )
        automation = read_automation_status(automation_path)
        assert automation["status"] == "PAUSED" and automation["is_paused"], automation
        assert automation["contract"]["status"] == "pass", automation

        ready_ledger_path = tmp / "ready-ledger.json"
        ready_ledger_path.write_text(
            json.dumps(
                {
                    "created_at": "2026-06-01T00:00:00+00:00",
                    "updated_at": "2026-06-06T01:30:00+00:00",
                    "live_orders_enabled": False,
                    "initial_capital_usd": 500.0,
                    "cash_usd": 520.0,
                    "equity_usd": 520.0,
                    "open_positions": [],
                    "closed_trades": [],
                    "paper_orders": [],
                    "events": [],
                    "monthly_goal_baselines": {
                        "2026-06": {
                            "month_id": "2026-06",
                            "month_start_equity_usd": 500.0,
                            "target_equity_usd": 1000.0,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        ready_ledger = inspect_ledger(ready_ledger_path, fixed_now, max_age_hours=2.0, near_expiry_hours=24.0)
        ready_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
        )
        ready = readiness_decision(ready_ledger, automation, ready_safety)
        assert ready_safety["status"] == "pass", ready_safety
        assert ready["status"] == "ready_but_paused" and ready["can_resume_hourly_new_sampling"], ready
        cache_refresh_ready = readiness_decision(
            ready_ledger,
            automation,
            ready_safety,
            {"refresh_required": True},
        )
        assert cache_refresh_ready["can_resume_hourly_new_sampling"], cache_refresh_ready
        assert (
            "rebuild_durable_kline_cache_before_current_signal_or_new_paper_entry"
            in cache_refresh_ready["resume_requirements"]
        ), cache_refresh_ready

        stale_ledger_path = tmp / "stale-ledger.json"
        stale_ledger_path.write_text(
            json.dumps(
                {
                    "created_at": "2026-06-01T00:00:00+00:00",
                    "updated_at": "2026-06-05T20:00:00+00:00",
                    "live_orders_enabled": False,
                    "initial_capital_usd": 500.0,
                    "cash_usd": 475.0,
                    "equity_usd": 502.0,
                    "open_positions": [
                        {
                            "paper_trade_id": "paper-stale",
                            "symbol": "TESTUSDT",
                            "expires_at": "2026-06-07T00:00:00+00:00",
                            "unrealized_pnl_pct": 4.2,
                            "risk_state": {"profit_protection_armed": True, "trailing_floor_pct": 2.0},
                        }
                    ],
                    "closed_trades": [],
                    "paper_orders": [],
                    "events": [
                        {
                            "event_type": "paper_equity_snapshot",
                            "created_at": "2026-06-01T00:00:00+00:00",
                            "equity_usd": 500.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        stale_ledger = inspect_ledger(stale_ledger_path, fixed_now, max_age_hours=2.0, near_expiry_hours=24.0)
        stale_readiness = readiness_decision(
            stale_ledger,
            automation,
            safety_status(
                stale_ledger,
                {"status": "ok"},
                {"status": "ok"},
                automation,
                {"status": "ok", "stdout_json": {"status": "pass"}},
            ),
        )
        assert stale_ledger["exit_review_required"] and stale_ledger["stale_with_open_positions"], stale_ledger
        assert stale_readiness["status"] == "exit_review_first", stale_readiness

        live_ledger = dict(ready_ledger)
        live_ledger["live_orders_enabled"] = True
        live_safety = safety_status(
            live_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
        )
        live_readiness = readiness_decision(live_ledger, automation, live_safety)
        assert live_safety["status"] == "blocked" and "ledger_live_orders_enabled_true" in live_safety["errors"], live_safety
        assert live_readiness["status"] == "not_ready", live_readiness

        unsafe_automation_path = tmp / "unsafe-automation.toml"
        unsafe_automation_path.write_text(
            '\n'.join(
                [
                    "version = 1",
                    'id = "active-alpha-hourly-crypto-paper-loop"',
                    'kind = "cron"',
                    'name = "Active Alpha Hourly Crypto Paper Loop"',
                    'prompt = "Run active-alpha-paper-monitor live_orders_enabled=true"',
                    'status = "PAUSED"',
                    'rrule = "FREQ=DAILY;INTERVAL=1"',
                    'execution_environment = "local"',
                    f'cwds = ["{WORKSPACE_ROOT}"]',
                    '',
                ]
            ),
            encoding="utf-8",
        )
        unsafe_automation = read_automation_status(unsafe_automation_path)
        unsafe_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            unsafe_automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
        )
        assert unsafe_automation["contract"]["status"] == "blocked", unsafe_automation
        assert unsafe_safety["status"] == "blocked" and any(
            item.startswith("automation_contract:") for item in unsafe_safety["errors"]
        ), unsafe_safety

        invariant_block_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "blocked", "blocked_count": 1}},
        )
        assert invariant_block_safety["status"] == "blocked" and "safety_invariant_auditor_blocked" in invariant_block_safety["errors"], invariant_block_safety

        recovery_monitor_block_safety = safety_status(
            ready_ledger,
            {"status": "ok", "stdout_json": {"status": "ok"}},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
            recovery_watchlist_monitor_self_test={"status": "ok", "stdout_json": {"status": "failed"}},
        )
        assert (
            recovery_monitor_block_safety["status"] == "blocked"
            and "recovery_watchlist_monitor_self_test_failed" in recovery_monitor_block_safety["errors"]
        ), recovery_monitor_block_safety

        capital_allocation_block_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
            capital_allocation_self_test={"status": "failed"},
        )
        assert (
            capital_allocation_block_safety["status"] == "blocked"
            and "capital_allocation_self_test_failed" in capital_allocation_block_safety["errors"]
        ), capital_allocation_block_safety

        ledger_integrity_block_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
            ledger_integrity_self_test={"status": "failed"},
        )
        assert (
            ledger_integrity_block_safety["status"] == "blocked"
            and "ledger_integrity_self_test_failed" in ledger_integrity_block_safety["errors"]
        ), ledger_integrity_block_safety

        market_context_block_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
            market_context_self_test={"status": "failed"},
        )
        assert (
            market_context_block_safety["status"] == "blocked"
            and "market_context_self_test_failed" in market_context_block_safety["errors"]
        ), market_context_block_safety

        binance_health_block_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
            binance_market_data_health_self_test={"status": "ok", "stdout_json": {"status": "failed"}},
        )
        assert (
            binance_health_block_safety["status"] == "blocked"
            and "binance_market_data_health_self_test_failed" in binance_health_block_safety["errors"]
        ), binance_health_block_safety

        binance_kline_builder_block_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
            binance_kline_cache_builder_self_test={"status": "ok", "stdout_json": {"status": "failed"}},
        )
        assert (
            binance_kline_builder_block_safety["status"] == "blocked"
            and "binance_kline_cache_builder_self_test_failed" in binance_kline_builder_block_safety["errors"]
        ), binance_kline_builder_block_safety

        recovery_optimizer_block_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
            strategy_recovery_optimizer_self_test={"status": "failed"},
        )
        assert (
            recovery_optimizer_block_safety["status"] == "blocked"
            and "strategy_recovery_optimizer_self_test_failed" in recovery_optimizer_block_safety["errors"]
        ), recovery_optimizer_block_safety

        pipeline_freshness_block_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
            pipeline_freshness_self_test={"status": "failed"},
        )
        assert (
            pipeline_freshness_block_safety["status"] == "blocked"
            and "pipeline_freshness_self_test_failed" in pipeline_freshness_block_safety["errors"]
        ), pipeline_freshness_block_safety

        automation_recovery_block_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
            automation_recovery_planner_self_test={"status": "failed"},
        )
        assert (
            automation_recovery_block_safety["status"] == "blocked"
            and "automation_recovery_planner_self_test_failed" in automation_recovery_block_safety["errors"]
        ), automation_recovery_block_safety

        paper_risk_control_block_safety = safety_status(
            ready_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
            paper_testnet_risk_control_self_test={"status": "failed"},
        )
        assert (
            paper_risk_control_block_safety["status"] == "blocked"
            and "paper_testnet_risk_control_self_test_failed" in paper_risk_control_block_safety["errors"]
        ), paper_risk_control_block_safety

        baseline_missing_safety = safety_status(
            stale_ledger,
            {"status": "ok"},
            {"status": "ok"},
            automation,
            {"status": "ok", "stdout_json": {"status": "pass"}},
        )
        assert "monthly_goal_baseline_missing_but_will_persist_on_next_save" in baseline_missing_safety["warnings"], baseline_missing_safety

    return {
        "status": "ok",
        "ready_decision": ready,
        "stale_decision": stale_readiness,
        "live_safety": live_safety,
        "automation_contract": automation["contract"],
        "unsafe_automation_contract": unsafe_automation["contract"],
        "safety_invariant_block_verified": True,
        "recovery_watchlist_monitor_block_verified": True,
        "capital_allocation_block_verified": True,
        "ledger_integrity_block_verified": True,
        "market_context_block_verified": True,
        "binance_market_data_health_block_verified": True,
        "binance_kline_cache_builder_block_verified": True,
        "strategy_recovery_optimizer_block_verified": True,
        "pipeline_freshness_block_verified": True,
        "automation_recovery_planner_block_verified": True,
        "paper_testnet_risk_control_block_verified": True,
        "baseline_missing_warning_verified": True,
        "uses_temporary_files_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--automation", default=str(DEFAULT_AUTOMATION))
    parser.add_argument("--max-ledger-age-hours", type=float, default=2.0)
    parser.add_argument("--near-expiry-hours", type=float, default=24.0)
    parser.add_argument("--self-test-timeout-seconds", type=int, default=60)
    parser.add_argument("--validation-timeout-seconds", type=int, default=90)
    parser.add_argument("--safety-invariant-timeout-seconds", type=int, default=60)
    parser.add_argument("--skip-self-test", action="store_true")
    parser.add_argument("--skip-safety-invariant-audit", action="store_true")
    parser.add_argument("--skip-validation-audit", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    run = build_run(args)
    if args.format == "markdown":
        print(render_report(run))
    else:
        print(json.dumps(compact(run) if args.compact_output else run, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
