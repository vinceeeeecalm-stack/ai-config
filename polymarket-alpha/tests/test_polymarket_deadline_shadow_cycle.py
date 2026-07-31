import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_deadline_shadow_cycle.py"
spec = importlib.util.spec_from_file_location("polymarket_deadline_shadow_cycle", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class DeadlineShadowCycleTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_recorded_window_is_not_active_unrecorded(self):
        now = datetime(2026, 7, 12, 4, 30, tzinfo=timezone.utc)
        outputs = {"weather": {"excluded": [{"event_id": "e1", "reason": "forecast_already_recorded", "capture_window_start_at": "2026-07-12T04:00:00+00:00", "cutoff_at": "2026-07-12T05:00:00+00:00"}]}}
        self.assertEqual(module.deadline_inventory(outputs, now)["active_unrecorded_window_count"], 0)

    def test_active_failure_keeps_diagnostics_and_next_retry(self):
        now = datetime(2026, 7, 12, 4, 2, tzinfo=timezone.utc)
        outputs = {"weather": {"excluded": [{
            "event_id": "e1", "reason": "two_sided_live_book_missing",
            "capture_window_start_at": "2026-07-12T03:59:00+00:00",
            "cutoff_at": "2026-07-12T04:59:00+00:00", "missing_market_count": 3,
            "missing_live_books": [{"market_id": "m1", "error": "two_sided_book_missing"}],
        }]}}
        row = module.deadline_inventory(outputs, now)["active_unrecorded_windows"][0]
        self.assertEqual(row["missing_market_count"], 3)
        self.assertEqual(row["next_expected_retry_at"], "2026-07-12T04:32:00+00:00")
        self.assertTrue(row["scheduled_retry_remains_before_window_end"])

    def test_no_retry_claim_when_cadence_exceeds_window(self):
        now = datetime(2026, 7, 12, 4, 40, tzinfo=timezone.utc)
        outputs = {"weather": {"excluded": [{
            "event_id": "e1", "reason": "two_sided_live_book_missing",
            "capture_window_start_at": "2026-07-12T03:59:00+00:00",
            "cutoff_at": "2026-07-12T04:59:00+00:00",
        }]}}
        row = module.deadline_inventory(outputs, now)["active_unrecorded_windows"][0]
        self.assertIsNone(row["next_expected_retry_at"])
        self.assertFalse(row["scheduled_retry_remains_before_window_end"])

    def test_full_cycle_can_supply_retry_before_deadline_cycle(self):
        now = datetime(2026, 7, 12, 4, 31, tzinfo=timezone.utc)
        full_completed = datetime(2026, 7, 12, 3, 48, tzinfo=timezone.utc)
        outputs = {"weather": {"excluded": [{
            "event_id": "e1", "reason": "two_sided_live_book_missing",
            "capture_window_start_at": "2026-07-12T03:59:00+00:00",
            "cutoff_at": "2026-07-12T04:59:00+00:00",
        }]}}
        row = module.deadline_inventory(outputs, now, full_completed)["active_unrecorded_windows"][0]
        self.assertEqual(row["next_expected_retry_at"], "2026-07-12T04:48:00+00:00")
        self.assertEqual(row["next_expected_retry_source"], "full_validation")
        self.assertTrue(row["scheduled_retry_remains_before_window_end"])


if __name__ == "__main__":
    unittest.main()
