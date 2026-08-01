#!/usr/bin/env python3

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("governance.py")


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "governance@example.test")
        self.git("config", "user.name", "Governance Test")
        (self.root / "app.txt").write_text("baseline\n", encoding="utf-8")
        self.git("add", "app.txt")
        self.git("commit", "-m", "baseline")
        self.invoke(
            "thread-a",
            "init",
            "--project-root", str(self.root),
            "--project-id", "test-project",
            "--name", "Test Project",
            "--risk", "high",
            "--test-command", "python3 -c \"print('test-ok')\"",
            "--runtime-check", "python3 -c \"print('runtime-ok')\"",
            "--external-inputs-required",
        )
        self.write_contracts()

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def invoke(self, owner, *args, expect=0, cwd=None):
        env = {**os.environ, "CODEX_THREAD_ID": owner}
        result = subprocess.run(
            ["python3", str(SCRIPT), *args],
            cwd=cwd or self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return json.loads(result.stdout)

    @property
    def governance(self):
        return self.root / ".codex" / "governance"

    @property
    def goal_source(self):
        return Path(self.temp.name) / "goal.json"

    def write_contracts(self):
        scenario = {
            "schema_version": "AcceptanceScenarioV1",
            "id": "golden-path",
            "title": "Golden path",
            "level": "RUNTIME_VERIFIED",
            "preconditions": [],
            "steps": ["run"],
            "expected": ["pass"],
            "evidence": ["command result"],
        }
        (self.governance / "acceptance" / "golden-path.json").write_text(
            json.dumps(scenario), encoding="utf-8"
        )
        goal = {
            "schema_version": "GoalContractV1",
            "id": "goal-1",
            "title": "Test governed delivery",
            "business_outcome": "Prove the governance loop",
            "scope": ["app.txt"],
            "non_goals": [],
            "constraints": ["no secrets"],
            "acceptance_scenario_ids": ["golden-path"],
            "required_level": "BUSINESS_READY",
            "risk": "high",
            "supersedes_id": None,
        }
        self.goal_source.write_text(json.dumps(goal), encoding="utf-8")

    def begin(self, owner="thread-a", root=None):
        project = root or self.root
        return self.invoke(
            owner,
            "begin",
            "--project-root", str(project),
            "--goal-file", str(self.goal_source),
            cwd=project,
        )

    def verify_to_runtime(self):
        self.invoke("thread-a", "verify", "--project-root", str(self.root), "--level", "CODED")
        self.invoke("thread-a", "verify", "--project-root", str(self.root), "--level", "TESTED")
        return self.invoke(
            "thread-a", "verify", "--project-root", str(self.root),
            "--level", "RUNTIME_VERIFIED", "--blocker", "real account confirmation required"
        )

    def test_same_worktree_second_writer_is_blocked(self):
        self.begin()
        output = self.invoke(
            "thread-b", "begin", "--project-root", str(self.root),
            "--goal-file", str(self.goal_source), expect=2
        )
        self.assertIn("worktree_locked_by:thread-a", output["error"])

    def test_frozen_goal_cannot_be_rewritten(self):
        self.begin()
        self.invoke("thread-a", "close", "--project-root", str(self.root))
        goal = json.loads(self.goal_source.read_text())
        goal["title"] = "Changed in place"
        self.goal_source.write_text(json.dumps(goal), encoding="utf-8")
        output = self.invoke(
            "thread-a", "begin", "--project-root", str(self.root),
            "--goal-file", str(self.goal_source), expect=2
        )
        self.assertIn("frozen_goal_immutable", output["error"])

    def test_blocked_runtime_does_not_become_business_ready(self):
        self.begin()
        runtime = self.verify_to_runtime()
        self.assertTrue(runtime["result"]["blocked"])
        output = self.invoke(
            "thread-a", "verify", "--project-root", str(self.root),
            "--level", "BUSINESS_READY", "--external-inputs-confirmed", expect=2
        )
        self.assertIn("independent_verification_required", output["error"])

    def test_independent_pass_allows_business_ready(self):
        self.begin()
        self.verify_to_runtime()
        status = self.invoke("thread-a", "status", "--project-root", str(self.root))["result"]
        independent = {
            "schema_version": "VerificationResultV1",
            "id": "independent-1",
            "goal_id": "goal-1",
            "level": "BUSINESS_READY",
            "source_digest": status["current_source_digest"],
            "verifier_thread_id": "thread-b",
            "result": "PASS",
            "checks": [],
            "findings": [],
            "created_at": "2026-07-31T00:00:00Z",
        }
        path = Path(self.temp.name) / "independent.json"
        path.write_text(json.dumps(independent), encoding="utf-8")
        output = self.invoke(
            "thread-a", "verify", "--project-root", str(self.root),
            "--level", "BUSINESS_READY", "--external-inputs-confirmed",
            "--independent-result", str(path)
        )
        self.assertEqual(output["result"]["highest_level"], "BUSINESS_READY")

    def test_independent_result_can_bind_runtime_verified_release(self):
        self.begin()
        self.invoke("thread-a", "verify", "--project-root", str(self.root), "--level", "CODED")
        self.invoke("thread-a", "verify", "--project-root", str(self.root), "--level", "TESTED")
        status = self.invoke("thread-a", "status", "--project-root", str(self.root))["result"]
        independent = {
            "schema_version": "VerificationResultV1",
            "id": "independent-runtime-1",
            "goal_id": "goal-1",
            "level": "RUNTIME_VERIFIED",
            "source_digest": status["current_source_digest"],
            "verifier_thread_id": "thread-b",
            "result": "PASS",
            "checks": [],
            "findings": [],
            "created_at": "2026-07-31T00:00:00Z",
        }
        path = Path(self.temp.name) / "independent-runtime.json"
        path.write_text(json.dumps(independent), encoding="utf-8")
        output = self.invoke(
            "thread-a", "verify", "--project-root", str(self.root),
            "--level", "RUNTIME_VERIFIED", "--blocker", "external input missing",
            "--independent-result", str(path)
        )
        self.assertTrue(output["result"]["blocked"])
        self.assertEqual(output["result"]["independent_verification_id"], "independent-runtime-1")

    def test_implementation_owner_cannot_self_verify(self):
        self.begin()
        self.verify_to_runtime()
        status = self.invoke("thread-a", "status", "--project-root", str(self.root))["result"]
        independent = {
            "schema_version": "VerificationResultV1",
            "id": "self-verification",
            "goal_id": "goal-1",
            "level": "BUSINESS_READY",
            "source_digest": status["current_source_digest"],
            "verifier_thread_id": "thread-a",
            "result": "PASS",
            "checks": [],
            "findings": [],
            "created_at": "2026-07-31T00:00:00Z",
        }
        path = Path(self.temp.name) / "self-verification.json"
        path.write_text(json.dumps(independent), encoding="utf-8")
        output = self.invoke(
            "thread-a", "verify", "--project-root", str(self.root),
            "--level", "BUSINESS_READY", "--external-inputs-confirmed",
            "--independent-result", str(path), expect=2
        )
        self.assertIn("independent_verifier_must_differ", output["error"])

    def test_failed_runtime_command_blocks_runtime_level(self):
        self.begin()
        config_path = self.governance / "project.json"
        config = json.loads(config_path.read_text())
        config["runtime_checks"] = ["python3 -c \"raise SystemExit(9)\""]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.invoke("thread-a", "verify", "--project-root", str(self.root), "--level", "CODED")
        self.invoke("thread-a", "verify", "--project-root", str(self.root), "--level", "TESTED")
        output = self.invoke(
            "thread-a", "verify", "--project-root", str(self.root),
            "--level", "RUNTIME_VERIFIED", expect=2
        )
        self.assertIn("gate_command_failed:RUNTIME_VERIFIED", output["error"])

    def test_source_change_invalidates_prior_verification(self):
        self.begin()
        self.invoke("thread-a", "verify", "--project-root", str(self.root), "--level", "CODED")
        self.invoke("thread-a", "verify", "--project-root", str(self.root), "--level", "TESTED")
        (self.root / "app.txt").write_text("changed\n", encoding="utf-8")
        status = self.invoke("thread-a", "status", "--project-root", str(self.root))["result"]
        self.assertTrue(status["verification_stale"])
        self.assertEqual(status["effective_level"], "DESIGNED")
        output = self.invoke(
            "thread-a", "verify", "--project-root", str(self.root),
            "--level", "RUNTIME_VERIFIED", expect=2
        )
        self.assertIn("source_changed_since_previous_gate", output["error"])

    def test_commit_of_same_verified_content_does_not_invalidate_digest(self):
        self.begin()
        self.verify_to_runtime()
        before = self.invoke("thread-a", "status", "--project-root", str(self.root))["result"]
        self.git("add", "-A")
        self.git("commit", "-m", "record verified content")
        after = self.invoke("thread-a", "status", "--project-root", str(self.root))["result"]
        self.assertEqual(after["current_source_digest"], before["current_source_digest"])
        self.assertFalse(after["verification_stale"])

    def test_acceptance_change_invalidates_prior_verification(self):
        self.begin()
        self.verify_to_runtime()
        scenario_path = self.governance / "acceptance" / "golden-path.json"
        scenario = json.loads(scenario_path.read_text())
        scenario["expected"].append("new expectation")
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
        status = self.invoke("thread-a", "status", "--project-root", str(self.root))["result"]
        self.assertTrue(status["verification_stale"])
        self.assertEqual(status["effective_level"], "DESIGNED")

    def test_deviation_is_append_only_and_redacted(self):
        self.begin()
        self.invoke(
            "thread-a", "deviation", "add", "--project-root", str(self.root),
            "--severity", "P1", "--expected", "Bearer " + "abcdefghijklmnop",
            "--actual", "sk-" + "abcdefghijklmnop", "--acceptance-scenario-id", "golden-path"
        )
        text = (self.governance / "deviations.jsonl").read_text()
        self.assertNotIn("abcdefghijklmnop", text)
        self.assertIn("[REDACTED]", text)

    def test_release_is_bound_to_current_digest(self):
        self.begin()
        self.invoke("thread-a", "verify", "--project-root", str(self.root), "--level", "CODED")
        release = self.invoke("thread-a", "release", "--project-root", str(self.root))["result"]
        self.assertEqual(release["achieved_level"], "CODED")
        (self.root / "app.txt").write_text("new source\n", encoding="utf-8")
        output = self.invoke("thread-a", "release", "--project-root", str(self.root), expect=2)
        self.assertIn("verification_stale", output["error"])

    def test_release_evidence_redacts_secret_shaped_values(self):
        self.begin()
        self.invoke("thread-a", "verify", "--project-root", str(self.root), "--level", "CODED")
        release = self.invoke(
            "thread-a", "release", "--project-root", str(self.root),
            "--build-version", "Bearer " + "abcdefghijklmnop"
        )["result"]
        release_path = self.governance / "releases" / f"{release['id']}.json"
        saved = release_path.read_text()
        self.assertNotIn("abcdefghijklmnop", saved)
        self.assertIn("[REDACTED]", saved)

    def test_separate_git_worktree_can_have_a_different_writer(self):
        self.git("add", "AGENTS.md", ".codex/governance/project.json", ".codex/governance/data-authority.json", ".codex/governance/acceptance/golden-path.json")
        self.git("commit", "-m", "add governance configuration")
        self.begin()
        worktree = Path(self.temp.name) / "worktree-two"
        self.git("worktree", "add", "-b", "feature-two", str(worktree), "HEAD")
        output = self.begin("thread-b", root=worktree)
        self.assertEqual(output["result"]["status"], "DESIGNED")


if __name__ == "__main__":
    unittest.main()
