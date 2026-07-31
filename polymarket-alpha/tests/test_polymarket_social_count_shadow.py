import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/polymarket_social_count_shadow.py"
spec=importlib.util.spec_from_file_location("polymarket_social_count_shadow",SCRIPT);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module)


class SocialCountShadowTests(unittest.TestCase):
    def test_self_test(self):self.assertEqual(module.self_test()["status"],"pass")

    def test_capture_is_pre_event_only(self):
        start=datetime(2026,7,13,0,0,tzinfo=timezone.utc)
        self.assertEqual(module.capture_state(start-timedelta(minutes=30),start),"eligible")

    def test_event_start_blocks_backfill(self):
        start=datetime(2026,7,13,0,0,tzinfo=timezone.utc)
        self.assertEqual(module.capture_state(start,start),"forecast_window_missed")


if __name__=="__main__":unittest.main()
