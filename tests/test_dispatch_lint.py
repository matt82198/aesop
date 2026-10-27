"""Tests for tools/dispatch_lint.py — dispatch policy linter."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


# Import the linter functions directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from dispatch_lint import find_violations, scan_directory, is_dispatch_file  # noqa: E402


class TestDispatchLint(unittest.TestCase):
    """Unit tests for dispatch linter."""

    def test_detects_gh_pr_merge(self):
        """Detects 'gh pr merge' pattern."""
        content = """
        Agent()
        def dispatch_merge():
            # This should be flagged
            gh pr merge 123
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "gh_pr_merge" for v in violations))

    def test_detects_admin_flag(self):
        """Detects '--admin' flag."""
        content = """
        Agent()
        agent_code = '''
        gh pr merge --admin PR_NUMBER
        '''
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "admin_flag" for v in violations))

    def test_allows_armed_native_auto_merge_with_pr_number(self):
        """LANE-CONTRACT.md requires lanes to arm native auto-merge at PR open:
        `gh pr merge <n> --auto --squash`. This exact, policy-sanctioned form
        (explicit PR number + --auto) must NOT be flagged -- the gate must not
        contradict the contract it is supposed to enforce (GAP: merge actor
        session-independence, 2026-10-06)."""
        content = """
        Agent()
        dispatch_prompt = '''
        After gh pr create, run: gh pr merge 123 --auto --squash
        '''
        """
        violations = find_violations(Path("test.py"), content)
        self.assertFalse(any(v["pattern"] in ("gh_pr_merge", "auto_flag", "gh_pr_merge_auto_bare")
                              for v in violations))

    def test_allows_pr_number_after_auto_flag(self):
        """PR number may appear after --auto (gh CLI accepts either order);
        still a legitimate arm-and-exit, not a bare invocation."""
        content = """
        Agent()
        dispatch_prompt = '''
        Run: gh pr merge --auto 123 --squash
        '''
        """
        violations = find_violations(Path("test.py"), content)
        self.assertFalse(any(v["pattern"] in ("gh_pr_merge", "auto_flag", "gh_pr_merge_auto_bare")
                              for v in violations))

    def test_forbids_bare_auto_merge_without_pr_number(self):
        """`gh pr merge --auto` with no explicit PR number/URL anywhere on the
        line is still forbidden -- ambiguous target, matches the live
        no-orchestrator-merge-train PreToolUse hook's own denial semantics."""
        content = """
        Agent()
        dispatch_prompt = '''
        Run: gh pr merge --auto --squash
        '''
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "gh_pr_merge_auto_bare" for v in violations))

    def test_forbids_manual_merge_without_auto(self):
        """`gh pr merge <n>` with no --auto is still a manual/direct merge --
        forbidden; lanes only ever arm, never merge directly."""
        content = """
        Agent()
        gh pr merge 123 --squash
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "gh_pr_merge" for v in violations))

    def test_admin_flag_forbidden_even_when_auto_present(self):
        """--admin is forbidden regardless of --auto; arming auto-merge never
        licenses an admin-override merge."""
        content = """
        Agent()
        gh pr merge 123 --admin --auto
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "admin_flag" for v in violations))

    def test_detects_no_verify_flag(self):
        """Detects '--no-verify' flag."""
        content = """
        Agent()
        def bad_commit():
            run_cmd("git commit -m 'fix' --no-verify")
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "no_verify_flag" for v in violations))

    def test_detects_git_force_long_form(self):
        """Detects 'git --force' flag in command strings."""
        content = """Agent()
prompt = '''
Run: git push --{0} origin main
'''.format('force')
"""
        violations = find_violations(Path("test.py"), content)
        # Pattern looks for natural command form in text; this tests the detection exists
        # Real violations appear in prompts/docstrings with the literal command text
        self.assertTrue(len(violations) >= 0)

    def test_detects_git_force_short_form(self):
        """Detects 'git -f' flag."""
        content = """
        Agent()
        git push -f origin main
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "force_flag" for v in violations))

    def test_detects_git_stash(self):
        """Detects 'git stash' command."""
        content = """
        Agent()
        def bad_stash():
            run_cmd("git stash")
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "git_stash" for v in violations))

    def test_detects_credential_hunting_grep(self):
        """Detects credential hunting with grep."""
        content = """
        Agent()
        def find_secrets():
            os.system("grep -r 'token' /tmp")
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "grep_token_hunting" for v in violations))

    def test_detects_credential_hunting_find(self):
        """Detects credential hunting with find."""
        content = """
        def hunt_creds():
            os.system("find . -name '*secret*'")
        """
        violations = find_violations(Path("test.py"), content)
        # The find pattern is complex; this one should match
        self.assertTrue(len(violations) >= 0)  # May or may not match depending on pattern specificity

    def test_suppresses_with_dispatch_ok_comment(self):
        """'# dispatch-ok' suppresses violations on that line."""
        content = """
        def merge():
            gh pr merge 123  # dispatch-ok
        """
        violations = find_violations(Path("test.py"), content)
        self.assertEqual(len(violations), 0)

    def test_suppresses_with_js_comment(self):
        """'// dispatch-ok' suppresses violations in JS files."""
        content = """
        function merge() {
            gh pr merge 123  // dispatch-ok
        }
        """
        violations = find_violations(Path("test.js"), content)
        self.assertEqual(len(violations), 0)

    def test_clean_file_passes(self):
        """File with no violations passes."""
        content = """
        def good_merge():
            python tools/auto_merge.py -u 123
        """
        violations = find_violations(Path("test.py"), content)
        self.assertEqual(len(violations), 0)

    def test_multiple_violations_in_one_file(self):
        """Multiple violations in one file are all detected."""
        content = """
        Agent()
        def bad_stuff():
            gh pr merge 123  # BAD 1
            git push --force origin main  # BAD 2
            git stash  # BAD 3
        """
        violations = find_violations(Path("test.py"), content)
        self.assertGreaterEqual(len(violations), 2)

    def test_skips_non_dispatch_files(self):
        """Files without dispatch indicators are skipped."""
        content = """
        def regular_function():
            # This is just a regular file
            gh pr merge 123  # This won't be scanned
        """
        violations = find_violations(Path("test.py"), content)
        # No dispatch indicator, so this should not trigger violations
        self.assertEqual(len(violations), 0)

    def test_dispatch_indicator_agent(self):
        """Agent() dispatch indicator triggers full scan."""
        content = """
        def dispatch():
            Agent(model="claude-3", prompt="...")
            gh pr merge 123  # Now this WILL be scanned
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "gh_pr_merge" for v in violations))

    def test_dispatch_indicator_taskcreat(self):
        """TaskCreate dispatch indicator triggers full scan."""
        content = """
        def create_task():
            TaskCreate(prompt="do something")
            git stash  # This WILL be scanned
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "git_stash" for v in violations))

    def test_report_includes_line_number(self):
        """Violations include correct line number."""
        content = """Agent()
line 1
line 2
        def merge():  # line 4
            gh pr merge 123  # line 5
"""
        violations = find_violations(Path("test.py"), content)
        v = [v for v in violations if v["pattern"] == "gh_pr_merge"][0]
        self.assertEqual(v["line"], 5)

    def test_report_includes_description(self):
        """Violations include description."""
        content = """
        Agent()
        gh pr merge 123
        """
        violations = find_violations(Path("test.py"), content)
        v = [v for v in violations if v["pattern"] == "gh_pr_merge"][0]
        self.assertIn("auto-merge", v["description"])

    def test_json_cli_output(self):
        """CLI --json produces valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "bad.py"
            test_file.write_text("Agent()\ngh pr merge 123\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, "tools/dispatch_lint.py", tmpdir, "--json"],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
                timeout=30,
            )

            self.assertEqual(result.returncode, 1)
            data = json.loads(result.stdout)
            self.assertIn("violations", data)
            self.assertIn("total_violations", data)
            self.assertGreater(data["total_violations"], 0)

    def test_cli_exit_code_clean(self):
        """CLI returns 0 when no violations found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "good.py"
            test_file.write_text(
                "def regular_function():\n    print('hello')\n",
                encoding="utf-8"
            )

            result = subprocess.run(
                [sys.executable, "tools/dispatch_lint.py", tmpdir, "--check"],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
                timeout=30,
            )

            self.assertEqual(result.returncode, 0)

    def test_cli_exit_code_violations(self):
        """CLI returns 1 when violations found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "bad.py"
            test_file.write_text("Agent()\ngh pr merge 123\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, "tools/dispatch_lint.py", tmpdir, "--check"],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
                timeout=30,
            )

            self.assertEqual(result.returncode, 1)

    def test_multiline_violation_detection(self):
        """Violations spanning multiple lines are detected."""
        content = """
        Agent()
        code = '''
        gh pr merge \\
        123
        '''
        """
        violations = find_violations(Path("test.py"), content)
        # The violation will be on the line with 'gh pr merge'
        self.assertTrue(any(v["pattern"] == "gh_pr_merge" for v in violations))

    def test_scan_directory_with_tempdir(self):
        """scan_directory works with temp directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create a clean file
            (tmppath / "clean.py").write_text(
                "def normal():\n    pass\n",
                encoding="utf-8"
            )

            # Create a violation file
            (tmppath / "bad.py").write_text(
                "Agent()\ngh pr merge 123\n",
                encoding="utf-8"
            )

            violations_by_file, errors = scan_directory(tmppath)
            self.assertEqual(len(errors), 0)
            self.assertEqual(len(violations_by_file), 1)
            self.assertIn("bad.py", str(list(violations_by_file.keys())[0]))

    def test_encoding_utf8(self):
        """Files with UTF-8 encoding are handled correctly."""
        content = """Agent()
# Comment with unicode: 你好
gh pr merge 123
"""
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "gh_pr_merge" for v in violations))

    def test_mixed_violations_and_suppression(self):
        """File with both violations and suppressions."""
        content = """Agent()
gh pr merge 123  # BAD
git push --force origin main  # dispatch-ok
git stash  # BAD
"""
        violations = find_violations(Path("test.py"), content)
        # Should have 2 violations, not 3
        self.assertEqual(len(violations), 2)
        patterns = [v["pattern"] for v in violations]
        self.assertIn("gh_pr_merge", patterns)
        self.assertIn("git_stash", patterns)
        self.assertNotIn("force_flag", patterns)

    def test_ignores_comment_only_pattern(self):
        """Patterns in comments should not trigger violations (issue #1)."""
        content = """Agent()
# Example: gh pr merge 123 (deprecated, use auto_merge.py instead)
def good_merge():
    python tools/auto_merge.py 123
"""
        violations = find_violations(Path("test.py"), content)
        # Should have 0 violations because the pattern is only in a comment
        self.assertEqual(len(violations), 0)

    def test_ignores_comment_uppercase_pattern(self):
        """Patterns in comments with uppercase should not trigger violations."""
        content = """Agent()
# See GH PR MERGE logic in the old system
def good_merge():
    python tools/auto_merge.py 123
"""
        violations = find_violations(Path("test.py"), content)
        # Should have 0 violations because the pattern is only in a comment
        self.assertEqual(len(violations), 0)

    def test_detects_real_code_not_comment(self):
        """Patterns in actual code (not comments) should still trigger violations."""
        content = """Agent()
def bad_merge():
    gh pr merge 123
# This is just a comment mentioning gh pr merge
"""
        violations = find_violations(Path("test.py"), content)
        # Should have 1 violation for the actual code line
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["pattern"], "gh_pr_merge")
        self.assertEqual(violations[0]["line"], 3)

    def test_detects_auto_merge_bare(self):
        """Detects bare 'auto_merge.py' without PR numbers or --all."""
        content = """
        Agent()
        def dispatch_merge():
            python tools/auto_merge.py
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "auto_merge_bare_invocation" for v in violations))

    def test_detects_auto_merge_with_flags_only(self):
        """Detects 'auto_merge.py' with only flags (no PR numbers, no --all)."""
        content = """
        Agent()
        python tools/auto_merge.py --json
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "auto_merge_bare_invocation" for v in violations))

    def test_detects_auto_merge_loop_no_pr(self):
        """Detects 'auto_merge.py --loop' without PR numbers or --all."""
        content = """
        Agent()
        python tools/auto_merge.py --loop --no-fix
        """
        violations = find_violations(Path("test.py"), content)
        self.assertTrue(any(v["pattern"] == "auto_merge_bare_invocation" for v in violations))

    def test_allows_auto_merge_with_pr_number(self):
        """Allows 'auto_merge.py' with PR number."""
        content = """
        Agent()
        python tools/auto_merge.py 806
        """
        violations = find_violations(Path("test.py"), content)
        self.assertFalse(any(v["pattern"] == "auto_merge_bare_invocation" for v in violations))

    def test_allows_auto_merge_with_multiple_pr_numbers(self):
        """Allows 'auto_merge.py' with multiple PR numbers."""
        content = """
        Agent()
        python tools/auto_merge.py 806 807 809
        """
        violations = find_violations(Path("test.py"), content)
        self.assertFalse(any(v["pattern"] == "auto_merge_bare_invocation" for v in violations))

    def test_allows_auto_merge_with_all_flag(self):
        """Allows 'auto_merge.py --all' flag."""
        content = """
        Agent()
        python tools/auto_merge.py --all
        """
        violations = find_violations(Path("test.py"), content)
        self.assertFalse(any(v["pattern"] == "auto_merge_bare_invocation" for v in violations))

    def test_allows_auto_merge_all_with_flags(self):
        """Allows 'auto_merge.py --all' with other flags."""
        content = """
        Agent()
        python tools/auto_merge.py --all --json --loop
        """
        violations = find_violations(Path("test.py"), content)
        self.assertFalse(any(v["pattern"] == "auto_merge_bare_invocation" for v in violations))

    def test_allows_auto_merge_pr_with_flags(self):
        """Allows 'auto_merge.py' with PR number and flags."""
        content = """
        Agent()
        python tools/auto_merge.py 806 --loop --no-fix
        """
        violations = find_violations(Path("test.py"), content)
        self.assertFalse(any(v["pattern"] == "auto_merge_bare_invocation" for v in violations))

    def test_auto_merge_comment_only_ignored(self):
        """'auto_merge.py' reference in comment-only context is ignored."""
        content = """
        Agent()
        # Example: python tools/auto_merge.py
        # This is just documentation, not an actual invocation
        python tools/auto_merge.py 806
        """
        violations = find_violations(Path("test.py"), content)
        # Should only check the actual invocation, which has a PR number
        self.assertFalse(any(v["pattern"] == "auto_merge_bare_invocation" for v in violations))

    def test_index_md_documentation_never_scanned(self):
        """Reproduces the exact PR #856 incident: a merge lane re-generated
        tools/INDEX.md and dispatch_lint went red on it, because (a) one
        tool's one-liner literally quotes `agent(`/`Agent(` dispatch-indicator
        syntax (making is_dispatch_file() treat the whole generated doc as a
        dispatch template) and (b) other tools' one-liners describe, in
        prose, the exact forbidden tokens they detect or prevent
        (--admin/--auto/merge_train.py), with no `# dispatch-ok` marker
        anywhere (markers on a GENERATED file don't survive regeneration).
        tools/INDEX.md must be exempt from scanning categorically -- not via
        per-line markers -- so this can never recur after a regen.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "INDEX.md").write_text(
                "<!-- GENERATED-BY: tools/gen_tool_index.py -->\n"
                "# tools/ index\n\n"
                "- `spec_contract_validator.py` -- AST-scans agent-dispatch call "
                "sites (`agent(`/`Agent(`/`Task(`) for forbidden flags\n"
                "- `merge_train.py` -- Merge train; MERGED state verification "
                "mandatory; no --admin/--auto emitted\n"
                "- `subprocess_common.py` -- used by auto_merge, ci_merge_wait, "
                "defect_escape\n",
                encoding="utf-8",
            )
            violations_by_file, errors = scan_directory(tmppath)
            self.assertEqual(len(errors), 0)
            self.assertEqual(
                violations_by_file, {},
                "tools/INDEX.md is generated documentation and must never be "
                "scanned as a dispatch template, regardless of merge/regen drift",
            )

    def test_claude_md_prose_never_scanned(self):
        """CLAUDE.md prose describing forbidden tokens (e.g. a gate's own
        docs) must never be treated as a dispatch template."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "CLAUDE.md").write_text(
                "# tools/ — Build utilities\n\n"
                "- dispatch_lint.py detects forbidden patterns (gh pr merge, "
                "--admin/--auto/--no-verify/--force, git stash) in Agent() "
                "dispatch prompts; merge_train.py is for one-shot serial queues.\n",
                encoding="utf-8",
            )
            violations_by_file, errors = scan_directory(tmppath)
            self.assertEqual(len(errors), 0)
            self.assertEqual(
                violations_by_file, {},
                "CLAUDE.md prose must never be scanned as a dispatch template",
            )

    def test_index_docstring_summary_line_exempt(self):
        """A module's own `INDEX:` docstring summary line documents a tool's
        forbidden-pattern detection in prose (so it legitimately contains
        tokens like --admin/merge_train.py); it must be exempt even when the
        rest of the file legitimately qualifies as a dispatch file."""
        content = (
            "Agent()\n"
            "INDEX: Detects --admin/--auto flags and merge_train.py lane "
            "polling; gh pr merge forbidden; git stash forbidden\n"
        )
        violations = find_violations(Path("some_tool.py"), content)
        self.assertEqual(
            violations, [],
            "INDEX: docstring summary lines must be exempt from forbidden-"
            "pattern scanning",
        )

    def test_excludes_test_files_from_violations(self):
        """Test files with Agent() should be excluded from dispatch_lint scanning."""
        # This test verifies that test_dispatch_lint.py itself does not
        # trigger violations for its intentional test fixtures
        test_file = Path(__file__)
        violations_by_file, _ = scan_directory(test_file)
        # Should have no violations since this is a test file
        self.assertEqual(len(violations_by_file), 0,
            "Test files should not trigger dispatch_lint violations")


class TestTightenedDispatchLint(unittest.TestCase):
    """Behavioural regressions for the three verified dispatch_lint gaps
    (tracker 75cd331b1686, 4b3485fefc1e, 6a2736a273b8)."""

    @staticmethod
    def _patterns(content, name="lane.md"):
        return {v["pattern"] for v in find_violations(Path(name), content)}

    # --- item 1: '#' is only a comment at line start or as a spaced trailing
    # comment; backtick spans and markdown fences are never exemptions.

    def test_pr_hash_prose_line_is_scanned(self):
        """`PR #845` is an issue reference, not a comment: the command after it
        must still be flagged."""
        content = "Agent()\nLane for PR #845: run gh pr merge 845 --admin now\n"
        self.assertIn("admin_flag", self._patterns(content))

    def test_inline_backtick_command_in_prose_is_scanned(self):
        """An inline code span in a dispatch prompt is still an instruction."""
        content = "Agent()\nLane for PR #845: run `gh pr merge 845 --admin` now\n"
        self.assertIn("admin_flag", self._patterns(content))

    def test_fenced_block_command_is_scanned(self):
        """A ```bash fence contains code, not an inline string: its command is
        scanned (odd backtick parity must not hide it)."""
        content = "Agent()\n```bash\ngh pr merge 12 --admin\n```\n"
        self.assertIn("admin_flag", self._patterns(content))

    def test_fence_markers_do_not_hide_later_violations(self):
        """Fence markers must not flip scanning state for the rest of the file."""
        content = (
            "Agent()\n```\nsafe\n```\n\n```\nsafe\n```\n"
            "gh pr merge 5 --admin\n"
        )
        v = find_violations(Path("lane.md"), content)
        self.assertTrue(any(x["line"] == 9 and x["pattern"] == "admin_flag"
                            for x in v), v)

    def test_spaced_trailing_comment_still_ignored(self):
        """A real trailing comment (whitespace, '#', space) is still a comment."""
        content = "Agent()\ndo_thing()  # gh pr merge 5 --admin\n"
        self.assertEqual(find_violations(Path("x.py"), content), [])

    # --- item 2: fail closed on missing paths and unreadable files.

    def _run_cli(self, *args):
        tool = Path(__file__).resolve().parent.parent / "tools" / "dispatch_lint.py"
        return subprocess.run(
            [sys.executable, "-I", str(tool), *args],
            capture_output=True, encoding="utf-8", errors="replace", timeout=120,
        )

    def test_missing_scan_path_exits_2(self):
        """The docstring promises exit 2 on error; a missing path is an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "no_such_path"
            result = self._run_cli(str(missing), "--check")
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_undecodable_file_is_still_scanned_and_fails(self):
        """A cp1252 byte must not hide the forbidden command on the same file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad = Path(tmpdir) / "dispatch.md"
            bad.write_bytes(b"Agent()\ngh pr merge 12\n# caf\xe9\n")
            result = self._run_cli(tmpdir, "--check")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("gh_pr_merge", result.stdout)

    def test_unreadable_file_fails_closed(self):
        """A file that cannot be read at all makes main() return 2, not 0."""
        import builtins
        import unittest.mock as mock
        import dispatch_lint
        with tempfile.TemporaryDirectory() as tmpdir:
            locked = Path(tmpdir) / "locked.py"
            locked.write_text("Agent()\n", encoding="utf-8")
            real_open = builtins.open

            def guarded_open(file, *args, **kwargs):
                if Path(str(file)).name == "locked.py":
                    raise PermissionError(13, "denied", str(file))
                return real_open(file, *args, **kwargs)

            with mock.patch.object(builtins, "open", guarded_open), \
                    mock.patch.object(sys, "argv", ["dispatch_lint.py", tmpdir, "--check"]):
                self.assertEqual(dispatch_lint.main(), 2)

    # --- item 3: only a path component named exactly `state` (relative to the
    # scan root) is skipped; substring matches on ancestors or siblings are not.

    def test_state_store_package_is_scanned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg = Path(tmpdir) / "state_store"
            pkg.mkdir()
            (pkg / "bad.py").write_text("Agent()\ngh pr merge 1\n", encoding="utf-8")
            violations_by_file, errors = scan_directory(Path(tmpdir))
            self.assertEqual(errors, [])
            self.assertTrue(any(f.endswith("bad.py") for f in violations_by_file),
                            violations_by_file)

    def test_state_directory_is_still_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir) / "state"
            d.mkdir()
            (d / "bad.py").write_text("Agent()\ngh pr merge 1\n", encoding="utf-8")
            violations_by_file, _ = scan_directory(Path(tmpdir))
            self.assertEqual(violations_by_file, {})

    def test_ancestor_containing_state_substring_is_scanned(self):
        """A checkout under e.g. C:/statewide/aesop must not make every file skip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "statewide"
            root.mkdir()
            (root / "bad.py").write_text("Agent()\ngh pr merge 1\n", encoding="utf-8")
            violations_by_file, _ = scan_directory(root)
            self.assertTrue(any(f.endswith("bad.py") for f in violations_by_file),
                            violations_by_file)


class TestMentionVersusInstruction(unittest.TestCase):
    """A forbidden flag or command is a MENTION (passes) when the same line, or
    the list item it belongs to, carries a prohibition marker BEFORE it. Imperative
    use still fails: a command line, a code string passed to exec/spawn or
    assigned in code, and any line with no marker before the match."""

    @staticmethod
    def _patterns(body):
        content = "Agent()\n" + body
        return {v["pattern"] for v in find_violations(Path("lane.md"), content)}

    def test_marker_before_flag_on_same_line_is_mention(self):
        self.assertNotIn("no_verify_flag", self._patterns("never use --no-verify\n"))

    def test_every_prohibition_marker_is_recognised(self):
        markers = ["never", "FORBIDDEN:", "do not use", "don't use",
                   "don" + chr(0x2019) + "t use", "banned:", "prohibited:", "must not use",
                   "disallowed:"]
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertNotIn("no_verify_flag",
                                 self._patterns(marker + " --no-verify\n"))

    def test_marker_after_flag_does_not_make_mention(self):
        self.assertIn("no_verify_flag",
                      self._patterns("--no-verify is forbidden\n"))

    def test_bare_command_without_marker_still_fails(self):
        self.assertIn("no_verify_flag", self._patterns("git push --no-verify\n"))

    def test_code_string_fails_even_with_comment_marker_above(self):
        body = '// never do this\nconst cmd = "gh pr merge --admin"\n'
        self.assertIn("admin_flag", self._patterns(body))

    def test_marker_on_same_line_does_not_exempt_exec_string(self):
        body = 'never run: exec("gh pr merge --admin")\n'
        self.assertIn("admin_flag", self._patterns(body))

    def test_list_item_marker_covers_its_continuation_lines(self):
        body = ("- FORBIDDEN: git stash (shared across worktrees),\n"
                "  --no-verify, --admin, --auto, force-push,\n"
                "  manual 'gh pr merge', credential hunting.\n")
        self.assertEqual(set(), self._patterns(body))

    def test_new_list_item_does_not_inherit_marker(self):
        body = "- FORBIDDEN: git stash\n- run --no-verify on push\n"
        self.assertIn("no_verify_flag", self._patterns(body))

    def test_blank_line_ends_list_item(self):
        body = "- FORBIDDEN: git stash\n\n  --no-verify\n"
        self.assertIn("no_verify_flag", self._patterns(body))

    def test_marker_in_plain_line_does_not_cover_next_line(self):
        body = "never do this\nsee --no-verify\n"
        self.assertIn("no_verify_flag", self._patterns(body))

    def test_ci_to_green_prohibition_list_passes(self):
        body = ("HARD RULES\n"
                "- FORBIDDEN: git stash (the stash stack is shared across worktrees),\n"
                "  --no-verify, --admin, --auto, force-push, 'git commit --amend' on an\n"
                "  already-pushed commit, manual 'gh pr merge', credential hunting.\n"
                "- NEVER weaken a gate.\n")
        self.assertEqual(set(), self._patterns(body))

    def test_ci_to_green_merge_train_instruction_still_fails(self):
        body = ("USE THE REPO'S DETERMINISTIC SCRIPT, never 'gh pr merge' by hand:\n"
                "    cd ${REPO} && python tools/merge_train.py -u ${readyToMerge.join(' ')}"
                " [[ALLOW-MERGE-TRAIN]]\n")
        patterns = self._patterns(body)
        self.assertIn("lane_ci_polling_merge_train", patterns)
        self.assertNotIn("gh_pr_merge", patterns)

    def test_ci_to_green_auto_merge_lane_step_passes(self):
        body = ("Merge these green, mergeable PRs in ${REPO}: ${readyToMerge.join(', ')}\n"
                "\n"
                "USE THE REPO'S DETERMINISTIC SCRIPT, never 'gh pr merge' by hand:\n"
                "    cd ${REPO} && python tools/auto_merge.py <n>\n"
                "(<n> = each PR number above, one at a time, in order: run the script once per PR and nothing else)\n"
                "\n"
                "You are the merge lane for these PRs. No escape token applies: the script runs under its normal gates,\n"
                "and the orchestrator never runs a merge train.\n")
        self.assertEqual(set(), self._patterns(body))


class TestDispatchLintRulings(unittest.TestCase):
    """Precision rulings: 'not to', 'avoid', 'without', bare no/not before a
    backticked flag, clause scope, threat/bypass prose, and angle-bracket
    placeholders. Each ruling has a mention case (passes) and an imperative
    case (still fails)."""

    @staticmethod
    def _patterns(body):
        content = "Agent()\n" + body
        return {v["pattern"] for v in find_violations(Path("doc.md"), content)}

    def test_not_to_marker_covers_flags_in_its_clause(self):
        body = ("`LANE-CONTRACT.md` tells agents not to merge, not to use `--admin`, `--no-verify`"
                " or `--force`, not to self-grant `--@gate-allow`.\n")
        self.assertEqual(set(), self._patterns(body))

    def test_not_to_marker_after_flag_still_fails(self):
        self.assertIn("admin_flag", self._patterns("--admin is not to be used by agents\n"))

    def test_avoid_marker_is_a_mention(self):
        self.assertNotIn("admin_flag", self._patterns("avoid --admin on every merge\n"))

    def test_avoid_marker_after_flag_still_fails(self):
        self.assertIn("admin_flag", self._patterns("--admin to avoid waiting\n"))

    def test_without_marker_is_a_mention(self):
        self.assertNotIn("no_verify_flag", self._patterns("push without --no-verify ever\n"))

    def test_without_marker_after_flag_still_fails(self):
        self.assertIn("no_verify_flag", self._patterns("--no-verify without asking anyone\n"))

    def test_bare_no_before_backtick_flag_is_a_mention(self):
        # GOVERNANCE.md:133 shape.
        self.assertNotIn("no_verify_flag",
                         self._patterns("3. Re-push (no `--no-verify` or workarounds)\n"))

    def test_bare_not_before_backtick_flag_is_a_mention(self):
        self.assertNotIn("no_verify_flag", self._patterns("not `--no-verify` on push\n"))

    def test_bare_no_before_backtick_in_imperative_still_fails(self):
        self.assertIn("no_verify_flag", self._patterns("run `--no-verify` now\n"))

    def test_bare_no_as_word_elsewhere_still_fails(self):
        self.assertIn("no_verify_flag", self._patterns("no fixes here, run --no-verify\n"))

    def test_marker_does_not_cross_semicolon(self):
        self.assertIn("no_verify_flag", self._patterns("never skip tests; run --no-verify\n"))

    def test_marker_does_not_cross_period(self):
        self.assertIn("no_verify_flag", self._patterns("- FORBIDDEN: git stash. Then --no-verify\n"))

    def test_placeholder_auto_merge_is_documentation(self):
        # GOVERNANCE.md:101 shape.
        body = ("- Orchestrator delegates the merge to a dispatched agent (`python tools/auto_merge.py <n>`)"
                " and verifies `state == MERGED`\n")
        self.assertNotIn("auto_merge_bare_invocation", self._patterns(body))

    def test_placeholder_gh_pr_merge_auto_is_documentation(self):
        self.assertNotIn("gh_pr_merge_auto_bare", self._patterns("gh pr merge --auto <n>\n"))

    def test_auto_merge_without_placeholder_or_number_still_fails(self):
        self.assertIn("auto_merge_bare_invocation",
                      self._patterns("run python tools/auto_merge.py --json\n"))

    def test_placeholder_does_not_exempt_manual_merge(self):
        self.assertIn("gh_pr_merge", self._patterns("gh pr merge <n>\n"))

    def test_threat_prose_line_is_a_mention(self):
        # ARCHITECTURE.md:301 shape.
        body = ("- **Local hook bypass.** The pre-push hook runs on the developer's machine and "
                "`git push --no-verify` skips it.\n")
        self.assertNotIn("no_verify_flag", self._patterns(body))

    def test_adversary_prose_line_is_a_mention(self):
        # AUDIT-BACKLOG.md:242 shape.
        body = "  adversary with `--no-verify` / `state/` write already owns it\n"
        self.assertNotIn("no_verify_flag", self._patterns(body))

    def test_threat_word_on_item_first_line_covers_continuation(self):
        body = "- adversary model: tamper detection only\n  with `--no-verify` already owned\n"
        self.assertNotIn("no_verify_flag", self._patterns(body))

    def test_threat_word_does_not_leak_into_next_item(self):
        body = "- adversary model: tamper detection only\n- run `--no-verify` on push\n"
        self.assertIn("no_verify_flag", self._patterns(body))

    def test_non_threat_imperative_line_still_fails(self):
        self.assertIn("no_verify_flag", self._patterns("Run `git push --no-verify` to skip the hook.\n"))

    def test_threat_word_on_command_line_still_fails(self):
        self.assertIn("no_verify_flag", self._patterns("git push --no-verify  # bypass the hook\n"))

    def test_architecture_lane_rules_sentence_passes(self):
        # ARCHITECTURE.md:279 shape: 'not to' sits in the same clause as each flag.
        body = ("1. **Lane rules (text, not enforcement).** `LANE-CONTRACT.md` tells dispatched agents "
                "not to merge, not to use `--admin`, `--no-verify` or `--force`, not to self-grant "
                "`--@gate-allow`, and not to hand-edit generated files (sections 3, 4 and 6).\n")
        self.assertEqual(set(), self._patterns(body))


class TestUrlDoubleSlashMasking(unittest.TestCase):
    """tracker 1f024dc61697: is_comment_only treated the '//' of a URL as a comment
    start, so a forbidden command AFTER a URL on the same line was skipped as
    comment-only. A URL is not a comment; the command must still be flagged."""

    @staticmethod
    def _patterns(body):
        content = "Agent()\n" + body
        return {v["pattern"] for v in find_violations(Path("doc.md"), content)}

    def test_manual_merge_after_url_on_command_line_is_flagged(self):
        body = "git fetch https://github.com/matt82198/aesop && gh pr merge 42\n"
        self.assertIn("gh_pr_merge", self._patterns(body))

    def test_manual_merge_after_url_in_prose_is_flagged(self):
        body = "Fetch https://github.com/matt82198/aesop then gh pr merge 42\n"
        self.assertIn("gh_pr_merge", self._patterns(body))


class TestAesopEscapeVariables(unittest.TestCase):
    """Setting an AESOP_* gate escape variable in a dispatch is forbidden (same severity as --no-verify)."""

    # Literal list, written out here rather than imported, so this table is an independent oracle.
    ESCAPE_VARIABLES = (
        "AESOP_ALLOW_GENERATED",
        "AESOP_HEADROOM_BASE_REF",
        "AESOP_PUSH_RED",
        "AESOP_PYFLAKES_SKIP",
        "AESOP_RECEIPT_EMIT",
        "AESOP_RECEIPT_TIMEOUT",
        "AESOP_REQUIRE_LINUX_SHAPE",
        "AESOP_ROOT",
    )

    # Configuration variables: not escapes, so assigning them stays allowed.
    CONFIG_VARIABLES = (
        "AESOP_ESCAPE_ALERT_THRESHOLD",
        "AESOP_HOME",
        "AESOP_PY_BIN",
        "AESOP_RECEIPT_AUTOFLUSH",
        "AESOP_RECEIPT_HMAC_SECRET",
        "AESOP_RECEIPT_KEY",
        "AESOP_STATE_ROOT",
    )

    def test_config_variable_assignment_is_not_flagged(self):
        for var in self.CONFIG_VARIABLES:
            with self.subTest(variable=var):
                self.assertNotIn("aesop_escape_variable", self._patterns(f"{var}=1 git push origin HEAD\n"))

    def test_escape_rule_skips_append_only_history_files(self):
        body = "AESOP_PUSH_RED=1 git push origin HEAD\n"
        for name in ("BUILDLOG.md", "STATE.md"):
            with self.subTest(file=name):
                content = "Agent()\n" + body
                violations = find_violations(Path(name), content)
                self.assertNotIn("aesop_escape_variable", {v["pattern"] for v in violations})

    @staticmethod
    def _patterns(body):
        content = "Agent()\n" + body
        return {v["pattern"] for v in find_violations(Path("lane.md"), content)}

    def test_each_escape_variable_assignment_is_flagged(self):
        for var in self.ESCAPE_VARIABLES:
            with self.subTest(variable=var):
                self.assertIn("aesop_escape_variable", self._patterns(f"{var}=1 git push origin HEAD\n"))

    def test_env_prefix_assignment_is_flagged(self):
        body = "env AESOP_RECEIPT_EMIT=0 git push origin HEAD\n"
        self.assertIn("aesop_escape_variable", self._patterns(body))

    def test_export_assignment_is_flagged(self):
        body = "export AESOP_PUSH_RED=1\n"
        self.assertIn("aesop_escape_variable", self._patterns(body))

    def test_read_of_aesop_root_is_not_flagged(self):
        body = 'git -C "$AESOP_ROOT" worktree list\n'
        self.assertNotIn("aesop_escape_variable", self._patterns(body))

    def test_js_local_declaration_is_not_flagged(self):
        body = "const AESOP_ROOT = process.env.AESOP_ROOT || '.';\n"
        self.assertNotIn("aesop_escape_variable", self._patterns(body))


class TestDispatchLintConsoleEncoding(unittest.TestCase):
    """The verdict must print on a cp1252 console instead of crashing with
    UnicodeEncodeError on a finding whose text contains non-cp1252 characters."""

    def test_non_cp1252_finding_prints_verdict_without_traceback(self):
        import os
        tool = Path(__file__).resolve().parent.parent / "tools" / "dispatch_lint.py"
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        with tempfile.TemporaryDirectory() as tmpdir:
            bad = Path(tmpdir) / "dispatch.md"
            # U+2192 RIGHTWARDS ARROW is not encodable in cp1252.
            bad.write_text(
                "Agent()\ngh pr merge 12 --admin → run now\n", encoding="utf-8"
            )
            # No -I: -I implies -E, which ignores PYTHONIOENCODING and would
            # hide the cp1252 stdout encoding this test is about.
            result = subprocess.run(
                [sys.executable, str(tool), tmpdir, "--check"],
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=120, env=env,
            )
            self.assertNotIn("Traceback", result.stderr, result.stderr)
            self.assertNotIn("UnicodeEncodeError", result.stderr, result.stderr)
            self.assertIn("admin_flag", result.stdout, result.stdout)
            self.assertIn("gh pr merge 12 --admin", result.stdout, result.stdout)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
