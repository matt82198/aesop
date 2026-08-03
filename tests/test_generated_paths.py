#!/usr/bin/env python3
"""Generated-path registry (tools/generated_paths.py) + pre-push gate tests.

Two halves:
  1. The registry itself -- declaration shape, is_generated() matching rules,
     and the --list/--check CLI exit-code contract including the
     AESOP_ALLOW_GENERATED=1 designed-writer escape hatch.
  2. The pre-push wiring -- hooks/pre-push-policy.sh check_generated_paths() is
     sourced and driven directly against fixture git repos, proving it REJECTS a
     push whose diff touches a registered path, PASSES an ordinary push, and
     honors the escape hatch.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "generated_paths.py"
HOOK = REPO_ROOT / "hooks" / "pre-push-policy.sh"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.generated_paths as generated_paths  # noqa: E402

ZERO_SHA = "0" * 40


def _find_bash():
    """Locate a usable bash (Git Bash on Windows, never the WSL launcher).

    Mirrors tests/test_encoding_cp1252.py::_find_bash: plain "bash" on PATH can
    resolve to the WSL launcher in System32, which fails on runners with no
    WSL distribution installed.
    """
    if os.name != "nt":
        return shutil.which("bash")
    git_exe = shutil.which("git")
    if git_exe:
        git_root = Path(git_exe).parent.parent
        for candidate in (git_root / "bin" / "bash.exe",
                          git_root / "usr" / "bin" / "bash.exe"):
            if candidate.exists():
                return str(candidate)
    path_bash = shutil.which("bash")
    if path_bash and "system32" not in path_bash.lower():
        return path_bash
    return None


BASH = _find_bash()


class TestRegistryDeclaration(unittest.TestCase):

    def test_registry_is_non_empty_and_well_formed(self):
        self.assertGreater(len(generated_paths.REGISTRY), 0)
        for entry in generated_paths.REGISTRY:
            self.assertEqual(set(entry), {"pattern", "generator", "why"})
            for value in entry.values():
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip())
            self.assertNotIn("\\", entry["pattern"], "patterns are POSIX-normalized")

    def test_seeded_paths_are_registered(self):
        for path in ("state/ledger/merge-telemetry.jsonl",
                     "tools/INDEX.md",
                     "tests/SUITE-COUNTS.md"):
            self.assertIsNotNone(generated_paths.is_generated(path), path)

    def test_every_entry_names_a_generator(self):
        for entry in generated_paths.REGISTRY:
            self.assertRegex(entry["generator"], r"\.(py|mjs|js|sh)")


class TestIsGenerated(unittest.TestCase):

    def test_authored_paths_are_not_registered(self):
        for path in ("tools/generated_paths.py", "README.md", "tests/CLAUDE.md",
                     "tools/CLAUDE.md", ".stateapi-baseline.json", "ui/serve.py"):
            self.assertIsNone(generated_paths.is_generated(path), path)

    def test_windows_separators_normalize(self):
        self.assertIsNotNone(generated_paths.is_generated("tools\\INDEX.md"))
        self.assertIsNotNone(generated_paths.is_generated(".\\tools\\INDEX.md"))

    def test_star_does_not_cross_a_directory_separator(self):
        # state/ledger/*.jsonl must NOT swallow a nested path.
        self.assertIsNotNone(generated_paths.is_generated("state/ledger/a.jsonl"))
        self.assertIsNone(generated_paths.is_generated("state/ledger/sub/a.jsonl"))
        self.assertIsNone(generated_paths.is_generated("state/a.jsonl"))

    def test_pattern_is_not_a_bare_suffix_match(self):
        # A same-named file elsewhere in the tree is authored, not generated.
        self.assertIsNone(generated_paths.is_generated("docs/INDEX.md"))
        self.assertIsNone(generated_paths.is_generated("bench/tools/INDEX.md"))

    def test_returned_entry_names_the_generator(self):
        entry = generated_paths.is_generated("tools/INDEX.md")
        self.assertIn("gen_tool_index.py", entry["generator"])


class CliTestCase(unittest.TestCase):

    def run_cli(self, args, stdin_text=None, env=None):
        run_env = dict(os.environ)
        run_env.pop(generated_paths.ALLOW_ENV, None)
        if env:
            run_env.update(env)
        return subprocess.run(  # subprocess-ok
            [sys.executable, str(TOOL)] + list(args),
            cwd=str(REPO_ROOT),
            input=stdin_text if stdin_text is not None else "",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=run_env,
        )


class TestCliContract(CliTestCase):

    def test_list_exits_0_and_prints_every_pattern(self):
        res = self.run_cli(["--list"])
        self.assertEqual(res.returncode, 0, res.stderr)
        for entry in generated_paths.REGISTRY:
            self.assertIn(entry["pattern"], res.stdout)

    def test_list_json_is_parseable(self):
        res = self.run_cli(["--list", "--json"])
        self.assertEqual(res.returncode, 0, res.stderr)
        payload = json.loads(res.stdout)
        self.assertEqual(len(payload["registry"]), len(generated_paths.REGISTRY))

    def test_default_invocation_lists(self):
        self.assertEqual(self.run_cli([]).returncode, 0)

    def test_check_clean_paths_exits_0(self):
        res = self.run_cli(["--check", "tools/generated_paths.py", "README.md"])
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_check_registered_path_exits_1(self):
        res = self.run_cli(["--check", "tools/INDEX.md"])
        self.assertEqual(res.returncode, 1)
        self.assertIn("tools/INDEX.md", res.stderr)

    def test_check_names_the_generator_in_the_message(self):
        res = self.run_cli(["--check", "tools/INDEX.md"])
        self.assertIn("gen_tool_index.py", res.stderr)

    def test_check_mixed_paths_exits_1(self):
        res = self.run_cli(["--check", "README.md", "tests/SUITE-COUNTS.md", "ui/serve.py"])
        self.assertEqual(res.returncode, 1)

    def test_check_no_paths_exits_0(self):
        self.assertEqual(self.run_cli(["--check"]).returncode, 0)

    def test_check_reads_paths_from_stdin(self):
        res = self.run_cli(["--check"], stdin_text="README.md\ntools/INDEX.md\n\n")
        self.assertEqual(res.returncode, 1)
        self.assertIn("tools/INDEX.md", res.stderr)

    def test_check_stdin_clean_exits_0(self):
        res = self.run_cli(["--check"], stdin_text="README.md\nui/serve.py\n")
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_escape_hatch_downgrades_to_a_report(self):
        res = self.run_cli(["--check", "tools/INDEX.md"],
                           env={generated_paths.ALLOW_ENV: "1"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("ALLOWED", res.stdout)
        self.assertIn("tools/INDEX.md", res.stdout)

    def test_escape_hatch_only_fires_on_exactly_1(self):
        for value in ("0", "true", "yes", ""):
            res = self.run_cli(["--check", "tools/INDEX.md"],
                               env={generated_paths.ALLOW_ENV: value})
            self.assertEqual(res.returncode, 1, "%s=%r must not open the gate"
                             % (generated_paths.ALLOW_ENV, value))

    def test_check_json_reports_hits(self):
        res = self.run_cli(["--check", "--json", "tools/INDEX.md"])
        self.assertEqual(res.returncode, 1)
        payload = json.loads(res.stdout)
        self.assertEqual(payload["count"], 1)
        self.assertFalse(payload["allowed"])

    def test_unknown_flag_exits_2(self):
        self.assertEqual(self.run_cli(["--check", "--disable"]).returncode, 2)

    def test_list_and_check_together_exit_2(self):
        self.assertEqual(self.run_cli(["--list", "--check"]).returncode, 2)

    def test_help_exits_0(self):
        res = self.run_cli(["--help"])
        self.assertEqual(res.returncode, 0)
        self.assertIn(generated_paths.ALLOW_ENV, res.stdout)


@unittest.skipIf(BASH is None, "usable bash unavailable")
class TestPrePushGate(unittest.TestCase):
    """Drive hooks/pre-push-policy.sh check_generated_paths() directly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.driver = self.tmp / "drive.sh"
        self.driver.write_text(
            '#!/usr/bin/env bash\n'
            '. "%s"\n'
            'check_generated_paths\n'
            'exit $?\n' % HOOK.as_posix(),
            encoding="utf-8", newline="\n")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, repo, *args):
        return subprocess.run(  # subprocess-ok
            ["git"] + list(args), cwd=str(repo),
            capture_output=True, text=True, encoding="utf-8", timeout=120)

    def make_repo(self, changed_path, name="repo"):
        """Fixture repo: one base commit, then one commit touching changed_path."""
        repo = self.tmp / name
        (repo / Path(changed_path).parent).mkdir(parents=True, exist_ok=True)
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "config", "user.email", "test@example.com")
        self.git(repo, "config", "user.name", "Test User")
        (repo / "seed.txt").write_text("seed\n", encoding="utf-8", newline="\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "base")
        base = self.git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / changed_path).write_text("content\n", encoding="utf-8", newline="\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "change")
        head = self.git(repo, "rev-parse", "HEAD").stdout.strip()
        return repo, base, head

    def run_gate(self, repo, stdin_text, env=None):
        run_env = dict(os.environ)
        run_env.pop(generated_paths.ALLOW_ENV, None)
        # bash `test -f` chokes on Windows backslash paths, so hand the hook a
        # POSIX AESOP_ROOT -- the same shape `git rev-parse --show-toplevel`
        # produces when AESOP_ROOT is unset in a real push.
        run_env["AESOP_ROOT"] = REPO_ROOT.as_posix()
        if env:
            run_env.update(env)
        # Feed stdin from a BINARY file, never subprocess `input=` in text mode:
        # on Windows that translates every \n to \r\n, and the stray CR lands
        # inside the parsed remote-sha so `git diff <range>` silently matches
        # nothing. Real git pre-push always pipes LF.
        stdin_path = self.tmp / "prepush-stdin.txt"
        stdin_path.write_bytes(stdin_text.encode("utf-8"))
        with open(stdin_path, "rb") as handle:
            return subprocess.run(  # subprocess-ok
                [BASH, str(self.driver)], cwd=str(repo),
                stdin=handle, capture_output=True, text=True,
                encoding="utf-8", timeout=120, env=run_env)

    @staticmethod
    def tuple_for(base, head, branch="feature/lane"):
        return "refs/heads/%s %s refs/heads/%s %s\n" % (branch, head, branch, base)

    def test_rejects_push_touching_a_registered_generated_path(self):
        repo, base, head = self.make_repo("tools/INDEX.md", "gen")
        res = self.run_gate(repo, self.tuple_for(base, head))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("tools/INDEX.md", res.stderr)

    def test_rejection_message_names_the_generator(self):
        repo, base, head = self.make_repo("tools/INDEX.md", "gen2")
        res = self.run_gate(repo, self.tuple_for(base, head))
        self.assertIn("gen_tool_index.py", res.stderr)

    def test_passes_on_an_ordinary_path(self):
        repo, base, head = self.make_repo("tools/some_tool.py", "clean")
        res = self.run_gate(repo, self.tuple_for(base, head))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_passes_on_a_similarly_named_authored_path(self):
        repo, base, head = self.make_repo("docs/INDEX.md", "docsindex")
        res = self.run_gate(repo, self.tuple_for(base, head))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_escape_hatch_allows_the_regeneration_push(self):
        repo, base, head = self.make_repo("tools/INDEX.md", "gen3")
        res = self.run_gate(repo, self.tuple_for(base, head),
                            env={generated_paths.ALLOW_ENV: "1"})
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_empty_stdin_passes(self):
        repo, base, head = self.make_repo("tools/INDEX.md", "gen4")
        self.assertEqual(self.run_gate(repo, "").returncode, 0)

    def test_delete_only_push_passes(self):
        repo, base, head = self.make_repo("tools/INDEX.md", "gen5")
        tuple_text = "refs/heads/x %s refs/heads/x %s\n" % (ZERO_SHA, base)
        self.assertEqual(self.run_gate(repo, tuple_text).returncode, 0)

    def test_missing_tool_fails_open(self):
        repo, base, head = self.make_repo("tools/INDEX.md", "gen6")
        empty_root = self.tmp / "no-aesop"
        (empty_root / "state").mkdir(parents=True)
        res = self.run_gate(repo, self.tuple_for(base, head),
                            env={"AESOP_ROOT": empty_root.as_posix()})
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_gate_is_wired_into_main(self):
        text = HOOK.read_text(encoding="utf-8")
        self.assertIn("check_generated_paths <<<", text)
        self.assertIn("generated_path_hand_edit", text)


if __name__ == "__main__":
    unittest.main()
