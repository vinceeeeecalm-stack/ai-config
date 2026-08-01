#!/usr/bin/env python3
"""Evaluate strategy promotion status from evidence.

This evaluator turns paper-trading evidence and recommendation outcome history
into promotion gates. It prevents the strategy library from being a static
table disconnected from whether the system has actually proven anything.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any
import datetime as dt


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECOMMENDATION_LEDGER = ROOT / "manual-investment-strategy-operator" / "recommendations" / "recommendation_history.json"
DEFAULT_PAPER_LEDGER = ROOT / "active-alpha-paper-monitor" / "paper_trades" / "paper_portfolio_ledger.json"
DEFAULT_WALKFORWARD_DIR = ROOT / "active-alpha-paper-monitor" / "experiments"
DEFAULT_VALIDATION_AUDITOR = ROOT / "active-alpha-paper-monitor" / "scripts" / "validation_sample_auditor.py"


DEFAULT_THRESHOLDS = {
    "min_closed_paper_trades": 20,
    "min_paper_win_rate_pct": 55.0,
    "min_paper_net_return_pct": 5.0,
    "max_paper_drawdown_pct": -15.0,
    "min_recommendation_outcome_reviews": 10,
    "min_calibration_resolved": 10,
}


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def recommendation_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "total_recommendations": 0,
            "outcome_reviews": 0,
            "resolved_count": 0,
            "hit_count": 0,
            "failed_count": 0,
            "hit_rate_pct": None,
        }
    ledger = load_json(path)
    recommendations = ledger.get("recommendations") or []
    hit = sum(1 for item in recommendations if item.get("outcome_status") == "hit")
    failed = sum(1 for item in recommendations if item.get("outcome_status") == "failed")
    resolved = hit + failed
    return {
        "status": "ok",
        "total_recommendations": len(recommendations),
        "outcome_reviews": len(ledger.get("outcome_reviews") or []),
        "resolved_count": resolved,
        "hit_count": hit,
        "failed_count": failed,
        "hit_rate_pct": (hit / resolved * 100.0) if resolved else None,
    }


def paper_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "initial_capital_usd": None,
            "net_return_pct": None,
            "max_drawdown_pct": None,
            "open_count": 0,
            "closed_count": 0,
            "win_rate_pct": None,
            "live_orders_enabled": None,
        }
    ledger = load_json(path)
    closed = ledger.get("closed_trades") or []
    open_positions = ledger.get("open_positions") or []
    hit = sum(1 for trade in closed if trade.get("outcome") in {"hit", "take_profit", "profit"})
    failed = sum(1 for trade in closed if trade.get("outcome") in {"failed", "stop", "expired", "invalidated"})
    resolved = hit + failed
    return {
        "status": "ok",
        "initial_capital_usd": ledger.get("initial_capital_usd"),
        "cash_usd": ledger.get("cash_usd"),
        "open_value_usd": ledger.get("open_value_usd"),
        "equity_usd": ledger.get("equity_usd"),
        "net_return_pct": ledger.get("net_return_pct"),
        "max_drawdown_pct": ledger.get("max_drawdown_pct"),
        "open_count": len(open_positions),
        "closed_count": len(closed),
        "resolved_count": resolved,
        "hit_count": hit,
        "failed_count": failed,
        "win_rate_pct": (hit / resolved * 100.0) if resolved else None,
        "live_orders_enabled": ledger.get("live_orders_enabled"),
        "updated_at": ledger.get("updated_at"),
    }


def latest_walkforward_evidence(path: Path | None) -> dict[str, Any]:
    if path and path.exists() and path.is_file():
        return walkforward_metrics(path)
    directory = path if path and path.exists() and path.is_dir() else DEFAULT_WALKFORWARD_DIR
    candidates = sorted(directory.glob("*weekly-goal*.json")) + sorted(directory.glob("*walkforward*.json"))
    candidates = [item for item in candidates if item.is_file() and looks_like_walkforward_evidence(item)]
    if not candidates:
        return {
            "status": "missing",
            "evidence_path": None,
            "frames_loaded": 0,
            "stage_counts": {},
            "target_research_pass_count": 0,
            "paper_only_count": 0,
            "best_symbol": None,
            "best_stage": None,
            "best_oos_trade_count": 0,
            "best_oos_win_rate_pct": None,
            "best_oos_net_return_pct": None,
            "best_oos_max_drawdown_pct": None,
        }
    return walkforward_metrics(max(candidates, key=lambda item: item.stat().st_mtime))


def looks_like_walkforward_evidence(path: Path) -> bool:
    try:
        payload = load_json(path)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("audit_version") or payload.get("walkforward_sample_metrics"):
        return False
    if "frames_loaded" not in payload:
        return False
    return bool(payload.get("top_ranked") or payload.get("results") or payload.get("stage_counts"))


def validation_sample_audit(thresholds: dict[str, Any]) -> dict[str, Any]:
    if not DEFAULT_VALIDATION_AUDITOR.exists():
        return {
            "status": "missing",
            "error": f"validation auditor not found: {DEFAULT_VALIDATION_AUDITOR}",
            "validation_sample_plan": {
                "status": "insufficient_evidence",
                "max_allowed_action": "paper_only",
                "failed_gates": ["validation_sample_auditor_missing"],
                "sample_gaps": {},
                "next_validation_queue": [],
            },
        }
    try:
        spec = importlib.util.spec_from_file_location("validation_sample_auditor", DEFAULT_VALIDATION_AUDITOR)
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to load validation sample auditor module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.build_audit(ROOT, thresholds=thresholds)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "error": str(exc),
            "validation_sample_plan": {
                "status": "insufficient_evidence",
                "max_allowed_action": "paper_only",
                "failed_gates": ["validation_sample_auditor_failed"],
                "sample_gaps": {},
                "next_validation_queue": [],
            },
        }


def walkforward_metrics(path: Path) -> dict[str, Any]:
    try:
        payload = load_json(path)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "evidence_path": str(path),
            "error": str(exc),
            "frames_loaded": 0,
            "stage_counts": {},
            "target_research_pass_count": 0,
            "paper_only_count": 0,
            "best_symbol": None,
            "best_stage": None,
            "best_oos_trade_count": 0,
            "best_oos_win_rate_pct": None,
            "best_oos_net_return_pct": None,
            "best_oos_max_drawdown_pct": None,
        }
    ranked = payload.get("top_ranked") or []
    best = ranked[0] if ranked else {}
    best_oos = best.get("best_oos") or {}
    stage_counts = payload.get("stage_counts") or {}
    target_pass_count = sum(
        int(count or 0)
        for stage, count in stage_counts.items()
        if str(stage).startswith("target_research_pass")
    )
    paper_only_count = sum(
        int(count or 0)
        for stage, count in stage_counts.items()
        if "paper" in str(stage)
    )
    return {
        "status": "ok",
        "evidence_path": str(path),
        "generated_at": payload.get("generated_at"),
        "frames_loaded": payload.get("frames_loaded", 0),
        "stage_counts": stage_counts,
        "target_research_pass_count": target_pass_count,
        "paper_only_count": paper_only_count,
        "best_symbol": best.get("symbol"),
        "best_interval": best.get("interval"),
        "best_stage": best_oos.get("stage"),
        "best_oos_trade_count": best_oos.get("trade_count"),
        "best_oos_win_rate_pct": best_oos.get("win_rate_pct"),
        "best_oos_final_capital": best_oos.get("final_capital"),
        "best_oos_net_return_pct": best_oos.get("net_return_pct"),
        "best_oos_max_drawdown_pct": best_oos.get("max_drawdown_pct"),
        "best_oos_weekly_double_count": best_oos.get("weekly_double_trade_count"),
        "top_candidates": [
            {
                "symbol": item.get("symbol"),
                "interval": item.get("interval"),
                "stage": (item.get("best_oos") or {}).get("stage"),
                "trade_count": (item.get("best_oos") or {}).get("trade_count"),
                "win_rate_pct": (item.get("best_oos") or {}).get("win_rate_pct"),
                "net_return_pct": (item.get("best_oos") or {}).get("net_return_pct"),
                "max_drawdown_pct": (item.get("best_oos") or {}).get("max_drawdown_pct"),
                "weekly_double_trade_count": (item.get("best_oos") or {}).get("weekly_double_trade_count"),
            }
            for item in ranked[:5]
        ],
    }


def gate_passed(metrics: dict[str, Any], rec: dict[str, Any], thresholds: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if int(metrics.get("closed_count") or 0) < int(thresholds["min_closed_paper_trades"]):
        failures.append("paper_closed_sample_too_small")
    if (as_float(metrics.get("win_rate_pct"), -1.0) or -1.0) < float(thresholds["min_paper_win_rate_pct"]):
        failures.append("paper_win_rate_below_gate")
    if (as_float(metrics.get("net_return_pct"), -999.0) or -999.0) <= float(thresholds["min_paper_net_return_pct"]):
        failures.append("paper_net_return_below_gate")
    if (as_float(metrics.get("max_drawdown_pct"), -999.0) or -999.0) < float(thresholds["max_paper_drawdown_pct"]):
        failures.append("paper_drawdown_too_deep")
    if int(rec.get("outcome_reviews") or 0) < int(thresholds["min_recommendation_outcome_reviews"]):
        failures.append("recommendation_outcome_sample_too_small")
    if int(rec.get("resolved_count") or 0) < int(thresholds["min_calibration_resolved"]):
        failures.append("probability_calibration_sample_too_small")
    return not failures, failures


def build_panel(
    recommendation_ledger: Path,
    paper_ledger: Path,
    walkforward_evidence: Path | None,
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    rec = recommendation_metrics(recommendation_ledger)
    paper = paper_metrics(paper_ledger)
    walkforward = latest_walkforward_evidence(walkforward_evidence)
    validation_audit = validation_sample_audit(thresholds)
    audit_paper = (validation_audit.get("paper_sample_metrics") or {}) if isinstance(validation_audit, dict) else {}
    paper_for_gate = dict(paper)
    if audit_paper.get("status") == "ok":
        for key in ("closed_count", "resolved_count", "hit_count", "failed_count", "win_rate_pct"):
            paper_for_gate[key] = audit_paper.get(key)
        paper_for_gate["validation_sample_source"] = "deduped_paper_trades_scan"
    tactical_passed, tactical_failures = gate_passed(paper_for_gate, rec, thresholds)
    validation_plan = validation_audit.get("validation_sample_plan") or {}
    for gate in validation_plan.get("failed_gates") or []:
        if gate not in tactical_failures:
            tactical_failures.append(gate)
    tactical_passed = tactical_passed and not validation_plan.get("failed_gates")
    walkforward_failures: list[str] = []
    if walkforward.get("status") != "ok":
        walkforward_failures.append("walkforward_evidence_missing")
    if int(walkforward.get("frames_loaded") or 0) < 10:
        walkforward_failures.append("walkforward_frame_sample_too_small")
    if int(walkforward.get("target_research_pass_count") or 0) <= 0:
        walkforward_failures.append("walkforward_no_target_research_pass")
    if int(walkforward.get("paper_only_count") or 0) <= 0:
        walkforward_failures.append("walkforward_no_paper_only_candidates")
    tactical_status = "paper_validated" if tactical_passed else "paper_only"
    tactical_max_action = "conditional_action" if tactical_passed else "paper_only"
    if tactical_passed and int(rec.get("resolved_count") or 0) >= int(thresholds["min_calibration_resolved"]):
        tactical_max_action = "conditional_action"

    strategies = [
        {
            "strategy_id": "goal_weighted_dca",
            "promotion_status": "human_confirmed_live_candidate",
            "max_allowed_action": "conditional_action",
            "live_execute_allowed": False,
            "api_ready_candidate": False,
            "manual_confirmation_required": True,
            "evidence_status": "structural_goal_and_dca_gate",
            "failed_gates": [],
            "reason": "Long-term DCA is allowed as conditional manual action when data quality and cash rail are verified; it is not a short-term alpha claim.",
        },
        {
            "strategy_id": "staking_compound_satellite",
            "promotion_status": "human_confirmed_live_candidate",
            "max_allowed_action": "conditional_action",
            "live_execute_allowed": False,
            "api_ready_candidate": False,
            "manual_confirmation_required": True,
            "evidence_status": "structural_staking_gate",
            "failed_gates": [],
            "reason": "Staking compounding can inform DCA sizing but does not validate short-term execute_now.",
        },
        {
            "strategy_id": "core_satellite_barbell",
            "promotion_status": "research_only_for_new_targets",
            "max_allowed_action": "hold_or_conditional_action",
            "live_execute_allowed": False,
            "api_ready_candidate": False,
            "manual_confirmation_required": True,
            "evidence_status": "portfolio_goal_construction_gate",
            "failed_gates": [],
            "reason": "Core/protected holdings can be held or conditionally sized, but new targets require fresh goal, data-quality, and research-committee evidence.",
        },
        {
            "strategy_id": "convex_tail_sleeve",
            "promotion_status": "research_only",
            "max_allowed_action": "watch",
            "live_execute_allowed": False,
            "api_ready_candidate": False,
            "manual_confirmation_required": True,
            "evidence_status": "insufficient_float_unlock_liquidity_evidence",
            "failed_gates": ["tail_asset_fundamental_data_incomplete"],
            "reason": "High-convexity tail assets need verified float, unlock, liquidity and official thesis before add size.",
        },
        {
            "strategy_id": "dynamic_reserve_timing",
            "promotion_status": "human_confirmed_live_candidate",
            "max_allowed_action": "conditional_action",
            "live_execute_allowed": False,
            "api_ready_candidate": False,
            "manual_confirmation_required": True,
            "evidence_status": "cash_rail_and_trigger_gate",
            "failed_gates": [],
            "reason": "Cash or stablecoin reserves can be deployed only through explicit trigger prices, cash-rail confirmation, and human approval.",
        },
        {
            "strategy_id": "trend_rotation_relay",
            "promotion_status": tactical_status,
            "max_allowed_action": tactical_max_action,
            "live_execute_allowed": False,
            "api_ready_candidate": False,
            "manual_confirmation_required": True,
            "evidence_status": "paper_gate_passed" if tactical_passed else "paper_gate_failed",
            "failed_gates": tactical_failures,
            "reason": "US tactical rotation must be proven by paper sample, drawdown, valid calibration, positive conservative EV, RR>=2, realtime signal and account-risk caps before live escalation.",
        },
        {
            "strategy_id": "event_news_alpha",
            "promotion_status": "paper_only" if tactical_passed else "research_only",
            "max_allowed_action": "paper_only" if tactical_passed else "watch",
            "live_execute_allowed": False,
            "api_ready_candidate": False,
            "manual_confirmation_required": True,
            "evidence_status": "requires_cross_source_and_price_volume_confirmation",
            "failed_gates": tactical_failures[:],
            "reason": "News/social alpha cannot trigger real trades without price/volume confirmation and paper evidence.",
        },
        {
            "strategy_id": "walk_forward_paper_validation",
            "promotion_status": "research_required" if not tactical_passed else "paper_validated",
            "max_allowed_action": "paper_only" if not tactical_passed else "conditional_action",
            "live_execute_allowed": False,
            "api_ready_candidate": False,
            "manual_confirmation_required": True,
            "evidence_status": "walkforward_research_present" if not walkforward_failures else "walkforward_research_insufficient",
            "failed_gates": sorted(set(tactical_failures + walkforward_failures)),
            "reason": "All short-term strategies pass through walk-forward, paper sample, drawdown, and calibration gates before manual live candidates.",
        },
    ]
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "status": "ok",
        "thresholds": thresholds,
        "recommendation_metrics": rec,
        "paper_metrics": paper,
        "validation_sample_audit": validation_audit,
        "walkforward_metrics": walkforward,
        "strategy_evidence_status": "paper_validated_candidate" if tactical_passed else "insufficient_evidence",
        "tactical_promotion_gate_passed": tactical_passed,
        "tactical_failed_gates": tactical_failures,
        "strategies": strategies,
        "max_real_action_from_evidence": "conditional_action" if tactical_passed else "paper_only",
        "max_non_api_manual_action": "conditional_action" if tactical_passed else "paper_only",
        "live_execute_allowed": False,
        "api_ready_candidate": False,
        "manual_confirmation_required": True,
        "live_execution_note": "No strategy evidence panel authorizes automatic or API live trading; all real-money actions require manual confirmation and separate readiness gates.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate strategy promotion gates")
    parser.add_argument("--recommendation-ledger", default=str(DEFAULT_RECOMMENDATION_LEDGER))
    parser.add_argument("--paper-ledger", default=str(DEFAULT_PAPER_LEDGER))
    parser.add_argument("--walkforward-evidence", default="", help="Optional weekly-goal/walk-forward evidence JSON or directory")
    parser.add_argument("--min-closed-paper-trades", type=int, default=DEFAULT_THRESHOLDS["min_closed_paper_trades"])
    parser.add_argument("--min-paper-win-rate-pct", type=float, default=DEFAULT_THRESHOLDS["min_paper_win_rate_pct"])
    parser.add_argument("--min-paper-net-return-pct", type=float, default=DEFAULT_THRESHOLDS["min_paper_net_return_pct"])
    parser.add_argument("--max-paper-drawdown-pct", type=float, default=DEFAULT_THRESHOLDS["max_paper_drawdown_pct"])
    parser.add_argument("--min-recommendation-outcome-reviews", type=int, default=DEFAULT_THRESHOLDS["min_recommendation_outcome_reviews"])
    parser.add_argument("--min-calibration-resolved", type=int, default=DEFAULT_THRESHOLDS["min_calibration_resolved"])
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    thresholds = {
        "min_closed_paper_trades": args.min_closed_paper_trades,
        "min_paper_win_rate_pct": args.min_paper_win_rate_pct,
        "min_paper_net_return_pct": args.min_paper_net_return_pct,
        "max_paper_drawdown_pct": args.max_paper_drawdown_pct,
        "min_recommendation_outcome_reviews": args.min_recommendation_outcome_reviews,
        "min_calibration_resolved": args.min_calibration_resolved,
    }
    walkforward_path = Path(args.walkforward_evidence) if args.walkforward_evidence else None
    panel = build_panel(Path(args.recommendation_ledger), Path(args.paper_ledger), walkforward_path, thresholds)
    if args.format == "json":
        print(json.dumps(panel, ensure_ascii=False, indent=2))
    else:
        print("# Strategy Promotion Evidence")
        print()
        print(f"- tactical_promotion_gate_passed: `{panel['tactical_promotion_gate_passed']}`")
        print(f"- max_real_action_from_evidence: `{panel['max_real_action_from_evidence']}`")
        print(f"- tactical_failed_gates: `{panel['tactical_failed_gates']}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
