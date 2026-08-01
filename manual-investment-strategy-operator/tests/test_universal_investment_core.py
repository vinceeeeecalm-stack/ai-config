import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "universal_investment_core.py"
SPEC = importlib.util.spec_from_file_location("universal_core", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def fixture(name="crypto-enter", symbol="BTC", asset="crypto", horizon="tactical_1_7d", price=100, fair=130, entry=(99, 101)):
    return M._shadow_case(name, symbol, asset, horizon, price=price, fair=fair, entry=entry)


def account(cash=0, authority="user_confirmed", settled=True):
    return {"authority": authority, "deployable_cash": cash, "settled": settled, "max_manual_amount": cash}


class UniversalInvestmentCoreTest(unittest.TestCase):
    def test_crypto_and_equity_adapters_require_distinct_fundamentals(self):
        crypto, _, _ = fixture()
        equity, _, _ = fixture("equity", "APLD", "us_equity", price=100, fair=130)
        self.assertEqual(M.validate_universal_case(crypto)["asset_class"], "crypto")
        self.assertEqual(M.validate_universal_case(equity)["asset_class"], "us_equity")
        broken = copy.deepcopy(crypto)
        broken["fundamentals"].pop("supply_dilution")
        with self.assertRaisesRegex(M.InvestmentContractError, "supply_dilution"):
            M.validate_universal_case(broken)

    def test_longterm_adapter_forbids_tactical_stop_and_exit_fields(self):
        case, _, _ = fixture("long", "COIN", "us_equity", "longterm", price=100, fair=180, entry=(90, 110))
        case["plan"]["stop_price"] = 80
        with self.assertRaisesRegex(M.InvestmentContractError, "tactical_exit_fields_forbidden"):
            M.validate_universal_case(case)

    def test_regression_is_internal_evidence_never_formal_action(self):
        _, regression, _ = fixture()
        regression["formal_action_eligible"] = True
        with self.assertRaisesRegex(M.InvestmentContractError, "cannot_be_formal_action"):
            M.validate_regression_evidence(regression)

    def test_paper_only_is_rejected_as_formal_user_action(self):
        case, regression, live = fixture()
        decision = M.build_live_decision(
            cases=[case], regressions=[regression], live_evidence_by_symbol={"BTC": live},
            account=account(0), decided_at="2030-01-01T00:00:40Z",
        )
        decision["current_action"] = "PAPER_ONLY"
        with self.assertRaisesRegex(M.InvestmentContractError, "formal_action_invalid"):
            M.validate_live_decision(decision)

    def test_same_binding_and_inputs_rank_deterministically(self):
        case, regression, _ = fixture()
        first = M.rank_investment_cases([case], [regression])
        second = M.rank_investment_cases([copy.deepcopy(case)], [copy.deepcopy(regression)])
        self.assertEqual(first, second)
        self.assertEqual(first["research_top1"], "BTC")

    def test_binding_mismatch_is_rejected(self):
        case, regression, _ = fixture()
        regression["config_digest"] = "f" * 64
        with self.assertRaisesRegex(M.InvestmentContractError, "regression_binding_mismatch"):
            M.rank_investment_cases([case], [regression])

    def test_zero_cash_preserves_enter_now_market_judgment(self):
        case, regression, live = fixture()
        decision = M.build_live_decision(
            cases=[case], regressions=[regression], live_evidence_by_symbol={"BTC": live},
            account=account(0), decided_at="2030-01-01T00:00:40Z",
        )
        self.assertEqual(decision["current_action"], "ENTER_NOW")
        self.assertEqual(decision["account_execution"], "NO_DEPLOY_CASH")
        self.assertEqual(decision["executable_amount"], 0)
        self.assertFalse(decision["paper_action_exposed"])

    def test_wait_has_specific_current_plan_and_never_says_paper_only(self):
        case, regression, live = fixture("wait", "APLD", "us_equity", price=100, fair=140, entry=(90, 95))
        decision = M.build_live_decision(
            cases=[case], regressions=[regression], live_evidence_by_symbol={"APLD": live},
            account=account(1000), decided_at="2030-01-01T00:00:40Z",
        )
        self.assertEqual(decision["current_action"], "WAIT_FOR_ENTRY")
        for field in ("entry_low", "entry_high", "entry_trigger", "valid_until", "target_1", "target_2", "stop_price", "latest_exit_at"):
            self.assertIn(field, decision["decision_card"])
        self.assertNotIn("PAPER", decision["current_action"])

    def test_stale_live_evidence_is_no_trade_with_no_fake_card(self):
        case, regression, live = fixture("stale", "ETH")
        live["quotes"][0]["as_of"] = "2029-12-31T23:00:00Z"
        decision = M.build_live_decision(
            cases=[case], regressions=[regression], live_evidence_by_symbol={"ETH": live},
            account=account(1000), decided_at="2030-01-01T00:00:40Z",
        )
        self.assertEqual(decision["current_action"], "NO_TRADE")
        self.assertIsNone(decision["decision_card"])
        self.assertIn("stale_live_price", decision["no_trade_reasons"])
        self.assertEqual(decision["research_top1"], "ETH")

    def test_paper_positive_cannot_overcome_expired_live_evidence(self):
        case, regression, live = fixture("paper-expired", "SOL")
        regression["mode"] = "forward_paper"
        regression["conservative_ev_pct"] = 30
        live["current_plan"]["valid_until"] = "2029-12-31T23:59:00Z"
        live["current_plan_digest"] = M.digest(live["current_plan"])
        decision = M.build_live_decision(
            cases=[case], regressions=[regression], live_evidence_by_symbol={"SOL": live},
            account=account(1000), decided_at="2030-01-01T00:00:40Z",
        )
        self.assertEqual(decision["current_action"], "NO_TRADE")
        self.assertIn("rebuilt_tactical_plan_expired", decision["no_trade_reasons"])

    def test_candidate_local_source_failure_does_not_kill_other_candidate(self):
        failed, failed_reg, _ = fixture("failed-source", "BAD", price=50, fair=200)
        failed["source_failures"] = ["one local source failed"]
        good, good_reg, good_live = fixture("good-source", "GOOD", price=100, fair=130)
        for field in M.BINDING_FIELDS:
            good[field] = failed[field]
            good_reg[field] = failed[field]
            good_live[field] = failed[field]
            good_live["current_plan"][field] = failed[field]
        good_live["current_plan_digest"] = M.digest(good_live["current_plan"])
        decision = M.build_live_decision(
            cases=[failed, good], regressions=[failed_reg, good_reg], live_evidence_by_symbol={"GOOD": good_live},
            account=account(0), decided_at="2030-01-01T00:00:40Z",
        )
        self.assertEqual(decision["research_top1"], "GOOD")
        self.assertEqual(decision["current_action"], "ENTER_NOW")

    def test_longterm_legacy_estimate_is_research_only_and_data_degraded(self):
        case, regression, live = fixture("long-degraded", "COIN", "us_equity", "longterm", price=100, fair=180, entry=(90, 110))
        decision = M.build_live_decision(
            cases=[case], regressions=[regression], live_evidence_by_symbol={"COIN": live},
            account=account(0, authority="legacy_estimate"), decided_at="2030-01-01T00:00:40Z",
        )
        self.assertEqual(decision["research_top1"], "COIN")
        self.assertEqual(decision["current_action"], "NO_TRADE")
        status = M.build_investment_outcome_status(
            delivery_status="RUNTIME_VERIFIED", decision=decision, live_profit_status="UNMEASURED",
            long_path_status="DATA_DEGRADED", regression_status="PASS", as_of="2030-01-01T00:00:40Z",
        )
        self.assertEqual(status["delivery_status"], "RUNTIME_VERIFIED")
        self.assertEqual(status["account_execution"], "NO_DEPLOY_CASH")
        self.assertEqual(status["live_profit_status"], "UNMEASURED")
        self.assertEqual(status["long_path_status"], "DATA_DEGRADED")
        self.assertEqual(status["business_outcome_status"], "EVIDENCE_PENDING")
        self.assertTrue(status["regression_status"]["internal_only"])

    def test_shadow_suite_covers_four_tasks_and_fault_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = M.run_shadow_suite(Path(temp_dir))
            self.assertTrue(package["passed"])
            self.assertEqual(set(package["tasks"]), {"crypto-enter", "crypto-no-trade", "equity-wait", "long-data-degraded"})
            self.assertEqual(package["fault_recovery"]["stale_action"], "NO_TRADE")
            self.assertEqual(package["fault_recovery"]["recovered_action"], "ENTER_NOW")
            self.assertTrue((Path(temp_dir) / "universal_investment_shadow_suite_v1.json").exists())


if __name__ == "__main__":
    unittest.main()
