import importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"scripts"/"polymarket_finance_daily_lab.py";S=importlib.util.spec_from_file_location("fin",P);M=importlib.util.module_from_spec(S);assert S.loader;S.loader.exec_module(M)
class FinanceTests(unittest.TestCase):
 def test_self(self):self.assertEqual(M.self_test()["status"],"pass")
 def test_threshold_excluded(self):self.assertEqual(M.contract_type("SPY closes above $700"),"close_threshold")
 def test_missing_session_definition_is_ambiguous(self):
  t="Apple (AAPL) Up or Down? most recent prior trading day. Pyth. exactly equal resolves Down."
  self.assertTrue(M.diagnostics(t)["high_rule_ambiguity"])
 def test_cutoff_never_uses_future_price(self):
  cutoff=M.core.parse_iso("2026-07-10T19:00:00Z");start=M.core.parse_iso("2026-07-09T00:00:00Z")
  point=M.latest_before([{"t":int(cutoff.timestamp())-1,"p":.51},{"t":int(cutoff.timestamp())+1,"p":.99}],cutoff,start)
  self.assertEqual(point["p"],.51)
 def test_fractional_start_timestamp(self):
  self.assertIsNotNone(M.parse_time("2026-03-24T12:05:13.26752Z"))
if __name__=="__main__":unittest.main()
