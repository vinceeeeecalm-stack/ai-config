import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_alpha.py"
spec = importlib.util.spec_from_file_location("polymarket_alpha", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class PolymarketAlphaTests(unittest.TestCase):
    @staticmethod
    def closed_trade(trade_id, opened_at, closed_at, pnl=10.0, resolution="YES"):
        return {
            "paper_trade_id": trade_id, "opened_at": opened_at, "closed_at": closed_at,
            "domain": "sports", "model_probability_at_entry": 0.85,
            "resolution": resolution, "net_pnl_usd": pnl, "cost_basis_usd": 50.0,
            "entry_fee_usd": 0.1, "fee_rate": 0.02, "spread_at_entry": 0.01,
            "price_impact_at_entry": 0.001, "slippage_at_entry": 0.0005,
            "clv_per_share": 0.02, "clv_observed_at": closed_at, "clv_horizon_minutes": 120,
        }

    @staticmethod
    def complete_back_case(trade_id):
        return {
            "back_case_id": f"b-{trade_id}", "paper_trade_id": trade_id, "status": "reviewed",
            "avoidable_rule_misread": False,
            "review_answers": {
                "data_completeness_and_latency": "complete_and_timely",
                "failed_assumption": "base_rate_shift",
                "rules_news_liquidity_omissions": "none_found",
                "market_lead_lag": "market_led",
                "correct_counterfactual_action": "pass",
                "new_downgrade_rule_or_probability_cap": "cap_at_0.80",
                "historical_replay": {"status": "passed", "artifact": "replay-1"},
                "oos_before_after": {"status": "insufficient_samples"},
                "defect_classification": "repeatable_model_defect",
            },
        }

    @staticmethod
    def observation_events(start, end, gap_hours=6):
        events = []
        point = start
        while point <= end:
            events.append({"at": point.isoformat(), "type": "paper_observation_cycle", "market_discovery_complete": True})
            point += timedelta(hours=gap_hours)
        return events
    def test_offline_self_test(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        result = module.self_test(policy)
        self.assertEqual(result["status"], "pass")
        self.assertIs(result["live_orders_enabled"], False)

    def test_domain_router(self):
        self.assertEqual(module.route_domain({"question": "Will the Fed cut rates?"}), "macro")
        self.assertEqual(module.route_domain({"question": "Will team A win the World Cup match?"}), "sports")
        self.assertEqual(module.route_domain({"question": "Will Bitcoin exceed $100k?"}), "crypto")
        self.assertEqual(module.route_domain({"question": "Whether Iran targets shipping"}), "other")
        self.assertEqual(module.route_domain({"question": "Will Trump post Bitcoin this week?"}), "social_count")
        self.assertEqual(module.route_domain({"question": "Will the Wimbledon winner defend?", "tags": ["Tennis"]}), "sports")
        self.assertEqual(module.route_domain({"question": "Will Microsoft shares rise?"}), "technology")
        self.assertEqual(module.route_domain({"question": "New Playboi Carti Album before GTA VI?", "description": "the game becoming public"}), "other")
        self.assertEqual(module.route_domain({"question": "Will Drake perform at the 2026 FIFA World Cup Final halftime show?", "tags": ["Sports", "Music", "culture/mentions"]}), "other")
        self.assertEqual(module.route_domain({"question": "Will Russia capture Serhiivka by July 31?", "tags": ["Ukraine", "Geopolitics"]}), "politics")
        self.assertEqual(module.route_domain({"question": "Will the Bank of Korea increase the base rate after the July Meeting?", "tags": ["Economy", "Global Rates"]}), "macro")
        details = module.route_domain_details({"question": "Will the Wimbledon winner defend?", "tags": ["Tennis"]})
        self.assertEqual((details["domain"], details["confidence"]), ("sports", "high"))
        self.assertEqual(module.route_domain({"question": "Will Moana score at least 40 on the Rotten Tomatoes Tomatometer?"}), "other")
        self.assertEqual(module.route_domain({"question": "Will Bilibili Gaming win MSI 2026?"}), "sports")
        self.assertEqual(module.route_domain({"question": "Will there be 4+ Pentakills at MSI?"}), "sports")

    def test_thin_book_is_blocked(self):
        result = module.simulate_buy({"bids": [{"price": 0.79, "size": 1}], "asks": [{"price": 0.80, "size": 1}]}, 100, 5)
        self.assertIs(result["fillable"], False)
        self.assertEqual(result["status"], "insufficient_depth")

    def test_sell_book_depth(self):
        result = module.simulate_sell({"bids": [{"price": 0.80, "size": 100}], "asks": [{"price": 0.81, "size": 100}]}, 10, 5)
        self.assertIs(result["fillable"], True)
        self.assertLess(result["fill_price"], 0.80)

    def test_clv_uses_first_eligible_append_only_observation(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        ledger = module.new_ledger(policy)
        opened = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ledger["open_positions"] = [{
            "paper_trade_id": "clv-1", "market_id": "m1", "yes_token_id": "y1",
            "opened_at": opened.isoformat(), "entry_price": 0.70, "shares": 10.0,
            "cost_basis_usd": 7.0, "entry_fee_usd": 0.0, "fee_rate": 0.0,
            "model_version": "approved-v1", "status": "open",
        }]
        estimates = {"m1": {"probability": 0.90, "fair_value_ceiling": 0.95, "model_version": "approved-v1"}}
        for minutes, bid in [(30, 0.72), (60, 0.74), (90, 0.80)]:
            book = {"y1": {"bids": [{"price": bid, "size": 100}], "asks": [{"price": bid + 0.01, "size": 100}]}}
            module.monitor_positions(
                ledger, book, estimates, {}, policy,
                observed_at=(opened + timedelta(minutes=minutes)).isoformat(),
            )
        position = ledger["open_positions"][0]
        self.assertAlmostEqual(position["clv_reference_price"], 0.74)
        self.assertAlmostEqual(position["clv_per_share"], 0.04)
        self.assertEqual(position["clv_horizon_minutes"], 60)
        self.assertEqual(len(ledger["position_observations"]), 3)
        references = [row for row in ledger["position_observations"] if row.get("is_clv_reference")]
        self.assertEqual(len(references), 1)
        self.assertEqual(position["clv_observation_id"], references[0]["observation_id"])

    def test_early_exit_counterfactual_is_resolved_after_final_outcome(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        ledger = module.new_ledger(policy)
        opened = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ledger["open_positions"] = [{
            "paper_trade_id": "exit-1", "market_id": "m1", "yes_token_id": "y1",
            "opened_at": opened.isoformat(), "entry_price": 0.70, "shares": 10.0,
            "cost_basis_usd": 7.0, "entry_fee_usd": 0.0, "fee_rate": 0.0,
            "model_probability_at_entry": 0.85, "model_version": "approved-v1",
            "domain": "sports", "status": "open", "strategy_version": "s1",
        }]
        ledger["cash_usd"] = 493.0
        module.monitor_positions(
            ledger,
            {"y1": {"bids": [{"price": 0.60, "size": 100}], "asks": [{"price": 0.61, "size": 100}]}},
            {"m1": {"probability": 0.50, "model_version": "approved-v1"}},
            {"m1": {"thesis_invalidated": True, "evidence_ids": ["official-event-1"]}},
            policy, observed_at=(opened + timedelta(hours=2)).isoformat(),
        )
        self.assertEqual(ledger["closed_positions"][0]["counterfactual_status"], "pending")
        self.assertEqual(ledger["back_cases"][0]["exit_observation_id"], ledger["position_observations"][0]["observation_id"])
        module.settle(ledger, {"m1": "YES"}, policy)
        self.assertEqual(ledger["closed_positions"][0]["counterfactual_status"], "resolved")
        self.assertEqual(ledger["closed_positions"][0]["counterfactual_resolution"], "YES")
        self.assertEqual(ledger["back_cases"][0]["counterfactual_status"], "resolved")

    def test_early_exit_still_gets_first_eligible_clv_snapshot(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        ledger = module.new_ledger(policy)
        opened = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ledger["closed_positions"] = [{
            "paper_trade_id": "exit-clv", "market_id": "m1", "yes_token_id": "y1",
            "opened_at": opened.isoformat(), "entry_price": 0.70, "shares": 10.0,
            "exit_reason": ["core_thesis_invalidated"], "counterfactual_status": "pending",
            "model_version": "approved-v1",
        }]
        result = module.monitor_pending_counterfactuals(
            ledger,
            {"y1": {"bids": [{"price": 0.76, "size": 100}], "asks": [{"price": 0.77, "size": 100}]}},
            policy, observed_at=(opened + timedelta(minutes=75)).isoformat(),
        )
        position = ledger["closed_positions"][0]
        self.assertEqual(result["tracked"], ["exit-clv"])
        self.assertAlmostEqual(position["clv_per_share"], 0.06)
        self.assertEqual(position["clv_observation_id"], ledger["position_observations"][0]["observation_id"])

    def test_pending_early_exit_back_case_cannot_be_complete(self):
        case = self.complete_back_case("t1")
        case.update({"type": "early_exit_review", "counterfactual_status": "pending"})
        self.assertFalse(module.back_case_is_complete(case))
        case.update({
            "counterfactual_status": "resolved", "counterfactual_resolution": "NO",
            "counterfactual_resolved_at": "2026-01-02T00:00:00+00:00", "exit_vs_hold_value_usd": 3.0,
        })
        self.assertTrue(module.back_case_is_complete(case))

    def test_official_fee_formula(self):
        self.assertEqual(module.taker_fee_usd(100, 0.50, 0.07), 1.75)
        self.assertEqual(module.taker_fee_usd(100, 0.80, 0.07), 1.12)

    def test_enabled_fee_without_schedule_is_blocked(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        end = "2099-01-01T00:00:00Z"
        market = module.normalize_market({"id": "fee-missing", "question": "Will Bitcoin rise?", "endDate": end, "feesEnabled": True, "outcomes": '["Yes","No"]', "clobTokenIds": '["y","n"]'})
        estimate = {"probability": 0.9, "confidence_low": 0.8, "confidence_high": 0.95, "model_version": "v", "calibration_samples": 30, "sources": [{"kind": "official", "url": "o"}, {"kind": "independent", "url": "a"}, {"kind": "independent", "url": "b"}], "rules_review": {"status": "clear", "reviewed_at": "x"}, "failure_paths": [{"controlled": True}]}
        result = module.evaluate_candidate(market, estimate, {"bids": [{"price": 0.7, "size": 1000}], "asks": [{"price": 0.71, "size": 1000}]}, policy, 25)
        self.assertIn("enabled_fee_schedule_missing", result["failed_gates"])

    def test_no_side_can_pass_enter_and_settle(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        end = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        market = module.normalize_market({
            "id": "no-edge", "conditionId": "c-no", "question": "Will the event occur?",
            "endDate": end, "active": True, "closed": False, "acceptingOrders": True,
            "outcomes": '["Yes","No"]', "outcomePrices": '["0.10","0.90"]',
            "clobTokenIds": '["yes-no-edge","no-no-edge"]', "feesEnabled": False,
        })
        estimate = {
            "probability": 0.10, "confidence_low": 0.05, "confidence_high": 0.20,
            "model_version": "approved-v1", "calibration_samples": 100,
            "sources": [{"kind": "official", "url": "official"}, {"kind": "independent", "url": "a"}, {"kind": "independent", "url": "b"}],
            "rules_review": {"status": "clear", "reviewed_at": "now"},
            "failure_paths": [{"controlled": True}], "correlation_group": "event-no",
        }
        no_book = {"bids": [{"price": 0.69, "size": 1000}], "asks": [{"price": 0.70, "size": 1000}]}
        candidate = module.evaluate_candidate(market, estimate, no_book, policy, 25, side="NO")
        self.assertTrue(candidate["gate_passed"])
        self.assertEqual((candidate["side"], candidate["token_id"]), ("NO", "no-no-edge"))
        self.assertAlmostEqual(candidate["model_probability"], 0.90)
        full_scan = module.scan_payload(
            [{"id": "no-edge", "conditionId": "c-no", "question": "Will the event occur?", "endDate": end,
              "active": True, "closed": False, "acceptingOrders": True, "outcomes": '["Yes","No"]',
              "outcomePrices": '["0.10","0.90"]', "clobTokenIds": '["yes-no-edge","no-no-edge"]', "feesEnabled": False}],
            {"no-no-edge": no_book}, {"no-edge": estimate}, policy,
        )
        self.assertEqual(full_scan["schema_version"], "polymarket-scan-v2")
        self.assertEqual(full_scan["markets_scanned"], 1)
        self.assertEqual(full_scan["side_decisions_evaluated"], 2)
        self.assertEqual(full_scan["selected_candidates"][0]["side"], "NO")
        scan = {"created_at": datetime.now(timezone.utc).isoformat(), "selected_candidates": [candidate]}
        ledger = module.new_ledger(policy)
        opened = module.enter_scan(scan, ledger, policy)
        self.assertEqual(len(opened["opened"]), 1)
        self.assertEqual(ledger["open_positions"][0]["side"], "NO")
        module.settle(ledger, {"no-edge": "NO"}, policy)
        self.assertEqual(ledger["closed_positions"][0]["outcome"], "win")
        self.assertTrue(ledger["closed_positions"][0]["side_won"])

    def test_high_win_small_return_uses_positive_ev_and_two_pct_cap(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        end = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        raw = {"id": "small-edge", "conditionId": "c-small", "question": "Will the favorite win?", "endDate": end,
               "active": True, "closed": False, "acceptingOrders": True, "outcomes": '["Yes","No"]',
               "outcomePrices": '["0.93","0.07"]', "clobTokenIds": '["yes-small","no-small"]', "feesEnabled": False}
        estimate = {"probability": .95, "confidence_low": .90, "confidence_high": .98,
            "model_version": "approved-small-v1", "calibration_samples": 60, "historical_hit_rate": .94,
            "model_brier_score": .04, "market_brier_score": .05, "model_log_loss": .20, "market_log_loss": .21,
            "sources": [{"kind": "official", "url": "official"}, {"kind": "independent", "url": "a"}, {"kind": "independent", "url": "b"}],
            "rules_review": {"status": "clear", "reviewed_at": "now"}, "failure_paths": [{"controlled": True}],
            "correlation_group": "event-small"}
        book = {"bids": [{"price": .92, "size": 1000}], "asks": [{"price": .93, "size": 1000}]}
        candidate = module.evaluate_candidate(module.normalize_market(raw), estimate, book, policy, 10, side="YES")
        self.assertFalse(candidate["gate_passed"])
        self.assertTrue(module.evaluate_high_win_small_return(candidate, policy)["eligible"])
        scan = module.scan_payload([raw], {"yes-small": book}, {"small-edge": estimate}, policy, 500)
        selected = scan["selected_candidates"][0]
        self.assertEqual(selected["recommendation_type"], "high_win_small_return")
        self.assertEqual(selected["planned_notional_usd"], 10.0)
        self.assertTrue(selected["paper_entry_eligible"])

    def test_no_side_monitor_uses_no_token_and_transformed_probability(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        ledger = module.new_ledger(policy)
        opened = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ledger["open_positions"] = [{
            "paper_trade_id": "no-monitor", "market_id": "m-no", "yes_token_id": "yes",
            "no_token_id": "no", "token_id": "no", "side": "NO", "opened_at": opened.isoformat(),
            "entry_price": 0.70, "shares": 10.0, "cost_basis_usd": 7.0, "entry_fee_usd": 0.0,
            "fee_rate": 0.0, "model_probability_at_entry": 0.9, "model_yes_probability_at_entry": 0.1,
            "model_version": "approved-v1", "domain": "politics", "status": "open", "strategy_version": "s1",
        }]
        ledger["cash_usd"] = 493.0
        result = module.monitor_positions(
            ledger, {"no": {"bids": [{"price": 0.69, "size": 100}], "asks": [{"price": 0.70, "size": 100}]}},
            {"m-no": {"probability": 0.90, "model_version": "approved-v1"}}, {}, policy,
            observed_at=(opened + timedelta(hours=2)).isoformat(),
        )
        self.assertEqual(result["exited"], ["no-monitor"])
        self.assertAlmostEqual(ledger["closed_positions"][0]["updated_probability_before_exit"], 0.10)
        self.assertEqual(ledger["position_observations"][0]["token_id"], "no")

    def test_audit_does_not_count_unresolved_early_exit_as_no_or_trust_external_count(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        ledger = module.new_ledger(policy)
        now = datetime.now(timezone.utc)
        trade = self.closed_trade("t1", (now - timedelta(hours=2)).isoformat(), now.isoformat(), pnl=-1.0)
        trade.pop("resolution")
        trade["exit_reason"] = ["core_thesis_invalidated"]
        trade.pop("clv_per_share")
        trade.pop("clv_observed_at")
        trade.pop("clv_horizon_minutes")
        ledger["closed_positions"] = [trade]
        audit = module.audit_ledger(ledger, {}, {"forward_paper_trades": 1000})
        self.assertEqual(audit["forward_paper_trades"], 1)
        self.assertEqual(audit["resolved_forecast_samples"], 0)
        self.assertIsNone(audit["brier_score"])
        self.assertFalse(audit["acceptance_checks"]["forecast_scoring_coverage_complete"])
        self.assertFalse(audit["acceptance_checks"]["all_losses_and_early_exits_have_complete_back_case"])
        self.assertFalse(audit["acceptance_checks"]["zero_avoidable_rule_misread_losses"])

    def test_back_case_is_joined_by_trade_and_must_be_complete(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        ledger = module.new_ledger(policy)
        now = datetime.now(timezone.utc)
        ledger["closed_positions"] = [
            self.closed_trade("loss-1", (now - timedelta(days=2)).isoformat(), (now - timedelta(days=1)).isoformat(), -5.0, "NO"),
            self.closed_trade("loss-2", (now - timedelta(days=1)).isoformat(), now.isoformat(), -5.0, "NO"),
        ]
        ledger["back_cases"] = [self.complete_back_case("loss-1"), {"paper_trade_id": "unrelated"}]
        audit = module.audit_ledger(ledger)
        self.assertEqual(audit["review_required_trade_count"], 2)
        self.assertEqual(audit["complete_back_case_trade_count"], 1)
        self.assertFalse(audit["acceptance_checks"]["all_losses_and_early_exits_have_complete_back_case"])

    def test_three_exact_same_capital_windows_compare_without_legacy_baseline(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        ledger = module.new_ledger(policy)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ledger["created_at"] = (start - timedelta(days=1)).isoformat()
        ledger["updated_at"] = (start + timedelta(days=91)).isoformat()
        ledger["events"] = self.observation_events(start, start + timedelta(days=90))
        windows = []
        for index in range(3):
            window_start = start + timedelta(days=30 * index)
            window_end = window_start + timedelta(days=30)
            ledger["closed_positions"].append(self.closed_trade(
                f"w{index}", (window_start + timedelta(days=1)).isoformat(),
                (window_start + timedelta(days=2)).isoformat(), pnl=30.0,
            ))
            windows.append({
                "window_id": f"w{index}", "start_at": window_start.isoformat(), "end_at": window_end.isoformat(),
                "initial_equity_usd": 500.0, "net_roi_pct": 0.0,
                "data_status": "complete", "friction_complete": True,
            })
        comparison = module.compare_30d_windows(ledger, {"windows": windows})
        self.assertEqual(comparison["valid_window_count"], 3)
        self.assertEqual(comparison["best_consecutive_windows_beating_binance"], 3)
        self.assertAlmostEqual(comparison["aggregate_roi_advantage_pct_points"], 6.0)
        audit = module.audit_ledger(ledger, {"net_roi_pct": -99, "comparison_status": "legacy_starting_baseline_not_same_window", "windows": windows})
        self.assertTrue(audit["acceptance_checks"]["three_consecutive_30d_windows_beat_binance"])
        self.assertTrue(audit["acceptance_checks"]["polymarket_beats_binance_by_5pct"])

    def test_window_with_boundary_position_is_invalid(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        ledger = module.new_ledger(policy)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ledger["created_at"] = (start - timedelta(days=1)).isoformat()
        ledger["updated_at"] = (start + timedelta(days=31)).isoformat()
        ledger["open_positions"] = [{"paper_trade_id": "carry", "opened_at": (start + timedelta(days=2)).isoformat()}]
        comparison = module.compare_30d_windows(ledger, {"windows": [{
            "window_id": "w", "start_at": start.isoformat(), "end_at": (start + timedelta(days=30)).isoformat(),
            "initial_equity_usd": 500.0, "net_roi_pct": 1.0, "data_status": "complete", "friction_complete": True,
        }]})
        self.assertFalse(comparison["windows"][0]["valid_same_window"])
        self.assertIn("unmarked_boundary_position", comparison["windows"][0]["failed_gates"])

    def test_observed_cash_window_is_valid_but_unobserved_window_is_not(self):
        policy = module.read_json(Path(__file__).resolve().parents[1] / "config" / "policy.json")
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        window = {"window_id": "cash", "start_at": start.isoformat(), "end_at": (start + timedelta(days=30)).isoformat(), "initial_equity_usd": 500.0, "net_roi_pct": -1.0, "data_status": "complete", "friction_complete": True}
        ledger = module.new_ledger(policy)
        ledger["created_at"] = (start - timedelta(minutes=1)).isoformat()
        ledger["updated_at"] = (start + timedelta(days=30, minutes=1)).isoformat()
        ledger["events"] = self.observation_events(start, start + timedelta(days=30))
        observed = module.compare_30d_windows(ledger, {"windows": [window]})
        self.assertTrue(observed["windows"][0]["valid_same_window"])
        self.assertTrue(observed["windows"][0]["beats_binance"])
        ledger["updated_at"] = (start + timedelta(days=29)).isoformat()
        unobserved = module.compare_30d_windows(ledger, {"windows": [window]})
        self.assertFalse(unobserved["windows"][0]["valid_same_window"])
        self.assertIn("polymarket_observation_window_incomplete", unobserved["windows"][0]["failed_gates"])


if __name__ == "__main__":
    unittest.main()
