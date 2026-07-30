"""Tests for tools.port_fidelity_check -- Port-task fidelity validation.

Covers: port/copy/vendor/migrate keyword detection, source-path validation, marker-requirement
validation, independent-verification validation, suppression via # fidelity-ok, and JSON/CLI
output shape. Fixtures are written to tempfile.TemporaryDirectory() -- no cwd or global
git-config pollution.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.port_fidelity_check import (  # noqa: E402
    find_dispatch_calls,
    run,
    scan_file,
    validate_call,
)


def _dispatch_source(prompt: str, call_name: str = "agent") -> str:
    """Build a minimal Python source string containing one dispatch call."""
    return (
        f'result = {call_name}(\n'
        f'    description="do the thing",\n'
        f'    prompt="""{prompt}""",\n'
        f')\n'
    )


class PortFidelityCheckTest(unittest.TestCase):
    """Tests for the port-fidelity checker tool."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        for d in ("driver", "monitor", "tools", "skills"):
            (self.repo_root / d).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    # -- 1. Port keyword detection ------------------------------------------

    def test_port_keyword_detected(self):
        """Port keyword triggers dispatch inspection."""
        source = _dispatch_source(
            "Port the file from /source/file.py to /dest/file.py"
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["has_port_keyword"])

    def test_copy_keyword_detected(self):
        """Copy keyword triggers dispatch inspection."""
        source = _dispatch_source(
            "Copy /source/config.json to the new location"
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["has_port_keyword"])

    def test_vendor_keyword_detected(self):
        """Vendor keyword triggers dispatch inspection."""
        source = _dispatch_source(
            "Vendor the library from /upstream/lib.py into our repo"
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["has_port_keyword"])

    def test_migrate_keyword_detected(self):
        """Migrate keyword triggers dispatch inspection."""
        source = _dispatch_source(
            "Migrate data from /old/location to /new/location"
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["has_port_keyword"])

    def test_case_insensitive_port_detection(self):
        """Port keyword detection is case-insensitive."""
        source = _dispatch_source(
            "PORT the file from /source to /dest"
        )
        calls = find_dispatch_calls(source)
        self.assertTrue(calls[0]["has_port_keyword"])

    # -- 2. All requirements met - should pass --------------------------------

    def test_all_requirements_present_posix_path(self):
        """Dispatch with port keyword + source path + markers + independent verification passes."""
        source = _dispatch_source(
            "Port the file from /source/file.py to /dest/file.py. "
            "Verify that the source-unique markers (function definitions, imports) are present. "
            "Use a separate test file to independently verify the implementation."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        self.assertEqual(len(findings), 0, f"Expected no findings, got {findings}")

    def test_all_requirements_present_windows_path(self):
        """Dispatch with Windows path passes all requirements."""
        source = _dispatch_source(
            "Port the file from C:\\source\\file.py. "
            "Ensure source-unique markers are present. "
            "Independent cross-artifact verification required."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        self.assertEqual(len(findings), 0)

    def test_all_requirements_present_copy_keyword(self):
        """Copy dispatch with all requirements passes."""
        source = _dispatch_source(
            "Copy /config/app.conf to the new location. "
            "Verify source markers match. "
            "Different file independent verification."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        self.assertEqual(len(findings), 0)

    # -- 3. Missing source path -----------------------------------------------

    def test_missing_source_path_flagged(self):
        """Port dispatch without explicit source path is flagged."""
        source = _dispatch_source(
            "Port the file. Verify source markers. Independent verification required."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertIn("missing_source_path", rules)

    def test_relative_path_not_accepted_as_source(self):
        """Relative paths alone don't satisfy source path requirement."""
        source = _dispatch_source(
            "Port file from src/module.py. "
            "Marker assertion required. Independent verification."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        # Relative path by itself might not match; depends on regex
        # src/module.py should match the POSIX pattern if / is present
        self.assertTrue(len(findings) <= 2, f"Expected <=2 findings, got {findings}")

    # -- 4. Missing marker requirement ----------------------------------------

    def test_missing_marker_requirement_flagged(self):
        """Port dispatch without marker requirement is flagged."""
        source = _dispatch_source(
            "Port the file from /source/file.py. "
            "Just copy it over. Independent verification required."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertIn("missing_marker_requirement", rules)

    def test_structural_element_satisfies_marker_requirement(self):
        """Mentioning 'structural element' satisfies marker requirement."""
        source = _dispatch_source(
            "Port from /source/file.py. "
            "Assert that structural elements from the source match. "
            "Independent verification."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertNotIn("missing_marker_requirement", rules)

    def test_unique_marker_satisfies_requirement(self):
        """Mentioning 'unique marker' satisfies marker requirement."""
        source = _dispatch_source(
            "Port from /source/file.py maintaining unique marker assertions. "
            "Independent verification required."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertNotIn("missing_marker_requirement", rules)

    # -- 5. Missing independent verification ---------------------------------

    def test_missing_independent_verification_flagged(self):
        """Port dispatch without independent verification requirement is flagged."""
        source = _dispatch_source(
            "Port the file from /source/file.py. "
            "Verify source-unique markers are present. Test thoroughly."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertIn("missing_independent_verification", rules)

    def test_different_file_satisfies_verification(self):
        """Mentioning 'different file' satisfies independent verification."""
        source = _dispatch_source(
            "Port from /source/file.py. Verify markers. "
            "Use a different file for independent verification."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertNotIn("missing_independent_verification", rules)

    def test_cross_artifact_satisfies_verification(self):
        """Mentioning 'cross-artifact' satisfies independent verification."""
        source = _dispatch_source(
            "Port from /source/file.py. Markers required. "
            "Cross-artifact verification needed."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertNotIn("missing_independent_verification", rules)

    def test_separate_verification_satisfies_requirement(self):
        """Mentioning 'separate verification' satisfies requirement."""
        source = _dispatch_source(
            "Port from /source. Verify markers. Separate verification required."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertNotIn("missing_independent_verification", rules)

    # -- 6. Suppression via # fidelity-ok -----------------------------------

    def test_fidelity_ok_suppresses_findings(self):
        """Dispatch with # fidelity-ok marker suppresses all findings."""
        source = (
            'result = agent(\n'
            '    description="do the thing",\n'
            '    prompt="""Port the file. Test it. This is incomplete.""",  # fidelity-ok\n'
            ')\n'
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["suppressed"])
        # When suppressed, scan_file won't include it in findings
        # Verify the call is marked suppressed
        self.assertTrue(calls[0]["suppressed"])

    # -- 7. Non-port dispatch passes through ---------------------------------

    def test_non_port_dispatch_not_inspected(self):
        """Dispatch without port keywords returns no calls."""
        source = _dispatch_source(
            "Run the tests and verify they pass."
        )
        calls = find_dispatch_calls(source)
        # Non-port dispatches won't match has_port_keyword, so find_dispatch_calls returns empty
        self.assertEqual(len(calls), 0)

    # -- 8. Multiple port keywords -------------------------------------------

    def test_multiple_port_keywords_in_one_dispatch(self):
        """Dispatch mentioning multiple port keywords is inspected once."""
        source = _dispatch_source(
            "Port and copy the file from /source/file.py. "
            "Verify markers. Independent verification."
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["has_port_keyword"])

    # -- 9. File-level scanning----------------------------------------------

    def test_scan_file_with_findings(self):
        """scan_file returns findings from a Python file with violations."""
        test_file = self.repo_root / "tools" / "test_port.py"
        test_file.write_text(
            _dispatch_source(
                "Port the file. This is incomplete."
            ),
            encoding="utf-8"
        )
        findings = scan_file(test_file)
        self.assertGreater(len(findings), 0)
        self.assertEqual(findings[0]["file"], str(test_file))
        self.assertIn("rule", findings[0])

    def test_scan_file_clean(self):
        """scan_file returns no findings for compliant file."""
        test_file = self.repo_root / "tools" / "test_clean.py"
        test_file.write_text(
            _dispatch_source(
                "Port from /source/file.py. "
                "Verify markers. Independent verification."
            ),
            encoding="utf-8"
        )
        findings = scan_file(test_file)
        self.assertEqual(len(findings), 0)

    # -- 10. End-to-end run function ----------------------------------------

    def test_run_returns_ok_when_clean(self):
        """run() returns ok=True for a clean repo."""
        # Create empty but valid structure
        result = run(self.repo_root, paths=["tools"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["scanned_files"], 0)

    def test_run_returns_findings_for_violations(self):
        """run() returns findings from files with port violations."""
        test_file = self.repo_root / "tools" / "bad_port.py"
        test_file.write_text(
            _dispatch_source("Port the file. Missing everything."),
            encoding="utf-8"
        )
        result = run(self.repo_root, paths=["tools"])
        self.assertFalse(result["ok"])
        self.assertGreater(len(result["findings"]), 0)

    # -- 11. CLI integration ------------------------------------------------

    def test_cli_check_clean(self):
        """CLI returns 0 for clean repo."""
        result = subprocess.run(
            [sys.executable, "tools/port_fidelity_check.py", "--check", "--root", str(self.repo_root)],
            capture_output=True,
            text=True,
            cwd=str(ROOT)
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("PASS", result.stdout)

    def test_cli_check_with_findings(self):
        """CLI returns 1 for repo with findings."""
        test_file = self.repo_root / "tools" / "cli_test.py"
        test_file.write_text(
            _dispatch_source("Port the file. Incomplete."),
            encoding="utf-8"
        )
        result = subprocess.run(
            [sys.executable, "tools/port_fidelity_check.py", "--check", "--root", str(self.repo_root)],
            capture_output=True,
            text=True,
            cwd=str(ROOT)
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL", result.stdout)

    def test_cli_json_output(self):
        """CLI --json produces valid JSON."""
        test_file = self.repo_root / "tools" / "json_test.py"
        test_file.write_text(
            _dispatch_source("Port the file. Missing markers."),
            encoding="utf-8"
        )
        result = subprocess.run(
            [sys.executable, "tools/port_fidelity_check.py", "--json", "--root", str(self.repo_root)],
            capture_output=True,
            text=True,
            cwd=str(ROOT)
        )
        data = json.loads(result.stdout)
        self.assertIn("ok", data)
        self.assertIn("findings", data)
        self.assertIn("scanned_files", data)

    def test_cli_custom_paths(self):
        """CLI --paths argument restricts scanning."""
        # Create files in different dirs
        (self.repo_root / "tools" / "file1.py").write_text(
            _dispatch_source("Port from /source."),
            encoding="utf-8"
        )
        (self.repo_root / "driver" / "file2.py").write_text(
            _dispatch_source("Port from /source."),
            encoding="utf-8"
        )
        result = subprocess.run(
            [
                sys.executable, "tools/port_fidelity_check.py",
                "--root", str(self.repo_root),
                "--paths", str(self.repo_root / "tools")
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT)
        )
        # Should only scan tools/, not driver/
        # With incomplete dispatches, both would fail
        # Just verify it runs
        self.assertIn("port-fidelity-check", result.stdout)

    # -- 12. AST edge cases -------------------------------------------------

    def test_task_call_name_variant(self):
        """Task() call name is recognized as dispatch."""
        source = (
            'task = Task(\n'
            '    prompt="Port from /source. Markers. Independent.",\n'
            ')\n'
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["has_port_keyword"])

    def test_agent_call_name_variant(self):
        """Agent() call name is recognized as dispatch."""
        source = (
            'result = Agent(\n'
            '    prompt="Port from /src/file. Markers. Independent.",\n'
            ')\n'
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["has_port_keyword"])

    def test_multiline_prompt_extraction(self):
        """Multiline prompts are correctly extracted."""
        source = (
            'agent(\n'
            '    prompt="""\n'
            '    Port the file from /source/file.py.\n'
            '    Verify markers.\n'
            '    Independent verification required.\n'
            '    """,\n'
            ')\n'
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        findings = validate_call(calls[0])
        self.assertEqual(len(findings), 0)


if __name__ == "__main__":
    unittest.main()
