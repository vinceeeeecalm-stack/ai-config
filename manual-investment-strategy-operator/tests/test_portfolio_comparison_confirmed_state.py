import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

portfolio_comparison_snapshot = importlib.import_module(
    "portfolio_comparison_snapshot"
)


class PortfolioComparisonConfirmedStateTest(unittest.TestCase):
    def test_confirmed_account_state_supersedes_current_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ledger_path = root / "portfolio_ledger.json"
            overrides_path = root / "current_position_overrides.json"
            states_path = root / "user_confirmed_account_states.jsonl"

            ledger_path.write_text(
                json.dumps(
                    {
                        "holdings": [
                            {
                                "symbol": "CRCL",
                                "quantity": 43,
                                "account_rail": "us_equity_rail",
                                "bucket": "US equity",
                                "current_price": 64.31,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            overrides_path.write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-19T13:10:00+08:00",
                        "holdings": {"CRCL": {"quantity": 43}},
                        "cash_rails": {"us_equity_cash_usd": 0},
                    }
                ),
                encoding="utf-8",
            )
            states_path.write_text(
                json.dumps(
                    {
                        "schema_version": "UserConfirmedAccountStateV1",
                        "id": "confirmed-crcl-one",
                        "rail": "us_equity",
                        "payload": {
                            "schema_version": "UserConfirmedAccountStateV1",
                            "rail": "us_equity",
                            "as_of": "2026-07-29T15:43:53.591Z",
                            "settled_cash_usd": 0,
                            "holdings": {"CRCL": 1},
                            "source": "dialogue",
                        },
                        "status": "confirmed",
                        "confirmed_at": "2026-07-29T15:43:53.660Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with (
                patch.object(
                    portfolio_comparison_snapshot,
                    "LEDGER_PATH",
                    ledger_path,
                ),
                patch.object(
                    portfolio_comparison_snapshot,
                    "OVERRIDES_PATH",
                    overrides_path,
                ),
                patch.object(
                    portfolio_comparison_snapshot,
                    "USER_CONFIRMED_STATES_PATH",
                    states_path,
                ),
            ):
                _, equity, cash, _, _, _, portfolio_state = (
                    portfolio_comparison_snapshot.build_holdings_from_ledger()
                )

            self.assertEqual(equity["CRCL"]["quantity"], 1)
            self.assertEqual(cash, 0)
            self.assertEqual(
                portfolio_state.field(
                    "holdings.CRCL.quantity"
                ).source_kind,
                "user_trade_confirmation",
            )


if __name__ == "__main__":
    unittest.main()
