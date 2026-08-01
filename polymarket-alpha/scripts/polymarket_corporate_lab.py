#!/usr/bin/env python3
"""Corporate-events Phase-0 taxonomy and resolution-contract audit."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path); module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("corporate_core", ROOT / "scripts/polymarket_alpha.py")
family = load("corporate_family", ROOT / "scripts/polymarket_market_family_audit.py")
public = load("corporate_public", ROOT / "scripts/polymarket_public_data.py")
GAMMA = "https://gamma-api.polymarket.com"; EARNINGS_TAG_ID = 1013


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def tag_values(market: dict[str, Any]) -> set[str]:
    return set((market.get("_sampling_raw") or {}).get("tags") or [])


def event_type(question: str, tags: set[str]) -> str:
    text = question.lower()
    if "Earnings" in tags or re.search(r"\bq[1-4]\b|revenue|earnings|gross margin|capex|backlog|production|fees|net interest income|provision for credit losses", text): return "earnings_kpi"
    if re.search(r"acquir|merger|deal close|acquisition", text): return "ma_completion"
    if re.search(r"bankrupt|chapter 11|insolven", text): return "bankruptcy_or_distress"
    if re.search(r"underwriter|public ticker|ticker be", text): return "ipo_metadata"
    if re.search(r"\bipo\b|initial public offering|go public", text):
        if re.search(r"market cap|valuation|raise |higher ipo", text): return "ipo_valuation_or_size"
        return "ipo_timing"
    if "Privates" in tags or re.search(r"valuation hit|higher valuation", text): return "private_valuation"
    return "other_corporate_event"


def source_authority(description: str) -> str:
    text = description.lower()
    if re.search(r"sec(?: filing| website)?|nasdaq|nyse|official (?:company|announcement|filing)|investor relations|company filings", text): return "official_regulatory_or_company"
    if re.search(r"closing price|market close|shares outstanding|public market", text): return "public_market_calculation"
    if re.search(r"caplight|forge|hiive|notice\.co|secondary market|private market", text): return "named_private_market_source"
    if "consensus of credible reporting" in text or "credible reporting" in text: return "media_consensus"
    if "resolution source" in text or "resolve based on" in text: return "named_other_source"
    return "source_unspecified"


def deadline_class(question: str) -> str:
    text = question.lower()
    if re.search(r"q[1-4]\b", text): return "quarterly_release"
    if re.search(r"september 15|september 30|august 31|october 31|december 31|before 2027|in 2026", text): return "fixed_calendar_deadline"
    if "ipo day" in text: return "ipo_day_close"
    return "other_deadline"


def rule_is_clear(kind: str, authority: str, description: str) -> bool:
    text = description.lower()
    if authority == "source_unspecified": return False
    if kind == "ipo_timing": return bool(re.search(r"publicly traded|ipo|initial public offering", text) and re.search(r"deadline|11:59|december|september|august|october", text))
    if kind == "ipo_valuation_or_size": return bool(re.search(r"market cap|valuation|shares outstanding", text) and re.search(r"market close|closing", text))
    if kind == "private_valuation": return authority == "named_private_market_source" and bool(re.search(r"high|low|hit|trade|price", text))
    if kind == "earnings_kpi": return authority in {"official_regulatory_or_company", "named_other_source"} and bool(re.search(r"quarter|q[1-4]|earnings|filing", text))
    return authority in {"official_regulatory_or_company", "public_market_calculation", "named_private_market_source"}


def merged_enrichment() -> dict[str, Any]:
    mapping = {}
    for name in ("current_family_identity_enrichment.json", "current_uncovered_identity_enrichment.json"):
        path = ROOT / "cache" / name
        if path.exists(): mapping.update(core.read_json(path).get("by_condition_id") or {})
    return {"by_condition_id": mapping}


def audit(markets: list[dict[str, Any]], enrichment: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    rows = []
    for market in markets:
        if not family.belongs(market, family.FAMILIES["corporate_events"]): continue
        raw = market.get("_sampling_raw") or {}; tags = tag_values(market); question = str(market.get("question") or raw.get("question") or ""); description = str(market.get("description") or raw.get("description") or ""); kind = event_type(question, tags); authority = source_authority(description); end = family.end_time(market)
        rows.append({"market_id": str(market.get("id") or market.get("conditionId") or ""), "event_id": family.event_identity(market, enrichment), "question": question, "event_type": kind, "source_authority": authority, "deadline_class": deadline_class(question), "end_time": end.isoformat() if end else None, "within_30d": bool(end and as_of <= end <= as_of + timedelta(days=30)), "expired_but_live": bool(end and end < as_of), "rule_clear": rule_is_clear(kind, authority, description)})
    groups = []
    for key in sorted({(row["event_type"], row["source_authority"], row["deadline_class"]) for row in rows}):
        selected = [row for row in rows if (row["event_type"], row["source_authority"], row["deadline_class"]) == key]; events = {row["event_id"] for row in selected if row["event_id"]}; clean = {row["event_id"] for row in selected if row["event_id"] and row["rule_clear"] and not row["expired_but_live"]}; near = {row["event_id"] for row in selected if row["event_id"] and row["within_30d"]}; ratio = len(clean) / len(events) if events else 0.0
        groups.append({"event_type": key[0], "source_authority": key[1], "deadline_class": key[2], "contract_count": len(selected), "independent_event_count": len(events), "clean_event_count": len(clean), "near_term_30d_event_count": len(near), "phase_zero_history_candidate": bool(events and len(events) >= 30 and ratio >= .90 and key[0] != "other_corporate_event")})
    groups.sort(key=lambda row: (row["phase_zero_history_candidate"], row["near_term_30d_event_count"], row["independent_event_count"]), reverse=True); candidates = [row for row in groups if row["phase_zero_history_candidate"]]
    return {"schema_version": "polymarket-corporate-taxonomy-audit-v1", "created_at": core.now_iso(), "inventory_as_of": as_of.isoformat(), "contract_count": len(rows), "independent_event_count": len({row["event_id"] for row in rows if row["event_id"]}), "groups": groups, "selected_group_for_history_research": candidates[0] if candidates else None, "model_status": "blocked_pending_terminal_history_and_point_in_time_features" if candidates else "blocked_no_homogeneous_live_group", "paper_entry_eligible": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}


def load_events(directory: Path) -> list[dict[str, Any]]:
    events = {}
    for page in sorted((directory / "pages").glob("page-*.json")):
        for event in core.read_json(page).get("events") or []: events[str(event.get("id"))] = event
    return sorted(events.values(), key=lambda row: int(row.get("id") or 0))


def strict_earnings_event(event: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    title = str(event.get("title") or ""); markets = event.get("markets") or []
    if "beat quarterly earnings" not in title.lower() or len(markets) != 1: return None, "not_single_earnings_beat"
    market = markets[0]; description = str(market.get("description") or event.get("description") or ""); lower = description.lower(); result = public.infer_resolution(market)
    if result.get("winning_outcome") not in {"Yes", "No"}: return None, "not_terminal"
    if not all(term in lower for term in ("as of market creation", "street consensus estimate", "official earnings documents", "seekingalpha")): return None, "contract_not_comparable"
    ticker_match = re.search(r"\(([A-Z.]{1,8})\)", title); estimate_match = re.search(r"consensus estimate.*?(-?\$?\d+(?:\.\d+)?)", description, re.I | re.S)
    if not ticker_match or not estimate_match: return None, "ticker_or_strike_missing"
    strike_text = estimate_match.group(1).replace("$", "")
    try: strike = float(strike_text)
    except ValueError: return None, "strike_invalid"
    basis = "non_gaap" if "non-gaap eps" in lower else "gaap" if "gaap eps" in lower else "unknown"
    if basis == "unknown": return None, "eps_basis_missing"
    end = market.get("endDate") or event.get("endDate")
    return {"event_id": str(event.get("id") or ""), "market_id": str(market.get("id") or ""), "condition_id": str(market.get("conditionId") or ""), "title": title, "ticker": ticker_match.group(1), "eps_basis": basis, "consensus_strike": strike, "market_created_at": market.get("createdAt") or market.get("startDate"), "earnings_release_at": end, "winning_outcome": result["winning_outcome"], "tokens": public.token_map(market), "contract_version": "earnings_beat_consensus_as_of_market_creation_official_documents_with_seekingalpha_fallback"}, None


def discover_history(directory: Path, max_pages: int, wall_clock_seconds: float) -> dict[str, Any]:
    (directory / "pages").mkdir(parents=True, exist_ok=True); contract = {"endpoint": f"{GAMMA}/events/keyset", "closed": True, "tag_id": EARNINGS_TAG_ID, "limit": 20}; state_path = directory / "state.json"; log_path = directory / "request-log.json"
    state = core.read_json(state_path) if state_path.exists() else {"schema_version": "polymarket-corporate-earnings-history-state-v1", "contract": contract, "next_cursor": None, "terminal_cursor_proven": False, "pages_fetched": 0}
    if state.get("contract") != contract: raise ValueError("corporate earnings history contract mismatch")
    requests = core.read_json(log_path) if log_path.exists() else []; core.write_json(state_path, state); core.write_json(log_path, requests); started = time.monotonic(); fetched = 0
    while not state["terminal_cursor_proven"] and fetched < max_pages and time.monotonic() - started < wall_clock_seconds:
        params = {"closed": "true", "tag_id": EARNINGS_TAG_ID, "limit": 20}
        if state.get("next_cursor"): params["after_cursor"] = state["next_cursor"]
        from urllib.parse import urlencode
        url = f"{GAMMA}/events/keyset?{urlencode(params)}"
        try:
            payload = public.get_json(url, timeout=25, retries=2); events = payload.get("events")
            if not isinstance(events, list): raise ValueError("keyset response missing events")
            number = int(state["pages_fetched"]) + 1; cursor = payload.get("next_cursor"); core.write_json(directory / "pages" / f"page-{number:04d}.json", {"page_number": number, "request_url": url, "events": events, "next_cursor": cursor}); requests.append({"page_number": number, "url": url, "status": "ok", "rows": len(events), "terminal": not bool(cursor), "created_at": core.now_iso()}); state.update({"pages_fetched": number, "next_cursor": cursor, "terminal_cursor_proven": not bool(cursor), "updated_at": core.now_iso()}); core.write_json(log_path, requests); core.write_json(state_path, state); fetched += 1
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()}); core.write_json(log_path, requests); break
    events = load_events(directory); strict = []; exclusions = {}
    for event in events:
        row, reason = strict_earnings_event(event)
        if row: strict.append(row)
        else: exclusions[reason or "unknown"] = exclusions.get(reason or "unknown", 0) + 1
    core.write_json(directory / "strict-earnings-events.json", strict); relative = ["state.json", "request-log.json", "strict-earnings-events.json"] + [str(path.relative_to(directory)) for path in sorted((directory / "pages").glob("page-*.json"))]
    release_dates = {str(row.get("earnings_release_at") or "")[:10] for row in strict if row.get("earnings_release_at")}
    manifest = {"schema_version": "polymarket-corporate-earnings-history-v1", "created_at": core.now_iso(), "official_source": f"{GAMMA}/events/keyset", "tag_id": EARNINGS_TAG_ID, "pages_fetched": state["pages_fetched"], "terminal_cursor_proven": state["terminal_cursor_proven"], "events_fetched": len(events), "strict_settled_earnings_events": len(strict), "independent_tickers": len({row["ticker"] for row in strict}), "distinct_release_dates": len(release_dates), "eps_basis_counts": {basis: sum(row["eps_basis"] == basis for row in strict) for basis in {row["eps_basis"] for row in strict}}, "exclusion_reason_counts": exclusions, "point_in_time_consensus_strike_embedded": True, "minimum_total_for_frozen_split": 150, "model_sample_gate_met": len(strict) >= 150, "data_status": "ok" if state["terminal_cursor_proven"] else "degraded", "files": [{"path": name, "bytes": (directory / name).stat().st_size, "sha256": sha256(directory / name)} for name in relative], "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}; core.write_json(directory / "manifest.json", manifest); return manifest


def markdown(payload: dict[str, Any], history: dict[str, Any] | None = None) -> str:
    lines = ["# Polymarket Corporate Events Phase-0 Audit", "", f"- Contracts / events: {payload['contract_count']} / {payload['independent_event_count']}", f"- Selected history group: `{payload['selected_group_for_history_research']}`", f"- Status: `{payload['model_status']}`", "", "| Event type | Source | Deadline | Contracts | Events | Clean | <=30d | Candidate |", "|---|---|---|---:|---:|---:|---:|---|"]
    for row in payload["groups"]:
        lines.append(f"| {row['event_type']} | {row['source_authority']} | {row['deadline_class']} | {row['contract_count']} | {row['independent_event_count']} | {row['clean_event_count']} | {row['near_term_30d_event_count']} | {str(row['phase_zero_history_candidate']).lower()} |")
    if history: lines.extend(["", f"Terminal Earnings-tag history: {history['events_fetched']} events; strict comparable settled contracts: {history['strict_settled_earnings_events']}; model sample gate: `{str(history['model_sample_gate_met']).lower()}`."])
    lines.extend(["", "IPO timing, IPO valuation, private valuation, earnings KPIs, M&A, distress and IPO metadata remain separate. Contract count is never substituted for independent events.", ""]); return "\n".join(lines)


def self_test() -> dict[str, Any]:
    assert event_type("Anthropic IPO before 2027?", {"IPOs"}) == "ipo_timing"
    assert event_type("OpenAI IPO closing market cap above $1T?", {"IPOs"}) == "ipo_valuation_or_size"
    assert event_type("Will Stripe's valuation hit (HIGH) $200B?", {"Privates"}) == "private_valuation"
    return {"status": "pass", "tests": ["ipo_timing", "ipo_valuation", "private_valuation"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--discover-history", action="store_true"); parser.add_argument("--max-pages", type=int, default=150); parser.add_argument("--wall-clock-seconds", type=float, default=240); parser.add_argument("--markets", default=str(ROOT / "cache/current_validation_snapshot/markets.json")); parser.add_argument("--snapshot-manifest", default=str(ROOT / "cache/current_validation_snapshot/snapshot-manifest.json")); parser.add_argument("--history-dir", default=str(ROOT / "cache/current_corporate_earnings_history")); parser.add_argument("--output", default=str(ROOT / "experiments/current-corporate-taxonomy-audit.json")); parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_CORPORATE_TAXONOMY_AUDIT.md")); args = parser.parse_args()
    if args.self_test: payload = self_test()
    else:
        snapshot = core.read_json(args.snapshot_manifest)
        if snapshot.get("terminal_cursor_proven") is not True: raise ValueError("snapshot is not terminal")
        payload = audit(core.read_json(args.markets), merged_enrichment(), core.parse_iso(snapshot.get("created_at")) or datetime.now(timezone.utc)); history_dir = Path(args.history_dir); history = discover_history(history_dir, args.max_pages, args.wall_clock_seconds) if args.discover_history else (core.read_json(history_dir / "manifest.json") if (history_dir / "manifest.json").exists() else None); payload["history"] = history; core.write_json(args.output, payload); Path(args.report).write_text(markdown(payload, history), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
