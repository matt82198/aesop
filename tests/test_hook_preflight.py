#!/usr/bin/env python3
"""Tests for tools/hook_preflight.py -- interpreter health checks for hooks/daemons.

History: PR #667 added this suite in bare pytest-function form (module-level `def
test_x(tmp_path):`). This repo's Python suite runs via `python -m unittest discover`
(see tests/CLAUDE.md), which silently SKIPS bare module-level functions -- they were
never collected, so hook_preflight.py has had zero real coverage since it was added.
One of the functions (`test_wrapper_stub_broken`) also referenced `tmp_path` without
declaring it as a unittest fixture, which would have raised NameError the instant it
actually executed. A later pass papered over both problems with a module-level
`raise unittest.SkipTest(...)`, which made the gap honest but did not close it.

This rewrite uses unittest.TestCase + tempfile.TemporaryDirectory (no pytest fixtures),
drives tools/hook_preflight.py as a real subprocess against hermetic fixture
directories (never the real repo's hooks/ or daemons/), and never touches cwd or
global git config (CLI invoked with subprocess `cwd=`, per tests/CLAUDE.md).

What hook_preflight.py checks (see tools/hook_preflight.py docstring):
  - Walks <repo_root>/hooks and <repo_root>/daemons for files with a shebang line.
  - For each shebang's interpreter name, tries to exec `<name> --version`.
  - Exit 0  -- every interpreter found was available.
  - Exit 1  -- at least one interpreter is missing/broken (fail CLOSED, not open).
  - Exit 2  -- no repo root found, or no files were available to check at all
               (fail CLOSED on "nothing to check" rather than silently passing).

RED-first proof (recorded, not re-run every CI pass -- see report): a hand-mutated
copy of tools/hook_preflight.py with `is_interpreter_available` stubbed to always
return `(True, None)` was run against the exact broken-interpreter fixture used by
test_missing_interpreter_fails_closed below; the stub exits 0 (wrongly green) where
the real script exits 1. test_missing_interpreter_fails_closed's assertion
(`exit_code == 1`) therefore fails against that stub and passes only against the
real fail-closed implementation -- it is not a vacuous assertion.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent / "tools"
PREFLIGHT_PY = TOOLS_DIR / "hook_preflight.py"


def _interpreter_name_and_path_env():
    """Return (basename, env) so a shebang naming this interpreter actually resolves.

    hook_preflight.py's own internal `subprocess.run([interpreter_name, "--version"])`
    looks the interpreter up on PATH by bare name, inheriting whatever environment the
    preflight process itself was launched with. To make a fixture reliably resolve to
    "this test's own interpreter" on every platform (Windows python.exe, Linux
    python3.x, venvs where the interpreter dir isn't already on PATH), we prepend the
    running interpreter's own directory to a copy of the environment and hand that
    env to the preflight subprocess call.
    """
    exe = Path(sys.executable)
    env = dict(os.environ)
    env["PATH"] = str(exe.parent) + os.pathsep + env.get("PATH", "")
    return exe.name, env


def run_preflight(cwd, env=None):
    """Run tools/hook_preflight.py with cwd=<cwd> and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(PREFLIGHT_PY)],
        cwd=str(cwd),
        env=env if env is not None else os.environ.copy(),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


class HookPreflightTestBase(unittest.TestCase):
    """Hermetic fixture base: throwaway temp dir with its own .git marker.

    Never touches the real repo's hooks/ or daemons/, never calls os.chdir (the
    preflight CLI is invoked via subprocess `cwd=` instead, per tests/CLAUDE.md).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="aesop-hook-preflight-test-")
        self.fixture_root = Path(self._tmp.name)
        # find_repo_root() only checks for a .git directory's existence; a bare
        # marker directory is sufficient and keeps the fixture hermetic (no real
        # git repo needed).
        (self.fixture_root / ".git").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def make_dir(self, name):
        d = self.fixture_root / name
        d.mkdir(parents=True, exist_ok=True)
        return d


class TestFailsClosedOnBrokenInterpreter(HookPreflightTestBase):
    """RED-first: a deliberately broken fixture must make the preflight fail."""

    def test_missing_interpreter_fails_closed(self):
        hooks_dir = self.make_dir("hooks")
        hook_file = hooks_dir / "mock-hook.sh"
        hook_file.write_text(
            "#!/usr/bin/env totally-bogus-interpreter-that-does-not-exist\necho hi\n",
            encoding="utf-8",
        )

        exit_code, _stdout, stderr = run_preflight(self.fixture_root)

        self.assertEqual(
            exit_code, 1,
            f"expected fail-closed exit 1 for a missing interpreter, got {exit_code}; "
            f"stderr={stderr!r}",
        )
        self.assertIn("BROKEN", stderr)
        self.assertIn("mock-hook.sh", stderr)
        self.assertIn("totally-bogus-interpreter-that-does-not-exist", stderr)

    def test_broken_interpreter_named_in_daemons_dir_too(self):
        # Same failure mode, but under daemons/ instead of hooks/, proving both
        # directories are actually scanned (not just hooks/).
        daemons_dir = self.make_dir("daemons")
        daemon_file = daemons_dir / "watchdog.sh"
        daemon_file.write_text(
            "#!/usr/bin/env another-bogus-interpreter-xyz\necho hi\n",
            encoding="utf-8",
        )

        exit_code, _stdout, stderr = run_preflight(self.fixture_root)

        self.assertEqual(exit_code, 1)
        self.assertIn("another-bogus-interpreter-xyz", stderr)


class TestPassesWhenInterpreterReallyAvailable(HookPreflightTestBase):
    def test_all_present_returns_zero(self):
        interp_name, env = _interpreter_name_and_path_env()
        hooks_dir = self.make_dir("hooks")
        hook_file = hooks_dir / "mock-hook.sh"
        hook_file.write_text(f"#!/usr/bin/env {interp_name}\necho hi\n", encoding="utf-8")

        exit_code, stdout, stderr = run_preflight(self.fixture_root, env=env)

        self.assertEqual(
            exit_code, 0,
            f"expected exit 0 when the named interpreter is really available, got "
            f"{exit_code}; stdout={stdout!r} stderr={stderr!r}",
        )
        self.assertIn("OK", stdout)

    def test_mixed_hooks_and_daemons_all_available_returns_zero(self):
        interp_name, env = _interpreter_name_and_path_env()
        hooks_dir = self.make_dir("hooks")
        daemons_dir = self.make_dir("daemons")
        (hooks_dir / "a.sh").write_text(f"#!/usr/bin/env {interp_name}\n", encoding="utf-8")
        (daemons_dir / "b.sh").write_text(f"#!/usr/bin/env {interp_name}\n", encoding="utf-8")

        exit_code, _stdout, _stderr = run_preflight(self.fixture_root, env=env)

        self.assertEqual(exit_code, 0)


class TestFileWithoutShebangIsSkippedNotFailed(HookPreflightTestBase):
    def test_non_script_file_is_skipped_and_does_not_fail_the_run(self):
        hooks_dir = self.make_dir("hooks")
        (hooks_dir / "README.md").write_text("just docs, no shebang\n", encoding="utf-8")

        exit_code, stdout, _stderr = run_preflight(self.fixture_root)

        # A file with no shebang isn't a script hook_preflight owns; it must be
        # skipped (counted as checked, but not failing), not treated as broken.
        self.assertEqual(exit_code, 0)
        self.assertIn("OK", stdout)


class TestFailsClosedOnNothingToCheck(HookPreflightTestBase):
    def test_zero_files_exits_nonzero(self):
        # Empty hooks/ and daemons/ dirs: nothing to check at all. This must fail
        # closed (non-zero), never silently report success.
        self.make_dir("hooks")
        self.make_dir("daemons")

        exit_code, _stdout, stderr = run_preflight(self.fixture_root)

        self.assertEqual(exit_code, 2)
        self.assertIn("No hook or daemon files found", stderr)

    def test_hidden_files_are_ignored_and_still_count_as_nothing_to_check(self):
        hooks_dir = self.make_dir("hooks")
        # Hidden files are filtered out by hook_preflight's own `not name.startswith(".")`
        # guard; a hooks/ dir containing only a hidden file must behave exactly like an
        # empty one (fail closed with "nothing to check"), not silently pass.
        (hooks_dir / ".hidden-broken-hook.sh").write_text(
            "#!/usr/bin/env totally-bogus-interpreter-that-does-not-exist\n",
            encoding="utf-8",
        )

        exit_code, _stdout, stderr = run_preflight(self.fixture_root)

        self.assertEqual(exit_code, 2)
        self.assertIn("No hook or daemon files found", stderr)

    def test_missing_repo_root_exits_two(self):
        # No .git anywhere above this fixture dir -> find_repo_root() returns None.
        # Use a plain subdirectory of the OS temp root with no .git ancestor.
        no_git_root = Path(tempfile.mkdtemp(prefix="aesop-hook-preflight-nogit-"))
        try:
            exit_code, _stdout, stderr = run_preflight(no_git_root)
            self.assertEqual(exit_code, 2)
            self.assertIn("Could not find repository root", stderr)
        finally:
            import shutil

            shutil.rmtree(no_git_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
