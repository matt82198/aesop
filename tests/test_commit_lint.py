"""Tests for tools/commit_lint.py — conventional commit message linter."""
import json
import subprocess
import sys
import unittest


# Import the lint function directly
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "tools"))
from commit_lint import lint_message  # noqa: E402


class TestCommitLint(unittest.TestCase):
    """Unit tests for lint_message and CLI."""

    def test_valid_type_scope(self):
        """Valid type(scope): desc passes clean."""
        vs = lint_message("feat(driver): add retry logic")
        self.assertEqual(vs, [])

    def test_valid_type_no_scope(self):
        """Valid type: desc passes clean."""
        vs = lint_message("fix: correct off-by-one in parser")
        self.assertEqual(vs, [])

    def test_invalid_type(self):
        """Unknown type is flagged."""
        vs = lint_message("yolo: do stuff")
        rules = [v["rule"] for v in vs]
        self.assertIn("unknown-type", rules)

    def test_bad_format_no_colon(self):
        """Missing colon separator is flagged."""
        vs = lint_message("feat add something")
        rules = [v["rule"] for v in vs]
        self.assertIn("subject-format", rules)

    def test_subject_too_long(self):
        """Subject over 72 chars is flagged."""
        msg = "feat: " + "x" * 80
        vs = lint_message(msg)
        rules = [v["rule"] for v in vs]
        self.assertIn("subject-length", rules)

    def test_trailing_period(self):
        """Trailing period on subject is flagged."""
        vs = lint_message("fix: correct the thing.")
        rules = [v["rule"] for v in vs]
        self.assertIn("trailing-period", rules)

    def test_blank_line_separator(self):
        """Body without blank line separator is flagged."""
        msg = "feat: add feature\nThis is the body without blank line."
        vs = lint_message(msg)
        rules = [v["rule"] for v in vs]
        self.assertIn("blank-line", rules)

    def test_blank_line_present(self):
        """Body with blank line separator passes."""
        msg = "feat: add feature\n\nThis is the body with blank line."
        vs = lint_message(msg)
        self.assertEqual(vs, [])

    def test_empty_message(self):
        """Empty message is flagged."""
        vs = lint_message("")
        rules = [v["rule"] for v in vs]
        self.assertIn("empty-message", rules)

    def test_valid_co_authored_by(self):
        """Valid Co-Authored-By trailer passes."""
        msg = "feat: add feature\n\nBody text.\n\nCo-Authored-By: Someone <someone@example.com>"
        vs = lint_message(msg)
        self.assertEqual(vs, [])

    def test_malformed_co_authored_by(self):
        """Malformed Co-Authored-By trailer is flagged."""
        msg = "feat: add feature\n\nCo-Authored-By: noangle"
        vs = lint_message(msg)
        rules = [v["rule"] for v in vs]
        self.assertIn("co-authored-by", rules)

    def test_all_allowed_types(self):
        """Every allowed type passes."""
        for t in ["feat", "fix", "refactor", "test", "docs", "chore", "ci", "perf", "style", "build"]:
            vs = lint_message(f"{t}: do something")
            self.assertEqual(vs, [], f"Type '{t}' should be allowed")

    def test_scope_with_slash(self):
        """Scope with slash (e.g. driver/swap) passes."""
        vs = lint_message("refactor(driver/swap): simplify config")
        self.assertEqual(vs, [])

    def test_json_cli_output(self):
        """CLI --json --message produces valid JSON."""
        repo_root = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
        result = subprocess.run(
            [sys.executable, "tools/commit_lint.py", "--json", "--message", "feat: valid msg"],
            capture_output=True, text=True, cwd=repo_root, timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["total_violations"], 0)

    def test_cli_exit_code_on_violation(self):
        """CLI returns exit 1 when violations found."""
        repo_root = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
        result = subprocess.run(
            [sys.executable, "tools/commit_lint.py", "--message", "badformat no colon"],
            capture_output=True, text=True, cwd=repo_root, timeout=30,
        )
        self.assertEqual(result.returncode, 1)

    def test_crlf_handling(self):
        """Windows CRLF line endings are handled."""
        msg = "feat: add feature\r\n\r\nBody text.\r\n"
        vs = lint_message(msg)
        self.assertEqual(vs, [])

    def test_cli_empty_message_explicit(self):
        """CLI --message "" (empty string) is linted, not treated as flag absence."""
        repo_root = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
        result = subprocess.run(
            [sys.executable, "tools/commit_lint.py", "--message", "", "--json"],
            capture_output=True, text=True, cwd=repo_root, timeout=30,
        )
        # Empty message should be linted (return 1, not read from stdin)
        self.assertEqual(result.returncode, 1, f"Expected exit 1 for empty message, got {result.returncode}")
        data = json.loads(result.stdout)
        # Should have empty-message violation
        violations = []
        for r in data["results"]:
            violations.extend(r["violations"])
        rules = [v["rule"] for v in violations]
        self.assertIn("empty-message", rules, f"Expected empty-message violation, got rules: {rules}")

    def test_cli_empty_message_vs_no_message(self):
        """Distinguish: --message "" (empty) vs no --message flag (reads stdin)."""
        repo_root = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
        # Test with --message "" and a valid input on stdin (should not read stdin)
        result = subprocess.run(
            [sys.executable, "tools/commit_lint.py", "--message", "", "--json"],
            input="feat: valid msg from stdin\n",  # This should NOT be used
            capture_output=True, text=True, cwd=repo_root, timeout=30,
        )
        # Empty message should be linted (not fall through to stdin)
        self.assertEqual(result.returncode, 1, f"Expected exit 1 for empty --message flag, got {result.returncode}")
        data = json.loads(result.stdout)
        # Should report empty-message violation
        violations = []
        for r in data["results"]:
            violations.extend(r["violations"])
        rules = [v["rule"] for v in violations]
        self.assertIn("empty-message", rules, f"Expected empty-message violation (not stdin read), got: {rules}")


if __name__ == "__main__":
    unittest.main()
