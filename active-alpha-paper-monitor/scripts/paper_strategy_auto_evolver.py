#!/usr/bin/env python3
"""Auto-apply paper-only strategy iteration proposals into the overlay.

This is the missing bridge between attribution/backlog and the next paper run:
closed paper trades can change simulated rules, A/B tests, and cooldowns.
Nothing here can enable live orders or private exchange APIs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import paper_strategy_overlay as overlay_lib


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
BACKLOG_PATH = ACTIVE_ROOT / "experiments" / "strategy-iteration-backlog.json"
OVERLAY_PATH = ACTIVE_ROOT / "config" / "paper_strategy_auto_overlay.json"
REPORTS_DIR = ACTIVE_ROOT / "reports"
EXPERIMENTS_DIR = ACTIVE_ROOT / "experiments"
CHINA_TZ = dt.timezone(dt.timedelta(hours=8))

PAPER_ONLY_SCOPES = {
    "paper_only_closed_trade_attribution",
    "paper_only_validation_recovery_plan",
    "paper_only_strategy_iteration_backlog",
}


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=CHINA_TZ).replace(microsecond=0)


def stamp(value: dt.datetime) -> str:
    return value.strftime("%Y%m%d-%H%M%S")


def rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any, dry_run: bool = False) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def normalize_status(item: dict[str, Any]) -> str:
    status = str(item.get("operator_status") or item.get("status") or "")
    if status in {"paper_applied", "paper_ab_testing", "paper_reverted", "paper_promoted_candidate"}:
        return status
    scope = str(item.get("scope") or "")
    if scope in PAPER_ONLY_SCOPES or scope.startswith("paper_only"):
        return "auto_apply_paper_only"
    return status or "proposed_pending_human_review"


def affected_names(item: dict[str, Any], key: str) -> list[str]:
    names: list[str] = []
    for affected in item.get("affected_items") or []:
        if not isinstance(affected, dict):
            continue
        value = affected.get(key)
        if not value and key == "interval":
            candidate = str(affected.get("name") or "")
            if candidate in {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "UNKNOWN"}:
                value = candidate
        if value:
            names.append(str(value))
    return sorted(set(names))


def affected_item_names(item: dict[str, Any]) -> list[str]:
    names = [
        str(affected.get("name"))
        for affected in item.get("affected_items") or []
        if isinstance(affected, dict) and affected.get("name")
    ]
    return sorted(set(names))


def change_log_has(overlay: dict[str, Any], change_id: str, status: str | None = None) -> bool:
    for change in overlay.get("change_log") or []:
        if not isinstance(change, dict):
            continue
        if change.get("change_id") != change_id:
            continue
        if status is None or change.get("status") == status:
            return True
    return False


def latest_change_log_entry(overlay: dict[str, Any], change_id: str) -> dict[str, Any] | None:
    for change in reversed(overlay.get("change_log") or []):
        if isinstance(change, dict) and change.get("change_id") == change_id:
            return change
    return None


def dedupe_change_log(overlay: dict[str, Any]) -> dict[str, Any]:
    """Keep one auditable record per proposed change id.

    Earlier V2.118 runs appended the same auto-apply change every time the
    runner synced learning. Forward validation is keyed by change id, so
    duplicate log rows make the closed-sample count hard to trust. Preserve the
    latest row for each id and report how much noise was removed.
    """

    original = overlay.get("change_log") or []
    if not isinstance(original, list):
        overlay["change_log"] = []
        return {"original_count": 0, "deduped_count": 0, "removed_count": 0}
    by_id: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for change in original:
        if not isinstance(change, dict):
            continue
        change_id = str(change.get("change_id") or "").strip()
        if not change_id:
            anonymous.append(change)
            continue
        by_id[change_id] = change
    deduped = anonymous + list(by_id.values())
    overlay["change_log"] = deduped
    return {
        "original_count": len([item for item in original if isinstance(item, dict)]),
        "deduped_count": len(deduped),
        "removed_count": max(0, len([item for item in original if isinstance(item, dict)]) - len(deduped)),
    }


def recent_forward_samples(change: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    """Evaluate samples after a paper overlay change.

    A trade is related when it shares an affected entry mode, family, or interval.
    If the change has not yet produced five closed samples, the function reports
    an awaiting status and does not trigger rollback.
    """

    applied_at = parse_dt(change.get("applied_at"))
    entry_modes = set(change.get("affected_entry_modes") or [])
    families = set(change.get("affected_strategy_families") or [])
    intervals = set(change.get("affected_intervals") or [])
    related_closed: list[dict[str, Any]] = []
    excluded_missing_context = 0
    for trade in ledger.get("closed_trades") or []:
        if not isinstance(trade, dict):
            continue
        closed_at = parse_dt(trade.get("closed_at") or trade.get("updated_at"))
        if applied_at and closed_at and closed_at < applied_at.astimezone(dt.timezone.utc):
            continue
        strategy_candidate = trade.get("strategy_candidate") if isinstance(trade.get("strategy_candidate"), dict) else {}
        trade_interval = str(strategy_candidate.get("interval") or trade.get("interval") or "")
        related = (
            (entry_modes and str(trade.get("paper_entry_mode") or "") in entry_modes)
            or (families and str(trade.get("strategy_family") or "") in families)
            or (intervals and trade_interval in intervals)
        )
        if related:
            related_closed.append(trade)
            if not has_market_context(trade):
                excluded_missing_context += 1
    eligible_closed = [trade for trade in related_closed if has_market_context(trade)]
    recent = eligible_closed[-5:]
    pnl_values = [safe_float(item.get("realized_pnl_usd")) for item in recent]
    gross_edge = sum(max(safe_float(item.get("gross_pnl_usd")), 0.0) for item in recent)
    friction = sum(
        safe_float(item.get("entry_commission_usd"))
        + safe_float(item.get("exit_commission_usd"))
        + safe_float(item.get("estimated_slippage_usd"))
        + safe_float(item.get("slippage_usd"))
        for item in recent
    )
    wins = len([value for value in pnl_values if value > 0])
    return {
        "sample_count": len(recent),
        "raw_related_closed_count": len(related_closed),
        "excluded_missing_market_context_count": excluded_missing_context,
        "win_rate_pct": round(wins / len(recent) * 100.0, 4) if recent else 0.0,
        "net_pnl_usd": round(sum(pnl_values), 6),
        "friction_to_gross_edge_pct": round(friction / gross_edge * 100.0, 4) if gross_edge > 0 else None,
        "status": "ready" if len(recent) >= 5 else "awaiting_forward_samples",
        "operator_note": "Trades missing market context are excluded from forward validation and cannot promote a paper rule.",
    }


def rollback_needed(sample: dict[str, Any], rules: dict[str, Any]) -> list[str]:
    if int(sample.get("sample_count") or 0) < int(rules.get("min_forward_samples") or 5):
        return []
    reasons: list[str] = []
    if safe_float(sample.get("win_rate_pct")) < safe_float(rules.get("recent_win_rate_floor_pct"), 30.0):
        reasons.append("recent_5_win_rate_below_30_pct")
    if rules.get("recent_net_pnl_must_be_positive") and safe_float(sample.get("net_pnl_usd")) < 0:
        reasons.append("recent_5_net_pnl_negative")
    friction_pct = sample.get("friction_to_gross_edge_pct")
    if friction_pct is not None and safe_float(friction_pct) > safe_float(rules.get("friction_cost_to_gross_edge_ceiling_pct"), 40.0):
        reasons.append("friction_cost_above_40_pct_of_gross_edge")
    return reasons


def has_market_context(trade: dict[str, Any]) -> bool:
    context = trade.get("market_context_at_entry")
    if isinstance(context, dict):
        values = [
            context.get("market_regime"),
            context.get("market_atmosphere"),
            context.get("short_term_state"),
            context.get("sentiment_state"),
        ]
        if any(str(item or "").strip() and str(item).strip() not in {"unknown", "unknown_or_not_attached"} for item in values):
            return True
    keys = ("market_regime", "market_atmosphere", "short_term_state", "sentiment_state")
    return any(
        str(trade.get(key) or "").strip()
        and str(trade.get(key)).strip() not in {"unknown", "unknown_or_not_attached"}
        for key in keys
    )


def apply_change(item: dict[str, Any], overlay: dict[str, Any], generated_at: str) -> dict[str, Any]:
    change_id = str(item.get("proposed_change_id") or "").strip()
    change_type = str(item.get("change_type") or "")
    status = normalize_status(item)
    applied_status = "paper_ab_testing" if "tighten" in change_type or "quality_filter" in change_type else "paper_applied"
    result = {
        "change_id": change_id,
        "change_type": change_type,
        "input_status": status,
        "output_status": "ignored",
        "affected_entry_modes": [],
        "affected_strategy_families": [],
        "affected_intervals": [],
        "notes": [],
    }
    if not change_id:
        result["notes"].append("missing_change_id")
        return result
    existing = latest_change_log_entry(overlay, change_id)
    if existing:
        result["output_status"] = existing.get("status") or status
        result["affected_entry_modes"] = list(existing.get("affected_entry_modes") or [])
        result["affected_strategy_families"] = list(existing.get("affected_strategy_families") or [])
        result["affected_intervals"] = list(existing.get("affected_intervals") or [])
        result["notes"].append("already_recorded_no_reapply")
        return result
    if status != "auto_apply_paper_only":
        result["output_status"] = status
        result["notes"].append("not_paper_auto_apply_scope")
        return result

    proposed = item.get("proposed_adjustment") if isinstance(item.get("proposed_adjustment"), dict) else {}
    validation = item.get("validation_plan") if isinstance(item.get("validation_plan"), dict) else {}
    entry_modes = affected_names(item, "paper_entry_mode")
    families = affected_names(item, "strategy_family")
    intervals = affected_names(item, "interval")

    if change_type in {"review_negative_entry_mode", "retire_failed_sampling_paths", "tighten_exploratory_probe_gate"}:
        if not entry_modes and "exploratory" in change_type:
            entry_modes = ["info_exploratory_probe"]
        if not entry_modes and change_type == "retire_failed_sampling_paths":
            entry_modes = affected_item_names(item)
        for mode in entry_modes:
            overlay["entry_mode_rules"][mode] = {
                "status": "cooldown" if "retire" in change_type or "negative" in change_type else "paper_ab_testing",
                "paper_status": applied_status,
                "reason": item.get("rationale") or item.get("title"),
                "requires_retest": True,
                "min_forward_samples": validation.get("minimum_new_closed_samples") or 5,
                "source_change_id": change_id,
                "candidate_rules": proposed.get("candidate_rules") or [],
            }
    if change_type in {"review_negative_strategy_family", "tighten_momentum_confirmation"}:
        if not families and change_type == "tighten_momentum_confirmation":
            families = ["momentum"]
        for family in families or ["momentum"]:
            overlay["strategy_family_rules"][family] = {
                "status": "paper_ab_testing",
                "paper_status": applied_status,
                "reason": item.get("rationale") or item.get("title"),
                "requires_retest": True,
                "min_forward_samples": validation.get("minimum_new_closed_samples") or 5,
                "source_change_id": change_id,
                "candidate_rules": proposed.get("candidate_rules") or [],
            }
    if change_type == "interval_quality_filter":
        for interval in intervals:
            overlay["interval_rules"][interval] = {
                "status": "paper_ab_testing",
                "paper_status": applied_status,
                "reason": item.get("rationale") or item.get("title"),
                "requires_retest": True,
                "min_forward_samples": validation.get("minimum_new_closed_samples") or 5,
                "source_change_id": change_id,
            }
    if change_type == "tighten_profit_protection":
        overlay.setdefault("exit_rules", {}).setdefault("profit_protection", {}).update(
            {
                "status": "paper_ab_testing",
                "paper_status": "paper_applied",
                "arm_after_mfe_pct": 2.0,
                "trailing_floor_pct": 0.25,
                "reason": item.get("rationale") or item.get("title"),
                "source_change_id": change_id,
            }
        )

    result["affected_entry_modes"] = entry_modes
    result["affected_strategy_families"] = families
    result["affected_intervals"] = intervals
    if not any([entry_modes, families, intervals, change_type == "tighten_profit_protection"]):
        result["output_status"] = "auto_apply_no_overlay_mapping"
        result["notes"].append("tracked_but_no_overlay_rule_written")
        applied_status = "paper_ab_testing"
    else:
        result["output_status"] = applied_status

    overlay.setdefault("change_log", []).append(
        {
            "change_id": change_id,
            "status": result["output_status"],
            "change_type": change_type,
            "applied_at": generated_at,
            "scope": "paper_only",
            "title": item.get("title"),
            "reason": item.get("rationale"),
            "affected_entry_modes": entry_modes,
            "affected_strategy_families": families,
            "affected_intervals": intervals,
            "source": item.get("source"),
            "previous_operator_status": item.get("operator_status"),
            "live_orders_enabled": False,
            "private_api_used": False,
        }
    )
    return result


def evaluate_rollbacks(overlay: dict[str, Any], ledger: dict[str, Any], generated_at: str) -> list[dict[str, Any]]:
    rules = overlay.get("rollback_rules") if isinstance(overlay.get("rollback_rules"), dict) else {}
    results: list[dict[str, Any]] = []
    for change in overlay.get("change_log") or []:
        if not isinstance(change, dict):
            continue
        if change.get("status") not in {"paper_applied", "paper_ab_testing"}:
            continue
        sample = recent_forward_samples(change, ledger)
        reasons = rollback_needed(sample, rules)
        result = {
            "change_id": change.get("change_id"),
            "status_before": change.get("status"),
            "forward_sample": sample,
            "rollback_reasons": reasons,
            "status_after": change.get("status"),
        }
        if reasons:
            change["status"] = "paper_reverted"
            change["reverted_at"] = generated_at
            change["rollback_reasons"] = reasons
            result["status_after"] = "paper_reverted"
            for mode in change.get("affected_entry_modes") or []:
                rule = overlay.get("entry_mode_rules", {}).get(mode)
                if isinstance(rule, dict) and rule.get("source_change_id") == change.get("change_id"):
                    rule["status"] = "paper_reverted"
                    rule["rollback_reasons"] = reasons
            for family in change.get("affected_strategy_families") or []:
                rule = overlay.get("strategy_family_rules", {}).get(family)
                if isinstance(rule, dict) and rule.get("source_change_id") == change.get("change_id"):
                    rule["status"] = "paper_reverted"
                    rule["rollback_reasons"] = reasons
        results.append(result)
    return results


def render_markdown(record: dict[str, Any]) -> str:
    summary = record.get("summary") or {}
    lines = [
        f"# Paper Strategy Auto Evolver | {record.get('run_id')}",
        "",
        "- scope: `paper_only_auto_strategy_evolver`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        "- allow_real_orders: `false`",
        f"- overlay: `{record.get('overlay_path')}`",
        "",
        "## Summary",
        "",
        f"- status: `{record.get('status')}`",
        f"- strategy_version: `{summary.get('strategy_version')}`",
        f"- considered_changes: `{summary.get('considered_changes')}`",
        f"- applied_or_ab_testing: `{summary.get('applied_or_ab_testing')}`",
        f"- newly_applied_or_ab_testing: `{summary.get('newly_applied_or_ab_testing')}`",
        f"- reverted: `{summary.get('reverted')}`",
        f"- awaiting_forward_samples: `{summary.get('awaiting_forward_samples')}`",
        f"- deduped_change_log_removed: `{summary.get('deduped_change_log_removed')}`",
        f"- strategy_version_changed: `{summary.get('strategy_version_changed')}`",
        "",
        "## Applied Decisions",
        "",
        "| Change | Type | Output | Entry Modes | Families | Intervals | Notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in record.get("applied_changes") or []:
        lines.append(
            f"| `{item.get('change_id')}` | `{item.get('change_type')}` | `{item.get('output_status')}` | "
            f"`{item.get('affected_entry_modes')}` | `{item.get('affected_strategy_families')}` | "
            f"`{item.get('affected_intervals')}` | `{item.get('notes')}` |"
        )
    if not record.get("applied_changes"):
        lines.append("| - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Rollback Watch",
            "",
            "| Change | Status | Eligible Samples | Raw Related | Context Excluded | Win Rate | Net PnL | Rollback Reasons |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in record.get("rollback_checks") or []:
        sample = item.get("forward_sample") or {}
        lines.append(
            f"| `{item.get('change_id')}` | `{item.get('status_after')}` | `{sample.get('sample_count')}` | "
            f"`{sample.get('raw_related_closed_count')}` | `{sample.get('excluded_missing_market_context_count')}` | "
            f"`{sample.get('win_rate_pct')}` | `${sample.get('net_pnl_usd')}` | `{item.get('rollback_reasons')}` |"
        )
    if not record.get("rollback_checks"):
        lines.append("| - | - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Guardrail",
            "",
            "- 本脚本只改变 paper overlay，不开仓、不平仓、不调用私有 API。",
            "- 新规则只能影响模拟交易；真实交易仍需要 Phase 2/3、testnet、kill switch、最小权限 API 和人工确认。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_record(args: argparse.Namespace) -> dict[str, Any]:
    created = now_local()
    generated_at = created.isoformat()
    run_id = f"{stamp(created)}-paper-strategy-auto-evolver"
    backlog = read_json(Path(args.backlog), {})
    ledger = read_json(Path(args.ledger), {})
    overlay = overlay_lib.default_overlay() if getattr(args, "reset_overlay", False) else overlay_lib.load_overlay(Path(args.overlay))
    original_strategy_version = overlay.get("strategy_version") or overlay_lib.strategy_version(overlay)
    dedupe_result = dedupe_change_log(overlay)

    items = backlog.get("items") if isinstance(backlog.get("items"), dict) else {}
    active_items = [
        item for item in items.values()
        if isinstance(item, dict) and item.get("currently_present", True)
    ]
    applied: list[dict[str, Any]] = []
    for item in active_items:
        applied.append(apply_change(item, overlay, generated_at))
    rollback_checks = evaluate_rollbacks(overlay, ledger if isinstance(ledger, dict) else {}, generated_at)
    awaiting = [
        item for item in rollback_checks
        if (item.get("forward_sample") or {}).get("status") == "awaiting_forward_samples"
    ]
    applied_or_ab = [
        item for item in applied
        if item.get("output_status") in {"paper_applied", "paper_ab_testing"}
    ]
    newly_applied_or_ab = [
        item for item in applied_or_ab
        if "already_recorded_no_reapply" not in (item.get("notes") or [])
    ]
    reverted = [item for item in rollback_checks if item.get("status_after") == "paper_reverted"]
    if newly_applied_or_ab or reverted or getattr(args, "reset_overlay", False):
        overlay["strategy_version"] = f"paper-auto-v{created.strftime('%Y%m%d-%H%M%S')}"
    else:
        overlay["strategy_version"] = original_strategy_version
    record = {
        "run_id": run_id,
        "created_at": generated_at,
        "scope": "paper_only_auto_strategy_evolver",
        "status": "ok",
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "backlog_path": rel(Path(args.backlog)),
        "ledger_path": rel(Path(args.ledger)),
        "overlay_path": rel(Path(args.overlay)),
        "summary": {
            "strategy_version": overlay.get("strategy_version"),
            "considered_changes": len(active_items),
            "applied_or_ab_testing": len(applied_or_ab),
            "newly_applied_or_ab_testing": len(newly_applied_or_ab),
            "reverted": len(reverted),
            "awaiting_forward_samples": len(awaiting),
            "deduped_change_log_removed": dedupe_result.get("removed_count"),
            "strategy_version_changed": overlay.get("strategy_version") != original_strategy_version,
        },
        "applied_changes": applied,
        "rollback_checks": rollback_checks,
        "change_log_dedupe": dedupe_result,
        "overlay_snapshot": overlay,
    }
    if not args.dry_run:
        overlay_lib.save_overlay(overlay, Path(args.overlay))
        experiment_path = EXPERIMENTS_DIR / f"{run_id}.json"
        report_path = REPORTS_DIR / f"{created.strftime('%Y-%m-%d')}-paper-strategy-auto-evolver.md"
        write_json(experiment_path, record)
        report_path.write_text(render_markdown(record), encoding="utf-8")
        record["experiment_path"] = rel(experiment_path)
        record["report_path"] = rel(report_path)
    return record


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        overlay = root / "overlay.json"
        backlog = root / "backlog.json"
        ledger = root / "ledger.json"
        write_json(
            backlog,
            {
                "items": {
                    "pc-test": {
                        "proposed_change_id": "pc-test",
                        "currently_present": True,
                        "scope": "paper_only_closed_trade_attribution",
                        "change_type": "review_negative_entry_mode",
                        "affected_items": [{"paper_entry_mode": "info_exploratory_probe"}],
                        "validation_plan": {"minimum_new_closed_samples": 5},
                    }
                }
            },
        )
        write_json(ledger, {"closed_trades": [], "live_orders_enabled": False, "private_api_used": False})
        args = argparse.Namespace(backlog=str(backlog), ledger=str(ledger), overlay=str(overlay), dry_run=False)
        record = build_record(args)
        out = read_json(overlay, {})
        assert record["summary"]["applied_or_ab_testing"] == 1, record
        assert out["entry_mode_rules"]["info_exploratory_probe"]["status"] == "cooldown", out
        second = build_record(args)
        out_second = read_json(overlay, {})
        assert second["summary"]["newly_applied_or_ab_testing"] == 0, second
        assert len(out_second["change_log"]) == 1, out_second
        assert out_second["strategy_version"] == out["strategy_version"], out_second
        assert out["live_orders_enabled"] is False and out["private_api_used"] is False
        sample = recent_forward_samples(
            {
                "applied_at": "2026-01-01T00:00:00+00:00",
                "affected_entry_modes": ["info_exploratory_probe"],
            },
            {
                "closed_trades": [
                    {
                        "paper_entry_mode": "info_exploratory_probe",
                        "closed_at": "2026-01-02T00:00:00+00:00",
                        "realized_pnl_usd": 1,
                    },
                    {
                        "paper_entry_mode": "info_exploratory_probe",
                        "closed_at": "2026-01-03T00:00:00+00:00",
                        "realized_pnl_usd": 1,
                        "market_context_at_entry": {"market_regime": "risk_on_momentum"},
                    },
                ]
            },
        )
        assert sample["raw_related_closed_count"] == 2 and sample["sample_count"] == 1, sample
        assert sample["excluded_missing_market_context_count"] == 1, sample
        rollback_overlay = overlay_lib.default_overlay()
        rollback_overlay["change_log"] = [
            {
                "change_id": "pc-rollback",
                "status": "paper_ab_testing",
                "applied_at": "2026-01-01T00:00:00+00:00",
                "affected_entry_modes": ["info_exploratory_probe"],
            }
        ]
        rollback_checks = evaluate_rollbacks(
            rollback_overlay,
            {
                "closed_trades": [
                    {
                        "paper_entry_mode": "info_exploratory_probe",
                        "closed_at": f"2026-01-0{idx}T00:00:00+00:00",
                        "realized_pnl_usd": -1,
                        "market_context_at_entry": {"market_regime": "risk_on_momentum"},
                    }
                    for idx in range(2, 7)
                ]
            },
            "2026-01-07T00:00:00+00:00",
        )
        assert rollback_checks[0]["status_after"] == "paper_reverted", rollback_checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-apply paper-only strategy learning into overlay.")
    parser.add_argument("--backlog", default=str(BACKLOG_PATH))
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    parser.add_argument("--overlay", default=str(OVERLAY_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset-overlay", action="store_true", help="Rebuild the paper-only overlay from current backlog instead of preserving old generated rules.")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "self_test_passed", "live_orders_enabled": False, "private_api_used": False}))
        return 0
    record = build_record(args)
    result = {
        "status": record.get("status"),
        "run_id": record.get("run_id"),
        "strategy_version": (record.get("summary") or {}).get("strategy_version"),
        "applied_or_ab_testing": (record.get("summary") or {}).get("applied_or_ab_testing"),
        "newly_applied_or_ab_testing": (record.get("summary") or {}).get("newly_applied_or_ab_testing"),
        "reverted": (record.get("summary") or {}).get("reverted"),
        "deduped_change_log_removed": (record.get("summary") or {}).get("deduped_change_log_removed"),
        "strategy_version_changed": (record.get("summary") or {}).get("strategy_version_changed"),
        "overlay": record.get("overlay_path"),
        "report": record.get("report_path"),
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    if args.compact_output:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Wrote paper auto evolver report: {record.get('report_path')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
