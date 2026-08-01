#!/usr/bin/env python3
"""Append the public-only Necaxa–Atlante event research to the canonical artifact."""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "experiments/current-sports-event-research.json"
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def main():
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    rows = {str(r.get("event_slug")): r for r in payload.get("research_items", [])}
    rows["mex-nec-atla-2026-07-16"] = {
        "event_slug": "mex-nec-atla-2026-07-16", "event_id": "663079", "game_id": "90112652",
        "title": "Club Necaxa vs. Atlante FC", "target_market_id": "2785146", "target_side": "YES",
        "direction": "AWAY", "rules_clear": True,
        "settlement_rules": "Polymarket resolves Atlante FC exact 90-minute win plus stoppage time; extra time and penalties are excluded.",
        "research_status": "insufficient", "research_probability": None, "confidence_low": None,
        "confidence_high": None, "calibrated_oos_samples": 0, "maximum_acceptable_price": None,
        "alpha_maximum_acceptable_price": None,
        "method": "Market was used only for discovery and execution baseline. Official club competition identity plus two independent previews were checked, but confirmed XI, opponent-adjusted 5–10 match sample, reproducible xG/shot-quality data, weather, and a reconciled independent P_win were not jointly available. No probability is fabricated.",
        "key_evidence": [
            "The Polymarket contract is Atlante FC to win on 16 July 2026 in 90 minutes plus stoppage time; the scan captured 64c bid, 65c ask, 1c spread and taker-fee schedule.",
            "Sports Mole identifies the 2026 Liga MX Apertura opener and reports Necaxa pre-season results plus projected lineups; Club Necaxa's official site confirms the club identity but does not provide a verified event-level XI in the available snapshot.",
            "Independent public previews agree on the fixture but do not provide a reproducible opponent-adjusted probability; the market remains a baseline, not independent research probability.",
            "The match was already marked possibly_live_unverified by the scan, so a live direction would require score, minute and CLOB timestamps all within 120 seconds."
        ],
        "first_goal_profile": "Atlante's market-implied away edge suggests a transition/quality route, but first-20-minute pressure, box entries and shot quality were not independently quantified.",
        "trailing_response": "Necaxa scoring first, a low-event draw, or Atlante failing to convert possession breaks a 65c away-win entry; the exact 90-minute contract must not be confused with broader season strength.",
        "lead_protection": "Atlante lead protection, bench depth and late-game transition control are unverified.",
        "failure_paths": ["0-0 or 1-1 draw", "Necaxa scores first", "unconfirmed XI, injury or rotation", "red card, penalty or weather/pitch disruption", "fee, spread and slippage consume thin edge", "stale or conflicting live score/clock/quote"],
        "conditional_trigger": "WAIT/PASS. Recheck official lineups, injuries/suspensions, venue weather, fresh CLOB depth/fees/slippage and live score/clock. Only reconsider below a verified friction-adjusted price cap; no paper position now.",
        "live_playbook": {"required": "Only study an in-play direction when official score, minute and CLOB bid/ask are each no older than 120 seconds; monitor field tilt, box entries, shot quality, set pieces and transitions.", "invalidate": "Stale/conflicting state, red card, key injury, major XI change or two consecutive checkpoints without repeatable Atlante chance quality."},
        "exit_plan": {"paper": "No paper position. Any later fully evidenced catalyst entry must be <=0.10u, use executable bid for trims, and hard-exit before 23:50 Beijing."},
        "research_missing_fields": ["confirmed_starting_XI", "complete_last_5_to_10_opponent_adjusted_results", "verified_xG_or_shot_quality_sequence", "injuries_and_suspensions", "matchday_weather", "independent_P_win_and_confidence_interval", "fresh_CLOB_depth_fees_slippage", "fresh_live_score_clock"],
        "next_review_at": "2026-07-17T11:00:00+08:00", "strength_classification": "第二候选/市场热门；研究证据不足，不是买入授权",
        "sources": [
            {"kind": "official", "title": "Club Necaxa official site", "url": "https://clubnecaxa.mx/"},
            {"kind": "independent", "title": "Sports Mole Necaxa vs Atlante preview, team news and lineups", "url": "https://www.sportsmole.co.uk/football/necaxa/preview/necaxa-vs-atlante-prediction-team-news-lineups_601210.html"},
            {"kind": "independent", "title": "Polymarket public market contract and CLOB snapshot", "url": "https://polymarket.com/"}
        ],
        "source_freshness": {"researched_at": NOW, "quote_snapshot": NOW}, "monitoring_level": "M3", "monitoring_activation": "unavailable", "monitoring_owner": "Codex automation; no live owner activated"
    }
    payload.update({"schema_version": "polymarket-sports-event-research-v1", "research_run_id": f"pm-sports-research-{NOW.replace(':','').replace('-','')}", "created_at": NOW, "report_date_beijing": "2026-07-17", "research_items": list(rows.values()), "paper_only": True, "live_orders_enabled": False, "private_api_used": False, "real_money_execution_authorized": False})
    PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status":"ok", "updated":"mex-nec-atla-2026-07-16", "research_items":len(rows), "created_at":NOW}, ensure_ascii=False))

if __name__ == "__main__":
    main()
