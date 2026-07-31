import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_automation_runtime_audit.py"
spec = importlib.util.spec_from_file_location("polymarket_automation_runtime_audit", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class AutomationRuntimeAuditTests(unittest.TestCase):
    def write_artifact(self, root: Path, relative: Path, created_at: str, status: str = "ok"):
        path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"created_at": created_at, "status": status}), encoding="utf-8")

    def test_fresh_artifacts_pass(self):
        now = datetime(2026, 7, 12, 4, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_artifact(root, module.TARGETS["full_validation"][0], "2026-07-12T03:30:00+00:00")
            self.write_artifact(root, module.TARGETS["deadline_shadow"][0], "2026-07-12T04:00:00+00:00")
            self.assertEqual(module.audit(root, now)["status"], "ok")

    def test_stale_or_missing_artifact_degrades(self):
        now = datetime(2026, 7, 12, 6, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_artifact(root, module.TARGETS["full_validation"][0], "2026-07-12T03:00:00+00:00")
            payload = module.audit(root, now)
            self.assertEqual(payload["status"], "degraded")
            self.assertEqual({row["status"] for row in payload["artifacts"]}, {"stale", "missing"})

    def test_future_timestamp_degrades(self):
        now = datetime(2026, 7, 12, 4, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative, _ in module.TARGETS.values():
                self.write_artifact(root, relative, "2026-07-12T04:10:00+00:00")
            self.assertEqual(module.audit(root, now)["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
