import importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"scripts/polymarket_macro_policy_lab.py";S=importlib.util.spec_from_file_location("macro_policy",P);M=importlib.util.module_from_spec(S);assert S.loader;S.loader.exec_module(M)
class MacroPolicyTests(unittest.TestCase):
 def test_self(self):self.assertEqual(M.self_test()["status"],"pass")
 def test_recession_separate(self):self.assertEqual(M.event_type("US recession by end of 2026?"),"recession_binary")
if __name__=="__main__":unittest.main()
