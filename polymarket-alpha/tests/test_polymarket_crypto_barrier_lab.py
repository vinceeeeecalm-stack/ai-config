import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_crypto_barrier_lab.py"
spec = importlib.util.spec_from_file_location("polymarket_crypto_barrier_lab", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)


class CryptoBarrierLabTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_money_suffix(self):
        self.assertEqual(module.money_value("$2.5K"), 2500)
        self.assertEqual(module.money_value("$1M"), 1_000_000)

    def test_month_sequence(self):
        self.assertEqual(module.month_sequence("2025-11", "2026-02"), ["2025-11", "2025-12", "2026-01", "2026-02"])

    def test_complete_sliced_months(self):
        rows = [
            {"status": "ok", "month": "2025-01", "tag_id": "235", "terminal": True},
            {"status": "ok", "month": "2025-01", "tag_id": "39", "terminal": True},
            {"status": "ok", "month": "2025-02", "tag_id": "235", "terminal": True},
        ]
        self.assertEqual(module.complete_sliced_months(rows), ["2025-01"])

    def test_first_passage_probability_is_monotonic(self):
        near = module.first_passage_probability(100, 105, 0.01, 24, "upper")
        far = module.first_passage_probability(100, 120, 0.01, 24, "upper")
        longer = module.first_passage_probability(100, 105, 0.01, 72, "upper")
        self.assertGreater(near, far)
        self.assertGreater(longer, near)

    def test_empirical_probability_uses_direction(self):
        upper = module.empirical_barrier_probability(100, 105, "upper", [1.10] * 180, [0.90] * 180)
        lower = module.empirical_barrier_probability(100, 95, "lower", [1.10] * 180, [0.90] * 180)
        self.assertGreater(upper, 0.99)
        self.assertGreater(lower, 0.99)


if __name__ == "__main__":
    unittest.main()
