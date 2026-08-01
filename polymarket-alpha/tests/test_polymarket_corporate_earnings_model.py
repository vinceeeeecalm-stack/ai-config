import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "scripts/polymarket_corporate_earnings_model.py"
S = importlib.util.spec_from_file_location("earnings_model", P)
M = importlib.util.module_from_spec(S); assert S.loader; S.loader.exec_module(M)


class EarningsModelTests(unittest.TestCase):
    def test_self(self): self.assertEqual(M.self_test()["status"], "pass")
    def test_future_price_excluded(self):
        cutoff=M.parse_time("2026-01-01T12:00:00Z");start=M.parse_time("2025-12-31T00:00:00Z");point=M.latest_before([{"t":int(cutoff.timestamp())-1,"p":.6},{"t":int(cutoff.timestamp())+1,"p":.9}],cutoff,start);self.assertEqual(point["p"],.6)
    def test_fractional_timestamp(self): self.assertIsNotNone(M.parse_time("2025-11-06T15:32:47.06809Z"))


if __name__ == "__main__": unittest.main()
