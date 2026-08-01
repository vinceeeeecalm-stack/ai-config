import sys
import unittest
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path


ACTIVE_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "active-alpha-paper-monitor"
    / "scripts"
)
sys.path.insert(0, str(ACTIVE_SCRIPTS))

from risk_adjusted_path_quality import (  # noqa: E402
    apply_cross_sectional_adjustments,
    calculate_risk_adjusted_path,
    calculate_window_metrics,
)


START = 1_700_000_000


def rows(growth, count=140, start_price=100.0):
    price = start_price
    result = []
    for idx in range(count):
        price *= growth
        result.append(
            {
                "ts": START + idx * 86_400,
                "close": price,
                "is_closed": True,
            }
        )
    return result


class RiskAdjustedPathQualityTest(unittest.TestCase):
    def test_open_bar_is_excluded(self):
        asset = rows(1.002)
        benchmark = rows(1.001)
        asset.append(
            {
                "ts": START + 141 * 86_400,
                "close": 1_000_000,
                "is_closed": False,
            }
        )
        path = calculate_risk_adjusted_path(
            asset,
            benchmark,
            asset_class="us_equity",
            lane="trend_continuation",
            benchmark="SPY",
            risk_free_annual_pct=4.0,
            risk_free_evidence_id="rf",
        )
        self.assertLess(
            path["windows"]["daily_20"]["total_return_pct"],
            10,
        )

    def test_zero_volatility_returns_null_not_infinite(self):
        flat = rows(1.0)
        metric = calculate_window_metrics(
            flat,
            rows(1.001),
            window=60,
            min_observations=50,
            annualization=252,
            risk_free_annual_pct=0.0,
        )
        self.assertIsNone(metric["sharpe"])
        self.assertEqual(metric["status"], "zero_or_invalid_volatility")

    def test_us_sharpe_uses_252_and_converts_annual_risk_free(self):
        returns = [0.01, -0.004, 0.006, 0.002, -0.001] * 12
        prices = [100.0]
        for value in returns:
            prices.append(prices[-1] * (1 + value))
        asset = [
            {"ts": START + idx * 86_400, "close": value, "is_closed": True}
            for idx, value in enumerate(prices)
        ]
        metric = calculate_window_metrics(
            asset,
            rows(1.001, count=len(asset)),
            window=60,
            min_observations=50,
            annualization=252,
            risk_free_annual_pct=5.0,
        )
        rf_period = (1.05 ** (1 / 252)) - 1
        excess = [value - rf_period for value in returns]
        expected = (
            statistics.mean(excess)
            / statistics.stdev(excess)
            * math.sqrt(252)
        )
        self.assertAlmostEqual(metric["sharpe"], expected, places=5)
        self.assertEqual(metric["annualization_factor"], 252)

    def test_crypto_daily_and_4h_annualization(self):
        crypto = calculate_risk_adjusted_path(
            rows(1.002),
            rows(1.001),
            asset_class="crypto",
            lane="trend_continuation",
            benchmark="BTCUSDT",
            risk_free_evidence_id="rf",
            asset_4h_rows=[
                {
                    "ts": START + idx * 14_400,
                    "close": 100 * (1.001 ** idx),
                    "is_closed": True,
                }
                for idx in range(140)
            ],
            benchmark_4h_rows=[
                {
                    "ts": START + idx * 14_400,
                    "close": 100 * (1.0005 ** idx),
                    "is_closed": True,
                }
                for idx in range(140)
            ],
        )
        self.assertEqual(crypto["windows"]["daily_60"]["annualization_factor"], 365)
        self.assertEqual(
            crypto["windows"]["crypto_4h_42"]["annualization_factor"],
            2190,
        )

    def test_information_ratio_relative_strength_sortino_and_drawdown(self):
        asset = rows(1.002)
        asset[-10]["close"] *= 0.8
        metric = calculate_window_metrics(
            asset,
            rows(1.001),
            window=60,
            min_observations=50,
            annualization=252,
            risk_free_annual_pct=0,
        )
        self.assertIsNotNone(metric["information_ratio"])
        self.assertIsNotNone(metric["relative_strength_pct"])
        self.assertIsNotNone(metric["sortino"])
        self.assertLess(metric["max_drawdown_pct"], 0)

    def test_missing_benchmark_degrades_ir_but_keeps_sharpe(self):
        varied = [100.0]
        for index in range(140):
            varied.append(varied[-1] * (1.004 if index % 2 else 0.999))
        asset = [
            {"ts": START + idx * 86_400, "close": value, "is_closed": True}
            for idx, value in enumerate(varied)
        ]
        metric = calculate_window_metrics(
            asset,
            [],
            window=60,
            min_observations=50,
            annualization=252,
            risk_free_annual_pct=0,
        )
        self.assertIsNotNone(metric["sharpe"])
        self.assertIsNone(metric["information_ratio"])

    def test_insufficient_sample_is_explicit(self):
        metric = calculate_window_metrics(
            rows(1.002, count=10),
            rows(1.001, count=10),
            window=20,
            min_observations=18,
            annualization=252,
            risk_free_annual_pct=0,
        )
        self.assertEqual(metric["status"], "insufficient_sample")

    def test_adjusted_close_is_used_for_split_continuity(self):
        split_rows = rows(1.001)
        split_rows[-1]["close"] /= 2
        split_rows[-1]["adjusted_close"] = split_rows[-2]["close"] * 1.001
        metric = calculate_window_metrics(
            split_rows,
            rows(1.001),
            window=20,
            min_observations=18,
            annualization=252,
            risk_free_annual_pct=0,
        )
        self.assertGreater(metric["total_return_pct"], 0)

    def test_future_and_stale_bars_are_flagged(self):
        cutoff = datetime.fromtimestamp(
            START + 140 * 86_400,
            tz=timezone.utc,
        )
        stale = rows(1.001, count=130)
        stale.append(
            {
                "ts": int(cutoff.timestamp()) + 86_400,
                "close": 999,
                "is_closed": True,
            }
        )
        path = calculate_risk_adjusted_path(
            stale,
            rows(1.001),
            asset_class="us_equity",
            lane="trend_continuation",
            benchmark="SPY",
            risk_free_evidence_id="rf",
            cutoff_at=cutoff,
        )
        self.assertIn("future_daily_bars_excluded", path["flags"])
        self.assertIn("stale_closed_daily_data", path["flags"])

    def test_extreme_return_remains_finite(self):
        asset = rows(1.001)
        asset[-1]["close"] *= 100
        metric = calculate_window_metrics(
            asset,
            rows(1.001),
            window=20,
            min_observations=18,
            annualization=252,
            risk_free_annual_pct=0,
        )
        self.assertTrue(math.isfinite(metric["sharpe"]))
        self.assertTrue(math.isfinite(metric["max_drawdown_pct"]))

    def test_trend_adjustment_is_bounded(self):
        candidates = []
        for growth in (0.999, 1.001, 1.004, 1.007):
            candidates.append(
                {
                    "base": 50,
                    "risk_adjusted_path": calculate_risk_adjusted_path(
                        rows(growth),
                        rows(1.001),
                        asset_class="us_equity",
                        lane="trend_continuation",
                        benchmark="SPY",
                        risk_free_evidence_id="rf",
                    ),
                }
            )
        apply_cross_sectional_adjustments(candidates, base_score_field="base")
        self.assertTrue(
            all(
                abs(item["risk_adjusted_path"]["ranking_adjustment_points"])
                <= 7.5
                for item in candidates
            )
        )

    def test_short_sharpe_spike_caps_quality_and_cannot_auto_upgrade(self):
        candidates = []
        for score in (20, 50, 80):
            candidates.append(
                {
                    "base": 50,
                    "risk_adjusted_path": {
                        "lane": "trend_continuation",
                        "persistence_label": "short_spike_not_persistent",
                        "windows": {
                            "daily_20": {"sharpe": 5 + score / 100},
                            "daily_60": {
                                "sharpe": 0.5,
                                "relative_strength_pct": score,
                                "sortino": 0.8,
                                "max_drawdown_pct": -20 + score / 10,
                            },
                        },
                        "flags": [],
                    },
                }
            )
        apply_cross_sectional_adjustments(candidates, base_score_field="base")
        self.assertTrue(
            all(
                item["risk_adjusted_path"]["quality_score"] <= 60
                for item in candidates
            )
        )
        self.assertTrue(
            all(item["risk_adjusted_path"]["ranking_adjustment_points"] <= 1.5 for item in candidates)
        )

    def test_value_repair_cannot_adjust_before_value_catalyst_gate(self):
        candidates = []
        for growth in (0.995, 1.0, 1.005):
            candidates.append(
                {
                    "base": 50,
                    "risk_adjusted_path": calculate_risk_adjusted_path(
                        rows(growth),
                        rows(1.001),
                        asset_class="us_equity",
                        lane="value_repair",
                        benchmark="SPY",
                        risk_free_evidence_id="rf",
                        value_catalyst_gate_status="pending",
                    ),
                }
            )
        apply_cross_sectional_adjustments(candidates, base_score_field="base")
        self.assertTrue(
            all(
                item["risk_adjusted_path"]["ranking_adjustment_points"] == 0
                for item in candidates
            )
        )

    def test_high_20d_sharpe_is_not_a_probability(self):
        path = calculate_risk_adjusted_path(
            rows(1.01),
            rows(1.001),
            asset_class="crypto",
            lane="trend_continuation",
            benchmark="BTCUSDT",
            risk_free_evidence_id="rf",
        )
        self.assertTrue(path["probability_mapping_forbidden"])
        self.assertEqual(path["live_gate_effect"], "none_until_promotion")


if __name__ == "__main__":
    unittest.main()
