import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_automation_contract_audit.py"
spec = importlib.util.spec_from_file_location("polymarket_automation_contract_audit", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def fixture(automation_id, row, project, *, status=None, rrule=None, prompt=None):
    status = status or row["status"]
    rrule = rrule or row["rrule"]
    prompt = prompt or f"运行 {row['script']}。" + "；".join(row["required_prompt_terms"]) + "；共享锁。"
    return f'''version = 1
id = "{automation_id}"
kind = "cron"
name = "{row['name']}"
prompt = "{prompt}"
status = "{status}"
rrule = "{rrule}"
execution_environment = "local"
target = {{ type = "project", project_id = "{project}" }}
cwds = ["{project}"]
'''


class AutomationContractAuditTests(unittest.TestCase):
    def build(self, root, project):
        for automation_id, row in module.EXPECTED.items():
            path = root / automation_id / "automation.toml"
            path.parent.mkdir(parents=True)
            path.write_text(fixture(automation_id, row, project), encoding="utf-8")

    def test_safe_contracts_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project = root / "project"; self.build(root, project)
            self.assertEqual(module.audit(root, project)["status"], "ok")

    def test_paused_contract_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project = root / "project"; self.build(root, project)
            automation_id = next(iter(module.EXPECTED)); row = module.EXPECTED[automation_id]
            (root / automation_id / "automation.toml").write_text(fixture(automation_id, row, project, status="PAUSED"), encoding="utf-8")
            self.assertEqual(module.audit(root, project)["status"], "blocked")

    def test_shadow_activation_blocks_single_scheduler_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project = root / "project"; self.build(root, project)
            automation_id = "polymarket-deadline-shadow-monitor"; row = module.EXPECTED[automation_id]
            (root / automation_id / "automation.toml").write_text(
                fixture(automation_id, row, project, status="ACTIVE"), encoding="utf-8"
            )
            self.assertEqual(module.audit(root, project)["status"], "blocked")

    def test_cadence_drift_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project = root / "project"; self.build(root, project)
            automation_id = next(iter(module.EXPECTED)); row = module.EXPECTED[automation_id]
            (root / automation_id / "automation.toml").write_text(fixture(automation_id, row, project, rrule="FREQ=DAILY"), encoding="utf-8")
            self.assertEqual(module.audit(root, project)["status"], "blocked")

    def test_unsafe_prompt_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project = root / "project"; self.build(root, project)
            automation_id = next(iter(module.EXPECTED)); row = module.EXPECTED[automation_id]
            prompt = f"运行 {row['script']}，live_orders_enabled=true，公开市场数据；禁止真实订单、私有 API、共享锁，不得绕过 gate。"
            (root / automation_id / "automation.toml").write_text(fixture(automation_id, row, project, prompt=prompt), encoding="utf-8")
            self.assertEqual(module.audit(root, project)["status"], "blocked")

    def test_missing_contract_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(module.audit(Path(temp), Path(temp) / "project")["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
