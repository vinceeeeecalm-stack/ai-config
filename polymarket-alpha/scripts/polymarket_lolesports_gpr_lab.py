#!/usr/bin/env python3
"""Audit official LoL Esports GPR data and run a leakage-aware research replay.

This route is research-only. It never emits formal estimates or mutates the
main paper ledger because the server-rendered GPR history lacks a separately
archived point-in-time revision contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://lolesports.com/en-US/leagues/msi"
ROBOTS_URL = "https://lolesports.com/robots.txt"
USER_AGENT = "PolymarketAlphaResearch/1.0 public-read-only"
SCALE_GRID = [200.0, 300.0, 400.0, 500.0, 600.0]


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def fetch(url: str) -> tuple[bytes, dict[str, Any]]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
    with urlopen(request, timeout=30) as response:
        body = response.read()
        final_url = response.geturl()
        if not final_url.startswith("https://lolesports.com/"):
            raise ValueError("official host redirect contract failed")
        return body, {"url": url, "final_url": final_url, "status": response.status,
                      "content_type": response.headers.get("Content-Type"), "bytes": len(body),
                      "sha256": hashlib.sha256(body).hexdigest()}


def robots_allows_public_read(text: str) -> bool:
    active = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line: continue
        key, _, value = line.partition(":")
        if key.lower() == "user-agent": active = value.strip() == "*"
        elif active and key.lower() == "disallow" and value.strip() == "/": return False
    return True


def largest_json_array(html: str, key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    marker = f'"{key}":['; pos = 0; arrays: list[list[Any]] = []; failures = 0
    while True:
        index = html.find(marker, pos)
        if index < 0: break
        try:
            value, _ = json.JSONDecoder().raw_decode(html[index + len(marker) - 1:])
            if isinstance(value, list): arrays.append(value)
        except (ValueError, json.JSONDecodeError): failures += 1
        pos = index + 1
    if not arrays: raise ValueError(f"server-rendered array missing: {key}")
    largest = max(arrays, key=len)
    return [row for row in largest if isinstance(row, dict)], {
        "array_occurrences": len(arrays), "array_lengths": [len(row) for row in arrays],
        "parse_failures": failures,
    }


def normalize_name(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return " ".join(word for word in value.split() if word not in {"esports", "gaming", "team", "dreamsmart"})


def team_alias_index(gpr_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in gpr_rows:
        team = row.get("team") or {}
        for value in (team.get("name"), team.get("code"), team.get("slug")):
            if value:
                key = normalize_name(str(value))
                if key: candidates[key].append(row)
    collisions = {key for key, rows in candidates.items() if len({(row.get("team") or {}).get("id") for row in rows}) > 1}
    return {key: rows[0] for key, rows in candidates.items() if key not in collisions}, collisions


def rating_before(row: dict[str, Any], cutoff: datetime) -> dict[str, Any] | None:
    points = []
    for key in ("teamGPRHistory",):
        points.extend(row.get(key) or [])
    for key in ("previousTeamGPR", "currentTeamGPR"):
        if isinstance(row.get(key), dict): points.append(row[key])
    eligible = []
    for point in points:
        try: timestamp = datetime.fromisoformat(str(point["dateCalculated"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError): continue
        if timestamp <= cutoff and point.get("elo") is not None:
            eligible.append((timestamp, point))
    return max(eligible, key=lambda item: item[0])[1] if eligible else None


def elo_probability(a: float, b: float, scale: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-(a - b) / scale))


def log_loss(y: int, p: float) -> float:
    p = min(1 - 1e-12, max(1e-12, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def build_rows(gpr_rows: list[dict[str, Any]], matches: list[dict[str, Any]],
               cutoffs: list[dict[str, Any]], scale: float) -> tuple[list[dict[str, Any]], dict[str, int]]:
    index, collisions = team_alias_index(gpr_rows)
    match_by_id = {str(row["match_id"]): row for row in matches}
    reasons: dict[str, int] = defaultdict(int); output = []
    for observation in cutoffs:
        if observation.get("cutoff_label") != "T-24h" or observation.get("status") != "complete": continue
        match = match_by_id.get(str(observation.get("match_id")))
        if not match: reasons["match_missing"] += 1; continue
        outcomes = match.get("outcomes") or []
        if len(outcomes) != 2: reasons["nonbinary"] += 1; continue
        first, second = (index.get(normalize_name(str(outcomes[0]))), index.get(normalize_name(str(outcomes[1]))))
        if not first or not second: reasons["team_alias_missing"] += 1; continue
        cutoff = datetime.fromisoformat(str(observation["cutoff_at"]).replace("Z", "+00:00"))
        rating_a, rating_b = rating_before(first, cutoff), rating_before(second, cutoff)
        if not rating_a or not rating_b: reasons["point_in_time_gpr_missing"] += 1; continue
        market_p = (observation.get("normalized_market_probabilities") or {}).get(outcomes[0])
        if market_p is None: reasons["market_probability_missing"] += 1; continue
        probability = elo_probability(float(rating_a["elo"]), float(rating_b["elo"]), scale)
        y = int(match.get("winning_outcome") == outcomes[0])
        output.append({"match_id": str(match["match_id"]), "match_start_time": match["match_start_time"],
                       "date": str(match["match_start_time"])[:10], "outcomes": outcomes, "winner": match.get("winning_outcome"),
                       "y": y, "market_probability": float(market_p), "model_probability": probability,
                       "rating_a": float(rating_a["elo"]), "rating_b": float(rating_b["elo"]),
                       "rating_a_at": rating_a["dateCalculated"], "rating_b_at": rating_b["dateCalculated"],
                       "model_brier": (probability - y) ** 2, "market_brier": (float(market_p) - y) ** 2,
                       "model_log_loss": log_loss(y, probability), "market_log_loss": log_loss(y, float(market_p))})
    reasons["alias_collisions"] = len(collisions)
    return sorted(output, key=lambda row: (row["match_start_time"], row["match_id"])), dict(reasons)


def split(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    a, b = int(len(rows) * .6), int(len(rows) * .8)
    return {"development": rows[:a], "validation": rows[a:b], "final_holdout": rows[b:]}


def clustered_lower(rows: list[dict[str, Any]], field: str, draws: int = 2000) -> float | None:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows: groups[row["date"]].append(float(row[field]))
    dates = sorted(groups)
    if len(dates) < 2: return None
    rng = random.Random(20260712); samples = []
    for _ in range(draws):
        chosen = [rng.choice(dates) for _ in dates]
        values = [value for date in chosen for value in groups[date]]
        samples.append(statistics.mean(values))
    samples.sort(); return samples[int(.025 * (len(samples) - 1))]


def summarize(rows: list[dict[str, Any]], segment: str) -> dict[str, Any]:
    for row in rows:
        row["brier_improvement"] = row["market_brier"] - row["model_brier"]
        row["log_loss_improvement"] = row["market_log_loss"] - row["model_log_loss"]
    return {"segment": segment, "matches": len(rows), "distinct_dates": len({row["date"] for row in rows}),
            "model_brier": statistics.mean(row["model_brier"] for row in rows) if rows else None,
            "market_brier": statistics.mean(row["market_brier"] for row in rows) if rows else None,
            "brier_improvement": statistics.mean(row["brier_improvement"] for row in rows) if rows else None,
            "brier_improvement_lower_95_date_clustered": clustered_lower(rows, "brier_improvement"),
            "model_log_loss": statistics.mean(row["model_log_loss"] for row in rows) if rows else None,
            "market_log_loss": statistics.mean(row["market_log_loss"] for row in rows) if rows else None,
            "log_loss_improvement": statistics.mean(row["log_loss_improvement"] for row in rows) if rows else None,
            "log_loss_improvement_lower_95_date_clustered": clustered_lower(rows, "log_loss_improvement")}


def run(cache_dir: Path) -> dict[str, Any]:
    robots, robots_meta = fetch(ROBOTS_URL); html_bytes, html_meta = fetch(SOURCE_URL)
    html = html_bytes.decode("utf-8"); robots_text = robots.decode("utf-8", errors="replace")
    events, event_parse = largest_json_array(html, "events"); gpr_rows, gpr_parse = largest_json_array(html, "teamGPR")
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "source.html").write_bytes(html_bytes)
    atomic_json(cache_dir / "events.json", events); atomic_json(cache_dir / "gpr.json", gpr_rows)
    matches = json.loads((ROOT / "cache/current_esports_2026_history/lol/eligible-match-winners.json").read_text())
    cutoffs = json.loads((ROOT / "cache/current_lol_cutoff_prices/cutoff-observations.json").read_text())
    scale_scores = []
    for scale in SCALE_GRID:
        rows, _ = build_rows(gpr_rows, matches, cutoffs, scale)
        development = split(rows)["development"]
        score = statistics.mean(row["model_brier"] for row in development) if development else float("inf")
        scale_scores.append({"scale": scale, "development_brier": score, "development_matches": len(development)})
    selected_scale = min(scale_scores, key=lambda row: row["development_brier"])["scale"]
    rows, exclusions = build_rows(gpr_rows, matches, cutoffs, selected_scale); segments = split(rows)
    summaries = [summarize(value, name) for name, value in segments.items()]
    oos = [row for row in summaries if row["segment"] in {"validation", "final_holdout"}]
    statistical_pass = bool(oos) and all(row["distinct_dates"] >= 30 for row in oos) and (
        all((row["brier_improvement_lower_95_date_clustered"] or -1) > 0 for row in oos) or
        all((row["log_loss_improvement_lower_95_date_clustered"] or -1) > 0 for row in oos))
    aliases, _ = team_alias_index(gpr_rows); current = {}
    for name in ("Bilibili Gaming", "Hanwha Life Esports"):
        row = aliases.get(normalize_name(name)); point = (row or {}).get("currentTeamGPR") or {}
        current[name] = {"elo": point.get("elo"), "gpr_score": point.get("gprScore"), "rank": point.get("rank"), "calculated_at": point.get("dateCalculated")}
    if current["Bilibili Gaming"].get("elo") and current["Hanwha Life Esports"].get("elo"):
        current["non_applicable_hypothetical_direct_match_probability"] = elo_probability(current["Bilibili Gaming"]["elo"], current["Hanwha Life Esports"]["elo"], selected_scale)
        current["scope_warning"] = "direct-match diagnostic only; not an MSI tournament-outright probability"
    blockers = ["server_rendered_history_revision_contract_unproven", "bulk_pagination_authorization_unproven"]
    if not statistical_pass: blockers.append("oos_improvement_gate_failed")
    payload = {"schema_version": "polymarket-lolesports-gpr-research-v1", "created_at": iso_now(), "status": "ok",
               "model_version": "pm-lolesports-official-gpr-v1-research", "research_promotion_candidate": False,
               "sustained_improvement": False, "paper_estimates_allowed": False,
               "model_outputs_are_true_probabilities": False, "promotion_status": "research_fail_oos_and_source_revision_contract_unproven",
               "source": {"official_url": SOURCE_URL, "robots": robots_meta, "robots_public_read_allowed": robots_allows_public_read(robots_text),
                          "html": html_meta, "api_key_used": False, "private_api_used": False,
                          "server_rendered_public_page": True, "bulk_pagination_authorization_proven": False},
               "extraction": {"completed_or_visible_events": len({str(row.get('id')) for row in events}), "gpr_teams": len(gpr_rows),
                              "event_parse": event_parse, "gpr_parse": gpr_parse},
               "protocol": {"model_version": "pm-lolesports-official-gpr-v1-research", "feature": "official historical Elo strictly at or before T-24h",
                            "scale_grid": SCALE_GRID, "selection_scope": "development_60pct_only", "split": "chronological_60_20_20"},
               "scale_selection": scale_scores, "selected_scale": selected_scale, "mapped_match_count": len(rows), "exclusions": exclusions,
               "segments": summaries, "statistical_oos_gate_passed": statistical_pass,
               "current_msi_research_diagnostic": current, "blockers": blockers,
               "paper_entry_eligible": False, "paper_estimates_emitted": False, "automatic_promotion": False,
               "main_paper_ledger_mutated": False, "paper_only": True, "live_orders_enabled": False, "private_api_used": False}
    atomic_json(cache_dir / "manifest.json", {"created_at": payload["created_at"], "source": payload["source"], "extraction": payload["extraction"],
                                              "files": [{"path": name, "bytes": (cache_dir / name).stat().st_size,
                                                         "sha256": hashlib.sha256((cache_dir / name).read_bytes()).hexdigest()} for name in ("source.html", "events.json", "gpr.json")]})
    return payload


def markdown(payload: dict[str, Any]) -> str:
    lines = ["# Daily LoL Esports GPR Coverage Audit", "", f"- Status: `{payload['status']}`",
             f"- Official GPR teams: {payload['extraction']['gpr_teams']}", f"- Historical matches mapped at T-24h: {payload['mapped_match_count']}",
             f"- Selected development-only Elo scale: {payload['selected_scale']}", f"- Statistical OOS gate: `{str(payload['statistical_oos_gate_passed']).lower()}`",
             f"- Paper entry eligible: `{str(payload['paper_entry_eligible']).lower()}`", "", "| Segment | Matches | Dates | Model Brier | Market Brier | Improvement | 95% lower |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for row in payload["segments"]:
        values = [row.get(key) for key in ("model_brier", "market_brier", "brier_improvement", "brier_improvement_lower_95_date_clustered")]
        fmt = lambda value: "—" if value is None else f"{value:.5f}"
        lines.append(f"| {row['segment']} | {row['matches']} | {row['distinct_dates']} | {fmt(values[0])} | {fmt(values[1])} | {fmt(values[2])} | {fmt(values[3])} |")
    lines.extend(["", "## Non-applicable direct-match diagnostic", "", f"```json\n{json.dumps(payload['current_msi_research_diagnostic'], ensure_ascii=False, indent=2)}\n```", "", "This model covers a direct head-to-head match only. It does not apply to MSI tournament-outright or pentakill contracts.", "", "## Hard blockers", ""])
    lines.extend(f"- {blocker}" for blocker in payload["blockers"])
    lines.extend(["", "This diagnostic is not a formal probability and cannot enter the paper gate. Market prices are not used as model features.", ""])
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    sample = '<script>{"events":[{"id":"e"}],"teamGPR":[{"team":{"id":"t","name":"Bilibili Gaming","code":"BLG"}}]}</script>'
    assert largest_json_array(sample, "events")[0][0]["id"] == "e"
    assert normalize_name("BILIBILI GAMING") == normalize_name("Bilibili Gaming")
    row = {"teamGPRHistory": [{"dateCalculated": "2026-01-01T00:00:00Z", "elo": 1500}, {"dateCalculated": "2026-02-01T00:00:00Z", "elo": 1600}]}
    assert rating_before(row, datetime(2026, 1, 15, tzinfo=timezone.utc))["elo"] == 1500
    assert abs(elo_probability(1500, 1500, 400) - .5) < 1e-12
    return {"status": "pass", "tests": ["server_rendered_array_parse", "team_alias_normalization", "strict_point_in_time_rating", "elo_probability"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--only-if-daily-esports", action="store_true")
    parser.add_argument("--cache-dir", default=str(ROOT / "cache/current_lolesports_gpr"))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-lolesports-gpr-research.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_DAILY_LOLESPORTS_GPR.md")); args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    elif args.only_if_daily_esports:
        daily_path = ROOT / "experiments/current-daily-priority-cycle.json"
        daily = json.loads(daily_path.read_text()) if daily_path.exists() else {}
        needed = any(str(row.get("market_family") or "").startswith("esports_") for row in daily.get("market_watchlist", []))
        payload = run(Path(args.cache_dir)) if needed else {
            "schema_version": "polymarket-lolesports-gpr-research-v1", "created_at": iso_now(),
            "status": "skipped_no_daily_esports", "paper_entry_eligible": False, "paper_estimates_emitted": False,
            "main_paper_ledger_mutated": False, "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        }
    else:
        payload = run(Path(args.cache_dir))
    if not args.self_test:
        atomic_json(Path(args.output), payload)
        if payload["status"] == "ok": Path(args.report).write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload if args.self_test else {key: payload.get(key) for key in ("status", "mapped_match_count", "selected_scale", "statistical_oos_gate_passed", "paper_entry_eligible", "blockers")}, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") in {"ok", "pass", "skipped_no_daily_esports"} else 1


if __name__ == "__main__": raise SystemExit(main())
