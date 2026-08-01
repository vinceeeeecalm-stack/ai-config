#!/usr/bin/env python3
"""Upsert this cycle's public-only sports research into the canonical artifact."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "experiments/current-sports-event-research.json"
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def item(event_slug, event_id, game_id, title, target_market_id, target_side,
         direction, official, independents, evidence, missing, method,
         settlement, first_goal, trailing, lead, failure, trigger, exit_plan):
    return {
        "event_slug": event_slug,
        "event_id": event_id,
        "game_id": game_id,
        "title": title,
        "target_market_id": target_market_id,
        "target_side": target_side,
        "direction": direction,
        "rules_clear": True,
        "settlement_rules": settlement,
        "research_status": "insufficient",
        "research_probability": None,
        "confidence_low": None,
        "confidence_high": None,
        "calibrated_oos_samples": 0,
        "maximum_acceptable_price": None,
        "method": method,
        "key_evidence": evidence,
        "first_goal_profile": first_goal,
        "trailing_response": trailing,
        "lead_protection": lead,
        "failure_paths": failure,
        "conditional_trigger": trigger,
        "live_playbook": {
            "required": "仅在比分、比赛分钟和 Polymarket 盘口时间戳均不超过120秒时才允许盘中观察；需同时核对射正、禁区触球、定位球和转换质量。",
            "invalidate": "早失球、红牌、关键伤退、首发重大变化或盘口/比赛状态过期。",
        },
        "exit_plan": {"paper": exit_plan},
        "research_missing_fields": missing,
        "next_review_at": "2026-07-15T15:00:00+08:00",
        "strength_classification": "方向性市场热门，但证据不足，不能升级为研究概率或入场授权",
        "sources": ([official] + independents),
    }


def main():
    if PATH.exists():
        payload = json.loads(PATH.read_text(encoding="utf-8"))
    else:
        payload = {"schema_version": "polymarket-sports-event-research-v1", "research_items": []}
    by_slug = {str(row.get("event_slug")): row for row in payload.get("research_items", [])}
    rows = [
        item(
            "auc-mcr-bjf-2026-07-15", "660057", "90117979",
            "Marlin Coast Rangers FC vs. Brunswick Juventus FC", "2773794", "YES", "AWAY",
            {"kind": "official", "title": "Football Australia: Hahn Australia Cup 2026 Round of 32 schedule", "url": "https://footballaustralia.com.au/news/hahn-australia-cup-2026-round-of-32-match-schedule-finalised"},
            [
                {"kind": "independent", "title": "Forebet: Marlin Coast Rangers vs Brunswick Juventus preview", "url": "https://www.forebet.com/en/football/matches/marlin-coast-rangers-brunswick-juventus-2470114"},
                {"kind": "independent", "title": "Tips.GG: Marlin Coast Rangers vs Brunswick Juventus prediction", "url": "https://tips.gg/matches/football/15-07-2026/marlin-coast-rangers-vs-brunswick-juventus/10-30/predictions/"},
            ],
            [
                "Football Australia confirms the fixture, venue Barlow Park Stadium and 7:30pm AEST kickoff.",
                "Forebet publishes an away-win estimate of 41%; Tips.GG aggregates bookmaker-facing estimates around 71% away, so independent pre-match anchors conflict materially.",
                "The Polymarket target is the exact 90-minute Brunswick Juventus win contract; market ask was about 77.0c with a 5c spread and taker fee schedule.",
            ],
            ["confirmed_starting_XI", "complete_last_5_to_10_comparable_results", "opponent_strength_adjustment", "xG_or_shot_quality_sequence", "injuries_and_suspensions", "weather_at_venue", "independent_probability"],
            "Market was used only for discovery. Public pages confirm the event but do not provide a sufficiently reconciled, independently reproducible team-strength and lineup model; conflicting external estimates require shrinkage to no probability.",
            "Polymarket resolves the stated Brunswick Juventus win market on 90 minutes plus stoppage time, not extra time or penalties.",
            "Brunswick has a plausible transition/quality edge, but no verified first-goal or shot-quality profile was available at research time.",
            "If Marlin scores first, the away-win path deteriorates sharply; if Brunswick trails, the cup setting can force higher-risk chasing.",
            "A Brunswick lead would support protection, but the away side's travel and late-game pressure path are not quantified.",
            "Draw after 90 minutes; early Marlin goal; Brunswick rotation or fatigue; venue/travel effect; red card; 90-minute result diverging from any cup-advance narrative.",
            "Do not enter. Recheck confirmed lineups, weather, reconciled recent-form data and a fresh executable ask; only reconsider paper if independent evidence converges and friction-adjusted edge clears the configured gate.",
            "No paper position. If a later, fully evidenced paper entry is opened, use a small probe only, sell part after a verified positive catalyst, and hard-exit before the monitoring deadline.",
        ),
        item(
            "auc-fb-nse-2026-07-15", "660058", "90117980",
            "FK Beograd vs. North Sunshine Eagles FC", "2773795", "YES", "HOME",
            {"kind": "official", "title": "Football Australia: Hahn Australia Cup 2026 Round of 32 schedule", "url": "https://footballaustralia.com.au/news/hahn-australia-cup-2026-round-of-32-match-schedule-finalised"},
            [
                {"kind": "independent", "title": "FotMob: FK Beograd vs North Sunshine Eagles match page", "url": "https://www.fotmob.com/en-GB/matches/fk-beograd-vs-north-sunshine-eagles-sc/406ip8hx"},
                {"kind": "independent", "title": "Forebet: FK Beograd vs North Sunshine Eagles preview", "url": "https://www.forebet.com/en/football/matches/fk-beograd-north-sunshine-eagles-2470115"},
            ],
            [
                "Football Australia confirms the fixture, Frank Mitchell Park venue and 8:00pm AEST kickoff.",
                "Forebet shows a 64% home-win model estimate and a 3-0 projected score, but this is an external model anchor, not independent event research.",
                "FotMob confirms the match identity and exposes live Opta-style shots/xG/lineup fields, but those fields were not yet populated with a confirmed XI at research time.",
                "The Polymarket target is the exact 90-minute FK Beograd win contract; market ask was about 62.0c with a 1c spread and taker fee schedule.",
            ],
            ["confirmed_starting_XI", "complete_last_5_to_10_North_Sunshine_results", "opponent_strength_adjustment", "verified_xG_or_shot_quality_sequence_for_both_teams", "injuries_and_suspensions", "weather_at_venue", "independent_probability"],
            "Market was used only for discovery. The event and one external model are verifiable, but lineup, injury, weather and a comparable recent-form/xG package are incomplete; no research probability is emitted.",
            "Polymarket resolves FK Beograd's 90-minute win, not extra time or penalties; the Australia Cup knockout context must not be confused with a team-to-advance contract.",
            "Home advantage and Forebet's model support a home-win path, but first-goal and shot-quality evidence is not independently verified.",
            "If North Sunshine scores first, the home-win thesis weakens; if FK Beograd leads, cup-game variance and late chasing remain relevant.",
            "FK Beograd can protect a lead at home, but the quality of that path is not supported by verified shot or lineup evidence.",
            "Draw after 90 minutes; North Sunshine counterattack; early red card; missing home attackers; fatigue; 90-minute result diverging from advancement.",
            "Do not enter. Recheck confirmed lineups, weather and fresh executable prices near kickoff; only reconsider a tiny paper probe if the missing evidence is resolved and friction-adjusted edge clears the gate.",
            "No paper position. If later evidence clears the gate, use an extremely small paper probe with a first-goal/catalyst trim and no overnight hold.",
        ),
        item(
            "ucl-ucr-mlv-2026-07-15", "655878", "90112941",
            "Universitatea Craiova CS vs. FK ML Viciebsk", "2759524", "YES", "HOME",
            {"kind": "official", "title": "UEFA: Universitatea Craiova vs ML Vitebsk match centre", "url": "https://www.uefa.com/uefachampionsleague/match/2047992--universitatea-craiova-vs-ml-vitebsk/"},
            [
                {"kind": "independent", "title": "The Stats Zone: Universitatea Craiova vs ML Vitebsk preview", "url": "https://www.thestatszone.com/universitatea-craiova-vs-ml-vitebsk-preview-prediction-2026-27-uefa-champions-league-first-qualifying-round-205864"},
                {"kind": "independent", "title": "Digi Sport: UEFA referee appointment for Craiova–ML Vitebsk", "url": "https://www.digisport.ro/fotbal/champions-league/uefa-a-decis-cine-va-arbitra-meciul-universitatea-craiova-ml-vitebsk-din-liga-campionilor-4377617"},
            ],
            [
                "UEFA match-centre wording identifies a first-qualifying-round fixture; the Polymarket target is the exact 90-minute Craiova win contract.",
                "The Stats Zone and Digi Sport independently confirm the fixture and competition context, but neither supplies a fully reconciled last-10/opponent-strength/xG model with confirmed XI.",
                "The market ask was about 77.0c with approximately 1c spread and taker fee; this is a price anchor only, not research probability.",
            ],
            ["confirmed_starting_XI", "complete_last_5_to_10_comparable_results", "opponent_strength_adjustment", "verified_xG_or_shot_quality_sequence", "injuries_and_suspensions", "weather_at_venue", "independent_probability"],
            "Official and independent pages confirm the event, but lineup, injury, weather and a reproducible comparative performance sample are incomplete; no independent probability is emitted.",
            "Polymarket resolves Craiova's win in 90 minutes plus stoppage time, not qualification or extra time/penalties.",
            "Home advantage and the market's favourite status imply a plausible control/territory path, but first-goal and shot-quality evidence is unverified.",
            "An early ML Vitebsk goal or low-event 0-0/1-1 path materially damages the home-win route; qualification incentives can be confused with 90-minute settlement.",
            "A Craiova lead could support controlled possession, but lead protection and rotation quality are unverified.",
            "Draw after 90 minutes; early away goal; unconfirmed rotation; low shot quality; red card; 90-minute result diverging from team-to-advance narrative.",
            "Do not enter. Recheck confirmed lineups, official match centre, weather and fresh executable ask; only reconsider if evidence converges and friction-adjusted edge clears the gate.",
            "No paper position. If later evidence clears the gate, use a tiny probe only and trim on a verified positive catalyst rather than assume qualification equals a win.",
        ),
        item(
            "ucl-sut-kai-2026-07-15", "659492", "90112948",
            "FK Sutjeska Nikšić vs. Qairat FK", "2771224", "YES", "AWAY",
            {"kind": "official", "title": "UEFA: Sutjeska vs Qairat match centre", "url": "https://www.uefa.com/uefachampionsleague/match/2048003--sutjeska-vs-kairat/"},
            [
                {"kind": "independent", "title": "Sports Mole: Kairat vs Sutjeska preview, team news and lineups", "url": "https://www.sportsmole.co.uk/football/kairat/champions-league/preview/kairat-vs-sutjeska-niksic-prediction-team-news-lineups_600788.html"},
                {"kind": "independent", "title": "Forebet: Sutjeska Niksic vs Kairat preview", "url": "https://www.forebet.com/en/football-match-previews/28834-sutjeskas-home-steel-faces-kairats-streak-as-second-leg-hangs-in-the-balance"},
            ],
            [
                "UEFA match-centre wording identifies the Champions League qualifying fixture; the Polymarket target is Qairat's exact 90-minute win.",
                "Sports Mole reports Qairat's recent four-match winning run and a large UEFA coefficient gap; Forebet independently provides a match preview, but these are narrative/model anchors rather than a reconciled event model.",
                "The market ask was about 57.0c with approximately 1c spread and taker fee; market price is not substituted for research probability.",
            ],
            ["confirmed_starting_XI", "complete_last_5_to_10_both_teams", "opponent_strength_adjustment", "verified_xG_or_shot_quality_sequence", "injuries_and_suspensions", "travel_and_weather", "independent_probability"],
            "Fixture and broad strength signals are independently reported, but confirmed XI, opponent-adjusted recent sample, injuries, weather and reproducible xG/shot quality remain incomplete; no research probability is emitted.",
            "Polymarket resolves Qairat's 90-minute win, not qualification, aggregate score or extra time/penalties.",
            "Qairat's reported form/quality gap suggests a transition and chance-creation route, but the first-goal profile is not independently quantified.",
            "A Sutjeska first goal, low-event draw, or Qairat travel/rotation issue breaks the away-win path; qualification and 90-minute settlement must remain separate.",
            "An away lead could support protection, but late-game control and substitutions are unverified.",
            "Draw after 90 minutes; early home goal; travel/fatigue; rotation; red card; 90-minute result diverging from advancement.",
            "Do not enter. Recheck confirmed lineups and fresh executable price near kickoff; only reconsider a tiny paper probe if the missing evidence resolves and net edge clears the gate.",
            "No paper position. If later evidence clears the gate, hard-exit any catalyst exposure before the monitoring deadline and never convert it into an overnight hold.",
        ),
    ]
    for row in rows:
        by_slug[row["event_slug"]] = row
    payload["schema_version"] = "polymarket-sports-event-research-v1"
    payload["research_run_id"] = f"pm-sports-research-{NOW.replace(':', '').replace('-', '')}"
    payload["created_at"] = NOW
    payload["report_date_beijing"] = "2026-07-15"
    payload["research_items"] = list(by_slug.values())
    payload["paper_only"] = True
    payload["live_orders_enabled"] = False
    payload["private_api_used"] = False
    payload["real_money_execution_authorized"] = False
    PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "updated": [row["event_slug"] for row in rows], "research_items": len(payload["research_items"]), "created_at": NOW}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
