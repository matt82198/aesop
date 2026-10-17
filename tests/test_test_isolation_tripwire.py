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
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRIPWIRE = REPO_ROOT / "tools" / "test_isolation_tripwire.py"


def run_tripwire(root, command, env=None):
    full_env = dict(os.environ)
    # Hermetic: the watched conductor3 root is the fixture under --root. Without this,
    # an inherited CONDUCTOR_ROOT (or the default ~/conductor3) makes the tripwire watch
    # the box-global fleet state that live daemons write while a shard runs.
    full_env["CONDUCTOR_ROOT"] = str(Path(root) / "conductor3")
    full_env.pop("AESOP_ROOT", None)
    # Scheduled-task snapshot is box-global too (Windows Aesop* tasks). Point the
    # override at a command that cannot run, so the snapshot is "undeterminable"
    # on both sides of the window and compares equal.
    full_env["AESOP_TRIPWIRE_SCHTASKS_CMD"] = "aesop-tripwire-schtasks-stub-unavailable"
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
    """Aesop* Windows scheduled-task DEFINITIONS, via stubbed
    AESOP_TRIPWIRE_SCHTASKS_CMD (task-name listing) and
    AESOP_TRIPWIRE_SCHTASKS_XML_CMD (per-task definition XML) so this is
    exercised on any platform without touching the real Task Scheduler.

    Regression context (2026-10-06 flaky-tripwire incident): the prior
    design hashed the CSV's Status column, which the live
    AesopWatchdogDaemon/AesopRefinementMonitor flip between Running/Ready
    every few minutes with no test touching tasks -- alternating OK/FAIL on
    a live box with nothing wrong. The fix hashes only the per-task
    definition XML, which structurally never contains run-state fields
    (LastRunTime/NextRunTime/Last Result/State). These tests are red-first:
    each one failed under the OLD Status-hashing design and passes under the
    new definition-only one (verified by running them against the pre-fix
    snapshot_scheduled_tasks before this change landed)."""

    def _write_list_stub(self, tmp: Path) -> str:
        """Fake `schtasks /query /fo CSV`: always lists \\Aesop\\Watchdog,
        plus \\Aesop\\RogueTask once env AESOP_TEST_SCHTASKS_LIST_FLAG exists.
        Status alternates Ready/Running on successive calls (counted via
        AESOP_TEST_SCHTASKS_LIST_COUNTER) when AESOP_TEST_SCHTASKS_LIST_TOGGLE=1
        -- simulating exactly the live-daemon churn that caused the incident,
        with no test actually touching anything."""
        script = tmp / "fake_schtasks_list.py"
        script.write_text(
            "import os, pathlib, sys\n"
            "flag = os.environ.get('AESOP_TEST_SCHTASKS_LIST_FLAG')\n"
            "counter_path = os.environ.get('AESOP_TEST_SCHTASKS_LIST_COUNTER')\n"
            "toggle = os.environ.get('AESOP_TEST_SCHTASKS_LIST_TOGGLE') == '1'\n"
            "names = ['\\\\Aesop\\\\Watchdog']\n"
            "if flag and pathlib.Path(flag).exists():\n"
            "    names.append('\\\\Aesop\\\\RogueTask')\n"
            "status = 'Ready'\n"
            "if toggle and counter_path:\n"
            "    p = pathlib.Path(counter_path)\n"
            "    n = int(p.read_text()) + 1 if p.exists() else 1\n"
            "    p.write_text(str(n))\n"
            "    status = 'Running' if n % 2 == 0 else 'Ready'\n"
            "rows = ['TaskName,\"Next Run Time\",\"Status\"']\n"
            "for nm in names:\n"
            "    rows.append('{},\"N/A\",\"{}\"'.format(nm, status))\n"
            "sys.stdout.write(chr(10).join(rows) + chr(10))\n",
            encoding="utf-8",
        )
        return " ".join(shlex.quote(p) for p in (sys.executable, str(script)))

    def _write_xml_stub(self, tmp: Path) -> str:
        """Fake `schtasks /query /tn <name> /xml`: emits a fixed task
        definition, except the Command for \\Aesop\\Watchdog switches to a
        different path once env AESOP_TEST_SCHTASKS_XML_FLAG exists --
        simulating a real definition edit (not run-state). Returns the
        AESOP_TRIPWIRE_SCHTASKS_XML_CMD template with the literal "{name}"
        token the tripwire substitutes."""
        script = tmp / "fake_schtasks_xml.py"
        script.write_text(
            "import os, sys\n"
            "name = sys.argv[1] if len(sys.argv) > 1 else ''\n"
            "flag = os.environ.get('AESOP_TEST_SCHTASKS_XML_FLAG')\n"
            "changed = bool(flag) and os.path.exists(flag)\n"
            "command = (r'C:\\new\\aesop\\run.exe' if (changed and name == '\\\\Aesop\\\\Watchdog')\n"
            "           else r'C:\\orig\\aesop\\run.exe')\n"
            "xml = ('<Task><RegistrationInfo><URI>' + name + '</URI></RegistrationInfo>'\n"
            "       '<Actions><Exec><Command>' + command + '</Command></Exec></Actions>'\n"
            "       '<Triggers><TimeTrigger><StartBoundary>2026-01-01T00:00:00'\n"
            "       '</StartBoundary></TimeTrigger></Triggers></Task>')\n"
            "sys.stdout.write(xml)\n",
            encoding="utf-8",
        )
        quoted = " ".join(shlex.quote(p) for p in (sys.executable, str(script)))
        return f"{quoted} {{name}}"

    def test_clean_when_scheduled_tasks_unchanged(self):
        """Baseline: nothing flagged, no toggling -> no scheduled-task FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "fake-home-real"
            root.mkdir()
            list_cmd = self._write_list_stub(tmp_path)
            xml_cmd = self._write_xml_stub(tmp_path)
            proc = run_tripwire(
                root, [sys.executable, "-c", "pass"],
                env={"AESOP_TRIPWIRE_SCHTASKS_CMD": list_cmd, "AESOP_TRIPWIRE_SCHTASKS_XML_CMD": xml_cmd},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)

    def test_passes_when_only_status_and_next_run_time_change(self):
        """RED-FIRST: Status/Next Run Time alternate between the tripwire's
        own before/after probes (AESOP_TEST_SCHTASKS_LIST_TOGGLE=1), with no
        test touching any task -- exactly the live-box incident. Must PASS.
        Under the OLD Status-hashing design this would FAIL every time."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "fake-home-real"
            root.mkdir()
            list_cmd = self._write_list_stub(tmp_path)
            xml_cmd = self._write_xml_stub(tmp_path)
            proc = run_tripwire(
                root, [sys.executable, "-c", "pass"],
                env={
                    "AESOP_TRIPWIRE_SCHTASKS_CMD": list_cmd,
                    "AESOP_TRIPWIRE_SCHTASKS_XML_CMD": xml_cmd,
                    "AESOP_TEST_SCHTASKS_LIST_TOGGLE": "1",
                    "AESOP_TEST_SCHTASKS_LIST_COUNTER": str(tmp_path / "list_calls.count"),
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)

    def test_fails_when_action_command_changes(self):
        """A real definition edit (Action Command) between before/after
        probes of the SAME task -> must FAIL, naming the task."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "fake-home-real"
            root.mkdir()
            list_cmd = self._write_list_stub(tmp_path)
            xml_cmd = self._write_xml_stub(tmp_path)
            xml_flag = tmp_path / "xml-changed"

            # Wrapped command flips the Action Command the XML stub reports
            # for \Aesop\Watchdog on the tripwire's SECOND (after) probe.
            switch_script = f"import pathlib; pathlib.Path(r'{xml_flag}').write_text('1')"
            proc = run_tripwire(
                root, [sys.executable, "-c", switch_script],
                env={
                    "AESOP_TRIPWIRE_SCHTASKS_CMD": list_cmd,
                    "AESOP_TRIPWIRE_SCHTASKS_XML_CMD": xml_cmd,
                    "AESOP_TEST_SCHTASKS_XML_FLAG": str(xml_flag),
                },
            )
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("scheduled task", proc.stderr)
            self.assertIn("definition changed", proc.stderr)
            self.assertIn(r"\Aesop\Watchdog", proc.stderr)

    def test_fails_when_new_aesop_task_appears(self):
        """A new Aesop* task registration appearing mid-run -> must FAIL,
        naming the new task."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "fake-home-real"
            root.mkdir()
            list_cmd = self._write_list_stub(tmp_path)
            xml_cmd = self._write_xml_stub(tmp_path)
            list_flag = tmp_path / "list-changed"

            # Wrapped command makes the list stub report a second task
            # (\Aesop\RogueTask) on the tripwire's SECOND (after) probe.
            switch_script = f"import pathlib; pathlib.Path(r'{list_flag}').write_text('1')"
            proc = run_tripwire(
                root, [sys.executable, "-c", switch_script],
                env={
                    "AESOP_TRIPWIRE_SCHTASKS_CMD": list_cmd,
                    "AESOP_TRIPWIRE_SCHTASKS_XML_CMD": xml_cmd,
                    "AESOP_TEST_SCHTASKS_LIST_FLAG": str(list_flag),
                },
            )
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("scheduled task", proc.stderr)
            self.assertIn("new task(s)", proc.stderr)
            self.assertIn(r"\Aesop\RogueTask", proc.stderr)

    def test_clean_when_schtasks_unavailable(self):
        """schtasks itself failing (non-zero exit) -> ABSENT sentinel on both
        sides -> no scheduled-task FAIL (loudly-but-green contract)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "fake-home-real"
            root.mkdir()
            script = tmp_path / "fake_schtasks_fail.py"
            script.write_text("import sys; sys.exit(1)\n", encoding="utf-8")
            cmd = " ".join(shlex.quote(p) for p in (sys.executable, str(script)))
            proc = run_tripwire(
                root, [sys.executable, "-c", "pass"],
                env={"AESOP_TRIPWIRE_SCHTASKS_CMD": cmd},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)

    def test_non_windows_or_no_override_is_inert(self):
        """No override set and not running on win32 -> ABSENT, never FAIL.
        On this box (win32) this exercises the override absent AND the real
        `schtasks` binary together with everything else the tripwire checks,
        which is covered by the 5-run stability proof instead; here we only
        assert the contract holds when forced non-Windows via the override
        mechanism returning nothing parseable."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "fake-home-real"
            root.mkdir()
            script = tmp_path / "fake_schtasks_empty.py"
            script.write_text("import sys; sys.stdout.write('not,valid,csv\\n')\n", encoding="utf-8")
            cmd = " ".join(shlex.quote(p) for p in (sys.executable, str(script)))
            proc = run_tripwire(
                root, [sys.executable, "-c", "pass"],
                env={"AESOP_TRIPWIRE_SCHTASKS_CMD": cmd},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)


PLACEHOLDER_WRITE = (
    "import os, pathlib; r = pathlib.Path(os.environ['CONDUCTOR_ROOT']); "
    "(r / 'state' / '.watchdog-heartbeat').write_text('1234567890', encoding='utf-8')"
)


def make_conductor_fixture(tmp_path: Path) -> Path:
    """Fake conductor root with both daemon heartbeats present and fresh, so the
    tripwire has a real baseline to compare a placeholder write against."""
    conductor = tmp_path / "conductor3"
    (conductor / "state").mkdir(parents=True)
    (conductor / "monitor").mkdir(parents=True)
    now = str(int(time.time()))
    (conductor / "state" / ".watchdog-heartbeat").write_text(now, encoding="utf-8")
    (conductor / "monitor" / ".monitor-heartbeat").write_text(now, encoding="utf-8")
    return conductor


class TestPyWrapperArmsIsolationTripwire(unittest.TestCase):
    """Proves the Python test entry points run under the isolation tripwire and
    fail closed on a placeholder live heartbeat (incident 2026-10-14: a Python
    test wrote 1234567890 into both live heartbeats and `npm run test:py`
    stayed green)."""

    def test_test_py_command_fails_closed_on_placeholder_heartbeat(self):
        """Executes the exact argv that package.json's test:py runs, differing
        only in the discover directory, which points at a throwaway module."""
        repo_root = Path(__file__).resolve().parent.parent
        with open(repo_root / "package.json", encoding="utf-8") as f:
            test_py = json.load(f)["scripts"]["test:py"]
        argv = shlex.split(test_py)
        self.assertEqual(argv[0], "python")
        argv[0] = sys.executable
        argv[argv.index("discover") + 2] = "{DISCOVER_DIR}"  # the token after "-s"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            conductor = make_conductor_fixture(tmp_path)
            discover_dir = tmp_path / "tests"
            discover_dir.mkdir()
            (discover_dir / "test_throwaway_placeholder.py").write_text(
                "import os, pathlib, unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_write(self):\n"
                "        r = pathlib.Path(os.environ['CONDUCTOR_ROOT'])\n"
                "        (r / 'state' / '.watchdog-heartbeat').write_text('1234567890')\n"
                "        (r / 'monitor' / '.monitor-heartbeat').write_text('1234567890')\n",
                encoding="utf-8",
            )
            argv = [str(discover_dir) if tok == "{DISCOVER_DIR}" else tok for tok in argv]
            env = dict(os.environ)
            env["CONDUCTOR_ROOT"] = str(conductor)
            env.pop("AESOP_CI_SHARD_TRIPWIRE_ARMED", None)
            proc = subprocess.run(
                argv, cwd=str(repo_root), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300, env=env,
            )
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("FAIL: the real profile changed", proc.stderr)
            self.assertIn("watchdog_heartbeat", proc.stderr)

    def test_shard_runner_arms_tripwire_and_passes_through_when_armed(self):
        """ci_shard_runner's arming helper: unarmed -> wrapped and fails closed on
        a placeholder write; already armed (child of the wrapper) -> runs the
        target directly with no second wrap."""
        tools_dir = Path(__file__).resolve().parent.parent / "tools"
        driver = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import ci_shard_runner as r; "
            "r._arm_isolation_tripwire([sys.executable, '-c', sys.argv[2]]); "
            "print('PASSTHROUGH')"
        )
        with tempfile.TemporaryDirectory() as tmp:
            conductor = make_conductor_fixture(Path(tmp))
            base_env = dict(os.environ)
            base_env["CONDUCTOR_ROOT"] = str(conductor)

            unarmed_env = dict(base_env)
            unarmed_env.pop("AESOP_CI_SHARD_TRIPWIRE_ARMED", None)
            armed = subprocess.run(
                [sys.executable, "-c", driver, str(tools_dir), PLACEHOLDER_WRITE],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, env=unarmed_env,
            )
            self.assertEqual(armed.returncode, 1, armed.stderr)
            self.assertIn("FAIL: the real profile changed", armed.stderr)
            self.assertNotIn("PASSTHROUGH", armed.stdout)

            armed_env = dict(base_env)
            armed_env["AESOP_CI_SHARD_TRIPWIRE_ARMED"] = "1"
            passthrough = subprocess.run(
                [sys.executable, "-c", driver, str(tools_dir), PLACEHOLDER_WRITE],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, env=armed_env,
            )
            self.assertEqual(passthrough.returncode, 0, passthrough.stderr)
            self.assertIn("PASSTHROUGH", passthrough.stdout)
            self.assertNotIn("tripwire", passthrough.stderr)


class TestCreatedFleetStateIsValidated(unittest.TestCase):
    """Regression for the Windows CI shard red (PR #974): an EMPTY fleet-state
    root appearing during a run must stay inert, while a placeholder heartbeat
    written into a created file must still fail closed."""

    def _write_heartbeat_cmd(self, conductor: Path, value: str):
        script = (
            "import pathlib; p = pathlib.Path(r'%s') / 'state'; "
            "p.mkdir(parents=True, exist_ok=True); "
            "(p / '.watchdog-heartbeat').write_text('%s', encoding='utf-8')"
        ) % (conductor, value)
        return [sys.executable, "-c", script]

    def test_empty_fleet_state_root_creation_is_inert(self):
        with tempfile.TemporaryDirectory() as tmp:
            conductor = Path(tmp) / "conductor3"
            root = Path(tmp) / "fake-home"
            root.mkdir()
            script = "import pathlib; pathlib.Path(r'%s').mkdir(parents=True)" % conductor
            proc = run_tripwire(root, [sys.executable, "-c", script],
                                env={"CONDUCTOR_ROOT": str(conductor)})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)

    def test_placeholder_heartbeat_in_created_root_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            conductor = Path(tmp) / "conductor3"
            root = Path(tmp) / "fake-home"
            root.mkdir()
            proc = run_tripwire(root, self._write_heartbeat_cmd(conductor, "1234567890"),
                                env={"CONDUCTOR_ROOT": str(conductor)})
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("FAIL: the real profile changed", proc.stderr)

    def test_placeholder_heartbeat_created_in_existing_root_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            conductor = Path(tmp) / "conductor3"
            (conductor / "state").mkdir(parents=True)
            (conductor / "monitor").mkdir(parents=True)
            root = Path(tmp) / "fake-home"
            root.mkdir()
            proc = run_tripwire(root, self._write_heartbeat_cmd(conductor, "1234567890"),
                                env={"CONDUCTOR_ROOT": str(conductor)})
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("created with stale/placeholder epoch", proc.stderr)


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=tripwire-test",
         "-c", "user.email=tripwire-test@example.invalid", *args],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def make_profile_checkout(tmp: Path) -> Path:
    """A fake profile whose .claude is a git checkout (mirrors the brain repo).
    Branch `next` holds a second commit changing hooks/a.py, so a
    `git checkout next` during a run is a real HEAD advance. Every git call
    lives in a throwaway temp repo, never the developer's profile."""
    root = tmp / "fake-home"
    claude = root / ".claude"
    (claude / "hooks").mkdir(parents=True)
    (claude / "hooks" / "a.py").write_text("v1\n", encoding="utf-8")
    _git(claude, "init", "-q", "-b", "main")
    _git(claude, "add", "-A")
    _git(claude, "commit", "-q", "-m", "v1")
    _git(claude, "checkout", "-q", "-b", "next")
    (claude / "hooks" / "a.py").write_text("v2\n", encoding="utf-8")
    _git(claude, "add", "-A")
    _git(claude, "commit", "-q", "-m", "v2")
    _git(claude, "checkout", "-q", "main")
    return root


def _advance_script(claude: Path, extra: str = "") -> str:
    """Python run as the wrapped command: advance the profile checkout by git
    (a HEAD move, as `git pull --ff-only` would), then optionally write one
    extra file under the profile (a test write)."""
    script = (
        "import subprocess; "
        f"subprocess.run(['git', '-C', {str(claude)!r}, 'checkout', '-q', 'next'], check=True)"
    )
    if extra:
        script += f"; open({str(claude / extra)!r}, 'w').write('written by a test')"
    return script


def _fail_bullets(stderr: str) -> list:
    """The `  - <path>` lines of the FAIL block (the findings, not the INFO notes)."""
    return [line for line in stderr.splitlines() if line.startswith("  - ")]


class TestGitDeployAndLiveWriterAttribution(unittest.TestCase):
    """2026-10-15 incident: two honest pushes were blocked because the tripwire
    failed on changes that were not tests. (1) ~/.claude is a git checkout whose
    HEAD another lane fast-forwarded during the shard. (2) the usage-watchdog
    daemon rewrites its status JSON every tick. Attribution: a HEAD move with a
    reflog entry inside the run window explains a clean tracked file (INFO); a
    registered live writer with a pidfile/heartbeat inside the window explains
    its file (INFO). Anything else still fails, and a mixed run names only the
    unexplained path."""

    STATUS = "state/usage-watchdog.status.json"

    def _seed_status(self, root: Path) -> Path:
        status = root / "conductor3" / self.STATUS
        status.write_text('{"v": 1}', encoding="utf-8")
        return status

    def test_a_git_deploy_during_run_is_info_not_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_profile_checkout(Path(tmp))
            claude = root / ".claude"
            proc = run_tripwire(root, [sys.executable, "-c", _advance_script(claude)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("INFO", proc.stderr)
            self.assertIn("~/.claude/hooks/a.py changed by git", proc.stderr)
            self.assertIn("reflog", proc.stderr)
            self.assertIn("not a test", proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)
            self.assertIn("~/.claude/hooks/a.py=git-deploy", proc.stderr)
            self.assertEqual((claude / "hooks" / "a.py").read_text(), "v2\n")

    def test_b_live_writer_with_evidence_is_info_not_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home"
            make_fake_conductor(root)
            self._seed_status(root)
            status = root / "conductor3" / self.STATUS
            evidence = root / "conductor3" / "state" / "usage-watchdog.heartbeat"
            script = (
                f"open(r'{status}', 'w').write('{{\"v\": 2}}'); "
                f"open(r'{evidence}', 'w').write('1')"
            )
            proc = run_tripwire(root, [sys.executable, "-c", script])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("INFO", proc.stderr)
            self.assertIn("usage-watchdog.status.json", proc.stderr)
            self.assertIn("written by live writer", proc.stderr)
            self.assertIn("not a test", proc.stderr)
            self.assertNotIn("FAIL", proc.stderr)
            self.assertIn("fleet-state/state/usage-watchdog.status.json=live-writer",
                          proc.stderr)

    def test_c_live_writer_file_without_evidence_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home"
            make_fake_conductor(root)
            self._seed_status(root)
            status = root / "conductor3" / self.STATUS
            proc = run_tripwire(root, [sys.executable, "-c",
                                       f"open(r'{status}', 'w').write('{{\"v\": 2}}')"])
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("usage-watchdog.status.json", proc.stderr)
            self.assertIn("no pidfile or heartbeat", proc.stderr)

    def test_c2_live_writer_evidence_from_before_the_run_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home"
            make_fake_conductor(root)
            self._seed_status(root)
            status = root / "conductor3" / self.STATUS
            evidence = root / "conductor3" / "state" / "usage-watchdog.heartbeat"
            evidence.write_text("1", encoding="utf-8")
            os.utime(evidence, (1000, 1000))  # stale: far before the run window
            proc = run_tripwire(root, [sys.executable, "-c",
                                       f"open(r'{status}', 'w').write('{{\"v\": 2}}')"])
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("usage-watchdog.status.json", proc.stderr)
            self.assertIn("FAIL", proc.stderr)

    def test_d_unrelated_fleet_state_write_during_run_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home"
            make_fake_conductor(root)
            new_file = root / "conductor3" / "state" / "orchestrator-status.json"
            proc = run_tripwire(root, [sys.executable, "-c",
                                       f"open(r'{new_file}', 'w').write('{{}}')"])
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("orchestrator-status.json", proc.stderr)
            self.assertNotIn("live writer", proc.stderr)

    def test_e_unrelated_profile_write_during_run_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_profile_checkout(Path(tmp))
            claude = root / ".claude"
            stray = claude / "hooks" / "stray.py"
            proc = run_tripwire(root, [sys.executable, "-c",
                                       f"open(r'{stray}', 'w').write('stray')"])
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("hooks", proc.stderr)
            self.assertIn("stray.py", proc.stderr)

    def test_f_mixed_deploy_and_unrelated_write_names_only_the_unrelated_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_profile_checkout(Path(tmp))
            claude = root / ".claude"
            proc = run_tripwire(root, [sys.executable, "-c",
                                       _advance_script(claude, extra="hooks/extra.py")])
            self.assertEqual(proc.returncode, 1, proc.stderr)
            bullets = _fail_bullets(proc.stderr)
            self.assertEqual(len(bullets), 1, proc.stderr)
            self.assertIn("hooks/extra.py", bullets[0])
            self.assertNotIn("hooks/a.py", bullets[0])
            self.assertIn("INFO", proc.stderr)
            self.assertIn("~/.claude/hooks/a.py changed by git", proc.stderr)
            self.assertIn("~/.claude/hooks/a.py=git-deploy", proc.stderr)
            self.assertIn("~/.claude/hooks/extra.py=unrelated", proc.stderr)

    def test_g_tracked_tamper_without_a_head_move_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_profile_checkout(Path(tmp))
            claude = root / ".claude"
            script = f"open(r'{claude / 'hooks' / 'a.py'}', 'w').write('tampered')"
            proc = run_tripwire(root, [sys.executable, "-c", script])
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("hooks", proc.stderr)
            self.assertNotIn("changed by git", proc.stderr)

    def test_h_git_unavailable_for_checkout_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_profile_checkout(Path(tmp))
            claude = root / ".claude"
            proc = run_tripwire(
                root, [sys.executable, "-c", _advance_script(claude)],
                env={"AESOP_TRIPWIRE_GIT_CMD": "aesop-tripwire-git-missing"},
            )
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("FAIL", proc.stderr)
            self.assertIn("hooks", proc.stderr)
            self.assertNotIn("changed by git", proc.stderr)


KILL_MARK = "AESOP_TEST_SIGKILL_MARK"


class TestSignalKilledWrappedCommand(unittest.TestCase):
    """A wrapped command killed by a signal reports a NEGATIVE returncode. It must
    never exit 0: returncode -9 with a clean profile used to give max(-9, 0) == 0,
    so a SIGKILLed (OOM) test shard reported green with its tests unrun."""

    def setUp(self):
        sys.path.insert(0, str(REPO_ROOT))
        from tools import test_isolation_tripwire as tit
        self.tit = tit

    def test_sigkill_returncode_yields_137_in_process(self):
        from unittest import mock
        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and KILL_MARK in cmd:
                return subprocess.CompletedProcess(cmd, -9)
            return real_run(cmd, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = tmp / "fake-home-real"
            root.mkdir()
            stub_env = {
                "CONDUCTOR_ROOT": str(tmp / "conductor3"),
                "AESOP_TRIPWIRE_SCHTASKS_CMD": "aesop-tripwire-schtasks-stub-unavailable",
            }
            with mock.patch.dict(os.environ, stub_env), \
                    mock.patch.object(subprocess, "run", side_effect=fake_run):
                rc = self.tit.main(["--root", str(root), "--",
                                    sys.executable, "-c", KILL_MARK])
        self.assertEqual(rc, 137)

    @unittest.skipUnless(os.name == "posix", "a real SIGKILL needs POSIX")
    def test_real_sigkill_child_yields_137_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fake-home-real"
            make_fake_profile(root)
            proc = run_tripwire(root, [sys.executable, "-c",
                                       "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"])
        self.assertEqual(proc.returncode, 137, proc.stderr)

    def test_combine_rule_is_nonzero_if_either_side_is_nonzero(self):
        self.assertEqual(self.tit._combine_exit(0, 0), 0)
        self.assertEqual(self.tit._combine_exit(0, 1), 1)
        self.assertEqual(self.tit._combine_exit(7, 0), 7)
        self.assertEqual(self.tit._exit_code(-9), 137)
        self.assertEqual(self.tit._exit_code(None), 1)


if __name__ == "__main__":
    unittest.main()
