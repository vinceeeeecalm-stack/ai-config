#!/usr/bin/env python3
"""Audit every research shadow for strict forward-only timestamp semantics."""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "crypto_barrier": ("experiments/current-shadow-forecast-cycle.json", "data/research_forecast_ledger.json", "retroactive_backfill_allowed"),
    "weather": ("experiments/current-weather-shadow-cycle.json", "data/weather_research_forecast_ledger.json", "retroactive_backfill_allowed"),
    "football": ("experiments/current-football-shadow-cycle.json", "data/football_research_forecast_ledger.json", "retroactive_backfill_allowed"),
    "social_count": ("experiments/current-social-count-shadow-cycle.json", "data/social_count_research_forecast_ledger.json", "retroactive_backfill_allowed"),
    "stock_weekly": ("experiments/current-stock-weekly-shadow-cycle.json", "data/stock_weekly_research_forecast_ledger.json", "retroactive_backfill_allowed"),
}
MISSED_REASONS = {"forecast_window_missed", "capture_window_missed"}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("forward_protocol_core", ROOT / "scripts/polymarket_alpha.py")


def forecast_window(domain: str, row: dict[str, Any]):
    captured = core.parse_iso(row.get("captured_at") or row.get("created_at"))
    cutoff = core.parse_iso(row.get("cutoff_at"))
    if domain == "crypto_barrier":
        return captured, None, core.parse_iso(row.get("end_at"))
    if domain == "weather":
        start = core.parse_iso(row.get("capture_window_start_at")) or (cutoff - timedelta(minutes=60) if cutoff else None)
        return captured, start, cutoff
    if domain == "football":
        return captured, cutoff, core.parse_iso(row.get("kickoff_at"))
    if domain == "social_count":
        start = core.parse_iso(row.get("capture_window_start_at")) or (cutoff - timedelta(hours=1) if cutoff else None)
        end = core.parse_iso(row.get("capture_window_end_at")) or cutoff
        return captured, start, end
    if domain == "stock_weekly":
        start = core.parse_iso(row.get("capture_window_start_at")) or (cutoff - timedelta(hours=24) if cutoff else None)
        return captured, start, cutoff
    return captured, None, None


def validate_forecast(domain: str, row: dict[str, Any]) -> list[str]:
    captured, start, end = forecast_window(domain, row)
    failures = []
    if captured is None or end is None:
        failures.append("capture_or_window_end_missing")
    elif captured >= end:
        failures.append("capture_not_before_window_end")
    if start is not None and captured is not None and captured < start:
        failures.append("capture_before_window_start")
    if row.get("research_only") is not True or row.get("not_eligible_for_paper_entry") is not True:
        failures.append("research_isolation_flag_missing")
    if row.get("live_orders_enabled") is not False or row.get("private_api_used") is not False:
        failures.append("unsafe_forecast_flags")
    return failures


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Polymarket Forward Protocol Audit", "",
        f"- Status: `{payload['status']}`",
        f"- Forecasts checked: {payload['forecast_count']}",
        f"- Protocol violations: {payload['protocol_violation_count']}",
        f"- Newly missed capture windows this audit: {payload['missed_capture_window_count']}",
        f"- Historical unique missed windows: {payload['historical_missed_capture_window_count']}", "",
        "| Domain | Forecasts | Violations | Missed windows |", "|---|---:|---:|---:|",
    ]
    for row in payload["domains"]:
        lines.append(f"| {row['domain']} | {row['forecast_count']} | {row['protocol_violation_count']} | {row['missed_capture_window_count']} |")
    lines.extend(["", "A newly observed missed or post-window forecast is never backfilled and degrades that operational cycle. Every unique miss remains in historical evidence without permanently degrading later on-time cycles.", ""])
    return "\n".join(lines)


def missed_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    identity = row.get("event_id") or row.get("tracking_id") or row.get("condition_id") or row.get("forecast_id")
    window = row.get("cutoff_at") or row.get("capture_window_end_at") or row.get("kickoff_at")
    return row.get("domain"), identity, row.get("reason"), window


def newly_missed(current: list[dict[str, Any]], previous_historical: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known = {missed_identity(row) for row in previous_historical}
    legacy_known = {identity[:3] for identity in known if identity[3] is None}
    return [row for row in current if missed_identity(row) not in known and missed_identity(row)[:3] not in legacy_known]


def normalize_missed_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge legacy misses without window fields into their sole detailed window."""
    windows: dict[tuple[Any, ...], set[Any]] = {}
    for row in rows:
        identity = missed_identity(row)
        if identity[3] is not None:
            windows.setdefault(identity[:3], set()).add(identity[3])
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        identity = missed_identity(row)
        if identity[3] is None and len(windows.get(identity[:3], set())) == 1:
            identity = (*identity[:3], next(iter(windows[identity[:3]])))
        previous = unique.get(identity) or {}
        if len(row) >= len(previous):
            unique[identity] = row
    return list(unique.values())


def audit(root: Path = ROOT) -> dict[str, Any]:
    previous_path = root / "experiments/current-forward-protocol-audit.json"
    previous_payload = core.read_json(previous_path) if previous_path.exists() else {}
    previous_historical = list(previous_payload.get("historical_missed_capture_windows") or [])
    domains, contract_failures, violations, missed_candidates, historical_missed = [], [], [], [], []
    for domain, (cycle_rel, ledger_rel, flag) in SOURCES.items():
        cycle_path, ledger_path = root / cycle_rel, root / ledger_rel
        if not cycle_path.exists() or not ledger_path.exists():
            contract_failures.append({"domain": domain, "reason": "cycle_or_ledger_missing"})
            domains.append({"domain": domain, "forecast_count": 0, "protocol_violation_count": 0, "missed_capture_window_count": 0})
            continue
        cycle, ledger = core.read_json(cycle_path), core.read_json(ledger_path)
        if cycle.get(flag) is not False:
            contract_failures.append({"domain": domain, "reason": "retroactive_backfill_not_explicitly_blocked"})
        if cycle.get("paper_estimates_emitted") is not False or cycle.get("main_paper_ledger_mutated") is not False:
            contract_failures.append({"domain": domain, "reason": "research_isolation_contract_failed"})
        rows = list(ledger.get("open_forecasts", [])) + list(ledger.get("resolved_forecasts", []))
        domain_violations = []
        for row in rows:
            failures = validate_forecast(domain, row)
            if failures:
                item = {"domain": domain, "forecast_id": row.get("forecast_id"), "failures": failures}
                violations.append(item); domain_violations.append(item)
        domain_missed = [{"domain": domain, **row} for row in cycle.get("excluded", []) if row.get("reason") in MISSED_REASONS]
        missed_candidates.extend(domain_missed)
        for event in ledger.get("events", []):
            for row in event.get("excluded", []):
                if row.get("reason") in MISSED_REASONS:
                    historical_missed.append({"domain": domain, **row})
        domains.append({
            "domain": domain, "forecast_count": len(rows),
            "protocol_violation_count": len(domain_violations),
            "missed_capture_window_count": 0,
        })
    missed = newly_missed(missed_candidates, previous_historical)
    for row in domains:
        row["missed_capture_window_count"] = sum(item.get("domain") == row["domain"] for item in missed)
    normalized_historical = normalize_missed_rows(previous_historical + historical_missed + missed_candidates)
    status = "ok" if not contract_failures and not violations and not missed else "failed"
    return {
        "schema_version": "polymarket-forward-protocol-audit-v1", "created_at": core.now_iso(),
        "status": status, "domains": domains,
        "forecast_count": sum(row["forecast_count"] for row in domains),
        "contract_failures": contract_failures,
        "protocol_violations": violations, "protocol_violation_count": len(violations),
        "missed_capture_windows": missed, "missed_capture_window_count": len(missed),
        "current_cycle_missed_candidates": missed_candidates,
        "current_cycle_missed_candidate_count": len(missed_candidates),
        "historical_missed_capture_windows": normalized_historical,
        "historical_missed_capture_window_count": len(normalized_historical),
        "retroactive_backfill_allowed": False, "paper_only": True,
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }


def self_test() -> dict[str, Any]:
    base = {
        "created_at": "2026-07-13T04:30:00+00:00", "captured_at": "2026-07-13T04:30:00+00:00",
        "cutoff_at": "2026-07-13T04:59:00+00:00", "capture_window_start_at": "2026-07-13T03:59:00+00:00",
        "research_only": True, "not_eligible_for_paper_entry": True,
        "live_orders_enabled": False, "private_api_used": False,
    }
    assert validate_forecast("weather", base) == []
    assert "capture_not_before_window_end" in validate_forecast("weather", {**base, "captured_at": base["cutoff_at"]})
    stock = {**base, "cutoff_at": "2026-07-13T13:29:00+00:00", "capture_window_start_at": "2026-07-12T13:29:00+00:00", "captured_at": "2026-07-12T14:00:00+00:00"}
    assert validate_forecast("stock_weekly", stock) == []
    miss = {"domain": "social_count", "tracking_id": "t1", "reason": "forecast_window_missed"}
    detailed_miss = {**miss, "cutoff_at": "2026-07-13T00:00:00+00:00"}
    assert newly_missed([miss], []) == [miss]
    assert newly_missed([miss], [miss]) == []
    assert newly_missed([detailed_miss], [miss]) == []
    assert normalize_missed_rows([miss, detailed_miss]) == [detailed_miss]
    return {"status": "pass", "tests": ["weather_pre_event_window", "post_window_rejected", "stock_24h_window", "new_miss_degrades_once", "historical_miss_retained_without_permanent_degradation", "legacy_miss_window_normalization"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "experiments/current-forward-protocol-audit.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_FORWARD_PROTOCOL_AUDIT.md"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    else:
        payload = audit(ROOT)
        core.write_json(Path(args.output), payload)
        report = Path(args.report); report.parent.mkdir(parents=True, exist_ok=True); report.write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") in {"ok", "pass"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
