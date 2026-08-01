import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "polymarket_strategy_evolver.py"
spec = importlib.util.spec_from_file_location("polymarket_strategy_evolver", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class StrategyEvolverTests(unittest.TestCase):
    def setUp(self):
        self.policy = module.core.read_json(ROOT / "config" / "policy.json")
        self.ledger = module.core.new_ledger(self.policy)
        self.overlay = module.new_overlay(self.policy)

    @staticmethod
    def case(index, proposal, explicit=None):
        row = {
            "back_case_id": f"b{index}", "paper_trade_id": f"t{index}",
            "status": "reviewed", "failure_category": "repeated_defect",
            "domain": "sports", "model_version": "model-v1", "proposed_change": proposal,
        }
        if explicit:
            row["explicit_error_type"] = explicit
        return row

    def test_one_failure_does_not_apply(self):
        self.ledger["back_cases"] = [self.case(1, {"type": "raise_min_net_edge", "value": 0.06})]
        result = module.evolve(self.ledger, self.policy, self.overlay)
        self.assertEqual(result["summary"]["applied"], [])

    def test_three_similar_failures_apply_once_with_stable_id(self):
        proposal = {"type": "raise_min_net_edge", "value": 0.06}
        self.ledger["back_cases"] = [self.case(i, proposal) for i in range(3)]
        first = module.evolve(self.ledger, self.policy, self.overlay)
        second = module.evolve(self.ledger, self.policy, first["overlay"])
        self.assertEqual(len(first["summary"]["applied"]), 1)
        self.assertEqual(second["summary"]["applied"], [])
        effective = module.core.load_effective_policy(self.policy, first["overlay"])
        self.assertEqual(effective["entry_gate"]["min_net_edge_per_share"], 0.06)

    def test_explicit_rule_error_applies_immediately(self):
        proposal = {"type": "block_model_version", "model_version": "model-v1"}
        self.ledger["back_cases"] = [self.case(1, proposal, "rule")]
        result = module.evolve(self.ledger, self.policy, self.overlay)
        self.assertEqual(len(result["summary"]["applied"]), 1)

    def test_unsafe_or_loosening_change_is_rejected(self):
        proposal = {"type": "raise_min_net_edge", "value": 0.01}
        self.ledger["back_cases"] = [self.case(1, proposal, "data")]
        result = module.evolve(self.ledger, self.policy, self.overlay)
        self.assertEqual(result["summary"]["applied"], [])
        self.assertEqual(result["summary"]["rejected"][0]["reason"], "cannot_lower_net_edge")

    def test_five_degraded_forward_samples_revert(self):
        proposal = {"type": "lower_max_spread", "value": 0.01}
        self.ledger["back_cases"] = [self.case(i, proposal) for i in range(3)]
        applied = module.evolve(self.ledger, self.policy, self.overlay)
        change_id = applied["summary"]["applied"][0]
        self.ledger["closed_positions"] = [
            {"paper_trade_id": f"f{i}", "overlay_change_ids": [change_id], "net_pnl_usd": -1.0}
            for i in range(5)
        ]
        result = module.evolve(self.ledger, self.policy, applied["overlay"])
        self.assertEqual(result["summary"]["reverted"], [change_id])
        effective = module.core.load_effective_policy(self.policy, result["overlay"])
        self.assertNotIn(change_id, effective["overlay_change_ids"])
        next_cycle = module.evolve(self.ledger, self.policy, result["overlay"])
        self.assertEqual(next_cycle["summary"]["applied"], [])
        self.assertEqual(next_cycle["summary"]["reverted"], [])

    def test_probability_cap_changes_candidate_and_flags_stay_safe(self):
        proposal = {"type": "probability_cap", "value": 0.78, "domain": "crypto"}
        self.ledger["back_cases"] = [self.case(i, proposal) for i in range(3)]
        result = module.evolve(self.ledger, self.policy, self.overlay)
        effective = module.core.load_effective_policy(self.policy, result["overlay"])
        self.assertTrue(effective["paper_only"])
        self.assertFalse(effective["live_orders_enabled"])
        self.assertFalse(effective["private_api_used"])
        market = module.core.normalize_market({
            "id": "m", "question": "Will Bitcoin rise?", "endDate": "2099-01-01T00:00:00Z",
            "outcomes": '["Yes","No"]', "clobTokenIds": '["y","n"]',
        })
        estimate = {
            "probability": 0.90, "confidence_low": 0.80, "confidence_high": 0.95,
            "model_version": "model-v1", "calibration_samples": 40,
            "sources": [{"kind": "official", "url": "o"}, {"kind": "independent", "url": "a"}, {"kind": "independent", "url": "b"}],
            "rules_review": {"status": "clear", "reviewed_at": "x"}, "failure_paths": [{"controlled": True}],
        }
        decision = module.core.evaluate_candidate(market, estimate, {"bids": [{"price": 0.70, "size": 1000}], "asks": [{"price": 0.71, "size": 1000}]}, effective, 25)
        self.assertEqual(decision["raw_model_probability"], 0.90)
        self.assertEqual(decision["model_probability"], 0.78)
        self.assertIn("model_probability_below_gate", decision["failed_gates"])


if __name__ == "__main__":
    unittest.main()
