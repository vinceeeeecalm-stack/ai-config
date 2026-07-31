#!/usr/bin/env python3
"""Record the 2026-07-17 football-priority research item in the canonical artifact."""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "experiments/current-sports-event-research.json"
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main():
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    by_slug = {str(row.get("event_slug")): row for row in payload.get("research_items", [])}
    row = {
        "event_slug": "chi-hen-hai-2026-07-17",
        "event_id": "663699",
        "game_id": "90108717",
        "title": "Henan FC vs. Qingdao Hainiu FC",
        "target_market_id": "2787544",
        "target_side": "YES",
        "direction": "HOME",
        "rules_clear": True,
        "settlement_rules": "Polymarket resolves Henan FC's exact 90-minute win plus stoppage time; extra time and penalties are excluded.",
        "research_status": "insufficient",
        "research_probability": None,
        "confidence_low": None,
        "confidence_high": None,
        "calibrated_oos_samples": 0,
        "maximum_acceptable_price": None,
        "alpha_maximum_acceptable_price": None,
        "method": "Market was used only for discovery and execution baseline. The CSL schedule identity and two independent previews were checked, but confirmed XI, opponent-adjusted last-5-to-10 opportunity-quality data, verified xG/shot-quality sequence, matchday weather and a reconciled independent P_win were not available. No probability is fabricated.",
        "key_evidence": [
            "The Polymarket contract is Henan FC to win on 17 July 2026 in 90 minutes plus stoppage time; the scan captured 72c bid, 73c ask, 1c spread and a taker-fee schedule.",
            "The Chinese Super League source is the market's stated resolution source; public previews from 7M Sport and SportsGambler identify the fixture and discuss team news, but neither supplies a verified matchday XI or a reproducible opponent-adjusted model.",
            "FootballAnt publishes a 3-1 Henan projection while other public previews and H2H material show Qingdao upset/draw paths; the conflict is a reason to shrink to no independent probability rather than copy a projection.",
            "Public disciplinary reporting indicates 2026 point deductions affecting Henan and Qingdao Hainiu; this changes table context but does not by itself prove 90-minute win value."
        ],
        "first_goal_profile": "Henan home territory and the market's home-favourite status support a possible first-goal path, but first-20-minute pressure, box entries, set-piece volume and shot quality were not independently quantified.",
        "trailing_response": "Qingdao scoring first, a low-event 0-0/1-1 draw, or Henan failing to convert pressure all break a 73c home-win entry; a single preview projection cannot distinguish these paths.",
        "lead_protection": "Henan may protect a lead, but defensive continuity, goalkeeper form, substitutions and late transition exposure are unverified.",
        "failure_paths": [
            "0-0 or 1-1 low-event draw",
            "Qingdao scores first or wins transition exchanges",
            "unconfirmed attacking/defensive absences or rotation",
            "red card, penalty or weather/pitch disruption",
            "market fee and slippage consume a thin theoretical edge",
            "90-minute result differs from broader table-strength narrative"
        ],
        "conditional_trigger": "WAIT/PASS. Recheck official lineups, injuries/suspensions, venue weather, fresh CLOB depth/fees/slippage and a reconciled independent estimate. Only reconsider below a verified friction-adjusted price cap; no paper position now.",
        "live_playbook": {
            "required": "Only study an in-play direction when official score, minute and CLOB bid/ask are each no older than 120 seconds; monitor field tilt, box entries, shot quality, set pieces and transition concessions.",
            "invalidate": "Stale/conflicting state, red card, key injury, major XI change, or two consecutive checkpoints without repeatable Henan box access while Qingdao counters remain dangerous."
        },
        "exit_plan": {
            "paper": "No paper position. If a later fully evidenced catalyst entry clears the gate, cap at 0.10u, use executable bid for any trim, recover stake after a verified repricing and hard-exit before 23:50 Beijing."
        },
        "research_missing_fields": [
            "confirmed_starting_XI",
            "complete_last_5_to_10_opponent_adjusted_results",
            "verified_xG_or_shot_quality_sequence",
            "injuries_and_suspensions",
            "matchday_weather",
            "independent_P_win_and_confidence_interval",
            "fresh_CLOB_depth_fees_slippage"
        ],
        "next_review_at": "2026-07-17T17:30:00+08:00",
        "strength_classification": "第一候选/市场强热门；研究证据不足，不是买入授权",
        "sources": [
            {"kind": "official", "title": "Chinese Super League official site / resolution source", "url": "https://www.csl-china.com/"},
            {"kind": "independent", "title": "7M Sport Henan vs Qingdao Hainiu preview", "url": "https://news.7msport.com/news/newsdata/20260716/257923.shtml"},
            {"kind": "independent", "title": "SportsGambler Henan vs Qingdao Hainiu preview", "url": "https://www.sportsgambler.com/betting-tips/football/henan-vs-qingdao-hainiu-prediction-lineups-odds-2026-07-17/"},
            {"kind": "independent", "title": "FootballAnt Henan vs Qingdao Hainiu lineup and prediction", "url": "https://www.footballant.com/match-news/matches/henan-football-club-vs-qingdao-manatee-lineup-2026/"},
            {"kind": "independent", "title": "AS reporting on 2026 CSL disciplinary point deductions", "url": "https://as.com/futbol/internacional/golpe-al-futbol-de-china-73-personas-sancionadas-y-castigo-a-13-clubes-f202601-n/"}
        ],
        "source_freshness": {"researched_at": NOW, "quote_snapshot": "2026-07-17T07:29:34+08:00"},
        "monitoring_level": "M3",
        "monitoring_activation": "unavailable",
        "monitoring_owner": "Codex automation; no live owner activated"
    }
    by_slug[row["event_slug"]] = row
    payload.update({
        "schema_version": "polymarket-sports-event-research-v1",
        "research_run_id": f"pm-sports-research-{NOW.replace(':', '').replace('-', '')}",
        "created_at": NOW,
        "report_date_beijing": "2026-07-17",
        "research_items": list(by_slug.values()),
        "paper_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "real_money_execution_authorized": False,
    })
    PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "updated": row["event_slug"], "research_items": len(by_slug), "created_at": NOW}, ensure_ascii=False))


if __name__ == "__main__":
    main()
