import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ACTIVE_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "active-alpha-paper-monitor"
    / "scripts"
)
sys.path.insert(0, str(ACTIVE_SCRIPTS))

from fast_candidate_funnel import (  # noqa: E402
    DiscoveryCandidateV1,
    benchmark_fixture,
    committee_requirement,
    historical_path_comparison,
    rank_discovery_candidates,
    run_fast_funnel,
)
import impulse_capture_scanner as crypto_scanner  # noqa: E402


def discovery_candidate(index: int) -> DiscoveryCandidateV1:
    return DiscoveryCandidateV1(
        symbol=f"ASSET{index}USDT",
        asset_class="crypto",
        market_time="2026-08-01T00:00:00Z",
        current_price=1 + index,
        turnover=1_000_000 + index,
        relative_volume=1 + index / 10,
        trade_count=10_000 + index,
        vwap=1 + index,
        spread_bps=4,
        depth_bid_usd=100_000,
        depth_ask_usd=100_000,
        anchor_state={"market": "supportive", "BTC": 0.2, "ETH": 0.3},
        confirmed_catalyst_present=False,
        discovery_score=float(index),
        ranking_reason=("fixture_momentum",),
        return_1m_pct=0.1,
        return_5m_pct=0.2,
        return_15m_pct=0.3,
    )


class FastCandidateFunnelTest(unittest.TestCase):
    def test_history_first_comparison_is_deterministic_and_non_overlapping(self):
        rows = []
        price_value = 100.0
        for index in range(500):
            price_value *= 1.002 if index % 7 else 0.997
            rows.append(
                {
                    "ts": index,
                    "close": price_value,
                    "high": price_value * 1.02,
                    "low": price_value * 0.99,
                    "is_closed": True,
                }
            )
        first = historical_path_comparison(
            rows,
            target_return_pct=5.0,
            stop_loss_pct=3.0,
            horizon_bars=5,
            evidence_id="fixture-history",
        )
        second = historical_path_comparison(
            rows,
            target_return_pct=5.0,
            stop_loss_pct=3.0,
            horizon_bars=5,
            evidence_id="fixture-history",
        )
        self.assertEqual(first, second)
        self.assertGreaterEqual(first["sample_size"], 30)
        self.assertTrue(first["non_overlapping_observations"])
        self.assertTrue(first["untouched_holdout"])

    def test_thirty_crypto_candidates_return_top3_inside_budget(self):
        result = rank_discovery_candidates(
            [discovery_candidate(index) for index in range(30)],
            top_n=3,
        )
        self.assertEqual(len(result["top_candidates"]), 3)
        self.assertTrue(result["within_budget"])
        self.assertLessEqual(result["elapsed_seconds"], 15)

    def test_thirty_crypto_benchmark_includes_completed_top1_card_budgets(self):
        result = benchmark_fixture(iterations=30, candidate_count=30)
        self.assertTrue(result["passed"])
        self.assertEqual(result["validated_top3_count"], 3)
        self.assertEqual(result["ranked_historical_comparison_count"], 3)
        self.assertEqual(result["deep_research_top1_count"], 1)
        self.assertTrue(result["top1_card_complete"])
        self.assertEqual(
            result["top1_decision_card"]["completion_status"],
            "complete_with_explicit_blockers",
        )
        for phase, elapsed in result["phase_p95_seconds"].items():
            self.assertLessEqual(elapsed, result["phase_budget_seconds"][phase])

    def test_only_top3_get_validation_and_only_top1_get_deep_research(self):
        validated = []
        deep = []

        def validate(item):
            validated.append(item["symbol"])
            return {"symbol": item["symbol"], "setup_quality_score": item["discovery_score"]}

        def research(item):
            deep.append(item["symbol"])
            return {"symbol": item["symbol"], "financials_checked": True}

        result = run_fast_funnel(
            [discovery_candidate(index) for index in range(30)],
            validate_candidate=validate,
            deep_research_candidate=research,
        )
        self.assertEqual(len(validated), 3)
        self.assertEqual(len(deep), 1)
        self.assertEqual(deep, result["deep_research_symbols"])
        comparison = result["ranked_historical_comparison"]
        self.assertEqual(len(comparison["rows"]), 3)
        self.assertTrue(comparison["history_simulation_is_not_forecast_probability"])

    def test_discovery_rejects_probability_cash_and_execution_leakage(self):
        payload = discovery_candidate(1).to_dict()
        payload["forecast_probability_pct"] = 80
        result = rank_discovery_candidates([payload])
        self.assertEqual(result["top_candidates"], [])
        self.assertIn(
            "discovery_contract_forbidden_fields",
            result["blocked_candidates"][0]["hard_rejection_reasons"][0],
        )

    def test_committee_is_tiered_after_discovery(self):
        self.assertEqual(
            committee_requirement(phase="discovery")["required_role_count"], 0
        )
        self.assertEqual(
            committee_requirement(
                phase="validation", request_mode="intraday_scalp"
            )["required_role_count"],
            2,
        )
        self.assertEqual(
            committee_requirement(
                phase="validation", request_mode="tactical_1_7d"
            )["required_role_count"],
            4,
        )
        self.assertGreaterEqual(
            committee_requirement(
                phase="validation", request_mode="event_trade_1_3w", binary_event=True
            )["required_role_count"],
            6,
        )

    def test_verified_top_of_book_is_not_misclassified_as_missing(self):
        signal = {
            "symbol": "BTCUSDT",
            "data_quality": "verified_top_of_book",
            "current_price": 100.0,
            "quote_volume_15m": 1_000_000,
            "volume_multiple_5m_vs_median": 2.0,
            "trade_count_15m": 1000,
            "vwap_15m": 99.8,
            "spread_bps": 2.0,
            "top_bid_depth_usd": 100_000,
            "top_ask_depth_usd": 90_000,
            "anchor_state": "supportive",
            "anchor_changes_pct": {"BTCUSDT": 0.1, "ETHUSDT": 0.2},
            "impulse_score_points": 55,
            "why_now": ["fixture"],
            "return_1m_pct": 0.1,
            "return_5m_pct": 0.2,
            "return_15m_pct": 0.3,
        }
        candidate = crypto_scanner.to_discovery_candidate(
            signal,
            datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(candidate.hard_rejection_reasons, ())

    def test_low_beta_gold_tokens_are_not_fast_alpha_candidates(self):
        self.assertFalse(crypto_scanner.is_dynamic_scan_candidate("PAXGUSDT"))
        self.assertFalse(crypto_scanner.is_dynamic_scan_candidate("XAUTUSDT"))
        self.assertTrue(crypto_scanner.is_dynamic_scan_candidate("TAOUSDT"))

    def test_active_impulse_stage_outranks_higher_scored_no_impulse(self):
        active = {
            "symbol": "XRPUSDT",
            "stage": "early_watch",
            "impulse_score_points": 40,
            "volume_multiple_5m_vs_median": 3,
        }
        inactive = {
            "symbol": "BANKUSDT",
            "stage": "no_current_impulse",
            "impulse_score_points": 90,
            "volume_multiple_5m_vs_median": 10,
        }
        ranked = sorted(
            [inactive, active],
            key=crypto_scanner.signal_ranking_key,
            reverse=True,
        )
        self.assertEqual(ranked[0]["symbol"], "XRPUSDT")

    def test_crypto_intraday_market_plan_has_same_day_exit_and_two_targets(self):
        captured = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        signal = {
            "mid": 100.0,
            "current_price": 100.0,
            "resistance": 101.0,
            "support": 98.0,
            "opening_range_high": 100.5,
            "opening_range_low": 98.5,
            "vwap_15m": 99.5,
            "volume_multiple_5m_vs_median": 2.0,
            "spread_bps": 3.0,
            "depth_1pct_bid_usd": 1_000_000,
            "depth_1pct_ask_usd": 900_000,
            "anchor_state": "supportive",
            "stage": "trigger",
        }
        plan = crypto_scanner.build_crypto_intraday_market_plan(signal, captured)
        self.assertGreater(plan["target_2_price"], plan["target_1_price"])
        self.assertGreater(plan["target_1_price"], plan["entry_trigger_price"])
        self.assertFalse(plan["overnight_allowed"])
        self.assertTrue(plan["latest_close_at"].startswith("2026-08-01"))

    def test_top1_decision_card_completes_with_explicit_blockers(self):
        captured = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        signal = {
            "symbol": "UNIUSDT",
            "impulse_score_points": 68.38,
            "stage": "early_watch",
        }
        card = crypto_scanner.build_top1_decision_card(
            signal,
            captured_at=captured,
            request_mode="intraday_scalp",
            intraday_market_plan={"live_orders_enabled": False},
        )
        self.assertEqual(card["completion_status"], "complete_with_explicit_blockers")
        self.assertEqual(card["best_candidate"], "UNIUSDT")
        self.assertEqual(card["current_direct_decision"], "do_not_enter_now")
        self.assertEqual(card["execution_decision"], "no_deploy_evidence")
        self.assertEqual(card["executable_amount"], 0.0)
        self.assertIn(
            "official_event_or_protocol_fundamental_review_missing",
            card["missing_evidence"],
        )
        self.assertFalse(card["live_orders_enabled"])


if __name__ == "__main__":
    unittest.main()
