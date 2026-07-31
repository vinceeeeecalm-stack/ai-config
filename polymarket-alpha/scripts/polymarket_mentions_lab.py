#!/usr/bin/env python3
"""Public-only Mentions event taxonomy and terminal settled-history gate."""
from __future__ import annotations

import argparse, hashlib, importlib.util, json, re, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
GAMMA = "https://gamma-api.polymarket.com"; MENTIONS_TAG_ID = 100343; PAGE_LIMIT = 20

def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path); module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module

core = load("mentions_core", ROOT / "scripts/polymarket_alpha.py")
family = load("mentions_family", ROOT / "scripts/polymarket_market_family_audit.py")
public = load("mentions_public", ROOT / "scripts/polymarket_public_data.py")

SUBJECTS = [("Trump", r"\b(?:donald )?trump\b"), ("Biden", r"\b(?:joe )?biden\b"), ("Harris", r"\b(?:kamala |kamala harris|harris)\b"),
            ("Elon Musk", r"\b(?:elon|musk)\b"), ("Leavitt", r"\bleavitt\b"), ("Starmer", r"\bstarmer\b"),
            ("MrBeast", r"\bmrbeast\b"), ("Joe Rogan", r"\bjoe rogan\b")]

def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def subject(text: str) -> str:
    for name, pattern in SUBJECTS:
        if re.search(pattern, text, re.I): return name
    return "Other"

def event_type(text: str) -> str:
    lower = text.lower()
    if re.search(r"earnings call", lower): return "earnings_call"
    if re.search(r"post .* on x|tweet|truth social|post count", lower): return "social_post"
    if re.search(r"headlines?", lower): return "news_headline"
    if re.search(r"world cup|match|announcers?|ufc|grand finals?|game broadcast", lower): return "sports_broadcast"
    if re.search(r"episode|house of the dragon|love island|big brother|rick and morty", lower): return "entertainment_episode"
    if re.search(r"podcast|joe rogan|youtube video", lower): return "podcast_or_video"
    if re.search(r"briefing|speech|debate|interview|press conference|pmq|rally|address", lower): return "political_appearance"
    return "other_mentions"

def source_type(text: str) -> str:
    lower = text.lower()
    if "xtracker" in lower: return "xtracker"
    if "truth social" in lower: return "truth_social"
    if re.search(r"official (?:video|transcript|recording|livestream)|whitehouse\.gov|youtube", lower): return "official_recording_or_transcript"
    if "earnings call" in lower: return "earnings_call_recording"
    if re.search(r"broadcast|announcer", lower): return "official_broadcast"
    if re.search(r"transcript|video recording|livestream", lower): return "public_recording_or_transcript"
    return "source_unspecified"

def diagnostics(text: str) -> dict[str, Any]:
    stype = source_type(text); lower = text.lower()
    counting = bool(re.search(r"\b(mention|mentions|say|says|said|post|posts|word|phrase)\b", lower))
    vague = bool(re.search(r"credible reporting|consensus of credible|preponderance of evidence", lower))
    subjective = bool(re.search(r"substantially the same|spirit of the phrase|similar wording", lower))
    return {"source_type": stype, "source_contract_present": stype != "source_unspecified",
            "counting_rule_present": counting, "media_judgment_required": vague, "subjective_equivalence_allowed": subjective,
            "high_rule_ambiguity": stype == "source_unspecified" or not counting or vague or subjective}

def live_audit(markets: list[dict[str, Any]], enrichment: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    selected = [m for m in markets if family.belongs(m, family.FAMILIES["mentions"])]
    rows = []
    for market in selected:
        raw = market.get("_sampling_raw") or {}; event = (market.get("events") or [{}])[0]
        question = str(market.get("question") or raw.get("question") or ""); desc = str(event.get("description") or market.get("description") or raw.get("description") or "")
        combined = f"{question}\n{desc}"; identity = family.event_identity(market, enrichment); end = family.end_time(market)
        rows.append({"market_id": str(market.get("id") or market.get("conditionId") or ""), "event_id": identity,
                     "question": question, "event_type": event_type(combined), "subject": subject(question),
                     "end_time": end.isoformat() if end else None, "expired_but_live": bool(end and end < as_of), **diagnostics(combined)})
    groups = []
    for key in sorted({(r["event_type"], r["subject"], r["source_type"]) for r in rows}):
        subset = [r for r in rows if (r["event_type"], r["subject"], r["source_type"]) == key]; events = {r["event_id"] for r in subset if r["event_id"]}
        groups.append({"event_type": key[0], "subject": key[1], "source_type": key[2], "contract_count": len(subset),
                       "independent_event_count": len(events), "identity_coverage_pct": round(100 * sum(bool(r["event_id"]) for r in subset) / len(subset), 2),
                       "clean_contract_count": sum(not r["high_rule_ambiguity"] and not r["expired_but_live"] for r in subset),
                       "phase_zero_candidate": len(events) >= 30 and all(r["event_id"] and not r["high_rule_ambiguity"] and not r["expired_but_live"] for r in subset)})
    groups.sort(key=lambda r: (r["phase_zero_candidate"], r["independent_event_count"], r["clean_contract_count"]), reverse=True)
    candidates = [r for r in groups if r["phase_zero_candidate"]]
    return {"schema_version": "polymarket-mentions-live-taxonomy-v1", "created_at": core.now_iso(), "inventory_as_of": as_of.isoformat(),
            "family": "mentions", "contract_count": len(rows), "independent_event_count": len({r["event_id"] for r in rows if r["event_id"]}),
            "groups": groups, "selected_group_for_history_research": candidates[0] if candidates else None,
            "model_status": "blocked_pending_terminal_history", "contract_rows": rows,
            "paper_entry_eligible": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
            "live_orders_enabled": False, "private_api_used": False}

def load_events(directory: Path) -> list[dict[str, Any]]:
    events = {}
    for path in sorted((directory / "pages").glob("page-*.json")):
        for event in core.read_json(path).get("events") or []: events[str(event.get("id"))] = event
    return sorted(events.values(), key=lambda r: int(r.get("id") or 0))

def settled_row(event: dict[str, Any]) -> dict[str, Any] | None:
    terminal = []
    for market in event.get("markets") or []:
        resolution = public.infer_resolution(market)
        if resolution.get("winning_outcome") is not None: terminal.append({"market_id": str(market.get("id") or ""), "winning_outcome": resolution["winning_outcome"]})
    if not terminal: return None
    text = f"{event.get('title') or ''}\n{event.get('description') or ''}"
    return {"event_id": str(event.get("id") or ""), "title": event.get("title"), "event_type": event_type(text), "subject": subject(str(event.get("title") or "")),
            "end_time": event.get("endDate"), "terminal_market_count": len(terminal), "terminal_markets": terminal, **diagnostics(text)}

def discover_history(directory: Path, max_pages: int, wall_clock_seconds: float) -> dict[str, Any]:
    (directory / "pages").mkdir(parents=True, exist_ok=True); contract = {"endpoint": f"{GAMMA}/events/keyset", "closed": True, "tag_id": MENTIONS_TAG_ID, "limit": PAGE_LIMIT}
    state_path, log_path = directory / "state.json", directory / "request-log.json"
    state = core.read_json(state_path) if state_path.exists() else {"schema_version": "polymarket-mentions-history-state-v1", "contract": contract, "next_cursor": None, "terminal_cursor_proven": False, "pages_fetched": 0}
    if state.get("contract") != contract: raise ValueError("mentions history cache contract mismatch")
    requests = core.read_json(log_path) if log_path.exists() else []; core.write_json(state_path, state); core.write_json(log_path, requests); started = time.monotonic(); fetched = 0
    while not state["terminal_cursor_proven"] and fetched < max_pages and time.monotonic() - started < wall_clock_seconds:
        params: dict[str, Any] = {"closed": "true", "tag_id": MENTIONS_TAG_ID, "limit": PAGE_LIMIT}
        if state.get("next_cursor"): params["after_cursor"] = state["next_cursor"]
        url = f"{GAMMA}/events/keyset?{urlencode(params)}"
        try:
            payload = public.get_json(url, timeout=25, retries=2); events = payload.get("events")
            if not isinstance(events, list): raise ValueError("keyset response missing events")
            number = int(state["pages_fetched"]) + 1; cursor = payload.get("next_cursor")
            core.write_json(directory / "pages" / f"page-{number:04d}.json", {"page_number": number, "request_url": url, "events": events, "next_cursor": cursor})
            requests.append({"page_number": number, "url": url, "status": "ok", "rows": len(events), "terminal": not bool(cursor), "created_at": core.now_iso()})
            state.update({"pages_fetched": number, "next_cursor": cursor, "terminal_cursor_proven": not bool(cursor), "updated_at": core.now_iso()}); core.write_json(log_path, requests); core.write_json(state_path, state); fetched += 1
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()}); core.write_json(log_path, requests); break
    events = load_events(directory); rows = [r for event in events if (r := settled_row(event))]; core.write_json(directory / "settled-mention-events.json", rows)
    groups = []
    for key in sorted({(r["event_type"], r["subject"], r["source_type"]) for r in rows}):
        subset = [r for r in rows if (r["event_type"], r["subject"], r["source_type"]) == key]; strict = [r for r in subset if not r["high_rule_ambiguity"]]
        groups.append({"event_type": key[0], "subject": key[1], "source_type": key[2], "settled_event_count": len(subset), "strict_event_count": len(strict), "model_sample_gate_met": len(strict) >= 150})
    groups.sort(key=lambda r: (r["model_sample_gate_met"], r["strict_event_count"]), reverse=True)
    relative = ["state.json", "request-log.json", "settled-mention-events.json"] + [str(p.relative_to(directory)) for p in sorted((directory / "pages").glob("page-*.json"))]
    manifest = {"schema_version": "polymarket-mentions-history-manifest-v1", "created_at": core.now_iso(), "official_source": f"{GAMMA}/events/keyset", "mentions_tag_id": MENTIONS_TAG_ID,
                "pages_fetched": state["pages_fetched"], "terminal_cursor_proven": state["terminal_cursor_proven"], "events_fetched": len(events), "settled_mention_events": len(rows),
                "homogeneous_groups": groups, "minimum_total_for_frozen_60_20_20_split": 150,
                "selected_group_for_model_research": next((r for r in groups if r["model_sample_gate_met"]), None), "model_sample_gate_met": any(r["model_sample_gate_met"] for r in groups),
                "data_status": "ok" if state["terminal_cursor_proven"] else "degraded", "files": [{"path": n, "bytes": (directory/n).stat().st_size, "sha256": sha256(directory/n)} for n in relative],
                "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}
    core.write_json(directory / "manifest.json", manifest); return manifest

def markdown(live, history):
    lines = ["# Polymarket Mentions Data Gate", "", f"- Live: {live['contract_count']} contracts / {live['independent_event_count']} event IDs", f"- Live candidate: `{live['selected_group_for_history_research']}`", "- No probability or paper permission is emitted.", "",
             "| Event type | Subject | Source | Contracts | Events | Clean | Candidate |", "|---|---|---|---:|---:|---:|---|"]
    for r in live["groups"]: lines.append(f"| {r['event_type']} | {r['subject']} | {r['source_type']} | {r['contract_count']} | {r['independent_event_count']} | {r['clean_contract_count']} | {str(r['phase_zero_candidate']).lower()} |")
    if history: lines.extend(["", f"Terminal history: {history['events_fetched']} events; model sample gate `{str(history['model_sample_gate_met']).lower()}`.", ""])
    return "\n".join(lines)

def self_test():
    assert event_type("Trump speech and press conference") == "political_appearance" and subject("Donald Trump") == "Trump"
    assert event_type("JPMorgan earnings call") == "earnings_call" and source_type("official video recording") == "official_recording_or_transcript"
    assert diagnostics("Will Trump say X? Source is an official video recording.")["high_rule_ambiguity"] is False
    return {"status":"pass","tests":["event_taxonomy","subject_isolation","source_contract","ambiguity_gate"]}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--self-test",action="store_true");p.add_argument("--discover-history",action="store_true");p.add_argument("--max-pages",type=int,default=100);p.add_argument("--wall-clock-seconds",type=float,default=240)
    p.add_argument("--markets",default=str(ROOT/"cache/current_validation_snapshot/markets.json"));p.add_argument("--snapshot-manifest",default=str(ROOT/"cache/current_validation_snapshot/snapshot-manifest.json"));p.add_argument("--identity-enrichment",default=str(ROOT/"cache/current_family_identity_enrichment.json"));p.add_argument("--history-dir",default=str(ROOT/"cache/current_mentions_history"));p.add_argument("--output",default=str(ROOT/"experiments/current-mentions-taxonomy-audit.json"));p.add_argument("--report",default=str(ROOT/"reports/CURRENT_MENTIONS_DATA_GATE.md"));a=p.parse_args()
    if a.self_test: payload=self_test()
    else:
        manifest=core.read_json(a.snapshot_manifest); enrichment=core.read_json(a.identity_enrichment)
        if manifest.get("terminal_cursor_proven") is not True: raise ValueError("snapshot has no terminal cursor proof")
        live=live_audit(core.read_json(a.markets),enrichment,core.parse_iso(manifest.get("created_at")) or datetime.now(timezone.utc));core.write_json(a.output,live)
        hp=Path(a.history_dir); history=discover_history(hp,a.max_pages,a.wall_clock_seconds) if a.discover_history else (core.read_json(hp/"manifest.json") if (hp/"manifest.json").exists() else None)
        Path(a.report).write_text(markdown(live,history),encoding="utf-8");payload={"live":{k:v for k,v in live.items() if k!="contract_rows"},"history":history}
    print(json.dumps(payload,ensure_ascii=False,indent=2));return 0

if __name__=="__main__":raise SystemExit(main())
