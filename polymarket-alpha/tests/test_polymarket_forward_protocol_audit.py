import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_forward_protocol_audit.py"
spec = importlib.util.spec_from_file_location("polymarket_forward_protocol_audit", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class ForwardProtocolAuditTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_unsafe_forecast_flags_fail(self):
        row = {
            "created_at": "2026-07-12T14:00:00+00:00", "cutoff_at": "2026-07-13T13:29:00+00:00",
            "research_only": True, "not_eligible_for_paper_entry": True,
            "live_orders_enabled": True, "private_api_used": False,
        }
        self.assertIn("unsafe_forecast_flags", module.validate_forecast("stock_weekly", row))

    def test_historical_miss_is_not_new_again(self):
        miss = {"domain": "social_count", "tracking_id": "t1", "reason": "forecast_window_missed"}
        self.assertEqual(module.newly_missed([miss], [miss]), [])

    def test_unseen_miss_is_new(self):
        miss = {"domain": "football", "event_id": "e1", "reason": "forecast_window_missed"}
        self.assertEqual(module.newly_missed([miss], []), [miss])

    def test_legacy_miss_merges_into_detailed_window(self):
        legacy = {"domain": "football", "event_id": "e1", "reason": "forecast_window_missed"}
        detailed = {**legacy, "cutoff_at": "2026-07-13T12:00:00+00:00"}
        self.assertEqual(module.normalize_missed_rows([legacy, detailed]), [detailed])


if __name__ == "__main__":
    unittest.main()
