import argparse
import copy
import datetime as dt
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

HISTORY_SPEC = importlib.util.spec_from_file_location(
    "recommendation_history",
    SCRIPTS / "recommendation_history.py",
)
HISTORY = importlib.util.module_from_spec(HISTORY_SPEC)
assert HISTORY_SPEC and HISTORY_SPEC.loader
sys.modules["recommendation_history"] = HISTORY
HISTORY_SPEC.loader.exec_module(HISTORY)

MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "migrate_recommendation_history_v1",
    SCRIPTS / "migrate_recommendation_history_v1.py",
)
MIGRATION = importlib.util.module_from_spec(MIGRATION_SPEC)
assert MIGRATION_SPEC and MIGRATION_SPEC.loader
MIGRATION_SPEC.loader.exec_module(MIGRATION)


def valid_v2_record():
    return {
        "schema_version": "recommendation-v2",
        "recommendation_id": "rec-v2-001",
        "request_id": "request-v2-001",
        "request_mode": "longterm_dca",
        "symbol": "SOL",
        "asset_class": "crypto",
        "evidence_snapshot_id": "snapshot-001",
        "generated_at": "2026-07-25T08:30:00+08:00",
        "research_decision": "preferred",
        "current_direct_decision": "small_entry_now",
        "execution_decision": "no_deploy_cash",
        "decision_price": 188.25,
        "decision_price_evidence_id": "sol-price-001",
        "price_as_of": "2026-07-25T08:29:00+08:00",
        "decision_valid_until": "2026-07-30T08:30:00+08:00",
        "review_due_at": "2026-10-25T08:30:00+08:00",
        "observation_status": "pending",
        "execution_status": "blocked",
        "outcome_status": "pending",
        "deployable_cash": 0,
        "cash_source": "settled USDT only",
        "execution_blockers": ["no_deployable_cash"],
        "data_quality_status": "verified_public_sources",
        "human_confirmation_required": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "longterm_plan": {
            "fundamental_quality_rank": 1,
            "raw_upside_rank": 1,
            "portfolio_next_dollar_rank": 1,
            "market_capacity": "large",
            "adoption": "growing",
            "value_capture": "fees and staking",
            "supply_dilution": "bounded but nonzero",
            "staking_net_yield_pct": 5.0,
            "staking_liquidity_risk": "unbonding and smart-contract risk",
            "five_year_scenarios": [
                {"case": "bear", "probability_pct": 25, "outcome": "1x"},
                {"case": "base", "probability_pct": 50, "outcome": "3x"},
                {"case": "bull", "probability_pct": 25, "outcome": "6x"},
            ],
            "ten_year_scenarios": [
                {"case": "bear", "probability_pct": 20, "outcome": "1x"},
                {"case": "base", "probability_pct": 50, "outcome": "5x"},
                {"case": "bull", "probability_pct": 30, "outcome": "10x"},
            ],
            "contribution_plan": "monthly",
            "quarterly_review_at": "2026-10-25T08:30:00+08:00",
            "annual_review_at": "2027-07-25T08:30:00+08:00",
            "thesis_invalidation": ["adoption reverses"],
        },
        "five_year_token_multiple": 3.0,
    }


class DueAtCompatibilityTest(unittest.TestCase):
    def test_v2_prefers_review_due_at(self):
        record = valid_v2_record()
        record.update({
            "latest_exit_or_review_at": "2026-08-01T00:00:00Z",
            "entry_deadline": "2026-07-26T00:00:00Z",
            "time_window": "1 trading day",
        })
        self.assertEqual(
            HISTORY.due_at(record),
            dt.datetime(2026, 10, 25, 0, 30, tzinfo=dt.timezone.utc),
        )

    def test_v2_never_falls_back_to_time_window(self):
        record = valid_v2_record()
        del record["review_due_at"]
        record["time_window"] = "1 trading day"
        self.assertIsNone(HISTORY.due_at(record))

    def test_malformed_preferred_structured_date_does_not_fall_through(self):
        record = valid_v2_record()
        record["review_due_at"] = "not-a-date"
        record["latest_exit_or_review_at"] = "2026-08-01T00:00:00Z"
        self.assertIsNone(HISTORY.due_at(record))

    def test_v1_calendar_year_is_not_interpreted_as_day_count(self):
        record = {
            "generated_at": "2026-07-25T00:00:00Z",
            "time_window": "2026-07-25 through 2026-07-30 post-FOMC review",
        }
        self.assertEqual(
            HISTORY.due_at(record),
            dt.datetime(2026, 8, 24, 0, 0, tzinfo=dt.timezone.utc),
        )

    def test_v1_explicit_trading_day_duration_is_preserved(self):
        record = {
            "generated_at": "2026-07-25T00:00:00Z",
            "time_window": "1-5 trading days",
        }
        self.assertEqual(
            HISTORY.due_at(record),
            dt.datetime(2026, 8, 1, 0, 0, tzinfo=dt.timezone.utc),
        )


class SensitiveKeyTest(unittest.TestCase):
    def test_tokenomics_fields_are_allowed(self):
        record = {
            "five_year_token_multiple": 3,
            "tokenomics": {"token_supply_pressure": "moderate"},
        }
        self.assertEqual(HISTORY.find_sensitive_keys(record), [])

    def test_credential_keys_are_blocked(self):
        record = {
            "api_token": "x",
            "nested": {
                "accessToken": "x",
                "client-secret": "x",
                "password": "x",
                "bearer_header": "x",
            },
        }
        self.assertEqual(
            set(HISTORY.find_sensitive_keys(record)),
            {
                "api_token",
                "nested.accessToken",
                "nested.client-secret",
                "nested.password",
                "nested.bearer_header",
            },
        )


class RecommendationV2ValidationTest(unittest.TestCase):
    def test_valid_v2_record_passes(self):
        HISTORY.validate_record(valid_v2_record())

    def test_record_version_alias_is_accepted(self):
        record = valid_v2_record()
        record["record_version"] = record.pop("schema_version")
        HISTORY.validate_record(record)

    def test_lifecycle_states_are_independent_and_validated(self):
        for field, invalid_value in (
            ("observation_status", "executed"),
            ("execution_status", "triggered"),
            ("outcome_status", "executed"),
        ):
            record = valid_v2_record()
            record[field] = invalid_value
            with self.assertRaisesRegex(ValueError, field):
                HISTORY.validate_record(record)

    def test_invalid_v2_batch_is_not_partially_appended(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            ledger_path = directory / "history.json"
            HISTORY.save_ledger(ledger_path, HISTORY.default_ledger())
            before = ledger_path.read_bytes()

            invalid = valid_v2_record()
            del invalid["review_due_at"]
            records_path = directory / "records.json"
            records_path.write_text(
                json.dumps([valid_v2_record(), invalid]),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                path=str(ledger_path),
                record_file=str(records_path),
                replace=False,
                supersede_open_equivalent=False,
                supersede_reason=None,
            )
            with self.assertRaisesRegex(ValueError, "review_due_at"):
                HISTORY.cmd_add(args)
            self.assertEqual(ledger_path.read_bytes(), before)

    def test_zero_cash_blocks_deploying_execution_decision_only(self):
        record = valid_v2_record()
        record["research_decision"] = "buy"
        record["execution_decision"] = "enter_now"
        with self.assertRaisesRegex(ValueError, "deployable_cash is zero"):
            HISTORY.validate_record(record)

    def test_v2_review_keeps_three_lifecycle_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger_path = Path(temporary) / "history.json"
            ledger = HISTORY.default_ledger()
            ledger["recommendations"].append(valid_v2_record())
            HISTORY.save_ledger(ledger_path, ledger)
            args = argparse.Namespace(
                path=str(ledger_path),
                recommendation_id="rec-v2-001",
                outcome_status="not_triggered",
                observation_status="not_triggered",
                execution_status="not_executed",
                reviewed_at="2026-10-25T08:30:00+08:00",
                actual_return_pct=None,
                actual_notes="Window expired without an observation trigger.",
                attribution=None,
                what_should_change=None,
                proposed_change_id=None,
            )
            HISTORY.cmd_review(args)
            updated = HISTORY.load_ledger(ledger_path)
            recommendation = updated["recommendations"][0]
            review = updated["outcome_reviews"][0]
            self.assertEqual(recommendation["observation_status"], "not_triggered")
            self.assertEqual(recommendation["execution_status"], "not_executed")
            self.assertEqual(recommendation["outcome_status"], "not_triggered")
            self.assertEqual(review["schema_version"], "outcome-review-v2")


class ReadOnlyMigrationTest(unittest.TestCase):
    def test_migration_preserves_raw_records_and_classifies(self):
        reviewable = {
            "recommendation_id": "legacy-reviewable",
            "generated_at": "2026-07-25T00:00:00Z",
            "time_window": "1-5 trading days",
            "forecast_probability_pct": 70,
            "decision_price": 42.5,
        }
        unreviewable = {
            "recommendation_id": "legacy-unreviewable",
            "generated_at": "2026-07-25T00:00:00Z",
            "time_window": "2026-07-25 through 2026-07-30",
            "forecast_probability_pct": 55,
            "decision_price": 12.0,
        }
        ledger = HISTORY.default_ledger()
        ledger["recommendations"] = [reviewable, unreviewable]

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.json"
            source.write_text(json.dumps(ledger), encoding="utf-8")
            source_before = source.read_bytes()
            index, summary = MIGRATION.build_outputs(source, copy.deepcopy(ledger))

            self.assertEqual(source.read_bytes(), source_before)
            self.assertEqual(index["records"][0]["raw_record"], reviewable)
            self.assertEqual(index["records"][1]["raw_record"], unreviewable)
            self.assertEqual(index["records"][0]["classification"], "reviewable")
            self.assertEqual(
                index["records"][1]["classification"],
                "legacy_unreviewable",
            )
            self.assertEqual(
                summary["classification_counts"],
                {"reviewable": 1, "legacy_unreviewable": 1},
            )
            self.assertFalse(summary["migration_policy"]["probabilities_or_prices_rewritten"])

    def test_cli_writes_only_explicit_outputs_and_leaves_source_unchanged(self):
        ledger = HISTORY.default_ledger()
        ledger["recommendations"] = [{
            "recommendation_id": "legacy-structured",
            "latest_exit_or_review_date": "2026-08-01T00:00:00Z",
            "forecast_probability_pct": 60,
            "price": 10,
        }]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.json"
            index_output = directory / "explicit-index.json"
            summary_output = directory / "explicit-summary.json"
            source.write_text(json.dumps(ledger), encoding="utf-8")
            source_before = source.read_bytes()
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "migrate_recommendation_history_v1.py"),
                    "--input",
                    str(source),
                    "--index-output",
                    str(index_output),
                    "--summary-output",
                    str(summary_output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(index_output.exists())
            self.assertTrue(summary_output.exists())
            self.assertEqual(source.read_bytes(), source_before)
            self.assertEqual(set(directory.iterdir()), {source, index_output, summary_output})


if __name__ == "__main__":
    unittest.main()
