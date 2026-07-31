#!/usr/bin/env python3
"""Technology-events Phase-0 taxonomy with strict source and event-type isolation."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path); module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("technology_core", ROOT / "scripts/polymarket_alpha.py")
family = load("technology_family", ROOT / "scripts/polymarket_market_family_audit.py")


def event_type(question: str) -> str:
    text = question.lower()
    if re.search(r"\bipo\b|go public|publicly traded", text): return "ipo_or_public_listing"
    if re.search(r"acquir|merger|bankrupt|chapter 11", text): return "ma_or_distress"
    if re.search(r"fda approves|scotus|court case|self-certify|government removes|government take a stake|moratorium|regulat", text): return "regulatory_or_policy_event"
    if re.search(r"leave .*(?:openai|anthropic|google)|out as .*ceo|arrested|retires?|resigns?", text): return "executive_or_personnel_event"
    if re.search(r"incident|disrupted|outage|rocket explodes|starship launch", text): return "infrastructure_or_launch_incident"
    if re.search(r"public ticker|ticker be", text): return "listing_symbol"
    if re.search(r"q[1-4]\b|gross margin|profit margin|provision for credit losses|investment banking fees|backlog|assets under management|production|load factor|combined ratio|ffo per|net interest income|procedure growth|unit case volume", text): return "company_kpi"
    if re.search(r"release|launch|announce|available|ship", text) and re.search(r"model|iphone|device|product|robotaxi|cybercab|ai|gpt|claude|gemini", text): return "product_or_model_release"
    if re.search(r"best ai|top ai|leaderboard|benchmark|score|rating", text): return "ai_benchmark_or_ranking"
    if re.search(r"revenue|capex|deliver|sell|sales|users|subscribers|downloads|earnings", text): return "company_kpi"
    if re.search(r"largest company|market cap|valuation", text): return "valuation_or_market_cap"
    if re.search(r"token", text): return "token_event"
    return "other_technology_event"


def subject(question: str) -> str:
    text = question.lower()
    patterns = [("OpenAI", "openai|chatgpt|gpt-"), ("Anthropic", "anthropic|claude"), ("Google", "google|gemini|deepmind"), ("Apple", "apple|iphone"), ("NVIDIA", "nvidia"), ("Tesla", "tesla|cybercab|robotaxi"), ("Meta", "meta|llama"), ("Microsoft", "microsoft"), ("Amazon", "amazon|aws")]
    for name, pattern in patterns:
        if re.search(pattern, text): return name
    return "Other"


def source_authority(description: str) -> str:
    text = description.lower()
    if re.search(r"sec filing|10-k|10-q|official (?:company|information|announcement|website|blog)|company's official|investor relations", text): return "official_company_or_regulatory"
    if re.search(r"artificial analysis|lmarena|chatbot arena|leaderboard|benchmark", text): return "objective_benchmark"
    if "consensus of credible reporting" in text or "credible reporting" in text: return "media_consensus"
    if "resolution source" in text or "resolve based on" in text: return "named_other_source"
    return "source_unspecified"


def merged_enrichment() -> dict[str, Any]:
    mapping = {}
    for name in ("current_family_identity_enrichment.json", "current_uncovered_identity_enrichment.json"):
        path = ROOT / "cache" / name
        if path.exists(): mapping.update((core.read_json(path).get("by_condition_id") or {}))
    return {"by_condition_id": mapping}


def audit(markets: list[dict[str, Any]], enrichment: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    rows = []
    for market in markets:
        if not family.belongs(market, family.FAMILIES["technology_events"]): continue
        raw = market.get("_sampling_raw") or {}; question = str(market.get("question") or raw.get("question") or ""); description = str(market.get("description") or raw.get("description") or ""); end = family.end_time(market); authority = source_authority(description)
        rows.append({"market_id": str(market.get("id") or market.get("conditionId") or ""), "event_id": family.event_identity(market, enrichment), "question": question, "event_type": event_type(question), "subject": subject(question), "source_authority": authority, "end_time": end.isoformat() if end else None, "within_30d": bool(end and as_of <= end <= as_of + timedelta(days=30)), "expired_but_live": bool(end and end < as_of), "high_rule_ambiguity": authority == "source_unspecified" or not family.rule_complete(market)})
    groups = []
    for key in sorted({(row["event_type"], row["source_authority"], row["subject"]) for row in rows}):
        selected = [row for row in rows if (row["event_type"], row["source_authority"], row["subject"]) == key]; events = {row["event_id"] for row in selected if row["event_id"]}; clean_events = {row["event_id"] for row in selected if row["event_id"] and not row["high_rule_ambiguity"] and not row["expired_but_live"]}; near_events = {row["event_id"] for row in selected if row["event_id"] and row["within_30d"]}
        eligible_type = key[0] != "other_technology_event" and key[2] != "Other"
        groups.append({"event_type": key[0], "source_authority": key[1], "subject": key[2], "contract_count": len(selected), "independent_event_count": len(events), "clean_event_count": len(clean_events), "near_term_30d_event_count": len(near_events), "phase_zero_candidate": bool(eligible_type and events and len(events) >= 30 and len(clean_events) / len(events) >= .90)})
    groups.sort(key=lambda row: (row["phase_zero_candidate"], row["near_term_30d_event_count"], row["independent_event_count"]), reverse=True); candidates = [row for row in groups if row["phase_zero_candidate"]]
    return {"schema_version": "polymarket-technology-taxonomy-audit-v1", "created_at": core.now_iso(), "inventory_as_of": as_of.isoformat(), "contract_count": len(rows), "independent_event_count": len({row["event_id"] for row in rows if row["event_id"]}), "groups": groups, "selected_group_for_history_research": candidates[0] if candidates else None, "model_status": "blocked_pending_terminal_history" if candidates else "blocked_no_homogeneous_live_group", "paper_entry_eligible": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}


def markdown(payload: dict[str, Any]) -> str:
    lines = ["# Polymarket Technology Phase-0 Audit", "", f"- Contracts / events: {payload['contract_count']} / {payload['independent_event_count']}", f"- Selected history group: `{payload['selected_group_for_history_research']}`", f"- Status: `{payload['model_status']}`", "", "| Event type | Source | Subject | Contracts | Events | Clean | <=30d | Candidate |", "|---|---|---|---:|---:|---:|---:|---|"]
    for row in payload["groups"]:
        lines.append(f"| {row['event_type']} | {row['source_authority']} | {row['subject']} | {row['contract_count']} | {row['independent_event_count']} | {row['clean_event_count']} | {row['near_term_30d_event_count']} | {str(row['phase_zero_candidate']).lower()} |")
    lines.extend(["", "IPO, M&A/distress, product/model releases, benchmark rankings, KPIs and valuation events remain isolated. Phase-0 selection is not probability or paper evidence.", ""]); return "\n".join(lines)


def self_test() -> dict[str, Any]:
    assert event_type("Will OpenAI launch GPT-6 by December?") == "product_or_model_release"
    assert event_type("Anthropic IPO before 2027?") == "ipo_or_public_listing"
    assert source_authority("The resolution source is the company's official announcement.") == "official_company_or_regulatory"
    return {"status": "pass", "tests": ["release_type", "ipo_type", "official_source"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--markets", default=str(ROOT / "cache/current_validation_snapshot/markets.json")); parser.add_argument("--snapshot-manifest", default=str(ROOT / "cache/current_validation_snapshot/snapshot-manifest.json")); parser.add_argument("--output", default=str(ROOT / "experiments/current-technology-taxonomy-audit.json")); parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_TECHNOLOGY_TAXONOMY_AUDIT.md")); args = parser.parse_args()
    if args.self_test: payload = self_test()
    else:
        snapshot = core.read_json(args.snapshot_manifest)
        if snapshot.get("terminal_cursor_proven") is not True: raise ValueError("snapshot is not terminal")
        payload = audit(core.read_json(args.markets), merged_enrichment(), core.parse_iso(snapshot.get("created_at")) or datetime.now(timezone.utc)); core.write_json(args.output, payload); Path(args.report).write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
