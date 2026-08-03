import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CONFIG = Path(__file__).resolve().parents[1] / "config" / "us_equity_intraday_shadow_v1.json"
sys.path.insert(0, str(SCRIPTS))

import us_equity_intraday_shadow as engine  # noqa: E402


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def raw_market(as_of: datetime, *, price: float = 100.0, spread: float = 0.10) -> dict:
    bars = []
    start = as_of - timedelta(minutes=20)
    for index in range(20):
        current = price * (0.985 + index * 0.001)
        bars.append(
            {
                "t": iso(start + timedelta(minutes=index)),
                "o": current * 0.999,
                "h": current * 1.002,
                "l": current * 0.998,
                "c": current,
                "v": 10_000,
                "vw": current * 0.9995,
                "n": 100 if index < 10 else 220,
            }
        )
    return {
        "latestTrade": {"p": price, "t": iso(as_of - timedelta(seconds=5))},
        "latestQuote": {
            "bp": price - spread / 2,
            "ap": price + spread / 2,
            "bs": 500,
            "as": 600,
            "t": iso(as_of - timedelta(seconds=4)),
        },
        "minuteBar": bars[-1],
        "dailyBar": {**bars[-1], "v": 3_000_000, "n": 20_000},
        "prevDailyBar": {
            **bars[-1],
            "o": price * 0.955,
            "h": price * 0.97,
            "l": price * 0.95,
            "c": price * 0.96,
            "v": 2_000_000,
            "n": 15_000,
        },
        "bars1m": bars,
        "bars5m": bars[::5],
    }


def candidate(symbol: str = "IREN", *, price: float = 100.0) -> dict:
    return {
        "symbol": symbol,
        "exchange": "NASDAQ",
        "asset_class": "COMMON_STOCK",
        "identity_verified": True,
        "tradable": True,
        "otc": False,
        "halted": False,
        "delisting_risk": False,
        "median_dollar_volume_20d": 200_000_000,
        "discovery_price": price,
        "discovery_rank": 1,
        "discovery_score": 80.0,
        "same_time_volume_baseline": 50_000,
        "sector": "AI_INFRASTRUCTURE",
        "sector_change_pct": 3.0,
        "benchmark_change_pct": 0.4,
        "symbol_change_pct": 6.0,
        "breakout_confirmed": True,
        "first_retest_confirmed": True,
        "higher_lows": True,
        "catalyst": {
            "verified": True,
            "title": "official capacity update",
            "official_source": "issuer_ir",
            "published_at": "2026-08-03T11:00:00Z",
            "realization_by": "2026-08-03T20:00:00Z",
        },
        "valuation": {"fair_value_low": price * 1.05, "fair_value_high": price * 1.35, "method": "scenario_cash_flow"},
        "capital_risk": {"severe_dilution": False, "binary_event": False, "halt_risk": False, "financing_imminent": False},
        "regression": {"sample_size": 35, "conservative_ev_net_pct": 1.4, "target_first_probability_lower": 0.58, "reward_risk": 2.5},
        "expected_return_pct": 10.0,
        "expected_drawdown_pct": 3.5,
        "fees_slippage_pct": 0.2,
        "plan": {
            "entry_low": price * 0.998,
            "entry_high": price * 1.002,
            "target_1": price * 1.06,
            "target_2": price * 1.10,
            "stop": price * 0.97,
            "trigger": "closed_breakout_or_first_retest",
            "latest_exit_at": "2026-08-03T19:55:00Z",
            "invalidation": "lost_vwap_and_opening_range",
        },
    }


def sanitized(symbol: str, as_of: datetime, *, feed: str = "iex", second: bool = True, price: float = 100.0, spread: float = 0.10) -> dict:
    cross = {"source": "independent_public_quote", "price": price * 1.001, "as_of": iso(as_of - timedelta(seconds=6))} if second else None
    return engine.sanitize_alpaca_snapshot(raw_market(as_of, price=price, spread=spread), symbol=symbol, feed=feed, received_at=iso(as_of), second_source=cross, _authority_token=engine._ADAPTER_AUTHORITY)


def payload(as_of: datetime, candidates: list[dict], snapshots: dict[str, dict]) -> dict:
    return {
        "snapshot_id": f"snapshot-{as_of.strftime('%Y%m%d%H%M')}",
        "config": json.loads(CONFIG.read_text(encoding="utf-8")),
        "source_manifest": ["alpaca_iex", "independent_public_quote", "issuer_ir"],
        "candidates": candidates,
        "alpaca_snapshots": snapshots,
        "prior_candidate_states": {},
        "universe_audit": {
            "schema_version": "USEquityDynamicUniverseAuditV1",
            "exchanges": ["NASDAQ", "NYSE", "NYSEAMERICAN", "NYSEARCA"],
            "active_asset_count": 6000,
            "screened_candidate_count": max(len(candidates), 40),
            "discovery_methods": ["unusual_volume", "day_gainers", "sector_rotation", "news_catalyst"],
            "completed_at": iso(as_of - timedelta(seconds=15)),
            "static_symbol_whitelist_used": False,
        },
    }


class AlpacaAuthorityTests(unittest.TestCase):
    def test_iex_only_and_cross_verified_are_distinct(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(sanitized("IREN", now, second=False)["data_grade"], "IEX_ONLY")
        self.assertEqual(sanitized("IREN", now, second=True)["data_grade"], "IEX_CROSS_VERIFIED")

    def test_sip_connector_success_remains_disabled_until_future_governed_upgrade(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        raw = raw_market(now)
        snapshot_inner = {"tool": "get_stock_snapshot", "request": {"symbols": ["IREN"], "feed": "sip"}, "snapshots": {"IREN": {key: value for key, value in raw.items() if key not in {"bars1m", "bars5m"}}}}
        envelope = {"isError": False, "structuredContent": {"result": json.dumps(snapshot_inner)}}
        with self.assertRaisesRegex(engine.USEquityShadowError, "sip_not_available_in_stage0"):
            engine.adapt_alpaca_plugin_evidence(envelope, {"bars": {"IREN": raw["bars1m"]}}, {"bars": {"IREN": raw["bars5m"]}}, symbol="IREN", received_at=iso(now))

    def test_caller_asserted_sip_without_connector_success_is_rejected(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(engine.USEquityShadowError, "sip_not_available_in_stage0"):
            engine.sanitize_alpaca_snapshot(raw_market(now), symbol="IREN", feed="sip", received_at=iso(now))

    def test_arbitrary_second_source_dictionary_cannot_upgrade_iex(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        second = {"source": "random", "price": 100.0, "as_of": iso(now)}
        with self.assertRaisesRegex(engine.USEquityShadowError, "second_source_requires_connector_envelope"):
            engine.sanitize_alpaca_snapshot(raw_market(now), symbol="IREN", feed="iex", received_at=iso(now), second_source=second)

    def test_stale_or_conflicting_source_blocks_realtime_grade(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        conflict = {"source": "public", "price": 103.0, "as_of": iso(now - timedelta(seconds=5))}
        snap = engine.sanitize_alpaca_snapshot(raw_market(now), symbol="IREN", feed="iex", received_at=iso(now), second_source=conflict, _authority_token=engine._ADAPTER_AUTHORITY)
        self.assertEqual(snap["data_grade"], "STALE_OR_CONFLICTED")
        stale_raw = raw_market(now - timedelta(minutes=2))
        stale = engine.sanitize_alpaca_snapshot(stale_raw, symbol="IREN", feed="iex", received_at=iso(now))
        self.assertEqual(stale["data_grade"], "STALE_OR_CONFLICTED")

    def test_future_quote_or_trade_is_not_treated_as_fresh(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        future = raw_market(now)
        future["latestTrade"]["t"] = iso(now + timedelta(seconds=30))
        result = engine.sanitize_alpaca_snapshot(future, symbol="IREN", feed="iex", received_at=iso(now))
        self.assertEqual(result["data_grade"], "STALE_OR_CONFLICTED")

    def test_secret_or_account_fields_are_rejected(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        unsafe = raw_market(now)
        unsafe["api_key"] = "never-persist"
        with self.assertRaises(engine.USEquityShadowError):
            engine.sanitize_alpaca_snapshot(unsafe, symbol="IREN", feed="iex", received_at=iso(now))

    def test_real_connector_outer_shape_is_extracted_and_whitelisted(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        raw = raw_market(now)
        snapshot_outer = {
            "tool": "get_stock_snapshot",
            "request": {"symbols": ["IREN"], "feed": "iex"},
            "snapshots": {"IREN": {key: value for key, value in raw.items() if key not in {"bars1m", "bars5m"}}},
        }
        bars1 = {"tool": "get_stock_bars", "bars": {"IREN": raw["bars1m"]}}
        bars5 = {"tool": "get_stock_bars", "bars": {"IREN": raw["bars5m"]}}
        call_tool_result = {"isError": False, "structuredContent": {"result": json.dumps(snapshot_outer)}}
        result = engine.adapt_alpaca_plugin_evidence(call_tool_result, bars1, bars5, symbol="IREN", received_at=iso(now))
        self.assertEqual(result["symbol"], "IREN")
        self.assertEqual(result["feed"], "IEX")
        self.assertEqual(len(result["bars_1m"]), 20)
        self.assertFalse(result["raw_payload_persisted"])

    def test_direct_symbol_map_calltoolresult_shape_is_supported(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        raw = raw_market(now)
        snapshot_map = {"IREN": {key: value for key, value in raw.items() if key not in {"bars1m", "bars5m"}}}
        snap_outer = {"isError": False, "structuredContent": {"result": json.dumps(snapshot_map)}}
        bars1_outer = {"isError": False, "structuredContent": {"result": json.dumps({"IREN": raw["bars1m"]})}}
        bars5_outer = {"isError": False, "structuredContent": {"result": json.dumps({"IREN": raw["bars5m"]})}}
        result = engine.adapt_alpaca_plugin_evidence(snap_outer, bars1_outer, bars5_outer, symbol="IREN", received_at=iso(now))
        self.assertEqual(result["symbol"], "IREN")
        self.assertEqual(len(result["bars_1m"]), 20)

    def test_allowed_public_price_connector_can_cross_verify_iex(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        raw = raw_market(now)
        snapshot_map = {"IREN": {key: value for key, value in raw.items() if key not in {"bars1m", "bars5m"}}}
        snap_outer = {"isError": False, "structuredContent": {"result": json.dumps({"tool": "get_stock_snapshot", "request": {"feed": "iex"}, "snapshots": snapshot_map})}}
        bars1_outer = {"isError": False, "structuredContent": {"result": json.dumps({"IREN": raw["bars1m"]})}}
        bars5_outer = {"isError": False, "structuredContent": {"result": json.dumps({"IREN": raw["bars5m"]})}}
        second = {"isError": False, "structuredContent": {"result": json.dumps({"tool": "public_price_quote", "symbol": "IREN", "source": "Nasdaq public quote", "price": 100.1, "as_of": iso(now - timedelta(seconds=5))})}}
        result = engine.adapt_alpaca_plugin_evidence(snap_outer, bars1_outer, bars5_outer, symbol="IREN", received_at=iso(now), second_source=second)
        self.assertEqual(result["data_grade"], "IEX_CROSS_VERIFIED")


class FullFunnelTests(unittest.TestCase):
    def test_premarket_iex_only_is_complete_wait(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        result = engine.run_shadow(payload(now, [candidate()], {"IREN": sanitized("IREN", now, second=False)}), as_of=iso(now))
        self.assertEqual(result["market_session"], "PREMARKET")
        self.assertEqual(result["research_top1"], "IREN")
        self.assertNotIn("PENNY", [item["symbol"] for item in result["top20"]])
        self.assertEqual(result["decision"]["current_action"], "WAIT_FOR_ENTRY")
        self.assertIn("second_realtime_source_required", result["decision"]["waiting_conditions"])
        self.assertIsNotNone(result["decision"]["decision_card"])
        self.assertFalse(result["formal_action_eligible"])

    def test_premarket_cross_verified_can_reach_shadow_enter(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        result = engine.run_shadow(payload(now, [candidate()], {"IREN": sanitized("IREN", now)}), as_of=iso(now), account_cash=0)
        self.assertEqual(result["decision"]["current_action"], "ENTER_NOW")
        self.assertEqual(result["decision"]["risk_tier"], "SMALL")
        self.assertEqual(result["decision"]["account_execution"], "NO_DEPLOY_CASH")
        self.assertEqual(result["decision"]["executable_amount_usd"], 0)
        self.assertTrue(result["decision"]["shadow_action_only"])

    def test_premarket_wide_spread_is_no_trade_without_fake_card(self) -> None:
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        result = engine.run_shadow(payload(now, [candidate()], {"IREN": sanitized("IREN", now, spread=2.0)}), as_of=iso(now))
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIn("spread_too_wide", result["decision"]["no_trade_reasons"])
        self.assertIsNone(result["decision"]["decision_card"])

    def test_intraday_complete_evidence_can_reach_shadow_enter(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        result = engine.run_shadow(payload(now, [candidate()], {"IREN": sanitized("IREN", now)}), as_of=iso(now), account_cash=1000)
        self.assertEqual(result["market_session"], "REGULAR")
        self.assertEqual(result["decision"]["current_action"], "ENTER_NOW")
        card = result["decision"]["decision_card"]
        self.assertEqual(card["actual_governed_risk_cap_pct"], 0.5)
        self.assertEqual(set(card["risk_stress"]), {"0.5", "1.0", "2.0"})
        self.assertEqual(card["executable_amount_usd"], 0)

    def test_intraday_weak_sector_is_no_trade(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        item = candidate()
        item["sector_change_pct"] = -1.0
        result = engine.run_shadow(payload(now, [item], {"IREN": sanitized("IREN", now)}), as_of=iso(now))
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIn("relative_strength_not_confirmed", result["decision"]["no_trade_reasons"])

    def test_missing_closed_five_minute_bars_is_no_trade(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        market = sanitized("IREN", now)
        market["bars_5m"] = []
        result = engine.run_shadow(payload(now, [candidate()], {"IREN": market}), as_of=iso(now))
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIn("closed_1m_5m_evidence_incomplete", result["decision"]["no_trade_reasons"])

    def test_low_price_and_otc_are_excluded_before_top1(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        low = candidate("PENNY", price=4.0)
        low["otc"] = True
        good = candidate("IREN")
        result = engine.run_shadow(
            payload(now, [low, good], {"PENNY": sanitized("PENNY", now, price=4.0), "IREN": sanitized("IREN", now)}),
            as_of=iso(now),
        )
        self.assertEqual(result["research_top1"], "IREN")

    def test_local_source_failure_preserves_other_candidates(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        result = engine.run_shadow(payload(now, [candidate("MISSING"), candidate("IREN")], {"IREN": sanitized("IREN", now)}), as_of=iso(now))
        self.assertEqual(result["research_top1"], "IREN")
        self.assertEqual(result["candidate_failures"], [{"symbol": "MISSING", "reason": "alpaca_snapshot_missing"}])

    def test_total_source_failure_keeps_top1_and_no_trade_contract(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        result = engine.run_shadow(payload(now, [candidate("IREN")], {}), as_of=iso(now))
        self.assertEqual(result["research_top1"], "IREN")
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIsNone(result["decision"]["decision_card"])

    def test_total_source_failure_without_price_hint_still_keeps_research_top1(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        item = candidate("IREN")
        item.pop("discovery_price")
        result = engine.run_shadow(payload(now, [item], {}), as_of=iso(now))
        self.assertEqual(result["research_top1"], "IREN")
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")

    def test_missing_full_market_universe_proof_forces_no_trade(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        request = payload(now, [candidate()], {"IREN": sanitized("IREN", now)})
        request.pop("universe_audit")
        result = engine.run_shadow(request, as_of=iso(now))
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIn("dynamic_full_market_universe_unverified", result["decision"]["no_trade_reasons"])

    def test_alpaca_amex_alias_satisfies_nyse_american_coverage(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        request = payload(now, [candidate()], {"IREN": sanitized("IREN", now)})
        request["universe_audit"]["exchanges"] = ["NASDAQ", "NYSE", "AMEX"]
        result = engine.run_shadow(request, as_of=iso(now))
        self.assertNotIn("required_exchange_coverage_missing", result["universe_blockers"])

    def test_prior_session_bars_cannot_satisfy_current_session_gate(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        market = sanitized("IREN", now)
        for group in ("bars_1m", "bars_5m"):
            for bar in market[group]:
                bar["timestamp"] = iso(datetime.fromisoformat(bar["timestamp"].replace("Z", "+00:00")) - timedelta(days=1))
        result = engine.run_shadow(payload(now, [candidate()], {"IREN": market}), as_of=iso(now))
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIn("closed_1m_5m_evidence_incomplete", result["decision"]["no_trade_reasons"])

    def test_duplicate_or_out_of_order_bars_cannot_satisfy_current_gate(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        market = sanitized("IREN", now)
        market["bars_1m"].insert(2, copy.deepcopy(market["bars_1m"][1]))
        result = engine.run_shadow(payload(now, [candidate()], {"IREN": market}), as_of=iso(now))
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIn("bar_sequence_not_strictly_increasing", result["all_blockers"])

    def test_future_or_expired_catalyst_is_hard_blocked(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        item = candidate()
        item["catalyst"]["published_at"] = iso(now + timedelta(hours=1))
        item["catalyst"]["realization_by"] = iso(now - timedelta(minutes=1))
        result = engine.run_shadow(payload(now, [item], {"IREN": sanitized("IREN", now)}), as_of=iso(now))
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIn("catalyst_published_in_future", result["decision"]["no_trade_reasons"])
        self.assertIn("catalyst_realization_window_invalid", result["decision"]["no_trade_reasons"])

    def test_healthy_candidate_ranks_ahead_of_high_score_hard_blocked_candidate(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        weak = candidate("WEAK")
        weak["expected_return_pct"] = 1000.0
        weak["sector_change_pct"] = -2.0
        healthy = candidate("IREN")
        result = engine.run_shadow(payload(now, [weak, healthy], {"WEAK": sanitized("WEAK", now), "IREN": sanitized("IREN", now)}), as_of=iso(now))
        self.assertEqual(result["research_top1"], "IREN")
        self.assertEqual(result["decision"]["current_action"], "ENTER_NOW")

    def test_runtime_input_cannot_loosen_authoritative_config(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        request = payload(now, [candidate()], {"IREN": sanitized("IREN", now, spread=2.0)})
        request["config"]["maximum_spread_bps"]["REGULAR"] = 10000.0
        with self.assertRaisesRegex(engine.USEquityShadowError, "config_digest_not_authoritative"):
            engine.run_shadow(request, as_of=iso(now))

    def test_deterministic_binding_and_ranking(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        request = payload(now, [candidate("IREN"), candidate("CRCL")], {"IREN": sanitized("IREN", now), "CRCL": sanitized("CRCL", now)})
        first = engine.run_shadow(copy.deepcopy(request), as_of=iso(now))
        second = engine.run_shadow(copy.deepcopy(request), as_of=iso(now))
        self.assertEqual(engine.canonical(first), engine.canonical(second))
        for field in engine.BINDING_FIELDS:
            self.assertEqual(first[field], first["decision"][field])

    def test_rank_four_to_twenty_memory_and_long_alert_are_separate(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        items = [candidate(symbol) for symbol in ("IREN", "CRCL", "NVDA", "AMD", "SMCI")]
        snaps = {item["symbol"]: sanitized(item["symbol"], now) for item in items}
        request = payload(now, items, snaps)
        request["prior_candidate_states"] = {"SMCI": {"rank": 10, "score": -100}}
        result = engine.run_shadow(request, as_of=iso(now))
        self.assertGreaterEqual(len(result["near_miss_candidates"]), 2)
        self.assertTrue(any(item["rank_acceleration"] > 0 for item in result["near_miss_candidates"] if item["symbol"] == "SMCI"))
        self.assertTrue(result["long_term_value_alerts"][0]["excluded_from_intraday_funding_source"])


class PersistenceAndIsolationTests(unittest.TestCase):
    def test_append_is_idempotent_and_conflict_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            row = {"record_id": "one", "value": 1}
            self.assertEqual(engine.append_jsonl(path, row, "record_id"), "APPENDED")
            self.assertEqual(engine.append_jsonl(path, row, "record_id"), "NO_UPDATE")
            with self.assertRaises(engine.USEquityShadowError):
                engine.append_jsonl(path, {"record_id": "one", "value": 2}, "record_id")

    def test_shadow_and_paper_cannot_enter_live_profit_attribution(self) -> None:
        base = {"schema_version": "USEquityProfitAttributionV1", "funds_weighted": True, "user_confirmed_receipts": False}
        for mode in ("shadow", "paper"):
            with self.assertRaises(engine.USEquityShadowError):
                engine.validate_profit_attribution({**base, "evidence_mode": mode})
        live = {**base, "evidence_mode": "live", "user_confirmed_receipts": True}
        self.assertIs(engine.validate_profit_attribution(live), live)

    def test_missed_opportunity_is_append_only_research_evidence(self) -> None:
        now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
        request = payload(now, [], {})
        common = engine.build_common(request, now, engine.load_config(request["config"]))
        row = {
            "schema_version": "USEquityMissedOpportunityV1",
            **common,
            "missed_opportunity_id": "missed-1",
            "symbol": "IREN",
            "classification": "ranking_error",
            "paper_roi_eligible": False,
            "real_money_roi_eligible": False,
        }
        self.assertIs(engine.validate_missed_opportunity(row), row)


if __name__ == "__main__":
    unittest.main()
