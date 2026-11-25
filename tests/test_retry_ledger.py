"""Unit and CLI integration tests for tools/retry_ledger.py."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tools import retry_ledger

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "tools" / "retry_ledger.py"


class FakeClock:
    """Deterministic manual clock for unit tests (zero wall-clock sleeps)."""

    def __init__(self, initial: float = 1000.0) -> None:
        self.current = float(initial)

    def __call__(self) -> float:
        return self.current

    def advance(self, delta_s: float) -> float:
        self.current += float(delta_s)
        return self.current


class TestRetryLedgerLifecycle(unittest.TestCase):
    """Core state machine, SQLite WAL persistence, and boundary tests."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / "state"
        self.clock = FakeClock(1000.0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_start_sets_running_and_creates_wal_db(self) -> None:
        ledger = retry_ledger.RetryLedger(
            state_root=self.state_dir, max_retries=3, clock=self.clock
        )
        ledger.start("task-alpha")
        self.assertEqual(ledger.status("task-alpha"), "RUNNING")

        db_file = self.state_dir / "retry_ledger.db"
        self.assertTrue(db_file.is_file())
        with closing(sqlite3.connect(str(db_file))) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(str(mode).lower(), "wal")

    def test_fail_increments_and_caps_at_default_max_retries(self) -> None:
        ledger = retry_ledger.RetryLedger(
            state_root=self.state_dir, clock=self.clock
        )
        ledger.start("lane-1")
        self.assertEqual(ledger.status("lane-1"), "RUNNING")

        self.clock.advance(5.0)
        ledger.fail("lane-1", "first failure")
        self.assertEqual(ledger.status("lane-1"), "RETRY")

        self.clock.advance(5.0)
        ledger.start("lane-1")
        self.assertEqual(ledger.status("lane-1"), "RUNNING")

        self.clock.advance(5.0)
        ledger.fail("lane-1", "second failure")
        self.assertEqual(ledger.status("lane-1"), "RETRY")

        self.clock.advance(5.0)
        ledger.start("lane-1")
        self.assertEqual(ledger.status("lane-1"), "RUNNING")

        self.clock.advance(5.0)
        ledger.fail("lane-1", "third failure")
        self.assertEqual(ledger.status("lane-1"), "BLOCKED")

    def test_blocked_is_sticky_against_start_heartbeat_and_cap_raise(self) -> None:
        ledger = retry_ledger.RetryLedger(
            state_root=self.state_dir, max_retries=2, clock=self.clock
        )
        ledger.start("sticky-task")
        ledger.fail("sticky-task", "err-1")
        self.assertEqual(ledger.status("sticky-task"), "RETRY")
        ledger.fail("sticky-task", "err-2")
        self.assertEqual(ledger.status("sticky-task"), "BLOCKED")

        # Calling start() on an already-BLOCKED task must NOT reset or bypass cap.
        self.clock.advance(10.0)
        ledger.start("sticky-task")
        self.assertEqual(ledger.status("sticky-task"), "BLOCKED")

        # Heartbeat on BLOCKED keeps BLOCKED.
        self.clock.advance(10.0)
        ledger.heartbeat("sticky-task")
        self.assertEqual(ledger.status("sticky-task"), "BLOCKED")

        # Constructing a new ledger with higher max_retries still sees sticky BLOCKED.
        higher = retry_ledger.RetryLedger(
            state_root=self.state_dir, max_retries=10, clock=self.clock
        )
        self.assertEqual(higher.status("sticky-task"), "BLOCKED")
        higher.start("sticky-task")
        self.assertEqual(higher.status("sticky-task"), "BLOCKED")

    def test_custom_max_retries_one_and_four(self) -> None:
        one_shot = retry_ledger.RetryLedger(
            state_root=self.state_dir, max_retries=1, clock=self.clock
        )
        one_shot.start("t-one")
        one_shot.fail("t-one", "boom")
        self.assertEqual(one_shot.status("t-one"), "BLOCKED")

        four_shot = retry_ledger.RetryLedger(
            state_root=self.state_dir, max_retries=4, clock=self.clock
        )
        four_shot.start("t-four")
        for idx in range(3):
            four_shot.fail("t-four", f"transient-{idx}")
            self.assertEqual(four_shot.status("t-four"), "RETRY")
            four_shot.start("t-four")
        four_shot.fail("t-four", "final-fail")
        self.assertEqual(four_shot.status("t-four"), "BLOCKED")

    def test_heartbeat_updates_last_heartbeat_and_unknown_raises_keyerror(self) -> None:
        ledger = retry_ledger.RetryLedger(
            state_root=self.state_dir, clock=self.clock
        )
        with self.assertRaises(KeyError):
            ledger.heartbeat("unknown-task")

        ledger.start("hb-task")
        self.clock.advance(50.0)
        ledger.heartbeat("hb-task")

        # 40s after heartbeat (90s after initial start): threshold 60s is NOT stale.
        self.clock.advance(40.0)
        self.assertEqual(ledger.stale(60.0), [])

        # 20s more (60s since heartbeat): exact boundary (now - last_heartbeat) == 60 -> stale.
        self.clock.advance(20.0)
        self.assertEqual(ledger.stale(60.0), ["hb-task"])

    def test_status_unknown_task_raises_keyerror(self) -> None:
        ledger = retry_ledger.RetryLedger(
            state_root=self.state_dir, clock=self.clock
        )
        with self.assertRaises(KeyError):
            ledger.status("missing-on-empty")

        ledger.start("present-task")
        with self.assertRaises(KeyError):
            ledger.status("missing-on-existing-db")

    def test_stale_deterministic_sort_boundaries_and_excludes_blocked(self) -> None:
        ledger = retry_ledger.RetryLedger(
            state_root=self.state_dir, max_retries=3, clock=self.clock
        )
        # t=1000: start z and a
        ledger.start("task-z")
        ledger.start("task-a")

        # t=1000: block one task so its heartbeat is old, yet it is BLOCKED
        ledger.start("task-blocked")
        ledger.fail("task-blocked", "e1")
        ledger.fail("task-blocked", "e2")
        ledger.fail("task-blocked", "e3")
        self.assertEqual(ledger.status("task-blocked"), "BLOCKED")

        # t=1000: put one task into RETRY
        ledger.start("task-retry")
        ledger.fail("task-retry", "e1")
        self.assertEqual(ledger.status("task-retry"), "RETRY")

        # t=1010: start task-m
        self.clock.advance(10.0)
        ledger.start("task-m")

        # t=1030: age of z, a, retry is 30.0; age of m is 20.0; blocked is excluded
        self.clock.advance(20.0)
        self.assertEqual(ledger.stale(30.0), ["task-a", "task-retry", "task-z"])
        self.assertEqual(ledger.stale(30.01), [])
        self.assertEqual(
            ledger.stale(0.0), ["task-a", "task-m", "task-retry", "task-z"]
        )

    def test_stale_negative_threshold_raises_value_error(self) -> None:
        ledger = retry_ledger.RetryLedger(
            state_root=self.state_dir, clock=self.clock
        )
        with self.assertRaises(ValueError):
            ledger.stale(-0.1)

        ledger.start("t1")
        with self.assertRaises(ValueError):
            ledger.stale(-1)

    def test_invalid_arguments_raise_value_error(self) -> None:
        for bad_max in (0, -3, True):
            with self.assertRaises(ValueError):
                retry_ledger.RetryLedger(
                    state_root=self.state_dir, max_retries=bad_max
                )

        ledger = retry_ledger.RetryLedger(
            state_root=self.state_dir, clock=self.clock
        )
        for bad_task in ("", "   "):
            with self.assertRaises(ValueError):
                ledger.start(bad_task)
            with self.assertRaises(ValueError):
                ledger.fail(bad_task, "reason")


class TestModuleFunctionsAndConcurrency(unittest.TestCase):
    """Module-level convenience functions, explicit now= overrides, and WAL concurrency."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / "state"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_module_functions_with_now_override(self) -> None:
        retry_ledger.start("mod-task", state_root=self.state_dir, max_retries=2, now=100.0)
        self.assertEqual(
            retry_ledger.status("mod-task", state_root=self.state_dir, max_retries=2),
            "RUNNING",
        )
        self.assertEqual(
            retry_ledger.stale(10.0, state_root=self.state_dir, now=109.9), []
        )
        self.assertEqual(
            retry_ledger.stale(10.0, state_root=self.state_dir, now=110.0),
            ["mod-task"],
        )

        retry_ledger.heartbeat("mod-task", state_root=self.state_dir, now=108.0)
        self.assertEqual(
            retry_ledger.stale(10.0, state_root=self.state_dir, now=115.0), []
        )

        retry_ledger.fail(
            "mod-task", "crash-1", state_root=self.state_dir, max_retries=2, now=120.0
        )
        self.assertEqual(
            retry_ledger.status("mod-task", state_root=self.state_dir, max_retries=2),
            "RETRY",
        )

        retry_ledger.fail(
            "mod-task", "crash-2", state_root=self.state_dir, max_retries=2, now=125.0
        )
        self.assertEqual(
            retry_ledger.status("mod-task", state_root=self.state_dir, max_retries=2),
            "BLOCKED",
        )
        self.assertEqual(
            retry_ledger.stale(0.0, state_root=self.state_dir, now=999.0), []
        )

    def test_concurrent_writers_serialize_atomically_under_wal(self) -> None:
        retry_ledger.start(
            "contended", state_root=self.state_dir, max_retries=6, now=10.0
        )
        workers = 6
        barrier = threading.Barrier(workers)
        errors = []

        def worker(idx: int) -> None:
            try:
                barrier.wait(timeout=10.0)
                retry_ledger.fail(
                    "contended",
                    f"worker-{idx}",
                    state_root=self.state_dir,
                    max_retries=6,
                    now=20.0 + idx,
                )
            except BaseException as exc:  # pragma: no cover - failure collector
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        self.assertEqual(errors, [])
        # Exactly 6 failures recorded -> hits max_retries=6 -> BLOCKED, whereas max_retries=7 would have been RETRY if not sticky
        self.assertEqual(
            retry_ledger.status("contended", state_root=self.state_dir, max_retries=6),
            "BLOCKED",
        )


class TestStateDirPrecedence(unittest.TestCase):
    """State root precedence: explicit > AESOP_STATE_ROOT > config > AESOP_ROOT/state > ./state."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_precedence_chain(self) -> None:
        explicit_dir = self.base / "explicit_state"
        env_state_dir = self.base / "env_state"
        aesop_root = self.base / "fake_aesop_root"
        aesop_root.mkdir(parents=True, exist_ok=True)
        config_rel_target = aesop_root / "custom_cfg_state"

        (aesop_root / "aesop.config.json").write_text(
            json.dumps({"state_root": "custom_cfg_state"}), encoding="utf-8"
        )

        # 1. Explicit state_root wins over everything.
        with mock.patch.dict(
            os.environ,
            {
                "AESOP_STATE_ROOT": str(env_state_dir),
                "AESOP_ROOT": str(aesop_root),
            },
            clear=False,
        ):
            retry_ledger.start("t-explicit", state_root=explicit_dir, now=1.0)
            self.assertTrue((explicit_dir / "retry_ledger.db").is_file())
            self.assertFalse((env_state_dir / "retry_ledger.db").exists())

        # 2. AESOP_STATE_ROOT beats aesop.config.json and AESOP_ROOT/state.
        with mock.patch.dict(
            os.environ,
            {
                "AESOP_STATE_ROOT": str(env_state_dir),
                "AESOP_ROOT": str(aesop_root),
            },
            clear=False,
        ):
            retry_ledger.start("t-env", now=1.0)
            self.assertTrue((env_state_dir / "retry_ledger.db").is_file())
            self.assertFalse((config_rel_target / "retry_ledger.db").exists())

        # 3. aesop.config.json state_root beats AESOP_ROOT/state when AESOP_STATE_ROOT unset.
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("AESOP_STATE_ROOT", "AESOP_ROOT")
        }
        clean_env["AESOP_ROOT"] = str(aesop_root)
        with mock.patch.dict(os.environ, clean_env, clear=True):
            retry_ledger.start("t-cfg", now=1.0)
            self.assertTrue((config_rel_target / "retry_ledger.db").is_file())
            self.assertFalse((aesop_root / "state" / "retry_ledger.db").exists())

        # 4. AESOP_ROOT/state used when config has no state_root and AESOP_STATE_ROOT unset.
        (aesop_root / "aesop.config.json").write_text(
            json.dumps({"repos": []}), encoding="utf-8"
        )
        with mock.patch.dict(os.environ, clean_env, clear=True):
            retry_ledger.start("t-aesop-root", now=1.0)
            self.assertTrue((aesop_root / "state" / "retry_ledger.db").is_file())


class TestRetryLedgerCLI(unittest.TestCase):
    """Subprocess CLI tests covering all subcommands, option forms, and exit codes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work_dir = Path(self._tmp.name)
        self.state_dir = self.work_dir / "cli_state"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_cli(self, args, cwd=None, env_overrides=None):
        run_cwd = Path(cwd) if cwd is not None else self.work_dir
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("AESOP_STATE_ROOT", "AESOP_ROOT")
        }
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            cwd=str(run_cwd),
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def test_cli_full_lifecycle_positional_and_flag_variants(self) -> None:
        sr = str(self.state_dir)

        # start with --state-root before subcommand
        res = self._run_cli(["--state-root", sr, "start", "job-1"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "RUNNING")

        # status with --json after subcommand
        res = self._run_cli(["status", "job-1", "--state-root", sr, "--json"])
        self.assertEqual(res.returncode, 0, res.stderr)
        payload = json.loads(res.stdout)
        self.assertEqual(payload["task"], "job-1")
        self.assertEqual(payload["status"], "RUNNING")
        self.assertEqual(payload["failures"], 0)

        # heartbeat
        res = self._run_cli(["heartbeat", "job-1", "--state-root", sr])
        self.assertEqual(res.returncode, 0, res.stderr)

        # fail with positional reason and --max-retries 2
        res = self._run_cli(
            ["fail", "job-1", "network reset", "--state-root", sr, "--max-retries", "2"]
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "RETRY")

        # stale with positional threshold 0 lists active non-BLOCKED job-1
        res = self._run_cli(["stale", "0", "--state-root", sr])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip().splitlines(), ["job-1"])

        # fail with --reason flag hits max_retries=2 -> BLOCKED
        res = self._run_cli(
            [
                "--state-root",
                sr,
                "--max-retries",
                "2",
                "--json",
                "fail",
                "job-1",
                "--reason",
                "second crash",
            ]
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        fail_json = json.loads(res.stdout)
        self.assertEqual(fail_json["status"], "BLOCKED")
        self.assertEqual(fail_json["failures"], 2)
        self.assertEqual(fail_json["reason"], "second crash")

        # status plain text confirms BLOCKED
        res = self._run_cli(["status", "job-1", "--state-root", sr])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "BLOCKED")

        # stale with --threshold-s 0 --json excludes BLOCKED task
        res = self._run_cli(
            ["stale", "--threshold-s", "0", "--state-root", sr, "--json"]
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        stale_json = json.loads(res.stdout)
        self.assertEqual(stale_json["stale"], [])

    def test_cli_default_cwd_state_fallback_and_error_exits(self) -> None:
        # With AESOP_STATE_ROOT and AESOP_ROOT unset and no --state-root, uses <cwd>/state
        isolated_cwd = self.work_dir / "fresh_cwd"
        isolated_cwd.mkdir(parents=True, exist_ok=True)
        res = self._run_cli(["start", "cwd-task"], cwd=isolated_cwd)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((isolated_cwd / "state" / "retry_ledger.db").is_file())

        # Unknown task on status and heartbeat exits 1
        res_status = self._run_cli(["status", "no-such-task"], cwd=isolated_cwd)
        self.assertNotEqual(res_status.returncode, 0)

        res_hb = self._run_cli(["heartbeat", "no-such-task"], cwd=isolated_cwd)
        self.assertNotEqual(res_hb.returncode, 0)

        # Negative threshold exits non-zero
        res_neg = self._run_cli(
            ["stale", "--threshold-s", "-5"], cwd=isolated_cwd
        )
        self.assertNotEqual(res_neg.returncode, 0)


if __name__ == "__main__":
    unittest.main()
