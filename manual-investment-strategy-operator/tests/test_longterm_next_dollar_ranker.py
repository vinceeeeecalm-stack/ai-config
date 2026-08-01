import copy
import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "longterm_next_dollar_ranker.py"
)
SPEC = importlib.util.spec_from_file_location("longterm_next_dollar_ranker", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def candidate(symbol: str, asset_class: str, base: float) -> dict:
    return MODULE._fixture(symbol, asset_class, base)


class LongTermNextDollarRankerTest(unittest.TestCase):
    def test_zero_cash_keeps_research_winner_and_blocks_amount_only(self):
        result = MODULE.rank_longterm_next_dollar(
            [candidate("QQQ", "etf", 70), candidate("SOL", "crypto", 82)],
            investment_goal="aggressive_10x",
            market_regime="risk_on",
            deployable_cash=0,
            planned_amount=1000,
        )
        self.assertEqual(result["best_candidate"], "SOL")
        self.assertEqual(result["research_decision"], "preferred")
        self.assertEqual(result["execution_decision"], "no_deploy_cash")
        self.assertEqual(result["executable_amount"], 0)

    def test_ranking_is_data_driven_not_fixed_crypto_role(self):
        pool = [candidate("SOL", "crypto", 72), candidate("MSFT", "us_equity", 78)]
        first = MODULE.rank_longterm_next_dollar(
            pool,
            investment_goal="compound_15_25",
            market_regime="neutral",
            deployable_cash=1000,
            planned_amount=500,
        )
        changed = copy.deepcopy(pool)
        for field in MODULE.SCORE_FIELDS:
            changed[0][field] = 96
        second = MODULE.rank_longterm_next_dollar(
            changed,
            investment_goal="compound_15_25",
            market_regime="neutral",
            deployable_cash=1000,
            planned_amount=500,
        )
        self.assertEqual(first["best_candidate"], "MSFT")
        self.assertEqual(second["best_candidate"], "SOL")
        self.assertFalse(second["fixed_asset_roles_used"])

    def test_risk_off_can_correctly_choose_cash(self):
        result = MODULE.rank_longterm_next_dollar(
            [candidate("QQQ", "etf", 55), candidate("BTC", "crypto", 58)],
            investment_goal="capital_preservation",
            market_regime="risk_off",
            deployable_cash=1000,
            planned_amount=1000,
        )
        self.assertEqual(result["best_candidate"], "CASH")
        self.assertEqual(result["current_direct_decision"], "do_not_enter_now")
        self.assertEqual(result["executable_amount"], 0)

    def test_pool_can_include_holdings_stocks_etfs_and_crypto(self):
        pool = [
            candidate("ETH", "crypto", 70),
            candidate("MSFT", "us_equity", 73),
            candidate("QQQ", "etf", 72),
            candidate("SOL", "crypto", 75),
        ]
        result = MODULE.rank_longterm_next_dollar(
            pool,
            investment_goal="aggressive_10x",
            market_regime="neutral",
            deployable_cash=500,
            planned_amount=500,
        )
        asset_classes = {item["asset_class"] for item in result["candidate_pool"]}
        self.assertTrue({"crypto", "us_equity", "etf", "cash"}.issubset(asset_classes))
        self.assertLessEqual(len(result["runners_up_rejections"]), 2)
        self.assertFalse(result["live_orders_enabled"])


if __name__ == "__main__":
    unittest.main()
