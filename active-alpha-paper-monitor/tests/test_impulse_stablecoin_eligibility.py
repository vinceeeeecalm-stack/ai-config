import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "impulse_capture_scanner.py"
SPEC = importlib.util.spec_from_file_location("impulse_stablecoin_eligibility", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class StablecoinEligibilityTests(unittest.TestCase):
    def run_main_fixture(self, symbols, exchange_symbols):
        aave_signal = {
            "symbol": "AAVEUSDT",
            "data_quality": "verified_fixture",
            "current_price": 100.0,
            "quote_volume_15m": 1_000_000.0,
            "volume_multiple_5m_vs_median": 2.0,
            "trade_count_15m": 1_000,
            "vwap_15m": 100.0,
            "spread_bps": 1.0,
            "top_bid_depth_usd": 100_000.0,
            "top_ask_depth_usd": 100_000.0,
            "anchor_state": "supportive",
            "anchor_changes_pct": {"BTCUSDT": 1.0, "ETHUSDT": 1.0},
            "impulse_score_points": 50.0,
            "why_now": ["fixture"],
            "return_1m_pct": 0.1,
            "return_5m_pct": 0.2,
            "return_15m_pct": 0.3,
        }

        def fake_fetch(path, *_args, **_kwargs):
            if path == "/api/v3/ping":
                return {}
            if path == "/api/v3/exchangeInfo":
                return {"symbols": exchange_symbols}
            if path == "/api/v3/ticker/bookTicker":
                return [
                    {
                        "symbol": "AAVEUSDT",
                        "bidPrice": "99.9",
                        "askPrice": "100.1",
                    }
                ]
            raise AssertionError(f"unexpected fetch: {path}")

        output = io.StringIO()
        with (
            patch.object(M, "fetch_json", side_effect=fake_fetch),
            patch.object(M, "anchor_state", return_value=("supportive", {})),
            patch.object(M, "analyze_symbol", return_value=aave_signal),
            redirect_stdout(output),
        ):
            return_code = M.main(
                [
                    "--symbols",
                    symbols,
                    "--dynamic-top",
                    "0",
                    "--discovery-only",
                    "--no-write",
                ]
            )
        self.assertEqual(return_code, 0)
        return output.getvalue()

    def test_known_stablecoins_are_excluded_before_opportunity_ranking(self):
        for symbol in (
            "UUSDT",
            "RLUSDUSDT",
            "USDEUSDT",
            "PYUSDUSDT",
            "USDCUSDT",
            "EURUSDT",
        ):
            with self.subTest(symbol=symbol):
                self.assertFalse(M.is_dynamic_scan_candidate(symbol))
                self.assertEqual(
                    M.dynamic_scan_rejection_reason(symbol),
                    "stablecoin_or_fiat_like",
                )

    def test_explicit_symbols_use_the_same_opportunity_gate(self):
        M.DISCOVERY_AUDIT["candidate_eligibility_rejected"].clear()

        accepted = M.filter_opportunity_symbols(
            ["UUSDT", "BTCUSDT", "RLUSDUSDT", "BTCUSDT"]
        )

        self.assertEqual(accepted, ["BTCUSDT"])
        rejected = {
            item["symbol"]: item["reason"]
            for item in M.DISCOVERY_AUDIT["candidate_eligibility_rejected"]
        }
        self.assertEqual(rejected["UUSDT"], "stablecoin_or_fiat_like")
        self.assertEqual(rejected["RLUSDUSDT"], "stablecoin_or_fiat_like")

    def test_explicit_stablecoin_cannot_reach_main_discovery_ranking(self):
        M.DISCOVERY_AUDIT["candidate_eligibility_rejected"].clear()
        output = self.run_main_fixture(
            "UUSDT,AAVEUSDT",
            [
                {
                    "symbol": "AAVEUSDT",
                    "baseAsset": "AAVE",
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "permissionSets": [["SPOT"]],
                }
            ],
        )

        result = json.loads(output)
        ranked_symbols = [item["symbol"] for item in result["top_candidates"]]
        self.assertNotIn("UUSDT", ranked_symbols)
        self.assertIn("AAVEUSDT", ranked_symbols)
        self.assertIn(
            {"symbol": "UUSDT", "reason": "stablecoin_or_fiat_like"},
            result["dynamic_discovery_audit"]["candidate_eligibility_rejected"],
        )

    def test_low_beta_commodity_tokens_remain_excluded(self):
        for symbol in ("PAXGUSDT", "XAUTUSDT"):
            with self.subTest(symbol=symbol):
                self.assertFalse(M.is_dynamic_scan_candidate(symbol))

    def test_leveraged_tokens_are_rejected_by_exchange_product_identity(self):
        for symbol in (
            "BTCUPUSDT",
            "SXPUPUSDT",
            "SXPDOWNUSDT",
            "1INCHUPUSDT",
            "1INCHDOWNUSDT",
        ):
            with self.subTest(symbol=symbol):
                self.assertEqual(
                    M.exchange_spot_rejection_reason(
                        {
                            "symbol": symbol,
                            "status": "BREAK",
                            "isSpotTradingAllowed": False,
                            "permissionSets": [["LEVERAGED"]],
                        }
                    ),
                    "leveraged_token",
                )

    def test_merged_inputs_share_the_authoritative_exchange_gate(self):
        definitions = {
            "SXPUPUSDT": {
                "symbol": "SXPUPUSDT",
                "status": "BREAK",
                "isSpotTradingAllowed": False,
                "permissionSets": [["LEVERAGED"]],
            },
            "AAVEUSDT": {
                "symbol": "AAVEUSDT",
                "status": "TRADING",
                "isSpotTradingAllowed": True,
                "permissionSets": [["SPOT"]],
            },
        }
        M.DISCOVERY_AUDIT["spot_eligibility_rejected"].clear()

        accepted = M.filter_exchange_spot_symbols(
            ["SXPUPUSDT", "AAVEUSDT"],
            definitions,
        )

        self.assertEqual(accepted, ["AAVEUSDT"])
        self.assertIn(
            {"symbol": "SXPUPUSDT", "reason": "leveraged_token"},
            M.DISCOVERY_AUDIT["spot_eligibility_rejected"],
        )

    def test_explicit_leveraged_product_cannot_reach_main_ranking(self):
        M.DISCOVERY_AUDIT["spot_eligibility_rejected"].clear()
        output = self.run_main_fixture(
            "SXPUPUSDT,AAVEUSDT",
            [
                {
                    "symbol": "SXPUPUSDT",
                    "baseAsset": "SXPUP",
                    "status": "BREAK",
                    "isSpotTradingAllowed": False,
                    "permissionSets": [["LEVERAGED"]],
                },
                {
                    "symbol": "AAVEUSDT",
                    "baseAsset": "AAVE",
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "permissionSets": [["SPOT"]],
                },
            ],
        )

        result = json.loads(output)
        ranked_symbols = [item["symbol"] for item in result["top_candidates"]]
        self.assertNotIn("SXPUPUSDT", ranked_symbols)
        self.assertIn("AAVEUSDT", ranked_symbols)
        self.assertIn(
            {"symbol": "SXPUPUSDT", "reason": "leveraged_token"},
            result["dynamic_discovery_audit"]["spot_eligibility_rejected"],
        )

    def test_ordinary_crypto_is_not_rejected_by_a_price_heuristic(self):
        # Eligibility deliberately accepts only symbol identity. A risk asset
        # trading at or near one dollar must not be mistaken for a stablecoin.
        for symbol in (
            "BTCUSDT",
            "AAVEUSDT",
            "NIGHTUSDT",
            "ASSETUSDT",
            "JUPUSDT",
            "SYRUPUSDT",
        ):
            with self.subTest(symbol=symbol):
                self.assertTrue(M.is_dynamic_scan_candidate(symbol))
                self.assertIsNone(M.dynamic_scan_rejection_reason(symbol))

    def test_jup_and_syrup_are_ordinary_spot_products(self):
        for symbol in ("JUPUSDT", "SYRUPUSDT"):
            with self.subTest(symbol=symbol):
                self.assertIsNone(
                    M.exchange_spot_rejection_reason(
                        {
                            "symbol": symbol,
                            "status": "TRADING",
                            "isSpotTradingAllowed": True,
                            "permissionSets": [["SPOT"]],
                        }
                    )
                )

    def test_dynamic_discovery_applies_exchange_identity_before_selection(self):
        ticker_rows = [
            {"symbol": "SXPUPUSDT", "quoteVolume": "900"},
            {"symbol": "1INCHDOWNUSDT", "quoteVolume": "800"},
            {"symbol": "JUPUSDT", "quoteVolume": "700"},
            {"symbol": "SYRUPUSDT", "quoteVolume": "600"},
        ]
        exchange_rows = {
            "symbols": [
                {
                    "symbol": symbol,
                    "baseAsset": symbol.removesuffix("USDT"),
                    "status": "BREAK",
                    "isSpotTradingAllowed": False,
                    "permissionSets": [["LEVERAGED"]],
                }
                for symbol in ("SXPUPUSDT", "1INCHDOWNUSDT")
            ]
            + [
                {
                    "symbol": symbol,
                    "baseAsset": symbol.removesuffix("USDT"),
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "permissionSets": [["SPOT"]],
                }
                for symbol in ("JUPUSDT", "SYRUPUSDT")
            ]
        }
        M.DISCOVERY_AUDIT["spot_eligibility_rejected"].clear()

        with (
            patch.object(M, "fetch_json", side_effect=[ticker_rows, exchange_rows]),
            patch.object(M, "fetch_binance_public_product_identity", return_value={}),
        ):
            selected = M.discover_symbols(4, 8)

        self.assertEqual(selected, ["JUPUSDT", "SYRUPUSDT"])
        rejected = {
            item["symbol"]: item["reason"]
            for item in M.DISCOVERY_AUDIT["spot_eligibility_rejected"]
        }
        self.assertEqual(rejected["SXPUPUSDT"], "leveraged_token")
        self.assertEqual(rejected["1INCHDOWNUSDT"], "leveraged_token")

    def test_suffix_collision_uses_public_identity_and_preserves_native_crypto(self):
        ticker_rows = [
            {"symbol": "SHIBUSDT", "quoteVolume": "1000"},
            {"symbol": "SOXLBUSDT", "quoteVolume": "900"},
            {"symbol": "BTCUSDT", "quoteVolume": "800"},
        ]
        exchange_rows = {
            "symbols": [
                {
                    "symbol": symbol,
                    "baseAsset": symbol.removesuffix("USDT"),
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "permissionSets": [["SPOT"]],
                }
                for symbol in ("SHIBUSDT", "SOXLBUSDT", "BTCUSDT")
            ]
        }

        product_identity = {
            "SHIBUSDT": {"s": "SHIBUSDT", "an": "SHIBA INU", "tags": ["Meme"]},
            "SOXLBUSDT": {
                "s": "SOXLBUSDT",
                "an": "Semicon Bull 3X ETF (bStocks)",
                "tags": ["bStocks"],
            },
            "BTCUSDT": {"s": "BTCUSDT", "an": "Bitcoin", "tags": []},
        }

        M.DISCOVERY_AUDIT["crypto_identity_rejected"].clear()
        with (
            patch.object(M, "fetch_json", side_effect=[ticker_rows, exchange_rows]),
            patch.object(
                M,
                "fetch_binance_public_product_identity",
                return_value=product_identity,
            ),
        ):
            selected = M.discover_symbols(3, 8)

        self.assertEqual(selected, ["SHIBUSDT", "BTCUSDT"])
        self.assertIn(
            "SOXLBUSDT",
            {item["symbol"] for item in M.DISCOVERY_AUDIT["crypto_identity_rejected"]},
        )

    def test_suffix_identity_lookup_failure_is_candidate_local(self):
        ticker_rows = [
            {"symbol": "NATIVEBUSDT", "quoteVolume": "1000"},
            {"symbol": "BTCUSDT", "quoteVolume": "900"},
        ]
        exchange_rows = {
            "symbols": [
                {
                    "symbol": symbol,
                    "baseAsset": symbol.removesuffix("USDT"),
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "permissionSets": [["SPOT"]],
                }
                for symbol in ("NATIVEBUSDT", "BTCUSDT")
            ]
        }

        with (
            patch.object(M, "fetch_json", side_effect=[ticker_rows, exchange_rows]),
            patch.object(
                M,
                "fetch_binance_public_product_identity",
                side_effect=TimeoutError("product catalog fixture timeout"),
            ),
            patch.object(
                M,
                "fetch_public_identity_search",
                side_effect=TimeoutError("identity fixture timeout"),
            ),
        ):
            selected = M.discover_symbols(2, 8)

        self.assertEqual(selected, ["BTCUSDT"])
        rejection = next(
            item
            for item in M.DISCOVERY_AUDIT["crypto_identity_rejected"]
            if item["symbol"] == "NATIVEBUSDT"
        )
        self.assertEqual(
            rejection["reason"],
            "public_identity_lookup_unavailable_for_suffix_collision",
        )

    def test_exact_public_native_crypto_identity_re_admits_suffix_collision(self):
        ticker_rows = [
            {"symbol": "NATIVEBUSDT", "quoteVolume": "1000"},
            {"symbol": "BTCUSDT", "quoteVolume": "900"},
        ]
        exchange_rows = {
            "symbols": [
                {
                    "symbol": symbol,
                    "baseAsset": symbol.removesuffix("USDT"),
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "permissionSets": [["SPOT"]],
                }
                for symbol in ("NATIVEBUSDT", "BTCUSDT")
            ]
        }
        public_identity = {
            "symbol": "NATIVEB",
            "security_matches": [],
            "non_security_matches": [
                {"id": "nativeb-chain", "name": "NativeB", "symbol": "nativeb"}
            ],
            "is_security_or_ambiguous": False,
        }

        with (
            patch.object(M, "fetch_json", side_effect=[ticker_rows, exchange_rows]),
            patch.object(
                M,
                "fetch_binance_public_product_identity",
                side_effect=TimeoutError("product catalog fixture timeout"),
            ),
            patch.object(
                M,
                "fetch_public_identity_search",
                return_value=public_identity,
            ),
        ):
            selected = M.discover_symbols(2, 8)

        self.assertEqual(selected, ["NATIVEBUSDT", "BTCUSDT"])

    def test_formal_discovery_source_attempts_are_bound_to_wall_clock_budget(self):
        calls = []

        def bounded_fetch(path, *_args, **kwargs):
            calls.append(
                (
                    path,
                    kwargs.get("timeout"),
                    kwargs.get("max_bases"),
                    kwargs.get("transport"),
                )
            )
            if path == "/api/v3/ticker/24hr":
                return [{"symbol": "BTCUSDT", "quoteVolume": "1000"}]
            if path == "/api/v3/exchangeInfo":
                return {
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "baseAsset": "BTC",
                            "status": "TRADING",
                            "isSpotTradingAllowed": True,
                            "permissionSets": [["SPOT"]],
                        }
                    ]
                }
            raise AssertionError(path)

        with (
            patch.object(M, "fetch_json", side_effect=bounded_fetch),
            patch.object(M, "fetch_binance_public_product_identity", return_value={}),
        ):
            selected = M.discover_symbols(1, 4)

        self.assertEqual(selected, ["BTCUSDT"])
        self.assertTrue(calls)
        for path, timeout, max_bases, transport in calls:
            expected_timeout = (
                M.DISCOVERY_TICKER_TIMEOUT_SECONDS
                if path == "/api/v3/ticker/24hr"
                else M.DISCOVERY_PUBLIC_TIMEOUT_SECONDS
            )
            self.assertLessEqual(timeout, expected_timeout)
            self.assertLessEqual(max_bases, M.DISCOVERY_PUBLIC_MAX_BASES)
            self.assertEqual(transport, "curl")

    def test_strategy_version_isolated_after_candidate_universe_change(self):
        self.assertEqual(M.TACTICAL_STRATEGY_VERSION, "impulse-capture-tactical-v6")

    def test_positive_micro_price_is_not_rounded_to_zero(self):
        rounded = M.rounded(0.00000576)

        self.assertGreater(rounded, 0)
        self.assertEqual(rounded, 0.00000576)


if __name__ == "__main__":
    unittest.main()
