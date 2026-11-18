"""Tests for daemons/install_launchd.py and Darwin mode in tools/task_cadence_check.py.

S1 TDD specification:
- `daemons/install_launchd.py --plan --json` emits valid launchd agent specs
  for com.aesop.watchdog, com.aesop.selfheal, com.aesop.monitor, and
  com.aesop.merge-queue with StartInterval cadences matching
  daemons/install-tasks.ps1, dynamic AESOP_ROOT / HOME paths (zero hardcoded
  user paths), and POSIX bash / node / python3 commands.
- `--install --dest-dir <tmpdir> --no-launchctl` writes valid XML .plist
  files parseable by plistlib.loads(), and `--uninstall --dest-dir <tmpdir>
  --no-launchctl` removes them cleanly.
- `tools/task_cadence_check.py` Darwin mode (`check_launchd_cadence` and
  `run_cli(..., platform="darwin")`) validates a directory of .plist files
  against expected cadences and fails closed on drifted StartInterval,
  disabled plists, unparseable XML, or missing plists.
"""

import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_LAUNCHD_PATH = REPO_ROOT / "daemons" / "install_launchd.py"
INSTALL_TASKS_PS1 = REPO_ROOT / "daemons" / "install-tasks.ps1"
TASK_CADENCE_PATH = REPO_ROOT / "tools" / "task_cadence_check.py"

EXPECTED_LABELS = (
    "com.aesop.watchdog",
    "com.aesop.selfheal",
    "com.aesop.monitor",
    "com.aesop.merge-queue",
)


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_installer(args, env_overrides=None):
    if not INSTALL_LAUNCHD_PATH.exists():
        raise AssertionError("missing installer script: %s" % INSTALL_LAUNCHD_PATH)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AESOP_STATE_ROOT", "AESOP_ROOT")
    }
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(INSTALL_LAUNCHD_PATH)] + list(args),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=30,
    )


class TestInstallLaunchdPlan(unittest.TestCase):
    """--plan --json output and cadence parity with install-tasks.ps1."""

    def test_plan_json_emits_all_four_agents_with_matching_cadences(self):
        tcc = _load_module("tcc_for_plan", TASK_CADENCE_PATH)
        ps1_cadences = tcc.parse_expected_cadences(
            INSTALL_TASKS_PS1.read_text(encoding="utf-8", errors="replace")
        )
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "aesop-checkout"
            fake_home = Path(tmp) / "fake-home"
            fake_dest = fake_home / "Library" / "LaunchAgents"
            proc = _run_installer(
                [
                    "--plan",
                    "--json",
                    "--aesop-root",
                    str(fake_root),
                    "--home",
                    str(fake_home),
                    "--dest-dir",
                    str(fake_dest),
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload.get("mode"), "plan")
            agents = {a["label"]: a for a in payload.get("agents", [])}
            self.assertEqual(set(agents.keys()), set(EXPECTED_LABELS))

            # StartInterval must match daemons/install-tasks.ps1 (minutes * 60)
            self.assertEqual(
                agents["com.aesop.watchdog"]["start_interval_seconds"],
                ps1_cadences["AesopWatchdogDaemon"] * 60,
            )
            self.assertEqual(
                agents["com.aesop.monitor"]["start_interval_seconds"],
                ps1_cadences["AesopRefinementMonitor"] * 60,
            )
            self.assertEqual(
                agents["com.aesop.merge-queue"]["start_interval_seconds"],
                ps1_cadences["AesopMergeQueue"] * 60,
            )
            self.assertGreater(
                agents["com.aesop.selfheal"]["start_interval_seconds"], 0
            )

    def test_plan_uses_dynamic_paths_and_posix_commands(self):
        fake_root = "/srv/custom-aesop-tree"
        fake_home = "/Users/test-runner"
        fake_dest = fake_home + "/Library/LaunchAgents"
        proc = _run_installer(
            [
                "--plan",
                "--json",
                "--aesop-root",
                fake_root,
                "--home",
                fake_home,
                "--dest-dir",
                fake_dest,
            ]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        raw = proc.stdout
        # Must never bake personal user paths or Windows launchers
        for forbidden in (
            "C:/Users/Matt" + "t",
            "/c/Users/matt" + "8",
            "run-hidden.exe",
            ".ps1",
            "bash.exe",
        ):
            self.assertNotIn(forbidden, raw)

        payload = json.loads(raw)
        agents = {a["label"]: a for a in payload["agents"]}

        for label, agent in agents.items():
            plist = agent["plist"]
            self.assertEqual(plist["Label"], label)
            self.assertEqual(
                plist["StartInterval"], agent["start_interval_seconds"]
            )
            env = plist.get("EnvironmentVariables", {})
            self.assertEqual(env.get("AESOP_ROOT", ""), fake_root)
            self.assertEqual(env.get("HOME", ""), fake_home)
            prog_args = plist["ProgramArguments"]
            self.assertIsInstance(prog_args, list)
            self.assertTrue(prog_args)
            joined = " ".join(prog_args)
            self.assertIn(fake_root, joined)

        # Verify POSIX bash / node / python3 conventions
        self.assertIn("bash", agents["com.aesop.watchdog"]["program_arguments"][0])
        self.assertIn("bash", agents["com.aesop.selfheal"]["program_arguments"][0])
        self.assertIn(
            "bash", agents["com.aesop.merge-queue"]["program_arguments"][0]
        )
        monitor_cmd = " ".join(agents["com.aesop.monitor"]["program_arguments"])
        self.assertIn("node", monitor_cmd)
        self.assertIn("collect-signals.mjs", monitor_cmd)


class TestInstallLaunchdWriteAndRemove(unittest.TestCase):
    """--install and --uninstall with --dest-dir <tmpdir> --no-launchctl."""

    def test_install_writes_valid_xml_plists_and_uninstall_removes_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "aesop"
            fake_home = Path(tmp) / "home"
            dest_dir = Path(tmp) / "LaunchAgents"

            install_proc = _run_installer(
                [
                    "--install",
                    "--dest-dir",
                    str(dest_dir),
                    "--aesop-root",
                    str(fake_root),
                    "--home",
                    str(fake_home),
                    "--no-launchctl",
                ]
            )
            self.assertEqual(install_proc.returncode, 0, install_proc.stderr)

            written = sorted(p.name for p in dest_dir.glob("*.plist"))
            self.assertEqual(
                written, sorted(label + ".plist" for label in EXPECTED_LABELS)
            )

            for label in EXPECTED_LABELS:
                plist_file = dest_dir / (label + ".plist")
                raw_bytes = plist_file.read_bytes()
                self.assertIn(b"<!DOCTYPE plist", raw_bytes)
                parsed = plistlib.loads(raw_bytes)
                self.assertEqual(parsed["Label"], label)
                self.assertIsInstance(parsed["StartInterval"], int)
                self.assertGreater(parsed["StartInterval"], 0)
                self.assertIsInstance(parsed["ProgramArguments"], list)

            uninstall_proc = _run_installer(
                [
                    "--uninstall",
                    "--dest-dir",
                    str(dest_dir),
                    "--aesop-root",
                    str(fake_root),
                    "--home",
                    str(fake_home),
                    "--no-launchctl",
                ]
            )
            self.assertEqual(uninstall_proc.returncode, 0, uninstall_proc.stderr)
            remaining = list(dest_dir.glob("*.plist"))
            self.assertEqual(remaining, [])


class TestTaskCadenceCheckDarwin(unittest.TestCase):
    """Darwin mode in tools/task_cadence_check.py."""

    def _write_plist(self, dest_dir, label, start_interval, disabled=False):
        data = {
            "Label": label,
            "ProgramArguments": ["/bin/bash", "/tmp/noop.sh"],
            "StartInterval": start_interval,
        }
        if disabled:
            data["Disabled"] = True
        path = Path(dest_dir) / (label + ".plist")
        path.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))
        return path

    def test_valid_plist_directory_passes(self):
        tcc = _load_module("tcc_darwin_ok", TASK_CADENCE_PATH)
        self.assertTrue(
            hasattr(tcc, "check_launchd_cadence"),
            "task_cadence_check must define check_launchd_cadence()",
        )
        with tempfile.TemporaryDirectory() as tmp:
            expected = {
                "com.aesop.watchdog": 300,
                "com.aesop.selfheal": 60,
                "com.aesop.monitor": 1200,
                "com.aesop.merge-queue": 300,
            }
            for label, secs in expected.items():
                self._write_plist(tmp, label, secs)

            report = tcc.check_launchd_cadence(plist_dir=tmp, expected=expected)
            self.assertEqual(report["exit_code"], 0)
            self.assertEqual(report["status"], "OK")
            self.assertTrue(all(t["status"] == "OK" for t in report["tasks"]))

    def test_drifted_start_interval_fails_closed(self):
        tcc = _load_module("tcc_darwin_drift", TASK_CADENCE_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            expected = {
                "com.aesop.watchdog": 300,
                "com.aesop.monitor": 1200,
            }
            self._write_plist(tmp, "com.aesop.watchdog", 300)
            # Drifted: hourly (3600s) instead of 20 min (1200s)
            self._write_plist(tmp, "com.aesop.monitor", 3600)

            report = tcc.check_launchd_cadence(plist_dir=tmp, expected=expected)
            self.assertEqual(report["exit_code"], 1)
            by_name = {t["task"]: t for t in report["tasks"]}
            self.assertEqual(by_name["com.aesop.watchdog"]["status"], "OK")
            self.assertEqual(by_name["com.aesop.monitor"]["status"], "INTERVAL_MISMATCH")

    def test_missing_plist_fails_closed(self):
        tcc = _load_module("tcc_darwin_missing", TASK_CADENCE_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            expected = {
                "com.aesop.watchdog": 300,
                "com.aesop.selfheal": 60,
            }
            self._write_plist(tmp, "com.aesop.watchdog", 300)

            report = tcc.check_launchd_cadence(plist_dir=tmp, expected=expected)
            self.assertEqual(report["exit_code"], 1)
            by_name = {t["task"]: t for t in report["tasks"]}
            self.assertEqual(by_name["com.aesop.selfheal"]["status"], "MISSING")

    def test_cli_darwin_mode_with_launchd_dir(self):
        tcc = _load_module("tcc_darwin_cli", TASK_CADENCE_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "LaunchAgents"
            dest_dir.mkdir()
            # Install real plists using install_launchd.py, then verify via run_cli
            install_proc = _run_installer(
                [
                    "--install",
                    "--dest-dir",
                    str(dest_dir),
                    "--aesop-root",
                    str(REPO_ROOT),
                    "--home",
                    str(tmp),
                    "--no-launchctl",
                ]
            )
            self.assertEqual(install_proc.returncode, 0, install_proc.stderr)

            code, out = tcc.run_cli(
                ["--json", "--root", str(REPO_ROOT), "--launchd-dir", str(dest_dir)],
                platform="darwin",
            )
            self.assertEqual(code, 0, out)
            payload = json.loads(out)
            self.assertEqual(payload["status"], "OK")

            # Mutate one plist StartInterval -> CLI must fail with exit 1
            mon_plist = dest_dir / "com.aesop.monitor.plist"
            data = plistlib.loads(mon_plist.read_bytes())
            data["StartInterval"] = 3600
            mon_plist.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))

            code_bad, out_bad = tcc.run_cli(
                ["--json", "--root", str(REPO_ROOT), "--launchd-dir", str(dest_dir)],
                platform="darwin",
            )
            self.assertEqual(code_bad, 1, out_bad)
            payload_bad = json.loads(out_bad)
            self.assertEqual(payload_bad["status"], "DRIFT")


if __name__ == "__main__":
    unittest.main()
