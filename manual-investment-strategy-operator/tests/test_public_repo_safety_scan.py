import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scan-public-repo.py"
if SCRIPT.exists():
    SPEC = importlib.util.spec_from_file_location("public_repo_scan", SCRIPT)
    M = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(M)
else:
    M = None


@unittest.skipUnless(M is not None, "repository-level scanner is not part of the installed Skill mirror")
class PublicRepoSafetyScanTest(unittest.TestCase):
    def test_secret_is_redacted_to_hash(self):
        token = "ghp_" + "abcdefghijklmnopqrstuvwxyz1234567890AB"
        findings = M.text_findings(f"token={token}", "demo.txt", "worktree")
        self.assertTrue(findings)
        self.assertNotIn(token, str(findings))
        self.assertIn("value_sha256", findings[0])

    def test_placeholder_is_not_generic_secret(self):
        findings = M.text_findings("api_key=your_api_key_placeholder", "demo.md", "worktree")
        self.assertEqual(findings, [])

    def test_private_runtime_path_is_rejected(self):
        findings = M.privacy_findings(["manual/runtime/account.json"], "worktree")
        self.assertEqual(findings[0]["category"], "privacy")

    def test_maintainable_source_path_is_allowed(self):
        self.assertEqual(M.privacy_findings(["manual/scripts/core.py"], "worktree"), [])


if __name__ == "__main__":
    unittest.main()
