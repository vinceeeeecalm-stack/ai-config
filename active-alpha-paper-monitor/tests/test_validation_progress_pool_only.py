import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "validation_progress_runner.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validation_progress_pool_only",
    SCRIPT,
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def pool_fixture():
    return {
        "status": "ok",
        "market_regime": "mixed_selective",
        "market_atmosphere": "mixed_selective",
        "short_term_state": "neutral",
        "sentiment_state": "neutral_or_missing_social",
        "pool_width_policy": {
            "configured_limit": 30,
            "effective_limit": 30,
            "reason": "fixture",
        },
        "pool_shape_policy": {
            "quota_status": "met",
            "selected_counts": {"core_defensive": 1, "high_beta": 1},
        },
        "selected_symbols": ["BTCUSDT", "PEPEUSDT"],
        "top_dynamic_candidates": [
            {"symbol": "PEPEUSDT", "score": 10.0}
        ],
    }


class ValidationProgressPoolOnlyTests(unittest.TestCase):
    def test_tokenized_security_suffixes_are_never_opportunity_ranked(self):
        product_identity = {
            symbol: {"s": symbol, "an": "Security (bStocks)", "tags": ["bStocks"]}
            for symbol in ("SOXLBUSDT", "MUBUSDT", "EWYBUSDT")
        }
        for symbol in product_identity:
            with self.subTest(symbol=symbol):
                self.assertEqual(
                    M.product_identity_rejection_reason(
                        symbol, product_identity, source_available=True
                    ),
                    "tokenized_security_product",
                )
        self.assertTrue(M.is_scan_eligible_usdt_symbol("SHIBUSDT"))
        self.assertTrue(M.is_scan_eligible_usdt_symbol("DGBUSDT"))

    def test_invalid_protected_symbol_is_monitor_only_not_selected(self):
        ticker_rows = [
            {
                "symbol": "SOXLBUSDT",
                "quoteVolume": "100000000",
                "priceChangePercent": "20",
                "count": "100000",
            },
            {
                "symbol": "BTCUSDT",
                "quoteVolume": "90000000",
                "priceChangePercent": "1",
                "count": "90000",
            },
            {
                "symbol": "SHIBUSDT",
                "quoteVolume": "80000000",
                "priceChangePercent": "5",
                "count": "80000",
            },
        ]
        product_identity = {
            "SOXLBUSDT": {
                "s": "SOXLBUSDT",
                "an": "Semicon Bull 3X ETF (bStocks)",
                "tags": ["bStocks"],
            },
            "BTCUSDT": {"s": "BTCUSDT", "an": "Bitcoin", "tags": []},
            "SHIBUSDT": {"s": "SHIBUSDT", "an": "SHIBA INU", "tags": ["Meme"]},
        }
        with (
            patch.object(M, "load_open_symbols", return_value=[]),
            patch.object(
                M,
                "fetch_binance_public_product_identity",
                return_value=product_identity,
            ),
            patch.object(M, "social_handoff_state", return_value={"status": "missing"}),
            patch.object(M, "social_symbol_scores", return_value={}),
            patch.object(M, "build_short_term_anchor_profile", return_value={}),
            patch.object(M, "public_json", return_value=(ticker_rows, {"source": "fixture"})),
        ):
            selected_text, audit = M.build_dynamic_scan_universe(
                "SOXLBUSDT,BTCUSDT", 5
            )

        self.assertNotIn("SOXLBUSDT", selected_text.split(","))
        self.assertNotIn(
            "SOXLBUSDT",
            [item["symbol"] for item in audit["top_dynamic_candidates"]],
        )
        self.assertIn("SHIBUSDT", [item["symbol"] for item in audit["top_dynamic_candidates"]])
        self.assertEqual(audit["identity_monitor_only_symbols"], ["SOXLBUSDT"])

    def test_dry_run_never_executes_automatic_or_explicit_cache_builder(self):
        automatic = M.dynamic_cache_execution_plan(
            dry_run=True,
            include_dynamic_kline_cache=False,
            auto_current_signal_refresh_required=True,
        )
        explicit = M.dynamic_cache_execution_plan(
            dry_run=True,
            include_dynamic_kline_cache=True,
            auto_current_signal_refresh_required=False,
        )
        live = M.dynamic_cache_execution_plan(
            dry_run=False,
            include_dynamic_kline_cache=True,
            auto_current_signal_refresh_required=False,
        )

        self.assertFalse(automatic["execute"])
        self.assertFalse(automatic["auto_prefetch_enabled"])
        self.assertEqual(automatic["skip_reason"], "skipped_dry_run")
        self.assertFalse(explicit["execute"])
        self.assertEqual(explicit["skip_reason"], "skipped_dry_run")
        self.assertTrue(live["execute"])

    def test_pool_only_output_is_bounded_and_cannot_create_action(self):
        with patch.object(M.time, "monotonic", return_value=110.0):
            output = M.pool_only_output(
                run_id="fixture-run",
                started_monotonic=100.0,
                dynamic_scan_pool_enabled=True,
                dynamic_scan_universe=pool_fixture(),
                effective_symbols="BTCUSDT,PEPEUSDT",
            )

        self.assertEqual(output["status"], "PASS")
        self.assertTrue(output["within_budget"])
        self.assertEqual(output["child_runs"], [])
        self.assertFalse(output["persistent_write_allowed"])
        self.assertFalse(output["formal_action_created"])
        self.assertFalse(output["paper_fill_created"])
        self.assertFalse(output["live_order_created"])

    def test_missing_pool_contract_field_is_explicitly_degraded(self):
        fixture = pool_fixture()
        del fixture["pool_shape_policy"]
        with patch.object(M.time, "monotonic", return_value=101.0):
            output = M.pool_only_output(
                run_id="fixture-run",
                started_monotonic=100.0,
                dynamic_scan_pool_enabled=True,
                dynamic_scan_universe=fixture,
                effective_symbols="BTCUSDT",
            )

        self.assertEqual(output["status"], "DEGRADED")
        self.assertEqual(
            output["missing_required_fields"],
            ["pool_shape_policy"],
        )

    def test_pool_only_main_returns_before_any_child_workflow(self):
        fixture = pool_fixture()
        stdout = io.StringIO()
        argv = [
            str(SCRIPT),
            "--pool-only",
            "--disable-dynamic-social-refresh",
            "--dynamic-max-symbols",
            "30",
        ]

        with (
            patch.object(sys, "argv", argv),
            patch.object(
                M,
                "social_handoff_state",
                return_value={"status": "missing", "should_refresh": False},
            ),
            patch.object(
                M,
                "build_dynamic_scan_universe",
                return_value=("BTCUSDT,PEPEUSDT", fixture),
            ),
            patch.object(M, "validation_audit") as validation_audit,
            patch.object(M, "dynamic_kline_cache_builder") as cache_builder,
            redirect_stdout(stdout),
        ):
            return_code = M.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(return_code, 0)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["child_runs"], [])
        validation_audit.assert_not_called()
        cache_builder.assert_not_called()

    def test_default_pool_only_does_not_refresh_stale_social_handoff(self):
        fixture = pool_fixture()
        stdout = io.StringIO()
        argv = [
            str(SCRIPT),
            "--pool-only",
            "--dynamic-max-symbols",
            "30",
        ]

        with (
            patch.object(sys, "argv", argv),
            patch.object(
                M,
                "social_handoff_state",
                return_value={"status": "stale", "should_refresh": True},
            ),
            patch.object(M, "refresh_social_handoff") as refresh_social,
            patch.object(
                M,
                "build_dynamic_scan_universe",
                return_value=("BTCUSDT,PEPEUSDT", fixture),
            ),
            redirect_stdout(stdout),
        ):
            return_code = M.main()

        payload = json.loads(stdout.getvalue())
        social = payload["dynamic_scan_universe"]["dynamic_social_refresh"]
        self.assertEqual(return_code, 0)
        self.assertFalse(social["enabled"])
        self.assertEqual(social["status"], "skipped_read_only_pool_only")
        refresh_social.assert_not_called()

    def test_recovery_shadow_prefetch_skips_builder_in_dry_run(self):
        args = M.build_parser().parse_args(["--dry-run"])
        plan = {
            "symbols": ["BTCUSDT"],
            "intervals": ["5m"],
            "cache_dir": "/tmp/never-written",
        }

        with patch.object(M, "run_command") as run_command:
            result = M.recovery_shadow_kline_prefetch(args, plan)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["stdout_json"]["status"], "skipped_dry_run")
        run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
