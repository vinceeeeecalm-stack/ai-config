#!/usr/bin/env python3
"""Append-only operational Back Cases for irrecoverably missed forward windows."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "data/forward_capture_backcase_ledger.json"
DOMAIN_LEDGERS = {
    "weather": ROOT / "data/weather_research_forecast_ledger.json",
    "football": ROOT / "data/football_research_forecast_ledger.json",
    "social_count": ROOT / "data/social_count_research_forecast_ledger.json",
    "stock_weekly": ROOT / "data/stock_weekly_research_forecast_ledger.json",
    "crypto_barrier": ROOT / "data/research_forecast_ledger.json",
}
PROTECTED = [ROOT / "data/paper_ledger.json", ROOT / "experiments/current-crypto-barrier-estimates.json"]
BACKCASE_ALGORITHM_VERSION = "polymarket-capture-backcase-v2"
NON_ATTEMPT_REASONS = {"fixed_cutoff_not_reached", "capture_window_not_reached",
                       "forecast_window_missed", "capture_window_missed"}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("capture_backcase_core", ROOT / "scripts/polymarket_alpha.py")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def miss_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    domain = str(row.get("domain") or "")
    identity = str(row.get("event_id") or row.get("tracking_id") or row.get("condition_id") or row.get("forecast_id") or "")
    window_end = str(row.get("cutoff_at") or row.get("capture_window_end_at") or row.get("kickoff_at") or "")
    return domain, identity, window_end


def matching_attempts(miss: dict[str, Any], ledger: dict[str, Any]) -> list[dict[str, Any]]:
    _, identity, window_end = miss_identity(miss); rows = []
    start = core.parse_iso(miss.get("capture_window_start_at") or miss.get("cutoff_at"))
    end = core.parse_iso(miss.get("capture_window_end_at") or miss.get("kickoff_at") or miss.get("cutoff_at"))
    for event in ledger.get("events", []):
        attempted_at = core.parse_iso(event.get("at"))
        if start is not None and end is not None and (attempted_at is None or attempted_at < start or attempted_at >= end):
            continue
        for excluded in event.get("excluded", []):
            _, attempt_identity, attempt_end = miss_identity({"domain": miss.get("domain"), **excluded})
            if attempt_identity != identity:
                continue
            if window_end and attempt_end and attempt_end != window_end:
                continue
            rows.append({"attempted_at": event.get("at"), **excluded})
    return rows


def classify(attempts: list[dict[str, Any]]) -> tuple[str, str, bool]:
    reasons = [str(row.get("reason") or "") for row in attempts if row.get("reason") not in NON_ATTEMPT_REASONS]
    if "two_sided_live_book_missing" in reasons:
        return "market_liquidity_or_book_completeness", "pass_without_forecast", False
    if "market_ladder_probability_sum_invalid" in reasons or "bucket_ladder_unparsed" in reasons:
        return "market_data_or_rule_integrity", "pass_without_forecast", False
    if not reasons or all(reason in {"forecast_window_missed", "capture_window_missed"} for reason in reasons):
        return "scheduler_or_capture_coverage", "pass_and_investigate_automation", False
    return "other_capture_failure", "pass_without_forecast", False


def build_backcase(miss: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    domain, identity, window_end = miss_identity(miss)
    backcase_id = core.stable_id("pm-capture-backcase", BACKCASE_ALGORITHM_VERSION, domain, identity, window_end)
    root_cause, correct_action, parameter_change_eligible = classify(attempts)
    actionable = [row for row in attempts if row.get("reason") not in NON_ATTEMPT_REASONS]
    reasons = Counter(str(row.get("reason")) for row in actionable)
    missing_books = []
    for row in actionable:
        missing_books.extend(row.get("missing_live_books") or [])
    unique_missing = list({(str(row.get("market_id")), str(row.get("bucket"))): row for row in missing_books}.values())
    return {
        "backcase_id": backcase_id, "algorithm_version": BACKCASE_ALGORITHM_VERSION,
        "created_at": core.now_iso(), "status": "reviewed_operational_failure",
        "domain": domain, "identity": identity, "window_end_at": window_end,
        "miss_reason": miss.get("reason"), "forecast_created": False,
        "predicted_probability": None, "market_price_at_forecast": None,
        "attempt_count": len(actionable),
        "first_attempt_at": actionable[0].get("attempted_at") if actionable else None,
        "last_attempt_at": actionable[-1].get("attempted_at") if actionable else None,
        "attempt_reason_counts": dict(reasons), "missing_live_books": unique_missing,
        "data_complete_timely_accurate": False, "key_assumption_failed": root_cause,
        "market_reflected_information_earlier": None,
        "correct_action_with_available_data": correct_action,
        "new_downgrade_condition": "retain complete-data gate; never normalize or backfill an incomplete ladder",
        "historical_replay_required": False, "oos_before_after_difference": None,
        "defect_classification": root_cause,
        "automatic_parameter_change_eligible": parameter_change_eligible,
        "minimum_similar_failures_before_model_change": 3,
        "model_parameter_changed": False, "paper_estimates_emitted": False,
        "main_paper_ledger_mutated": False, "paper_only": True,
        "live_orders_enabled": False, "private_api_used": False,
    }


def new_ledger() -> dict[str, Any]:
    return {"schema_version": "polymarket-forward-capture-backcase-ledger-v1", "created_at": core.now_iso(),
            "updated_at": core.now_iso(), "backcases": [], "paper_estimates_emitted": False,
            "main_paper_ledger_mutated": False, "paper_only": True,
            "live_orders_enabled": False, "private_api_used": False}


def run(root: Path = ROOT, ledger_path: Path = DEFAULT_LEDGER) -> dict[str, Any]:
    before = {str(path.relative_to(root)): file_sha(path) for path in PROTECTED}
    audit = core.read_json(root / "experiments/current-forward-protocol-audit.json")
    ledger = core.read_json(ledger_path) if ledger_path.exists() else new_ledger()
    if (ledger.get("paper_estimates_emitted") is not False or ledger.get("main_paper_ledger_mutated") is not False
            or ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False):
        raise ValueError("unsafe capture backcase ledger")
    known = {row.get("backcase_id") for row in ledger.get("backcases", [])}; added = []
    for miss in audit.get("historical_missed_capture_windows", []):
        domain = str(miss.get("domain") or ""); path = DOMAIN_LEDGERS.get(domain)
        domain_ledger = core.read_json(path) if path and path.exists() else {"events": []}
        case = build_backcase(miss, matching_attempts(miss, domain_ledger))
        if case["backcase_id"] not in known:
            same_window = [row for row in ledger["backcases"] if
                           (str(row.get("domain")), str(row.get("identity")), str(row.get("window_end_at"))) ==
                           (case["domain"], case["identity"], case["window_end_at"])]
            if same_window:
                case["supersedes_backcase_id"] = same_window[-1].get("backcase_id")
                case["correction_reason"] = "exclude post-window status polling from capture attempt counts"
            ledger["backcases"].append(case); known.add(case["backcase_id"]); added.append(case["backcase_id"])
    ledger["updated_at"] = core.now_iso(); core.write_json(ledger_path, ledger)
    after = {str(path.relative_to(root)): file_sha(path) for path in PROTECTED}
    if before != after:
        raise RuntimeError("capture backcase generation mutated protected artifacts")
    return {
        "schema_version": "polymarket-capture-backcase-cycle-v1", "created_at": core.now_iso(),
        "status": "ok", "added_backcase_ids": added, "added_count": len(added),
        "total_backcase_count": len(ledger["backcases"]),
        "active_backcase_count": len({(row.get("domain"), row.get("identity"), row.get("window_end_at")) for row in ledger["backcases"]}),
        "protected_artifact_sha256_before": before, "protected_artifact_sha256_after": after,
        "protected_artifacts_unchanged": True, "paper_only": True,
        "live_orders_enabled": False, "private_api_used": False,
    }


def markdown(payload: dict[str, Any]) -> str:
    return "\n".join(["# Polymarket Capture Back Case Cycle", "",
                      f"- Status: `{payload['status']}`", f"- Added: {payload['added_count']}",
                      f"- Total Back Cases: {payload['total_backcase_count']}",
                      f"- Active Back Cases: {payload['active_backcase_count']}",
                      f"- Protected artifacts unchanged: `{str(payload['protected_artifacts_unchanged']).lower()}`", ""])


def self_test() -> dict[str, Any]:
    miss = {"domain": "weather", "event_id": "e1", "reason": "forecast_window_missed", "cutoff_at": "2026-07-12T05:00:00+00:00"}
    attempts = [{"attempted_at": "2026-07-12T04:30:00+00:00", "reason": "two_sided_live_book_missing"}]
    case = build_backcase(miss, attempts)
    assert case["defect_classification"] == "market_liquidity_or_book_completeness"
    assert case["automatic_parameter_change_eligible"] is False
    return {"status": "pass", "tests": ["post_miss_backcase", "no_automatic_parameter_change"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-capture-backcase-cycle.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_CAPTURE_BACKCASE_CYCLE.md")); args = parser.parse_args()
    payload = self_test() if args.self_test else run(ROOT, Path(args.ledger))
    if not args.self_test:
        core.write_json(Path(args.output), payload); Path(args.report).write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
