import importlib.util,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];S=ROOT/"scripts/polymarket_periodic_review.py";sp=importlib.util.spec_from_file_location("pr",S);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_contract(self):
  p=m.build();self.assertEqual(p["independence_unit"],"condition_id");self.assertTrue(p["hourly_snapshots_do_not_inflate_samples"]);self.assertFalse(p["real_money_execution_authorized"]);self.assertTrue(p["unverified_results_excluded_from_direction_price_exit_metrics"])
 def test_invalid_winner_is_not_mapped_to_no(self):
  self.assertIsNone(m.normalize_winner(None));self.assertIsNone(m.normalize_winner(""));self.assertIsNone(m.normalize_winner("pending"));self.assertEqual(m.normalize_winner("YES"),"YES")
if __name__=="__main__":unittest.main()
