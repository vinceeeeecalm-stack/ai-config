import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_capture_backcase.py"
spec = importlib.util.spec_from_file_location("polymarket_capture_backcase", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)


class CaptureBackCaseTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_waiting_attempts_do_not_count_as_failures(self):
        miss = {"domain": "weather", "event_id": "e1", "reason": "forecast_window_missed",
                "cutoff_at": "2026-07-12T05:00:00+00:00"}
        attempts = [{"attempted_at": "2026-07-12T03:30:00+00:00", "reason": "fixed_cutoff_not_reached"}]
        case = module.build_backcase(miss, attempts)
        self.assertEqual(case["attempt_count"], 0)
        self.assertEqual(case["defect_classification"], "scheduler_or_capture_coverage")

    def test_missing_book_details_are_deduplicated(self):
        miss = {"domain": "weather", "event_id": "e1", "reason": "forecast_window_missed",
                "cutoff_at": "2026-07-12T05:00:00+00:00"}
        missing = {"market_id": "m1", "bucket": "hot", "error": "two_sided_book_missing"}
        attempts = [
            {"attempted_at": "2026-07-12T04:02:00+00:00", "reason": "two_sided_live_book_missing", "missing_live_books": [missing]},
            {"attempted_at": "2026-07-12T04:31:00+00:00", "reason": "two_sided_live_book_missing", "missing_live_books": [missing]},
        ]
        case = module.build_backcase(miss, attempts)
        self.assertEqual(case["attempt_count"], 2)
        self.assertEqual(len(case["missing_live_books"]), 1)
        self.assertFalse(case["model_parameter_changed"])

    def test_backcase_id_is_stable_for_same_window(self):
        miss = {"domain": "football", "event_id": "e1", "reason": "forecast_window_missed",
                "cutoff_at": "2026-07-12T12:00:00+00:00"}
        first = module.build_backcase(miss, [])
        second = module.build_backcase(miss, [])
        self.assertEqual(first["backcase_id"], second["backcase_id"])

    def test_matching_attempts_excludes_post_window_polling(self):
        miss = {"domain": "weather", "event_id": "e1", "reason": "forecast_window_missed",
                "capture_window_start_at": "2026-07-12T04:00:00+00:00",
                "cutoff_at": "2026-07-12T05:00:00+00:00"}
        ledger = {"events": [
            {"at": "2026-07-12T04:30:00+00:00", "excluded": [{"event_id": "e1", "reason": "two_sided_live_book_missing", "cutoff_at": miss["cutoff_at"]}]},
            {"at": "2026-07-12T05:01:00+00:00", "excluded": [{"event_id": "e1", "reason": "forecast_window_missed", "cutoff_at": miss["cutoff_at"]}]},
        ]}
        attempts = module.matching_attempts(miss, ledger)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["reason"], "two_sided_live_book_missing")


if __name__ == "__main__":
    unittest.main()
