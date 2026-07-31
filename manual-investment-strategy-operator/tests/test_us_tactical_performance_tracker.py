import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "us_tactical_performance_tracker.py"
SPEC = importlib.util.spec_from_file_location("us_tactical_performance_tracker", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class UsTacticalPerformanceTrackerTest(unittest.TestCase):
    def test_zero_current_value_requires_baseline_rebuild_without_fake_roi(self):
        ledger = MODULE.empty_ledger()
        ledger["sleeves"] = [{
            "sleeve_id": "stale",
            "status": "active",
            "baseline_at": "2026-01-01T00:00:00+00:00",
            "baseline_value_usd": 1000,
            "target_monthly_return_pct": 50,
            "target_quarterly_return_pct": 50,
            "protected_holdings_excluded": ["CRCL"],
        }]
        snapshot = {
            "generated_at": "2026-07-29T00:00:00+00:00",
            "holdings": [],
        }
        result = MODULE.build_summary(
            ledger=ledger,
            snapshot=snapshot,
            tactical_symbols=[],
            cash_symbols=["USD_US_EQUITY"],
            protected_symbols=["CRCL"],
            include_conditional_symbols=[],
        )
        self.assertEqual(result["status"], "baseline_rebuild_required")
        self.assertEqual(result["max_allowed_action"], "watch")
        self.assertIsNone(result["required_daily_return_to_monthly_target_pct"])
        self.assertIn("CRCL", result["protected_symbols_excluded"])


if __name__ == "__main__":
    unittest.main()
