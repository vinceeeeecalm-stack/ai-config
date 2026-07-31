import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_elections_lab.py"
spec = importlib.util.spec_from_file_location("polymarket_elections_lab", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)

class ElectionsLabTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_ungrouped_condition_is_not_independent_event_evidence(self):
        self.assertEqual(module.identity({"events": [], "_sampling_raw": {}}), (None, "unproven"))

    def test_strict_governor_excludes_primary(self):
        self.assertIsNone(module.strict_governor_race({"title": "Vermont Governor Primary Winner", "markets": []}))

if __name__ == "__main__": unittest.main()
