"""Tests for tools.spec_contract_validator -- Guardrail G4 spec-contract validation.

Covers: forbidden-flag detection, credential-hunting detection, git-stash prohibition (G8),
clean dispatches passing, `# contract-ok` suppression, isolation-marker detection,
env-var allowlisting, role-routing, and JSON/CLI output shape. Fixtures are written to
tempfile.TemporaryDirectory() -- no cwd or global git-config pollution.
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

from tools.spec_contract_validator import (  # noqa: E402
    find_dispatch_calls,
    run,
    scan_file,
    validate_call,
)


def _dispatch_source(prompt: str, subagent_type: str = None, call_name: str = "agent") -> str:
    """Build a minimal Python source string containing one dispatch call."""
    kw = f'subagent_type="{subagent_type}", ' if subagent_type else ""
    return (
        f'result = {call_name}(\n'
        f'    description="do the thing",\n'
        f'    {kw}'
        f'    prompt="""{prompt}""",\n'
        f')\n'
    )


class SpecContractValidatorTest(unittest.TestCase):
    """Tests for the spec-contract validator tool."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        for d in ("driver", "monitor", "tools"):
            (self.repo_root / d).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    # -- 1. forbidden flags -------------------------------------------------

    def test_forbidden_flag_detected(self):
        source = _dispatch_source(
            "Do the release. Merge with git push --force to be safe."
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertIn("forbidden_flag", rules)
        flag_details = [f["detail"] for f in findings if f["rule"] == "forbidden_flag"]
        self.assertIn("--force", flag_details)

    def test_each_forbidden_flag_is_individually_detected(self):
        for flag in ("--admin", "--auto", "--force", "--no-verify"):
            source = _dispatch_source(f"Merge the PR using {flag} for speed.")
            calls = find_dispatch_calls(source)
            findings = validate_call(calls[0])
            flag_details = [f["detail"] for f in findings if f["rule"] == "forbidden_flag"]
            self.assertIn(flag, flag_details, f"expected {flag} to be flagged")

    # -- 2. credential hunting -----------------------------------------------

    def test_credential_hunting_pattern_detected(self):
        source = _dispatch_source(
            "Before starting, search for api key in the environment and use whatever you find."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertIn("credential_hunting", rules)

    def test_named_env_var_alone_is_not_credential_hunting(self):
        """Naming an explicit allowlisted env var is fine on its own."""
        source = _dispatch_source(
            "Use the BENCH_API_KEY env var if set. If missing, skip and report -- never search."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertNotIn("credential_hunting", rules)
        self.assertNotIn("env_var_not_allowlisted", rules)

    def test_unnamed_env_var_token_flagged(self):
        """An env-var-shaped token not in the allowlist is flagged even without hunting language."""
        source = _dispatch_source("Read the SHADOW_ADMIN_TOKEN value and use it directly.")
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules_and_details = {(f["rule"], f["detail"]) for f in findings}
        self.assertIn(("env_var_not_allowlisted", "SHADOW_ADMIN_TOKEN"), rules_and_details)

    # -- 3. git stash prohibition (G8) -----------------------------------------------

    def test_git_stash_detected(self):
        source = _dispatch_source("Save your changes with git stash before switching branches.")
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertIn("git_stash_forbidden", rules)
        detail = [f["detail"] for f in findings if f["rule"] == "git_stash_forbidden"]
        self.assertIn("git stash", detail)

    def test_git_stash_case_insensitive(self):
        """git stash detection should be case-insensitive."""
        source = _dispatch_source("Save work using GIT STASH temporarily.")
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertIn("git_stash_forbidden", rules)

    def test_git_stash_suppressed_with_contract_ok(self):
        """git stash suppressed with # contract-ok comment."""
        prompt_body = "Save changes with git stash before proceeding."
        source = (
            f'result = agent(\n'
            f'    description="do the thing",\n'
            f'    prompt="""{prompt_body}""",\n'
            f')  # contract-ok\n'
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["suppressed"])
        # End-to-end via scan_file: no findings surface.
        target = self.repo_root / "tools" / "suppressed_stash_dispatch.py"
        target.write_text(source, encoding="utf-8")
        results = scan_file(target)
        self.assertEqual(results, [])

    def test_prompt_without_git_stash_passes(self):
        """A prompt without git stash should pass cleanly."""
        source = _dispatch_source(
            "[ISOLATION: sibling worktree] Implement the feature, git commit and push to your branch."
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertNotIn("git_stash_forbidden", rules)

    # -- 4. clean dispatch passes --------------------------------------------

    def test_clean_dispatch_produces_no_findings(self):
        source = _dispatch_source(
            "[ISOLATION: sibling worktree] Implement the feature, Write(the file), "
            "then git commit and push to your feature branch. No env vars needed here."
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        findings = validate_call(calls[0])
        self.assertEqual(findings, [])

    def test_non_dispatch_calls_are_ignored(self):
        """Calls with no agent-like name and no subagent_type/agentType keyword are skipped."""
        source = 'result = some_other_function(prompt="--force this is fine, not a dispatch")\n'
        calls = find_dispatch_calls(source)
        self.assertEqual(calls, [])

    # -- 4. contract-ok suppression -------------------------------------------

    def test_contract_ok_suppresses_finding(self):
        prompt_body = "Merge using git push --force for this documented exception."
        source = (
            f'result = agent(\n'
            f'    description="do the thing",\n'
            f'    prompt="""{prompt_body}""",\n'
            f')  # contract-ok\n'
        )
        calls = find_dispatch_calls(source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["suppressed"])

        # And end-to-end via scan_file: no findings surface.
        target = self.repo_root / "tools" / "suppressed_dispatch.py"
        target.write_text(source, encoding="utf-8")
        results = scan_file(target)
        self.assertEqual(results, [])

    def test_contract_ok_does_not_suppress_other_calls(self):
        """A suppression comment on one call must not blanket-suppress a sibling call."""
        source = (
            'result_a = agent(prompt="""git push --force here""")  # contract-ok\n'
            'result_b = agent(prompt="""git push --force here too""")\n'
        )
        target = self.repo_root / "tools" / "mixed_dispatch.py"
        target.write_text(source, encoding="utf-8")
        results = scan_file(target)
        self.assertTrue(results, "expected findings from the non-suppressed call")
        # None of the findings may come from the suppressed line-1 call.
        self.assertTrue(all(r["line"] == 2 for r in results))
        self.assertTrue(all("mixed_dispatch.py" in r["file"] for r in results))

    # -- 5. isolation marker ---------------------------------------------------

    def test_missing_isolation_marker_flagged_when_prompt_writes_files(self):
        source = _dispatch_source("Implement the feature: Write(the new module) then commit.")
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertIn("missing_isolation_marker", rules)

    def test_isolation_marker_present_suppresses_that_finding(self):
        source = _dispatch_source(
            "[ISOLATION: sibling worktree] Implement the feature: Write(the new module) then commit."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertNotIn("missing_isolation_marker", rules)

    def test_read_only_prompt_does_not_require_isolation_marker(self):
        """A prompt that never writes files doesn't need the isolation marker."""
        source = _dispatch_source("Read the tracker and report the open item count. No writes.")
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertNotIn("missing_isolation_marker", rules)

    def test_isolation_marker_without_space_after_colon(self):
        """Isolation marker should work even without space after colon (finding #3)."""
        # Test "[ISOLATION:sibling worktree]" (no space after colon)
        source = _dispatch_source(
            "[ISOLATION:sibling worktree] Implement the feature: Write(the new module) then commit."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        # Should NOT flag missing_isolation_marker because the marker is present
        self.assertNotIn("missing_isolation_marker", rules,
                        "Isolation marker should be recognized even without space after colon")

    def test_isolation_marker_with_space_after_colon(self):
        """Isolation marker with space after colon should work as before."""
        # Test "[ISOLATION: sibling worktree]" (with space after colon)
        source = _dispatch_source(
            "[ISOLATION: sibling worktree] Implement the feature: Write(the new module) then commit."
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        # Should NOT flag missing_isolation_marker because the marker is present
        self.assertNotIn("missing_isolation_marker", rules,
                        "Isolation marker with space should work as before")

    # -- 6. role routing (advisory) ---------------------------------------------

    def test_unknown_specialist_type_flagged(self):
        source = _dispatch_source(
            "Read-only audit task, no writes.", subagent_type="mystery-specialist-9000"
        )
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules_and_details = {(f["rule"], f["detail"]) for f in findings}
        self.assertIn(("unknown_specialist_type", "mystery-specialist-9000"), rules_and_details)

    def test_general_purpose_specialist_type_not_flagged(self):
        source = _dispatch_source("Read-only audit task, no writes.", subagent_type="general-purpose")
        calls = find_dispatch_calls(source)
        findings = validate_call(calls[0])
        rules = [f["rule"] for f in findings]
        self.assertNotIn("unknown_specialist_type", rules)

    # -- 7. JSON output shape + end-to-end run() --------------------------------

    def test_run_reports_findings_across_scanned_files(self):
        bad = self.repo_root / "driver" / "bad_dispatch.py"
        bad.write_text(
            _dispatch_source("Merge with git push --force, no isolation marker needed here."),
            encoding="utf-8",
        )
        good = self.repo_root / "driver" / "good_dispatch.py"
        good.write_text(
            _dispatch_source("[ISOLATION: sibling worktree] Read-only task, nothing to write."),
            encoding="utf-8",
        )

        result = run(self.repo_root, paths=["driver"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["scanned_files"], 2)
        files_with_findings = {f["file"] for f in result["findings"]}
        self.assertTrue(any("bad_dispatch.py" in f for f in files_with_findings))
        self.assertFalse(any("good_dispatch.py" in f for f in files_with_findings))

    def test_run_clean_repo_is_ok(self):
        clean = self.repo_root / "tools" / "clean_dispatch.py"
        clean.write_text(
            _dispatch_source("[ISOLATION: sibling worktree] Read-only, no writes, no flags."),
            encoding="utf-8",
        )
        result = run(self.repo_root, paths=["tools"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["findings"], [])

    def test_missing_scan_dir_is_skipped_not_an_error(self):
        """monitor/ (or any absent dir) is silently skipped, never an error."""
        result = run(self.repo_root, paths=["monitor", "does-not-exist"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["scanned_files"], 0)

    def test_cli_json_output_and_exit_code(self):
        bad = self.repo_root / "tools" / "bad_dispatch.py"
        bad.write_text(
            _dispatch_source("Use --no-verify to skip the gate, then git commit and push."),
            encoding="utf-8",
        )
        validator = str(ROOT / "tools" / "spec_contract_validator.py")
        proc = subprocess.run(
            [sys.executable, validator, "--json", "--root", str(self.repo_root), "--paths", "tools"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(any(f["rule"] == "forbidden_flag" for f in payload["findings"]))

    def test_cli_ascii_output_exit_zero_on_clean(self):
        clean = self.repo_root / "tools" / "clean_dispatch.py"
        clean.write_text(
            _dispatch_source("[ISOLATION: sibling worktree] Read-only task."),
            encoding="utf-8",
        )
        validator = str(ROOT / "tools" / "spec_contract_validator.py")
        proc = subprocess.run(
            [sys.executable, validator, "--check", "--root", str(self.repo_root), "--paths", "tools"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("PASS", proc.stdout)
        # ASCII-only: no non-ASCII bytes anywhere in stdout.
        proc.stdout.encode("ascii")


if __name__ == "__main__":
    unittest.main()
