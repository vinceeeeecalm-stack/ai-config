import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("polymarket_btc_hourly_paper", ROOT / "scripts" / "polymarket_btc_hourly_paper.py")
chainlink = load("polymarket_btc_5m_chainlink_paper", ROOT / "scripts" / "polymarket_btc_5m_chainlink_paper.py")


class Btc5mChainlinkPaperTests(unittest.TestCase):
    def test_contract_identity_rejects_wrong_duration_or_source(self):
        start = datetime(2026, 7, 26, 10, tzinfo=timezone.utc)
        market = {
            "description": "Resolves using Chainlink BTC/USD. Up is greater than or equal to the opening price.",
            "resolutionSource": "https://data.chain.link/streams/btc-usd",
        }
        clear, tie_up, failures = chainlink.chainlink_rules_clear(market, start, start.replace(minute=5))
        self.assertTrue(clear)
        self.assertTrue(tie_up)
        self.assertEqual(failures, [])
        clear, _, failures = chainlink.chainlink_rules_clear(market, start, start.replace(minute=10))
        self.assertFalse(clear)
        self.assertIn("not_five_minute_contract", failures)

    def test_discovery_prefers_the_valid_five_minute_series_over_a_valid_page_sibling(self):
        chainlink.configure_core()
        at = datetime(2026, 7, 26, 10, 2, tzinfo=timezone.utc)
        common = {
            "acceptingOrders": True, "outcomes": '["Up", "Down"]', "clobTokenIds": '["up", "down"]',
            "resolutionSource": "https://data.chain.link/streams/btc-usd",
            "description": "Chainlink BTC/USD; Up is greater than or equal to the opening price.",
            "eventStartTime": "2026-07-26T10:00:00Z",
        }
        fifteen = {**common, "conditionId": "fifteen", "question": "Bitcoin Up or Down 15m", "endDate": "2026-07-26T10:15:00Z"}
        five = {**common, "conditionId": "five", "question": "Bitcoin Up or Down 5m", "endDate": "2026-07-26T10:05:00Z"}
        selected, failures = chainlink.core.discover_market(at, lambda _: [fifteen, five])
        self.assertEqual(selected["market"]["conditionId"], "five")
        self.assertEqual(failures, [])

    def test_forward_samples_use_terminal_contract_label_not_binance_proxy(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            feature = {name: 0.0 for name in core.FEATURE_NAMES}
            core.write_json(base / "state.json", {"hourly_coverage": {"condition": {"resolved": True, "resolution": "DOWN"}}})
            core.write_json(base / "snapshots" / "condition_2.json", {
                "captured_at": "2026-07-26T10:02:00+00:00", "checkpoint_minute": 2,
                "rules_clear": True, "feature_row": feature, "market": {"conditionId": "condition"},
            })
            rows = chainlink.forward_samples(base)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], 0)
        self.assertEqual(rows[0]["label_source"], "polymarket_gamma_terminal_outcome")

    def test_shadow_cycle_freezes_a_valid_chainlink_feature_snapshot(self):
        chainlink.configure_core()
        at = datetime(2026, 7, 26, 10, 2, tzinfo=timezone.utc)
        start = at.replace(minute=0)
        market = {
            "id": "m", "conditionId": "condition", "question": "Bitcoin Up or Down 5m",
            "description": "Chainlink BTC/USD; Up is greater than or equal to the opening price.",
            "resolutionSource": "https://data.chain.link/streams/btc-usd", "active": True, "closed": False,
            "acceptingOrders": True, "eventStartTime": start.isoformat(),
            "endDate": (start + timedelta(minutes=5)).isoformat(),
            "outcomes": '["Up", "Down"]', "clobTokenIds": '["up", "down"]',
        }
        book = {"bids": [{"price": .49, "size": 1000}], "asks": [{"price": .50, "size": 1000}], "min_order_size": 5}
        def fetch(url):
            if "/markets?" in url:
                return [market]
            if "/clob-markets/" in url:
                return {"fd": {"r": .07}}
            if "token_id=" in url:
                return book
            raise AssertionError(url)
        def raw_bar(stamp, minutes=1):
            value = 100000 + stamp.minute
            return [int(stamp.timestamp() * 1000), str(value), str(value + 1), str(value - 1), str(value + .5), "1",
                    int((stamp + timedelta(minutes=minutes)).timestamp() * 1000 - 1), "1", 1, "1", ".6"]
        def binance(path, params):
            if params.get("interval") == "1m":
                return [raw_bar(at - timedelta(minutes=360 - index)) for index in range(360)]
            if params.get("interval") == "1h":
                return [raw_bar(start - timedelta(hours=30 - index), 60) for index in range(30)]
            if path.endswith("depth"):
                return {"bids": [], "asks": []}
            return []
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            chainlink.core.REPORT_DIR = base / "reports"
            result = chainlink.core.cycle(base, at, fetch=fetch, fetch_binance=binance)
            snapshot = core.read_json(base / "snapshots" / "condition_2.json")
        self.assertEqual(result["phase"], "shadow")
        self.assertTrue(snapshot["rules_clear"])
        self.assertIsInstance(snapshot["feature_row"], dict)

    def test_terminal_settlement_is_explicit_about_missing_chainlink_numeric_crosscheck(self):
        policy = chainlink.load_policy()
        ledger = core.new_ledger(policy)
        ledger["accounts"]["micro_5usd"]["cash_usd"] = 2.5
        ledger["open_positions"] = [{
            "paper_trade_id": "chainlink-winner", "account": "micro_5usd", "condition_id": "condition",
            "side": "UP", "shares": 5.0, "total_entry_cost": 2.5, "token_id": "up", "fee_rate": .07,
        }]
        market = {"conditionId": "condition", "closed": True, "resolutionSource": "https://data.chain.link/streams/btc-usd",
                  "outcomes": '["Up", "Down"]', "clobTokenIds": '["up", "down"]', "outcomePrices": '["1", "0"]'}
        closed = chainlink.settle_chainlink_positions(ledger, datetime.now(timezone.utc), lambda _: [market])
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["net_pnl_usd"], 2.5)
        self.assertEqual(closed[0]["settlement_evidence"]["independent_chainlink_price_check"], "unavailable_not_substituted")

    def test_no_forward_record_can_never_be_called_high_win_rate_ready(self):
        policy = chainlink.load_policy()
        audit = core.audit_ledger(core.new_ledger(policy), {"hourly_coverage": {}}, None, policy)
        readiness = audit["recommendation_readiness"]
        self.assertEqual(readiness["status"], "paper_only")
        self.assertIn("insufficient_forward_paper_trades", readiness["blockers"])
        self.assertIn("forward_win_rate_confidence_too_low", readiness["blockers"])


if __name__ == "__main__":
    unittest.main()
