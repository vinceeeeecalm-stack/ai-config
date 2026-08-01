import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_100usd_scheduler_entrypoint.py"
SPEC = importlib.util.spec_from_file_location("scheduler_entrypoint", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class SchedulerEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_dir = self.root / "runtime"
        self.report_dir = self.root / "reports"
        self.state = self.root / "state.json"
        self.report = self.root / "report.json"
        payload = {"decision": "WAIT", "safety": dict(MODULE.SAFE_FLAGS), "created_at": "one"}
        self.state.write_text(json.dumps(payload), encoding="utf-8")
        self.report.write_text(json.dumps(payload), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def start(self, trigger, run_id):
        return MODULE.guard.start(self.state_dir, trigger, "isolated_dry_run", run_id, None, 900)

    def test_one_report_then_no_state_change(self):
        self.assertEqual(self.start("trigger-1", "run-1")["status"], "RUN_STARTED")
        first = MODULE.commit_report(self.state_dir, self.report_dir, "run-1", self.state,
                                     self.report, "run-1.json")
        self.assertEqual(first["status"], "REPORT_COMMITTED")
        self.assertEqual(MODULE.guard.finish(self.state_dir, "run-1", 0, "completed")["status"], "RUN_FINISHED")
        self.assertEqual(self.start("trigger-2", "run-2")["status"], "RUN_STARTED")
        second = MODULE.commit_report(self.state_dir, self.report_dir, "run-2", self.state,
                                      self.report, "run-2.json")
        self.assertEqual(second["status"], "NO_STATE_CHANGE")
        self.assertFalse((self.report_dir / "run-2.json").exists())
        self.assertEqual(len(list(self.report_dir.glob("*"))), 1)

    def test_second_primary_report_in_same_run_is_incident(self):
        self.start("trigger-1", "run-1")
        first = MODULE.commit_report(self.state_dir, self.report_dir, "run-1", self.state,
                                     self.report, "run-1.json")
        self.assertEqual(first["status"], "REPORT_COMMITTED")
        changed = json.loads(self.state.read_text())
        changed["decision"] = "PASS"
        self.state.write_text(json.dumps(changed), encoding="utf-8")
        second = MODULE.commit_report(self.state_dir, self.report_dir, "run-1", self.state,
                                      self.report, "run-1-second.json")
        self.assertEqual(second["status"], "PROCESS_INCIDENT")
        self.assertEqual(second["reason"], "primary_report_already_committed")

    def test_postmortem_marker_can_commit_report_already_in_report_dir(self):
        self.start("postmortem-trigger", "postmortem-run")
        self.report_dir.mkdir(parents=True)
        report = self.report_dir / "daily.md"
        report.write_text("# daily\n", encoding="utf-8")
        result = MODULE.commit_report(self.state_dir, self.report_dir, "postmortem-run",
                                      Path("POSTMORTEM_COMPLETE"), report, "daily.md")
        self.assertEqual(result["status"], "REPORT_COMMITTED")
        self.assertEqual(report.read_text(encoding="utf-8"), "# daily\n")

    def test_duplicate_trigger_is_blocked(self):
        self.start("trigger-1", "run-1")
        MODULE.guard.finish(self.state_dir, "run-1", 0, "completed")
        duplicate = self.start("trigger-1", "run-2")
        self.assertEqual(duplicate["status"], "DUPLICATE_RUN_BLOCKED")


if __name__ == "__main__":
    unittest.main()
