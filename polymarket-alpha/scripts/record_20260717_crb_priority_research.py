#!/usr/bin/env python3
"""Upsert CRB-Nautico public-only research into the canonical sports artifact."""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "experiments/current-sports-event-research.json"
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

payload = json.loads(PATH.read_text(encoding="utf-8"))
items = {str(x.get("event_slug")): x for x in payload.get("research_items", [])}
items["bra2-crb-nau-2026-07-16"] = {
    "event_slug": "bra2-crb-nau-2026-07-16", "event_id": "663401", "game_id": "90108035",
    "title": "CR Brasil vs. Clube Náutico Capibaribe", "target_market_id": "2786674",
    "target_side": "YES", "direction": "HOME", "rules_clear": True,
    "settlement_rules": "Polymarket resolves CR Brasil's exact 90-minute win plus stoppage time; extra time and penalties are excluded.",
    "research_status": "insufficient", "research_probability": None,
    "confidence_low": None, "confidence_high": None, "calibrated_oos_samples": 0,
    "maximum_acceptable_price": None, "alpha_maximum_acceptable_price": None,
    "method": "Market was used only as discovery and execution baseline. Official club preparation material, confirmed lineups from a Brazilian sports outlet, and independent match pages verify identity and team selection, but a fresh score/minute/CLOB bundle, complete opponent-adjusted recent sample, reproducible xG/shot-quality series, weather, and reconciled independent P_win were not jointly available. No probability is fabricated.",
    "key_evidence": [
        "CRB official confirms the Serie B round-18 fixture at Estádio Rei Pelé on 16 July 2026; the CBF schedule confirms the competition and kickoff.",
        "Itatiaia reported the confirmed starting lineups shortly before kickoff, including CRB's Vitor Caetano, Hereda, Bressan, Fábio Alemão, Lucas Lovat and Náutico's Muriel, Reginaldo, Léo Índio, Gustavo Henrique and others.",
        "Sofascore and FotMob independently verify the event identity and live-match coverage; public match-thread evidence reported CRB 1-0 in the second half, but the current scan's live quote/state was not refreshed within the 120-second gate.",
        "The captured Polymarket HOME baseline was about 79c bid / 82c ask with a 3c spread and sports taker fee; it is market-implied context only, not independent research probability."
    ],
    "first_goal_profile": "CRB home territory and the reported first-goal path support a plausible home route, but first-20-minute pressure, box entries, shot quality and repeatable scoring routes were not independently quantified.",
    "trailing_response": "Náutico scoring first, a low-event draw, or CRB failing to convert pressure would break an 82c home-win entry; stale live state prevents a valid in-play update.",
    "lead_protection": "A CRB lead can support controlled possession, but late defensive protection, substitutions and transition exposure are not verified in a current statistical bundle.",
    "failure_paths": ["0-0 or 1-1 draw", "Náutico scores first or wins transitions", "unverified late injury/rotation", "red card or penalty", "3c spread plus fees/slippage consumes edge", "stale score/minute/quote bundle"],
    "conditional_trigger": "WAIT/PASS. Only reconsider after official score, minute and CLOB bid/ask are each no older than 120 seconds and a friction-adjusted independent estimate clears the price gate.",
    "live_playbook": {
        "required": "M3/unavailable until score, minute and executable quote are refreshed together; if refreshed, track box entries, shot quality, set pieces and transition concessions.",
        "invalidate": "Any stale/conflicting live state, red card, key injury, or loss of repeatable CRB chance creation."
    },
    "exit_plan": {"paper": "No paper position. If a later fully evidenced catalyst clears the gate, cap at 0.10u, use executable bid for trims and hard-exit catalyst exposure before 23:50 Beijing."},
    "research_missing_fields": ["verified_score_minute_and_CLOB_age_under_120_seconds", "complete_last_5_to_10_opponent_adjusted_results", "verified_xG_or_shot_quality_sequence", "injuries_and_suspensions", "weather_at_venue", "independent_P_win_and_confidence_interval", "fresh_CLOB_depth_fees_slippage"],
    "next_review_at": "2026-07-17T08:00:00+08:00",
    "strength_classification": "第一候选/市场强热门；研究证据不足，不是买入授权",
    "sources": [
        {"kind": "official", "title": "CRB official preparation notice", "url": "https://www.crboficial.com.br/futebol/crb-finaliza-preparacao-para-enfrentar-o-nautico-pela-18a-rodada-da-serie-b"},
        {"kind": "official", "title": "CBF Série B 2026 detailed schedule", "url": "https://stcbfsiteprdimgbrs.blob.core.windows.net/img-site/cdn/Tabela_Detalhada_Brasileiro_Serie_B_2026_28_05_47bc407866.pdf"},
        {"kind": "independent", "title": "Itatiaia confirmed lineups", "url": "https://www.itatiaia.com.br/esportes/crb-x-nautico-veja-escalacoes-para-jogo-da-serie-b-do-campeonato-brasileiro/"},
        {"kind": "independent", "title": "Sofascore live match page", "url": "https://www.sofascore.com/football/match/crb-nautico/lPsHPi"},
        {"kind": "independent", "title": "FotMob match page", "url": "https://www.fotmob.com/matches/nautico-vs-crb/2n0d8b2"}
    ],
    "source_freshness": {"researched_at": NOW, "quote_snapshot": "2026-07-17T07:29:00+08:00"},
    "monitoring_level": "M3", "monitoring_activation": "unavailable", "monitoring_owner": "Codex automation; no live owner activated"
}
payload.update({"schema_version": "polymarket-sports-event-research-v1", "research_run_id": f"pm-sports-research-{NOW.replace(':','').replace('-','')}", "created_at": NOW, "report_date_beijing": "2026-07-17", "research_items": list(items.values()), "paper_only": True, "live_orders_enabled": False, "private_api_used": False, "real_money_execution_authorized": False})
PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"status": "ok", "updated": "bra2-crb-nau-2026-07-16", "research_items": len(items), "created_at": NOW}, ensure_ascii=False))
