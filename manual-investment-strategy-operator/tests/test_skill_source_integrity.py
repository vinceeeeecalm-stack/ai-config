import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class SkillSourceIntegrityTest(unittest.TestCase):
    def test_core_skill_files_are_compact_and_declared_paths_exist(self):
        skill_paths = [
            ROOT / "manual-investment-strategy-operator" / "SKILL.md",
            ROOT / "active-alpha-paper-monitor" / "SKILL.md",
            ROOT / "unified-longterm-alpha-investor" / "SKILL.md",
        ]
        for skill_path in skill_paths:
            self.assertTrue(skill_path.exists(), skill_path)
            text = skill_path.read_text(encoding="utf-8")
            self.assertLessEqual(len(text.splitlines()), 200, skill_path)
            frontmatter = text.split("---", 2)[1]
            keys = {
                line.split(":", 1)[0].strip()
                for line in frontmatter.splitlines()
                if ":" in line
            }
            self.assertEqual(keys, {"name", "description"}, skill_path)
            for rel in re.findall(
                r"`((?:references|scripts|config)/[^`\\s]+)`", text
            ):
                target = skill_path.parent / rel.rstrip("。、，；：")
                self.assertTrue(target.exists(), f"{skill_path}: missing {rel}")
                if target.is_file():
                    self.assertGreater(target.stat().st_size, 0, target)

    def test_unified_does_not_declare_missing_runtime_scripts(self):
        text = (
            ROOT / "unified-longterm-alpha-investor" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("scripts/crypto/", text)
        self.assertNotIn("scripts/stocks/", text)

    def test_active_runtime_directories_are_excluded_by_contract(self):
        text = (
            ROOT / "active-alpha-paper-monitor" / "SKILL.md"
        ).read_text(encoding="utf-8")
        for directory in (
            "reports/",
            "experiments/",
            "handoffs/",
            "cache/",
            "paper_trades/",
        ):
            self.assertIn(directory, text)


if __name__ == "__main__":
    unittest.main()
