import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_daily_manual_report.py"
spec = importlib.util.spec_from_file_location("polymarket_daily_manual_report", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
POLICY = module.core.read_json(module.ROOT / "config/policy.json")

def row(*, probability=.85, ci=.76, edge=.06, failures=None, passed=False, market_id="m"):
    failures = failures if failures is not None else []
    return {"market_id": market_id, "question": "Q", "side": "YES", "end_date": "2026-07-13T00:00:00Z",
        "model_probability": probability, "confidence_low": ci, "confidence_high": .9,
        "calibration_samples": 40, "model_version": "v", "net_ev_per_share": edge, "fee_rate": 0,
        "historical_hit_rate": .84, "model_brier_score": .10, "market_brier_score": .11,
        "model_log_loss": .30, "market_log_loss": .31, "market_yes_price": .75, "market_no_price": .25,
        "planned_notional_usd": 25, "failure_paths": [{"name": "x", "controlled": True}],
        "execution": {"fillable": True, "fill_price": .75, "best_bid": .73, "best_ask": .75, "spread": .01, "price_impact": 0},
        "failed_gates": failures, "gate_passed": passed}

class ManualReportTests(unittest.TestCase):
    def test_operational_priority_is_ongoing_then_same_day_then_next_24h(self):
        now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
        live = row(passed=True, market_id="live"); live.update({"game_start_time":"2026-07-12T11:00:00Z","end_date":"2026-07-12T14:00:00Z","event_status":"live","live_state_updated_at":"2026-07-12T11:59:30Z"})
        same = row(passed=True, market_id="same"); same["end_date"]="2026-07-12T15:00:00Z"
        later = row(passed=True, market_id="later"); later["end_date"]="2026-07-13T08:00:00Z"
        items = module.best_side_per_market([later, same, live], POLICY, now)
        self.assertEqual([item["market_id"] for item in items], ["live", "same", "later"])
        self.assertEqual(items[0]["live_data_status"], "fresh")

    def test_ongoing_recommendation_requires_fresh_live_state(self):
        now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
        live = row(passed=True); live.update({"game_start_time":"2026-07-12T11:00:00Z","end_date":"2026-07-12T14:00:00Z","event_status":"live"})
        item = module.best_side_per_market([live], POLICY, now)[0]
        self.assertEqual(item["category"], "conditional_watch")
        self.assertEqual(item["final_action"], "WAIT")
        self.assertEqual(item["position_management_mode"], "wait_live_data_verification")

    def test_exit_plan_holds_only_with_remaining_probability_gap(self):
        now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
        strong = row(passed=True); strong["execution"]["best_bid"] = .80
        item = module.best_side_per_market([strong], POLICY, now)[0]
        self.assertTrue(item["hold_to_settlement_allowed"])
        self.assertEqual(item["position_management_mode"], "hold_to_settlement")
        weak = row(passed=True, market_id="weak"); weak["execution"]["best_bid"] = .86
        weak_item = module.best_side_per_market([weak], POLICY, now)[0]
        self.assertEqual(weak_item["position_management_mode"], "full_exit")

    def test_action_categories(self):
        self.assertEqual(module.classify(row(passed=True), POLICY), "alpha_primary_recommendation")
        high_win = row(probability=.95, ci=.90, edge=.01, failures=["net_ev_below_gate"])
        high_win["historical_hit_rate"] = .94
        self.assertEqual(module.classify(high_win, POLICY), "high_win_small_return_recommendation")
        self.assertEqual(module.classify(row(edge=.01, failures=["net_ev_below_gate"]), POLICY), "conditional_watch")
        self.assertEqual(module.classify(row(probability=.7, ci=.6, failures=["model_probability_below_gate", "confidence_lower_below_gate"]), POLICY), "conditional_watch")
        market_only = row(probability=None, ci=None, edge=None, failures=["probability_estimate_missing"]); market_only["model_version"] = None; market_only["market_yes_price"] = .95
        self.assertEqual(module.classify(market_only, POLICY), "no_bet")
        insufficient = dict(market_only); insufficient["market_yes_price"] = .55; insufficient["market_no_price"] = .45
        self.assertEqual(module.classify(insufficient, POLICY), "no_bet")

    def test_uncalibrated_but_complete_research_can_be_experimental(self):
        item=row(probability=.72,ci=.55,edge=.08,failures=["domain_model_calibration_insufficient"])
        item["calibration_samples"]=0;item["source_counts"]={"official":1,"independent":2};item["rules_review"]={"status":"clear"}
        self.assertEqual(module.classify(item,POLICY),"experimental_research_recommendation")

    def test_limits_watch_and_recommendations(self):
        decisions = [row(passed=True, market_id=str(i)) for i in range(6)]
        payload = module.build({"status": "ok", "cycle_id": "c", "inventory_1_24h_count": 6, "all_decisions": decisions}, {"closed_positions": []}, POLICY)
        self.assertEqual(payload["watch_item_count"], 2); self.assertEqual(payload["recommendation_count"], 2)
        self.assertFalse(payload["real_money_execution_authorized"]); self.assertTrue(payload["manual_confirmation_required"])

    def test_missing_model_never_borrows_market_price(self):
        missing = row(probability=None, ci=None, edge=None, failures=["probability_estimate_missing"])
        missing["model_version"] = None
        no_side = dict(missing); no_side["side"] = "NO"; no_side["execution"] = {"fillable": True, "fill_price": .27, "best_bid": .25, "best_ask": .27, "spread": .01, "price_impact": 0}
        item = module.best_side_per_market([missing, no_side], POLICY)[0]
        self.assertIsNone(item["model_estimated_probability"]); self.assertEqual(item["suggested_paper_amount_usd"], 0)
        self.assertEqual(item["final_action"], "NO_BET"); self.assertIsNone(item["side"])
        self.assertEqual(item["bet_direction"], "NO_BET")
        self.assertEqual(item["execution_tests_by_side"]["YES"]["executable_price"], .75)
        self.assertEqual(item["execution_tests_by_side"]["NO"]["executable_price"], .27)
        self.assertEqual(item["market_consensus_side"], "YES")
        self.assertFalse(item["market_consensus_is_model_probability"])

    def test_high_win_size_and_alpha_priority(self):
        alpha = row(passed=True, market_id="alpha")
        high_win = row(probability=.95, ci=.90, edge=.01, failures=["net_ev_below_gate"], market_id="small")
        high_win["historical_hit_rate"] = .94
        payload = module.build({"status": "ok", "cycle_id": "c", "inventory_1_24h_count": 2,
                                "all_decisions": [high_win, alpha]}, {"closed_positions": [], "equity_usd": 500}, POLICY)
        self.assertEqual(payload["alpha_primary_recommendation_count"], 1)
        self.assertEqual(payload["high_win_small_return_recommendation_count"], 1)
        self.assertEqual(payload["watch_items"][0]["recommendation_type"], "alpha_primary")

    def test_blocked_daily_still_emits_cash_report(self):
        payload = module.build({"status": "blocked", "cycle_id": "c", "inventory_1_24h_count": 0, "all_decisions": []}, {"closed_positions": []}, POLICY)
        self.assertEqual(payload["status"], "degraded_cash_only"); self.assertEqual(payload["decision"], "cash_no_recommendation")

if __name__ == "__main__": unittest.main()
