import importlib.util
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "impulse_capture_scanner.py"
SPEC = importlib.util.spec_from_file_location("impulse_capture_scanner_failover", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class EndpointFailoverTests(unittest.TestCase):
    def setUp(self):
        M.reset_endpoint_health_for_test()

    def test_two_timed_out_bases_fall_through_to_third(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            if request.full_url.startswith(M.BINANCE_PUBLIC_BASES[0]):
                raise TimeoutError("primary timed out")
            if request.full_url.startswith(M.BINANCE_PUBLIC_BASES[1]):
                raise TimeoutError("secondary timed out")
            if request.full_url.startswith(M.BINANCE_PUBLIC_BASES[2]):
                return FakeResponse({"source": "third", "ok": True})
            raise AssertionError(f"unexpected endpoint: {request.full_url}")

        with mock.patch.object(M, "urlopen", side_effect=fake_urlopen):
            payload = M.fetch_json("/api/v3/ping", timeout=0.01, max_bases=3)
            first_call_count = len(calls)
            repeated = M.fetch_json("/api/v3/ping", timeout=0.01, max_bases=3)

        self.assertEqual(payload, {"source": "third", "ok": True})
        self.assertEqual(repeated, payload)
        self.assertEqual(first_call_count, 3)
        self.assertEqual(len(calls), 4)
        self.assertTrue(calls[-1].startswith(M.BINANCE_PUBLIC_BASES[2]))
        audit = M.endpoint_audit_snapshot()
        self.assertEqual(audit[M.BINANCE_PUBLIC_BASES[0]]["failure_count"], 1)
        self.assertEqual(audit[M.BINANCE_PUBLIC_BASES[1]]["failure_count"], 1)
        self.assertFalse(audit[M.BINANCE_PUBLIC_BASES[0]]["circuit_open"])
        self.assertFalse(audit[M.BINANCE_PUBLIC_BASES[1]]["circuit_open"])
        self.assertEqual(audit[M.BINANCE_PUBLIC_BASES[2]]["success_count"], 2)

    def test_repeated_failures_open_circuit_and_skip_endpoint(self):
        first = M.BINANCE_PUBLIC_BASES[0]
        second = M.BINANCE_PUBLIC_BASES[1]
        calls = []

        def fail_first(request, timeout):
            calls.append(request.full_url)
            raise TimeoutError("primary timed out")

        with mock.patch.object(M, "urlopen", side_effect=fail_first):
            for _ in range(M.BINANCE_ENDPOINT_FAILURE_THRESHOLD):
                with self.assertRaises(TimeoutError):
                    M.fetch_json("/api/v3/ping", timeout=0.01, max_bases=1)

        self.assertEqual(len(calls), M.BINANCE_ENDPOINT_FAILURE_THRESHOLD)
        self.assertTrue(M.endpoint_audit_snapshot()[first]["circuit_open"])

        calls.clear()
        with mock.patch.object(
            M,
            "urlopen",
            side_effect=lambda request, timeout: (
                calls.append(request.full_url) or FakeResponse({"ok": True})
            ),
        ):
            self.assertEqual(
                M.fetch_json("/api/v3/ping", timeout=0.01, max_bases=1),
                {"ok": True},
            )
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith(second))

    def test_max_bases_counts_actual_attempts_not_open_circuit_skips(self):
        first = M.BINANCE_PUBLIC_BASES[0]
        with M.BINANCE_ENDPOINT_LOCK:
            M.BINANCE_ENDPOINT_STATE[first]["circuit_open_until"] = (
                M.time.monotonic() + 30
            )
        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            return FakeResponse({"ok": True})

        with mock.patch.object(M, "urlopen", side_effect=fake_urlopen):
            payload = M.fetch_json("/api/v3/ping", timeout=0.01, max_bases=1)

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith(M.BINANCE_PUBLIC_BASES[1]))
        self.assertEqual(M.endpoint_audit_snapshot()[first]["skipped_count"], 1)

    def test_bounded_curl_transport_returns_json_and_records_transport(self):
        completed = M.subprocess.CompletedProcess(
            args=["curl"],
            returncode=0,
            stdout='{"ok": true, "source": "bounded-curl"}',
            stderr="",
        )
        with mock.patch.object(M.subprocess, "run", return_value=completed) as run:
            payload = M.fetch_json(
                "/api/v3/ping",
                timeout=0.5,
                max_bases=1,
                transport="curl",
            )

        self.assertEqual(payload, {"ok": True, "source": "bounded-curl"})
        args = run.call_args.args[0]
        self.assertIn("--connect-timeout", args)
        self.assertIn("--max-time", args)
        audit = M.endpoint_audit_snapshot()[M.BINANCE_PUBLIC_BASES[0]]
        self.assertEqual(audit["last_transport"], "curl")
        self.assertEqual(audit["success_count"], 1)

    def test_bounded_curl_transport_fails_over_once(self):
        failed = M.subprocess.CompletedProcess(
            args=["curl"], returncode=28, stdout="", stderr="timed out"
        )
        passed = M.subprocess.CompletedProcess(
            args=["curl"], returncode=0, stdout='{"ok": true}', stderr=""
        )
        with mock.patch.object(
            M.subprocess, "run", side_effect=[failed, passed]
        ) as run:
            payload = M.fetch_json(
                "/api/v3/ping",
                timeout=0.5,
                max_bases=2,
                transport="curl",
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(run.call_count, 2)
        self.assertIn(M.BINANCE_PUBLIC_BASES[0], run.call_args_list[0].args[0][-1])
        self.assertIn(M.BINANCE_PUBLIC_BASES[1], run.call_args_list[1].args[0][-1])

    def test_discovery_runtime_audit_exposes_fresh_curl_evidence(self):
        endpoint_audit = {
            base: {
                "success_count": 1 if index == 0 else 0,
                "failure_count": 0,
                "skipped_count": 0,
                "last_error": None,
                "last_success_at": "2026-08-02T07:40:00Z" if index == 0 else None,
                "last_transport": "curl" if index == 0 else None,
                "consecutive_failures": 0,
                "circuit_open": False,
            }
            for index, base in enumerate(M.BINANCE_PUBLIC_BASES)
        }
        original_rows = M.DISCOVERY_AUDIT["ticker_rows_considered"]
        M.DISCOVERY_AUDIT["ticker_rows_considered"] = 701
        try:
            with mock.patch.object(
                M, "endpoint_audit_snapshot", return_value=endpoint_audit
            ):
                audit = M.discovery_runtime_audit([])
        finally:
            M.DISCOVERY_AUDIT["ticker_rows_considered"] = original_rows

        self.assertEqual(audit["ticker_rows_considered"], 701)
        self.assertEqual(
            audit["dynamic_discovery_audit"]["ticker_rows_considered"], 701
        )
        self.assertEqual(audit["public_endpoint_audit"], endpoint_audit)
        self.assertTrue(audit["fresh_transport_verified"])
        self.assertEqual(audit["transport_successes"][0]["last_transport"], "curl")

    def test_all_endpoint_failures_emit_no_fresh_decision(self):
        output = io.StringIO()
        failed = M.subprocess.CompletedProcess(
            args=["curl"], returncode=28, stdout="", stderr="all public endpoints timed out"
        )
        with mock.patch.object(
            M.subprocess,
            "run",
            return_value=failed,
        ), contextlib.redirect_stdout(output):
            return_code = M.main(
                [
                    "--symbols",
                    "BTCUSDT",
                    "--dynamic-top",
                    "0",
                    "--top",
                    "1",
                    "--max-workers",
                    "1",
                    "--request-mode",
                    "tactical_1_7d",
                    "--timeout",
                    "1",
                    "--no-write",
                ]
            )

        result = json.loads(output.getvalue())
        self.assertEqual(return_code, 0)
        self.assertEqual(result["decision_status"], "NO_FRESH_DECISION")
        self.assertEqual(result["current_action"], "NO_TRADE")
        self.assertFalse(result["fresh_market_data_available"])
        self.assertTrue(result["no_fresh_decision"])
        self.assertIsNone(result["trade_plan"])
        self.assertEqual(result["observation_samples"], [])
        self.assertTrue(result["human_confirmation_required"])
        self.assertFalse(result["live_orders_enabled"])
        self.assertFalse(result["private_api_used"])
        self.assertTrue(result["runtime_mode"]["no_write"])


if __name__ == "__main__":
    unittest.main()
