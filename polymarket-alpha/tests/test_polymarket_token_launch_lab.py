import importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"scripts/polymarket_token_launch_lab.py";S=importlib.util.spec_from_file_location("token",P);M=importlib.util.module_from_spec(S);assert S.loader;S.loader.exec_module(M)
class TokenTests(unittest.TestCase):
 def test_self(self):self.assertEqual(M.self_test()["status"],"pass")
 def test_announcement_only_rejected(self):self.assertEqual(M.rule_profile("Will X launch a token?","Official announcement only")[1],"other_definition")
if __name__=="__main__":unittest.main()
