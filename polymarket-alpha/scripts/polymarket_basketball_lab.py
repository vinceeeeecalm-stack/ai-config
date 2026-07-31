#!/usr/bin/env python3
"""Public-only Basketball Phase-0 taxonomy and terminal game-history audit."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
GAMMA = "https://gamma-api.polymarket.com"
LEAGUES = {
    "nba": {"series_id": 10345, "official_results": "https://www.nba.com/"},
    "wnba": {"series_id": 10105, "official_results": "https://www.wnba.com/"},
    "nbasl": {"series_id": 12174, "official_results": "https://www.nba.com/summer-league"},
}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("basketball_core", ROOT / "scripts/polymarket_alpha.py")
family = load("basketball_family", ROOT / "scripts/polymarket_market_family_audit.py")
public = load("basketball_public", ROOT / "scripts/polymarket_public_data.py")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_time(value: Any):
    if not value:
        return None
    text = str(value)
    match = re.match(r"^(.*\.)(\d+)(Z|[+-]\d\d:?\d\d)$", text)
    if match:
        text = f"{match.group(1)}{match.group(2)[:6].ljust(6, '0')}{match.group(3)}"
    if re.search(r"[+-]\d\d$", text):
        text += ":00"
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return core.parse_iso(value)


def live_contract_type(question: str) -> str:
    text = question.lower()
    if "highest " in text and "regular season" in text:
        return "season_stat_leader"
    if "rookie of the year" in text or "mvp award" in text or "defensive player of the year" in text:
        return "season_award"
    if "win the 202" in text and "finals" in text:
        return "season_champion"
    if "conference champion" in text or "make the 2027 nba play" in text:
        return "season_team_outcome"
    if "play for" in text or "sign " in text or "contract" in text or "traded" in text or "retire" in text:
        return "roster_or_contract"
    if " vs" in text or " vs." in text:
        return "single_game_candidate"
    return "other"


def live_audit(markets: list[dict[str, Any]], enrichment: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    rows = []
    for market in markets:
        if not family.belongs(market, family.FAMILIES["basketball"]):
            continue
        raw = market.get("_sampling_raw") or {}
        question = str(market.get("question") or raw.get("question") or "")
        end = family.end_time(market)
        rows.append({
            "market_id": str(market.get("id") or market.get("conditionId") or ""),
            "event_id": family.event_identity(market, enrichment), "question": question,
            "contract_type": live_contract_type(question), "end_time": end.isoformat() if end else None,
            "within_30d": bool(end and as_of <= end <= as_of + timedelta(days=30)),
            "expired_but_live": bool(end and end < as_of),
        })
    groups = []
    for contract_type in sorted({row["contract_type"] for row in rows}):
        selected = [row for row in rows if row["contract_type"] == contract_type]
        groups.append({
            "contract_type": contract_type, "contract_count": len(selected),
            "independent_event_count": len({row["event_id"] for row in selected if row["event_id"]}),
            "within_30d_event_count": len({row["event_id"] for row in selected if row["event_id"] and row["within_30d"]}),
            "expired_but_live_count": sum(row["expired_but_live"] for row in selected),
        })
    groups.sort(key=lambda row: row["independent_event_count"], reverse=True)
    return {
        "schema_version": "polymarket-basketball-live-taxonomy-v1", "created_at": core.now_iso(),
        "inventory_as_of": as_of.isoformat(), "contract_count": len(rows),
        "independent_event_count": len({row["event_id"] for row in rows if row["event_id"]}),
        "groups": groups, "single_game_live_candidate_count": sum(row["contract_type"] == "single_game_candidate" for row in rows),
        "history_research_required": True, "paper_entry_eligible": False, "paper_estimates_emitted": False,
        "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False,
    }


def official_sports_metadata() -> dict[str, Any]:
    payload = public.get_json(f"{GAMMA}/sports", timeout=30, retries=2)
    if not isinstance(payload, list):
        raise ValueError("Gamma sports response is not a list")
    found = {str(row.get("sport")): row for row in payload if str(row.get("sport")) in LEAGUES}
    missing = sorted(set(LEAGUES) - set(found))
    mismatches = []
    for league, expected in LEAGUES.items():
        row = found.get(league) or {}
        if str(row.get("series")) != str(expected["series_id"]):
            mismatches.append(f"{league}:series")
        if str(row.get("resolution")) != expected["official_results"]:
            mismatches.append(f"{league}:resolution")
    return {
        "schema_version": "polymarket-basketball-sports-metadata-v1", "created_at": core.now_iso(),
        "source": f"{GAMMA}/sports", "leagues": found, "missing": missing, "mismatches": mismatches,
        "status": "ok" if not missing and not mismatches else "degraded", "research_only": True,
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }


def load_events(directory: Path) -> list[dict[str, Any]]:
    events = {}
    for page in sorted((directory / "pages").glob("page-*.json")):
        for event in core.read_json(page).get("events") or []:
            events[str(event.get("id"))] = event
    return sorted(events.values(), key=lambda row: int(row.get("id") or 0))


def strict_moneyline(league: str, event: dict[str, Any], market: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if str(market.get("sportsMarketType") or "").lower() != "moneyline":
        return None, "not_moneyline"
    question = str(market.get("question") or "")
    if any(term in question.lower() for term in ("series", "game 1", "quarter", "half", "o/u", "spread", "wins by")):
        return None, "derivative_or_series"
    outcomes = public.parse_jsonish(market.get("outcomes")); tokens = public.parse_jsonish(market.get("clobTokenIds"))
    if len(outcomes) != 2 or len(tokens) != 2 or len(set(map(str, outcomes))) != 2:
        return None, "not_two_distinct_teams"
    result = public.infer_resolution(market)
    if result.get("winning_outcome") not in outcomes:
        return None, "not_terminal"
    game_start = parse_time(market.get("gameStartTime") or event.get("endDate")); market_start = parse_time(market.get("startDate") or event.get("startDate"))
    if not game_start or not market_start or market_start >= game_start:
        return None, "invalid_timing"
    return {
        "league": league, "game_id": str(event.get("id") or ""), "event_id": str(event.get("id") or ""),
        "market_id": str(market.get("id") or ""), "question": question,
        "game_start_time": game_start.isoformat(), "market_start_time": market_start.isoformat(),
        "teams": list(map(str, outcomes)), "tokens": dict(zip(map(str, outcomes), map(str, tokens))),
        "winning_outcome": result["winning_outcome"], "available_at_t24": market_start <= game_start - timedelta(hours=24),
        "available_at_t60": market_start <= game_start - timedelta(minutes=60),
    }, None


def discover_league(league: str, root: Path, max_pages: int, wall_clock_seconds: float) -> dict[str, Any]:
    contract = LEAGUES[league]; directory = root / league; (directory / "pages").mkdir(parents=True, exist_ok=True)
    query = {"endpoint": f"{GAMMA}/events/keyset", "closed": True, "series_id": contract["series_id"], "limit": 100}
    state_path, log_path = directory / "state.json", directory / "request-log.json"
    state = core.read_json(state_path) if state_path.exists() else {"schema_version": "polymarket-basketball-history-state-v1", "contract": query, "next_cursor": None, "terminal_cursor_proven": False, "pages_fetched": 0}
    if state.get("contract") != query:
        raise ValueError(f"existing {league} basketball history contract mismatch")
    requests = core.read_json(log_path) if log_path.exists() else []; core.write_json(state_path, state); core.write_json(log_path, requests)
    started = time.monotonic(); fetched = 0
    while not state["terminal_cursor_proven"] and fetched < max_pages and time.monotonic() - started < wall_clock_seconds:
        page_limit = int(state.get("adaptive_page_size") or 100)
        params: dict[str, Any] = {"closed": "true", "series_id": contract["series_id"], "limit": page_limit}
        if state.get("next_cursor"):
            params["after_cursor"] = state["next_cursor"]
        url = f"{GAMMA}/events/keyset?{urlencode(params)}"
        try:
            payload = public.get_json(url, timeout=25, retries=2); events = payload.get("events")
            if not isinstance(events, list):
                raise ValueError("keyset response missing events")
            number = int(state["pages_fetched"]) + 1; cursor = payload.get("next_cursor")
            core.write_json(directory / "pages" / f"page-{number:04d}.json", {"page_number": number, "request_url": url, "events": events, "next_cursor": cursor})
            requests.append({"page_number": number, "url": url, "status": "ok", "requested_limit": page_limit, "rows": len(events), "terminal": not bool(cursor), "created_at": core.now_iso()})
            state.update({"pages_fetched": number, "next_cursor": cursor, "terminal_cursor_proven": not bool(cursor), "updated_at": core.now_iso()})
            core.write_json(log_path, requests); core.write_json(state_path, state); fetched += 1
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "requested_limit": page_limit, "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()})
            if page_limit > 20:
                fallback_params = {**params, "limit": 20}; fallback_url = f"{GAMMA}/events/keyset?{urlencode(fallback_params)}"
                try:
                    payload = public.get_json(fallback_url, timeout=25, retries=2); events = payload.get("events")
                    if not isinstance(events, list):
                        raise ValueError("fallback keyset response missing events")
                    number = int(state["pages_fetched"]) + 1; cursor = payload.get("next_cursor")
                    core.write_json(directory / "pages" / f"page-{number:04d}.json", {"page_number": number, "request_url": fallback_url, "events": events, "next_cursor": cursor})
                    requests.append({"page_number": number, "url": fallback_url, "status": "ok_fallback", "requested_limit": 20, "rows": len(events), "terminal": not bool(cursor), "created_at": core.now_iso()})
                    state.update({"pages_fetched": number, "next_cursor": cursor, "terminal_cursor_proven": not bool(cursor), "adaptive_page_size": 20, "updated_at": core.now_iso()})
                    core.write_json(log_path, requests); core.write_json(state_path, state); fetched += 1; continue
                except Exception as fallback_exc:
                    requests.append({"url": fallback_url, "status": "failed_fallback", "requested_limit": 20, "error": f"{type(fallback_exc).__name__}:{fallback_exc}", "created_at": core.now_iso()})
            core.write_json(log_path, requests); break
    events = load_events(directory); eligible, exclusions = [], Counter()
    for event in events:
        for market in event.get("markets") or []:
            row, reason = strict_moneyline(league, event, market)
            if row:
                eligible.append(row)
            else:
                exclusions[reason or "unknown"] += 1
    core.write_json(directory / "eligible-moneylines.json", eligible)
    relative = ["state.json", "request-log.json", "eligible-moneylines.json"] + [str(path.relative_to(directory)) for path in sorted((directory / "pages").glob("page-*.json"))]
    manifest = {
        "schema_version": "polymarket-basketball-league-history-v1", "created_at": core.now_iso(), "league": league,
        "series_id": contract["series_id"], "official_result_source": contract["official_results"],
        "pages_fetched": state["pages_fetched"], "terminal_cursor_proven": state["terminal_cursor_proven"],
        "events_fetched": len(events), "markets_fetched": sum(len(event.get("markets") or []) for event in events),
        "eligible_moneylines": len(eligible), "independent_game_ids": len({row["game_id"] for row in eligible}),
        "eligible_t24": sum(row["available_at_t24"] for row in eligible), "eligible_t60": sum(row["available_at_t60"] for row in eligible),
        "first_game_start": min((row["game_start_time"] for row in eligible), default=None), "last_game_start": max((row["game_start_time"] for row in eligible), default=None),
        "exclusion_reason_counts": dict(exclusions), "model_sample_gate_met": len(eligible) >= 150,
        "data_status": "ok" if state["terminal_cursor_proven"] and len(eligible) >= 30 else "degraded",
        "files": [{"path": name, "bytes": (directory / name).stat().st_size, "sha256": sha256(directory / name)} for name in relative],
        "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(directory / "manifest.json", manifest); return manifest


def aggregate(live: dict[str, Any], metadata: dict[str, Any], manifests: list[dict[str, Any]], output: Path, report: Path) -> dict[str, Any]:
    ranked = sorted(manifests, key=lambda row: (row["model_sample_gate_met"], row["eligible_t24"], row["eligible_moneylines"]), reverse=True)
    candidates = [row for row in ranked if row["data_status"] == "ok" and row["model_sample_gate_met"]]
    selected = candidates[0]["league"] if candidates else None
    payload = {
        "schema_version": "polymarket-basketball-history-audit-v1", "created_at": core.now_iso(),
        "live": live, "sports_metadata_status": metadata["status"], "leagues": ranked,
        "selected_league_for_model_research": selected,
        "selection_rule": "terminal strict full-game moneyline history, minimum 150 games, then maximum T-24h coverage",
        "selection_is_alpha_evidence": False, "independent_result_data_acquired": False,
        "cutoff_market_prices_acquired": False, "model_implemented": False, "paper_entry_eligible": False,
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(output, payload)
    lines = ["# Polymarket Basketball Data Gate", "", f"- Live contracts / events: {live['contract_count']} / {live['independent_event_count']}", f"- Live single-game candidates: {live['single_game_live_candidate_count']}", f"- Selected historical league: `{selected}`", "- Selection is data feasibility only; no probability or paper permission.", "", "| League | Status | Events | Moneylines | T-24h | T-60m | Sample gate |", "|---|---|---:|---:|---:|---:|---|"]
    for row in ranked:
        lines.append(f"| {row['league']} | {row['data_status']} | {row['events_fetched']} | {row['eligible_moneylines']} | {row['eligible_t24']} | {row['eligible_t60']} | {str(row['model_sample_gate_met']).lower()} |")
    lines.extend(["", "Season futures, awards, stat leaders, roster outcomes, spreads, totals and derivatives remain excluded from the single-game research scope.", ""])
    report.write_text("\n".join(lines), encoding="utf-8"); return payload


def self_test() -> dict[str, Any]:
    market = {"id": "m1", "question": "Suns vs. Lakers", "sportsMarketType": "moneyline", "gameStartTime": "2026-07-12T20:00:00Z", "startDate": "2026-07-10T00:00:00Z", "outcomes": '["Suns","Lakers"]', "outcomePrices": '["1","0"]', "clobTokenIds": '["a","b"]', "closed": True}
    row, reason = strict_moneyline("nba", {"id": "e1"}, market)
    assert reason is None and row and row["available_at_t24"]
    derivative = {**market, "sportsMarketType": "totals", "question": "Suns vs. Lakers O/U"}
    assert strict_moneyline("nba", {"id": "e1"}, derivative)[1] == "not_moneyline"
    assert live_contract_type("Will A win the 2027 NBA Finals?") == "season_champion"
    assert parse_time("2026-03-24T12:05:13.26752Z") is not None
    return {"status": "pass", "tests": ["strict_moneyline", "derivative_exclusion", "live_futures_taxonomy", "fractional_timestamp"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--discover-history", action="store_true"); parser.add_argument("--league", choices=["all", *LEAGUES], default="all")
    parser.add_argument("--max-pages", type=int, default=100); parser.add_argument("--wall-clock-seconds", type=float, default=240)
    parser.add_argument("--markets", default=str(ROOT / "cache/current_validation_snapshot/markets.json")); parser.add_argument("--snapshot-manifest", default=str(ROOT / "cache/current_validation_snapshot/snapshot-manifest.json"))
    parser.add_argument("--identity-enrichment", default=str(ROOT / "cache/current_family_identity_enrichment.json")); parser.add_argument("--history-dir", default=str(ROOT / "cache/current_basketball_history"))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-basketball-history-audit.json")); parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_BASKETBALL_DATA_GATE.md")); args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    else:
        snapshot = core.read_json(args.snapshot_manifest)
        if snapshot.get("terminal_cursor_proven") is not True:
            raise ValueError("current market snapshot is not terminal")
        live = live_audit(core.read_json(args.markets), core.read_json(args.identity_enrichment), parse_time(snapshot.get("created_at")) or datetime.now(timezone.utc))
        metadata = official_sports_metadata(); history_root = Path(args.history_dir); core.write_json(history_root / "sports-metadata.json", metadata)
        leagues = list(LEAGUES) if args.league == "all" else [args.league]
        manifests = [discover_league(league, history_root, args.max_pages, args.wall_clock_seconds) for league in leagues] if args.discover_history else [core.read_json(history_root / league / "manifest.json") for league in leagues]
        payload = aggregate(live, metadata, manifests, Path(args.output), Path(args.report))
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
