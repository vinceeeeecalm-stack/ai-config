import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_weather_v2_feasibility.py"
spec = importlib.util.spec_from_file_location("polymarket_weather_v2_feasibility", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class WeatherV2FeasibilityTests(unittest.TestCase):
    def test_selection_uses_development_metric(self):
        candidates = [
            {"gamma": 1, "development": {"market_minus_candidate_brier": {"mean": 0.01}}, "final": {"mean": -1}},
            {"gamma": 2, "development": {"market_minus_candidate_brier": {"mean": 0.00}}, "final": {"mean": 99}},
        ]
        self.assertEqual(module.select_development_only(candidates)["gamma"], 1)

    def test_pool_normalizes(self):
        row = {"buckets": [(None, 69), (70, 71), (72, None)], "mean_forecast_high_f": 71, "market_probabilities": [.2, .5, .3]}
        result = module.pooled_probabilities(row, {"bias_f": 0, "sigma_f": 2}, .8, .1)
        self.assertAlmostEqual(sum(result), 1.0)


if __name__ == "__main__":
    unittest.main()
