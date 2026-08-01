import importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"scripts"/"polymarket_fdv_lab.py";S=importlib.util.spec_from_file_location("fdv",P);M=importlib.util.module_from_spec(S);assert S.loader;S.loader.exec_module(M)
class FDVTests(unittest.TestCase):
 def test_self_test(self):self.assertEqual(M.self_test()["status"],"pass")
 def test_unspecified_liquid_source_blocked(self):self.assertTrue(M.diagnostics("total token supply at 4:00 PM ET on the calendar day following launch using the most liquid price source available")["high_rule_ambiguity"])
if __name__=="__main__":unittest.main()
