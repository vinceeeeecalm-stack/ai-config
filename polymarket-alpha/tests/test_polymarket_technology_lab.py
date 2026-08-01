import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "scripts/polymarket_technology_lab.py"
S = importlib.util.spec_from_file_location("technology", P)
M = importlib.util.module_from_spec(S); assert S.loader; S.loader.exec_module(M)


class TechnologyTests(unittest.TestCase):
    def test_self(self): self.assertEqual(M.self_test()["status"], "pass")
    def test_kpi_is_not_release(self): self.assertEqual(M.event_type("Will Amazon 2026 capex exceed $190B?"), "company_kpi")


if __name__ == "__main__": unittest.main()
