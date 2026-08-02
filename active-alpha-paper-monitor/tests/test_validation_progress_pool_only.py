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
