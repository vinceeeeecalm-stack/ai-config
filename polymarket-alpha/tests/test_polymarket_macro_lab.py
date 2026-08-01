import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_macro_lab.py"
spec = importlib.util.spec_from_file_location("polymarket_macro_lab", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)

class MacroLabTests(unittest.TestCase):
    def test_self_test(self): self.assertEqual(module.self_test()["status"], "pass")
    def test_thresholds_share_one_derived_event(self):
        a = module.event_identity({"_sampling_raw": {}}, "policy_rate", "United States", "2026", "official_policy_decision")
        b = module.event_identity({"_sampling_raw": {}}, "policy_rate", "United States", "2026", "official_policy_decision")
        self.assertEqual(a, b)
    def test_unspecified_vintage_is_ambiguous(self):
        self.assertTrue(module.diagnostics("US GDP from the BEA")["high_rule_ambiguity"])

if __name__ == "__main__": unittest.main()
