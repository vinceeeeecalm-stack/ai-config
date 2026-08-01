#!/usr/bin/env python3
"""Public-only Macro Indicators taxonomy and terminal history data gate."""
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
MACRO_TAG_ID = 102000
PAGE_LIMIT = 20


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    return module


core = load("macro_core", ROOT / "scripts/polymarket_alpha.py")
family = load("macro_family", ROOT / "scripts/polymarket_market_family_audit.py")
public = load("macro_public", ROOT / "scripts/polymarket_public_data.py")


COUNTRIES = {
    "United States": (" us ", "u.s.", "united states", "fed", "federal reserve", "bls", "bea"),
    "Eurozone": ("eurozone", "euro area", "eurostat"), "United Kingdom": (" uk ", "u.k.", "united kingdom"),
    "Canada": ("canada",), "China": ("china",), "India": ("india",), "Mexico": ("mexico",),
    "Argentina": ("argentina",), "South Korea": ("south korea",), "South Africa": ("south africa",),
    "World": ("world gdp", "international monetary fund", "imf"),
}
AUTHORITIES = {
    "Federal Reserve": ("federal reserve", "fomc"), "BLS": ("bureau of labor statistics", "bls"),
    "BEA": ("bureau of economic analysis", "bea"), "Statistics Canada": ("statistics canada",),
    "Eurostat": ("eurostat",), "UK ONS": ("office for national statistics",),
    "IMF": ("international monetary fund", "world economic outlook"),
    "China NBS": ("national bureau of statistics",), "India MOSPI": ("ministry of statistics and programme implementation",),
    "Mexico INEGI": ("national institute of statistics and geography",),
    "Argentina INDEC": ("national institute of statistics and censuses",),
    "Statistics Korea": ("south korean ministry of data and statistics", "statistics korea"),
    "Statistics South Africa": ("statistics south africa",),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def series_type(text: str, tags: set[str]) -> str:
    lower = text.lower()
    if "Fed Rates" in tags or re.search(r"\b(fed|fomc|federal funds)\b", lower): return "policy_rate"
    if "Inflation" in tags or "CPI" in tags or "inflation" in lower or "consumer price" in lower: return "inflation_cpi"
    if "GDP" in tags or re.search(r"\bgdp\b|gross domestic product", lower): return "gdp_growth"
    if "unemployment" in tags or "unemployment" in lower: return "unemployment_rate"
    if re.search(r"nonfarm|payroll|jobs added", lower): return "employment_payroll"
    if "population" in tags or "population" in lower: return "population"
    if re.search(r"retail sales", lower): return "retail_sales"
    return "other_macro"


def country_scope(text: str, tags: set[str]) -> str:
    padded = f" {text.lower()} "
    tag_lower = {tag.lower() for tag in tags}
    for country, needles in COUNTRIES.items():
        if country.lower() in tag_lower or any(needle in padded for needle in needles): return country
    return "Unspecified"


def period_key(text: str) -> str:
    quarter = re.search(r"\bQ([1-4])\s*(20\d{2})\b|\b(20\d{2})\s*Q([1-4])\b", text, re.I)
    if quarter:
        q = quarter.group(1) or quarter.group(4); year = quarter.group(2) or quarter.group(3)
        return f"{year}-Q{q}"
    month = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b", text, re.I)
    if month: return f"{month.group(2)}-{month.group(1).lower()}"
    year = re.search(r"\b(20\d{2})\b", text)
    return year.group(1) if year else "period_unspecified"


def release_convention(text: str) -> str:
    lower = text.lower()
    if "advance estimate" in lower: return "advance_estimate"
    if "preliminary" in lower: return "preliminary_release"
    if re.search(r"first (?:quarterly )?estimate|first official", lower): return "first_release"
    if "final" in lower: return "final_release"
    if re.search(r"12-month period ending|annual inflation|annual gdp|full year", lower): return "annual_first_specified_release"
    if re.search(r"fomc|target federal funds|federal reserve", lower): return "official_policy_decision"
    return "release_vintage_unspecified"


def authorities(text: str) -> list[str]:
    lower = text.lower()
    return [name for name, needles in AUTHORITIES.items() if any(needle in lower for needle in needles)]


def diagnostics(text: str) -> dict[str, Any]:
    named = authorities(text); convention = release_convention(text)
    vague = bool(re.search(r"credible reporting|consensus of credible|reputable media", text, re.I))
    return {
        "official_authorities": named, "official_authority_present": bool(named),
        "release_convention": convention, "release_vintage_explicit": convention != "release_vintage_unspecified",
        "media_judgment_required": vague,
        "high_rule_ambiguity": not named or convention == "release_vintage_unspecified" or vague,
    }


def event_identity(market: dict[str, Any], series: str, country: str, period: str, convention: str) -> tuple[str, str]:
    official = family.event_identity(market)
    if official: return official, "gamma_event_or_clob_negative_risk"
    return f"derived:{series}:{country}:{period}:{convention}", "derived_series_country_period_release"


def live_taxonomy(markets: list[dict[str, Any]], as_of: datetime) -> dict[str, Any]:
    selected = [market for market in markets if family.belongs(market, family.FAMILIES["macro_indicators"])]
    rows = []
    for market in selected:
        raw = market.get("_sampling_raw") or {}; event = (market.get("events") or [{}])[0]
        question = str(market.get("question") or raw.get("question") or "")
        description = str(event.get("description") or market.get("description") or raw.get("description") or "")
        tags = set(raw.get("tags") or []); combined = f"{question}\n{description}"
        series, country, period = series_type(combined, tags), country_scope(combined, tags), period_key(combined)
        diag = diagnostics(combined); identity, source = event_identity(market, series, country, period, diag["release_convention"])
        end = family.end_time(market)
        rows.append({"market_id": str(market.get("id") or market.get("conditionId") or ""), "question": question,
                     "series": series, "country": country, "period": period, "event_cluster_id": identity,
                     "identity_source": source, "end_time": end.isoformat() if end else None,
                     "expired_but_live": bool(end and end < as_of), **diag})
    groups = []
    for key in sorted({(row["series"], row["country"], row["release_convention"]) for row in rows}):
        subset = [row for row in rows if (row["series"], row["country"], row["release_convention"]) == key]
        events = {row["event_cluster_id"] for row in subset}; clean = [row for row in subset if not row["high_rule_ambiguity"] and not row["expired_but_live"]]
        groups.append({"series": key[0], "country": key[1], "release_convention": key[2], "contract_count": len(subset),
                       "independent_event_count": len(events), "clean_contract_count": len(clean),
                       "expired_but_live_count": sum(row["expired_but_live"] for row in subset),
                       "official_authority_pct": round(100 * sum(row["official_authority_present"] for row in subset) / len(subset), 2),
                       "explicit_vintage_pct": round(100 * sum(row["release_vintage_explicit"] for row in subset) / len(subset), 2),
                       "phase_zero_candidate": len(events) >= 30 and len(clean) == len(subset)})
    groups.sort(key=lambda row: (row["phase_zero_candidate"], row["independent_event_count"], row["clean_contract_count"]), reverse=True)
    candidates = [row for row in groups if row["phase_zero_candidate"]]
    return {"schema_version": "polymarket-macro-live-taxonomy-v1", "created_at": core.now_iso(),
            "inventory_as_of": as_of.isoformat(), "family": "macro_indicators", "contract_count": len(rows),
            "independent_event_count": len({row["event_cluster_id"] for row in rows}),
            "derived_identity_contract_count": sum(row["identity_source"].startswith("derived") for row in rows),
            "groups": groups, "selected_group_for_history_research": candidates[0] if candidates else None,
            "model_status": "blocked_pending_terminal_history", "contract_rows": rows,
            "paper_entry_eligible": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
            "live_orders_enabled": False, "private_api_used": False}


def load_events(directory: Path) -> list[dict[str, Any]]:
    events = {}
    for path in sorted((directory / "pages").glob("page-*.json")):
        for event in core.read_json(path).get("events") or []: events[str(event.get("id"))] = event
    return sorted(events.values(), key=lambda row: int(row.get("id") or 0))


def settled_event_row(event: dict[str, Any]) -> dict[str, Any] | None:
    markets = event.get("markets") or []; terminal = []
    for market in markets:
        resolution = public.infer_resolution(market)
        if resolution.get("winning_outcome") is not None:
            terminal.append({"market_id": str(market.get("id") or ""), "question": market.get("question"), "winning_outcome": resolution["winning_outcome"]})
    if not terminal: return None
    title = str(event.get("title") or ""); description = str(event.get("description") or "")
    tags = {str(tag.get("label") or tag) for tag in event.get("tags") or []}; combined = f"{title}\n{description}"
    series, country, period = series_type(combined, tags), country_scope(combined, tags), period_key(combined)
    return {"event_id": str(event.get("id") or ""), "title": title, "series": series, "country": country,
            "period": period, "end_time": event.get("endDate"), "terminal_market_count": len(terminal),
            "terminal_markets": terminal, **diagnostics(combined)}


def discover_history(directory: Path, max_pages: int, wall_clock_seconds: float) -> dict[str, Any]:
    (directory / "pages").mkdir(parents=True, exist_ok=True)
    contract = {"endpoint": f"{GAMMA}/events/keyset", "closed": True, "tag_id": MACRO_TAG_ID, "limit": PAGE_LIMIT}
    state_path, log_path = directory / "state.json", directory / "request-log.json"
    state = core.read_json(state_path) if state_path.exists() else {"schema_version": "polymarket-macro-history-state-v1", "contract": contract, "next_cursor": None, "terminal_cursor_proven": False, "pages_fetched": 0}
    if state.get("contract") != contract: raise ValueError("macro history cache contract mismatch")
    requests = core.read_json(log_path) if log_path.exists() else []; core.write_json(state_path, state); core.write_json(log_path, requests)
    started = time.monotonic(); fetched = 0
    while not state["terminal_cursor_proven"] and fetched < max_pages and time.monotonic() - started < wall_clock_seconds:
        params: dict[str, Any] = {"closed": "true", "tag_id": MACRO_TAG_ID, "limit": PAGE_LIMIT}
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
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()}); core.write_json(log_path, requests); break
    events = load_events(directory); rows = [row for event in events if (row := settled_event_row(event))]
    core.write_json(directory / "settled-macro-events.json", rows)
    summaries = []
    for key in sorted({(row["series"], row["country"], row["release_convention"]) for row in rows}):
        subset = [row for row in rows if (row["series"], row["country"], row["release_convention"]) == key]
        strict = [row for row in subset if not row["high_rule_ambiguity"]]
        summaries.append({"series": key[0], "country": key[1], "release_convention": key[2], "settled_event_count": len(subset),
                          "strict_event_count": len(strict), "model_sample_gate_met": len(strict) >= 150})
    summaries.sort(key=lambda row: (row["model_sample_gate_met"], row["strict_event_count"]), reverse=True)
    relative = ["state.json", "request-log.json", "settled-macro-events.json"] + [str(path.relative_to(directory)) for path in sorted((directory / "pages").glob("page-*.json"))]
    manifest = {"schema_version": "polymarket-macro-history-manifest-v1", "created_at": core.now_iso(),
                "official_source": f"{GAMMA}/events/keyset", "macro_tag_id": MACRO_TAG_ID,
                "pages_fetched": state["pages_fetched"], "terminal_cursor_proven": state["terminal_cursor_proven"],
                "events_fetched": len(events), "settled_macro_events": len(rows), "homogeneous_groups": summaries,
                "minimum_total_for_frozen_60_20_20_split": 150,
                "selected_group_for_model_research": next((row for row in summaries if row["model_sample_gate_met"]), None),
                "data_status": "ok" if state["terminal_cursor_proven"] else "degraded",
                "model_sample_gate_met": any(row["model_sample_gate_met"] for row in summaries),
                "files": [{"path": name, "bytes": (directory / name).stat().st_size, "sha256": sha256(directory / name)} for name in relative],
                "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
                "live_orders_enabled": False, "private_api_used": False}
    core.write_json(directory / "manifest.json", manifest); return manifest


def markdown(live: dict[str, Any], history: dict[str, Any] | None) -> str:
    lines = ["# Polymarket Macro Indicators Data Gate", "", f"- Live contracts: {live['contract_count']}",
             f"- Live independent event clusters: {live['independent_event_count']}",
             f"- Selected live group: `{live['selected_group_for_history_research']}`", "- No probability model or paper permission is emitted.", "",
             "| Live group | Country | Convention | Contracts | Events | Clean | Candidate |", "|---|---|---|---:|---:|---:|---|"]
    for row in live["groups"]: lines.append(f"| {row['series']} | {row['country']} | {row['release_convention']} | {row['contract_count']} | {row['independent_event_count']} | {row['clean_contract_count']} | {str(row['phase_zero_candidate']).lower()} |")
    if history:
        lines.extend(["", f"Terminal history: {history['events_fetched']} tagged events, {history['settled_macro_events']} settled macro events; model sample gate `{str(history['model_sample_gate_met']).lower()}`.", ""])
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    text = "US GDP growth in Q1 2025? Advance Estimate from the Bureau of Economic Analysis"
    assert series_type(text, {"GDP"}) == "gdp_growth" and country_scope(text, set()) == "United States"
    assert period_key(text) == "2025-Q1" and release_convention(text) == "advance_estimate"
    assert diagnostics(text)["high_rule_ambiguity"] is False
    a = event_identity({"_sampling_raw": {}}, "inflation_cpi", "Canada", "2026", "annual_first_specified_release")
    b = event_identity({"_sampling_raw": {}}, "inflation_cpi", "Canada", "2026", "annual_first_specified_release")
    assert a == b and a[1] == "derived_series_country_period_release"
    return {"status": "pass", "tests": ["series_country_period", "release_vintage", "official_authority", "threshold_ladder_clustering"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--markets", default=str(ROOT / "cache/current_validation_snapshot/markets.json")); parser.add_argument("--snapshot-manifest", default=str(ROOT / "cache/current_validation_snapshot/snapshot-manifest.json"))
    parser.add_argument("--history-dir", default=str(ROOT / "cache/current_macro_history")); parser.add_argument("--discover-history", action="store_true")
    parser.add_argument("--max-pages", type=int, default=100); parser.add_argument("--wall-clock-seconds", type=float, default=240)
    parser.add_argument("--output", default=str(ROOT / "experiments/current-macro-taxonomy-audit.json")); parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_MACRO_DATA_GATE.md")); args = parser.parse_args()
    if args.self_test: payload = self_test()
    else:
        manifest = core.read_json(args.snapshot_manifest)
        if manifest.get("terminal_cursor_proven") is not True: raise ValueError("snapshot has no terminal cursor proof")
        live = live_taxonomy(core.read_json(args.markets), core.parse_iso(manifest.get("created_at")) or datetime.now(timezone.utc)); core.write_json(args.output, live)
        history = discover_history(Path(args.history_dir), args.max_pages, args.wall_clock_seconds) if args.discover_history else (core.read_json(Path(args.history_dir) / "manifest.json") if (Path(args.history_dir) / "manifest.json").exists() else None)
        report = Path(args.report); report.parent.mkdir(parents=True, exist_ok=True); report.write_text(markdown(live, history), encoding="utf-8")
        payload = {"live": {k: v for k, v in live.items() if k != "contract_rows"}, "history": history}
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
