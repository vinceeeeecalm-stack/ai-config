import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "scripts/polymarket_basketball_model.py"
S = importlib.util.spec_from_file_location("basketball_model", P)
M = importlib.util.module_from_spec(S); assert S.loader; S.loader.exec_module(M)


class BasketballModelTests(unittest.TestCase):
    def test_self(self):
        self.assertEqual(M.self_test()["status"], "pass")

    def test_same_date_split_unit(self):
        values = ["2026-01-01", "2026-01-01", "2026-01-02"]
        self.assertEqual(len(set(values)), 2)

    def test_wnba_full_name_alias(self):
        self.assertEqual(M.normalize("Golden State Valkyries"), "valkyries")
        self.assertEqual(M.normalize("PortlandFire"), "fire")


if __name__ == "__main__":
    unittest.main()
