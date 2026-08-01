import importlib.util,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];S=ROOT/"scripts/polymarket_manual_decision_ledger.py";sp=importlib.util.spec_from_file_location("mdl",S);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_append_and_dedupe(self):
  l=m.new();r={"source_daily_cycle_id":"c","created_at":"2026-07-12T00:00:00Z","event_research_artifact":"r","decision_items":[{"condition_id":"x","market_id":"m","recommendation_level":"conditional_watch"}]}
  a=m.build(r,l);self.assertEqual(a["added"],1);self.assertEqual(m.build(r,a["ledger"])["added"],0);self.assertTrue(l["append_only"]);self.assertFalse(l["live_orders_enabled"])
if __name__=="__main__":unittest.main()
