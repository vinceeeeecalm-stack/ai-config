import importlib.util, unittest
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[1]/"scripts"/"polymarket_mentions_lab.py";spec=importlib.util.spec_from_file_location("mentions",SCRIPT);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module)
class MentionsTests(unittest.TestCase):
    def test_self_test(self): self.assertEqual(module.self_test()["status"],"pass")
    def test_subjects_not_pooled(self): self.assertNotEqual(module.subject("Trump speech"),module.subject("Biden speech"))
    def test_description_examples_do_not_assign_subject(self):
        self.assertEqual(module.subject("JPMorgan earnings call"), "Other")
if __name__=="__main__":unittest.main()
