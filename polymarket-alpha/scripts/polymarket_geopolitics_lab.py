#!/usr/bin/env python3
"""Taxonomy-first, public-only Phase 0 audit for live geopolitics markets."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.I)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    return module


core = load("geopolitics_core", ROOT / "scripts/polymarket_alpha.py")
family = load("geopolitics_family", ROOT / "scripts/polymarket_market_family_audit.py")


SUBTYPE_RULES = [
    ("compound_or_parlay", re.compile(r"\b(parlay|nothing ever happens)\b", re.I)),
    ("election_or_office_winner", re.compile(r"\b(election|electoral|next prime minister|win the most|next leader)\b", re.I)),
    ("territorial_capture", re.compile(r"\b(capture|captures|control of .* city|take control of .* city)\b", re.I)),
    ("legal_custody_or_sentence", re.compile(r"\b(custody|released|sentenced|prison|indicted|convicted|extradit)\b", re.I)),
    ("leader_tenure_or_regime", re.compile(r"\b(out (?:as|before|by)|ceases? to be|leader of .* end|regime fall|removed from power|resigns?|overthrown)\b", re.I)),
    ("diplomatic_meeting_or_visit", re.compile(r"\b(meet with|meeting with|visit(?:s|ed)?|summit with)\b", re.I)),
    ("normalization_accord_or_treaty", re.compile(r"\b(normalize|normalization|abraham accords|peace deal|ceasefire|armistice|trade deal|join nato|join the eu)\b", re.I)),
    ("military_strike_invasion_or_clash", re.compile(r"\b(strike on|strike [a-z]|invad|military clash|military encounter|offensive|article 5)\b", re.I)),
    ("nuclear_or_security_treaty", re.compile(r"\b(nuclear|nuke|npt|nato|withdraw from nato|missile)\b", re.I)),
    ("policy_or_count_threshold", re.compile(r"\b(deport|tariff|gold cards?|how many|\d+k|less than|more than|between \d|at least \d|over \d)\b", re.I)),
    ("award_or_appointment", re.compile(r"\b(nobel|prize|appointed|nomination|secretary|ambassador)\b", re.I)),
]


def classify(question: str, tags: set[str]) -> str:
    text = question.strip()
    if "Parlays" in tags:
        return "compound_or_parlay"
    for subtype, pattern in SUBTYPE_RULES:
        if pattern.search(text):
            return subtype
    return "other_geopolitics"


def rule_diagnostics(market: dict[str, Any]) -> dict[str, Any]:
    raw = market.get("_sampling_raw") or {}
    description = str(market.get("description") or raw.get("description") or "")
    resolution_source = str(market.get("resolutionSource") or raw.get("resolution_source") or "").strip()
    urls = sorted(set(URL_RE.findall(description)))
    lower = description.lower()
    explicit_authority = bool(
        resolution_source or urls or re.search(
            r"\b(according to|as announced by|official(?:ly| information| sources?)|resolution source|primary resolution source|will use information from)\b",
            lower,
        )
    )
    media_judgment = bool(re.search(r"\b(credible report(?:s|ing)?|consensus of credible|preponderance of credible|reputable media)\b", lower))
    return {
        "description_present": bool(description.strip()),
        "resolution_source_field_present": bool(resolution_source),
        "source_urls": urls,
        "explicit_authority_language": explicit_authority,
        "media_judgment_required": media_judgment,
        "high_rule_ambiguity": not explicit_authority or media_judgment,
    }


def binary_contract(market: dict[str, Any]) -> bool:
    raw = market.get("_sampling_raw") or {}
    tokens = raw.get("tokens") or []
    outcomes = market.get("outcomes")
    if isinstance(outcomes, str):
        try: outcomes = json.loads(outcomes)
        except json.JSONDecodeError: outcomes = []
    return len(tokens) == 2 and isinstance(outcomes, list) and len(outcomes) == 2


def audit(markets: list[dict[str, Any]], as_of: datetime) -> dict[str, Any]:
    selected = [market for market in markets if family.belongs(market, family.FAMILIES["geopolitics"])]
    rows, counts = [], Counter()
    for market in selected:
        raw = market.get("_sampling_raw") or {}
        question = str(market.get("question") or raw.get("question") or "")
        tags = set(raw.get("tags") or [])
        subtype = classify(question, tags)
        diagnostics = rule_diagnostics(market)
        end = family.end_time(market)
        expired = bool(end and end < as_of)
        event_id = family.event_identity(market)
        row = {
            "market_id": str(market.get("id") or market.get("conditionId") or ""),
            "event_id": event_id, "question": question, "subtype": subtype,
            "end_time": end.isoformat() if end else None, "expired_but_in_live_inventory": expired,
            "binary_two_token_contract": binary_contract(market), "tags": sorted(tags),
            **diagnostics,
        }
        rows.append(row); counts[subtype] += 1
    subtype_rows = []
    for subtype in sorted(counts):
        subset = [row for row in rows if row["subtype"] == subtype]
        independent = len({row["event_id"] for row in subset})
        explicit = sum(row["explicit_authority_language"] for row in subset)
        ambiguous = sum(row["high_rule_ambiguity"] for row in subset)
        binary = sum(row["binary_two_token_contract"] for row in subset)
        expired = sum(row["expired_but_in_live_inventory"] for row in subset)
        subtype_rows.append({
            "subtype": subtype, "contract_count": len(subset), "independent_event_count": independent,
            "binary_contract_pct": round(100 * binary / len(subset), 2),
            "explicit_authority_pct": round(100 * explicit / len(subset), 2),
            "high_rule_ambiguity_pct": round(100 * ambiguous / len(subset), 2),
            "expired_but_live_count": expired,
            "phase_zero_candidate": independent >= 30 and binary == len(subset) and expired == 0 and explicit / len(subset) >= 0.80 and ambiguous / len(subset) <= 0.20,
        })
    subtype_rows.sort(key=lambda row: (row["phase_zero_candidate"], row["independent_event_count"], row["explicit_authority_pct"]), reverse=True)
    candidates = [row for row in subtype_rows if row["phase_zero_candidate"]]
    return {
        "schema_version": "polymarket-geopolitics-taxonomy-audit-v1", "created_at": core.now_iso(),
        "inventory_as_of": as_of.isoformat(), "family": "geopolitics",
        "market_count": len(rows), "independent_event_count": len({row["event_id"] for row in rows}),
        "subtypes": subtype_rows, "selected_subtype_for_history_research": candidates[0]["subtype"] if candidates else None,
        "selection_is_alpha_evidence": False,
        "model_status": "blocked_pending_subtype_history_and_external_source_contract",
        "final_holdout_inspected": False, "paper_entry_eligible": False,
        "contract_rows": rows, "paper_estimates_emitted": False,
        "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False,
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Polymarket Geopolitics Taxonomy Audit", "",
        f"- Inventory: {payload['market_count']} contracts / {payload['independent_event_count']} event IDs",
        f"- Phase-0 history candidate: `{payload['selected_subtype_for_history_research']}`",
        f"- Model status: `{payload['model_status']}`",
        "- This audit classifies data feasibility only; it emits no probability and cannot enable paper entry.", "",
        "| Subtype | Contracts | Events | Binary | Explicit authority | High ambiguity | Expired live | Candidate |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["subtypes"]:
        lines.append(
            f"| {row['subtype']} | {row['contract_count']} | {row['independent_event_count']} | "
            f"{row['binary_contract_pct']:.1f}% | {row['explicit_authority_pct']:.1f}% | "
            f"{row['high_rule_ambiguity_pct']:.1f}% | {row['expired_but_live_count']} | {str(row['phase_zero_candidate']).lower()} |"
        )
    lines.extend(["", "A candidate still needs terminal settled history, point-in-time external data, cutoff prices, frozen splits and OOS comparison against the market before any model estimate.", ""])
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    assert classify("Will Trump meet with Person X in 2026?", {"Geopolitics"}) == "diplomatic_meeting_or_visit"
    assert classify("US strike on Country X by December 31?", {"Geopolitics"}) == "military_strike_invasion_or_clash"
    assert classify("Nothing Ever Happens: 2026", {"Geopolitics", "Parlays"}) == "compound_or_parlay"
    clear = {"description": "This resolves according to the official government announcement at https://example.gov/result"}
    vague = {"description": "This resolves according to a consensus of credible reporting."}
    assert rule_diagnostics(clear)["high_rule_ambiguity"] is False
    assert rule_diagnostics(vague)["high_rule_ambiguity"] is True
    return {"status": "pass", "tests": ["meeting_taxonomy", "strike_taxonomy", "parlay_exclusion", "authority_detection", "media_judgment_ambiguity"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", default=str(ROOT / "cache/current_validation_snapshot/markets.json"))
    parser.add_argument("--snapshot-manifest", default=str(ROOT / "cache/current_validation_snapshot/snapshot-manifest.json"))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-geopolitics-taxonomy-audit.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_GEOPOLITICS_TAXONOMY_AUDIT.md"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    else:
        manifest = core.read_json(args.snapshot_manifest)
        if manifest.get("terminal_cursor_proven") is not True:
            raise ValueError("snapshot has no terminal cursor proof")
        as_of = core.parse_iso(manifest.get("created_at")) or datetime.now(timezone.utc)
        payload = audit(core.read_json(args.markets), as_of)
        core.write_json(args.output, payload)
        report = Path(args.report); report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload if args.self_test else {key: value for key, value in payload.items() if key != "contract_rows"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
