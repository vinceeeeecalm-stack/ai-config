import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_probability_lab.py"
spec = importlib.util.spec_from_file_location("polymarket_probability_lab", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class ProbabilityLabTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_linear_solver(self):
        result = module.solve_linear([[2.0, 0.0], [0.0, 4.0]], [4.0, 8.0])
        self.assertEqual(result, [2.0, 2.0])


if __name__ == "__main__":
    unittest.main()
