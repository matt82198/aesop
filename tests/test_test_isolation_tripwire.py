"""
Regression tests proving tools/test_isolation_tripwire.py actually bites.

Context (incident 2026-10-05): running the Node suite on a developer box let
bin/cli.js's installSkills() overwrite the REAL ~/.claude/skills/{power,
dashboard,buildsystem}/SKILL.md. "Tests must not pollute cwd or global state"
existed only as prose in tests/CLAUDE.md and LANE-CONTRACT.md. These tests
prove the tripwire gate -- not inspection of its source -- actually fails
closed on exactly that class of write, and stays green when nothing touches
the protected root.

Every test points --root at a throwaway temp directory standing in for "the
real profile" (never the developer's actual ~/.claude or global git config),
per tools/test_isolation_tripwire.py's own --root contract.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRIPWIRE = REPO_ROOT / "tools" / "test_isolation_tripwire.py"


def run_tripwire(root, command, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(TRIPWIRE), "--root", str(root), "--", *command],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env=full_env,
    )
    return proc


def make_fake_profile(root: Path):
    skills = root / ".claude" / "skills" / "power"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# original power skill\n", encoding="utf-8")
    (root / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")


class TestIsolationTripwire(unittest.TestCase):
    def test_inert_when_profile_absent(self):
        """No ~/.claude under --root at all -> degrades loudly-but-green."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-empty"
            root.mkdir()
            proc = run_tripwire(root, [sys.executable, "-c", "pass"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("tripwire inert", proc.stderr)

    def test_allows_write_outside_root(self):
        """A command that writes somewhere OTHER than --root must be let through
        (this is what isolated-env.mjs guarantees in production: tests never even
        point at the real root in the first place)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_profile(root)
            isolated = Path(tmp) / "fake-home-isolated"
            (isolated / ".claude" / "skills" / "buildsystem").mkdir(parents=True)

            write_target = isolated / ".claude" / "skills" / "buildsystem" / "SKILL.md"
            script = f"open(r'{write_target}', 'w').write('fine, not the real one')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("OK: real profile unchanged", proc.stderr)
            # And the real fixture's own skill file is untouched.
            self.assertEqual(
                (root / ".claude" / "skills" / "power" / "SKILL.md").read_text(),
                "# original power skill\n",
            )

    def test_catches_write_to_real_profile(self):
        """The exact incident, reproduced: a command overwrites a real SKILL.md
        under --root. The tripwire must fail closed and NAME the changed path."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_profile(root)

            victim = root / ".claude" / "skills" / "power" / "SKILL.md"
            script = f"open(r'{victim}', 'w').write('clobbered by an unisolated test')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("skills", proc.stderr)

    def test_catches_added_file_under_protected_dir(self):
        """A NEW file appearing under a protected dir (not just a modified one)
        must also be caught -- installSkills() creates files that never existed
        when a skill wasn't installed before."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_profile(root)

            new_file = root / ".claude" / "skills" / "dashboard" / "SKILL.md"
            script = (
                f"import os; os.makedirs(r'{new_file.parent}', exist_ok=True); "
                f"open(r'{new_file}', 'w').write('new skill that should not be here')"
            )
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("skills", proc.stderr)

    def test_catches_global_git_config_change(self):
        """A command that mutates the (fixture-sandboxed) global git config must
        be caught and the changed key named."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            root.mkdir()
            # HOME is pinned to root for the WHOLE wrapped invocation (tripwire's own
            # git-config snapshot calls already pin HOME=root internally regardless;
            # this also makes the wrapped `git config --global` write land under root).
            proc = run_tripwire(
                root,
                ["git", "config", "--global", "--add", "user.tripwiretestkey", "testvalue"],
                env={"HOME": str(root), "USERPROFILE": str(root)},
            )
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("global git config", proc.stderr)
            self.assertIn("user.tripwiretestkey", proc.stderr)

    def test_clean_run_is_silent_about_findings(self):
        """A populated profile, untouched by the wrapped command, is clean."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_profile(root)
            proc = run_tripwire(root, [sys.executable, "-c", "pass"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)

    def test_propagates_wrapped_command_exit_code_when_profile_clean(self):
        """A red wrapped command on an otherwise-clean profile must still fail
        the overall run (the tripwire must never swallow a real test failure)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_profile(root)
            proc = run_tripwire(root, [sys.executable, "-c", "import sys; sys.exit(7)"])
            self.assertEqual(proc.returncode, 7, proc.stderr)


if __name__ == "__main__":
    unittest.main()
