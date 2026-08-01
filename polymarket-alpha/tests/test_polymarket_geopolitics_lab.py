import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_geopolitics_lab.py"
spec = importlib.util.spec_from_file_location("polymarket_geopolitics_lab", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)


class GeopoliticsLabTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_media_judgment_is_ambiguous(self):
        row = module.rule_diagnostics({"description": "Resolves on a consensus of credible reporting."})
        self.assertTrue(row["media_judgment_required"])
        self.assertTrue(row["high_rule_ambiguity"])


if __name__ == "__main__":
    unittest.main()
