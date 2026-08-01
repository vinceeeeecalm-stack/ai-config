#!/usr/bin/env python3
"""Macro-policy Phase-0 taxonomy for central-bank and policy events."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path); module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("macro_policy_core", ROOT / "scripts/polymarket_alpha.py")
family = load("macro_policy_family", ROOT / "scripts/polymarket_market_family_audit.py")


def institution(question: str) -> str:
    text = question.lower()
    if re.search(r"fed|federal reserve|fomc|powell", text): return "US_Fed"
    if re.search(r"ecb|european central bank|lagarde", text): return "ECB"
    if re.search(r"bank of england|boe", text): return "BoE"
    if re.search(r"bank of japan|boj", text): return "BoJ"
    if re.search(r"bank of canada|boc", text): return "BoC"
    if re.search(r"rba|reserve bank of australia", text): return "RBA"
    if re.search(r"rbnz|reserve bank of new zealand", text): return "RBNZ"
    return "Other"


def event_type(question: str) -> str:
    text = question.lower()
    if re.search(r"target rate|target range|basis points|bps|cut rates?|raise rates?|rate cut|rate hike", text) and re.search(r"meeting|decision|fomc|july|september|october|december", text): return "meeting_rate_decision"
    if re.search(r"\d+ fed rate cuts?|no fed rate cuts?|how many.*cuts?", text): return "annual_cut_count"
    if re.search(r"rate hike|raise rates|hike in 2026", text): return "annual_hike_binary"
    if re.search(r"rate cut|cut rates|cut in 2026", text): return "annual_cut_binary"
    if re.search(r"recession", text): return "recession_binary"
    if re.search(r"powell|fed chair|board|governor", text): return "central_bank_personnel"
    if re.search(r"tariff|tax|capital gains|economic policy|stake in", text): return "fiscal_or_trade_policy"
    if re.search(r"yield curve|treasury|bond yield", text): return "rates_market_outcome"
    return "other_macro_policy"


def source_authority(description: str) -> str:
    text = description.lower()
    if re.search(r"federal reserve|fomc|ecb\.europa|bank of england|bank of japan|bank of canada|official (?:announcement|statement|data)", text): return "official_central_bank_or_government"
    if re.search(r"cme fedwatch|fedwatch", text): return "named_market_tool"
    if re.search(r"fred|bea|bls|nber", text): return "official_statistical_or_nber"
    if "consensus of credible reporting" in text or "credible reporting" in text: return "media_consensus"
    if "resolution source" in text or "resolve based on" in text: return "named_other_source"
    return "source_unspecified"


def horizon(question: str) -> str:
    text = question.lower()
    if re.search(r"july|september|october|december|next meeting|fomc meeting", text): return "specific_meeting_or_month"
    if re.search(r"2026|end of the year|year-end|this year", text): return "calendar_2026"
    if re.search(r"2027", text): return "calendar_2027"
    return "other_horizon"


def merged_enrichment() -> dict[str, Any]:
    mapping = {}
    for name in ("current_family_identity_enrichment.json", "current_uncovered_identity_enrichment.json"):
        path = ROOT / "cache" / name
        if path.exists(): mapping.update(core.read_json(path).get("by_condition_id") or {})
    return {"by_condition_id": mapping}


def audit(markets: list[dict[str, Any]], enrichment: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    rows = []
    for market in markets:
        if not family.belongs(market, family.FAMILIES["macro_policy"]): continue
        raw = market.get("_sampling_raw") or {}; question = str(market.get("question") or raw.get("question") or ""); description = str(market.get("description") or raw.get("description") or ""); authority = source_authority(description); end = family.end_time(market)
        rows.append({"market_id": str(market.get("id") or market.get("conditionId") or ""), "event_id": family.event_identity(market, enrichment), "question": question, "institution": institution(f"{question} {description}"), "event_type": event_type(question), "source_authority": authority, "horizon": horizon(question), "within_30d": bool(end and as_of <= end <= as_of + timedelta(days=30)), "expired_but_live": bool(end and end < as_of), "rule_clear": authority != "source_unspecified" and family.rule_complete(market)})
    groups = []
    for key in sorted({(row["institution"], row["event_type"], row["source_authority"], row["horizon"]) for row in rows}):
        selected = [row for row in rows if (row["institution"], row["event_type"], row["source_authority"], row["horizon"]) == key]; events = {row["event_id"] for row in selected if row["event_id"]}; clean = {row["event_id"] for row in selected if row["event_id"] and row["rule_clear"] and not row["expired_but_live"]}; near = {row["event_id"] for row in selected if row["event_id"] and row["within_30d"]}; ratio = len(clean) / len(events) if events else 0
        groups.append({"institution": key[0], "event_type": key[1], "source_authority": key[2], "horizon": key[3], "contract_count": len(selected), "independent_event_count": len(events), "clean_event_count": len(clean), "near_term_30d_event_count": len(near), "phase_zero_history_candidate": bool(events and len(events) >= 30 and ratio >= .90 and key[0] != "Other" and key[1] != "other_macro_policy")})
    groups.sort(key=lambda row: (row["phase_zero_history_candidate"], row["near_term_30d_event_count"], row["independent_event_count"]), reverse=True); candidates = [row for row in groups if row["phase_zero_history_candidate"]]
    return {"schema_version": "polymarket-macro-policy-taxonomy-v1", "created_at": core.now_iso(), "inventory_as_of": as_of.isoformat(), "contract_count": len(rows), "independent_event_count": len({row["event_id"] for row in rows if row["event_id"]}), "groups": groups, "selected_group_for_history_research": candidates[0] if candidates else None, "model_status": "blocked_pending_terminal_history" if candidates else "blocked_no_homogeneous_live_group", "paper_entry_eligible": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}


def markdown(payload: dict[str, Any]) -> str:
    lines = ["# Polymarket Macro Policy Phase-0 Audit", "", f"- Contracts / events: {payload['contract_count']} / {payload['independent_event_count']}", f"- Selected history group: `{payload['selected_group_for_history_research']}`", f"- Status: `{payload['model_status']}`", "", "| Institution | Type | Source | Horizon | Contracts | Events | Clean | <=30d | Candidate |", "|---|---|---|---|---:|---:|---:|---:|---|"]
    for row in payload["groups"]: lines.append(f"| {row['institution']} | {row['event_type']} | {row['source_authority']} | {row['horizon']} | {row['contract_count']} | {row['independent_event_count']} | {row['clean_event_count']} | {row['near_term_30d_event_count']} | {str(row['phase_zero_history_candidate']).lower()} |")
    lines.extend(["", "Meeting decisions, annual cut counts, binary hike/cut events, recession, personnel and fiscal/trade policy remain isolated. Macro statistical releases remain in the separate Macro Indicators family.", ""]); return "\n".join(lines)


def self_test() -> dict[str, Any]:
    assert event_type("Will the Fed cut rates at the September FOMC meeting?") == "meeting_rate_decision"
    assert event_type("Will 3 Fed rate cuts happen in 2026?") == "annual_cut_count"
    assert institution("ECB rate decision in July?") == "ECB"
    return {"status":"pass","tests":["meeting_rate","annual_cut_count","ecb_route"]}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--self-test",action="store_true");parser.add_argument("--markets",default=str(ROOT/"cache/current_validation_snapshot/markets.json"));parser.add_argument("--snapshot-manifest",default=str(ROOT/"cache/current_validation_snapshot/snapshot-manifest.json"));parser.add_argument("--output",default=str(ROOT/"experiments/current-macro-policy-taxonomy-audit.json"));parser.add_argument("--report",default=str(ROOT/"reports/CURRENT_MACRO_POLICY_TAXONOMY_AUDIT.md"));args=parser.parse_args()
    if args.self_test:payload=self_test()
    else:
        snapshot=core.read_json(args.snapshot_manifest)
        if snapshot.get("terminal_cursor_proven") is not True:raise ValueError("snapshot is not terminal")
        payload=audit(core.read_json(args.markets),merged_enrichment(),core.parse_iso(snapshot.get("created_at")) or datetime.now(timezone.utc));core.write_json(args.output,payload);Path(args.report).write_text(markdown(payload),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
