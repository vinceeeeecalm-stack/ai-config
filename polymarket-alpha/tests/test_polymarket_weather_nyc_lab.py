import importlib.util
import unittest
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/polymarket_weather_nyc_lab.py"
spec=importlib.util.spec_from_file_location("polymarket_weather_nyc_lab",SCRIPT);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module)


class WeatherNYCLabTests(unittest.TestCase):
    def test_self_test(self):self.assertEqual(module.self_test()["status"],"pass")


if __name__=="__main__":unittest.main()
