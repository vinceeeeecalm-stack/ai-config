#!/usr/bin/env python3
"""Append daily PASS/candidate observations and proven resolutions to a forward ledger."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DAILY = ROOT / "experiments/current-daily-priority-cycle.json"
DEFAULT_LEDGER = ROOT / "data/daily_candidate_observation_ledger.json"
PROTECTED = [ROOT / "data/paper_ledger.json", ROOT / "experiments/current-crypto-barrier-estimates.json"]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("daily_candidate_core", ROOT / "scripts/polymarket_alpha.py")
public = load("daily_candidate_public", ROOT / "scripts/polymarket_public_data.py")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def new_ledger() -> dict[str, Any]:
    return {"schema_version": "polymarket-daily-candidate-observation-ledger-v1", "created_at": core.now_iso(),
            "updated_at": core.now_iso(), "events": [], "append_only": True, "paper_only": True,
            "counts_as_paper_trade": False, "live_orders_enabled": False, "private_api_used": False}


def assert_safe(ledger: dict[str, Any]) -> None:
    if (ledger.get("append_only") is not True or ledger.get("paper_only") is not True
            or ledger.get("counts_as_paper_trade") is not False
            or ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False):
        raise ValueError("unsafe daily candidate observation ledger")
    ids = [row.get("event_id") for row in ledger.get("events", [])]
    if None in ids or len(ids) != len(set(ids)): raise ValueError("duplicate or missing append-only event id")


def coverage_by_family(daily: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["market_family"]): row for row in daily.get("model_coverage_queue", [])}


def append_scan_events(ledger: dict[str, Any], daily: dict[str, Any], artifact_sha: str) -> tuple[int, int]:
    existing = {row["event_id"] for row in ledger["events"]}; added = deduped = 0
    coverage = coverage_by_family(daily)
    for row in daily.get("market_watchlist", []):
        event_id = core.stable_id("pm-daily-candidate-scan", daily.get("cycle_id"), row.get("market_id"))
        if event_id in existing: deduped += 1; continue
        family = str(row.get("market_family") or "unknown")
        ledger["events"].append({
            "event_id": event_id, "event_type": "scan_observation", "recorded_at": daily.get("created_at"),
            "daily_cycle_id": daily.get("cycle_id"), "source_daily_artifact_sha256": artifact_sha,
            "market_id": str(row.get("market_id") or ""), "condition_id": str(row.get("condition_id") or ""),
            "question": row.get("question"), "domain": row.get("domain"), "market_family": family,
            "end_date": row.get("end_date"), "action": row.get("action"),
            "yes_fill_price": row.get("yes_fill_price"), "no_fill_price": row.get("no_fill_price"),
            "yes_spread": row.get("yes_spread"), "no_spread": row.get("no_spread"),
            "yes_price_impact": row.get("yes_price_impact"), "no_price_impact": row.get("no_price_impact"),
            "yes_midpoint": row.get("yes_midpoint"), "no_midpoint": row.get("no_midpoint"),
            "normalized_yes_midpoint": row.get("normalized_yes_midpoint"),
            "market_consensus_side": row.get("market_consensus_side"),
            "market_consensus_probability_proxy": row.get("market_consensus_probability_proxy"),
            "market_consensus_is_model_probability": False,
            "model_version": row.get("model_version"),
            "model_yes_probability": row.get("model_yes_probability"),
            "model_yes_confidence_low": row.get("model_yes_confidence_low"),
            "model_yes_confidence_high": row.get("model_yes_confidence_high"),
            "model_calibration_samples": row.get("model_calibration_samples"),
            "model_historical_hit_rate": row.get("model_historical_hit_rate"),
            "model_brier_score_oos": row.get("model_brier_score_oos"),
            "market_brier_score_oos": row.get("market_brier_score_oos"),
            "model_log_loss_oos": row.get("model_log_loss_oos"),
            "market_log_loss_oos": row.get("market_log_loss_oos"),
            "model_source_counts": row.get("model_source_counts"),
            "model_rules_review": row.get("model_rules_review"),
            "estimated_fee_per_share": row.get("estimated_fee_per_share"),
            "estimated_slippage_per_share": row.get("estimated_slippage_per_share"),
            "recommendation_type": row.get("recommendation_type"),
            "net_ev_per_share": row.get("net_ev_per_share"),
            "rule_failure_thresholds": row.get("rule_failure_thresholds") or row.get("failed_gates", []),
            "source_requirements": row.get("model_source_counts"),
            "approved_model_probability_present": bool(row.get("approved_model_probability_present")),
            "depth_test_notional_usd": row.get("planned_notional_usd"), "failed_gates": row.get("failed_gates", []),
            "route_blocker": row.get("route_blocker"), "model_coverage": coverage.get(family),
            "resolution_status_at_scan": "pending", "counts_as_paper_trade": False,
        })
        existing.add(event_id); added += 1
    return added, deduped


def unresolved_due_conditions(ledger: dict[str, Any], now: datetime, limit: int = 100) -> list[str]:
    resolved = {row.get("condition_id") for row in ledger["events"] if row.get("event_type") == "resolution_observation"}
    conditions = []
    for row in ledger["events"]:
        if row.get("event_type") != "scan_observation" or not row.get("condition_id") or row.get("condition_id") in resolved: continue
        end = core.parse_iso(row.get("end_date"))
        if end and end <= now and row["condition_id"] not in conditions: conditions.append(row["condition_id"])
    return conditions[:limit]


def append_resolution_events(ledger: dict[str, Any], details: dict[str, dict[str, Any]], recorded_at: str) -> int:
    existing = {row["event_id"] for row in ledger["events"]}; added = 0
    scans: dict[str, list[dict[str, Any]]] = {}
    for row in ledger["events"]:
        if row.get("event_type") == "scan_observation": scans.setdefault(str(row.get("condition_id") or ""), []).append(row)
    for condition, market in details.items():
        resolution = public.infer_resolution(market)
        winner = resolution.get("winning_outcome")
        if winner is None: continue
        event_id = core.stable_id("pm-daily-candidate-resolution", condition, winner)
        if event_id in existing: continue
        related = scans.get(condition, [])
        outcome_yes = 1.0 if str(winner).lower() == "yes" else 0.0 if str(winner).lower() == "no" else None
        benchmark_scores = []
        model_scores = []
        for scan in related:
            probability = scan.get("normalized_yes_midpoint")
            if probability is None or outcome_yes is None: continue
            probability = min(.999999, max(.000001, float(probability)))
            benchmark_scores.append({
                "scan_event_id": scan["event_id"], "observed_at": scan.get("recorded_at"),
                "normalized_yes_midpoint": probability,
                "market_consensus_side": scan.get("market_consensus_side"),
                "market_consensus_hit": scan.get("market_consensus_side") == str(winner).upper(),
                "market_baseline_brier": (probability - outcome_yes) ** 2,
                "market_baseline_log_loss": -(outcome_yes * math.log(probability) + (1 - outcome_yes) * math.log(1 - probability)),
            })
            model_probability = scan.get("model_yes_probability")
            if scan.get("approved_model_probability_present") is True and scan.get("model_version") and model_probability is not None:
                model_probability = min(.999999, max(.000001, float(model_probability)))
                model_brier = (model_probability - outcome_yes) ** 2
                model_log_loss = -(outcome_yes * math.log(model_probability) + (1 - outcome_yes) * math.log(1 - model_probability))
                model_scores.append({
                    "scan_event_id": scan["event_id"], "observed_at": scan.get("recorded_at"),
                    "model_version": scan.get("model_version"), "model_yes_probability": model_probability,
                    "model_brier": model_brier, "model_log_loss": model_log_loss,
                    "market_baseline_brier": benchmark_scores[-1]["market_baseline_brier"],
                    "market_baseline_log_loss": benchmark_scores[-1]["market_baseline_log_loss"],
                    "brier_improvement_vs_market": benchmark_scores[-1]["market_baseline_brier"] - model_brier,
                    "log_loss_improvement_vs_market": benchmark_scores[-1]["market_baseline_log_loss"] - model_log_loss,
                })
        ledger["events"].append({"event_id": event_id, "event_type": "resolution_observation", "recorded_at": recorded_at,
                                 "condition_id": condition, "market_id": str(market.get("id") or ""),
                                 "question": market.get("question") or (related[-1].get("question") if related else None),
                                 "winning_outcome": winner, "resolution_proof_status": resolution.get("status"),
                                 "winning_token_id": resolution.get("winning_token_id"),
                                 "terminal_outcome_prices": public.parse_jsonish(market.get("outcomePrices")),
                                 "related_scan_event_ids": [row["event_id"] for row in related],
                                 "market_baseline_forward_scores": benchmark_scores,
                                 "approved_model_forward_scores": model_scores,
                                 "recommendation_or_no_bet_counterfactuals": [{"scan_event_id": row["event_id"], "action": row.get("action"), "recommendation_type": row.get("recommendation_type"), "winning_outcome": winner} for row in related],
                                 "counts_as_paper_trade": False})
        existing.add(event_id); added += 1
    return added


def run(daily_path: Path, ledger_path: Path) -> dict[str, Any]:
    protected_before = {str(path.relative_to(ROOT)): sha(path) for path in PROTECTED}
    daily = json.loads(daily_path.read_text()); artifact_sha = sha(daily_path)
    if daily.get("paper_only") is not True or daily.get("live_orders_enabled") is not False or daily.get("private_api_used") is not False:
        raise ValueError("unsafe daily priority source")
    if daily.get("status") != "ok" or daily.get("snapshot_contract_failures"):
        raise ValueError("daily priority source is blocked, degraded, or not based on a fresh complete snapshot")
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else new_ledger(); assert_safe(ledger)
    scan_added, scan_deduped = append_scan_events(ledger, daily, artifact_sha)
    now = datetime.now(timezone.utc); due = unresolved_due_conditions(ledger, now)
    details, requests = public.fetch_gamma_details_by_condition(due) if due else ({}, [])
    resolution_added = append_resolution_events(ledger, details, core.now_iso())
    ledger["updated_at"] = core.now_iso(); assert_safe(ledger); core.write_json(ledger_path, ledger)
    scans = [row for row in ledger["events"] if row["event_type"] == "scan_observation"]
    resolutions = [row for row in ledger["events"] if row["event_type"] == "resolution_observation"]
    unresolved = len({row["condition_id"] for row in scans} - {row["condition_id"] for row in resolutions})
    protected_after = {str(path.relative_to(ROOT)): sha(path) for path in PROTECTED}
    if protected_before != protected_after: raise RuntimeError("candidate observation cycle mutated protected artifacts")
    return {"schema_version": "polymarket-daily-candidate-observation-cycle-v1", "created_at": core.now_iso(), "status": "ok",
            "source_daily_cycle_id": daily.get("cycle_id"), "scan_events_added": scan_added, "scan_events_deduped": scan_deduped,
            "due_resolution_conditions_polled": len(due), "resolution_events_added": resolution_added,
            "resolution_request_count": len(requests), "total_scan_events": len(scans), "total_resolution_events": len(resolutions),
            "unique_observed_markets": len({row["condition_id"] for row in scans}), "unresolved_observed_markets": unresolved,
            "protected_artifacts_unchanged": True, "paper_only": True, "counts_as_paper_trade": False,
            "live_orders_enabled": False, "private_api_used": False}


def markdown(payload: dict[str, Any]) -> str:
    return "\n".join(["# Daily Candidate Forward Observation Ledger", "", f"- Status: `{payload['status']}`",
                      f"- Scan events added / deduped: {payload['scan_events_added']} / {payload['scan_events_deduped']}",
                      f"- Resolution events added: {payload['resolution_events_added']}",
                      f"- Total scan / resolution events: {payload['total_scan_events']} / {payload['total_resolution_events']}",
                      f"- Unique / unresolved observed markets: {payload['unique_observed_markets']} / {payload['unresolved_observed_markets']}",
                      "", "These rows are forward observations and counterfactual market evidence. They are not paper trades and never count toward the 100-trade acceptance gate.", ""])


def self_test() -> dict[str, Any]:
    ledger = new_ledger(); daily = {"cycle_id": "c1", "created_at": "2026-07-12T00:00:00+00:00",
        "market_watchlist": [{"market_id": "m", "condition_id": "c", "question": "Q", "market_family": "x", "end_date": "2026-07-12T01:00:00+00:00", "action": "PASS_NO_TRADE",
                              "normalized_yes_midpoint": .7, "market_consensus_side": "YES",
                              "market_consensus_probability_proxy": .7, "model_version": "model-v1",
                              "model_yes_probability": .8, "approved_model_probability_present": True}], "model_coverage_queue": []}
    assert append_scan_events(ledger, daily, "a" * 64) == (1, 0)
    assert append_scan_events(ledger, daily, "a" * 64) == (0, 1)
    market = {"id": "m", "conditionId": "c", "question": "Q", "closed": True, "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]', "clobTokenIds": '["y","n"]'}
    assert append_resolution_events(ledger, {"c": market}, "2026-07-12T02:00:00+00:00") == 1
    resolution = next(row for row in ledger["events"] if row["event_type"] == "resolution_observation")
    assert abs(resolution["market_baseline_forward_scores"][0]["market_baseline_brier"] - .09) < 1e-12
    assert resolution["market_baseline_forward_scores"][0]["market_consensus_hit"] is True
    assert abs(resolution["approved_model_forward_scores"][0]["model_brier"] - .04) < 1e-12
    assert abs(resolution["approved_model_forward_scores"][0]["brier_improvement_vs_market"] - .05) < 1e-12
    assert append_resolution_events(ledger, {"c": market}, "2026-07-12T02:01:00+00:00") == 0
    assert_safe(ledger)
    return {"status": "pass", "tests": ["append_scan", "scan_dedupe", "append_resolution", "resolution_dedupe", "safe_flags"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--daily", default=str(DEFAULT_DAILY)); parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-daily-candidate-observation-cycle.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_DAILY_CANDIDATE_OBSERVATIONS.md")); args = parser.parse_args()
    payload = self_test() if args.self_test else run(Path(args.daily), Path(args.ledger))
    if not args.self_test:
        core.write_json(args.output, payload); Path(args.report).write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0 if payload.get("status") in {"ok", "pass"} else 1


if __name__ == "__main__": raise SystemExit(main())
