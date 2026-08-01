import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "sync_installed_skill.py"
)
SPEC = importlib.util.spec_from_file_location("sync_installed_skill", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def make_skill(root: Path, name: str) -> None:
    skill = root / name
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "config").mkdir()
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n# Test\n",
        encoding="utf-8",
    )
    (skill / "scripts" / "main.py").write_text("pass\n", encoding="utf-8")
    (skill / "config" / "config.json").write_text("{}\n", encoding="utf-8")
    (skill / "references" / "policy.md").write_text(
        "policy\n", encoding="utf-8"
    )


class SyncInstalledSkillTest(unittest.TestCase):
    def test_manifest_detects_active_skill_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in MODULE.SKILL_SPECS:
                make_skill(root, name)
            (root / MODULE.ACTIVE_SKILL / "SKILL.md").unlink()
            manifest = MODULE.build_full_manifest(root, require_declared=True)
            key = f"{MODULE.ACTIVE_SKILL}/SKILL.md"
            self.assertEqual(manifest[key], "__MISSING__")
            self.assertTrue(MODULE.validate_tree(root, manifest))

    def test_runtime_directories_are_not_in_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in MODULE.SKILL_SPECS:
                make_skill(root, name)
            runtime_files = (
                root / MODULE.ACTIVE_SKILL / "reports" / "report.md",
                root
                / MODULE.MANUAL_SKILL
                / "recommendations"
                / "recommendation_history.json",
                root
                / MODULE.MANUAL_SKILL
                / "performance"
                / "us_tactical_performance.json",
            )
            for runtime_file in runtime_files:
                runtime_file.parent.mkdir(parents=True, exist_ok=True)
                runtime_file.write_text("{}\n", encoding="utf-8")
            manifest = MODULE.build_full_manifest(root, require_declared=True)
            for runtime_part in ("reports", "recommendations", "performance"):
                self.assertFalse(
                    any(f"/{runtime_part}/" in path for path in manifest),
                    manifest,
                )

    def test_exact_manifest_detects_hash_and_extra_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            workspace = base / "workspace"
            installed = base / "installed"
            for name in MODULE.SKILL_SPECS:
                make_skill(workspace, name)
                make_skill(installed, name)
            (
                installed / MODULE.MANUAL_SKILL / "scripts" / "main.py"
            ).write_text("changed\n", encoding="utf-8")
            (
                installed / MODULE.ACTIVE_SKILL / "scripts" / "extra.py"
            ).write_text("extra\n", encoding="utf-8")
            left = MODULE.build_full_manifest(
                workspace, require_declared=True
            )
            right = MODULE.build_full_manifest(
                installed, require_declared=False
            )
            diff = MODULE.compare_manifests(left, right)
            self.assertIn(
                f"{MODULE.MANUAL_SKILL}/scripts/main.py",
                diff["hash_mismatches"],
            )
            self.assertIn(
                f"{MODULE.ACTIVE_SKILL}/scripts/extra.py",
                diff["extra_installed"],
            )

    def test_critical_summary_failures_block_false_green(self):
        summary = {
            "status": "ok",
            "smoke_mode": True,
            "schema_baseline_summary": {"status": "verified_schema_baseline"},
            "steps": [{"name": "history", "status": "failed"}],
            "report_summary": {
                "recommendation_execution_calendar_gate": {
                    "status": "blocked"
                }
            },
        }
        failures = MODULE.critical_summary_failures(summary)
        self.assertTrue(any(item.startswith("step:history") for item in failures))
        self.assertTrue(
            any(
                item.startswith(
                    "report:recommendation_execution_calendar_gate"
                )
                for item in failures
            )
        )

    def test_invalid_json_is_a_validation_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in MODULE.SKILL_SPECS:
                make_skill(root, name)
            path = root / MODULE.MANUAL_SKILL / "config" / "config.json"
            path.write_text("{", encoding="utf-8")
            manifest = MODULE.build_full_manifest(root, require_declared=True)
            errors = MODULE.validate_tree(root, manifest)
            self.assertTrue(any(error.startswith("invalid_json:") for error in errors))

    def test_prefixed_inner_failure_cannot_be_false_green(self):
        summary = {
            "status": "ok",
            "smoke_mode": True,
            "schema_baseline_summary": {"status": "verified_schema_baseline"},
            "steps": [],
            "report_summary": {
                "recommendation_history_write": {
                    "status": "failed_calendar_gate"
                }
            },
        }
        failures = MODULE.critical_summary_failures(summary)
        self.assertIn(
            "report:recommendation_history_write:failed_calendar_gate",
            failures,
        )


if __name__ == "__main__":
    unittest.main()
