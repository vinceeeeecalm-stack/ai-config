import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "recommendation_execution_calendar_gate",
    ROOT / "scripts" / "recommendation_execution_calendar_gate.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class RecommendationExecutionCalendarGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(
            (ROOT / "config" / "recommendation_execution_calendar_schema.json").read_text(encoding="utf-8")
        )

    def test_self_test_fixture_has_positive_frozen_decision_price(self) -> None:
        result = MODULE.run_self_test(self.schema)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["valid_fixture"]["valid"])

    def test_missing_decision_price_is_false_green_blocker(self) -> None:
        fixture = {field: "x" for field in self.schema["required_fields"]}
        fixture.update(
            {
                "symbol": "BTCUSDT",
                "generated_at": "2026-07-30T08:30:00+08:00",
                "decision_price": None,
                "price_as_of": "2026-07-30T08:29:00+08:00",
                "data_age_minutes": 1,
                "data_freshness_status": "verified_realtime",
                "entry_window_start": "2026-07-30",
                "entry_window_end": "2026-07-31",
                "decision_valid_until": "2026-07-31T08:30:00+08:00",
                "latest_exit_or_review_date": "2026-07-31"
            }
        )
        result = MODULE.validate_record(fixture, self.schema)
        self.assertFalse(result["valid"])
        self.assertIn("decision_price", result["missing_fields"])
        self.assertIn("decision_price must be a positive numeric frozen price", result["errors"])


if __name__ == "__main__":
    unittest.main()
