import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "runtime_artifact_inventory.py"
)
SPEC = importlib.util.spec_from_file_location("runtime_artifact_inventory", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class RuntimeArtifactInventoryTest(unittest.TestCase):
    def test_inventory_ignores_skill_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "reports").mkdir()
            (root / "reports" / "one.md").write_text("report", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts" / "source.py").write_text("pass\n", encoding="utf-8")
            result = MODULE.inventory(root)
            self.assertEqual(result["file_count"], 1)
            self.assertEqual(result["files"][0]["relative_path"], "reports/one.md")

    def test_archive_moves_and_verifies_without_deleting_source_code(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            archive = base / "archive"
            (source / "experiments").mkdir(parents=True)
            (source / "experiments" / "one.json").write_text(
                '{"ok": true}\n', encoding="utf-8"
            )
            (source / "SKILL.md").write_text("source", encoding="utf-8")
            manifest = MODULE.inventory(source)
            result = MODULE.archive_runtime(source, archive, manifest)
            self.assertTrue(result["verified"])
            self.assertFalse((source / "experiments" / "one.json").exists())
            self.assertTrue((archive / "experiments" / "one.json").exists())
            self.assertTrue((source / "SKILL.md").exists())

    def test_existing_archive_target_blocks_move(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source"
            archive = base / "archive"
            (source / "reports").mkdir(parents=True)
            (archive / "reports").mkdir(parents=True)
            (source / "reports" / "one.md").write_text("new", encoding="utf-8")
            (archive / "reports" / "one.md").write_text("old", encoding="utf-8")
            manifest = MODULE.inventory(source)
            with self.assertRaises(FileExistsError):
                MODULE.archive_runtime(source, archive, manifest)
            self.assertTrue((source / "reports" / "one.md").exists())


if __name__ == "__main__":
    unittest.main()
