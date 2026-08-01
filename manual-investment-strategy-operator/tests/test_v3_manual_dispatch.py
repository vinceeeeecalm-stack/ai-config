import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "manual-investment-strategy-operator"
    / "scripts"
    / "v3_manual_dispatch.py"
)
SPEC = importlib.util.spec_from_file_location("v3_manual_dispatch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class V3ManualDispatchTest(unittest.TestCase):
    def test_frozen_package_passes_deterministically(self):
        package = MODULE._self_test_package(negative=False)
        first = MODULE.validate_package(package)
        second = MODULE.validate_package(package)
        self.assertTrue(first["passed"], first)
        self.assertEqual(first, second)

    def test_inner_price_evidence_failure_blocks_outer_status(self):
        audit = MODULE.validate_package(
            MODULE._self_test_package(negative=True)
        )
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["status"], "failed")
        self.assertTrue(
            any("evidence_value_mismatch" in item for item in audit["errors"])
        )

    def test_no_qualified_candidate_is_valid_and_explicit(self):
        package = MODULE._self_test_package(negative=False)
        package["recommendations"] = []
        package["no_qualified_candidate_modes"] = ["longterm_dca"]
        package["rejected_candidates"] = [
            {
                "request_mode": "longterm_dca",
                "symbol": "SOL",
                "current_direct_decision": "do_not_enter_now",
                "evidence_snapshot_id": "snapshot-self-test",
                "decision_price": 188.25,
                "decision_price_evidence_id": "sol-price-self-test",
                "rejection_reasons": ["fundamental evidence missing"],
                "evidence_gaps": ["adoption", "value capture"],
                "nearest_pass_distance": "research evidence incomplete",
            }
        ]
        audit = MODULE.validate_package(package)
        self.assertTrue(audit["passed"], audit)
        self.assertEqual(
            audit["no_qualified_candidate_modes"], ["longterm_dca"]
        )

    def test_rejected_candidate_cannot_use_fake_trade_placeholders(self):
        package = MODULE._self_test_package(negative=False)
        package["recommendations"] = []
        package["no_qualified_candidate_modes"] = ["longterm_dca"]
        package["rejected_candidates"] = [
            {
                "request_mode": "longterm_dca",
                "symbol": "SOL",
                "current_direct_decision": "do_not_enter_now",
                "evidence_snapshot_id": "snapshot-self-test",
                "decision_price": 188.25,
                "decision_price_evidence_id": "sol-price-self-test",
                "rejection_reasons": ["evidence missing"],
                "evidence_gaps": ["options"],
                "nearest_pass_distance": "unknown",
                "probability_event": {"probability_pct": 50},
            }
        ]
        audit = MODULE.validate_package(package)
        self.assertFalse(audit["passed"])
        self.assertTrue(
            any(
                "forbidden_trade_placeholders" in item
                for item in audit["errors"]
            )
        )

    def test_zero_cash_preserves_research_and_blocks_execution(self):
        package = MODULE._self_test_package(negative=False)
        recommendation = package["recommendations"][0]
        self.assertEqual(recommendation["research_decision"], "preferred")
        self.assertEqual(
            recommendation["current_direct_decision"], "small_entry_now"
        )
        self.assertEqual(recommendation["execution_decision"], "no_deploy_cash")
        self.assertEqual(recommendation["deployable_cash"], 0)
        self.assertTrue(MODULE.validate_package(package)["passed"])


if __name__ == "__main__":
    unittest.main()
