import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from v3_dca_contributions import (  # noqa: E402
    DCAContributionV2,
    append_dca_contribution,
    observations_from_dca_contributions,
)
from v3_portfolio_state import resolve_portfolio_state_from_files  # noqa: E402


def contribution_payload(**overrides):
    payload = {
        "schema_version": "DCAContributionV2",
        "contribution_id": "contribution-001",
        "source_transfer_id": "transfer-001",
        "source": "self_operated_store_realized_profit",
        "source_profit_month": "2026-10",
        "store_closing_id": "close-2026-10",
        "rail": "crypto",
        "amount_cny": 1000.0,
        "destination_amount": 140.0,
        "destination_currency": "USDT",
        "fx_rate_cny_per_destination_unit": 7.142857,
        "post_transfer_cash": 140.0,
        "transferred_at": "2026-11-01T09:00:00+08:00",
        "settled_at": "2026-11-01T09:05:00+08:00",
        "evidence_ref": "account-export:deposit-001",
        "notes": "",
        "human_confirmed": True,
    }
    payload.update(overrides)
    return payload


class DCAContributionTests(unittest.TestCase):
    def test_append_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "contributions.jsonl"
            append_dca_contribution(ledger, contribution_payload())
            with self.assertRaisesRegex(ValueError, "contribution_id:duplicate"):
                append_dca_contribution(ledger, contribution_payload())
            self.assertEqual(
                len(ledger.read_text(encoding="utf-8").splitlines()), 1
            )

    def test_rail_currency_must_match(self):
        with self.assertRaisesRegex(
            ValueError, "destination_currency:must_match_rail"
        ):
            DCAContributionV2.from_payload(
                contribution_payload(
                    rail="us_equity", destination_currency="USDT"
                )
            )

    def test_contribution_updates_only_destination_cash_rail(self):
        record = DCAContributionV2.from_payload(contribution_payload())
        observations = observations_from_dca_contributions(
            [record], source="dca_contributions.jsonl"
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].key, "cash.crypto.USDT")
        self.assertEqual(observations[0].value, 140.0)

    def test_contribution_overrides_zero_cash_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overrides = root / "overrides.json"
            legacy = root / "legacy.json"
            ledger = root / "contributions.jsonl"
            overrides.write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-20T08:00:00+08:00",
                        "holdings": {},
                        "cash_rails": {
                            "crypto_usdt_available": 0,
                            "us_equity_cash_usd": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            legacy.write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-01T08:00:00+08:00",
                        "holdings": [],
                        "cash_rails": {},
                    }
                ),
                encoding="utf-8",
            )
            append_dca_contribution(ledger, contribution_payload())
            state = resolve_portfolio_state_from_files(
                overrides_path=overrides,
                legacy_ledger_path=legacy,
                dca_contributions_path=ledger,
            )
            self.assertEqual(state.get("cash.crypto.USDT"), 140.0)
            self.assertEqual(state.get("cash.us_equity.USD"), 0)


if __name__ == "__main__":
    unittest.main()
