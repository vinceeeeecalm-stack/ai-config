import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_lolesports_gpr_lab.py"
spec = importlib.util.spec_from_file_location("lol_gpr", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)


class LoLEsportsGPRTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_future_rating_is_not_used(self):
        row = {"teamGPRHistory": [{"dateCalculated": "2026-07-01T00:00:00Z", "elo": 1400},
                                  {"dateCalculated": "2026-07-20T00:00:00Z", "elo": 1800}]}
        selected = module.rating_before(row, datetime(2026, 7, 10, tzinfo=timezone.utc))
        self.assertEqual(selected["elo"], 1400)

    def test_robots_root_disallow_blocks(self):
        self.assertFalse(module.robots_allows_public_read("User-agent: *\nDisallow: /\n"))
        self.assertTrue(module.robots_allows_public_read("User-agent: *\n"))


if __name__ == "__main__": unittest.main()
