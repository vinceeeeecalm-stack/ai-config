import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_daily_priority.py"
spec = importlib.util.spec_from_file_location("polymarket_daily_priority", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)


class DailyPriorityTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_boundaries_include_ongoing_and_near_expiry_through_twenty_four_hours(self):
        now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
        self.assertTrue(module.within_daily_window({"endDate": "2026-07-12T13:00:00+00:00"}, now))
        self.assertTrue(module.within_daily_window({"endDate": "2026-07-13T12:00:00+00:00"}, now))
        self.assertTrue(module.within_daily_window({"endDate": "2026-07-12T12:00:01+00:00"}, now))
        self.assertFalse(module.within_daily_window({"endDate": "2026-07-12T11:59:59+00:00"}, now))
        self.assertFalse(module.within_daily_window({"endDate": "2026-07-13T12:00:01+00:00"}, now))

    def test_maximum_price_preserves_required_net_edge(self):
        policy = module.core.read_json(module.ROOT / "config/policy.json")
        candidate = {"model_probability": .85, "fee_rate": .04}
        price = module.maximum_acceptable_price(candidate, policy)
        conservative = .85 - policy["entry_gate"]["uncertainty_discount"]
        edge = conservative - price - .04 * price * (1 - price) - policy["entry_gate"]["resolution_risk_reserve"]
        self.assertGreaterEqual(edge + 1e-6, policy["entry_gate"]["min_net_edge_per_share"])

    def test_daily_inventory_rejects_nonbinary_market(self):
        now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
        market = {"id": "m", "conditionId": "c", "question": "Q", "endDate": "2026-07-12T18:00:00+00:00",
                  "active": True, "closed": False, "acceptingOrders": True,
                  "outcomes": '["A","B","C"]', "clobTokenIds": '["a","b","c"]'}
        eligible, excluded = module.daily_inventory([market], now)
        self.assertEqual(eligible, [])
        self.assertIn("binary_tokens_missing", excluded[0]["reasons"])

    def test_gamma_detail_must_still_be_inside_daily_window(self):
        now = datetime(2026, 7, 12, 4, tzinfo=timezone.utc)
        sampling = [{"id": "c", "conditionId": "c", "question": "Q", "endDate": "2026-07-13T03:00:00+00:00"}]
        detail = [{"id": "m", "conditionId": "c", "question": "Q", "endDate": "2026-07-13T23:59:00+00:00"}]
        accepted, conflicts = module.reconcile_detail_windows(sampling, detail, now)
        self.assertEqual(accepted, [])
        self.assertEqual(conflicts[0]["sampling_end_date"], sampling[0]["endDate"])
        self.assertEqual(conflicts[0]["gamma_end_date"], detail[0]["endDate"])

    def test_market_family_prevents_esports_from_using_football_models(self):
        market = {"question": "Will Bilibili Gaming win MSI 2026?", "description": "LoL Esports", "domain": "sports"}
        self.assertEqual(module.market_family(market), "esports_tournament_outright")
        registry = {"models": [{"model_version": "pm-football-v1", "domain": "sports", "paper_estimates_allowed": True}]}
        decisions = [{"market_id": "m", "question": market["question"], "description": market["description"],
                      "domain": "sports", "end_date": "2026-07-13T00:00:00Z", "side": "YES",
                      "execution": {"fill_price": .5, "spread": .01, "price_impact": 0},
                      "planned_notional_usd": 63.75, "failed_gates": ["probability_estimate_missing"],
                      "gate_passed": False}]
        row = module.build_market_watchlist(decisions, registry)[0]
        self.assertEqual(row["market_family"], "esports_tournament_outright")
        self.assertEqual(row["applicable_registered_model_versions"], [])
        self.assertEqual(row["route_blocker"], "no_applicable_registered_model")
        self.assertEqual(row["action"], "PASS_NO_TRADE")

    def test_watchlist_market_consensus_is_explicitly_not_model_probability(self):
        base = {"market_id": "m", "question": "Q", "domain": "other", "end_date": "2026-07-13T00:00:00Z",
                "planned_notional_usd": 25, "failed_gates": ["probability_estimate_missing"], "gate_passed": False}
        yes = {**base, "side": "YES", "execution": {"fill_price": .76, "best_bid": .74, "best_ask": .76, "spread": .02, "price_impact": 0}}
        no = {**base, "side": "NO", "execution": {"fill_price": .26, "best_bid": .24, "best_ask": .26, "spread": .02, "price_impact": 0}}
        item = module.build_market_watchlist([yes, no], {"models": []})[0]
        self.assertAlmostEqual(item["normalized_yes_midpoint"], .75)
        self.assertEqual(item["market_consensus_side"], "YES")
        self.assertFalse(item["market_consensus_is_model_probability"])

    def test_crypto_barrier_family_matches_only_barrier_model(self):
        market = {"question": "Will Ethereum dip to $1,700 this week?", "domain": "crypto"}
        self.assertEqual(module.market_family(market), "crypto_price_barrier")
        self.assertEqual(module.registered_model_family({"model_version": "binance-spot-first-passage-barrier-v1"}),
                         "crypto_price_barrier")
        self.assertEqual(module.registered_model_family({"model_version": "pm-lolesports-official-gpr-v1-research"}),
                         "esports_match_winner")

    def test_esports_prop_does_not_inherit_match_winner_model(self):
        market = {"question": "Will there be 4+ Pentakills at MSI?", "domain": "sports"}
        self.assertEqual(module.market_family(market), "esports_tournament_prop")
        self.assertNotEqual(module.market_family(market),
                            module.registered_model_family({"model_version": "pm-lolesports-official-gpr-v1-research"}))

    def test_direct_match_and_tournament_outright_are_isolated(self):
        direct = {"question": "LoL: Bilibili Gaming vs Hanwha Life Esports", "domain": "sports"}
        outright = {"question": "Will Bilibili Gaming win MSI 2026?", "domain": "sports"}
        self.assertEqual(module.market_family(direct), "esports_match_winner")
        self.assertEqual(module.market_family(outright), "esports_tournament_outright")

    def test_failed_applicable_model_enters_no_retune_queue(self):
        policy = module.core.read_json(module.ROOT / "config/policy.json")
        watchlist = [{"market_family": "crypto_price_barrier", "yes_spread": .01, "yes_price_impact": 0,
                      "no_spread": .01, "no_price_impact": 0}]
        registry = {"models": [{"model_version": "binance-spot-first-passage-barrier-v1-research",
                                 "paper_estimates_allowed": False}]}
        queue = module.build_model_coverage_queue(watchlist, registry, {"families": {}}, policy)
        self.assertEqual(queue[0]["coverage_state"], "applicable_model_blocked_or_failed")
        self.assertTrue(queue[0]["repeat_tuning_prohibited"])
        self.assertFalse(queue[0]["paper_entry_allowed"])

    def test_outright_queue_does_not_inherit_direct_match_model(self):
        policy = module.core.read_json(module.ROOT / "config/policy.json")
        watchlist = [{"market_family": "esports_tournament_outright", "yes_spread": .01, "yes_price_impact": 0,
                      "no_spread": .01, "no_price_impact": 0}]
        registry = {"models": [{"model_version": "pm-lolesports-official-gpr-v1-research",
                                 "paper_estimates_allowed": False}]}
        family = {"families": {"esports": {"model_status": "blocked", "reason": "direct_match_failed"}}}
        queue = module.build_model_coverage_queue(watchlist, registry, family, policy)
        self.assertEqual(queue[0]["coverage_state"], "no_applicable_model")
        self.assertFalse(queue[0]["repeat_tuning_prohibited"])
        self.assertIn("bracket_path_model", queue[0]["next_research_action"])


if __name__ == "__main__":
    unittest.main()
