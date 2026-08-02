import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "impulse_capture_scanner.py"
SPEC = importlib.util.spec_from_file_location("impulse_profit_first_ranking", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def candidate(
    symbol,
    *,
    conservative_ev,
    expected_return,
    profit_factor,
    max_drawdown,
    setup_score,
    stage,
    sample_size=59,
    reward_risk=2.0,
    win_floor=10.0,
):
    return {
        "symbol": symbol,
        "stage": stage,
        "impulse_score_points": setup_score,
        "volume_multiple_5m_vs_median": 5.0,
        "historical_comparison": {
            "sample_size": sample_size,
            "conservative_expected_value_pct": conservative_ev,
            "expected_return_pct": expected_return,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_drawdown,
            "reward_risk_ratio": reward_risk,
            "out_of_sample_win_rate_interval_pct": [win_floor, 40.0],
        },
    }


class ValidatedProfitFirstRankingTests(unittest.TestCase):
    def test_near_miss_prefers_better_conservative_ev_over_stronger_impulse(self):
        stronger_impulse = candidate(
            "UNIUSDT",
            conservative_ev=-1.98,
            expected_return=-0.71,
            profit_factor=0.77,
            max_drawdown=-53.29,
            setup_score=92.5,
            stage="trigger",
        )
        better_profit_evidence = candidate(
            "AAVEUSDT",
            conservative_ev=-1.86,
            expected_return=-0.53,
            profit_factor=0.83,
            max_drawdown=-56.42,
            setup_score=70.0,
            stage="no_current_impulse",
        )

        ranked = sorted(
            [stronger_impulse, better_profit_evidence],
            key=M.validated_profit_ranking_key,
            reverse=True,
        )

        self.assertEqual(ranked[0]["symbol"], "AAVEUSDT")

    def test_qualified_positive_ev_beats_unqualified_trigger(self):
        qualified = candidate(
            "QUALIFIEDUSDT",
            conservative_ev=0.35,
            expected_return=1.1,
            profit_factor=1.2,
            max_drawdown=-12.0,
            setup_score=45.0,
            stage="no_current_impulse",
            sample_size=40,
            reward_risk=2.0,
        )
        unqualified = candidate(
            "TRIGGERUSDT",
            conservative_ev=-0.01,
            expected_return=2.0,
            profit_factor=1.4,
            max_drawdown=-8.0,
            setup_score=100.0,
            stage="trigger",
            sample_size=80,
            reward_risk=3.0,
        )

        ranked = sorted(
            [unqualified, qualified],
            key=M.validated_profit_ranking_key,
            reverse=True,
        )

        self.assertEqual(ranked[0]["symbol"], "QUALIFIEDUSDT")

    def test_same_input_is_deterministic_and_strategy_version_isolated(self):
        rows = [
            candidate(
                "BETAUSDT",
                conservative_ev=0.2,
                expected_return=0.8,
                profit_factor=1.1,
                max_drawdown=-10.0,
                setup_score=60.0,
                stage="pre_breakout",
            ),
            candidate(
                "ALPHAUSDT",
                conservative_ev=0.2,
                expected_return=0.8,
                profit_factor=1.1,
                max_drawdown=-10.0,
                setup_score=60.0,
                stage="pre_breakout",
            ),
        ]

        first = [
            row["symbol"]
            for row in sorted(rows, key=M.validated_profit_ranking_key, reverse=True)
        ]
        second = [
            row["symbol"]
            for row in sorted(rows, key=M.validated_profit_ranking_key, reverse=True)
        ]

        self.assertEqual(first, second)
        self.assertEqual(M.TACTICAL_STRATEGY_VERSION, "impulse-capture-tactical-v7")


if __name__ == "__main__":
    unittest.main()
