import copy
import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "tactical_research_handoff.py"
SPEC = importlib.util.spec_from_file_location("tactical_research_handoff", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def bind(tag="a"):
    return {
        "snapshot_id": f"snapshot-{tag}",
        "strategy_version": "impulse-capture-tactical-v6",
        "config_digest": "a" * 64,
        "source_digest": "b" * 64,
    }


def history(symbol="GOODUSDT", rank=1, ev=4.0):
    return {
        "rank": rank,
        "symbol": symbol,
        "setup_quality_score": 70.0 - rank,
        "sample_size": 40,
        "out_of_sample_win_rate_interval_pct": [45.0, 70.0],
        "conservative_expected_value_pct": ev,
        "expected_return_pct": 3.0,
        "profit_factor": 1.5,
        "max_drawdown_pct": -10.0,
        "reward_risk_ratio": 2.0,
        "walk_forward_positive_windows": 4,
        "walk_forward_total_windows": 5,
        "evidence_ids": [f"history:{symbol}"],
    }


def signal(symbol="GOODUSDT", *, price=100.0, complete=True):
    return {
        "symbol": symbol,
        "current_price": price,
        "mid": price,
        "stage": "trigger" if complete else "early_watch",
        "breakout_close": complete,
        "breakout_level": 99.0 if complete else 105.0,
        "resistance": 99.0 if complete else 105.0,
        "support": 97.0,
        "spread_bps": 2.0,
        "depth_1pct_bid_usd": 100000.0,
        "depth_1pct_ask_usd": 120000.0,
        "historical_comparison": {
            "sample_size": 40,
            "conservative_expected_value_pct": 4.0,
            "profit_factor": 1.5,
            "max_drawdown_pct": -10.0,
            "reward_risk_ratio": 2.0,
            "walk_forward_positive_windows": 4,
            "walk_forward_total_windows": 5,
            "untouched_holdout": True,
            "lookahead_free": True,
            "friction_pct": 0.2,
            "target_return_pct": 10.0,
            "stop_loss_pct": 5.0,
            "horizon_bars": 5,
            "evidence_ids": [f"history:{symbol}"],
        },
    }


def scanner(symbol="GOODUSDT", *, tag="a", complete=True, ev=4.0):
    return {
        "run_type": "impulse_capture_scan",
        "request_mode": "tactical_1_7d",
        **bind(tag),
        "captured_at": "2030-01-01T00:00:20Z",
        "live_orders_enabled": False,
        "private_api_used": False,
        "signals": [signal(symbol, complete=complete)],
        "discovery_top3": [{"symbol": symbol, "current_price": 100.0}],
        "ranked_historical_comparison": {
            "schema_version": "ranked-historical-comparison-v1",
            "primary_symbol": symbol,
            "rows": [history(symbol, ev=ev)],
        },
    }


def evidence(evidence_id, category, source_type, summary="verified evidence"):
    return {
        "evidence_id": evidence_id,
        "category": category,
        "source_type": source_type,
        "url_or_provider": f"https://example.com/{evidence_id}",
        "as_of": "2030-01-01T00:00:10Z",
        "status": "verified",
        "public": True,
        "summary": summary,
    }


def dossier(request, *, catalyst_verified=True):
    items = [
        evidence("val-official", "valuation", "official"),
        evidence("val-onchain", "valuation", "onchain_public"),
        evidence("cat-official", "catalyst", "official"),
        evidence("fund-onchain", "fundamentals", "onchain_public"),
        evidence("fund-independent", "fundamentals", "independent_public"),
        evidence("risk-official", "risk", "official"),
    ]
    role_status = {role: "PASS" for role in M.ROLE_IDS}
    if not catalyst_verified:
        role_status["official_catalyst"] = "BLOCKED"
        items[2]["status"] = "blocked"
    fundamentals = {
        field: {
            "status": "verified",
            "summary": f"{field} verified",
            "evidence_ids": ["fund-onchain", "fund-independent"],
        }
        for field in M.FUNDAMENTAL_FIELDS
    }
    return {
        "schema_version": "TacticalResearchDossierV1",
        "dossier_id": "dossier-good",
        "request_id": request["request_id"],
        "provisional_snapshot_id": request["snapshot_id"],
        "symbol": request["symbol"],
        "prepared_at": "2030-01-01T00:00:25Z",
        "valid_until": "2030-01-01T06:00:00Z",
        "roles": [
            {
                "role_id": role,
                "status": role_status[role],
                "evidence_ids": [item["evidence_id"] for item in items],
                "blockers": [] if role_status[role] == "PASS" else ["no verified future event"],
            }
            for role in M.ROLE_IDS
        ],
        "evidence_items": items,
        "field_evidence": {
            "fair_value": ["val-official", "val-onchain"],
            "catalyst": ["cat-official"],
            "fundamentals": ["fund-onchain", "fund-independent"],
            "downside": ["risk-official"],
            "risk_challenge": ["risk-official"],
        },
        "valuation_method_class": "adoption_value_capture_scenario",
        "fair_value": {
            "low": 115.0,
            "base": 130.0,
            "high": 150.0,
            "method": "adoption and fee capture scenario",
            "uncertainty": "high",
            "assumptions": ["adoption grows", "supply dilution stays bounded"],
        },
        "catalyst": {
            "summary": "officially scheduled network event" if catalyst_verified else "no verified event",
            "verified": catalyst_verified,
            "realization_by": "2030-01-03T00:00:00Z",
            "time_certainty": 0.8 if catalyst_verified else 0.0,
            "invalidation": "official schedule is cancelled",
        },
        "downside": {
            "expected_drawdown_pct": 5.0,
            "invalidation_conditions": ["catalyst cancelled", "support lost"],
        },
        "fundamentals": fundamentals,
        "risk_challenge": {
            "status": "PASS",
            "critical_blockers": [],
            "approved_max_loss_pct": 0.5,
        },
        "source_failures": [],
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def certification(scan, *, complete=True, second_price=100.2, certified_at="2030-01-01T00:00:40Z"):
    symbol = scan["signals"][0]["symbol"]
    return {
        "schema_version": "TacticalMarketCertificationV1",
        "certification_id": "cert-good",
        "binding": bind(scan["snapshot_id"].split("snapshot-", 1)[1]),
        "symbol": symbol,
        "certified_at": certified_at,
        "scan_started_at": "2030-01-01T00:00:20Z",
        "scan_duration_seconds": 20.0,
        "scanner_signal_digest": M.core.digest(scan["signals"][0]),
        "current_signal_complete": complete,
        "quotes": [
            {
                "evidence_id": "quote-a",
                "source": "public-a",
                "public": True,
                "price": 100.0,
                "as_of": "2030-01-01T00:00:30Z",
                "latency_seconds": 2.0,
            },
            {
                "evidence_id": "quote-b",
                "source": "public-b",
                "public": True,
                "price": second_price,
                "as_of": "2030-01-01T00:00:32Z",
                "latency_seconds": 3.0,
            },
        ],
        "liquidity_status": "verified",
        "data_quality_status": "verified",
        "risk_gate_pass": True,
        "human_confirmation_required": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


class TacticalResearchHandoffTest(unittest.TestCase):
    def test_request_is_deterministic_and_contains_one_top1(self):
        scan = scanner()
        first = M.build_research_request(scan, M.safe_zero_account())
        second = M.build_research_request(copy.deepcopy(scan), M.safe_zero_account())
        self.assertEqual(first, second)
        self.assertEqual(first["symbol"], "GOODUSDT")
        self.assertEqual(len(first["research_roles"]), 4)
        self.assertTrue(first["deep_research_recommended"])
        self.assertFalse(first["formal_action_eligible"])

    def test_negative_regression_skips_expensive_research_and_is_no_trade(self):
        result = M.run_handoff(scanner=scanner(ev=-1.0))
        self.assertEqual(result["handoff_status"], "PRE_RESEARCH_GATES_FAILED")
        self.assertIn("non_positive_conservative_ev", result["blockers"])
        self.assertFalse(result["research_request"]["deep_research_recommended"])
        self.assertEqual(result["current_action"], "NO_TRADE")
        self.assertIsNone(result["decision_card"])

    def test_missing_dossier_is_explicit_evidence_pending(self):
        result = M.run_handoff(scanner=scanner())
        self.assertEqual(result["handoff_status"], "EVIDENCE_PENDING")
        self.assertFalse(result["research_by_symbol_populated"])
        self.assertEqual(result["current_action"], "NO_TRADE")

    def test_complete_trigger_produces_enter_now_but_zero_cash_amount_stays_zero(self):
        scan = scanner(complete=True)
        request = M.build_research_request(scan, M.safe_zero_account())
        result = M.run_handoff(
            scanner=scan,
            dossier=dossier(request),
            market_certification=certification(scan, complete=True),
        )
        self.assertEqual(result["handoff_status"], "ARBITRATED_ACTION")
        self.assertEqual(result["current_action"], "ENTER_NOW")
        self.assertEqual(result["account_execution"], "NO_DEPLOY_CASH")
        self.assertEqual(result["manual_result"]["decision"]["executable_amount"], 0.0)
        self.assertIsNotNone(result["decision_card"])
        self.assertTrue(result["research_by_symbol_populated"])

    def test_incomplete_signal_produces_specific_wait_card(self):
        scan = scanner(complete=False)
        request = M.build_research_request(scan, M.safe_zero_account())
        result = M.run_handoff(
            scanner=scan,
            dossier=dossier(request),
            market_certification=certification(scan, complete=False),
        )
        self.assertEqual(result["current_action"], "WAIT_FOR_ENTRY")
        self.assertGreater(result["decision_card"]["entry_low"], 100.0)
        self.assertIn("valid_until", result["decision_card"])

    def test_blocked_catalyst_cannot_create_fake_trade_card(self):
        scan = scanner()
        request = M.build_research_request(scan, M.safe_zero_account())
        result = M.run_handoff(
            scanner=scan,
            dossier=dossier(request, catalyst_verified=False),
            market_certification=certification(scan),
        )
        self.assertEqual(result["handoff_status"], "EVIDENCE_BLOCKED")
        self.assertIn("verified_1_7d_catalyst_missing", result["blockers"])
        self.assertEqual(result["current_action"], "NO_TRADE")
        self.assertIsNone(result["decision_card"])

    def test_price_path_evidence_cannot_be_used_as_fair_value(self):
        scan = scanner()
        request = M.build_research_request(scan, M.safe_zero_account())
        data = dossier(request)
        data["evidence_items"][0]["category"] = "market"
        result = M.run_handoff(
            scanner=scan,
            dossier=data,
            market_certification=certification(scan),
        )
        self.assertIn("market_path_cannot_prove_fair_value", result["blockers"])
        self.assertEqual(result["current_action"], "NO_TRADE")

    def test_top1_change_rejects_old_dossier_and_creates_new_request(self):
        old = scanner("OLDUSDT", tag="old")
        old_request = M.build_research_request(old, M.safe_zero_account())
        final = scanner("NEWUSDT", tag="new")
        result = M.run_handoff(
            scanner=final,
            dossier=dossier(old_request),
            market_certification=certification(final),
            provisional_request=old_request,
        )
        self.assertEqual(result["handoff_status"], "TOP1_CHANGED")
        self.assertEqual(result["research_request"]["symbol"], "NEWUSDT")
        self.assertEqual(result["current_action"], "NO_TRADE")

    def test_same_top1_can_rebind_slow_dossier_to_final_fresh_snapshot(self):
        provisional = scanner("GOODUSDT", tag="provisional")
        provisional_request = M.build_research_request(provisional, M.safe_zero_account())
        final = scanner("GOODUSDT", tag="final")
        result = M.run_handoff(
            scanner=final,
            dossier=dossier(provisional_request),
            market_certification=certification(final),
            provisional_request=provisional_request,
        )
        self.assertEqual(result["current_action"], "ENTER_NOW")
        self.assertEqual(result["snapshot_id"], "snapshot-final")
        research = result["production_input"]["research_by_symbol"]["GOODUSDT"]
        self.assertEqual(research["binding"]["snapshot_id"], "snapshot-final")
        self.assertEqual(result["provisional_research_request_id"], provisional_request["request_id"])

    def test_stale_final_scanner_is_certification_blocked(self):
        scan = scanner()
        request = M.build_research_request(scan, M.safe_zero_account())
        cert = certification(scan, certified_at="2030-01-01T00:05:00Z")
        result = M.run_handoff(scanner=scan, dossier=dossier(request), market_certification=cert)
        self.assertEqual(result["handoff_status"], "CERTIFICATION_BLOCKED")
        self.assertIn("final_scanner_not_fresh_at_certification", result["blockers"])
        self.assertEqual(result["current_action"], "NO_TRADE")

    def test_dual_source_conflict_is_blocked(self):
        scan = scanner()
        request = M.build_research_request(scan, M.safe_zero_account())
        result = M.run_handoff(
            scanner=scan,
            dossier=dossier(request),
            market_certification=certification(scan, second_price=102.0),
        )
        self.assertEqual(result["handoff_status"], "CERTIFICATION_BLOCKED")
        self.assertIn("live_price_conflict", result["blockers"])
        self.assertEqual(result["current_action"], "NO_TRADE")

    def test_nonblocking_local_source_failure_does_not_poison_complete_candidate(self):
        scan = scanner()
        request = M.build_research_request(scan, M.safe_zero_account())
        data = dossier(request)
        data["source_failures"] = [
            {
                "source": "optional-social-source",
                "field": "social_context",
                "blocking": False,
                "reason": "optional source timed out",
            }
        ]
        result = M.run_handoff(
            scanner=scan,
            dossier=data,
            market_certification=certification(scan),
        )
        self.assertEqual(result["current_action"], "ENTER_NOW")
        self.assertEqual(result["blockers"], [])

    def test_blocking_source_failure_is_candidate_local_no_trade(self):
        scan = scanner()
        request = M.build_research_request(scan, M.safe_zero_account())
        data = dossier(request)
        data["source_failures"] = [
            {
                "source": "official-calendar",
                "field": "catalyst",
                "blocking": True,
                "reason": "official source unavailable",
            }
        ]
        result = M.run_handoff(
            scanner=scan,
            dossier=data,
            market_certification=certification(scan),
        )
        self.assertEqual(result["current_action"], "NO_TRADE")
        self.assertIn("candidate_source_failure:catalyst:official-calendar", result["blockers"])

    def test_same_complete_inputs_are_deterministic(self):
        scan = scanner()
        request = M.build_research_request(scan, M.safe_zero_account())
        data = dossier(request)
        cert = certification(scan)
        first = M.run_handoff(scanner=scan, dossier=data, market_certification=cert)
        second = M.run_handoff(
            scanner=copy.deepcopy(scan),
            dossier=copy.deepcopy(data),
            market_certification=copy.deepcopy(cert),
        )
        self.assertEqual(first, second)
        self.assertFalse(first["manual_result"]["decision"]["paper_action_exposed"])
        self.assertEqual(first["live_profit_status"], "UNMEASURED")


if __name__ == "__main__":
    unittest.main()
