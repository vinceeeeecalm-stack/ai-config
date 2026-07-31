import copy
import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_btc_hourly_paper.py"
spec = importlib.util.spec_from_file_location("polymarket_btc_hourly_paper", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class BtcHourlyPaperTests(unittest.TestCase):
    def setUp(self):
        self.policy = module.load_policy()

    @staticmethod
    def book(price=.50, minimum=5):
        return {"bids": [{"price": price - .01, "size": 1000}], "asks": [{"price": price, "size": 1000}], "min_order_size": minimum}

    def test_vwap_fee_and_minimum_order_are_all_in_costed(self):
        fill = module.buy_for_budget(self.book(), 3.0, .07, 5)
        self.assertTrue(fill["fillable"])
        self.assertGreaterEqual(fill["shares"], 5)
        self.assertLessEqual(fill["total_entry_cost"], 3)
        self.assertEqual(module.fee_usd(100, .50, .07), 1.75)
        self.assertFalse(module.buy_for_budget(self.book(), 1.0, .07, 5)["fillable"])

    def test_up_down_tokens_and_terminal_result_are_not_yes_no_assumptions(self):
        market = {"outcomes": '["Up", "Down"]', "clobTokenIds": '["up-token", "down-token"]', "closed": True,
                  "outcomePrices": '["1", "0"]'}
        self.assertEqual(module.outcomes(market)[0], {"UP": "up-token", "DOWN": "down-token"})
        self.assertEqual(module.terminal_outcome(market), "UP")

    def test_calibration_error_measures_probability_bins_not_direction_misses(self):
        values = [(.8, 1), (.8, 1), (.2, 0), (.2, 0)]
        self.assertAlmostEqual(module.expected_calibration_error(values), .2)

    def test_entry_selects_only_side_with_fee_adjusted_edge(self):
        ledger = module.new_ledger(self.policy)
        decision = {"status": "ok", "p_up": .80, "p_up_low": .70, "p_down": .20, "p_down_low": .10, "model_version": "fixture"}
        market = {"conditionId": "c1", "fee_rate": .07}
        tokens = {"UP": "u", "DOWN": "d"}
        entry = module.choose_entry(decision, {"u": self.book(.50), "d": self.book(.50)}, tokens, market, self.policy, ledger, datetime.now(timezone.utc) + timedelta(minutes=20))
        self.assertEqual(entry["side"], "UP")
        self.assertGreater(entry["edge"], .05)

    def test_ambiguous_rules_and_missing_bid_are_never_entry_eligible(self):
        start = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
        clear, _, failures = module.rules_clear({"description": "Uses a price feed", "resolutionSource": "x"}, start, start + timedelta(hours=1))
        self.assertFalse(clear)
        self.assertIn("binance_btcusdt_rule_not_proven", failures)
        self.assertFalse(module.buy_for_budget({"asks": [{"price": .5, "size": 100}], "bids": []}, 5, .07, 5)["fillable"])

    def test_cycle_writes_audit_snapshot_but_stays_shadow(self):
        at = datetime(2026, 7, 24, 12, 15, tzinfo=timezone.utc); start = at.replace(minute=0)
        market = {"id": "m1", "conditionId": "c1", "question": "Bitcoin Up or Down", "description": "Resolves using Binance BTC/USDT close greater than or equal to open.",
                  "resolutionSource": "https://binance.com", "active": True, "closed": False, "acceptingOrders": True,
                  "startDate": start.isoformat(), "endDate": (start + timedelta(hours=1)).isoformat(), "outcomes": '["Up","Down"]', "clobTokenIds": '["u","d"]'}
        def fetch(url):
            if "/markets?" in url: return [market]
            if "/clob-markets/" in url: return {"fd": {"r": .07}}
            if "token_id=u" in url: return self.book(.50)
            if "token_id=d" in url: return self.book(.50)
            raise AssertionError(url)
        def raw_bar(stamp, minutes=1):
            value = 100000 + stamp.minute
            return [int(stamp.timestamp()*1000), str(value), str(value+1), str(value-1), str(value+.5), "1", int((stamp+timedelta(minutes=minutes)).timestamp()*1000-1), "1", 1, "1", ".6"]
        def binance(path, params):
            if params.get("interval") == "1m": return [raw_bar(at - timedelta(minutes=360-index)) for index in range(360)]
            if params.get("interval") == "1h": return [raw_bar(at.replace(minute=0) - timedelta(hours=30-index), 60) for index in range(30)]
            if path.endswith("depth"): return {"bids": [], "asks": []}
            return []
        with tempfile.TemporaryDirectory() as temp:
            result = module.cycle(Path(temp), at, fetch=fetch, fetch_binance=binance)
            self.assertEqual(result["phase"], "shadow")
            self.assertEqual(result["action"], "PASS")
            self.assertTrue((Path(temp) / "snapshots" / "c1_10.json").exists())
            self.assertTrue(result["protected_artifacts_unchanged"])

    def test_settlement_is_disputed_when_binance_conflicts_with_gamma(self):
        ledger = module.new_ledger(self.policy)
        ledger["open_positions"] = [{"paper_trade_id": "t", "account": "micro_5usd", "condition_id": "c", "market_start": "2026-07-24T12:00:00+00:00", "side": "UP", "shares": 5,
                                     "total_entry_cost": 2.5, "fee_rate": .07, "token_id": "u"}]
        original = module.get_binance
        module.get_binance = lambda *_: [[1721822400000, "100", "101", "99", "99", "1", 1721825999999, "1", 1, "1", ".5"]]
        try:
            result = module.settle_open(ledger, datetime.now(timezone.utc), lambda _: [{"conditionId": "c", "closed": True, "outcomes": '["Up","Down"]', "clobTokenIds": '["u","d"]', "outcomePrices": '["1","0"]'}])
        finally:
            module.get_binance = original
        self.assertEqual(result, [])
        self.assertEqual(ledger["open_positions"][0]["settlement_status"], "DISPUTED_PENDING")

    def test_winning_settlement_is_exactly_reconciled_to_cash_and_pnl(self):
        ledger = module.new_ledger(self.policy); ledger["accounts"]["micro_5usd"]["cash_usd"] = 2.5
        ledger["open_positions"] = [{"paper_trade_id": "winner", "account": "micro_5usd", "condition_id": "c", "market_start": "2026-07-24T12:00:00+00:00", "side": "UP", "shares": 5,
                                     "total_entry_cost": 2.5, "fee_rate": .07, "token_id": "u"}]
        original = module.get_binance
        module.get_binance = lambda *_: [[1721822400000, "100", "101", "99", "101", "1", 1721825999999, "1", 1, "1", ".5"]]
        try:
            result = module.settle_open(ledger, datetime.now(timezone.utc), lambda _: [{"conditionId": "c", "closed": True, "outcomes": '["Up","Down"]', "clobTokenIds": '["u","d"]', "outcomePrices": '["1","0"]'}])
        finally:
            module.get_binance = original
        self.assertEqual(len(result), 1)
        self.assertEqual(ledger["closed_positions"][0]["net_pnl_usd"], 2.5)
        self.assertEqual(ledger["accounts"]["micro_5usd"]["cash_usd"], 7.5)


if __name__ == "__main__":
    unittest.main()
