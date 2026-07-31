import importlib.util
import json
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    ROOT
    / "active-alpha-paper-monitor"
    / "scripts"
    / "us_crypto_legislative_event_radar.py"
)
REPLAY_PATH = (
    ROOT
    / "active-alpha-paper-monitor"
    / "config"
    / "incident_replays"
    / "clarity_20260726.json"
)

SPEC = importlib.util.spec_from_file_location(
    "us_crypto_legislative_event_radar", SCRIPT_PATH
)
RADAR = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(RADAR)


class UsCryptoLegislativeEventRadarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            REPLAY_PATH.read_text(encoding="utf-8")
        )

    def test_clarity_replay_blocks_false_monday_vote_claim(self):
        result = RADAR.evaluate(deepcopy(self.fixture))
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(result["current_chamber"], "senate")
        self.assertEqual(result["current_stage"], "committee_advanced")
        self.assertEqual(
            result["official_schedule_status"],
            "not_on_published_floor_schedule",
        )
        self.assertTrue(result["false_vote_claim_blocked"])
        self.assertEqual(
            result["event_label"], "expected_window_unconfirmed"
        )
        self.assertIsNone(result["forecast_probability_pct"])
        self.assertEqual(result["max_active_action"], "watch")

    def test_house_passage_does_not_become_senate_passage(self):
        result = RADAR.evaluate(deepcopy(self.fixture))
        self.assertIn(
            "senate_floor_scheduling", result["remaining_steps"]
        )
        self.assertNotEqual(result["current_stage"], "floor_passed")

    def test_official_floor_item_can_confirm_schedule(self):
        fixture = deepcopy(self.fixture)
        fixture["official_schedule_checks"][1]["bill_present"] = True
        fixture["official_schedule_checks"][1]["scheduled_at"] = (
            "2026-07-27T21:30:00Z"
        )
        result = RADAR.evaluate(fixture)
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(
            result["official_schedule_status"],
            "official_floor_vote_scheduled",
        )
        self.assertFalse(result["false_vote_claim_blocked"])

    def test_explicit_absence_overrides_negative_text_mention(self):
        fixture = deepcopy(self.fixture)
        senate_check = fixture["official_schedule_checks"][1]
        senate_check["bill_present"] = False
        senate_check["published_items"] = [
            "H.R. 3633 / CLARITY Act is not on the published schedule"
        ]
        result = RADAR.evaluate(fixture)
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(
            result["official_schedule_status"],
            "not_on_published_floor_schedule",
        )
        self.assertTrue(result["false_vote_claim_blocked"])

    def test_weekend_requires_both_chamber_schedules(self):
        fixture = deepcopy(self.fixture)
        fixture["official_schedule_checks"] = [
            fixture["official_schedule_checks"][1]
        ]
        result = RADAR.evaluate(fixture)
        self.assertFalse(result["passed"])
        self.assertIn(
            "weekend_house_and_senate_schedule_coverage_incomplete",
            result["errors"],
        )
        self.assertEqual(result["data_quality"], "degraded")


if __name__ == "__main__":
    unittest.main()
