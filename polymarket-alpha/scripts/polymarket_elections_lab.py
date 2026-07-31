#!/usr/bin/env python3
"""Phase-0 election taxonomy and independence audit for public Polymarket data."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
GAMMA = "https://gamma-api.polymarket.com"
ELECTIONS_TAG_ID = 144


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    return module


core = load("elections_core", ROOT / "scripts/polymarket_alpha.py")
family = load("elections_family", ROOT / "scripts/polymarket_market_family_audit.py")
public = load("elections_public", ROOT / "scripts/polymarket_public_data.py")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def subtype(question: str, tags: set[str]) -> str:
    lower = question.lower()
    if "Parlays" in tags: return "compound_or_parlay"
    if "House Elections" in tags: return "us_house_general"
    if "Senate midterms" in tags: return "us_senate_general"
    if "Governor midterms" in tags: return "us_governor_general"
    if "Primaries" in tags or "primary" in lower:
        if "House Primary" in tags: return "us_house_primary"
        if "Senate Primary" in tags: return "us_senate_primary"
        if "Governor Primary" in tags: return "us_governor_primary"
        return "us_other_primary"
    if re.search(r"announce a presidential run|announce a presidential campaign", lower): return "presidential_run_announcement"
    if "Global Elections" in tags or "World Elections" in tags: return "international_election"
    if re.search(r"control of (?:the )?(?:house|senate|congress)|majority|trifecta|number of seats", lower): return "balance_of_power"
    if re.search(r"president(?:ial)? election|presidential nominee|win the presidency", lower): return "presidential_election"
    return "other_election_related"


def identity(market: dict[str, Any]) -> tuple[str | None, str]:
    event = (market.get("events") or [{}])[0]
    if event.get("id"):
        return f"event:{event['id']}", "gamma_event_id"
    raw = market.get("_sampling_raw") or {}
    if raw.get("neg_risk_market_id"):
        return f"neg-risk:{raw['neg_risk_market_id']}", "clob_neg_risk_market_id"
    return None, "unproven"


def diagnostics(market: dict[str, Any]) -> dict[str, Any]:
    raw = market.get("_sampling_raw") or {}
    event = (market.get("events") or [{}])[0]
    description = str(event.get("description") or market.get("description") or raw.get("description") or "")
    lower = description.lower()
    named_callers = all(name in lower for name in ("associated press", "fox news", "nbc"))
    official_certification = "official certification" in lower or "official announcement of the results" in lower
    explicit_authority = bool(re.search(r"\b(resolution source|will resolve based on|according to|official certification|official announcement)\b", lower))
    vague_media = bool(re.search(r"\b(consensus of credible|overwhelming consensus|credible reporting|reputable media)\b", lower)) and not (named_callers and official_certification)
    outcomes = market.get("outcomes")
    if isinstance(outcomes, str):
        try: outcomes = json.loads(outcomes)
        except json.JSONDecodeError: outcomes = []
    tokens = raw.get("tokens") or []
    return {
        "description_present": bool(description.strip()), "explicit_resolution_authority": explicit_authority,
        "named_ap_fox_nbc_plus_certification": named_callers and official_certification,
        "vague_media_fallback": vague_media, "high_rule_ambiguity": not explicit_authority or vague_media,
        "binary_two_token_contract": isinstance(outcomes, list) and len(outcomes) == 2 and len(tokens) == 2,
    }


def audit(markets: list[dict[str, Any]], as_of: datetime) -> dict[str, Any]:
    selected = [market for market in markets if family.belongs(market, family.FAMILIES["elections"])]
    rows = []
    for market in selected:
        raw = market.get("_sampling_raw") or {}; question = str(market.get("question") or raw.get("question") or "")
        event_id, identity_source = identity(market); end = family.end_time(market)
        rows.append({
            "market_id": str(market.get("id") or market.get("conditionId") or ""), "question": question,
            "subtype": subtype(question, set(raw.get("tags") or [])), "event_cluster_id": event_id,
            "event_identity_source": identity_source, "end_time": end.isoformat() if end else None,
            "expired_but_in_live_inventory": bool(end and end < as_of), **diagnostics(market),
        })
    summaries = []
    for name in sorted({row["subtype"] for row in rows}):
        subset = [row for row in rows if row["subtype"] == name]
        proven = [row for row in subset if row["event_cluster_id"]]
        clusters = {row["event_cluster_id"] for row in proven}
        explicit = sum(row["explicit_resolution_authority"] for row in subset)
        ambiguous = sum(row["high_rule_ambiguity"] for row in subset)
        binary = sum(row["binary_two_token_contract"] for row in subset)
        expired = sum(row["expired_but_in_live_inventory"] for row in subset)
        proven_pct = len(proven) / len(subset)
        summaries.append({
            "subtype": name, "contract_count": len(subset), "proven_independent_event_count": len(clusters),
            "event_identity_proven_pct": round(100 * proven_pct, 2),
            "binary_contract_pct": round(100 * binary / len(subset), 2),
            "explicit_authority_pct": round(100 * explicit / len(subset), 2),
            "high_rule_ambiguity_pct": round(100 * ambiguous / len(subset), 2),
            "expired_but_live_count": expired,
            "phase_zero_candidate": len(clusters) >= 30 and proven_pct >= 0.95 and binary == len(subset) and expired == 0 and explicit / len(subset) >= 0.80 and ambiguous / len(subset) <= 0.20,
        })
    summaries.sort(key=lambda row: (row["phase_zero_candidate"], row["proven_independent_event_count"], row["explicit_authority_pct"]), reverse=True)
    candidates = [row for row in summaries if row["phase_zero_candidate"]]
    identity_counts = Counter(row["event_identity_source"] for row in rows)
    return {
        "schema_version": "polymarket-elections-taxonomy-audit-v1", "created_at": core.now_iso(),
        "inventory_as_of": as_of.isoformat(), "family": "elections", "contract_count": len(rows),
        "event_identity_source_counts": dict(identity_counts),
        "proven_independent_event_count": len({row["event_cluster_id"] for row in rows if row["event_cluster_id"]}),
        "subtypes": summaries, "selected_subtype_for_history_research": candidates[0]["subtype"] if candidates else None,
        "selection_is_alpha_evidence": False,
        "model_status": "blocked_pending_terminal_history_external_features_and_cutoff_prices" if candidates else "blocked_no_subtype_passes_phase_zero",
        "final_holdout_inspected": False, "paper_entry_eligible": False, "contract_rows": rows,
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }


def strict_governor_race(event: dict[str, Any]) -> dict[str, Any] | None:
    title = str(event.get("title") or "")
    if not re.search(r"\b(governor|gubernatorial)\b", title, re.I): return None
    if re.search(r"\b(primary|nominee|nomination|drops? out|approval|margin|turnout|endorse|debate)\b", title, re.I): return None
    if not re.search(r"\b(election|winner|wins?)\b", title, re.I): return None
    markets = event.get("markets") or []
    terminal, winners = [], []
    for market in markets:
        resolution = public.infer_resolution(market)
        if resolution.get("winning_outcome") is None: continue
        terminal.append({
            "market_id": str(market.get("id") or ""), "question": market.get("question"),
            "winning_outcome": resolution["winning_outcome"], "condition_id": market.get("conditionId"),
        })
        if str(resolution["winning_outcome"]).lower() in {"yes", "1"}:
            winners.append(str(market.get("question") or ""))
    if not terminal: return None
    combined = {**event, "description": str(event.get("description") or "")}
    rule = diagnostics(combined)
    return {
        "event_id": str(event.get("id") or ""), "event_slug": event.get("slug"), "title": title,
        "start_time": event.get("startDate"), "end_time": event.get("endDate"),
        "market_count": len(markets), "terminal_market_count": len(terminal),
        "yes_winner_questions": winners, "terminal_markets": terminal,
        "rule_diagnostics": rule,
    }


def load_history_events(directory: Path) -> list[dict[str, Any]]:
    events = {}
    for path in sorted((directory / "pages").glob("page-*.json")):
        for event in core.read_json(path).get("events") or []: events[str(event.get("id"))] = event
    return sorted(events.values(), key=lambda row: int(row.get("id") or 0))


def discover_history(directory: Path, max_pages: int, wall_clock_seconds: float) -> dict[str, Any]:
    (directory / "pages").mkdir(parents=True, exist_ok=True)
    contract = {"endpoint": f"{GAMMA}/events/keyset", "closed": True, "tag_id": ELECTIONS_TAG_ID, "limit": 100}
    state_path, log_path = directory / "state.json", directory / "request-log.json"
    state = core.read_json(state_path) if state_path.exists() else {"schema_version": "polymarket-elections-history-state-v1", "contract": contract, "next_cursor": None, "terminal_cursor_proven": False, "pages_fetched": 0}
    if state.get("contract") != contract: raise ValueError("elections history cache contract mismatch")
    requests = core.read_json(log_path) if log_path.exists() else []
    core.write_json(state_path, state); core.write_json(log_path, requests)
    started = time.monotonic(); fetched = 0
    while not state["terminal_cursor_proven"] and fetched < max_pages and time.monotonic() - started < wall_clock_seconds:
        page_limit = int(state.get("adaptive_page_size") or 100)
        params: dict[str, Any] = {"closed": "true", "tag_id": ELECTIONS_TAG_ID, "limit": page_limit}
        if state.get("next_cursor"): params["after_cursor"] = state["next_cursor"]
        url = f"{GAMMA}/events/keyset?{urlencode(params)}"
        try:
            payload = public.get_json(url, timeout=25, retries=2); events = payload.get("events")
            if not isinstance(events, list): raise ValueError("keyset response missing events")
            number = int(state["pages_fetched"]) + 1; cursor = payload.get("next_cursor")
            core.write_json(directory / "pages" / f"page-{number:04d}.json", {"page_number": number, "request_url": url, "events": events, "next_cursor": cursor})
            requests.append({"page_number": number, "url": url, "status": "ok", "rows": len(events), "terminal": not bool(cursor), "created_at": core.now_iso()})
            state.update({"pages_fetched": number, "next_cursor": cursor, "terminal_cursor_proven": not bool(cursor), "updated_at": core.now_iso()})
            core.write_json(log_path, requests); core.write_json(state_path, state); fetched += 1
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()})
            if page_limit > 20:
                fallback_params = {**params, "limit": 20}; fallback_url = f"{GAMMA}/events/keyset?{urlencode(fallback_params)}"
                try:
                    payload = public.get_json(fallback_url, timeout=25, retries=2); events = payload.get("events")
                    if not isinstance(events, list): raise ValueError("fallback keyset response missing events")
                    number = int(state["pages_fetched"]) + 1; cursor = payload.get("next_cursor")
                    core.write_json(directory / "pages" / f"page-{number:04d}.json", {"page_number": number, "request_url": fallback_url, "events": events, "next_cursor": cursor})
                    requests.append({"page_number": number, "url": fallback_url, "status": "ok_fallback", "rows": len(events), "terminal": not bool(cursor), "created_at": core.now_iso()})
                    state.update({"pages_fetched": number, "next_cursor": cursor, "terminal_cursor_proven": not bool(cursor), "adaptive_page_size": 20, "updated_at": core.now_iso()})
                    core.write_json(log_path, requests); core.write_json(state_path, state); fetched += 1; continue
                except Exception as fallback_exc:
                    requests.append({"url": fallback_url, "status": "failed_fallback", "error": f"{type(fallback_exc).__name__}:{fallback_exc}", "created_at": core.now_iso()})
            core.write_json(log_path, requests); core.write_json(state_path, state); break
    events = load_history_events(directory)
    eligible = [row for event in events if (row := strict_governor_race(event))]
    core.write_json(directory / "strict-settled-governor-races.json", eligible)
    relative = ["state.json", "request-log.json", "strict-settled-governor-races.json"] + [str(path.relative_to(directory)) for path in sorted((directory / "pages").glob("page-*.json"))]
    manifest = {
        "schema_version": "polymarket-elections-governor-history-manifest-v1", "created_at": core.now_iso(),
        "official_source": f"{GAMMA}/events/keyset", "elections_tag_id": ELECTIONS_TAG_ID,
        "pages_fetched": state["pages_fetched"], "terminal_cursor_proven": state["terminal_cursor_proven"],
        "events_fetched": len(events), "strict_settled_governor_races": len(eligible),
        "unambiguous_rule_races": sum(not row["rule_diagnostics"]["high_rule_ambiguity"] for row in eligible),
        "minimum_total_for_frozen_60_20_20_split": 150,
        "data_status": "ok" if state["terminal_cursor_proven"] else "degraded",
        "model_sample_gate_met": state["terminal_cursor_proven"] and len(eligible) >= 150,
        "files": [{"path": name, "bytes": (directory / name).stat().st_size, "sha256": sha256(directory / name)} for name in relative],
        "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(directory / "manifest.json", manifest); return manifest


def markdown(payload: dict[str, Any]) -> str:
    lines = ["# Polymarket Elections Taxonomy Audit", "",
             f"- Live inventory: {payload['contract_count']} contracts",
             f"- Proven event clusters: {payload['proven_independent_event_count']}",
             f"- Selected subtype for history research: `{payload['selected_subtype_for_history_research']}`",
             f"- Model status: `{payload['model_status']}`",
             "- Event counts use Gamma event IDs or public CLOB negative-risk market IDs; ungrouped conditions do not count as independent events.", "",
             "| Subtype | Contracts | Proven events | Identity proven | Binary | Authority | Ambiguity | Expired live | Candidate |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for row in payload["subtypes"]:
        lines.append(f"| {row['subtype']} | {row['contract_count']} | {row['proven_independent_event_count']} | {row['event_identity_proven_pct']:.1f}% | {row['binary_contract_pct']:.1f}% | {row['explicit_authority_pct']:.1f}% | {row['high_rule_ambiguity_pct']:.1f}% | {row['expired_but_live_count']} | {str(row['phase_zero_candidate']).lower()} |")
    lines.extend(["", "Passing Phase 0 only authorizes terminal settled-history collection. It does not select a probability model or permit paper entry.", ""])
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    assert subtype("Will the Democratic Party win the CA-13 House seat?", {"House Elections"}) == "us_house_general"
    assert subtype("Will Person win the primary?", {"Primaries", "Governor Primary"}) == "us_governor_primary"
    event_market = {"events": [{"id": "e1"}], "_sampling_raw": {"neg_risk_market_id": "n1"}}
    assert identity(event_market) == ("event:e1", "gamma_event_id")
    neg_market = {"events": [], "_sampling_raw": {"neg_risk_market_id": "n1"}}
    assert identity(neg_market) == ("neg-risk:n1", "clob_neg_risk_market_id")
    named = {"description": "The resolution source is the Associated Press, Fox News, and NBC; otherwise official certification."}
    assert diagnostics(named)["high_rule_ambiguity"] is False
    return {"status": "pass", "tests": ["house_taxonomy", "primary_taxonomy", "event_id_precedence", "negative_risk_grouping", "named_source_rule"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", default=str(ROOT / "cache/current_validation_snapshot/markets.json"))
    parser.add_argument("--snapshot-manifest", default=str(ROOT / "cache/current_validation_snapshot/snapshot-manifest.json"))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-elections-taxonomy-audit.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_ELECTIONS_TAXONOMY_AUDIT.md"))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--discover-history", action="store_true")
    parser.add_argument("--history-output-dir", default=str(ROOT / "cache/current_elections_governor_history"))
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--wall-clock-seconds", type=float, default=240)
    args = parser.parse_args()
    if args.self_test: payload = self_test()
    elif args.discover_history: payload = discover_history(Path(args.history_output_dir), args.max_pages, args.wall_clock_seconds)
    else:
        manifest = core.read_json(args.snapshot_manifest)
        if manifest.get("terminal_cursor_proven") is not True: raise ValueError("snapshot has no terminal cursor proof")
        as_of = core.parse_iso(manifest.get("created_at")) or datetime.now(timezone.utc)
        payload = audit(core.read_json(args.markets), as_of); core.write_json(args.output, payload)
        report = Path(args.report); report.parent.mkdir(parents=True, exist_ok=True); report.write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload if args.self_test else {k: v for k, v in payload.items() if k != "contract_rows"}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
