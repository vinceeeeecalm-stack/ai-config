import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_esports_lab.py"
spec = importlib.util.spec_from_file_location("polymarket_esports_lab", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)


class EsportsLabTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_game_two_is_not_full_match(self):
        self.assertIsNotNone(module.DERIVATIVE.search("Team A vs Team B - Game 2 Winner"))

    def test_latest_before_excludes_future_price(self):
        cutoff = module.baseball.parse_time("2026-07-12T19:00:00Z")
        market_start = module.baseball.parse_time("2026-07-10T00:00:00Z")
        rows = [{"t": int(cutoff.timestamp()) - 5, "p": 0.51}, {"t": int(cutoff.timestamp()) + 1, "p": 0.97}]
        self.assertEqual(module.latest_before(rows, cutoff, market_start)["p"], 0.51)


if __name__ == "__main__": unittest.main()
