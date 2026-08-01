#!/usr/bin/env python3
"""Audit markets outside the current research-family taxonomy and propose safe additions."""
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = {
    "finance_barrier": {"any_tags": {"Hit Price", "Finance Updown", "Pyth Finance"}, "exclude_tags": {"Crypto", "Weekly", "Daily-Close", "Up or Down"}, "source_feasibility": 1.0},
    "technology_events": {"any_tags": {"AI", "Big Tech", "Tech", "AI Releases", "KPIs", "OpenAI", "Anthropic"}, "source_feasibility": 0.7},
    "token_launch": {"any_tags": {"token launch"}, "source_feasibility": 0.6},
    "corporate_events": {"any_tags": {"IPOs", "IPO", "Privates", "Earnings", "M&A"}, "source_feasibility": 0.7},
    "entertainment": {"any_tags": {"TV", "Movies", "Awards", "Emmys", "Music", "Celebrities"}, "source_feasibility": 0.6},
    "macro_policy": {"any_tags": {"Economy", "Global Rates", "Fed Rates", "Economic Policy"}, "source_feasibility": 0.8},
}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path); module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("uncovered_core", ROOT / "scripts/polymarket_alpha.py")
family = load("uncovered_family", ROOT / "scripts/polymarket_market_family_audit.py")
identity = load("uncovered_identity", ROOT / "scripts/polymarket_family_identity_enrichment.py")


def tags(market: dict[str, Any]) -> set[str]:
    return set((market.get("_sampling_raw") or {}).get("tags") or [])


def matches(market: dict[str, Any], rule: dict[str, Any]) -> bool:
    values = tags(market); any_tags = set(rule.get("any_tags") or []); all_tags = set(rule.get("all_tags") or []); excluded = set(rule.get("exclude_tags") or [])
    return (not any_tags or bool(values & any_tags)) and (not all_tags or all_tags <= values) and not bool(values & excluded)


def audit(markets: list[dict[str, Any]], enrichment: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    uncovered = [market for market in markets if not any(family.belongs(market, rule) for rule in family.FAMILIES.values())]
    rows = []
    for name, rule in CANDIDATES.items():
        selected = [market for market in uncovered if matches(market, rule)]; identities = [family.event_identity(market, enrichment) for market in selected]
        near = [market for market in selected if (end := family.end_time(market)) and as_of <= end <= as_of + timedelta(days=30)]
        near_ids = {value for market in near if (value := family.event_identity(market, enrichment))}
        proven = {value for value in identities if value}; complete = sum(family.rule_complete(market) for market in selected)
        rows.append({"family": name, "contract_count": len(selected), "independent_event_count": len(proven), "event_identity_proven_contract_count": sum(bool(value) for value in identities), "near_term_30d_event_count": len(near_ids), "rule_complete_pct": round(100 * complete / len(selected), 2) if selected else 0.0, "source_feasibility": rule["source_feasibility"], "phase_zero_inventory_gate_met": len(proven) >= 30})
    rows.sort(key=lambda row: (row["phase_zero_inventory_gate_met"], row["near_term_30d_event_count"], row["independent_event_count"]), reverse=True)
    tag_counts = Counter(tag for market in uncovered for tag in tags(market) if not tag.lower().startswith("rewards"))
    selected = next((row["family"] for row in rows if row["phase_zero_inventory_gate_met"]), None)
    return {"schema_version": "polymarket-uncovered-market-audit-v1", "created_at": core.now_iso(), "inventory_as_of": as_of.isoformat(), "total_market_count": len(markets), "uncovered_contract_count": len(uncovered), "uncovered_independent_event_count": len({value for market in uncovered if (value := family.event_identity(market, enrichment))}), "uncovered_event_identity_proven_contract_count": sum(family.event_identity(market, enrichment) is not None for market in uncovered), "top_uncovered_tags": [{"tag": tag, "contracts": count} for tag, count in tag_counts.most_common(50)], "candidate_families": rows, "selected_family_for_router_addition": selected, "selection_is_alpha_evidence": False, "paper_entry_eligible": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}


def report(payload: dict[str, Any]) -> str:
    lines = ["# Polymarket Uncovered-Market Audit", "", f"- Total / uncovered contracts: {payload['total_market_count']} / {payload['uncovered_contract_count']}", f"- Uncovered proven events: {payload['uncovered_independent_event_count']}", f"- Proposed next family: `{payload['selected_family_for_router_addition']}`", "- This is taxonomy coverage only, never Alpha or paper permission.", "", "| Candidate | Contracts | Events | <=30d events | Rules | Gate |", "|---|---:|---:|---:|---:|---|"]
    for row in payload["candidate_families"]:
        lines.append(f"| {row['family']} | {row['contract_count']} | {row['independent_event_count']} | {row['near_term_30d_event_count']} | {row['rule_complete_pct']:.2f}% | {str(row['phase_zero_inventory_gate_met']).lower()} |")
    lines.extend(["", "Candidate families may overlap during Phase 0. A model scope must later choose a homogeneous event type and freeze its own independence unit.", ""]); return "\n".join(lines)


def self_test() -> dict[str, Any]:
    market = {"_sampling_raw": {"tags": ["Finance", "Hit Price", "Equities"]}}
    crypto = {"_sampling_raw": {"tags": ["Crypto", "Hit Price"]}}
    assert matches(market, CANDIDATES["finance_barrier"]); assert not matches(crypto, CANDIDATES["finance_barrier"])
    return {"status": "pass", "tests": ["equity_barrier_match", "crypto_barrier_exclusion"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--enrich", action="store_true")
    parser.add_argument("--markets", default=str(ROOT / "cache/current_validation_snapshot/markets.json")); parser.add_argument("--snapshot-manifest", default=str(ROOT / "cache/current_validation_snapshot/snapshot-manifest.json")); parser.add_argument("--enrichment", default=str(ROOT / "cache/current_uncovered_identity_enrichment.json")); parser.add_argument("--output", default=str(ROOT / "experiments/current-uncovered-market-audit.json")); parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_UNCOVERED_MARKET_AUDIT.md")); args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    else:
        markets = core.read_json(args.markets); snapshot = core.read_json(args.snapshot_manifest); as_of = core.parse_iso(snapshot.get("created_at")) or datetime.now(timezone.utc)
        uncovered = [market for market in markets if not any(family.belongs(market, rule) for rule in family.FAMILIES.values())]
        enrichment_path = Path(args.enrichment)
        if args.enrich:
            enrichment = identity.build(uncovered, Path(args.markets)); enrichment["scope"] = "markets_uncovered_by_current_family_taxonomy"; core.write_json(enrichment_path, enrichment)
        else:
            enrichment = core.read_json(enrichment_path) if enrichment_path.exists() else {}
        payload = audit(markets, enrichment, as_of); core.write_json(args.output, payload); Path(args.report).write_text(report(payload), encoding="utf-8")
    print(json.dumps(payload if args.self_test else {k: v for k, v in payload.items() if k != "top_uncovered_tags"}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
