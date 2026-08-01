#!/usr/bin/env python3
"""Build a paper-only strategy recovery queue after validation losses.

This script is research-only. It reads the latest validation audit and
walk-forward evidence, filters out retired/cooldown groups from the recovery
plan, and emits a small queue of strategy families that may be worth future
paper retesting. It never opens, closes, or mutates paper positions.
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
DEFAULT_EXPERIMENT_DIR = ACTIVE_ROOT / "experiments"
DEFAULT_PAPER_DIR = ACTIVE_ROOT / "paper_trades"
DEFAULT_RECOMMENDATION_LEDGER = WORKSPACE_ROOT / "manual-investment-strategy-operator" / "recommendations" / "recommendation_history.json"
DEFAULT_REPRO_AUDIT = DEFAULT_EXPERIMENT_DIR / "kline-research-reproducibility-audit.json"
INITIAL_CAPITAL_USD = 500.0


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalized_label(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ")


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return WORKSPACE_ROOT / path


def display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def run_validation_audit(args: argparse.Namespace) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "validation_sample_auditor.py"),
        "--format",
        "json",
        "--paper-dir",
        args.paper_dir,
        "--experiment-dir",
        args.experiment_dir,
        "--recommendation-ledger",
        args.recommendation_ledger,
    ]
    result = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), text=True, capture_output=True, check=False, timeout=args.audit_timeout_seconds)
    if result.returncode != 0:
        return {
            "status": "failed",
            "error": result.stderr[-2000:] or result.stdout[-2000:] or f"validation_sample_auditor returned {result.returncode}",
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"status": "failed", "error": f"validation_sample_auditor stdout was not JSON: {exc}"}
    if not isinstance(payload, dict):
        return {"status": "failed", "error": "validation_sample_auditor JSON was not an object"}
    return payload


def looks_like_walkforward(path: Path) -> bool:
    try:
        payload = load_json(path)
    except Exception:  # noqa: BLE001
        return False
    if payload.get("audit_version") or payload.get("walkforward_sample_metrics"):
        return False
    return "frames_loaded" in payload and bool(payload.get("top_ranked") or payload.get("results"))


def latest_walkforward(experiment_dir: Path) -> tuple[dict[str, Any] | None, Path | None]:
    files = (
        sorted(experiment_dir.glob("*localized-lightweight-extended-history-lab*.json"))
        + sorted(experiment_dir.glob("*lightweight-extended-history-lab*.json"))
        + sorted(experiment_dir.glob("*weekly-goal*.json"))
        + sorted(experiment_dir.glob("*walkforward*.json"))
    )
    files = [path for path in files if path.is_file() and looks_like_walkforward(path)]
    if not files:
        return None, None
    latest = max(files, key=lambda path: path.stat().st_mtime)
    return load_json(latest), latest


def reproducibility_for_walkforward(
    path: Path | None,
    audit_path: Path = DEFAULT_REPRO_AUDIT,
) -> dict[str, Any]:
    if not path or not audit_path.exists():
        return {"status": "missing_reproducibility_audit", "promotion_allowed": False}
    audit = load_json(audit_path)
    path_ref = display_path(path) or str(path)
    for item in audit.get("artifacts") or []:
        if item.get("artifact") == path_ref:
            return {
                "status": item.get("reproducibility_status"),
                "promotion_allowed": item.get("promotion_allowed") is True,
                "raw_cache_available": item.get("raw_cache_available"),
                "protocol_ok": item.get("protocol_ok"),
                "execution_model_ok": item.get("execution_model_ok"),
                "artifact": path_ref,
            }
    return {"status": "walkforward_not_present_in_reproducibility_audit", "promotion_allowed": False, "artifact": path_ref}


def recovery_items(plan: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in keys:
        for item in plan.get(key) or []:
            if isinstance(item, dict):
                items.append({**item, "_source_key": key})
    return items


def recovery_match(plan: dict[str, Any], group: str, names: list[Any], statuses: set[str]) -> dict[str, Any] | None:
    key_map = {
        "entry_mode": ["retired_entry_modes", "cooldown_entry_modes"],
        "strategy_family": ["retired_strategy_families", "cooldown_strategy_families"],
        "interval": ["retired_intervals", "cooldown_intervals"],
        "symbol": ["retired_symbols", "cooldown_symbols"],
    }
    wanted = {normalized_label(name) for name in names if name}
    if not wanted:
        return None
    for item in recovery_items(plan, key_map.get(group, [])):
        if str(item.get("status") or "") not in statuses:
            continue
        if normalized_label(item.get("name")) in wanted:
            return item
    return None


def proposed_entry_mode(stage: str, current_signal: bool, oos: dict[str, Any], train: dict[str, Any]) -> str:
    if current_signal and stage in {"target_research_pass_current_signal", "paper_forward_candidate_current_signal"}:
        return "strict_strategy_gate"
    if (as_float(oos.get("net_return_pct"), 0.0) or 0.0) >= 50.0 and (as_float(train.get("net_return_pct"), 0.0) or 0.0) >= 20.0:
        return "strict_strategy_gate"
    return "info_exploratory_probe"


def strategy_score(
    oos: dict[str, Any],
    train: dict[str, Any],
    validation: dict[str, Any],
    current_signal: bool,
    stage: str,
    pre_holdout_selection_score: float,
) -> float:
    oos_return = as_float(oos.get("net_return_pct"), 0.0) or 0.0
    train_return = as_float(train.get("net_return_pct"), 0.0) or 0.0
    oos_win = as_float(oos.get("win_rate_pct"), 0.0) or 0.0
    train_win = as_float(train.get("win_rate_pct"), 0.0) or 0.0
    oos_dd = as_float(oos.get("max_drawdown_pct"), 0.0) or 0.0
    train_dd = as_float(train.get("max_drawdown_pct"), 0.0) or 0.0
    oos_trades = as_int(oos.get("trade_count"))
    validation_return = as_float(validation.get("net_return_pct"), 0.0) or 0.0
    validation_win = as_float(validation.get("win_rate_pct"), 0.0) or 0.0
    current_bonus = 25.0 if current_signal else 0.0
    stage_bonus = 35.0 if str(stage).startswith("target_research_pass") else 15.0 if "paper" in str(stage) else 0.0
    consistency_penalty = (
        abs(validation_return - train_return) * 0.15
        + abs(validation_win - train_win) * 0.2
    )
    drawdown_penalty = abs(min(oos_dd, train_dd, 0.0)) * 1.2
    return round(
        max(-100.0, min(pre_holdout_selection_score, 100.0)) * 0.8
        + train_return * 0.20
        + validation_return * 0.50
        + validation_win * 0.2
        + min(oos_trades, 80) * 0.35
        + current_bonus
        + stage_bonus
        - consistency_penalty
        - drawdown_penalty,
        6,
    )


def extract_walkforward_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("top_ranked") or payload.get("results") or []
    candidates: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        oos = item.get("best_oos") or {}
        if not isinstance(oos, dict):
            continue
        strategy = oos.get("strategy") or {}
        train = oos.get("train_summary") or {}
        validation = oos.get("validation_summary") or {}
        family = strategy.get("family") or "UNKNOWN"
        interval = item.get("interval") or strategy.get("interval") or "UNKNOWN"
        symbol = str(item.get("symbol") or "UNKNOWN").upper()
        stage = str(oos.get("stage") or "failed")
        current_signal = bool(oos.get("current_signal"))
        entry_mode = proposed_entry_mode(stage, current_signal, oos, train)
        candidates.append(
            {
                "symbol": symbol,
                "interval": interval,
                "strategy_family": family,
                "paper_entry_mode": entry_mode,
                "stage": stage,
                "current_signal": current_signal,
                "strategy": strategy,
                "train_summary": train,
                "validation_summary": validation,
                "selection_score": oos.get("selection_score"),
                "holdout_used_for_selection": oos.get("holdout_used_for_selection"),
                "oos_summary": {
                    key: oos.get(key)
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
                        "expectancy_pct",
                        "profit_factor",
                        "largest_winner_share_pct",
                    ]
                },
                "score": strategy_score(
                    oos,
                    train,
                    validation,
                    current_signal,
                    stage,
                    float(as_float(oos.get("selection_score"), -999999.0)),
                ),
            }
        )
    return candidates


def candidate_quality(candidate: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    oos = candidate.get("oos_summary") or {}
    train = candidate.get("train_summary") or {}
    validation = candidate.get("validation_summary") or {}
    if candidate.get("holdout_used_for_selection") is not False:
        reasons.append("final_holdout_selection_contract_missing")
    if not validation:
        reasons.append("validation_summary_missing")
    if as_int(oos.get("trade_count")) < 8:
        reasons.append("oos_trade_sample_below_8")
    if (as_float(oos.get("final_capital"), 0.0) or 0.0) <= INITIAL_CAPITAL_USD:
        reasons.append("oos_final_capital_not_above_initial")
    if (as_float(oos.get("max_drawdown_pct"), 0.0) or 0.0) < -15.0:
        reasons.append("oos_drawdown_deeper_than_15_pct")
    if as_int(train.get("trade_count")) < 8:
        reasons.append("train_trade_sample_below_8")
    if (as_float(train.get("final_capital"), 0.0) or 0.0) <= INITIAL_CAPITAL_USD:
        reasons.append("train_final_capital_not_above_initial")
    if as_int(validation.get("trade_count")) < 4:
        reasons.append("validation_trade_sample_below_4")
    if (as_float(validation.get("net_return_pct"), 0.0) or 0.0) <= 0.0:
        reasons.append("validation_net_return_not_positive")
    if (as_float(validation.get("max_drawdown_pct"), 0.0) or 0.0) < -15.0:
        reasons.append("validation_drawdown_deeper_than_15_pct")
    return reasons


def build_recovery_queue(audit: dict[str, Any], walkforward: dict[str, Any], max_candidates: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    plan = audit.get("validation_recovery_plan") or {}
    statuses = {"retire_from_new_samples", "cooldown_until_retested"}
    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in extract_walkforward_candidates(walkforward):
        key = (
            str(candidate["symbol"]),
            str(candidate["interval"]),
            str(candidate["strategy_family"]),
            str(candidate["paper_entry_mode"]),
        )
        if key in seen:
            continue
        seen.add(key)
        quality_flags = candidate_quality(candidate)
        candidate["quality_flags"] = quality_flags
        block: dict[str, Any] | None = None
        for group, names in [
            ("entry_mode", [candidate.get("paper_entry_mode")]),
            ("strategy_family", [candidate.get("strategy_family"), f"{candidate.get('interval')} {candidate.get('strategy_family')}", f"{candidate.get('strategy_family')} {candidate.get('interval')}"]),
            ("interval", [candidate.get("interval")]),
            ("symbol", [candidate.get("symbol")]),
        ]:
            match = recovery_match(plan, group, names, statuses)
            if match:
                block = {
                    "matched_group": group,
                    "matched_name": match.get("name"),
                    "matched_status": match.get("status"),
                    "matched_source_key": match.get("_source_key"),
                    "reason": match.get("reason"),
                    "closed_count": match.get("closed_count"),
                    "win_rate_pct": match.get("win_rate_pct"),
                    "realized_net_return_pct": match.get("realized_net_return_pct"),
                }
                break
        if block:
            blocked.append({**candidate, "recovery_block": block})
            continue
        if quality_flags:
            blocked.append({**candidate, "recovery_block": {"matched_group": "quality_floor", "reason": ", ".join(quality_flags)}})
            continue
        candidate["recommended_next_action"] = "paper_retest_watch"
        candidate["operator_note"] = "Research-only recovery queue; future paper entry must still pass live market, liquidity, friction, capacity, and recovery gates."
        eligible.append(candidate)
    eligible.sort(key=lambda item: item.get("score") or 0.0, reverse=True)
    blocked.sort(key=lambda item: item.get("score") or 0.0, reverse=True)
    return eligible[:max_candidates], blocked[:max_candidates]


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    audit = load_json(args.audit_json) if args.audit_json else run_validation_audit(args)
    experiment_dir = resolve_path(args.experiment_dir)
    walkforward = None
    walkforward_path = None
    if args.walkforward_json:
        walkforward_path = resolve_path(args.walkforward_json)
        walkforward = load_json(walkforward_path)
    else:
        walkforward, walkforward_path = latest_walkforward(experiment_dir)
    reproducibility = reproducibility_for_walkforward(
        walkforward_path,
        resolve_path(args.reproducibility_audit),
    )

    status = "ok"
    errors: list[str] = []
    if not isinstance(audit, dict) or audit.get("status") == "failed":
        status = "degraded_missing_audit"
        errors.append(str((audit or {}).get("error") or "validation audit missing"))
        audit = {}
    if not isinstance(walkforward, dict):
        status = "degraded_missing_walkforward" if status == "ok" else "degraded_missing_audit_and_walkforward"
        errors.append("walk-forward evidence missing")
        walkforward = {}
    elif reproducibility.get("promotion_allowed") is not True:
        status = "blocked_nonreproducible_walkforward"
        errors.append(f"walk-forward reproducibility gate: {reproducibility.get('status')}")

    if status == "ok":
        queue, blocked = build_recovery_queue(audit, walkforward, args.max_candidates)
    elif status == "blocked_nonreproducible_walkforward":
        queue = []
        blocked = [
            {
                **candidate,
                "recovery_block": {
                    "matched_group": "walkforward_reproducibility",
                    "reason": reproducibility.get("status"),
                },
            }
            for candidate in extract_walkforward_candidates(walkforward)[: args.max_candidates]
        ]
    else:
        queue, blocked = [], []
    recovery = audit.get("validation_recovery_plan") or {}
    portfolio = audit.get("current_portfolio_metrics") or {}
    monthly_target = recovery.get("monthly_target") or {}
    output_status = status
    if status == "ok" and not queue:
        output_status = "no_eligible_recovery_candidates"
    return {
        "run_id": f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M')}-strategy-recovery-optimizer",
        "created_at": utc_now(),
        "source_skill": "active-alpha-paper-monitor",
        "optimizer_version": "strategy-recovery-optimizer-v2-reproducible-three-segment-only",
        "status": output_status,
        "live_orders_enabled": False,
        "private_api_keys_used": False,
        "allow_real_orders": False,
        "errors": errors,
        "evidence": {
            "audit_status": audit.get("status"),
            "walkforward_status": "ok" if walkforward else "missing",
            "walkforward_path": display_path(walkforward_path),
            "frames_loaded": walkforward.get("frames_loaded"),
            "stage_counts": walkforward.get("stage_counts") or {},
            "reproducibility": reproducibility,
        },
        "monthly_goal_state": {
            "initial_capital_usd": portfolio.get("initial_capital_usd"),
            "current_equity_usd": portfolio.get("equity_usd"),
            "target_equity_usd": monthly_target.get("target_equity_usd"),
            "target_gap_usd": monthly_target.get("target_gap_usd"),
            "required_return_pct_from_current_equity": monthly_target.get("required_return_pct_from_current_equity"),
            "recovery_status": recovery.get("status"),
            "new_sample_policy": recovery.get("new_sample_policy"),
        },
        "recovery_filter": {
            "blocked_statuses": ["retire_from_new_samples", "cooldown_until_retested"],
            "retired_entry_modes": [item.get("name") for item in (recovery.get("retired_entry_modes") or [])],
            "cooldown_entry_modes": [item.get("name") for item in (recovery.get("cooldown_entry_modes") or [])],
            "retired_strategy_families": [item.get("name") for item in (recovery.get("retired_strategy_families") or [])],
            "cooldown_strategy_families": [item.get("name") for item in (recovery.get("cooldown_strategy_families") or [])],
        },
        "eligible_retest_queue": queue,
        "blocked_candidates": blocked,
        "operator_note": "Optimizer is research-only. It never opens paper positions and never authorizes live trading.",
    }


def render_markdown(payload: dict[str, Any]) -> str:
    monthly = payload.get("monthly_goal_state") or {}
    lines = [
        f"# Strategy Recovery Optimizer | {payload.get('run_id')}",
        "",
        "Research-only recovery queue. No paper or live orders were placed.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Status | `{payload.get('status')}` |",
        f"| Live orders | `{payload.get('live_orders_enabled')}` |",
        f"| Current equity | `{monthly.get('current_equity_usd')}` |",
        f"| Target equity | `{monthly.get('target_equity_usd')}` |",
        f"| Target gap | `{monthly.get('target_gap_usd')}` |",
        f"| Required return | `{monthly.get('required_return_pct_from_current_equity')}` |",
        f"| Recovery policy | `{monthly.get('new_sample_policy')}` |",
        "",
        "## Eligible Retest Queue",
        "",
        "| Rank | Symbol | Interval | Family | Entry mode | Stage | Current signal | Score | OOS return | OOS win | OOS DD |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    queue = payload.get("eligible_retest_queue") or []
    if queue:
        for idx, item in enumerate(queue, 1):
            oos = item.get("oos_summary") or {}
            lines.append(
                f"| {idx} | `{item.get('symbol')}` | `{item.get('interval')}` | `{item.get('strategy_family')}` | "
                f"`{item.get('paper_entry_mode')}` | `{item.get('stage')}` | `{item.get('current_signal')}` | "
                f"`{item.get('score')}` | `{oos.get('net_return_pct')}` | `{oos.get('win_rate_pct')}` | `{oos.get('max_drawdown_pct')}` |"
            )
    else:
        lines.append("| - | none | - | - | - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Blocked Candidates",
            "",
            "| Symbol | Interval | Family | Entry mode | Score | Block | Reason |",
            "|---|---|---|---|---:|---|---|",
        ]
    )
    for item in (payload.get("blocked_candidates") or [])[:10]:
        block = item.get("recovery_block") or {}
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('interval')}` | `{item.get('strategy_family')}` | "
            f"`{item.get('paper_entry_mode')}` | `{item.get('score')}` | `{block.get('matched_group')}` | `{block.get('reason')}` |"
        )
    if not payload.get("blocked_candidates"):
        lines.append("| none | - | - | - | - | - | - |")
    lines.extend(["", "## Safety", "", "- `live_orders_enabled=false`", "- `private_api_keys_used=false`", "- `allow_real_orders=false`"])
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="strategy_recovery_optimizer_") as tmp_text:
        root = Path(tmp_text)
        audit_path = root / "validation-audit.json"
        walkforward_path = root / "weekly-goal.json"
        reproducibility_path = root / "reproducibility.json"
        audit_path.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "validation_recovery_plan": {
                        "status": "learning",
                        "new_sample_policy": "paper_only",
                        "monthly_target": {},
                    },
                    "current_portfolio_metrics": {},
                }
            ),
            encoding="utf-8",
        )
        candidate = {
            "symbol": "TESTUSDT",
            "interval": "1h",
            "best_oos": {
                "strategy": {"family": "MOMENTUM", "interval": "1h"},
                "stage": "paper_forward_candidate_current_signal",
                "current_signal": True,
                "selection_score": 18.5,
                "holdout_used_for_selection": False,
                "train_summary": {
                    "trade_count": 12,
                    "win_rate_pct": 58.0,
                    "final_capital": 530.0,
                    "net_return_pct": 6.0,
                    "max_drawdown_pct": -7.0,
                },
                "validation_summary": {
                    "trade_count": 6,
                    "win_rate_pct": 60.0,
                    "final_capital": 515.0,
                    "net_return_pct": 3.0,
                    "max_drawdown_pct": -5.0,
                },
                "trade_count": 10,
                "win_rate_pct": 60.0,
                "final_capital": 520.0,
                "net_return_pct": 4.0,
                "max_drawdown_pct": -6.0,
                "expectancy_pct": 0.4,
                "profit_factor": 1.2,
                "largest_winner_share_pct": 30.0,
            },
        }
        walkforward = {
            "frames_loaded": 1,
            "stage_counts": {"paper_forward_candidate_current_signal": 1},
            "top_ranked": [candidate],
        }
        walkforward_path.write_text(json.dumps(walkforward), encoding="utf-8")

        def write_reproducibility(promotion_allowed: bool) -> None:
            reproducibility_path.write_text(
                json.dumps(
                    {
                        "artifacts": [
                            {
                                "artifact": str(walkforward_path),
                                "reproducibility_status": "reproducible" if promotion_allowed else "raw_cache_missing",
                                "promotion_allowed": promotion_allowed,
                                "raw_cache_available": promotion_allowed,
                                "protocol_ok": promotion_allowed,
                                "execution_model_ok": promotion_allowed,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

        args = argparse.Namespace(
            audit_json=str(audit_path),
            walkforward_json=str(walkforward_path),
            reproducibility_audit=str(reproducibility_path),
            paper_dir=str(root / "paper"),
            experiment_dir=str(root),
            recommendation_ledger=str(root / "recommendations.json"),
            max_candidates=8,
            audit_timeout_seconds=10,
            format="json",
            self_test=False,
        )

        write_reproducibility(False)
        blocked = build_payload(args)
        assert blocked["status"] == "blocked_nonreproducible_walkforward", blocked
        assert not blocked["eligible_retest_queue"], blocked
        assert (blocked["blocked_candidates"][0]["recovery_block"] or {}).get("matched_group") == "walkforward_reproducibility", blocked

        write_reproducibility(True)
        allowed = build_payload(args)
        assert allowed["status"] == "ok", allowed
        assert len(allowed["eligible_retest_queue"]) == 1, allowed
        assert allowed["eligible_retest_queue"][0]["holdout_used_for_selection"] is False, allowed

        legacy = json.loads(json.dumps(walkforward))
        legacy_best = legacy["top_ranked"][0]["best_oos"]
        legacy_best.pop("validation_summary")
        legacy_best.pop("holdout_used_for_selection")
        walkforward_path.write_text(json.dumps(legacy), encoding="utf-8")
        legacy_blocked = build_payload(args)
        assert legacy_blocked["status"] == "no_eligible_recovery_candidates", legacy_blocked
        reasons = (legacy_blocked["blocked_candidates"][0]["recovery_block"] or {}).get("reason") or ""
        assert "final_holdout_selection_contract_missing" in reasons, legacy_blocked
        assert "validation_summary_missing" in reasons, legacy_blocked

    return {
        "status": "ok",
        "nonreproducible_walkforward_blocked": True,
        "reproducible_three_segment_candidate_allowed": True,
        "legacy_two_segment_candidate_blocked": True,
        "uses_temporary_files_only": True,
        "live_orders_enabled": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a paper-only recovery strategy queue")
    parser.add_argument("--audit-json", default="", help="Optional existing validation audit JSON path")
    parser.add_argument("--walkforward-json", default="", help="Optional existing weekly-goal/walk-forward evidence JSON path")
    parser.add_argument("--reproducibility-audit", default=str(DEFAULT_REPRO_AUDIT))
    parser.add_argument("--paper-dir", default=str(DEFAULT_PAPER_DIR))
    parser.add_argument("--experiment-dir", default=str(DEFAULT_EXPERIMENT_DIR))
    parser.add_argument("--recommendation-ledger", default=str(DEFAULT_RECOMMENDATION_LEDGER))
    parser.add_argument("--max-candidates", type=int, default=8)
    parser.add_argument("--audit-timeout-seconds", type=int, default=120)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    payload = build_payload(args)
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") in {
        "ok",
        "no_eligible_recovery_candidates",
        "blocked_nonreproducible_walkforward",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
