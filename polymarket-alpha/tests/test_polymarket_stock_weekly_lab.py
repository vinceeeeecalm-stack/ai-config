import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_stock_weekly_lab.py"
spec = importlib.util.spec_from_file_location("polymarket_stock_weekly_lab", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class StockWeeklyLabTests(unittest.TestCase):
    def test_self_test(self):
        result = module.self_test()
        self.assertEqual(result["status"], "pass")
        self.assertIs(result["live_orders_enabled"], False)

    def test_parser_rejects_non_weekly_contract(self):
        self.assertIsNone(module.parse_contract("Will Apple close above $320?"))


if __name__ == "__main__":
    unittest.main()
