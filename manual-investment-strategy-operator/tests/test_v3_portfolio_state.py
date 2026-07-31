import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from v3_portfolio_state import (  # noqa: E402
    PortfolioFieldObservationV2,
    PortfolioStateV2,
    current_zero_cash_baseline,
)


AS_OF = "2026-07-25T12:00:00+08:00"


def observation(
    key,
    value,
    *,
    as_of=AS_OF,
    source_kind="legacy_ledger",
    evidence_id="legacy-1",
):
    return PortfolioFieldObservationV2(
        key=key,
        value=value,
        as_of=as_of,
        source=f"{source_kind}.json",
        source_kind=source_kind,
        confidence=0.9,
        evidence_id=evidence_id,
    )


class PortfolioStateV2Test(unittest.TestCase):
    def test_current_override_prevents_ghost_balances(self):
        legacy = [
            observation("holdings.NIGHT.quantity", 1000, evidence_id="old-night"),
            observation("holdings.ENA.quantity", 500, evidence_id="old-ena"),
            observation("cash.crypto.USDT", 1240, evidence_id="old-usdt"),
            observation("cash.us_equity.USD", 468, evidence_id="old-usd"),
        ]
        state = PortfolioStateV2.resolve(
            state_id="portfolio-20260725",
            as_of=AS_OF,
            observations=[*legacy, *current_zero_cash_baseline(as_of=AS_OF)],
        )
        self.assertEqual(state.get("holdings.NIGHT.quantity"), 0)
        self.assertEqual(state.get("holdings.ENA.quantity"), 0)
        self.assertEqual(state.get("cash.crypto.USDT"), 0)
        self.assertEqual(state.get("cash.us_equity.USD"), 0)
        self.assertEqual(len(state.conflicts), 4)
        self.assertTrue(
            all(item.reason == "higher_source_precedence" for item in state.conflicts)
        )

    def test_user_confirmation_beats_newer_screenshot(self):
        state = PortfolioStateV2.resolve(
            state_id="portfolio-precedence",
            as_of=AS_OF,
            observations=[
                observation(
                    "holdings.SOL.quantity",
                    3,
                    as_of="2026-07-25T11:59:00+08:00",
                    source_kind="screenshot",
                    evidence_id="screenshot-newer",
                ),
                observation(
                    "holdings.SOL.quantity",
                    2,
                    as_of="2026-07-25T10:00:00+08:00",
                    source_kind="user_trade_confirmation",
                    evidence_id="user-confirmed",
                ),
            ],
        )
        self.assertEqual(state.get("holdings.SOL.quantity"), 2)
        self.assertEqual(
            state.field("holdings.SOL.quantity").evidence_id, "user-confirmed"
        )

    def test_newer_account_evidence_wins_at_same_precedence(self):
        state = PortfolioStateV2.resolve(
            state_id="portfolio-same-tier",
            as_of=AS_OF,
            observations=[
                observation(
                    "holdings.ETH.quantity",
                    1,
                    as_of="2026-07-25T09:00:00+08:00",
                    source_kind="account_export",
                    evidence_id="export-old",
                ),
                observation(
                    "holdings.ETH.quantity",
                    1.5,
                    as_of="2026-07-25T10:00:00+08:00",
                    source_kind="screenshot",
                    evidence_id="screenshot-new",
                ),
            ],
        )
        self.assertEqual(state.get("holdings.ETH.quantity"), 1.5)
        self.assertEqual(
            state.conflicts[0].reason, "newer_evidence_at_same_precedence"
        )

    def test_each_serialized_field_retains_full_provenance(self):
        state = PortfolioStateV2.resolve(
            state_id="portfolio-provenance",
            as_of=AS_OF,
            observations=[
                observation(
                    "cash.crypto.USDT",
                    0,
                    source_kind="current_override",
                    evidence_id="cash-zero",
                )
            ],
        )
        serialized = state.to_dict()["fields"]["cash.crypto.USDT"]
        self.assertEqual(
            set(serialized),
            {"value", "as_of", "source", "source_kind", "confidence", "evidence_id"},
        )

    def test_future_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "evidence_after_state_cutoff"):
            PortfolioStateV2.resolve(
                state_id="portfolio-future",
                as_of=AS_OF,
                observations=[
                    observation(
                        "cash.crypto.USDT",
                        1,
                        as_of="2026-07-25T12:01:00+08:00",
                    )
                ],
            )


if __name__ == "__main__":
    unittest.main()
