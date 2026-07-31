import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from v3_execution_receipts import (  # noqa: E402
    ExecutionReceiptV2,
    append_execution_receipt,
    load_execution_receipts,
    observations_from_execution_receipts,
)
from v3_portfolio_state import resolve_portfolio_state_from_files  # noqa: E402


def receipt_payload(**overrides):
    payload = {
        "schema_version": "ExecutionReceiptV2",
        "receipt_id": "receipt-001",
        "source": "owner_dashboard_manual_confirmation",
        "recommendation_id": "recommendation-001",
        "rail": "crypto",
        "asset_class": "crypto",
        "symbol": "SOL",
        "side": "buy",
        "quantity": 2.0,
        "price": 150.0,
        "fee": 1.0,
        "fee_currency": "USDT",
        "executed_at": "2026-07-26T20:30:00+08:00",
        "post_trade_quantity": 12.0,
        "post_trade_cash": 699.0,
        "settlement_status": "settled",
        "settled_at": "2026-07-26T20:31:00+08:00",
        "evidence_ref": "account-export:trade-001",
        "notes": "",
        "human_confirmed": True,
    }
    payload.update(overrides)
    return payload


class ExecutionReceiptTests(unittest.TestCase):
    def test_append_is_immutable_and_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "receipts.jsonl"
            appended = append_execution_receipt(ledger, receipt_payload())
            self.assertEqual(appended.receipt_id, "receipt-001")
            original_text = ledger.read_text(encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "receipt_id:duplicate"):
                append_execution_receipt(ledger, receipt_payload())

            self.assertEqual(ledger.read_text(encoding="utf-8"), original_text)
            self.assertEqual(len(load_execution_receipts(ledger)), 1)

    def test_settled_receipt_updates_holding_and_correct_cash_rail(self):
        receipt = ExecutionReceiptV2.from_payload(receipt_payload())
        observations = observations_from_execution_receipts(
            [receipt], source="receipts.jsonl"
        )
        by_key = {item.key: item for item in observations}

        self.assertEqual(by_key["holdings.SOL.quantity"].value, 12.0)
        self.assertEqual(by_key["cash.crypto.USDT"].value, 699.0)
        self.assertNotIn("cash.us_equity.USD", by_key)

    def test_unsettled_sale_never_creates_available_cash_observation(self):
        receipt = ExecutionReceiptV2.from_payload(
            receipt_payload(
                side="sell",
                post_trade_quantity=8.0,
                post_trade_cash=None,
                settlement_status="unsettled",
                settled_at="",
            )
        )
        observations = observations_from_execution_receipts(
            [receipt], source="receipts.jsonl"
        )

        self.assertEqual(
            [item.key for item in observations], ["holdings.SOL.quantity"]
        )

    def test_human_confirmation_is_required(self):
        with self.assertRaisesRegex(ValueError, "human_confirmed:true_required"):
            ExecutionReceiptV2.from_payload(
                receipt_payload(human_confirmed=False)
            )

    def test_receipt_overrides_current_position_and_cash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overrides_path = root / "overrides.json"
            legacy_path = root / "legacy.json"
            ledger_path = root / "receipts.jsonl"
            overrides_path.write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-20T08:00:00+08:00",
                        "holdings": {"SOL": {"quantity": 10}},
                        "cash_rails": {
                            "crypto_usdt_available": 0,
                            "us_equity_cash_usd": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            legacy_path.write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-01T08:00:00+08:00",
                        "holdings": [{"symbol": "SOL", "quantity": 9}],
                        "cash_rails": {},
                    }
                ),
                encoding="utf-8",
            )
            append_execution_receipt(ledger_path, receipt_payload())

            state = resolve_portfolio_state_from_files(
                overrides_path=overrides_path,
                legacy_ledger_path=legacy_path,
                execution_receipts_path=ledger_path,
            )

            self.assertEqual(state.get("holdings.SOL.quantity"), 12.0)
            self.assertEqual(state.get("cash.crypto.USDT"), 699.0)
            self.assertEqual(
                state.field("holdings.SOL.quantity").source_kind,
                "user_trade_confirmation",
            )


if __name__ == "__main__":
    unittest.main()
