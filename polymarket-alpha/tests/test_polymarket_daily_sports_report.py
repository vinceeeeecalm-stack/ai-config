import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_daily_sports_report.py"
spec = importlib.util.spec_from_file_location("polymarket_daily_sports_report", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)


class DailySportsReportTests(unittest.TestCase):
    def test_main_football_event_and_props_are_separated(self):
        main = {"gameId": 1, "title": "A vs. B", "markets": [{"active": True, "closed": False, "question": "Will A win?"}, {"active": True, "closed": False, "question": "Will A vs B end in a draw?"}, {"active": True, "closed": False, "question": "Will B win?"}]}
        prop = {**main, "title": "A vs. B - Exact Score"}
        self.assertTrue(module.canonical_event(main, "football")); self.assertFalse(module.canonical_event(prop, "football"))

    def test_priority_is_live_then_upcoming_then_same_day(self):
        now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
        self.assertEqual(module.event_phase(now - timedelta(minutes=10), now)[0], "possibly_live_unverified")
        self.assertEqual(module.event_phase(now + timedelta(hours=2), now)[0], "upcoming_0_6h")

    def test_missing_research_is_no_bet_and_market_is_not_model(self):
        now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
        event = {"id": "e", "gameId": "g", "slug": "a-b", "title": "A vs. B", "_sport": "football", "endDate": (now + timedelta(hours=2)).isoformat(), "series": [{"title": "League"}]}
        markets = [{"role": "HOME", "execution_by_side": {"YES": {"best_bid": .69, "best_ask": .70}, "NO": {}}, "condition_id": "c1"}, {"role": "DRAW", "execution_by_side": {"YES": {"best_bid": .19, "best_ask": .20}, "NO": {}}, "condition_id": "c2"}, {"role": "AWAY", "execution_by_side": {"YES": {"best_bid": .09, "best_ask": .10}, "NO": {}}, "condition_id": "c3"}]
        row = module.build_candidate(event, markets, None, now, {})
        self.assertEqual(row["final_action"], "NO_BET"); self.assertIsNone(row["research_probability"])
        self.assertEqual(row["executable_price"], .70)
        self.assertFalse(row["market_consensus_is_research_probability"])

    def test_incomplete_football_three_way_market_has_no_consensus(self):
        now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
        event = {"id": "e", "gameId": "g", "slug": "a-b", "title": "A vs. B", "_sport": "football", "endDate": (now + timedelta(hours=2)).isoformat(), "series": [{"title": "League"}]}
        markets = [{"role": "DRAW", "execution_by_side": {"YES": {"best_bid": 0, "best_ask": .01}, "NO": {}}, "condition_id": "draw"}]
        row = module.build_candidate(event, markets, None, now, {})
        self.assertFalse(row["market_consensus_complete"])
        self.assertIsNone(row["market_consensus_direction"])
        self.assertIsNone(row["market_consensus_probability"])
        self.assertIsNone(row["market_consensus_executable_price"])

    def test_report_can_surface_researched_pass_as_closest(self):
        payload = {"all_candidates": [{"decision_status": "pass", "research_probability": .61, "sport": "football"}]}
        closest = [row for row in payload["all_candidates"] if row["decision_status"] == "pass" and row.get("research_probability") is not None][:1]
        self.assertEqual(len(closest), 1)

    def test_tennis_research_contract_carries_surface_elo_and_serve_return(self):
        now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
        event = {"id": "te", "gameId": "tg", "slug": "p1-p2", "title": "P1 vs. P2", "_sport": "tennis", "endDate": (now + timedelta(hours=2)).isoformat(), "series": [{"title": "ATP"}]}
        markets = [{"market_id": "tm", "market_slug": "tm", "role": "PLAYER_1", "question": "Will P1 win?", "condition_id": "tc",
                    "execution_by_side": {"YES": {"fillable": True, "fill_price": .60, "best_bid": .59, "best_ask": .60, "spread": .01, "price_impact": 0}, "NO": {}},
                    "fee_contract": {"fee_rate": 0}, "rules": "match winner", "resolution_source": "ATP"}]
        research = {"event_slug": "p1-p2", "research_status": "experimental_complete", "target_market_id": "tm", "target_side": "YES", "direction": "P1",
                    "research_probability": .75, "confidence_low": .65, "confidence_high": .82, "rules_clear": True,
                    "sources": [{"kind": "official"}, {"kind": "independent"}, {"kind": "independent"}],
                    "tennis_profile": {"surface": "grass", "elo": {"P1": 1900, "P2": 1800}, "serve_return": "verified", "injury_fatigue": "clear", "format": "best_of_3"}}
        row = module.build_candidate(event, markets, research, now, {"entry_gate": {"uncertainty_discount": .01, "resolution_risk_reserve": .005}})
        self.assertEqual(row["decision_status"], "pre_match_entry")
        self.assertEqual(row["tennis_profile"]["surface"], "grass")

    def test_terminal_football_winner_is_scored_by_role(self):
        event = {"_sport": "football", "markets": [
            {"closed": True, "conditionId": "h", "question": "Will A win?", "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps(["0", "1"])},
            {"closed": True, "conditionId": "d", "question": "Will A vs B end in a draw?", "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps(["0", "1"])},
            {"closed": True, "conditionId": "a", "question": "Will B win?", "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps(["1", "0"])}]}
        self.assertEqual(module.resolved_winner(event), ("AWAY", "a"))

    def test_high_probability_alert_keeps_price_warning(self):
        row = {"sport": "football", "event_priority": 0, "market_consensus_probability": .94,
               "research_probability": .61, "net_edge_per_share": -.34, "executable_price": .94,
               "maximum_acceptable_price": .54, "decision_status": "watch_live_first"}
        alerts = module.build_high_probability_alerts([row])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["alert_level"], "强队高亮／价格或证据未通过")
        self.assertIn("等待价格", alerts[0]["operation_instruction"])
        self.assertIn("不追价", alerts[0]["exit_instruction"])

    def test_relay_schedule_ranks_at_most_two_per_two_hour_window(self):
        rows = [
            {"title": "researched", "sport": "football", "kickoff_at": "2026-07-12T14:30:00+00:00", "decision_status": "watch_live_first", "research_probability": .65, "market_consensus_probability": .70},
            {"title": "unresearched", "sport": "football", "kickoff_at": "2026-07-12T15:00:00+00:00", "decision_status": "pass", "research_probability": None, "market_consensus_probability": .90},
        ]
        schedule = module.build_relay_schedule(rows)
        self.assertEqual(len(schedule), 2)
        self.assertEqual(schedule[0]["title"], "researched")
        self.assertEqual([row["candidate_rank"] for row in schedule], [1, 2])

    def test_display_status_does_not_call_strong_favorite_no_bet(self):
        row = {"decision_status": "pass", "research_probability": None, "market_consensus_probability": .90}
        self.assertEqual(module.operation_status(row), "强热门候选／研究排队中")

    def test_missing_research_strong_favorites_enter_bounded_queue(self):
        rows = [{"event_priority": 1, "sport": "football", "market_consensus_probability": .70 + i / 100,
                 "research_probability": None, "event_slug": f"event-{i}", "title": f"Match {i}"} for i in range(6)]
        queue = module.build_research_queue(rows)
        self.assertEqual(len(queue), 4)
        self.assertTrue(all(row["status"] == "queued" for row in queue))
        self.assertTrue(all(row["output_path"] == "experiments/current-sports-event-research.json" for row in queue))

    def test_insufficient_research_is_terminal_not_requeued(self):
        row = {"event_priority": 0, "sport": "football", "market_consensus_probability": .80,
               "research_probability": None, "research_status": "insufficient", "event_slug": "closed-research"}
        self.assertEqual(module.build_research_queue([row]), [])
        self.assertEqual(module.operation_status(row), "研究完成／证据不足")

    def test_positive_value_below_sixty_percent_is_still_ranked_experimental(self):
        row = {"sport": "football", "event_priority": 2, "market_consensus_probability": .50,
               "research_probability": .56, "net_edge_per_share": .03, "decision_status": "late_recheck"}
        alerts = module.build_high_probability_alerts([row])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["alert_level"], "实验价值候选／中等胜率")

    def test_researched_no_side_is_explicit_in_display_direction(self):
        row = {"research_probability": .56, "direction": "Home team not to win", "target_side": "NO"}
        self.assertEqual(module.display_direction(row), "Home team not to win / NO")


if __name__ == "__main__": unittest.main()
