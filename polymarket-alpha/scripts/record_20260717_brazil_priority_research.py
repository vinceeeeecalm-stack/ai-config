#!/usr/bin/env python3
"""Add the current Brazil priority event to the canonical sports research contract."""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "experiments/current-sports-event-research.json"
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def main():
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    by_slug = {str(x.get("event_slug")): x for x in payload.get("research_items", [])}
    by_slug["bra-bot-san-2026-07-16"] = {
        "event_slug": "bra-bot-san-2026-07-16", "event_id": "663398", "game_id": "90104777",
        "title": "Botafogo FR vs. Santos FC", "target_market_id": "2786665", "target_side": "YES",
        "direction": "HOME", "rules_clear": True,
        "settlement_rules": "Polymarket resolves Botafogo's exact 90-minute win plus stoppage time in Brasileirão, not extra time, penalties, or team to advance.",
        "research_status": "insufficient", "research_probability": None, "confidence_low": None,
        "confidence_high": None, "calibrated_oos_samples": 0, "maximum_acceptable_price": None,
        "alpha_maximum_acceptable_price": None,
        "method": "Public CBF fixture, club material and independent match pages confirm identity and kickoff. The match is possibly live, but a score/minute/CLOB bundle no older than 120 seconds, confirmed XI, complete opponent-adjusted recent form and reproducible xG/shot-quality sample were not jointly verified; no independent P_win is emitted.",
        "key_evidence": [
            "CBF confirms Botafogo-Santos on 16 July 2026 at Nilton Santos in Brasileirão round 19; Botafogo's official ticket notice confirms the same fixture.",
            "GE and SofaScore independently identify the match and 22:30 UTC kickoff, but a stale or incomplete live bundle cannot support an in-play direction.",
            "The captured Polymarket baseline was HOME about 67.8% with executable ask about 68.0c; this is market-implied context only, not independent research probability.",
            "No verified matchday XI, injuries/suspensions, weather, opponent-adjusted last-5-to-10/xG sequence, or current depth/fee/slippage bundle was available together at the research timestamp."
        ],
        "first_goal_profile": "Botafogo home pressure is a plausible first-goal route, but first-20-minute territory, box entries and chance quality were not independently quantified.",
        "trailing_response": "Santos scoring first or a 0-0/1-1 low-event path would materially weaken the 68c home-win entry; any live repricing requires a fresh score/minute/quote bundle.",
        "lead_protection": "Botafogo may protect a lead through home control, but defensive/bench continuity and late-game protection were not verified.",
        "failure_paths": ["low-event draw", "Santos scores first or sustains transition pressure", "unconfirmed attacking or defensive absence", "red card/penalty", "stale live state or CLOB quote", "fees/slippage consume the thin edge"],
        "conditional_trigger": "WAIT/PASS. Only reconsider if official match state, score, minute and CLOB bid/ask are each no older than 120 seconds and the independent evidence clears the friction-adjusted price gate.",
        "live_playbook": {"required": "Until the live bundle is verified, monitoring is M3/unavailable; if refreshed, track field tilt, box entries, shot quality, set pieces and transition concessions.", "invalidate": "Any stale/conflicting live data, red card, key injury or loss of repeatable home chance creation."},
        "exit_plan": {"paper": "No paper position and no live entry; do not infer a fill or exit from the stale scan."},
        "research_missing_fields": ["verified_score_minute_and_CLOB_age_under_120_seconds", "confirmed_starting_XI", "complete_last_5_to_10_opponent_adjusted_results", "verified_xG_or_shot_quality_sequence", "injuries_and_suspensions", "weather_at_venue", "independent_P_win_and_confidence_interval", "fresh_CLOB_depth_fees_slippage"],
        "next_review_at": "2026-07-17T10:00:00+08:00",
        "strength_classification": "今日第一候选；市场热门但研究门禁未完成，不是买入授权",
        "sources": [
            {"kind": "official", "title": "CBF official fixture page", "url": "https://www.cbf.com.br/futebol-brasileiro/jogos/campeonato-brasileiro/serie-a/2026/botafogo-x-santos-fc/832071"},
            {"kind": "official", "title": "Botafogo official fixture notice", "url": "https://www.botafogo.com.br/noticias/ingressos-botafogo-x-santos-botafogo-x-vitoria"},
            {"kind": "independent", "title": "GE live match page", "url": "https://ge.globo.com/rj/futebol/brasileirao-serie-a/jogo/16-07-2026/botafogo-santos.ghtml"},
            {"kind": "independent", "title": "SofaScore match page", "url": "https://www.sofascore.com/pt/football/match/santos-botafogo/iOstO"}
        ]
    }
    payload.update({"schema_version": "polymarket-sports-event-research-v1", "research_run_id": f"pm-sports-research-{NOW.replace(':','').replace('-','')}", "created_at": NOW, "report_date_beijing": "2026-07-17", "research_items": list(by_slug.values()), "paper_only": True, "live_orders_enabled": False, "private_api_used": False, "real_money_execution_authorized": False})
    PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "updated": "bra-bot-san-2026-07-16", "research_items": len(by_slug), "created_at": NOW}, ensure_ascii=False))

if __name__ == "__main__":
    main()
