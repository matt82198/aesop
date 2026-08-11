#!/usr/bin/env python3
"""
Test suite for verify_test_suite_count tool.

Contract under test (A1 gate-fix):
- --check / --strict are READ-ONLY validation and NEVER write. Drift = exit 1.
- --regenerate (alias --fix) is the only writing mode.
- Fail-closed preserved: missing section = exit 1, duplicated section = exit 1,
  vacuous zero-file derivation with non-zero documented counts = exit 2.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestVerifyTestSuiteCount(unittest.TestCase):
    """Test verify_test_suite_count tool."""

    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures."""
        # Repo root (parent of tests dir)
        cls.repo_root = Path(__file__).parent.parent

    def setUp(self):
        """Create a temporary isolated repo structure for mutation testing.

        CRITICAL: Tests must NEVER mutate the real repo. --regenerate writes to
        tests/CLAUDE.md, so all tests must use an isolated temp repo to avoid
        polluting the real repository.
        """
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)

        # Create complete temp repo structure
        tools_dir = self.temp_root / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)

        # Copy the tool itself
        tool_path = self.repo_root / "tools" / "verify_test_suite_count.py"
        if tool_path.exists():
            (tools_dir / "verify_test_suite_count.py").write_text(tool_path.read_text())

        # Create tests directory with CLAUDE.md
        tests_dir = self.temp_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        claudemd_src = self.repo_root / "tests" / "CLAUDE.md"
        claudemd_dst = tests_dir / "CLAUDE.md"
        claudemd_dst.write_text(claudemd_src.read_text())

        # Create minimal test files so git ls-files can count them
        # IMPORTANT: These must match the counts in CLAUDE.md for tests to pass
        (tests_dir / "test_a.py").touch()
        (tests_dir / "test_b.py").touch()
        (tests_dir / "test_a.test.mjs").touch()
        (tests_dir / "test_b.test.mjs").touch()
        # Named to match exactly ONE of the shell globs (a `test_a.test.sh` name
        # matches both `tests/*.test.sh` and `tests/test_*.sh` and is counted twice).
        (tests_dir / "test_a.sh").touch()

        # Initialize a minimal git repo in temp_root so git ls-files works
        # This is ESSENTIAL: the tool calls git ls-files to count files
        subprocess.run(
            ["git", "init"],
            cwd=str(self.temp_root),
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(self.temp_root),
            capture_output=True,
            check=False,
        )

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def _run_tool(self, *args):
        """Run the verify_test_suite_count.py tool in the isolated temp repo.

        CRITICAL: All tests must run the tool in self.temp_root, NOT in
        self.repo_root, to avoid mutating the real repository.
        """
        # Use sys.executable for cross-platform compatibility
        cmd = [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py")]
        cmd.extend(args)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',  # Ensure UTF-8 encoding for cross-platform compatibility
            cwd=str(self.temp_root),  # Run in isolated temp repo, NOT real repo
            timeout=30,
        )
        return result

    def test_check_mode_passes_when_counts_match(self):
        """--check should exit 0 when counts match actual files."""
        # Ensure counts match in the temp repo
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()
        # Update counts to match the minimal test files we created (2 py, 2 mjs, 1 sh)
        updated = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (2 suites)**:",
            content,
        )
        updated = re.sub(
            r"\*\*Node \(\d+ suites?\)\*\*:",
            "**Node (2 suites)**:",
            updated,
        )
        updated = re.sub(
            r"\*\*Shell \(\d+ suites?\)\*\*:",
            "**Shell (1 suites)**:",
            updated,
        )
        claudemd_path.write_text(updated)

        result = self._run_tool("--check")
        self.assertEqual(
            result.returncode,
            0,
            f"Expected --check to pass with correct counts. stderr: {result.stderr}",
        )

    def test_check_mode_is_read_only_on_drift(self):
        """--check MUST NOT write: drifted counts leave the file byte-identical, exit non-zero.

        Defect being fixed (plan finding F4): --check auto-corrected drift by WRITING
        tests/CLAUDE.md and exiting 0. That made a "check" mode mutate the tree and made
        drift structurally unable to fail CI (fail-open gate).
        """
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text(encoding="utf-8")
        drifted = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (99999 suites)**:",
            content,
        )
        claudemd_path.write_text(drifted, encoding="utf-8")
        before = claudemd_path.read_bytes()

        result = self._run_tool("--check")

        after = claudemd_path.read_bytes()
        self.assertEqual(
            before,
            after,
            "--check MUST be read-only; tests/CLAUDE.md was mutated by a check-mode run",
        )
        self.assertEqual(
            result.returncode,
            1,
            f"--check must fail closed (exit 1) on count drift. stdout: {result.stdout}",
        )

    def test_check_mode_reports_drift_and_hints_regenerate(self):
        """--check reports the drifted numbers and points at --regenerate.

        Replaces the former test_check_mode_auto_corrects_drift, which asserted the
        defect (exit 0 + in-place rewrite) as if it were the contract.
        """
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text(encoding="utf-8")
        drifted = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (99999 suites)**:",
            content,
        )
        claudemd_path.write_text(drifted, encoding="utf-8")

        result = self._run_tool("--check")

        self.assertEqual(
            result.returncode,
            1,
            f"Expected --check to fail closed on drift. stdout: {result.stdout}",
        )
        self.assertIn("[DRIFT]", result.stdout, "Should report drift")
        self.assertIn("99999", result.stdout, "Should name the documented count")
        self.assertIn("--regenerate", result.stdout, "Should hint at --regenerate")
        self.assertIn(
            "99999",
            claudemd_path.read_text(encoding="utf-8"),
            "--check must leave the stale literal in place (read-only)",
        )

    def test_strict_is_read_only_alias_of_check(self):
        """--strict behaves exactly like --check today (reserved for main-only wiring)."""
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text(encoding="utf-8")
        drifted = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (99999 suites)**:",
            content,
        )
        claudemd_path.write_text(drifted, encoding="utf-8")
        before = claudemd_path.read_bytes()

        result = self._run_tool("--strict")

        self.assertEqual(
            result.returncode,
            1,
            f"Expected --strict to fail closed on drift. stdout: {result.stdout}",
        )
        self.assertEqual(
            before,
            claudemd_path.read_bytes(),
            "--strict MUST be read-only",
        )

    def test_check_and_regenerate_are_mutually_exclusive(self):
        """Passing a read-only mode and a writing mode together is a usage error."""
        result = self._run_tool("--check", "--regenerate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("mutually exclusive", result.stderr)

    def test_regenerate_writes_and_makes_check_pass(self):
        """--regenerate is the writing mode: it fixes drift, then --check goes green."""
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text(encoding="utf-8")
        drifted = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (99999 suites)**:",
            content,
        )
        claudemd_path.write_text(drifted, encoding="utf-8")

        regen = self._run_tool("--regenerate")
        self.assertEqual(
            regen.returncode,
            0,
            f"Expected --regenerate to succeed. stderr: {regen.stderr}",
        )
        self.assertNotIn(
            "99999",
            claudemd_path.read_text(encoding="utf-8"),
            "--regenerate should have rewritten the stale literal",
        )

        recheck = self._run_tool("--check")
        self.assertEqual(
            recheck.returncode,
            0,
            f"Expected --check to pass after --regenerate. stdout: {recheck.stdout}",
        )

    def test_fix_is_deprecated_alias_for_regenerate(self):
        """--fix keeps working as an alias so existing callers (auto_merge.py) do not break."""
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text(encoding="utf-8")
        drifted = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (99999 suites)**:",
            content,
        )
        claudemd_path.write_text(drifted, encoding="utf-8")

        result = self._run_tool("--fix")
        self.assertEqual(
            result.returncode,
            0,
            f"Expected --fix alias to succeed. stderr: {result.stderr}",
        )
        self.assertNotIn("99999", claudemd_path.read_text(encoding="utf-8"))

    def test_check_mode_fails_on_missing_sections(self):
        """--check should exit 1 if documented sections are missing (real invariant)."""
        # Break the invariant by removing a documented section
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()
        # Remove the Python section header entirely
        corrupted = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python SECTION REMOVED**:",
            content
        )
        claudemd_path.write_text(corrupted)

        # Run --check which should fail because the section is missing
        result = self._run_tool("--check")

        # Real invariant broken: should exit 1
        self.assertEqual(
            result.returncode,
            1,
            f"Expected exit 1 for missing sections. stdout: {result.stdout}",
        )

    def test_regenerate_dry_run_does_not_write(self):
        """--regenerate --dry-run reports the change but leaves the file untouched."""
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        before = claudemd_path.read_bytes()

        result = self._run_tool("--regenerate", "--dry-run")

        self.assertEqual(
            result.returncode,
            0,
            f"Expected --regenerate --dry-run to succeed. stderr: {result.stderr}",
        )
        self.assertIn("[DRY-RUN]", result.stdout)
        self.assertEqual(
            before,
            claudemd_path.read_bytes(),
            "--dry-run must not write",
        )

    def test_regenerate_is_idempotent(self):
        """Running --regenerate twice produces identical file bytes."""
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"

        result1 = self._run_tool("--regenerate")
        self.assertEqual(
            result1.returncode,
            0,
            f"Expected first --regenerate to succeed. stderr: {result1.stderr}",
        )
        after_first = claudemd_path.read_bytes()

        result2 = self._run_tool("--regenerate")
        self.assertEqual(
            result2.returncode,
            0,
            f"Expected second --regenerate to be idempotent. stderr: {result2.stderr}",
        )
        self.assertEqual(
            after_first,
            claudemd_path.read_bytes(),
            "--regenerate must be idempotent",
        )
        self.assertIn("[OK]", result2.stdout, "Second run should report no changes needed")

    def test_check_mode_fails_on_zero_files_found(self):
        """--check should exit 2 when no files found but CLAUDE.md expects counts (cannot evaluate).

        This tests the fail-closed path: if git ls-files returns zero files (actual == (0,0,0))
        but CLAUDE.md documents non-zero counts, the tool cannot evaluate the state and exits 2.
        """
        # Create CLAUDE.md with non-zero documented counts
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()
        # Ensure CLAUDE.md documents non-zero counts
        updated = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            "**Python (5 suites)**:",
            content,
        )
        updated = re.sub(
            r"\*\*Node \(\d+ suites?\)\*\*:",
            "**Node (3 suites)**:",
            updated,
        )
        updated = re.sub(
            r"\*\*Shell \(\d+ suites?\)\*\*:",
            "**Shell (2 suites)**:",
            updated,
        )
        claudemd_path.write_text(updated)

        # Now remove all test files so git ls-files returns zero
        tests_dir = self.temp_root / "tests"
        for f in tests_dir.glob("test_*"):
            f.unlink()

        # Re-stage (remove from git's view)
        subprocess.run(
            ["git", "-C", str(self.temp_root), "add", "-A"],
            capture_output=True,
            check=False,
        )

        # Run --check which should detect cannot-evaluate and exit 2
        result = self._run_tool("--check")

        # Cannot-evaluate: should exit 2
        self.assertEqual(
            result.returncode,
            2,
            f"Expected exit 2 for zero files with documented counts. stderr: {result.stderr}",
        )
        self.assertIn("[ERROR]", result.stderr, "Should report error on stderr")
        self.assertIn("Cannot evaluate", result.stderr, "Should mention cannot evaluate")

    def test_tool_provides_help(self):
        """Tool should provide --help documentation."""
        result = self._run_tool("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("verify", result.stdout.lower())

    def test_check_mode_normal_match_unchanged(self):
        """--check with matching counts exits 0 and leaves the file byte-identical."""
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"

        # Bring the doc in sync using the writing mode (the only mode allowed to write).
        regen = self._run_tool("--regenerate")
        self.assertEqual(
            regen.returncode,
            0,
            f"Expected --regenerate to succeed. stderr: {regen.stderr}",
        )
        original_bytes = claudemd_path.read_bytes()

        result = self._run_tool("--check")

        self.assertEqual(
            result.returncode,
            0,
            f"Expected --check to pass with matching counts. stderr: {result.stderr}",
        )
        self.assertIn("[OK]", result.stdout, "Should report OK when counts match")
        self.assertEqual(
            original_bytes,
            claudemd_path.read_bytes(),
            "--check must never modify the file, even on the happy path",
        )

    def test_regenerate_fails_closed_on_zero_files_found(self):
        """--regenerate must not zero out real counts when git returns nothing."""
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text(encoding="utf-8")
        updated = re.sub(r"\*\*Python \(\d+ suites?\)\*\*:", "**Python (5 suites)**:", content)
        updated = re.sub(r"\*\*Node \(\d+ suites?\)\*\*:", "**Node (3 suites)**:", updated)
        updated = re.sub(r"\*\*Shell \(\d+ suites?\)\*\*:", "**Shell (2 suites)**:", updated)
        claudemd_path.write_text(updated, encoding="utf-8")

        tests_dir = self.temp_root / "tests"
        for f in tests_dir.glob("test_*"):
            f.unlink()
        subprocess.run(
            ["git", "-C", str(self.temp_root), "add", "-A"],
            capture_output=True,
            check=False,
            cwd=str(self.temp_root),
        )

        before = claudemd_path.read_bytes()
        result = self._run_tool("--regenerate")

        self.assertEqual(
            result.returncode,
            2,
            f"Expected exit 2 (cannot evaluate). stdout: {result.stdout}",
        )
        self.assertEqual(
            before,
            claudemd_path.read_bytes(),
            "--regenerate must not rewrite counts it cannot derive",
        )

    def test_check_mode_detects_duplicated_python_count_line(self):
        """--check should exit 1 when detecting duplicated Python count lines."""
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()

        # Inject a duplicated Python count line with proper pattern
        # Format: **Python (N suites)**: (note the closing ** before the colon)
        duplicated = re.sub(
            r"(\*\*Python \(\d+ suites?\)\*\*:)",
            r"\1\n\n... some text ...\n\n**Python (2 suites)**:",
            content,
            count=1,
        )
        claudemd_path.write_text(duplicated)

        # Run --check which should detect the duplicate and exit 1
        result = self._run_tool("--check")

        self.assertEqual(
            result.returncode,
            1,
            f"Expected exit 1 for duplicated Python count line. stderr: {result.stderr}",
        )
        self.assertIn("duplicated", result.stderr.lower(), "Should mention duplicated lines")
        self.assertIn("Python", result.stderr, "Should name the duplicated line type")

    def test_check_mode_detects_duplicated_node_count_line(self):
        """--check should exit 1 when detecting duplicated Node count lines."""
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()

        # Inject a duplicated Node count line with proper pattern
        duplicated = re.sub(
            r"(\*\*Node \(\d+ suites?\)\*\*:)",
            r"\1\n\n... some text ...\n\n**Node (2 suites)**:",
            content,
            count=1,
        )
        claudemd_path.write_text(duplicated)

        # Run --check which should detect the duplicate and exit 1
        result = self._run_tool("--check")

        self.assertEqual(
            result.returncode,
            1,
            f"Expected exit 1 for duplicated Node count line. stderr: {result.stderr}",
        )
        self.assertIn("duplicated", result.stderr.lower(), "Should mention duplicated lines")
        self.assertIn("Node", result.stderr, "Should name the duplicated line type")

    def test_check_mode_detects_duplicated_shell_count_line(self):
        """--check should exit 1 when detecting duplicated Shell count lines."""
        claudemd_path = self.temp_root / "tests" / "CLAUDE.md"
        content = claudemd_path.read_text()

        # Inject a duplicated Shell count line with proper pattern
        duplicated = re.sub(
            r"(\*\*Shell \(\d+ suites?\)\*\*:)",
            r"\1\n\n... some text ...\n\n**Shell (1 suites)**:",
            content,
            count=1,
        )
        claudemd_path.write_text(duplicated)

        # Run --check which should detect the duplicate and exit 1
        result = self._run_tool("--check")

        self.assertEqual(
            result.returncode,
            1,
            f"Expected exit 1 for duplicated Shell count line. stderr: {result.stderr}",
        )
        self.assertIn("duplicated", result.stderr.lower(), "Should mention duplicated lines")
        self.assertIn("Shell", result.stderr, "Should name the duplicated line type")


class TestCountLineScanning(unittest.TestCase):
    """Adversarial format-variant cases against the exactly-one count-line assertion.

    Cases C-H reproduce the evasions and false positives found by the refine-r1
    adversarial lens: the original exact-literal regex was blind to spacing and
    colon-placement variants, to Unicode homoglyph labels, and to markdown fence /
    HTML-comment context (in both directions).

    These fixtures live in a temp file passed via --claudemd; the tool is invoked
    with cwd=<repo root> so the derived counts come from the real repo.
    """

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).parent.parent
        cls.tool = cls.repo_root / "tools" / "verify_test_suite_count.py"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.md = Path(self.temp_dir.name) / "CLAUDE.md"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, *args):
        cmd = [sys.executable, str(self.tool), "--claudemd", str(self.md)]
        cmd.extend(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(self.repo_root),
            timeout=30,
        )

    def _write(self, body):
        self.md.write_text(body, encoding="utf-8")

    def _synced_baseline(self):
        """Write a three-label doc and regenerate it to the repo's real counts."""
        self._write(
            "# fixture\n\n"
            "**Node (0 suites)**: node\n\n"
            "**Shell (0 suites)**: shell\n\n"
            "**Python (0 suites)**: python\n"
        )
        result = self._run("--regenerate")
        self.assertEqual(
            result.returncode,
            0,
            f"Baseline regeneration failed. stderr: {result.stderr}",
        )
        control = self._run("--check")
        self.assertEqual(
            control.returncode,
            0,
            f"Control fixture should be clean. stdout: {control.stdout}",
        )
        return self.md.read_text(encoding="utf-8")

    # --- Case C/D: spacing and colon-placement variants must count as count lines ---

    def test_case_c_space_before_colon_duplicate_is_caught(self):
        """`**Python (99 suites)** :` is a count line and must trip dup detection."""
        base = self._synced_baseline()
        self._write(base + "\n**Python (99 suites)** :\n")

        result = self._run("--check")

        self.assertEqual(
            result.returncode,
            1,
            f"Spacing-variant duplicate must be caught. stdout: {result.stdout}",
        )
        self.assertIn("duplicated", result.stderr.lower())
        self.assertIn("Python", result.stderr)

    def test_case_d_colon_inside_bold_duplicate_is_caught(self):
        """`**Python (99 suites):**` is a count line and must trip dup detection."""
        base = self._synced_baseline()
        self._write(base + "\n**Python (99 suites):**\n")

        result = self._run("--check")

        self.assertEqual(
            result.returncode,
            1,
            f"Colon-inside-bold duplicate must be caught. stdout: {result.stdout}",
        )
        self.assertIn("duplicated", result.stderr.lower())

    def test_case_d_variant_is_accepted_as_the_sole_count_line(self):
        """A lone colon-inside-bold line is a valid count line, not a missing section."""
        base = self._synced_baseline()
        python_count = re.search(r"\*\*Python \((\d+) suites?\)\*\*:", base).group(1)
        swapped = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            f"**Python ({python_count} suites):**",
            base,
        )
        self._write(swapped)

        result = self._run("--check")

        self.assertEqual(
            result.returncode,
            0,
            f"Colon-inside-bold should be recognized as the count line. "
            f"stdout: {result.stdout} stderr: {result.stderr}",
        )

    # --- Case E: homoglyph labels must be MALFORMED, not invisible ---

    def test_case_e_homoglyph_label_is_flagged_malformed(self):
        """A Cyrillic-o `Pythоn` label must be reported, never silently ignored."""
        base = self._synced_baseline()
        self._write(base + "\n**Pythоn (99 suites)**:\n")

        result = self._run("--check")

        self.assertEqual(
            result.returncode,
            1,
            f"Homoglyph label must be flagged. stdout: {result.stdout}",
        )
        self.assertIn("MALFORMED", result.stderr)

    # --- Case F/G/H: fence and HTML-comment context ---

    def test_case_f_fenced_only_occurrence_does_not_satisfy_exactly_one(self):
        """A count line that exists only inside a fence is not a real count line."""
        self._write(
            "# fixture\n\n"
            "**Node (0 suites)**: node\n\n"
            "**Shell (0 suites)**: shell\n\n"
            "```markdown\n"
            "**Python (99 suites)**: python\n"
            "```\n"
        )

        result = self._run("--check")

        self.assertEqual(
            result.returncode,
            1,
            f"Fenced-only count line must not satisfy the requirement. stdout: {result.stdout}",
        )
        self.assertIn("Missing", result.stderr)
        self.assertIn("Python", result.stderr)

    def test_case_g_fenced_format_example_is_not_a_duplicate(self):
        """Documenting the count-line format in a fence must not be a false positive."""
        base = self._synced_baseline()
        self._write(
            base
            + "\nFormat reference:\n\n```markdown\n**Python (227 suites)**: python\n```\n"
        )

        result = self._run("--check")

        self.assertEqual(
            result.returncode,
            0,
            f"Fenced format example must not count. stdout: {result.stdout} "
            f"stderr: {result.stderr}",
        )

    def test_case_h_html_comment_is_not_a_duplicate(self):
        """A commented-out count line must not be a false positive."""
        base = self._synced_baseline()
        self._write(base + "\n<!-- **Python (99 suites)**: old -->\n")

        result = self._run("--check")

        self.assertEqual(
            result.returncode,
            0,
            f"HTML-commented count line must not count. stdout: {result.stdout} "
            f"stderr: {result.stderr}",
        )

    # --- Error text must teach the canonical format the regex actually prefers ---

    def test_error_text_uses_canonical_format(self):
        """Missing-section errors must show `**Python (N suites)**:`, colon outside bold."""
        self._write(
            "# fixture\n\n"
            "**Node (0 suites)**: node\n\n"
            "**Shell (0 suites)**: shell\n"
        )

        result = self._run("--check")

        self.assertEqual(result.returncode, 1)
        self.assertIn("**Python (N suites)**:", result.stderr)
        self.assertNotIn(
            "**Python (N suites):**",
            result.stderr,
            "Error text must not instruct the user into a non-canonical format",
        )

    # --- The writing mode must never launder a duplicate into a green tree ---

    def test_regenerate_refuses_to_launder_duplicates(self):
        """--regenerate must not rewrite BOTH duplicate lines and exit 0.

        Regression: the old --fix used unbounded re.sub with no exactly-one
        assertion, so it rewrote every duplicate to the same number and exited 0 --
        producing a file its own --check rejected.
        """
        base = self._synced_baseline()
        self._write(base + "\n**Python (99 suites)**: dup\n")
        before = self.md.read_bytes()

        result = self._run("--regenerate")

        self.assertEqual(
            result.returncode,
            1,
            f"--regenerate must refuse a duplicated count line. stdout: {result.stdout}",
        )
        self.assertEqual(
            before,
            self.md.read_bytes(),
            "--regenerate must not rewrite a file whose structure is invalid",
        )
        self.assertEqual(
            len(re.findall(r"\*\*Python \(\d+ suites?\)\*\*", self.md.read_text(encoding="utf-8"))),
            2,
            "the duplicate should still be visible for a human to resolve",
        )

    def test_regenerate_does_not_touch_fenced_examples(self):
        """--regenerate rewrites the real count line only, never a fenced example."""
        self._write(
            "# fixture\n\n"
            "**Node (0 suites)**: node\n\n"
            "**Shell (0 suites)**: shell\n\n"
            "**Python (0 suites)**: python\n\n"
            "```markdown\n"
            "**Python (12345 suites)**: format example\n"
            "```\n"
        )

        result = self._run("--regenerate")

        self.assertEqual(
            result.returncode,
            0,
            f"Expected --regenerate to succeed. stderr: {result.stderr}",
        )
        content = self.md.read_text(encoding="utf-8")
        self.assertIn(
            "**Python (12345 suites)**: format example",
            content,
            "the fenced example must be left alone",
        )

    def test_regenerate_normalizes_variant_to_canonical_form(self):
        """A variant count line is rewritten in the canonical `**X (N suites)**:` form."""
        self._write(
            "# fixture\n\n"
            "**Node (0 suites)**: node\n\n"
            "**Shell (0 suites)**: shell\n\n"
            "**Python (0 suites):** python\n"
        )

        result = self._run("--regenerate")

        self.assertEqual(
            result.returncode,
            0,
            f"Expected --regenerate to succeed. stderr: {result.stderr}",
        )
        content = self.md.read_text(encoding="utf-8")
        self.assertRegex(content, r"\*\*Python \(\d+ suites\)\*\*:")
        self.assertNotIn("(0 suites):**", content)

        recheck = self._run("--check")
        self.assertEqual(
            recheck.returncode,
            0,
            f"--check must pass after normalization. stdout: {recheck.stdout}",
        )


class TestRepoRootAndVacuousGuards(unittest.TestCase):
    """--repo must actually be honored, and a per-family wipeout must fail closed.

    Two P1s from the regression-adversarial lens:
    1. get_actual_counts() ignored its repo_root argument -- every `git ls-files`
       ran in the process CWD, so `--repo <empty tree>` silently graded the CWD
       repo and reported "[OK] counts match".
    2. The vacuous-zero guard was an AND over all three families, so wiping out a
       single language (227 Python suites deleted) sailed through and the doc was
       rewritten to 0 with exit 0.
    """

    @classmethod
    def setUpClass(cls):
        cls.aesop_root = Path(__file__).parent.parent
        cls.tool = cls.aesop_root / "tools" / "verify_test_suite_count.py"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.md = self.temp_root / "fixture-CLAUDE.md"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, *args, cwd=None):
        cmd = [sys.executable, str(self.tool)]
        cmd.extend(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(cwd or self.aesop_root),
            timeout=30,
        )

    def _write_doc(self, node, shell, python):
        self.md.write_text(
            "# fixture\n\n"
            f"**Node ({node} suites)**: node\n\n"
            f"**Shell ({shell} suites)**: shell\n\n"
            f"**Python ({python} suites)**: python\n",
            encoding="utf-8",
        )

    def _make_repo(self, name, node=0, shell=0, python=0):
        """Create a git repo with the requested number of test files."""
        root = self.temp_root / name
        tests_dir = root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        for i in range(node):
            (tests_dir / f"node_{i}.test.mjs").write_text("// node\n", encoding="utf-8")
        for i in range(shell):
            (tests_dir / f"test_shell_{i}.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        for i in range(python):
            (tests_dir / f"test_py_{i}.py").write_text("# python\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=str(root), capture_output=True, check=False)
        subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, check=False)
        return root

    def test_repo_flag_is_honored_not_the_cwd(self):
        """--repo must derive counts from THAT tree, not the process CWD."""
        target = self._make_repo("target", node=2, shell=3, python=4)
        self._write_doc(2, 3, 4)

        # Run from the aesop repo (whose real counts are nothing like 2/3/4).
        result = self._run("--check", "--repo", str(target), "--claudemd", str(self.md))

        self.assertEqual(
            result.returncode,
            0,
            f"--repo counts should come from the target tree. "
            f"stdout: {result.stdout} stderr: {result.stderr}",
        )

    def test_repo_flag_drift_is_detected_against_target_tree(self):
        """A doc that matches the CWD repo but not --repo must still fail."""
        target = self._make_repo("target", node=2, shell=3, python=4)
        self._write_doc(2, 3, 99)

        result = self._run("--check", "--repo", str(target), "--claudemd", str(self.md))

        self.assertEqual(
            result.returncode,
            1,
            f"Drift against the --repo tree must be caught. stdout: {result.stdout}",
        )
        self.assertIn("Python", result.stdout)

    def test_non_git_repo_target_fails_closed(self):
        """--repo pointing at a non-git tree must exit 2, never grade the CWD."""
        target = self.temp_root / "not-a-repo"
        (target / "tests").mkdir(parents=True)
        self._write_doc(26, 13, 227)

        result = self._run("--check", "--repo", str(target), "--claudemd", str(self.md))

        self.assertEqual(
            result.returncode,
            2,
            f"Non-git target must fail closed. stdout: {result.stdout} "
            f"stderr: {result.stderr}",
        )
        self.assertIn("git repository", result.stderr)

    def test_single_family_wipeout_fails_closed(self):
        """One family collapsing to zero while documented non-zero must exit 2.

        Previously the guard was `actual == (0,0,0)`, so a Python-only wipeout was
        treated as ordinary drift and auto-blessed.
        """
        target = self._make_repo("target", node=2, shell=3, python=0)
        self._write_doc(2, 3, 227)

        result = self._run("--check", "--repo", str(target), "--claudemd", str(self.md))

        self.assertEqual(
            result.returncode,
            2,
            f"Single-family wipeout must fail closed. stdout: {result.stdout} "
            f"stderr: {result.stderr}",
        )
        self.assertIn("Python", result.stderr)

    def test_single_family_wipeout_is_not_regenerated_away(self):
        """--regenerate must refuse to rewrite a wiped-out family to 0."""
        target = self._make_repo("target", node=2, shell=3, python=0)
        self._write_doc(2, 3, 227)
        before = self.md.read_bytes()

        result = self._run("--regenerate", "--repo", str(target), "--claudemd", str(self.md))

        self.assertEqual(
            result.returncode,
            2,
            f"--regenerate must refuse a wiped-out family. stdout: {result.stdout}",
        )
        self.assertEqual(
            before,
            self.md.read_bytes(),
            "--regenerate must not rewrite the count to 0",
        )

    def test_deliberate_zero_family_is_accepted(self):
        """Documenting 0 for a genuinely empty family is the sanctioned escape hatch."""
        target = self._make_repo("target", node=2, shell=3, python=0)
        self._write_doc(2, 3, 0)

        result = self._run("--check", "--repo", str(target), "--claudemd", str(self.md))

        self.assertEqual(
            result.returncode,
            0,
            f"A documented zero for an empty family should pass. "
            f"stdout: {result.stdout} stderr: {result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
