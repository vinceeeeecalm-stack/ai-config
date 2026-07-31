import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/polymarket_sports_result_gate.py"
spec = importlib.util.spec_from_file_location("sports_result_gate", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)


class SportsResultGateTests(unittest.TestCase):
    def test_missing_result_cannot_be_a_miss_or_calibration_sample(self):
        row = module.normalize_result({"review_id": "missing", "direction_hit": True, "binary_outcome": 1})
        self.assertEqual(row["final_result_status"], "UNKNOWN")
        self.assertIsNone(row["direction_hit"])
        self.assertFalse(row["eligible_for_direction_calibration"])
        self.assertIsNone(row["brier_score"])

    def test_conflicting_result_is_censored(self):
        row = module.normalize_result({"review_id": "conflict", "result_status": "conflicting",
                                       "official_result": "A won", "binary_outcome": 1,
                                       "direction_hit": True, "official_result_source": "https://official.test"})
        self.assertEqual(row["final_result_status"], "UNKNOWN")
        self.assertIsNone(row["direction_hit"])

    def test_source_conflict_overrides_result_text(self):
        row = module.normalize_result({"review_id": "source-conflict", "result_status": "verified",
                                       "official_result": "A won", "binary_outcome": 1,
                                       "direction_hit": True, "source_conflict": True,
                                       "official_result_source": "https://official.test"})
        self.assertEqual(row["final_result_status"], "UNKNOWN")
        self.assertEqual(row["unknown_reason"], "result_sources_conflict")

    def test_verified_result_can_be_scored_but_fill_stays_separate(self):
        row = module.normalize_result({"review_id": "verified", "result_status": "verified",
                                       "official_result": "A won 2-0", "binary_outcome": 1,
                                       "direction_hit": True, "counterfactual_fill_valid": False,
                                       "eligible_for_price_edge_calibration": True,
                                       "official_result_source": "https://official.test"})
        self.assertEqual(row["final_result_status"], "VERIFIED")
        self.assertTrue(row["direction_hit"])
        self.assertTrue(row["eligible_for_direction_calibration"])
        self.assertFalse(row["eligible_for_price_edge_calibration"])

    def test_audit_rate_uses_verified_denominator_only(self):
        payload = module.audit([
            {"review_id": "hit", "result_status": "verified", "official_result": "won", "binary_outcome": 1, "direction_hit": True, "official_result_source": "https://official.test/1"},
            {"review_id": "miss", "result_status": "verified", "official_result": "lost", "binary_outcome": 0, "direction_hit": False, "official_result_source": "https://official.test/2"},
            {"review_id": "unknown", "direction_hit": True},
        ])
        self.assertEqual(payload["verified_results"], 2)
        self.assertEqual(payload["unknown_or_unverified_results"], 1)
        self.assertEqual(payload["direction_hit_rate_verified_only"], .5)

    def test_result_without_source_is_unknown(self):
        row = module.normalize_result({"review_id": "no-source", "result_status": "verified",
                                       "official_result": "A won", "binary_outcome": 1,
                                       "direction_hit": True})
        self.assertEqual(row["final_result_status"], "UNKNOWN")
        self.assertEqual(row["unknown_reason"], "result_sources_missing")


if __name__ == "__main__":
    unittest.main()
