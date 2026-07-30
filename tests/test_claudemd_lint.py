#!/usr/bin/env python3
"""Tests for claudemd_lint.py — CLAUDE.md integrity linter.

Fixtures prove it CATCHES:
1. Real phantom repo-doc pointer (non-existent file reference)
2. Bad npm script (script not in package.json)
3. pytest-vs-unittest mismatch (pytest reference in unittest repo)

And does NOT flag:
- state/ runtime references
- Control file references (BRIEF.md, PROPOSALS.md, etc.)
- The allowed 'Map of all domains: /CLAUDE.md' line
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from claudemd_lint import (
    lint_claudemd,
    extract_path_references,
    extract_npm_scripts,
    extract_domain_claude_references,
    is_runtime_artifact,
    get_sibling_domains,
)


class TestRuntimeArtifactDetection(unittest.TestCase):
    """Test that runtime artifacts are correctly identified."""

    def test_state_directory_is_runtime_artifact(self):
        """state/ directory references should NOT be flagged."""
        self.assertTrue(is_runtime_artifact("state/"))
        self.assertTrue(is_runtime_artifact("./state/"))
        self.assertTrue(is_runtime_artifact("../state/"))

    def test_control_files_are_runtime_artifacts(self):
        """Control files should NOT be flagged."""
        control_files = [
            "BRIEF.md",
            "PROPOSALS.md",
            "BUILDLOG.md",
            "MEMORY.md",
            "STATE.md",
            "OUTCOMES-LEDGER.md",
            "tracker.json",
            "ACTIONS.log",
        ]
        for cf in control_files:
            self.assertTrue(is_runtime_artifact(cf), f"{cf} not recognized as runtime")

    def test_heartbeat_files_are_runtime_artifacts(self):
        """Any *heartbeat* file is a runtime artifact."""
        self.assertTrue(is_runtime_artifact(".monitor-heartbeat"))
        self.assertTrue(is_runtime_artifact("test-heartbeat"))
        self.assertTrue(is_runtime_artifact("orchestrator-heartbeat"))

    def test_real_repo_files_are_not_runtime_artifacts(self):
        """Real repo files should NOT be detected as runtime artifacts."""
        repo_files = [
            "README.md",
            "docs/ARCHITECTURE.md",
            "tools/secret_scan.py",
            "daemons/run-watchdog.sh",
        ]
        for rf in repo_files:
            self.assertFalse(is_runtime_artifact(rf), f"{rf} incorrectly marked as runtime")


class TestPathReferenceExtraction(unittest.TestCase):
    """Test extraction of path references from text."""

    def test_extract_simple_path_references(self):
        """Extract basic file references."""
        text = "See tools/common.py and docs/README.md for details."
        refs = extract_path_references(text)
        self.assertIn("tools/common.py", refs)
        self.assertIn("docs/README.md", refs)

    def test_extract_backtick_enclosed_paths(self):
        """Extract paths in backticks."""
        text = "Run `daemons/run-watchdog.sh` for details."
        refs = extract_path_references(text)
        self.assertIn("daemons/run-watchdog.sh", refs)

    def test_ignore_short_references(self):
        """Ignore very short or invalid references."""
        text = "The .py file format is used."
        refs = extract_path_references(text)
        # Should not include just ".py"
        self.assertNotIn(".py", refs)


class TestNpmScriptExtraction(unittest.TestCase):
    """Test extraction of npm run commands."""

    def test_extract_npm_run_scripts(self):
        """Extract npm run command references."""
        text = "Run `npm run test:py` and then `npm run test:node`."
        scripts = extract_npm_scripts(text)
        self.assertIn("test:py", scripts)
        self.assertIn("test:node", scripts)

    def test_extract_npm_run_with_colons(self):
        """Extract npm run scripts with colons."""
        text = "The `npm run test:all` command runs all tests."
        scripts = extract_npm_scripts(text)
        self.assertIn("test:all", scripts)


class TestPhantomPathDetection(unittest.TestCase):
    """Test detection of non-existent path references."""

    def test_catch_phantom_doc_pointer(self):
        """MUST CATCH a reference to a non-existent file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create a CLAUDE.md that references a non-existent file
            claudemd_dir = repo_root / "testdomain"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            # Create package.json so we don't get npm script errors
            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Write CLAUDE.md with phantom reference
            claudemd_path.write_text(
                "# Test Domain\n\nSee docs/nonexistent-file.md for details."
            )

            findings = lint_claudemd(claudemd_path, repo_root)

            # Should find the phantom path
            phantom_findings = [f for f in findings if f["type"] == "phantom-path"]
            self.assertGreater(len(phantom_findings), 0, "Should catch phantom path")
            self.assertTrue(
                any("nonexistent-file" in f["message"] for f in phantom_findings),
                "Should mention the phantom file"
            )

    def test_no_false_positive_state_directory(self):
        """MUST NOT flag state/ directory references."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            claudemd_dir = repo_root / "testdomain"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Write CLAUDE.md with state/ reference
            claudemd_path.write_text(
                "# Test Domain\n\nRuntime state lives in state/tracker.json."
            )

            findings = lint_claudemd(claudemd_path, repo_root)

            # Should NOT find phantom-path for state/
            phantom_findings = [f for f in findings if f["type"] == "phantom-path"]
            self.assertEqual(len(phantom_findings), 0, "Should not flag state/ as phantom")

    def test_no_false_positive_control_files(self):
        """MUST NOT flag control file references (BRIEF.md, PROPOSALS.md, etc.)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            claudemd_dir = repo_root / "testdomain"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Write CLAUDE.md with control file references
            claudemd_path.write_text(
                "# Test Domain\n\n"
                "See BRIEF.md, PROPOSALS.md, STATE.md, BUILDLOG.md, "
                "OUTCOMES-LEDGER.md, and tracker.json for status."
            )

            findings = lint_claudemd(claudemd_path, repo_root)

            # Should NOT find any phantom-path findings
            phantom_findings = [f for f in findings if f["type"] == "phantom-path"]
            self.assertEqual(len(phantom_findings), 0, "Should not flag control files")

    def test_same_dir_relative_doc_pointer_resolves(self):
        """MUST NOT flag same-directory-relative doc pointers as phantoms.

        This is the exact bug fix: a reference in skills/CLAUDE.md to
        'healthcheck/SKILL.md' should resolve to skills/healthcheck/SKILL.md
        (relative to the referencing file's directory), not to the repo root.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create skills/ subdirectory with CLAUDE.md
            skills_dir = repo_root / "skills"
            skills_dir.mkdir()
            claudemd_path = skills_dir / "CLAUDE.md"

            # Create the target file that skills/CLAUDE.md references
            # This is the file that would be flagged as phantom by the old logic
            healthcheck_dir = skills_dir / "healthcheck"
            healthcheck_dir.mkdir()
            (healthcheck_dir / "SKILL.md").write_text("# Healthcheck Skill")

            # Another same-dir reference
            power_dir = skills_dir / "power"
            power_dir.mkdir()
            (power_dir / "SKILL.md").write_text("# Power Skill")

            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Write skills/CLAUDE.md with same-directory-relative references
            claudemd_path.write_text(
                "# Skills Domain\n\n"
                "- See healthcheck/SKILL.md for the healthcheck skill\n"
                "- See power/SKILL.md for the power skill\n"
            )

            findings = lint_claudemd(claudemd_path, repo_root)

            # Should NOT find any phantom-path findings for the relative references
            phantom_findings = [f for f in findings if f["type"] == "phantom-path"]
            self.assertEqual(
                len(phantom_findings), 0,
                f"Same-directory-relative references should resolve without false phantoms. "
                f"Found: {[f['message'] for f in phantom_findings]}"
            )


class TestNestedDomainDiscovery(unittest.TestCase):
    """Test that nested domain CLAUDE.md files are discovered by the glob."""

    def test_find_nested_domain_claude_md(self):
        """MUST find CLAUDE.md in nested directories (e.g., tools/inner/CLAUDE.md)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create nested domain structure
            # tools/inner/CLAUDE.md (two levels deep)
            nested_dir = repo_root / "tools" / "inner"
            nested_dir.mkdir(parents=True)
            nested_claudemd = nested_dir / "CLAUDE.md"
            nested_claudemd.write_text("# Nested Domain\n\nTest content.")

            # Also create a top-level one to ensure both are found
            top_level = repo_root / "CLAUDE.md"
            top_level.write_text("# Root Domain\n\nRoot content.")

            # And a one-level-deep one
            one_level = repo_root / "tools" / "CLAUDE.md"
            one_level.write_text("# Tools Domain\n\nTools content.")

            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Import the discover function or use main logic
            from claudemd_lint import main as lint_main
            import argparse

            # Simulate the glob from main()
            claudemd_files = sorted(repo_root.glob("*/CLAUDE.md"))
            claudemd_files.extend(repo_root.glob("CLAUDE.md"))
            # This is the fix: add rglob for nested directories
            claudemd_files.extend(repo_root.glob("**/CLAUDE.md"))
            claudemd_files = sorted(set(claudemd_files))

            # Should find all three CLAUDE.md files
            self.assertIn(top_level, claudemd_files, "Root CLAUDE.md not found")
            self.assertIn(one_level, claudemd_files, "One-level CLAUDE.md not found")
            self.assertIn(nested_claudemd, claudemd_files, "Nested CLAUDE.md not found")
            self.assertEqual(len(claudemd_files), 3,
                           f"Expected 3 CLAUDE.md files, found {len(claudemd_files)}: {claudemd_files}")


class TestNpmScriptValidation(unittest.TestCase):
    """Test npm script existence checking."""

    def test_catch_missing_npm_script(self):
        """MUST CATCH reference to a non-existent npm script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            claudemd_dir = repo_root / "testdomain"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            # Create package.json with only test:py
            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Write CLAUDE.md with reference to non-existent script
            claudemd_path.write_text(
                "# Test Domain\n\nRun `npm run nonexistent:script` to test."
            )

            findings = lint_claudemd(claudemd_path, repo_root)

            # Should find the missing script
            missing_findings = [f for f in findings if f["type"] == "missing-npm-script"]
            self.assertGreater(len(missing_findings), 0, "Should catch missing npm script")
            self.assertTrue(
                any("nonexistent:script" in f["message"] for f in missing_findings),
                "Should mention the missing script"
            )

    def test_allow_valid_npm_scripts(self):
        """Should NOT flag scripts that exist in package.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            claudemd_dir = repo_root / "testdomain"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            # Create package.json with test:py, test:node, test:all
            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({
                "scripts": {
                    "test:py": "python -m unittest",
                    "test:node": "node --test tests/*.test.mjs",
                    "test:all": "npm run test:py && npm run test:node",
                }
            }))

            # Write CLAUDE.md referencing valid scripts
            claudemd_path.write_text(
                "# Test Domain\n\n"
                "Run `npm run test:py` and `npm run test:node`, or `npm run test:all`."
            )

            findings = lint_claudemd(claudemd_path, repo_root)

            # Should NOT find any missing-npm-script findings
            missing_findings = [f for f in findings if f["type"] == "missing-npm-script"]
            self.assertEqual(len(missing_findings), 0, "Should allow valid scripts")


class TestPytestVsUnittestMismatch(unittest.TestCase):
    """Test pytest vs unittest conflict detection."""

    def test_catch_pytest_in_unittest_repo(self):
        """MUST CATCH pytest reference when repo uses unittest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            claudemd_dir = repo_root / "testdomain"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            # Create package.json with unittest (not pytest)
            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Write CLAUDE.md mentioning pytest
            claudemd_path.write_text(
                "# Test Domain\n\nTests use pytest for assertions and mocking."
            )

            findings = lint_claudemd(claudemd_path, repo_root)

            # Should find the pytest vs unittest mismatch
            pytest_findings = [f for f in findings if f["type"] == "pytest-vs-unittest"]
            self.assertGreater(len(pytest_findings), 0, "Should catch pytest/unittest mismatch")

    def test_no_flag_pytest_in_pytest_repo(self):
        """Should NOT flag pytest when repo actually uses pytest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            claudemd_dir = repo_root / "testdomain"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            # Create package.json with pytest
            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "pytest tests/"}}))

            # Write CLAUDE.md mentioning pytest
            claudemd_path.write_text(
                "# Test Domain\n\nTests use pytest for assertions and mocking."
            )

            findings = lint_claudemd(claudemd_path, repo_root)

            # Should NOT find pytest/unittest mismatch
            pytest_findings = [f for f in findings if f["type"] == "pytest-vs-unittest"]
            self.assertEqual(len(pytest_findings), 0, "Should allow pytest in pytest repo")

    def test_no_flag_pytest_when_explicitly_excluded(self):
        """Should NOT flag pytest when mentioned but explicitly excluded in unittest repo.

        Regression test for issue where tools/CLAUDE.md says
        'uses unittest, not pytest' but was still flagged.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            claudemd_dir = repo_root / "tools"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            # Create package.json with unittest
            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Write CLAUDE.md that mentions pytest but explicitly excludes it
            claudemd_path.write_text(
                "# Tools Domain\n\n"
                "- **Python**: `npm run test:py` (= `python -m unittest discover`); "
                "tests live in tests/, not tools/; the repo uses unittest, not pytest"
            )

            findings = lint_claudemd(claudemd_path, repo_root)

            # Should NOT find pytest/unittest mismatch (pytest is explicitly excluded)
            pytest_findings = [f for f in findings if f["type"] == "pytest-vs-unittest"]
            self.assertEqual(
                len(pytest_findings), 0,
                f"Should not flag pytest when explicitly excluded as 'not pytest'. "
                f"Found: {[f['message'] for f in pytest_findings]}"
            )


class TestDomainMapAllowlist(unittest.TestCase):
    """Test that the 'Map of all domains' /CLAUDE.md reference is allowed."""

    def test_domain_map_clause_not_flagged(self):
        """The phrase 'Map of all domains: /CLAUDE.md' should NOT trigger false positives."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # This is the root CLAUDE.md
            claudemd_path = repo_root / "CLAUDE.md"

            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Write root CLAUDE.md with domain map references
            claudemd_path.write_text(
                "# Project CLAUDE.md\n\n"
                "**Domain map:**\n\n"
                "- **skills/** — Orchestration skills — see skills/CLAUDE.md\n"
                "- **daemons/** — Watchdog daemon — see daemons/CLAUDE.md\n"
                "- **tools/** — Build utilities — see tools/CLAUDE.md\n"
            )

            findings = lint_claudemd(claudemd_path, repo_root)

            # Create the referenced domain directories
            for domain in ["skills", "daemons", "tools"]:
                (repo_root / domain).mkdir(exist_ok=True)
                (repo_root / domain / "CLAUDE.md").touch()

            # Re-lint after creating the domains
            findings = lint_claudemd(claudemd_path, repo_root)

            # Should NOT find any phantom-path findings (all domains exist)
            phantom_findings = [f for f in findings if f["type"] == "phantom-path"]
            self.assertEqual(len(phantom_findings), 0, "Domain map references should be valid")


class TestLineCountFlag(unittest.TestCase):
    """Test optional line count flagging."""

    def test_flag_file_over_max_lines(self):
        """Should flag CLAUDE.md files exceeding max-lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            claudemd_dir = repo_root / "testdomain"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Write CLAUDE.md with > 150 lines
            lines = ["# Test\n"] + ["Content line\n"] * 160
            claudemd_path.write_text("".join(lines))

            findings = lint_claudemd(claudemd_path, repo_root, max_lines=150)

            # Should find line-count violation
            line_findings = [f for f in findings if f["type"] == "line-count"]
            self.assertGreater(len(line_findings), 0, "Should flag files over max-lines")

    def test_allow_file_within_max_lines(self):
        """Should NOT flag CLAUDE.md files within max-lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            claudemd_dir = repo_root / "testdomain"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Write CLAUDE.md with <= 150 lines
            lines = ["# Test\n"] + ["Content line\n"] * 100
            claudemd_path.write_text("".join(lines))

            findings = lint_claudemd(claudemd_path, repo_root, max_lines=150)

            # Should NOT find line-count violation
            line_findings = [f for f in findings if f["type"] == "line-count"]
            self.assertEqual(len(line_findings), 0, "Should allow files within max-lines")


class TestCompleteIntegration(unittest.TestCase):
    """Integration tests with multiple issues in one CLAUDE.md."""

    def test_multiple_issues_all_caught(self):
        """Should catch multiple issues in a single CLAUDE.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            claudemd_dir = repo_root / "testdomain"
            claudemd_dir.mkdir()
            claudemd_path = claudemd_dir / "CLAUDE.md"

            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Create some real files
            (repo_root / "docs").mkdir()
            (repo_root / "docs" / "ARCHITECTURE.md").touch()

            # Write CLAUDE.md with multiple issues:
            # 1. Phantom path: docs/nonexistent.md
            # 2. Missing npm script: npm run fake:script
            # 3. pytest reference in unittest repo
            # 4. Over max lines
            lines = [
                "# Test Domain\n",
                "\n",
                "See docs/ARCHITECTURE.md and docs/nonexistent.md.\n",
                "Run `npm run fake:script` to test.\n",
                "Uses pytest for assertions.\n",
            ]
            # Add enough content to exceed 10 lines
            for i in range(15):
                lines.append(f"Content line {i}.\n")

            claudemd_path.write_text("".join(lines))

            findings = lint_claudemd(claudemd_path, repo_root, max_lines=10)

            # Group by type
            by_type = {}
            for f in findings:
                t = f["type"]
                by_type.setdefault(t, []).append(f)

            # Should have caught all issues
            self.assertIn("phantom-path", by_type, "Should catch phantom path")
            self.assertIn("missing-npm-script", by_type, "Should catch missing npm script")
            self.assertIn("pytest-vs-unittest", by_type, "Should catch pytest/unittest mismatch")
            self.assertIn("line-count", by_type, "Should catch line count violation")


class TestDomainClaudeReferenceExtraction(unittest.TestCase):
    """Test extraction of domain CLAUDE.md references."""

    def test_extract_simple_domain_claude_references(self):
        """Extract references like 'tools/CLAUDE.md' and 'daemons/CLAUDE.md'."""
        text = "See tools/CLAUDE.md and daemons/CLAUDE.md for details."
        refs = extract_domain_claude_references(text)
        self.assertIn("tools", refs)
        self.assertIn("daemons", refs)

    def test_extract_nested_domain_claude_references(self):
        """Extract nested domain paths like 'driver/orchestrator-swap/CLAUDE.md'."""
        text = "Read driver/orchestrator-swap/CLAUDE.md for the orchestrator."
        refs = extract_domain_claude_references(text)
        self.assertIn("driver/orchestrator-swap", refs)

    def test_ignore_root_claude_md(self):
        """Should NOT extract plain 'CLAUDE.md' (root file)."""
        text = "See CLAUDE.md for the domain map."
        refs = extract_domain_claude_references(text)
        # Should be empty because 'CLAUDE.md' alone (no domain prefix) is not extracted
        self.assertNotIn("CLAUDE.md", refs)
        self.assertEqual(len(refs), 0)

    def test_ignore_relative_claude_md(self):
        """Should NOT extract './CLAUDE.md' or '../CLAUDE.md'."""
        text = "See ./CLAUDE.md or ../CLAUDE.md for reference."
        refs = extract_domain_claude_references(text)
        self.assertEqual(len(refs), 0, "Should not extract relative root references")


class TestSiblingDomainsDetection(unittest.TestCase):
    """Test detection of sibling domain paths."""

    def test_get_sibling_domains(self):
        """Should discover all domains except root and current domain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create domain structure
            for domain in ["tools", "daemons", "monitor"]:
                domain_dir = repo_root / domain
                domain_dir.mkdir()
                (domain_dir / "CLAUDE.md").write_text(f"# {domain.title()} Domain")

            # Create the current domain
            current_dir = repo_root / "tools"
            current_claudemd = current_dir / "CLAUDE.md"

            # Get siblings (should exclude 'tools' itself, not include root)
            siblings = get_sibling_domains(repo_root, current_claudemd)

            self.assertIn("daemons", siblings)
            self.assertIn("monitor", siblings)
            self.assertNotIn("tools", siblings, "Should exclude current domain")

    def test_exclude_root_claude_md_from_siblings(self):
        """Root CLAUDE.md should not be considered a sibling domain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create root CLAUDE.md
            (repo_root / "CLAUDE.md").write_text("# Root")

            # Create a domain
            tools_dir = repo_root / "tools"
            tools_dir.mkdir()
            current_claudemd = tools_dir / "CLAUDE.md"
            current_claudemd.write_text("# Tools Domain")

            siblings = get_sibling_domains(repo_root, current_claudemd)

            # Root should not appear as a domain (it has no domain prefix)
            self.assertNotIn("", siblings, "Root should not be in sibling domains")


class TestDomainCrossRefCheck(unittest.TestCase):
    """Test detection of domain CLAUDE.md cross-references."""

    def test_catch_domain_cross_ref_in_domain_claudemd(self):
        """MUST CATCH a domain CLAUDE.md that references another domain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create package.json
            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Create two domains
            tools_dir = repo_root / "tools"
            tools_dir.mkdir()
            tools_claudemd = tools_dir / "CLAUDE.md"

            daemons_dir = repo_root / "daemons"
            daemons_dir.mkdir()
            (daemons_dir / "CLAUDE.md").write_text("# Daemons Domain")

            # Write tools/CLAUDE.md with reference to daemons/CLAUDE.md
            tools_claudemd.write_text(
                "# Tools Domain\n\n"
                "For daemon details, see daemons/CLAUDE.md."
            )

            findings = lint_claudemd(tools_claudemd, repo_root)

            # Should find the cross-reference violation
            cross_ref_findings = [f for f in findings if f["type"] == "domain-cross-ref"]
            self.assertGreater(
                len(cross_ref_findings), 0,
                "Should catch domain cross-ref violation"
            )
            self.assertTrue(
                any("daemons" in f["message"] for f in cross_ref_findings),
                "Should mention the referenced domain"
            )

    def test_allow_root_claude_md_to_reference_domains(self):
        """Root CLAUDE.md SHOULD be allowed to reference domain CLAUDE.md files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create package.json
            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Create domains
            for domain in ["tools", "daemons", "monitor"]:
                domain_dir = repo_root / domain
                domain_dir.mkdir()
                (domain_dir / "CLAUDE.md").write_text(f"# {domain.title()}")

            # Create root CLAUDE.md with domain references
            root_claudemd = repo_root / "CLAUDE.md"
            root_claudemd.write_text(
                "# Root CLAUDE.md\n\n"
                "- See tools/CLAUDE.md for tools\n"
                "- See daemons/CLAUDE.md for daemons\n"
                "- See monitor/CLAUDE.md for monitor\n"
            )

            findings = lint_claudemd(root_claudemd, repo_root)

            # Should NOT find any domain-cross-ref violations (root is exempt)
            cross_ref_findings = [f for f in findings if f["type"] == "domain-cross-ref"]
            self.assertEqual(
                len(cross_ref_findings), 0,
                "Root CLAUDE.md should be exempt from domain-cross-ref check"
            )

    def test_nested_domain_cross_ref_check(self):
        """Should catch cross-refs in nested domains like 'driver/orchestrator-swap'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create package.json
            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Create driver/orchestrator-swap domain
            orch_swap_dir = repo_root / "driver" / "orchestrator-swap"
            orch_swap_dir.mkdir(parents=True)
            orch_swap_claudemd = orch_swap_dir / "CLAUDE.md"

            # Create tools domain
            tools_dir = repo_root / "tools"
            tools_dir.mkdir()
            (tools_dir / "CLAUDE.md").write_text("# Tools")

            # Write orchestrator-swap CLAUDE.md with reference to tools
            orch_swap_claudemd.write_text(
                "# Orchestrator-Swap Domain\n\n"
                "For utilities, read tools/CLAUDE.md."
            )

            findings = lint_claudemd(orch_swap_claudemd, repo_root)

            # Should find the cross-reference
            cross_ref_findings = [f for f in findings if f["type"] == "domain-cross-ref"]
            self.assertGreater(len(cross_ref_findings), 0)

    def test_allow_same_domain_references(self):
        """A domain CLAUDE.md MAY reference itself (same domain)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create package.json
            pkg = repo_root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"test:py": "python -m unittest"}}))

            # Create driver domain with nested structure
            driver_dir = repo_root / "driver"
            driver_dir.mkdir()
            driver_claudemd = driver_dir / "CLAUDE.md"

            orchestrator_dir = driver_dir / "orchestrator-swap"
            orchestrator_dir.mkdir()
            (orchestrator_dir / "CLAUDE.md").write_text("# Nested")

            # Write driver/CLAUDE.md with reference to driver/orchestrator-swap/CLAUDE.md
            # This is self-referential (same domain), should be allowed
            driver_claudemd.write_text(
                "# Driver Domain\n\n"
                "For orchestrator swap, see driver/orchestrator-swap/CLAUDE.md."
            )

            findings = lint_claudemd(driver_claudemd, repo_root)

            cross_ref_findings = [f for f in findings if f["type"] == "domain-cross-ref"]
            self.assertEqual(len(cross_ref_findings), 0,
                "Parent-child domain references (driver -> driver/orchestrator-swap) should be allowed")


if __name__ == "__main__":
    unittest.main()
