import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_baseball_lab.py"
spec = importlib.util.spec_from_file_location("polymarket_baseball_lab", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class BaseballLabTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_post_cutoff_history_is_never_selected(self):
        start = module.core.parse_iso("2026-07-10T00:00:00Z")
        cutoff = module.core.parse_iso("2026-07-12T19:00:00Z")
        history = [
            {"t": int(module.core.parse_iso("2026-07-12T18:55:00Z").timestamp()), "p": 0.6},
            {"t": int(module.core.parse_iso("2026-07-12T19:05:00Z").timestamp()), "p": 0.9},
        ]
        point = module.latest_before(history, cutoff, start)
        self.assertEqual(point["p"], 0.6)

    def test_legacy_event_identity_is_stable(self):
        market = {
            "id": "m1", "question": "A vs. B", "sportsMarketType": "moneyline",
            "gameStartTime": "2025-07-12T20:00:00Z", "startDate": "2025-07-10T00:00:00Z",
            "description": "The primary resolution source is official final statistics.",
            "outcomes": '["A", "B"]', "outcomePrices": '["1", "0"]',
            "clobTokenIds": '["ta", "tb"]', "closed": True,
        }
        row, reason = module.parse_moneyline({"id": "e1"}, market)
        self.assertIsNone(reason)
        self.assertEqual(row["game_identity_source"], "event_id_plus_game_start")

    def test_legacy_utc_suffix_parses(self):
        self.assertIsNotNone(module.parse_time("2025-04-18 23:05:00+00"))

    def test_team_name_variants(self):
        self.assertTrue(module.team_matches("Red Sox", "Boston Red Sox"))
        self.assertFalse(module.team_matches("Red Sox", "Chicago White Sox"))

    def test_elo_never_uses_current_result_in_prediction(self):
        games = [{
            "game_pk": "1", "game_date": "2025-04-01T20:00:00Z",
            "away_team_id": "a", "home_team_id": "b", "home_win": 1,
        }]
        prediction = module.elo_game_predictions(games, 20.0, 0.0)["1"]
        self.assertEqual(prediction, 0.5)


if __name__ == "__main__":
    unittest.main()
