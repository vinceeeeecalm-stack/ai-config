import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_runner.py"
spec = importlib.util.spec_from_file_location("polymarket_runner", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class RunnerTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_partial_or_degraded_discovery_cannot_open_new_position(self):
        ok_manifest = {"data_status": "ok", "failed_request_count": 0, "terminal_cursor_proven": True}
        verification = {"status": "pass"}
        complete, blockers = module.discovery_entry_contract(100, ok_manifest, verification)
        self.assertFalse(complete)
        self.assertIn("full_market_discovery_not_complete", blockers)
        complete, blockers = module.discovery_entry_contract(0, {"data_status": "degraded", "failed_request_count": 1}, verification)
        self.assertFalse(complete)
        self.assertIn("public_snapshot_degraded_or_integrity_failed", blockers)
        complete, blockers = module.discovery_entry_contract(0, ok_manifest, verification)
        self.assertTrue(complete)
        self.assertEqual(blockers, [])
        incomplete_cursor = {"data_status": "ok", "failed_request_count": 0, "terminal_cursor_proven": False}
        complete, blockers = module.discovery_entry_contract(0, incomplete_cursor, verification)
        self.assertFalse(complete)
        self.assertIn("full_market_discovery_not_complete", blockers)

    def test_unapproved_estimate_is_removed_and_blocks_entry(self):
        registry={"eligible_paper_model_versions":[],"paper_only":True,"live_orders_enabled":False,"private_api_used":False}
        approvals={"approved_model_versions":[],"paper_only":True,"live_orders_enabled":False,"private_api_used":False}
        filtered,audit=module.validate_model_governance({"m1":{"model_version":"failed-v1"}},registry,approvals)
        self.assertEqual(filtered,{})
        self.assertEqual(audit["status"],"blocked")
        self.assertIn("unapproved_or_ineligible_probability_estimates_present",audit["blockers"])

    def test_pending_early_exit_is_still_polled_for_final_resolution(self):
        ledger = {
            "open_positions": [],
            "closed_positions": [{
                "paper_trade_id": "t1", "market_id": "m1", "yes_token_id": "y1",
                "exit_reason": ["core_thesis_invalidated"], "counterfactual_status": "pending",
            }],
        }
        with patch.object(module.public_data, "get_json", return_value={"id": "m1"}), patch.object(
            module.public_data, "infer_resolution", return_value={"winning_outcome": "YES"}
        ):
            resolutions, states, errors = module.open_market_data(ledger, {})
        self.assertEqual(resolutions, {"m1": "YES"})
        self.assertIn("m1", states)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
