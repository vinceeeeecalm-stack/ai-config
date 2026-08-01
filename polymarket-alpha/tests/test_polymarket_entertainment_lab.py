import importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/"scripts/polymarket_entertainment_lab.py";S=importlib.util.spec_from_file_location("ent",P);M=importlib.util.module_from_spec(S);assert S.loader;S.loader.exec_module(M)
class EntertainmentTests(unittest.TestCase):
 def test_self(self):self.assertEqual(M.self_test()["status"],"pass")
 def test_boxoffice(self):self.assertEqual(M.event_type("Movie opening weekend box office above $50m?"),"box_office")
if __name__=="__main__":unittest.main()
