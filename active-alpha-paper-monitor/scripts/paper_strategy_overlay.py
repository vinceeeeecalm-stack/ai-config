#!/usr/bin/env python3
"""Paper-only strategy overlay helpers.

The overlay is intentionally small and auditable: it can block or tag paper
entries, but it cannot authorize live orders or private exchange APIs.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
OVERLAY_PATH = ACTIVE_ROOT / "config" / "paper_strategy_auto_overlay.json"
CHINA_TZ = dt.timezone(dt.timedelta(hours=8))

TERMINAL_BLOCK_STATUSES = {"blocked", "cooldown", "retired", "paper_reverted"}


def now_local_iso() -> str:
    return dt.datetime.now(tz=CHINA_TZ).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_overlay() -> dict[str, Any]:
    created = now_local_iso()
    return {
        "strategy_version": "paper-auto-v20260627-001",
        "created_at": created,
        "updated_at": created,
        "scope": "paper_only_auto_strategy_overlay",
        "auto_learning_enabled": True,
        "auto_apply_scope": "paper_only",
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "default_decision": "allow",
        "risk_rules": {
            "max_open_positions": 1,
            "default_notional_usd": 25,
            "max_symbol_open_notional_usd": 25,
            "paper_only": True,
        },
        "entry_mode_rules": {},
        "strategy_family_rules": {},
        "interval_rules": {},
        "exit_rules": {
            "profit_protection": {
                "status": "paper_ab_testing",
                "arm_after_mfe_pct": 2.0,
                "trailing_floor_pct": 0.25,
                "source": "default_overlay",
            }
        },
        "rollback_rules": {
            "min_forward_samples": 5,
            "recent_win_rate_floor_pct": 30,
            "recent_net_pnl_must_be_positive": True,
            "max_drawdown_worsen_multiple": 1.5,
            "friction_cost_to_gross_edge_ceiling_pct": 40,
        },
        "change_log": [],
        "operator_note": "Paper-only overlay. It may alter simulated entries and tags only; it cannot enable real trading.",
    }


def ensure_safety(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["live_orders_enabled"] = False
    payload["private_api_used"] = False
    payload["allow_real_orders"] = False
    payload.setdefault("scope", "paper_only_auto_strategy_overlay")
    payload.setdefault("auto_apply_scope", "paper_only")
    payload.setdefault("auto_learning_enabled", True)
    payload.setdefault("entry_mode_rules", {})
    payload.setdefault("strategy_family_rules", {})
    payload.setdefault("interval_rules", {})
    payload.setdefault("exit_rules", {})
    payload.setdefault("risk_rules", {})
    payload.setdefault("change_log", [])
    payload.setdefault("rollback_rules", default_overlay()["rollback_rules"])
    payload.setdefault("strategy_version", default_overlay()["strategy_version"])
    payload["updated_at"] = payload.get("updated_at") or now_local_iso()
    return payload


def load_overlay(path: Path = OVERLAY_PATH) -> dict[str, Any]:
    payload = read_json(path, {})
    if not isinstance(payload, dict) or not payload:
        payload = default_overlay()
    return ensure_safety(payload)


def save_overlay(payload: dict[str, Any], path: Path = OVERLAY_PATH) -> None:
    out = ensure_safety(payload)
    out["updated_at"] = now_local_iso()
    write_json(path, out)


def strategy_version(overlay: dict[str, Any] | None = None) -> str:
    payload = overlay if isinstance(overlay, dict) else load_overlay()
    return str(payload.get("strategy_version") or default_overlay()["strategy_version"])


def _rule_decision(name: str, rule: dict[str, Any] | None, rule_type: str) -> tuple[bool, dict[str, Any]]:
    if not isinstance(rule, dict) or not rule:
        return True, {"decision": "allow", "rule_type": rule_type, "name": name, "reason": "no_overlay_rule"}
    status = str(rule.get("status") or rule.get("paper_status") or "paper_applied")
    allow = status not in TERMINAL_BLOCK_STATUSES
    decision = "allow" if allow else "block"
    return allow, {
        "decision": decision,
        "rule_type": rule_type,
        "name": name,
        "status": status,
        "reason": rule.get("reason") or rule.get("rationale") or f"{rule_type}_{status}",
        "source_change_id": rule.get("source_change_id"),
        "requires_retest": rule.get("requires_retest"),
        "min_forward_samples": rule.get("min_forward_samples"),
    }


def entry_mode_overlay_gate(entry_mode: str, overlay: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
    payload = overlay if isinstance(overlay, dict) else load_overlay()
    rules = payload.get("entry_mode_rules") if isinstance(payload.get("entry_mode_rules"), dict) else {}
    return _rule_decision(entry_mode, rules.get(entry_mode), "entry_mode")


def strategy_family_overlay_gate(family: str, overlay: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
    payload = overlay if isinstance(overlay, dict) else load_overlay()
    rules = payload.get("strategy_family_rules") if isinstance(payload.get("strategy_family_rules"), dict) else {}
    return _rule_decision(family, rules.get(family), "strategy_family")


def interval_overlay_gate(interval: str, overlay: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
    payload = overlay if isinstance(overlay, dict) else load_overlay()
    rules = payload.get("interval_rules") if isinstance(payload.get("interval_rules"), dict) else {}
    return _rule_decision(interval, rules.get(interval), "interval")


def combined_overlay_gate(
    *,
    entry_mode: str,
    strategy_family: str | None = None,
    interval: str | None = None,
    overlay: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    payload = overlay if isinstance(overlay, dict) else load_overlay()
    checks: list[dict[str, Any]] = []
    allow_entry, entry_decision = entry_mode_overlay_gate(entry_mode, payload)
    checks.append(entry_decision)
    allow_family = True
    if strategy_family:
        allow_family, family_decision = strategy_family_overlay_gate(strategy_family, payload)
        checks.append(family_decision)
    allow_interval = True
    if interval:
        allow_interval, interval_decision = interval_overlay_gate(interval, payload)
        checks.append(interval_decision)
    allow = allow_entry and allow_family and allow_interval
    return allow, {
        "decision": "allow" if allow else "block",
        "strategy_version": strategy_version(payload),
        "overlay_updated_at": payload.get("updated_at"),
        "checks": checks,
    }


def apply_overlay_to_position(
    position: dict[str, Any],
    overlay: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = overlay if isinstance(overlay, dict) else load_overlay()
    position["strategy_version"] = strategy_version(payload)
    position["paper_strategy_overlay_version"] = strategy_version(payload)
    position["paper_auto_learning_enabled"] = bool(payload.get("auto_learning_enabled", True))
    position["paper_strategy_overlay_decision"] = decision or {"decision": "allow"}
    position["live_orders_enabled"] = False
    position["private_api_used"] = False
    position["allow_real_orders"] = False
    return position
