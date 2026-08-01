import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("policy_gate", ROOT / "scripts/polymarket_100usd_policy_gate.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)
POLICY = json.loads((ROOT / "config/sports_100usd_policy.json").read_text(encoding="utf-8"))
GATES = {gate: True for gate in MODULE.REQUIRED_GATES}


class PolicyGateTests(unittest.TestCase):
    def evaluate(self, p, ask, friction=1.0, gates=None, lower_bound=None, upper_bound=None):
        return MODULE.evaluate(POLICY, p, ask,
                               {"fees": friction, "spread": 0, "slippage": 0, "impact": 0},
                               gates or GATES, research_probability_lower_bound=lower_bound,
                               research_probability_upper_bound=upper_bound)

    def test_high_confidence_requires_three_cent_edge_and_probability_floor(self):
        result = self.evaluate(.84, 80, lower_bound=.81, upper_bound=.86)
        self.assertEqual(result["lane"], "HIGH_CONFIDENCE")
        self.assertEqual(result["action"], "PAPER_BUY")
        self.assertEqual(result["net_ev_cents"], 3.0)

    def test_high_confidence_thin_positive_edge_is_rejected(self):
        result = self.evaluate(.82, 80, lower_bound=.81, upper_bound=.86)
        self.assertEqual(result["lane"], "EXPLORATION_MICRO")
        self.assertEqual(result["action"], "PAPER_BUY_MICRO")
        self.assertEqual(result["formal_lane_rejection_reason"], "high_probability_net_ev_below_3c")
        self.assertFalse(result["counts_as_formal_recommendation"])
        self.assertEqual(result["stake_usd"], 1.0)

    def test_high_confidence_point_estimate_cannot_override_lower_bound(self):
        result = self.evaluate(.84, 75, lower_bound=.79, upper_bound=.86)
        self.assertEqual(result["lane"], "EXPLORATION_MICRO")
        self.assertEqual(result["formal_lane_rejection_reason"], "high_confidence_research_lower_bound_below_80")

    def test_high_probability_negative_ev_is_not_allowed(self):
        result = self.evaluate(.82, 86, lower_bound=.81, upper_bound=.86)
        self.assertEqual(result["lane"], "PASS")
        self.assertEqual(result["reason"], "exploration_net_ev_below_minus_4c")

    def test_sixty_to_eighty_requires_five_cents(self):
        self.assertEqual(self.evaluate(.65, 59, lower_bound=.62, upper_bound=.69)["lane"], "VALUE")
        self.assertEqual(self.evaluate(.65, 60, lower_bound=.62, upper_bound=.69)["lane"], "PASS")

    def test_any_gate_failure_blocks_both_lanes(self):
        gates = dict(GATES)
        gates["sources"] = False
        result = self.evaluate(.9, 70, gates=gates, lower_bound=.85, upper_bound=.91)
        self.assertEqual(result["lane"], "PASS")
        self.assertIn("sources", result["failed_gates"])

    def test_exploration_micro_accepts_only_complete_narrow_interval(self):
        result = self.evaluate(.71, 72, friction=2.0, lower_bound=.68, upper_bound=.74)
        self.assertEqual(result["lane"], "EXPLORATION_MICRO")
        self.assertEqual(result["net_ev_cents"], -3.0)

    def test_exploration_micro_rejects_missing_or_wide_interval(self):
        self.assertEqual(self.evaluate(.75, 74, lower_bound=.70)["reason"],
                         "exploration_probability_interval_missing")
        self.assertEqual(self.evaluate(.75, 74, lower_bound=.65, upper_bound=.76)["reason"],
                         "exploration_probability_interval_too_wide")

    def test_exploration_micro_rejects_extreme_favourite(self):
        result = self.evaluate(.94, 91, friction=1.0, lower_bound=.90, upper_bound=.96)
        self.assertEqual(result["lane"], "PASS")
        self.assertEqual(result["reason"], "exploration_ask_above_90c")


if __name__ == "__main__":
    unittest.main()
