import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "manual-investment-strategy-operator" / "scripts"
FIXTURES = (
    ROOT / "manual-investment-strategy-operator" / "tests" / "fixtures"
)
sys.path.insert(0, str(SCRIPTS))

from v3_horizon_router import ResearchMode, split_request  # noqa: E402
from v3_incident_replay import replay_impulse_coverage  # noqa: E402


class HistoricalIncidentReplayTest(unittest.TestCase):
    def test_night_missed_impulse_becomes_coverage_rule_not_hindsight_entry(self):
        payload = json.loads(
            (FIXTURES / "night_missed_signal_snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        result = replay_impulse_coverage(payload)
        self.assertAlmostEqual(result["move_pct"], 20.0)
        self.assertTrue(result["coverage_gap"])
        self.assertFalse(result["counterfactual_predictability_claimed"])
        self.assertIn("rejected candidates", result["reusable_rule"])

    def test_sol_longterm_and_shortterm_are_split(self):
        specs = split_request(
            request_id="sol-incident",
            query="SOL 长期 DCA 与一周短线是否现在买",
            requested_at="2026-07-25T08:30:00+08:00",
            modes=(
                ResearchMode.LONGTERM_DCA,
                ResearchMode.TACTICAL_1_7D,
            ),
        )
        self.assertEqual(len(specs), 2)
        self.assertNotEqual(specs[0].request_id, specs[1].request_id)
        self.assertNotEqual(specs[0].mode, specs[1].mode)


if __name__ == "__main__":
    unittest.main()
