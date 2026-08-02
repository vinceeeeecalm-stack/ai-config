import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "tactical_evidence_ledger.py"
SPEC = importlib.util.spec_from_file_location("tactical_evidence_ledger", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def observation(**changes):
    record = {
        "schema_version": "ObservationSampleV1",
        "observation_id": "obs-1",
        "candidate_id": "EULUSDT",
        "setup_id": "setup-1",
        "symbol": "EULUSDT",
        "asset_class": "crypto",
        "request_mode": "tactical_1_7d",
        "strategy_family": "impulse_capture",
        "strategy_version": "v4",
        "snapshot_id": "snapshot-1",
        "config_digest": DIGEST_A,
        "source_digest": DIGEST_B,
        "observed_at": "2030-01-01T00:00:00Z",
        "price": 1.5,
        "price_as_of": "2030-01-01T00:00:00Z",
        "rank": 1,
        "accepted": False,
        "rejection_reasons": ["negative_conservative_ev"],
        "entry_trigger_state": "not_triggered",
        "next_check_at": "2030-01-01T01:00:00Z",
        "review_due_at": "2030-01-08T00:00:00Z",
        "evidence_ids": ["price-1", "history-1"],
        "formal_action_eligible": False,
        "paper_entry_eligible": False,
        "paper_live_separated": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    record.update(changes)
    return record


def receipt(event, price, when):
    return {
        "schema_version": "PaperExecutionReceiptV1",
        "receipt_id": f"receipt-{event}",
        "baseline_id": "baseline-1",
        "trade_id": "trade-1",
        "symbol": "EULUSDT",
        "request_mode": "tactical_1_7d",
        "evidence_mode": "paper",
        "strategy_version": "v4",
        "snapshot_id": "snapshot-1",
        "event": event,
        "quantity": 10,
        "price": price,
        "fee_usd": 0.1,
        "slippage_usd": 0.05,
        "executed_at": when,
        "live_orders_enabled": False,
    }


def trade_sample(**changes):
    entry = receipt("entry", 1.5, "2030-01-01T01:00:00Z")
    exit_receipt = receipt("exit", 1.65, "2030-01-03T01:00:00Z")
    invested = 15.0
    pnl = 10 * (1.65 - 1.5) - 0.3
    record = {
        "schema_version": "TradeSampleV1",
        "trade_sample_id": "sample-1",
        "observation_id": "obs-1",
        "candidate_id": "EULUSDT",
        "symbol": "EULUSDT",
        "asset_class": "crypto",
        "request_mode": "tactical_1_7d",
        "evidence_mode": "paper",
        "strategy_version": "v4",
        "snapshot_id": "snapshot-1",
        "config_digest": DIGEST_A,
        "source_digest": DIGEST_B,
        "entry_receipt": entry,
        "exit_receipt": exit_receipt,
        "outcome": "hit",
        "mfe_pct": 12.0,
        "mae_pct": -3.0,
        "holding_hours": 48.0,
        "net_pnl_usd": pnl,
        "net_roi_pct": pnl / invested * 100,
        "formal_action_eligible": False,
        "real_money_roi_eligible": False,
        "business_ready_eligible": False,
        "paper_live_separated": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    record.update(changes)
    return record


class TacticalEvidenceLedgerTests(unittest.TestCase):
    def test_append_is_idempotent_and_collision_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "observations.jsonl"
            item = observation()
            self.assertEqual(M.append_observation(path, item), "APPENDED")
            self.assertEqual(M.append_observation(path, item), "NO_UPDATE")
            self.assertEqual(len(path.read_text().splitlines()), 1)
            with self.assertRaises(M.EvidenceContractError):
                M.append_observation(path, observation(price=1.6))

    def test_rejected_observation_cannot_be_empty(self):
        with self.assertRaises(M.EvidenceContractError):
            M.validate_observation(observation(rejection_reasons=[]))

    def test_trade_sample_is_paper_only_and_bound_to_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            observations = Path(tmp) / "observations.jsonl"
            trades = Path(tmp) / "trades.jsonl"
            eligible = observation(accepted=True, rejection_reasons=[], paper_entry_eligible=True)
            M.append_observation(observations, eligible)
            item = trade_sample()
            self.assertEqual(M.append_trade_sample(trades, item, observation_ledger=observations), "APPENDED")
            self.assertEqual(M.append_trade_sample(trades, item, observation_ledger=observations), "NO_UPDATE")
            summary = M.summarize(observations, trades)
            self.assertEqual(summary["observation_count"], 1)
            self.assertEqual(summary["paper_trade_sample_count"], 1)
            self.assertEqual(summary["real_money_roi_sample_count"], 0)

    def test_live_or_unknown_horizon_trade_is_rejected(self):
        eligible = observation(accepted=True, rejection_reasons=[], paper_entry_eligible=True)
        with self.assertRaises(M.EvidenceContractError):
            M.validate_trade_sample(trade_sample(evidence_mode="live"), eligible)
        bad = trade_sample()
        bad["entry_receipt"]["request_mode"] = "UNKNOWN"
        with self.assertRaises(M.EvidenceContractError):
            M.validate_trade_sample(bad, eligible)

    def test_cross_snapshot_trade_is_rejected(self):
        eligible = observation(accepted=True, rejection_reasons=[], paper_entry_eligible=True)
        with self.assertRaises(M.EvidenceContractError):
            M.validate_trade_sample(trade_sample(snapshot_id="different"), eligible)

    def test_rejected_observation_cannot_create_trade_sample(self):
        with self.assertRaises(M.EvidenceContractError):
            M.validate_trade_sample(trade_sample(), observation())

    def test_non_allowlisted_fields_are_rejected(self):
        with self.assertRaises(M.EvidenceContractError):
            M.validate_observation(observation(api_token="forbidden"))

    def test_eul_no_trade_replay_adds_only_observation(self):
        fixture = Path(__file__).parent / "fixtures" / "eul_no_trade_observation_v4.json"
        with tempfile.TemporaryDirectory() as tmp:
            observations = Path(tmp) / "observations.jsonl"
            trades = Path(tmp) / "trades.jsonl"
            item = json.loads(fixture.read_text(encoding="utf-8"))
            self.assertEqual(M.append_observation(observations, item), "APPENDED")
            summary = M.summarize(observations, trades)
            self.assertEqual(summary["symbols"], ["EULUSDT"])
            self.assertEqual(summary["rejected_observation_count"], 1)
            self.assertEqual(summary["paper_trade_sample_count"], 0)
            self.assertEqual(summary["real_money_roi_sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
