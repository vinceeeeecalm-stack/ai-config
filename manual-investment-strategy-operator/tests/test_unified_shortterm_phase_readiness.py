import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "unified_shortterm_phase_readiness.py"
SPEC = importlib.util.spec_from_file_location("unified_shortterm_phase_readiness", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class UnifiedShortTermPhaseReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        for directory in (".codex/governance/releases", ".codex/governance/verification", "active-alpha-paper-monitor/scripts", "active-alpha-paper-monitor/config", "active-alpha-paper-monitor/runtime"):
            (self.repo / directory).mkdir(parents=True, exist_ok=True)
        real_repo = ROOT.parent
        for relative in ("active-alpha-paper-monitor/scripts/derivatives_shadow_promotion_evaluator.py", "active-alpha-paper-monitor/config/derivatives_shadow_promotion_gate_v1.json"):
            (self.repo / relative).write_text((real_repo / relative).read_text(encoding="utf-8"), encoding="utf-8")
        self.goals = ("unified-short-term-opportunity-engine-v1", "crypto-dynamic-spot-eligibility-failover-v15", "universal-investment-core-github-canonical-v3", "unified-shortterm-derivatives-shadow-v2", "derivatives-shadow-continuous-sampling-v1")
        for goal in self.goals:
            self.write_authority(goal)
        self.observations = self.repo / "active-alpha-paper-monitor/runtime/derivatives-shadow-v2-observations.jsonl"
        self.observations.write_text(json.dumps({"schema_version": "DerivativesShadowObservationV1", "observation_id": "obs-1", "snapshot_id": "snap-1", "directional_state": "OI_ONLY_CONFLICT"}) + "\n", encoding="utf-8")
        self.automation = self.repo / "automation.toml"
        self.automation.write_text('id="test"\nkind="heartbeat"\nstatus="ACTIVE"\nrrule="FREQ=DAILY;BYHOUR=0,4,8,12,16,20;BYMINUTE=15;BYSECOND=0;UNTIL=20260823T161500Z"\nprompt="derivatives_shadow_cycle.py derivatives_shadow_outcome_reviewer.py derivatives_shadow_promotion_evaluator.py 2026-08-16T04:15:00Z 不要启动阶段二 不要自动下单 不要把影子结果计入 Paper/真钱 ROI 或 BUSINESS_READY"\n', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_authority(self, goal, *, verifier_result="PASS", verifier_digest=None):
        digest = MODULE.hashlib.sha256(goal.encode()).hexdigest()
        verifier_id = f"verify-{goal}"
        (self.repo / f".codex/governance/releases/{goal}.json").write_text(json.dumps({"schema_version": "ReleaseEvidenceV1", "id": f"release-{goal}", "goal_id": goal, "achieved_level": "RUNTIME_VERIFIED", "blocked": False, "source_digest": digest, "independent_verification_id": verifier_id, "created_by": "owner-thread", "created_at": "2026-08-02T00:00:00Z"}), encoding="utf-8")
        (self.repo / f".codex/governance/verification/{goal}.json").write_text(json.dumps({"schema_version": "VerificationResultV1", "id": verifier_id, "goal_id": goal, "level": "RUNTIME_VERIFIED", "source_digest": verifier_digest or digest, "verifier_thread_id": "independent", "result": verifier_result}), encoding="utf-8")

    def audit(self, now="2026-08-02T00:00:00Z", **kwargs):
        return MODULE.build_readiness(self.repo, now=MODULE.parse_time(now, "now"), automation_path=self.automation, **kwargs)

    def test_current_slice_is_verified_but_phase1_is_only_collecting(self):
        result = self.audit()
        self.assertEqual(result["system_delivery_status"], "RUNTIME_VERIFIED")
        self.assertEqual(result["current_production_phase"], "PHASE0_BASELINE")
        self.assertEqual(result["phase1_status"], "EVIDENCE_COLLECTING")
        self.assertIsNone(result["next_eligible_phase"])
        self.assertEqual(result["unique_blocker"], "phase1_forward_outcomes_and_gate_pending")

    def test_file_existence_without_matching_pass_cannot_verify(self):
        self.write_authority("unified-shortterm-derivatives-shadow-v2", verifier_digest="f" * 64)
        result = self.audit()
        self.assertEqual(result["phase1_status"], "EVIDENCE_MISSING")
        layer = next(row for row in result["layer_status"] if row["layer_id"] == "derivatives_factors_front_loaded")
        self.assertIn("independent_verification_binding_mismatch:unified-shortterm-derivatives-shadow-v2", layer["blockers"])

    def test_low_level_or_same_thread_verifier_cannot_authorize_runtime(self):
        goal = "crypto-dynamic-spot-eligibility-failover-v15"
        digest = MODULE.hashlib.sha256(goal.encode()).hexdigest()
        verifier = self.repo / f".codex/governance/verification/{goal}.json"
        payload = json.loads(verifier.read_text(encoding="utf-8"))
        payload["level"] = "CODED"
        payload["verifier_thread_id"] = "owner-thread"
        verifier.write_text(json.dumps(payload), encoding="utf-8")
        result = self.audit()
        dynamic = next(row for row in result["layer_status"] if row["layer_id"] == "dynamic_liquidity_candidate_pool")
        self.assertEqual(dynamic["status"], "EVIDENCE_MISSING")
        self.assertIn(f"independent_verification_level_insufficient:{goal}", dynamic["blockers"])
        payload["level"] = "RUNTIME_VERIFIED"
        verifier.write_text(json.dumps(payload), encoding="utf-8")
        same_thread = self.audit()
        dynamic = next(row for row in same_thread["layer_status"] if row["layer_id"] == "dynamic_liquidity_candidate_pool")
        self.assertEqual(dynamic["status"], "EVIDENCE_MISSING")
        self.assertIn(f"independent_verifier_same_as_release_owner:{goal}", dynamic["blockers"])

    def test_inactive_automation_degrades_and_sets_matching_next_step(self):
        self.automation.write_text('kind="heartbeat"\nstatus="PAUSED"\nprompt=""\n', encoding="utf-8")
        result = self.audit()
        self.assertEqual(result["phase1_status"], "RUNTIME_DEGRADED")
        self.assertEqual(result["unique_blocker"], "phase1_automation_not_active")
        self.assertIn("恢复", result["unique_next_step"])

    def test_malformed_or_unsafe_automation_cannot_pass_by_token_presence(self):
        self.automation.write_text('kind="heartbeat"\nstatus="ACTIVE"\nrrule="NOT_AN_RRULE;UNTIL=20260823T161500Z"\nprompt="derivatives_shadow_cycle.py derivatives_shadow_outcome_reviewer.py derivatives_shadow_promotion_evaluator.py 2026-08-16T04:15:00Z 不要启动阶段二 不要自动下单 不要把影子结果计入 Paper/真钱 ROI 或 BUSINESS_READY AUTO_LIVE_ORDER PRIVATE_API"\n', encoding="utf-8")
        result = self.audit()
        self.assertEqual(result["phase1_status"], "RUNTIME_DEGRADED")
        self.assertIn("phase1_automation_contract_incomplete", result["all_blockers"])
        self.assertIn("phase1_automation_safety_boundary_invalid", result["all_blockers"])

    def test_phase2_and_live_profit_do_not_inherit_shadow_evidence(self):
        result = self.audit()
        near_miss = next(row for row in result["layer_status"] if row["layer_id"] == "persistent_near_miss_and_rank_acceleration")
        profit = next(row for row in result["layer_status"] if row["layer_id"] == "unified_live_profit_attribution")
        self.assertEqual(near_miss["status"], "SHADOW_ONLY")
        self.assertEqual(profit["status"], "UNMEASURED")
        self.assertFalse(result["real_money_roi_eligible"])
        self.assertFalse(result["business_ready_eligible"])

    def test_authoritative_live_summary_can_measure_but_not_make_business_ready(self):
        path = self.repo / "live-summary.json"
        path.write_text(json.dumps({"schema_version": "ShortTermProfitAttributionV1", "evidence_mode": "live", "trade_count": 2, "profit_status": "EXPERIMENT_RUNNING", "money_weighted_net_roi_pct": 1.5}), encoding="utf-8")
        result = self.audit(live_profit_path=path)
        self.assertEqual(result["live_profit_status"], "EXPERIMENT_RUNNING")
        self.assertFalse(result["business_ready_eligible"])

    def test_output_is_deterministic_for_same_inputs_and_as_of(self):
        first = self.audit()
        second = self.audit()
        self.assertEqual(first, second)
        self.assertEqual(first["audit_digest"], second["audit_digest"])
        self.assertIn(first["unique_blocker"], first["all_blockers"])


if __name__ == "__main__":
    unittest.main()
