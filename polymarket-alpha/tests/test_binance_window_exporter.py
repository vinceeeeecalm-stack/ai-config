import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "binance_window_exporter.py"
spec = importlib.util.spec_from_file_location("binance_window_exporter", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def execution():
    return {
        "status": "verified", "commission_usd": 0.1, "commission_bps": 10,
        "slippage_bps": 5, "spread_bps": 2, "depth_1pct_usd": 10000, "fill_price": 1.0,
    }


class BinanceWindowExporterTests(unittest.TestCase):
    def test_only_completed_windows_and_real_friction_are_exported(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ledger = {
            "created_at": start.isoformat(), "updated_at": (start + timedelta(days=65)).isoformat(),
            "initial_capital_usd": 500, "live_orders_enabled": False, "private_api_used": False,
            "closed_trades": [{
                "paper_trade_id": "t1", "opened_at": (start + timedelta(days=1)).isoformat(),
                "closed_at": (start + timedelta(days=2)).isoformat(), "realized_pnl_usd": 5,
                "entry_execution": execution(), "exit_execution": execution(),
            }],
        }
        payload = module.export_windows(ledger, "a" * 64)
        self.assertEqual(len(payload["windows"]), 2)
        self.assertEqual(payload["windows"][0]["data_status"], "complete")
        self.assertEqual(payload["windows"][0]["closed_trades"], 1)
        self.assertEqual(payload["windows"][1]["closed_trades"], 0)
        self.assertTrue(payload["windows"][1]["friction_complete"])

    def test_boundary_trade_degrades_both_overlapping_windows(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ledger = {
            "created_at": start.isoformat(), "updated_at": (start + timedelta(days=61)).isoformat(),
            "initial_capital_usd": 500, "live_orders_enabled": False, "private_api_used": False,
            "closed_trades": [{
                "paper_trade_id": "carry", "opened_at": (start + timedelta(days=29)).isoformat(),
                "closed_at": (start + timedelta(days=31)).isoformat(), "realized_pnl_usd": 0,
                "entry_execution": execution(), "exit_execution": execution(),
            }],
        }
        payload = module.export_windows(ledger, "b" * 64)
        self.assertEqual([row["data_status"] for row in payload["windows"]], ["incomplete", "incomplete"])


if __name__ == "__main__":
    unittest.main()
