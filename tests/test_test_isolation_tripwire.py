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
import shlex
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


def make_fake_conductor(root: Path):
    state = root / "conductor3" / "state"
    monitor = root / "conductor3" / "monitor"
    state.mkdir(parents=True)
    monitor.mkdir(parents=True)
    (state / ".watchdog-heartbeat").write_text("1700000000", encoding="utf-8")
    (monitor / ".monitor-heartbeat").write_text("1700000000", encoding="utf-8")
    (state / "tracker.json").write_text('{"items": []}', encoding="utf-8")
    (state / ".watchdog-repos.json").write_text('{"repos": []}', encoding="utf-8")


class TestIsolationTripwireConductor3(unittest.TestCase):
    """2026-10-06 incident: a shell-test lane left the test placeholder
    "1234567890" in the LIVE ~/conductor3/state/.watchdog-heartbeat. These
    tests prove the extended tripwire catches exactly that class of write
    against a fake 'real' conductor3 fixture under --root -- never the
    developer's actual ~/conductor3."""

    def test_catches_write_to_watchdog_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            victim = root / "conductor3" / "state" / ".watchdog-heartbeat"
            script = f"open(r'{victim}', 'w').write('1234567890')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("watchdog-heartbeat", proc.stderr)

    def test_catches_write_to_monitor_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            victim = root / "conductor3" / "monitor" / ".monitor-heartbeat"
            script = f"open(r'{victim}', 'w').write('not_a_timestamp')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("monitor-heartbeat", proc.stderr)

    def test_catches_write_to_conductor_state_json(self):
        """A lock/state JSON file under conductor3/state/ (e.g. tracker.json)
        changing must be caught and the exact file named."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            victim = root / "conductor3" / "state" / "tracker.json"
            script = f"open(r'{victim}', 'w').write('{{\"items\": [\"clobbered\"]}}')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("tracker.json", proc.stderr)

    def test_catches_new_conductor_state_json_file(self):
        """A NEW json file appearing under conductor3/state/ must also be
        caught, not just a modified one."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            new_file = root / "conductor3" / "state" / "orchestrator-status.json"
            script = f"open(r'{new_file}', 'w').write('{{}}')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("orchestrator-status.json", proc.stderr)

    def test_allows_write_outside_conductor_root(self):
        """A write to a conductor3 fixture OTHER than --root's must be let
        through, mirroring test_allows_write_outside_root for ~/.claude."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)
            isolated = Path(tmp) / "fake-home-isolated"
            (isolated / "conductor3" / "state").mkdir(parents=True)

            write_target = isolated / "conductor3" / "state" / ".watchdog-heartbeat"
            script = f"open(r'{write_target}', 'w').write('1234567890')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)

    def test_inert_when_conductor_absent(self):
        """No conductor3 dir under --root at all -> degrades loudly-but-green,
        same contract as the ~/.claude absent case."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-empty"
            root.mkdir()
            proc = run_tripwire(root, [sys.executable, "-c", "pass"])
            self.assertEqual(proc.returncode, 0, proc.stderr)


class TestIsolationTripwireHeartbeatValidity(unittest.TestCase):
    """Daemon-written files (heartbeats, logs, repos.json) require VALIDITY
    checks instead of hash comparisons. The daemons legitimately update
    these files during test runs; the tripwire must distinguish between
    expected changes (daemon ticking) and anomalies (wrong value, invalid
    format, truncation).

    Incident 2026-10-06: tests/test-selfheal.sh left "1234567890" in the
    LIVE ~/.conductor3/state/.watchdog-heartbeat because the hash comparison
    failed to detect the daemon's legitimate updates mid-run, and no test
    created a true/green baseline for this class of validity check."""

    def test_heartbeat_can_advance_by_daemon(self):
        """A heartbeat that advances by the daemon ticking (+300s) must PASS
        (this is the normal case during ≥5-min test runs)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            before_hb = root / "conductor3" / "state" / ".watchdog-heartbeat"
            before_val = before_hb.read_text(encoding="utf-8").strip()

            # Wrapped command advances the heartbeat (simulating daemon tick)
            # by 300 seconds forward.
            import time
            before_epoch = int(before_val)
            after_epoch = before_epoch + 300
            script = f"open(r'{before_hb}', 'w').write(str({after_epoch}))"

            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)

    def test_heartbeat_rejects_placeholder_value(self):
        """Heartbeat overwritten with test placeholder '1234567890' (far in
        the past, 2009) must FAIL -- this was the literal incident."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            victim = root / "conductor3" / "state" / ".watchdog-heartbeat"
            script = f"open(r'{victim}', 'w').write('1234567890')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("watchdog-heartbeat", proc.stderr)

    def test_heartbeat_rejects_non_integer(self):
        """Heartbeat with non-integer content must FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            victim = root / "conductor3" / "state" / ".watchdog-heartbeat"
            script = f"open(r'{victim}', 'w').write('not_a_timestamp')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("watchdog-heartbeat", proc.stderr)

    def test_heartbeat_rejects_decrease(self):
        """Heartbeat that decreases (time going backwards) must FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            before_hb = root / "conductor3" / "state" / ".watchdog-heartbeat"
            before_val = int(before_hb.read_text(encoding="utf-8").strip())

            # Decrease by 60 seconds
            after_epoch = before_val - 60
            script = f"open(r'{before_hb}', 'w').write(str({after_epoch}))"

            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("watchdog-heartbeat", proc.stderr)

    def test_log_can_grow(self):
        """A log file that only grows (append) must PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            # Create a log file in the conductor state dir
            log_file = root / "conductor3" / "state" / "FLEET-BACKUP.log"
            log_file.write_text("[2026-10-06 12:00:00] cycle 1 ok\n", encoding="utf-8")

            # Wrapped command appends to the log
            script = f"with open(r'{log_file}', 'a') as f: f.write('[2026-10-06 12:00:01] cycle 2 ok\\n')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)

    def test_log_rejects_truncation(self):
        """A log file that shrinks (truncation) must FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            log_file = root / "conductor3" / "state" / "FLEET-BACKUP.log"
            log_file.write_text("[2026-10-06 12:00:00] cycle 1 ok\n", encoding="utf-8")

            # Wrapped command truncates the log
            script = f"open(r'{log_file}', 'w').write('[2026-10-06 12:00:00] c')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAIL", proc.stderr)

    def test_repos_json_can_have_new_fields(self):
        """A repos.json that still parses and keeps the same top-level shape
        (dict with consistent keys) must PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            repos_json = root / "conductor3" / "state" / ".watchdog-repos.json"
            repos_json.write_text('{"repos": [], "timestamp": 1700000000}', encoding="utf-8")

            # Wrapped command updates the repos json (same shape, different values)
            script = (
                "import json;"
                f"j = {{'repos': [{{'path': '/some/repo'}}], 'timestamp': 1700000300}};"
                f"open(r'{repos_json}', 'w').write(json.dumps(j))"
            )
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)

    def test_repos_json_rejects_invalid_json(self):
        """A repos.json that no longer parses as JSON must FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_conductor(root)

            repos_json = root / "conductor3" / "state" / ".watchdog-repos.json"
            repos_json.write_text('{"repos": []}', encoding="utf-8")

            # Wrapped command corrupts the JSON
            script = f"open(r'{repos_json}', 'w').write('not valid json here')"
            proc = run_tripwire(root, [sys.executable, "-c", script])

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAIL", proc.stderr)


class TestIsolationTripwireScheduledTasks(unittest.TestCase):
    """Aesop* Windows scheduled-task registrations, via a stubbed
    AESOP_TRIPWIRE_SCHTASKS_CMD so this is exercised on any platform without
    touching the real Task Scheduler. The stub reads its CSV rows from a flag
    file so the WRAPPED command (which the tripwire runs between its before/
    after probes) can flip what the second probe sees -- simulating a
    scheduled task registration appearing mid-run."""

    def _write_stub(self, tmp: Path, flag: Path) -> str:
        """A fake schtasks that prints one row normally, and a second row once
        `flag` exists. Returns the shlex-quoted AESOP_TRIPWIRE_SCHTASKS_CMD."""
        script = tmp / "fake_schtasks.py"
        base_row = r'\Aesop\Watchdog,"10/6/2026 12:00:00 PM","Ready"'
        new_row = r'\Aesop\RogueTask,"10/6/2026 1:00:00 PM","Ready"'
        lines = [
            "import pathlib, sys",
            f"flag = pathlib.Path({str(flag)!r})",
            f"rows = ['TaskName,\"Next Run Time\",\"Status\"', {base_row!r}]",
            "if flag.exists():",
            f"    rows.append({new_row!r})",
            "sys.stdout.write(chr(10).join(rows) + chr(10))",
        ]
        script.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return " ".join(shlex.quote(p) for p in (sys.executable, str(script)))

    def test_catches_scheduled_task_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "fake-home-real"
            root.mkdir()
            flag = tmp_path / "task-appeared"
            cmd = self._write_stub(tmp_path, flag)

            # The wrapped command creates the flag file, so the tripwire's
            # SECOND (after) probe of the stub sees the new RogueTask row.
            switch_script = f"import pathlib; pathlib.Path(r'{flag}').write_text('1')"
            proc = run_tripwire(
                root,
                [sys.executable, "-c", switch_script],
                env={"AESOP_TRIPWIRE_SCHTASKS_CMD": cmd},
            )
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("scheduled task", proc.stderr)

    def test_clean_when_scheduled_tasks_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "fake-home-real"
            root.mkdir()
            flag = tmp_path / "task-appeared"  # never created in this test
            cmd = self._write_stub(tmp_path, flag)
            proc = run_tripwire(root, [sys.executable, "-c", "pass"], env={"AESOP_TRIPWIRE_SCHTASKS_CMD": cmd})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)


if __name__ == "__main__":
    unittest.main()
