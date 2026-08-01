import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "scripts/polymarket_corporate_lab.py"
S = importlib.util.spec_from_file_location("corporate", P)
M = importlib.util.module_from_spec(S); assert S.loader; S.loader.exec_module(M)


class CorporateTests(unittest.TestCase):
    def test_self(self): self.assertEqual(M.self_test()["status"], "pass")
    def test_earnings_before_ipo_tags(self): self.assertEqual(M.event_type("Google Q2 capex above $43B?", {"Earnings", "IPOs"}), "earnings_kpi")
    def test_strict_earnings_contract(self):
        description = "As of market creation, the Street consensus estimate for X's GAAP EPS is $1.25. The resolution source is official earnings documents. Fallback SeekingAlpha."
        market = {"id":"m","conditionId":"c","description":description,"outcomes":'["Yes","No"]',"outcomePrices":'["1","0"]',"closed":True,"endDate":"2026-01-01T00:00:00Z","createdAt":"2025-12-20T00:00:00Z","clobTokenIds":'["a","b"]'}
        row, reason = M.strict_earnings_event({"id":"e","title":"Will Example (EX) beat quarterly earnings?","markets":[market]})
        self.assertIsNone(reason); self.assertEqual(row["consensus_strike"],1.25)


if __name__ == "__main__": unittest.main()
