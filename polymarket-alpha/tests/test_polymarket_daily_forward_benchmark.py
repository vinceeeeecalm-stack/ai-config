import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_daily_forward_benchmark.py"
spec = importlib.util.spec_from_file_location("polymarket_daily_forward_benchmark", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)

def ledger(count=2):
    events = []
    for index in range(count):
        condition = f"c{index}"; scan = f"s{index}"
        events.append({"event_id": scan, "event_type": "scan_observation", "condition_id": condition,
                       "market_family": "fixture", "recorded_at": f"2026-07-{index + 1:02d}T00:00:00Z"})
        outcome = 1.0 if index % 2 == 0 else 0.0; market_p = .6 if outcome else .4; model_p = .8 if outcome else .2
        events.append({"event_id": f"r{index}", "event_type": "resolution_observation", "condition_id": condition,
            "market_id": f"m{index}", "winning_outcome": "YES" if outcome else "NO",
            "market_baseline_forward_scores": [{"scan_event_id": scan, "observed_at": f"2026-07-{index + 1:02d}T00:00:00Z",
                "normalized_yes_midpoint": market_p, "market_consensus_side": "YES" if outcome else "NO",
                "market_consensus_hit": True, "market_baseline_brier": .16, "market_baseline_log_loss": .5108256238}],
            "approved_model_forward_scores": [{"scan_event_id": scan, "observed_at": f"2026-07-{index + 1:02d}T00:00:00Z",
                "model_version": "v1", "model_yes_probability": model_p, "model_brier": .04,
                "model_log_loss": .2231435513, "market_baseline_brier": .16,
                "market_baseline_log_loss": .5108256238, "brier_improvement_vs_market": .12,
                "log_loss_improvement_vs_market": .2876820725}]})
    return {"events": events}

class DailyForwardBenchmarkTests(unittest.TestCase):
    def test_independent_conditions_not_hourly_observations(self):
        payload = module.build(ledger(), iterations=100)
        summary = payload["approved_model_summaries"][0]
        self.assertEqual(summary["independent_market_count"], 2)
        self.assertEqual(summary["repeated_observation_count"], 2)
        self.assertAlmostEqual(summary["mean_brier_improvement_vs_market"], .12)

    def test_repeated_snapshot_does_not_inflate_independent_count(self):
        data = ledger(1); resolution = data["events"][1]
        resolution["market_baseline_forward_scores"].append({**resolution["market_baseline_forward_scores"][0], "observed_at": "2026-07-01T01:00:00Z"})
        resolution["approved_model_forward_scores"].append({**resolution["approved_model_forward_scores"][0], "observed_at": "2026-07-01T01:00:00Z"})
        payload = module.build(data, iterations=10); summary = payload["approved_model_summaries"][0]
        self.assertEqual(summary["independent_market_count"], 1)
        self.assertEqual(summary["repeated_observation_count"], 2)
        self.assertIsNone(summary["brier_improvement_lower_95"])

    def test_empty_ledger_is_valid_zero_evidence(self):
        payload = module.build({"events": []}, iterations=10)
        self.assertEqual(payload["resolved_condition_count"], 0)
        self.assertEqual(payload["approved_model_summaries"], [])
        self.assertFalse(payload["live_orders_enabled"])

if __name__ == "__main__": unittest.main()
