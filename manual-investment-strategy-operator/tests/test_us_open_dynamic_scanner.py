import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ACTIVE_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "active-alpha-paper-monitor"
    / "scripts"
)
sys.path.insert(0, str(ACTIVE_SCRIPTS))

import us_open_dynamic_scanner as scanner  # noqa: E402
from us_open_dynamic_scanner import build_candidate, compute_metrics  # noqa: E402


def closed_rows(count: int = 140) -> list[dict]:
    price = 100.0
    output = []
    for index in range(count):
        price *= 1.001
        output.append(
            {
                "ts": 1_700_000_000 + index * 86_400,
                "close": price,
                "adjusted_close": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "volume": 1_000_000,
                "is_closed": True,
            }
        )
    return output


class UsOpenDynamicScannerTests(unittest.TestCase):
    def test_action_price_uses_live_quote_but_path_uses_closed_bars(self) -> None:
        rows = closed_rows()
        quote = {
            "shortName": "TEST",
            "quoteType": "EQUITY",
            "regularMarketPrice": 125.0,
            "regularMarketTime": 1_800_000_000,
        }
        metrics = compute_metrics(
            "TEST",
            quote,
            rows=rows,
            benchmark_rows=rows,
            benchmark_symbol="SPY",
        )
        self.assertEqual(metrics["price"], 125.0)
        self.assertNotEqual(metrics["closed_reference_price"], 125.0)
        self.assertEqual(
            metrics["price_source"],
            "Yahoo screener regularMarketPrice",
        )

    def test_missing_live_quote_falls_back_to_last_closed_price(self) -> None:
        rows = closed_rows()
        metrics = compute_metrics(
            "TEST",
            {"shortName": "TEST", "quoteType": "EQUITY"},
            rows=rows,
            benchmark_rows=rows,
            benchmark_symbol="SPY",
        )
        self.assertEqual(metrics["price"], rows[-1]["close"])
        self.assertEqual(
            metrics["price_source"],
            "Yahoo closed daily chart fallback",
        )

    def test_large_fresh_quote_gap_blocks_mechanical_entry_levels(self) -> None:
        rows = closed_rows()
        metrics = compute_metrics(
            "TEST",
            {
                "shortName": "TEST",
                "quoteType": "EQUITY",
                "regularMarketPrice": rows[-1]["close"] * 1.12,
                "regularMarketTime": 1_800_000_000,
            },
            rows=rows,
            benchmark_rows=rows,
            benchmark_symbol="SPY",
        )
        candidate = build_candidate(metrics, {"day_gainers"}, 1, "", None)
        self.assertTrue(candidate["gap_repricing_guard"])
        self.assertIn("no entry", candidate["entry_zone"])
        self.assertIsNone(candidate["target_price"])
        self.assertIsNone(candidate["stop_loss"])

    def test_gap_candidate_null_target_renders_without_crash(self) -> None:
        rows = closed_rows()
        metrics = compute_metrics(
            "TEST",
            {
                "shortName": "TEST",
                "quoteType": "EQUITY",
                "regularMarketPrice": rows[-1]["close"] * 1.12,
                "regularMarketTime": 1_800_000_000,
            },
            rows=rows,
            benchmark_rows=rows,
            benchmark_symbol="SPY",
        )
        candidate = build_candidate(metrics, {"day_gainers"}, 1, "", None)
        candidate["open_scan_score_points"] = 70
        payload = {
            "created_at": "2026-08-01T00:00:00Z",
            "scan_status": "partial",
            "live_orders_enabled": False,
            "research_committee_degraded": True,
            "max_allowed_action": "watch",
            "candidates": [candidate],
        }
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.md"
            scanner.write_report(report, payload)
            self.assertIn("unavailable", report.read_text(encoding="utf-8"))

    def test_one_screener_failure_preserves_available_pool(self) -> None:
        health = scanner.scan_health(
            ["day_gainers", "most_actives"],
            {"day_gainers"},
            {"TEST": {}},
            [{"symbol": "TEST"}],
        )
        self.assertFalse(health["degraded_no_actionable_trade"])
        self.assertEqual(health["scan_status"], "partial")

    def test_one_spark_batch_failure_preserves_other_batches(self) -> None:
        symbols = [f"US{index:02d}" for index in range(12)]

        def fake_spark(batch, range_="5d", interval="1d", timeout=None):
            if "US04" in batch:
                raise RuntimeError("fixture_batch_failure")
            return {
                symbol: {
                    "closes": [10, 11],
                    "meta": {"instrumentType": "EQUITY"},
                    "price_as_of": "2026-07-31T20:00:01+00:00",
                }
                for symbol in batch
            }

        with mock.patch.object(scanner, "yahoo_spark", side_effect=fake_spark):
            output, errors = scanner.yahoo_spark_batched(
                symbols,
                batch_size=4,
                timeout=1,
            )
        self.assertEqual(len(output), 8)
        self.assertEqual(len(errors), 1)
        self.assertIn("US00", output)
        self.assertIn("US08", output)

    def test_historical_account_price_does_not_select_current_tactical_symbol(self) -> None:
        ledger = {
            "holdings": [
                {
                    "account_rail": "us_equity_rail",
                    "symbol": "OLD",
                    "quantity": 1,
                    "current_price": 1_000_000,
                    "bucket": "longterm",
                },
                {
                    "account_rail": "us_equity_rail",
                    "symbol": "TACT",
                    "quantity": 2,
                    "current_price": 1,
                    "bucket": "tactical",
                },
            ]
        }
        with mock.patch.object(scanner, "load_portfolio_ledger", return_value=(Path("ledger.json"), ledger)), mock.patch.object(
            scanner, "load_position_overrides", return_value={}
        ):
            snapshot = scanner.current_tactical_snapshot()
        self.assertEqual(snapshot["current_tactical_position"], "TACT")
        self.assertTrue(
            all(
                item["historical_account_price_used_for_current_ranking"] is False
                for item in snapshot["us_equity_holdings"]
            )
        )

    def test_intraday_mode_is_same_day_and_stale_quote_is_blocked(self) -> None:
        generated = datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc)
        candidate = {
            "price_as_of": "2026-08-01T17:58:00+00:00",
        }
        precheck = scanner.build_us_intraday_precheck(candidate, generated)
        self.assertEqual(precheck["status"], "blocked")
        self.assertIn("quote_age_over_60_seconds", precheck["blockers"])
        self.assertFalse(precheck["overnight_allowed"])
        self.assertEqual(
            datetime.fromisoformat(precheck["latest_close_at"]).date(),
            datetime.fromisoformat(candidate["price_as_of"]).date(),
        )

    def test_us_funnel_benchmark_includes_complete_top1_card(self) -> None:
        result = scanner.run_self_test(iterations=30, candidate_count=25)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["checks"]["all_phase_p95_within_budget"])
        self.assertEqual(
            result["top1_decision_card"]["completion_status"],
            "complete_with_explicit_blockers",
        )
        self.assertFalse(result["live_orders_enabled"])

    def test_live_flow_uses_batch_discovery_and_deep_charts_only_for_top3(self) -> None:
        quotes = [
            {
                "symbol": f"US{index:02d}",
                "shortName": f"US {index}",
                "quoteType": "EQUITY",
                "regularMarketPrice": 10.0 + index,
                "regularMarketTime": 1_785_528_001,
                "regularMarketChangePercent": float(index),
                "regularMarketVolume": 1_000_000 + index,
                "averageDailyVolume10Day": 500_000,
                "bid": 10.0 + index,
                "ask": 10.01 + index,
            }
            for index in range(25)
        ]
        spark = {
            item["symbol"]: {
                "closes": [10.0, 10.2, 10.4, 10.6, item["regularMarketPrice"]],
                "meta": {
                    "instrumentType": "EQUITY",
                    "regularMarketPrice": item["regularMarketPrice"],
                    "regularMarketTime": item["regularMarketTime"],
                },
                "price_as_of": "2026-07-31T20:00:01+00:00",
            }
            for item in quotes
        }
        chart_calls = []

        def fake_chart(symbol, range_="2mo", interval="1d"):
            chart_calls.append(symbol)
            return closed_rows()

        tactical = {
            "current_tactical_position": None,
            "deployable_tactical_position": None,
            "selection_source": "fixture",
            "selection_reason": "fixture",
            "protected_long_term_holdings": ["CRCL"],
            "us_equity_holdings": [],
            "ledger_path": None,
            "us_equity_cash_usd": 0.0,
        }
        argv = [
            "us_open_dynamic_scanner.py",
            "--screeners",
            "day_gainers",
            "--count",
            "25",
            "--top",
            "3",
            "--max-symbols",
            "25",
            "--request-mode",
            "intraday_scalp",
            "--json-only",
        ]
        with mock.patch.object(scanner, "yahoo_screener", return_value=quotes) as screener_mock, mock.patch.object(
            scanner, "yahoo_spark", return_value=spark
        ) as spark_mock, mock.patch.object(scanner, "yahoo_chart", side_effect=fake_chart), mock.patch.object(
            scanner, "yahoo_news", return_value=[]
        ), mock.patch.object(
            scanner, "current_tactical_snapshot", return_value=tactical
        ), mock.patch.object(sys, "argv", argv), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as stdout:
            scanner.main()
        payload = json.loads(stdout.getvalue())
        top3 = [item["symbol"] for item in payload["discovery_top3"]]
        candidate_chart_calls = [
            symbol for symbol in chart_calls if symbol not in {"SPY", "QQQ", "SMH", "^IRX"}
        ]
        self.assertEqual(set(candidate_chart_calls), set(top3))
        self.assertLessEqual(len(candidate_chart_calls), 3)
        self.assertTrue(payload["stage_timings"]["discovery"]["within_budget"])
        self.assertEqual(
            payload["top1_decision_card"]["completion_status"],
            "complete_with_explicit_blockers",
        )
        self.assertEqual(
            payload["top1_decision_card"]["current_direct_decision"],
            "do_not_enter_now",
        )
        self.assertFalse(payload["live_orders_enabled"])
        self.assertTrue(payload["runtime_contract_audit"]["passed"])
        self.assertLessEqual(
            screener_mock.call_args.kwargs["timeout"],
            scanner.DISCOVERY_SCREENER_TIMEOUT_SECONDS,
        )
        self.assertLessEqual(
            spark_mock.call_args.kwargs["timeout"],
            scanner.DISCOVERY_SPARK_TIMEOUT_SECONDS,
        )
        open_required = scanner.audit_runtime_contract(
            payload,
            require_open_session=True,
        )
        self.assertFalse(open_required["passed"])
        self.assertIn("open_live_session_required", open_required["errors"])

    def test_missing_spark_fallback_is_limited_to_one_bounded_wave(self) -> None:
        quotes = [
            {
                "symbol": f"US{index:02d}",
                "shortName": f"US {index}",
                "quoteType": "EQUITY",
                "regularMarketPrice": 10.0 + index,
                "regularMarketTime": 1_785_528_001,
                "regularMarketChangePercent": float(index),
                "regularMarketVolume": 1_000_000 + index,
                "averageDailyVolume10Day": 500_000,
            }
            for index in range(25)
        ]
        fallback_calls = []

        def fake_chart(symbol, range_="2mo", interval="1d", timeout=None):
            fallback_calls.append((symbol, timeout))
            return closed_rows(5)

        argv = [
            "us_open_dynamic_scanner.py",
            "--screeners",
            "day_gainers",
            "--count",
            "25",
            "--top",
            "3",
            "--max-symbols",
            "25",
            "--request-timeout",
            "12",
            "--discovery-only",
        ]
        with mock.patch.object(scanner, "yahoo_screener", return_value=quotes), mock.patch.object(
            scanner, "yahoo_spark", return_value={}
        ), mock.patch.object(
            scanner, "yahoo_chart", side_effect=fake_chart
        ), mock.patch.object(sys, "argv", argv), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as stdout:
            scanner.main()
        payload = json.loads(stdout.getvalue())
        self.assertLessEqual(
            len(fallback_calls), scanner.DISCOVERY_FALLBACK_MAX_SYMBOLS
        )
        self.assertTrue(
            all(
                timeout <= scanner.DISCOVERY_FALLBACK_TIMEOUT_SECONDS
                for _, timeout in fallback_calls
            )
        )
        self.assertEqual(len(payload["top_candidates"]), 3)
        self.assertTrue(payload["within_budget"])


if __name__ == "__main__":
    unittest.main()
