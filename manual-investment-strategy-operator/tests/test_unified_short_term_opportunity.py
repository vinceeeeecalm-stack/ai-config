import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "unified_short_term_opportunity.py"
SPEC = importlib.util.spec_from_file_location("unified_short_term_opportunity", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def factor(symbol="ADAUSDT", rank=4, captured_at="2026-07-01T00:00:00Z"):
    return {
        "schema_version": "ShortTermFactorSnapshotV1",
        "factor_snapshot_id": f"factor-{symbol}-{captured_at}",
        "candidate_id": symbol,
        "symbol": symbol,
        "snapshot_id": "snapshot-round-1",
        "strategy_version": "stage0-shadow-v1",
        "config_digest": "a" * 64,
        "source_digest": "b" * 64,
        "captured_at": captured_at,
        "current_price": 100.0,
        "rank": rank,
        "spot": {"relative_strength_pct": 1.5, "relative_volume": 1.8, "taker_buy_ratio": 0.6, "cvd_delta": 2.0, "orderbook_imbalance": 0.2},
        "structure": {"higher_lows": True, "breakout_attempt": True},
        "derivatives": {"oi_change_1h_pct": 2.0, "oi_change_4h_pct": 4.0, "oi_change_24h_pct": 6.0, "funding_zscore": 0.5, "funding_rate_pct": 0.01, "perp_taker_buy_ratio": 0.6, "spot_perp_volume_confirmation": True},
        "catalyst": {"status": "verified"},
        "liquidity": {"spread_bps": 3.0, "depth_1pct_bid_usd": 100000.0, "depth_1pct_ask_usd": 100000.0},
        "data_quality": {"status": "verified"},
        "formal_action_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }


def opportunity(symbol, hold, net, entry="ENTER_NOW"):
    factor_snapshot = factor(symbol=symbol, rank=1)
    factor_snapshot["factor_snapshot_id"] = f"factor-{symbol}"
    return {
        "schema_version": "UnifiedShortTermOpportunityV1",
        "opportunity_id": f"opp-{symbol}",
        "factor_snapshot_id": f"factor-{symbol}",
        "symbol": symbol,
        "snapshot_id": "snapshot-round-1",
        "strategy_version": "stage0-shadow-v1",
        "config_digest": "a" * 64,
        "source_digest": "b" * 64,
        "estimated_holding_minutes": hold,
        "conservative_net_profit_pct": net,
        "expected_max_drawdown_pct": 2.0,
        "target_before_stop_lower_pct": 60.0,
        "reward_risk_ratio": 2.5,
        "catalyst_time_certainty": 0.8,
        "liquidity_exit_score": 0.9,
        "information_decay_penalty_pct": 0.0,
        "overnight_risk_penalty_pct": 0.0,
        "monitoring_burden_penalty_pct": 0.0,
        "hard_gates": {key: True for key in ("value", "catalyst", "realtime", "regression", "liquidity", "risk")},
        "factor_snapshot": factor_snapshot,
        "entry_state": entry,
        "formal_action_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }


class UnifiedShortTermTests(unittest.TestCase):
    def test_oi_alone_and_extreme_funding_cannot_confirm(self):
        item = factor()
        item["spot"] = {"relative_strength_pct": -1.0, "relative_volume": 0.8, "taker_buy_ratio": 0.45, "cvd_delta": -1.0, "orderbook_imbalance": -0.1}
        result = MODULE.directional_confirmation(item)
        self.assertFalse(result["confirmed"])
        self.assertFalse(result["oi_alone_can_authorize"])
        hot = factor()
        hot["derivatives"]["funding_zscore"] = 4.0
        self.assertFalse(MODULE.directional_confirmation(hot)["confirmed"])
        bypass = opportunity("OI_ONLY", 60, 3.0)
        bypass["factor_snapshot"] = item
        bypass["factor_snapshot_id"] = item["factor_snapshot_id"]
        bypass["symbol"] = item["symbol"]
        result = MODULE.rank_opportunities([bypass])
        self.assertEqual(result["shadow_gate_state"], "REJECTED")

    def test_binding_mismatch_blocks_a_ranking_round(self):
        records = [opportunity("FAST", 60, 2.0), opportunity("SLOW", 2880, 3.0)]
        records[1]["snapshot_id"] = "other-snapshot"
        with self.assertRaisesRegex(MODULE.ShortTermContractError, "binding_mismatch"):
            MODULE.rank_opportunities(records)

    def test_unique_top1_and_stage0_never_changes_formal_action(self):
        result = MODULE.rank_opportunities([opportunity("FAST", 60, 2.0), opportunity("SLOW", 2880, 2.0)])
        self.assertEqual(result["research_top1"], "FAST")
        self.assertEqual(result["current_action"], "NO_TRADE")
        self.assertEqual(result["shadow_gate_state"], "ELIGIBLE_NOW")
        self.assertFalse(result["production_rule_changed"])

    def test_materially_better_multiday_candidate_can_win(self):
        result = MODULE.rank_opportunities([opportunity("FAST", 60, 1.0), opportunity("BETTER", 2880, 5.0)])
        self.assertEqual(result["research_top1"], "BETTER")

    def test_near_miss_memory_is_append_only_and_detects_acceleration(self):
        first = MODULE.build_near_miss_state(factor(rank=10), None, expires_at="2026-07-03T00:00:00Z")
        second_factor = factor(rank=5, captured_at="2026-07-01T01:00:00Z")
        second_factor["spot"]["relative_volume"] = 2.4
        second_factor["spot"]["relative_strength_pct"] = 2.5
        second_factor["derivatives"]["oi_change_1h_pct"] = 4.0
        second = MODULE.build_near_miss_state(second_factor, first, expires_at="2026-07-03T01:00:00Z")
        self.assertEqual(second["internal_state"], "EARLY_WARNING")
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp) / "near-miss.jsonl"
            self.assertEqual(MODULE.append_record(ledger, second, "near_miss_state_id"), "APPENDED")
            self.assertEqual(MODULE.append_record(ledger, second, "near_miss_state_id"), "NO_UPDATE")
            changed = copy.deepcopy(second)
            changed["current_rank"] = 4
            with self.assertRaisesRegex(MODULE.ShortTermContractError, "append_only_collision"):
                MODULE.append_record(ledger, changed, "near_miss_state_id")

    def test_future_leakage_and_more_than_seven_days_are_blocked(self):
        cutoff = MODULE.parse_time("2026-07-01T00:00:00Z", "cutoff")
        with self.assertRaisesRegex(MODULE.ShortTermContractError, "future_leakage"):
            MODULE.first_path_outcome(100.0, [{"as_of": "2026-07-01T00:00:00Z", "high": 101, "low": 99, "close": 100}], target_pct=5, stop_pct=3, cutoff=cutoff)
        with self.assertRaisesRegex(MODULE.ShortTermContractError, "exceeds_seven_days"):
            MODULE.first_path_outcome(100.0, [{"as_of": "2026-07-08T00:00:01Z", "high": 101, "low": 99, "close": 100}], target_pct=5, stop_pct=3, cutoff=cutoff)

    def test_profit_pool_rejects_paper_and_strategy_mixing(self):
        payload = {"schema_version": "ShortTermProfitAttributionInputV1", "snapshot_id": "profit-period-1", "strategy_version": "v1", "config_digest": "a" * 64, "source_digest": "b" * 64, "opening_capital_usd": 1000.0, "trades": [{"evidence_mode": "live", "request_mode": "unified_short_term", "holding_minutes": 60, "strategy_version": "v1", "config_digest": "a" * 64, "source_digest": "b" * 64, "net_pnl_usd": 50.0}]}
        result = MODULE.profit_attribution(payload)
        self.assertEqual(result["money_weighted_net_roi_pct"], 5.0)
        self.assertFalse(result["business_ready_eligible"])
        empty = copy.deepcopy(payload)
        empty["trades"] = []
        empty_result = MODULE.profit_attribution(empty)
        self.assertEqual(empty_result["strategy_version"], "v1")
        self.assertEqual(empty_result["snapshot_id"], "profit-period-1")
        self.assertFalse(empty_result["live_profit_measured"])
        paper = copy.deepcopy(payload)
        paper["trades"][0]["evidence_mode"] = "paper"
        with self.assertRaisesRegex(MODULE.ShortTermContractError, "non_live_trade"):
            MODULE.profit_attribution(paper)
        mixed = copy.deepcopy(payload)
        mixed["trades"].append({"evidence_mode": "live", "request_mode": "unified_short_term", "holding_minutes": 120, "strategy_version": "v2", "config_digest": "a" * 64, "source_digest": "b" * 64, "net_pnl_usd": 10.0})
        with self.assertRaisesRegex(MODULE.ShortTermContractError, "strategy_binding_mismatch"):
            MODULE.profit_attribution(mixed)

    def test_lifecycle_cannot_exceed_seven_days(self):
        lifecycle = {
            "schema_version": "ShortTermLifecycleV1",
            "lifecycle_id": "life-1",
            "decision_id": "decision-1",
            "snapshot_id": "snapshot-round-1",
            "strategy_version": "stage0-shadow-v1",
            "config_digest": "a" * 64,
            "source_digest": "b" * 64,
            "opened_at": "2026-07-01T00:00:00Z",
            "next_check_at": "2026-07-01T00:05:00Z",
            "expires_at": "2026-07-08T00:00:01Z",
            "current_action": "NO_TRADE",
            "auto_order_forbidden": True,
            "formal_action_eligible": False,
            "live_orders_enabled": False,
            "private_api_used": False,
            "human_confirmation_required": True,
        }
        with self.assertRaisesRegex(MODULE.ShortTermContractError, "lifecycle_clock_out_of_range"):
            MODULE.validate_lifecycle(lifecycle)

    def test_missed_review_is_append_only_and_never_enters_roi(self):
        review = {
            "schema_version": "MissedOpportunityReviewV1",
            "missed_review_id": "miss-1",
            "candidate_id": "ADAUSDT",
            "symbol": "ADAUSDT",
            "snapshot_id": "snapshot-round-1",
            "strategy_version": "stage0-shadow-v1",
            "config_digest": "a" * 64,
            "source_digest": "b" * 64,
            "decision_cutoff_at": "2026-07-01T00:00:00Z",
            "reviewed_at": "2026-07-02T00:00:00Z",
            "classification": "ranking_error",
            "paper_roi_eligible": False,
            "real_money_roi_eligible": False,
            "formal_action_eligible": False,
            "live_orders_enabled": False,
            "private_api_used": False,
            "human_confirmation_required": True,
        }
        MODULE.validate_missed_review(review)
        invalid = copy.deepcopy(review)
        invalid["real_money_roi_eligible"] = True
        with self.assertRaisesRegex(MODULE.ShortTermContractError, "cannot_enter_roi"):
            MODULE.validate_missed_review(invalid)
        inverted = copy.deepcopy(review)
        inverted["reviewed_at"] = "2026-06-30T23:59:59Z"
        with self.assertRaisesRegex(MODULE.ShortTermContractError, "time_out_of_range"):
            MODULE.validate_missed_review(inverted)

    def test_runtime_status_does_not_fabricate_maturity(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp) / "observations.jsonl"
            observation = {"schema_version": "ObservationSampleV1", "observation_id": "obs-1", "candidate_id": "ADAUSDT", "symbol": "ADAUSDT", "snapshot_id": "snap-1", "strategy_version": "v1", "config_digest": "a" * 64, "source_digest": "b" * 64, "observed_at": "2026-07-01T00:00:00Z", "review_due_at": "2026-07-08T00:00:00Z"}
            ledger.write_text(json.dumps(observation) + "\n", encoding="utf-8")
            result = MODULE.runtime_readiness(ledger)
            self.assertEqual(result["status"], "DATA_INSUFFICIENT")
            self.assertEqual(result["matured_observation_count"], 0)
            self.assertFalse(result["real_money_roi_eligible"])
            outcomes = Path(temp) / "outcomes.jsonl"
            outcomes.write_text(json.dumps({"observation_id": "obs-1"}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ShortTermContractError, "invalid_outcome_schema"):
                MODULE.runtime_readiness(ledger, outcomes)

    def test_baseline_replay_uses_all_three_frozen_thresholds(self):
        item = factor()
        payload = {
            "schema_version": "UnifiedShortTermReplayInputV1",
            "production_rule_changed": False,
            "thresholds": [{"target_pct": 5.0, "stop_pct": 3.0}, {"target_pct": 8.0, "stop_pct": 4.0}, {"target_pct": 10.0, "stop_pct": 5.0}],
            "candidates": [{"factor_snapshot": item, "opportunity_started_at": "2026-06-30T23:30:00Z", "opportunity_start_price": 98.0, "baseline_rank": 4, "baseline_research_top1": False, "baseline_action": "NO_TRADE", "outcome_path": [{"as_of": "2026-07-01T01:00:00Z", "high": 111.0, "low": 99.0, "close": 108.0}]}],
        }
        result = MODULE.baseline_replay(payload)
        self.assertEqual(len(result["threshold_summaries"]), 3)
        self.assertFalse(result["ada_special_case_present"])
        self.assertFalse(result["real_money_roi_eligible"])
        mixed = copy.deepcopy(payload)
        second = copy.deepcopy(mixed["candidates"][0])
        second["factor_snapshot"]["factor_snapshot_id"] = "factor-other"
        second["factor_snapshot"]["candidate_id"] = "OTHER"
        second["factor_snapshot"]["symbol"] = "OTHER"
        second["factor_snapshot"]["snapshot_id"] = "other-round"
        mixed["candidates"].append(second)
        with self.assertRaisesRegex(MODULE.ShortTermContractError, "binding_mismatch"):
            MODULE.baseline_replay(mixed)


if __name__ == "__main__":
    unittest.main()
