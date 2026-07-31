import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("public_preflight", ROOT / "scripts/polymarket_sports_public_preflight.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class PublicPreflightTests(unittest.TestCase):
    def test_team_and_score_reconciliation(self):
        event = {
            "competitions": [{"competitors": [
                {"homeAway": "home", "score": "2", "team": {"displayName": "Henan"}},
                {"homeAway": "away", "score": "0", "team": {"displayName": "Qingdao Hainiu"}},
            ]}]
        }
        selected = MODULE.find_score_event([event], "Henan FC", "Qingdao Hainiu FC")
        self.assertIs(selected, event)
        self.assertEqual(MODULE.score_pair(selected), ("2", "0"))

    def test_wrong_fixture_is_not_selected(self):
        event = {
            "competitions": [{"competitors": [
                {"homeAway": "home", "score": "1", "team": {"displayName": "Beijing Guoan"}},
                {"homeAway": "away", "score": "0", "team": {"displayName": "Liaoning Tieren"}},
            ]}]
        }
        self.assertIsNone(MODULE.find_score_event([event], "Henan FC", "Qingdao Hainiu FC"))

    def test_gamma_title_split(self):
        self.assertEqual(MODULE.gamma_teams("Henan FC vs. Qingdao Hainiu FC"),
                         ("Henan FC", "Qingdao Hainiu FC"))


if __name__ == "__main__":
    unittest.main()
