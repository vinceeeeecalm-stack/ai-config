import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/polymarket_weather_shadow.py"
spec=importlib.util.spec_from_file_location("polymarket_weather_shadow",SCRIPT);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module)

class WeatherShadowTests(unittest.TestCase):
    def test_self_test(self):self.assertEqual(module.self_test()["status"],"pass")

    def test_capture_only_inside_pre_event_window(self):
        cutoff=datetime(2026,7,13,5,0,tzinfo=timezone.utc)
        self.assertEqual(module.capture_state(cutoff-timedelta(minutes=30),cutoff),"eligible")

    def test_post_cutoff_backfill_is_missed(self):
        cutoff=datetime(2026,7,13,5,0,tzinfo=timezone.utc)
        self.assertEqual(module.capture_state(cutoff,cutoff),"forecast_window_missed")

if __name__=="__main__":unittest.main()
