#!/usr/bin/env python3
"""TDD tests for CLAUDE.md semantic drift detector.

Tests the drift detector's ability to find:
1. Files/commands referenced in CLAUDE.md that don't exist
2. Domain dirs on disk missing from root map
3. Root-map entries whose dirs are gone
4. Documented CLI flags absent from --help
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

try:
    from claudemd_drift import (
        extract_domain_map,
        extract_cli_specs,
        check_domain_dirs_exist,
        check_root_map_complete,
        check_cli_flags,
        check_file_references,
        run_drift_check,
    )
except ImportError:
    # Tool doesn't exist yet; tests should fail gracefully
    extract_domain_map = None


class TestDomainMapExtraction(unittest.TestCase):
    """Test extraction of domain map from root CLAUDE.md."""

    def test_extract_domain_map_basic(self):
        """Extract domain entries from CLAUDE.md."""
        if extract_domain_map is None:
            self.skipTest("claudemd_drift not yet implemented")

        root_claude = """
## Domain map

- **skills/** — Orchestration skills — read skills/CLAUDE.md
- **daemons/** — Watchdog daemon — read daemons/CLAUDE.md
- **tools/** — Build utilities — read tools/CLAUDE.md
"""
        domains = extract_domain_map(root_claude)
        self.assertIn("skills", domains)
        self.assertIn("daemons", domains)
        self.assertIn("tools", domains)

    def test_extract_domain_map_ignores_non_slashed(self):
        """Domain map entries must be directory-like (end with /)."""
        if extract_domain_map is None:
            self.skipTest("claudemd_drift not yet implemented")

        root_claude = """
## Domain map

- **skills/** — Orchestration skills
- **not-a-dir** — This shouldn't be in the map
"""
        domains = extract_domain_map(root_claude)
        self.assertIn("skills", domains)
        # non-slash entries should be ignored
        self.assertNotIn("not-a-dir", domains)


class TestCliSpecExtraction(unittest.TestCase):
    """Test extraction of CLI specifications from tool descriptions."""

    def test_extract_cli_flags_from_tool_index(self):
        """Extract CLI flags from tool index entries."""
        if extract_cli_specs is None:
            self.skipTest("claudemd_drift not yet implemented")

        # Test with realistic tool index format
        tool_line = (
            "`secret_scan.py` — Pre-push secret/credential detection gate; "
            "CLI: `--staged [--repo PATH]` | `--history`"
        )
        specs = extract_cli_specs("secret_scan.py", tool_line)
        self.assertIn("--staged", specs)
        # --history and --repo should be captured
        self.assertIn("--history", specs, f"Got specs: {specs}")
        self.assertIn("--repo", specs)

    def test_extract_cli_flags_with_args(self):
        """Extract CLI flags that take arguments."""
        if extract_cli_specs is None:
            self.skipTest("claudemd_drift not yet implemented")

        tool_line = (
            "`defect_escape.py` — Quality telemetry; "
            "CLI: `--repo <path> --since <ISO date> [--json]`"
        )
        specs = extract_cli_specs("defect_escape.py", tool_line)
        self.assertIn("--repo", specs)
        self.assertIn("--since", specs)
        self.assertIn("--json", specs)


class TestDomainDirChecks(unittest.TestCase):
    """Test checking that domain directories exist."""

    def test_check_domain_dirs_exist_basic(self):
        """Check that referenced domains exist as directories."""
        if check_domain_dirs_exist is None:
            self.skipTest("claudemd_drift not yet implemented")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            # Create some domains
            (tmproot / "tools").mkdir()
            (tmproot / "daemons").mkdir()

            domains = {"tools": None, "daemons": None, "missing": None}
            findings = check_domain_dirs_exist(domains, tmproot)

            # Should find "missing" as a drift
            drift_messages = [f["message"] for f in findings]
            missing_found = any("missing" in msg for msg in drift_messages)
            self.assertTrue(missing_found)


class TestRootMapCompleteness(unittest.TestCase):
    """Test checking for domains on disk missing from root map."""

    def test_check_root_map_complete(self):
        """Detect domains on disk not in root map."""
        if check_root_map_complete is None:
            self.skipTest("claudemd_drift not yet implemented")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            # Create a domain dir not in the map
            (tmproot / "tools").mkdir()
            (tmproot / "orphan_domain").mkdir()

            mapped_domains = {"tools": None}
            findings = check_root_map_complete(mapped_domains, tmproot)

            # Should find "orphan_domain" as unmapped
            drift_messages = [f["message"] for f in findings]
            orphan_found = any("orphan_domain" in msg for msg in drift_messages)
            self.assertTrue(orphan_found)


class TestCliFlagValidation(unittest.TestCase):
    """Test validating CLI flags exist in tool help."""

    def test_check_cli_flags_basic(self):
        """Check that documented flags exist in --help output."""
        if check_cli_flags is None:
            self.skipTest("claudemd_drift not yet implemented")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            tool_path = tmproot / "tools" / "test_tool.py"
            tool_path.parent.mkdir(parents=True)
            # Create a minimal Python tool
            tool_path.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "if '--help' in sys.argv:\n"
                "    print('--valid-flag    Description')\n"
            )

            cli_specs = {
                "test_tool.py": {"--valid-flag", "--missing-flag"}
            }

            with patch("subprocess.run") as mock_run:
                # Mock help output
                mock_run.return_value = MagicMock(
                    stdout="--valid-flag    Description\n",
                    returncode=0
                )
                findings = check_cli_flags(cli_specs, tmproot)

                # Should report missing flag
                drift_messages = [f["message"] for f in findings]
                missing_found = any("--missing-flag" in msg for msg in drift_messages)
                self.assertTrue(missing_found)


class TestFileReferenceValidation(unittest.TestCase):
    """Test validating file references in CLAUDE.md."""

    def test_check_file_references(self):
        """Check that referenced files exist."""
        if check_file_references is None:
            self.skipTest("claudemd_drift not yet implemented")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            # Create a domain dir with CLAUDE.md
            domain_dir = tmproot / "tools"
            domain_dir.mkdir()
            (domain_dir / "helper.py").write_text("# helper")

            claudemd_content = """
Some text referencing `tools/helper.py`.
But this file does not exist: `tools/missing.py`.
"""
            domain_dir.joinpath("CLAUDE.md").write_text(claudemd_content)

            findings = check_file_references("tools", domain_dir, tmproot)

            # Should report tools/missing.py as missing
            drift_messages = [f["message"] for f in findings]
            missing_found = any("tools/missing.py" in msg or "missing.py" in msg for msg in drift_messages)
            self.assertTrue(missing_found, f"No findings for missing file. Got: {findings}")


class TestIntegration(unittest.TestCase):
    """Test full drift detection workflow."""

    def test_run_drift_check_json_output(self):
        """Run full drift check and verify JSON output format."""
        if run_drift_check is None:
            self.skipTest("claudemd_drift not yet implemented")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            # Create minimal repo structure
            (tmproot / "tools").mkdir()
            root_claude = tmproot / "CLAUDE.md"
            root_claude.write_text(
                """
## Domain map

- **tools/** — Build utilities — read tools/CLAUDE.md
"""
            )

            findings = run_drift_check(tmproot)
            self.assertIsInstance(findings, list)
            # Each finding should have required fields
            for finding in findings:
                self.assertIn("type", finding)
                self.assertIn("message", finding)
                self.assertIn("domain", finding)

    def test_run_drift_check_exit_code(self):
        """Verify exit code on findings."""
        if run_drift_check is None:
            self.skipTest("claudemd_drift not yet implemented")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            (tmproot / "tools").mkdir()
            # Create incomplete domain map
            (tmproot / "missing_domain").mkdir()
            root_claude = tmproot / "CLAUDE.md"
            root_claude.write_text(
                """
## Domain map

- **tools/** — Build utilities
"""
            )

            findings = run_drift_check(tmproot)
            # Should have findings for missing_domain not being mapped
            self.assertTrue(len(findings) > 0)


class TestRegressionNoDrifts(unittest.TestCase):
    """Regression tests for clean repos (no expected drift)."""

    def test_no_drift_on_valid_repo(self):
        """Valid repo should have no drift findings."""
        if run_drift_check is None:
            self.skipTest("claudemd_drift not yet implemented")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            # Create valid repo structure
            domains = ["tools", "daemons", "hooks"]
            for domain in domains:
                domain_dir = tmproot / domain
                domain_dir.mkdir()
                (domain_dir / "CLAUDE.md").write_text(
                    f"# {domain}\n\nDomain docs.\n",
                    encoding="utf-8"
                )

            root_claude = tmproot / "CLAUDE.md"
            root_claude.write_text(
                """## Domain map

- **tools/** — Build utilities — read tools/CLAUDE.md
- **daemons/** — Watchdog — read daemons/CLAUDE.md
- **hooks/** — Git hooks — read hooks/CLAUDE.md
""",
                encoding="utf-8"
            )

            findings = run_drift_check(tmproot)
            # No drift should be found
            self.assertEqual(len(findings), 0, f"Unexpected findings: {findings}")


if __name__ == "__main__":
    unittest.main()
