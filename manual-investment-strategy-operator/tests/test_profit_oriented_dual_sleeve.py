import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "profit_oriented_dual_sleeve.py"
SPEC = importlib.util.spec_from_file_location("profit_loop", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def candidate(symbol="AAA", ev=8, drawdown=3, sample=35, calibrated=True, signal=True):
    return {
        "symbol": symbol, "request_mode": "tactical_1_7d", "current_price": 90, "price_as_of": "2030-01-01T00:00:00Z",
        "fair_value_base": 120, "fair_value_range": [110, 130], "conservative_ev_pct": ev,
        "expected_drawdown_pct": drawdown, "expected_upside_pct": 20, "catalyst_certainty": 0.8,
        "catalyst_verified": True, "catalyst_summary": "verified demo event", "liquidity_status": "verified",
        "data_quality_status": "verified", "sample_size": sample,
        "probability_tier": "calibrated" if calibrated else "wide_interval", "current_signal_complete": signal,
        "holdout_pass": calibrated, "evidence_ids": ["ev-1"], "invalidation_conditions": ["event cancelled"],
        "entry_plan": {"entry_low": 89, "entry_high": 91, "entry_trigger": "close above trigger", "valid_until": "2030-01-02T00:00:00Z", "target_1": 100, "target_2": 108, "stop_price": 86, "latest_exit_at": "2030-01-08T00:00:00Z"},
    }


def receipt(receipt_id, trade_id, event, qty, price, fee, when, symbol="AAA"):
    return {"schema_version": "MinimalExecutionReceiptV1", "receipt_id": receipt_id, "trade_id": trade_id,
            "recommendation_id": "rec-1", "symbol": symbol, "rail": "crypto", "event": event,
            "quantity": qty, "price": price, "fee_usd": fee, "slippage_usd": 0, "executed_at": when, "human_confirmed": True}


def paper_receipt(baseline, receipt_id, trade_id, event, qty, price, when, symbol="AAA", snapshot_id="paper-snapshot-1"):
    return {
        "schema_version": "PaperExecutionReceiptV1",
        "receipt_id": receipt_id,
        "baseline_id": baseline["baseline_id"],
        "trade_id": trade_id,
        "symbol": symbol,
        "request_mode": "tactical_1_7d",
        "evidence_mode": "paper",
        "strategy_version": baseline["strategy_version"],
        "snapshot_id": snapshot_id,
        "event": event,
        "quantity": qty,
        "price": price,
        "fee_usd": 0.5,
        "slippage_usd": 0.25,
        "executed_at": when,
        "live_orders_enabled": False,
    }


def current_plan(snapshot_id="snap", symbol="AAA"):
    return {
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "generated_at": "2030-01-01T00:01:50Z",
        "entry_low": 99,
        "entry_high": 101,
        "entry_trigger": "current close confirms above 99",
        "valid_until": "2030-01-01T00:05:00Z",
        "target_1": 110,
        "target_2": 115,
        "stop_price": 96,
        "latest_exit_at": "2030-01-08T00:00:00Z",
    }


class ProfitLoopTests(unittest.TestCase):
    def test_current_market_gate_requires_fresh_consistent_dual_source_prices(self):
        gate = M.evaluate_market_evidence(
            snapshot_id="snap",
            quotes=[
                {"source": "public-a", "public": True, "price": 100, "as_of": "2030-01-01T00:01:30Z", "latency_seconds": 1},
                {"source": "public-b", "public": True, "price": 100.5, "as_of": "2030-01-01T00:01:40Z", "latency_seconds": 2},
            ],
            as_of="2030-01-01T00:02:00Z",
            scan_started_at="2030-01-01T00:01:00Z",
            scan_completed_at="2030-01-01T00:01:50Z",
            plan_valid_until="2030-01-01T00:05:00Z",
            current_plan=current_plan(),
        )
        self.assertEqual(gate["status"], "FRESH")
        ranked = M.rank_candidates("snap", "v1", [candidate()], "2030-01-01T00:02:00Z")
        plan = M.build_current_trade_plan(ranked, gate)
        self.assertEqual(plan["action"], "ENTER_NOW")
        self.assertEqual(plan["current_price"], 100.25)
        self.assertEqual(plan["price_as_of"], "2030-01-01T00:01:50Z")
        self.assertEqual(plan["market_evidence_digest"], gate["evidence_digest"])

    def test_fresh_gate_cannot_reuse_stale_candidate_price(self):
        stale_candidate = candidate()
        stale_candidate["current_price"] = 90
        stale_candidate["price_as_of"] = "2029-12-31T00:00:00Z"
        ranked = M.rank_candidates("snap", "v1", [stale_candidate], "2030-01-01T00:02:00Z")
        gate = M.evaluate_market_evidence(
            snapshot_id="snap",
            symbol="AAA",
            quotes=[
                {"source": "public-a", "public": True, "price": 100, "as_of": "2030-01-01T00:01:30Z", "latency_seconds": 1},
                {"source": "public-b", "public": True, "price": 100.5, "as_of": "2030-01-01T00:01:40Z", "latency_seconds": 2},
            ],
            as_of="2030-01-01T00:02:00Z",
            scan_started_at="2030-01-01T00:01:00Z",
            scan_completed_at="2030-01-01T00:01:50Z",
            current_plan=current_plan(),
        )
        plan = M.build_current_trade_plan(ranked, gate)
        self.assertEqual(plan["current_price"], 100.25)
        self.assertNotEqual(plan["price_as_of"], stale_candidate["price_as_of"])
        self.assertEqual(plan["entry_low"], 99)
        self.assertEqual(plan["entry_high"], 101)
        self.assertEqual(plan["target_1"], 110)
        self.assertEqual(plan["stop_price"], 96)

    def test_market_gate_rejects_missing_or_internally_stale_current_plan(self):
        base = dict(
            snapshot_id="snap",
            symbol="AAA",
            quotes=[
                {"source": "public-a", "public": True, "price": 100, "as_of": "2030-01-01T00:01:30Z", "latency_seconds": 1},
                {"source": "public-b", "public": True, "price": 100.5, "as_of": "2030-01-01T00:01:40Z", "latency_seconds": 2},
            ],
            as_of="2030-01-01T00:02:00Z",
            scan_started_at="2030-01-01T00:01:00Z",
            scan_completed_at="2030-01-01T00:01:50Z",
        )
        missing = M.evaluate_market_evidence(**base)
        self.assertEqual(missing["status"], "NO_FRESH_DECISION")
        self.assertIn("current_plan_rebuild_required", missing["reasons"])
        stale_levels = current_plan()
        stale_levels["entry_low"] = 89
        stale_levels["entry_high"] = 91
        stale_levels["target_1"] = 100
        stale_levels["stop_price"] = 86
        invalid = M.evaluate_market_evidence(**base, current_plan=stale_levels)
        self.assertEqual(invalid["status"], "NO_FRESH_DECISION")
        self.assertIn("target_1_not_above_current_price", invalid["reasons"])

    def test_stale_conflicting_or_expired_market_never_emits_current_trade_plan(self):
        ranked = M.rank_candidates("snap", "v1", [candidate()], "2030-01-01T00:03:00Z")
        gate = M.evaluate_market_evidence(
            snapshot_id="snap",
            quotes=[
                {"source": "public-a", "public": True, "price": 100, "as_of": "2030-01-01T00:00:00Z", "latency_seconds": 13},
                {"source": "public-b", "public": True, "price": 103, "as_of": "2030-01-01T00:00:10Z", "latency_seconds": 2},
            ],
            as_of="2030-01-01T00:03:00Z",
            scan_started_at="2030-01-01T00:00:00Z",
            scan_completed_at="2030-01-01T00:02:30Z",
            plan_valid_until="2030-01-01T00:02:00Z",
            current_plan=current_plan(),
        )
        self.assertEqual(gate["status"], "NO_FRESH_DECISION")
        self.assertTrue({"scan_duration_exceeded", "cross_source_price_conflict", "trade_plan_expired"}.issubset(gate["reasons"]))
        decision = M.build_current_trade_plan(ranked, gate)
        self.assertEqual(decision["status"], "NO_FRESH_DECISION")
        self.assertIsNone(decision["trade_plan"])

    def test_new_paper_baseline_is_isolated_and_starts_collecting_samples(self):
        baseline = M.build_paper_tactical_baseline(month_id="2030-01", strategy_version="v2", opened_at="2030-01-01T00:00:00Z")
        self.assertEqual(baseline["initial_capital_usd"], 500)
        self.assertFalse(baseline["legacy_unknown_horizon_imported"])
        result = M.calculate_paper_profit_attribution(
            baseline, [], period_start="2030-01-01T00:00:00Z", period_end="2030-01-31T23:59:59Z", as_of="2030-01-02T00:00:00Z"
        )
        self.assertEqual(result["status"], "COLLECTING_SAMPLES")
        self.assertEqual(result["money_weighted_net_roi_pct"], 0)
        self.assertFalse(result["business_ready_eligible"])

    def test_paper_attribution_rejects_legacy_receipts_and_uses_500_denominator(self):
        baseline = M.build_paper_tactical_baseline(month_id="2030-01", strategy_version="v2", opened_at="2030-01-01T00:00:00Z")
        receipts = [
            paper_receipt(baseline, "p1", "trade-1", "entry", 1, 100, "2030-01-02T00:00:00Z"),
            paper_receipt(baseline, "p2", "trade-1", "exit", 1, 110, "2030-01-03T00:00:00Z"),
        ]
        result = M.calculate_paper_profit_attribution(
            baseline, receipts, period_start="2030-01-01T00:00:00Z", period_end="2030-01-31T23:59:59Z", as_of="2030-02-01T00:00:00Z"
        )
        self.assertEqual(result["net_profit_usd"], 8.5)
        self.assertEqual(result["money_weighted_net_roi_pct"], 1.7)
        with self.assertRaisesRegex(M.ContractError, "legacy or live"):
            M.calculate_paper_profit_attribution(
                baseline,
                [receipt("legacy", "old", "entry", 1, 100, 0, "2030-01-02T00:00:00Z")],
                period_start="2030-01-01T00:00:00Z",
                period_end="2030-01-31T23:59:59Z",
            )

    def test_paper_attribution_rejects_receipts_after_as_of(self):
        baseline = M.build_paper_tactical_baseline(month_id="2030-01", strategy_version="v2", opened_at="2030-01-01T00:00:00Z")
        future = [
            paper_receipt(baseline, "p1", "future-trade", "entry", 1, 100, "2030-01-20T00:00:00Z"),
            paper_receipt(baseline, "p2", "future-trade", "exit", 1, 110, "2030-01-21T00:00:00Z"),
        ]
        with self.assertRaisesRegex(M.ContractError, "after attribution as_of"):
            M.calculate_paper_profit_attribution(
                baseline,
                future,
                period_start="2030-01-01T00:00:00Z",
                period_end="2030-01-31T23:59:59Z",
                as_of="2030-01-15T00:00:00Z",
            )

    def test_zero_live_cash_is_paper_ready_not_globally_blocked(self):
        baseline = M.build_paper_tactical_baseline(month_id="2030-01", strategy_version="v2", opened_at="2030-01-01T00:00:00Z")
        paper = M.calculate_paper_profit_attribution(
            baseline, [], period_start="2030-01-01T00:00:00Z", period_end="2030-01-31T23:59:59Z", as_of="2030-01-02T00:00:00Z"
        )
        status = M.build_business_status_v2(
            delivery_status="RUNTIME_VERIFIED",
            paper_attribution=paper,
            live_attribution=None,
            longterm_authority="legacy_estimate",
            market_evidence_status="FRESH",
            paper_mode_enabled=True,
            live_cash_available=False,
            ninety_day_live_evidence=False,
            as_of="2030-01-02T00:00:00Z",
        )
        self.assertEqual(status["delivery_status"], "RUNTIME_VERIFIED")
        self.assertEqual(status["execution_readiness"], "PAPER_ONLY_READY")
        self.assertEqual(status["short_profit_status"], {"paper": "COLLECTING_SAMPLES", "live": "NOT_STARTED"})
        self.assertEqual(status["long_path_status"]["status"], "DATA_DEGRADED")
        self.assertEqual(status["business_outcome_status"], "EVIDENCE_PENDING")
        self.assertNotEqual(status["unique_blocker"], "BLOCKED")
        self.assertIn("Paper", status["unique_next_step"])
        self.assertEqual(M.project_business_status_v1(status)["delivery_status"], "RUNTIME_VERIFIED")

    def test_missing_price_evidence_cannot_emit_trade_plan(self):
        item = candidate()
        item.pop("price_as_of")
        item["evidence_ids"] = []
        with self.assertRaises(M.ContractError):
            M.rank_candidates("s0", "v1", [item], "2030-01-01T00:00:00Z")

    def test_unique_top1_is_deterministic(self):
        ranked = M.rank_candidates("snap", "v1", [candidate("LOW", 4), candidate("TOP", 9)], "2030-01-01T00:00:00Z")
        self.assertEqual(ranked["unique_top1"], "TOP")
        self.assertEqual(len([ranked["unique_top1"]]), 1)

    def test_enter_now_and_wait_are_explicit(self):
        enter = M.build_trade_plan(M.rank_candidates("s1", "v1", [candidate()], "2030-01-01T00:00:00Z"))
        wait = M.build_trade_plan(M.rank_candidates("s2", "v1", [candidate(sample=8, calibrated=False)], "2030-01-01T00:00:00Z"))
        self.assertEqual(enter["action"], "ENTER_NOW")
        self.assertEqual(wait["action"], "WAIT_FOR_ENTRY")
        for key in ["entry_low", "entry_high", "valid_until", "stop_price", "latest_exit_at"]:
            self.assertIn(key, wait)

    def test_money_weighted_roi_not_sum_of_trade_roi(self):
        receipts = [
            receipt("r1", "t1", "entry", 1, 100, 1, "2030-01-02T00:00:00Z"),
            receipt("r2", "t1", "exit", 1, 110, 1, "2030-01-03T00:00:00Z"),
            receipt("r3", "t2", "entry", 10, 10, 1, "2030-01-04T00:00:00Z", "BBB"),
            receipt("r4", "t2", "exit", 10, 9, 1, "2030-01-05T00:00:00Z", "BBB"),
        ]
        result = M.calculate_profit_attribution(receipts, "2030-01-01T00:00:00Z", "2030-01-31T23:59:59Z", 200, as_of="2030-02-01T00:00:00Z")
        self.assertEqual(result["net_profit_usd"], -4)
        self.assertEqual(result["monthly_money_weighted_net_roi_pct"], -2)

    def test_missing_opening_capital_blocks_monthly_claim(self):
        result = M.calculate_profit_attribution([], "2030-01-01T00:00:00Z", "2030-01-31T23:59:59Z", None, as_of="2030-02-01T00:00:00Z")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["monthly_money_weighted_net_roi_pct"])

    def test_cross_period_entry_cost_is_not_dropped(self):
        receipts = [
            receipt("prior-entry", "cross", "entry", 1, 100, 0, "2029-12-20T00:00:00Z"),
            receipt("current-exit", "cross", "exit", 1, 110, 0, "2030-01-05T00:00:00Z"),
        ]
        result = M.calculate_profit_attribution(receipts, "2030-01-01T00:00:00Z", "2030-01-31T23:59:59Z", 100, as_of="2030-02-01T00:00:00Z")
        self.assertEqual(result["realized_profit_usd"], 10)
        self.assertEqual(result["monthly_money_weighted_net_roi_pct"], 10)

    def test_open_position_without_ending_mark_blocks(self):
        receipts = [receipt("open", "open-trade", "entry", 1, 100, 0, "2030-01-05T00:00:00Z")]
        result = M.calculate_profit_attribution(receipts, "2030-01-01T00:00:00Z", "2030-01-31T23:59:59Z", 100, marks={}, as_of="2030-02-01T00:00:00Z")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("期末价格", result["blocker"])

    def test_append_only_receipt_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "receipts.jsonl"
            item = receipt("same", "t1", "entry", 1, 100, 0, "2030-01-02T00:00:00Z")
            M.append_receipt(path, item)
            with self.assertRaises(M.ContractError):
                M.append_receipt(path, item)

    def test_short_loss_cannot_auto_convert_to_long(self):
        base = {"reunderwrite_id": "rw1", "short_trade_id": "t1", "reviewed_at": "2030-01-02T00:00:00Z",
                "independent_from_short_thesis": True, "longterm_thesis_pass": True, "valuation_pass": True,
                "liquidity_pass": True, "concentration_pass": True, "user_confirmed": False, "evidence_ids": ["e1"]}
        result = M.reunderwrite_short_to_long(base)
        self.assertEqual(result["decision"], "KEEP_ORIGINAL_SHORT_EXIT")

    def test_only_one_profit_rule_change_per_14_days(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reviews.jsonl"
            base = {"schema_version": "ProfitReviewV1", "review_id": "p1", "reviewed_at": "2030-01-01T00:00:00Z", "sleeve": "short_tactical", "change_type": "profit_rule",
                    "metric": "net_roi", "baseline": 0, "single_rule_change": "change one", "expected_profit_effect": "positive",
                    "required_sample": 30, "failure_condition": "lower EV", "rollback_version": "v0", "approved": True}
            M.append_profit_review(path, base)
            second = dict(base, review_id="p2", reviewed_at="2030-01-10T00:00:00Z", sleeve="long_compound")
            with self.assertRaises(M.ContractError):
                M.append_profit_review(path, second)

    def test_unknown_change_type_cannot_bypass_biweekly_gate(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reviews.jsonl"
            item = {"schema_version": "ProfitReviewV1", "review_id": "p1", "reviewed_at": "2030-01-01T00:00:00Z", "sleeve": "short_tactical", "change_type": "profit_rules",
                    "metric": "net_roi", "baseline": 0, "single_rule_change": "change one", "expected_profit_effect": "positive",
                    "required_sample": 30, "failure_condition": "lower EV", "rollback_version": "v0", "approved": True}
            with self.assertRaises(M.ContractError):
                M.append_profit_review(path, item)

    def test_new_principal_not_counted_as_long_profit_and_status_has_one_next_step(self):
        previous = {"schema_version": "LongTermHoldingSnapshotV1", "snapshot_id": "l1", "as_of": "2030-01-01T00:00:00Z", "source": "user", "human_confirmed": True, "holdings": [], "cash_rails": {"usd": 100}, "baseline_value_usd": 100}
        current = dict(previous, snapshot_id="l2", as_of="2030-02-01T00:00:00Z", baseline_value_usd=200)
        path = M.calculate_long_term_path(previous, current, net_new_principal_usd=100)
        self.assertEqual(path["economic_gain_usd"], 0)
        self.assertEqual(path["principal_adjusted_return_pct"], 0)
        status = M.build_business_status("RUNTIME_VERIFIED", None, None, "2030-01-01T00:00:00Z")
        self.assertEqual(status["short_profit_status"], "BLOCKED")
        self.assertEqual(status["long_path_status"], "BLOCKED")
        self.assertIsInstance(status["unique_next_step"], str)

    def test_no_update_status(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "status.json"
            status = M.build_business_status("TESTED", None, None, "2030-01-01T00:00:00Z")
            self.assertEqual(M.persist_status_if_changed(path, status), "UPDATED")
            status["as_of"] = "2030-01-02T00:00:00Z"
            self.assertEqual(M.persist_status_if_changed(path, status), "NO_UPDATE")

    def test_unique_next_step_matches_delivery_blocker_after_inputs_exist(self):
        attribution = M.calculate_profit_attribution([], "2030-01-01T00:00:00Z", "2030-01-31T23:59:59Z", 100, as_of="2030-01-15T00:00:00Z")
        snapshot = {"schema_version": "LongTermHoldingSnapshotV1", "snapshot_id": "l1", "as_of": "2030-01-01T00:00:00Z", "source": "user", "human_confirmed": True, "holdings": [], "cash_rails": {"usd": 100}, "baseline_value_usd": 100}
        status = M.build_business_status("RUNTIME_VERIFIED", attribution, snapshot, "2030-01-15T00:00:00Z")
        self.assertIn("RUNTIME_VERIFIED", status["unique_blocker"])
        self.assertIn("90 天", status["unique_next_step"])


if __name__ == "__main__":
    unittest.main()
