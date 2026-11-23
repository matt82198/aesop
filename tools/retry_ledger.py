#!/usr/bin/env python3
"""Mechanical SQLite WAL retry ledger for deterministic task retry caps.

INDEX: Mechanical SQLite WAL retry ledger (start, heartbeat, fail, status, stale) with sticky BLOCKED cap and CLI subcommands.

State machine per task:
  - start(task) sets RUNNING and updates last_heartbeat; if already BLOCKED
    (or failures >= max_retries), keeps BLOCKED sticky so the retry cap cannot
    be bypassed.
  - heartbeat(task) updates last_heartbeat (raises KeyError on unknown task).
  - fail(task, reason) increments failures and sets RETRY when
    failures < max_retries, else BLOCKED when failures >= max_retries.
  - status(task) returns 'RUNNING' | 'RETRY' | 'BLOCKED' (raises KeyError on
    unknown task).
  - stale(threshold_s) returns a deterministic sorted list of active
    non-BLOCKED tasks whose (now - last_heartbeat) >= threshold_s (raises
    ValueError if threshold_s < 0).

Database location:
  <state_dir>/retry_ledger.db with precedence:
  explicit state_root > AESOP_STATE_ROOT env > aesop.config.json state_root
  > AESOP_ROOT/state > common.get_state_dir() (./state).
"""

import argparse
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Callable, Dict, List, Optional

try:
    from tools import common
except ImportError:
    _tools_dir = str(Path(__file__).resolve().parent)
    if _tools_dir not in sys.path:
        sys.path.insert(0, _tools_dir)
    import common

DB_FILENAME = "retry_ledger.db"
BUSY_TIMEOUT_MS = 5000

STATE_RUNNING = "RUNNING"
STATE_RETRY = "RETRY"
STATE_BLOCKED = "BLOCKED"
VALID_STATES = (STATE_RUNNING, STATE_RETRY, STATE_BLOCKED)

_SCHEMA_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    task TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('RUNNING', 'RETRY', 'BLOCKED')),
    failures INTEGER NOT NULL DEFAULT 0,
    last_reason TEXT,
    started_at REAL NOT NULL,
    last_heartbeat REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""

_SCHEMA_EVENTS = """
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    action TEXT NOT NULL,
    state TEXT NOT NULL,
    failures INTEGER NOT NULL,
    reason TEXT,
    ts REAL NOT NULL
)
"""


def _read_config_state_root() -> Optional[Path]:
    """Read state_root from aesop.config.json in AESOP_ROOT or cwd if configured."""
    aesop_root = os.environ.get("AESOP_ROOT")
    base = Path(aesop_root).expanduser() if aesop_root else Path.cwd()
    cfg_path = base / "aesop.config.json"
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("state_root")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else base / candidate


def resolve_state_dir(state_root=None) -> Path:
    """Resolve state directory respecting override, env, config, and cwd fallback."""
    if state_root is not None:
        if not isinstance(state_root, (str, Path)):
            raise ValueError("state_root must be a string or Path")
        if not str(state_root).strip():
            raise ValueError("state_root must not be empty")
        return Path(state_root).expanduser()

    env_state = os.environ.get("AESOP_STATE_ROOT")
    if env_state and env_state.strip():
        return Path(env_state).expanduser()

    configured = _read_config_state_root()
    if configured is not None:
        return configured

    aesop_root = os.environ.get("AESOP_ROOT")
    if aesop_root and aesop_root.strip():
        return Path(aesop_root).expanduser() / "state"

    return Path(common.get_state_dir())


def _validate_task(task: str) -> str:
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    return task


def _validate_max_retries(max_retries: int) -> int:
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 1:
        raise ValueError("max_retries must be an integer >= 1")
    return max_retries


def _validate_timestamp(value, label: str = "timestamp") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    ts = float(value)
    if math.isnan(ts) or math.isinf(ts):
        raise ValueError(f"{label} must be a finite number")
    return ts


class RetryLedger:
    """Deterministic SQLite WAL mechanical retry ledger."""

    def __init__(
        self,
        state_root=None,
        max_retries: int = 3,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Initialize a retry ledger bound to state_root, max_retries, and clock."""
        self.max_retries = _validate_max_retries(max_retries)
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.clock = clock
        self.state_dir = resolve_state_dir(state_root)
        self.db_path = self.state_dir / DB_FILENAME

    def __enter__(self) -> "RetryLedger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        """No-op close hook; connections are scoped and closed per operation."""
        return None

    def _resolve_now(self, now=None) -> float:
        raw = self.clock() if now is None else now
        return _validate_timestamp(raw, "now")

    def _connect(self, *, create: bool) -> sqlite3.Connection:
        if create:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=BUSY_TIMEOUT_MS / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            if create:
                cur = conn.execute("PRAGMA journal_mode").fetchone()
                mode = str(cur[0]).lower() if cur else ""
                if mode != "wal":
                    conn.execute("PRAGMA journal_mode=WAL")
            return conn
        except BaseException:
            conn.close()
            raise

    def _effective_state(self, stored_state: str, failures: int) -> str:
        if stored_state == STATE_BLOCKED or failures >= self.max_retries:
            return STATE_BLOCKED
        return stored_state

    @staticmethod
    def _record_dict(
        task: str,
        state: str,
        failures: int,
        reason: Optional[str],
        last_hb: float,
    ) -> Dict[str, object]:
        return {
            "task": task,
            "status": state,
            "failures": int(failures),
            "reason": reason,
            "last_heartbeat": float(last_hb),
        }

    def start(self, task: str, *, now=None) -> Dict[str, object]:
        """Set task to RUNNING and update last_heartbeat unless sticky BLOCKED."""
        _validate_task(task)
        ts = self._resolve_now(now)
        with closing(self._connect(create=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(_SCHEMA_TASKS)
                conn.execute(_SCHEMA_EVENTS)
                row = conn.execute(
                    "SELECT state, failures, last_reason, last_heartbeat "
                    "FROM tasks WHERE task = ?",
                    (task,),
                ).fetchone()

                if row is None:
                    new_state = STATE_RUNNING
                    failures = 0
                    reason = None
                    last_hb = ts
                    conn.execute(
                        "INSERT INTO tasks "
                        "(task, state, failures, last_reason, started_at, last_heartbeat, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (task, new_state, failures, reason, ts, last_hb, ts),
                    )
                elif row["state"] == STATE_BLOCKED or int(row["failures"]) >= self.max_retries:
                    new_state = STATE_BLOCKED
                    failures = int(row["failures"])
                    reason = row["last_reason"]
                    last_hb = float(row["last_heartbeat"])
                    if row["state"] != STATE_BLOCKED:
                        conn.execute(
                            "UPDATE tasks SET state = ?, updated_at = ? WHERE task = ?",
                            (STATE_BLOCKED, ts, task),
                        )
                else:
                    new_state = STATE_RUNNING
                    failures = int(row["failures"])
                    reason = row["last_reason"]
                    last_hb = ts
                    conn.execute(
                        "UPDATE tasks SET state = ?, started_at = ?, "
                        "last_heartbeat = ?, updated_at = ? WHERE task = ?",
                        (new_state, ts, last_hb, ts, task),
                    )

                conn.execute(
                    "INSERT INTO task_events (task, action, state, failures, reason, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (task, "start", new_state, failures, reason, ts),
                )
                conn.execute("COMMIT")
                return self._record_dict(task, new_state, failures, reason, last_hb)
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def heartbeat(self, task: str, *, now=None) -> Dict[str, object]:
        """Update last_heartbeat for an existing task; raise KeyError if unknown."""
        _validate_task(task)
        ts = self._resolve_now(now)
        if not self.db_path.is_file():
            raise KeyError(task)

        with closing(self._connect(create=False)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(_SCHEMA_TASKS)
                conn.execute(_SCHEMA_EVENTS)
                row = conn.execute(
                    "SELECT state, failures, last_reason FROM tasks WHERE task = ?",
                    (task,),
                ).fetchone()
                if row is None:
                    raise KeyError(task)

                failures = int(row["failures"])
                eff_state = self._effective_state(str(row["state"]), failures)
                reason = row["last_reason"]
                conn.execute(
                    "UPDATE tasks SET state = ?, last_heartbeat = ?, updated_at = ? "
                    "WHERE task = ?",
                    (eff_state, ts, ts, task),
                )
                conn.execute(
                    "INSERT INTO task_events (task, action, state, failures, reason, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (task, "heartbeat", eff_state, failures, reason, ts),
                )
                conn.execute("COMMIT")
                return self._record_dict(task, eff_state, failures, reason, ts)
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def fail(self, task: str, reason: str, *, now=None) -> Dict[str, object]:
        """Increment failures and transition to RETRY or BLOCKED at max_retries."""
        _validate_task(task)
        if not isinstance(reason, str):
            raise ValueError("reason must be a string")
        ts = self._resolve_now(now)

        with closing(self._connect(create=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(_SCHEMA_TASKS)
                conn.execute(_SCHEMA_EVENTS)
                row = conn.execute(
                    "SELECT state, failures, started_at FROM tasks WHERE task = ?",
                    (task,),
                ).fetchone()

                if row is None:
                    failures = 1
                    was_blocked = False
                    started_at = ts
                else:
                    failures = int(row["failures"]) + 1
                    was_blocked = row["state"] == STATE_BLOCKED
                    started_at = float(row["started_at"])

                if was_blocked or failures >= self.max_retries:
                    new_state = STATE_BLOCKED
                else:
                    new_state = STATE_RETRY

                if row is None:
                    conn.execute(
                        "INSERT INTO tasks "
                        "(task, state, failures, last_reason, started_at, last_heartbeat, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (task, new_state, failures, reason, started_at, ts, ts),
                    )
                else:
                    conn.execute(
                        "UPDATE tasks SET state = ?, failures = ?, last_reason = ?, "
                        "last_heartbeat = ?, updated_at = ? WHERE task = ?",
                        (new_state, failures, reason, ts, ts, task),
                    )

                conn.execute(
                    "INSERT INTO task_events (task, action, state, failures, reason, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (task, "fail", new_state, failures, reason, ts),
                )
                conn.execute("COMMIT")
                return self._record_dict(task, new_state, failures, reason, ts)
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def get_task(self, task: str) -> Dict[str, object]:
        """Return task metadata dict; raise KeyError on unknown task."""
        _validate_task(task)
        if not self.db_path.is_file():
            raise KeyError(task)

        with closing(self._connect(create=False)) as conn:
            try:
                row = conn.execute(
                    "SELECT state, failures, last_reason, last_heartbeat "
                    "FROM tasks WHERE task = ?",
                    (task,),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    raise KeyError(task) from None
                raise

        if row is None:
            raise KeyError(task)

        failures = int(row["failures"])
        eff_state = self._effective_state(str(row["state"]), failures)
        return self._record_dict(
            task,
            eff_state,
            failures,
            row["last_reason"],
            float(row["last_heartbeat"]),
        )

    def status(self, task: str) -> str:
        """Return 'RUNNING', 'RETRY', or 'BLOCKED'; raise KeyError if unknown."""
        return str(self.get_task(task)["status"])

    def stale(self, threshold_s: float, *, now=None) -> List[str]:
        """Return sorted list of active non-BLOCKED tasks with age >= threshold_s."""
        thresh = _validate_timestamp(threshold_s, "threshold_s")
        if thresh < 0:
            raise ValueError("threshold_s must be >= 0")
        ts = self._resolve_now(now)
        if not self.db_path.is_file():
            return []

        with closing(self._connect(create=False)) as conn:
            try:
                rows = conn.execute(
                    "SELECT task, state, failures, last_heartbeat "
                    "FROM tasks WHERE state != ? ORDER BY task ASC",
                    (STATE_BLOCKED,),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    return []
                raise

        stale_tasks: List[str] = []
        for row in rows:
            failures = int(row["failures"])
            if self._effective_state(str(row["state"]), failures) == STATE_BLOCKED:
                continue
            if (ts - float(row["last_heartbeat"])) >= thresh:
                stale_tasks.append(str(row["task"]))
        return stale_tasks


def start(task: str, *, state_root=None, max_retries: int = 3, now=None) -> Dict[str, object]:
    """Start or restart a task in RUNNING unless already sticky BLOCKED."""
    return RetryLedger(state_root=state_root, max_retries=max_retries).start(
        task, now=now
    )


def heartbeat(task: str, *, state_root=None, now=None) -> Dict[str, object]:
    """Record a fresh heartbeat for an existing task; raise KeyError if unknown."""
    return RetryLedger(state_root=state_root).heartbeat(task, now=now)


def fail(
    task: str,
    reason: str,
    *,
    state_root=None,
    max_retries: int = 3,
    now=None,
) -> Dict[str, object]:
    """Increment task failure count and set RETRY or BLOCKED."""
    return RetryLedger(state_root=state_root, max_retries=max_retries).fail(
        task, reason, now=now
    )


def status(task: str, *, state_root=None, max_retries: int = 3) -> str:
    """Return 'RUNNING', 'RETRY', or 'BLOCKED' for task; raise KeyError if unknown."""
    return RetryLedger(state_root=state_root, max_retries=max_retries).status(task)


def stale(threshold_s: float, *, state_root=None, now=None) -> List[str]:
    """Return sorted active non-BLOCKED tasks whose heartbeat age >= threshold_s."""
    return RetryLedger(state_root=state_root).stale(threshold_s, now=now)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for retry_ledger subcommands."""
    sub_shared = argparse.ArgumentParser(add_help=False)
    sub_shared.add_argument(
        "--state-root",
        default=argparse.SUPPRESS,
        help="Override state directory containing retry_ledger.db",
    )
    sub_shared.add_argument(
        "--max-retries",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum failures before BLOCKED (default: 3)",
    )
    sub_shared.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit JSON output",
    )

    parser = argparse.ArgumentParser(
        prog="retry_ledger.py",
        description="Deterministic SQLite WAL mechanical retry ledger",
    )
    parser.add_argument(
        "--state-root",
        default=None,
        help="Override state directory containing retry_ledger.db",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum failures before BLOCKED (default: 3)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", parents=[sub_shared], help="Set task RUNNING")
    p_start.add_argument("task", help="Task identifier")

    p_hb = sub.add_parser(
        "heartbeat", parents=[sub_shared], help="Update task last_heartbeat"
    )
    p_hb.add_argument("task", help="Task identifier")

    p_fail = sub.add_parser(
        "fail", parents=[sub_shared], help="Increment failures and set RETRY or BLOCKED"
    )
    p_fail.add_argument("task", help="Task identifier")
    p_fail.add_argument(
        "reason_pos",
        nargs="?",
        default=None,
        metavar="REASON",
        help="Failure reason (positional)",
    )
    p_fail.add_argument(
        "--reason",
        dest="reason_opt",
        default=None,
        help="Failure reason",
    )

    p_status = sub.add_parser(
        "status", parents=[sub_shared], help="Print RUNNING, RETRY, or BLOCKED"
    )
    p_status.add_argument("task", help="Task identifier")

    p_stale = sub.add_parser(
        "stale",
        parents=[sub_shared],
        help="List active non-BLOCKED tasks with stale heartbeats",
    )
    p_stale.add_argument(
        "threshold_pos",
        nargs="?",
        type=float,
        default=None,
        metavar="THRESHOLD_S",
        help="Staleness threshold in seconds (positional)",
    )
    p_stale.add_argument(
        "--threshold-s",
        dest="threshold_opt",
        type=float,
        default=None,
        help="Staleness threshold in seconds",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for retry_ledger."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        ledger = RetryLedger(
            state_root=args.state_root,
            max_retries=args.max_retries,
        )
    except ValueError as exc:
        print(f"retry_ledger: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "start":
            rec = ledger.start(args.task)
            if args.json:
                print(json.dumps(rec, sort_keys=True))
            else:
                print(rec["status"])
            return 0

        if args.command == "heartbeat":
            rec = ledger.heartbeat(args.task)
            if args.json:
                print(json.dumps(rec, sort_keys=True))
            else:
                print(rec["status"])
            return 0

        if args.command == "fail":
            if args.reason_pos is not None and args.reason_opt is not None:
                print(
                    "retry_ledger: specify reason positionally or via --reason, not both",
                    file=sys.stderr,
                )
                return 2
            reason = args.reason_opt if args.reason_opt is not None else args.reason_pos
            if reason is None:
                print(
                    "retry_ledger: fail requires a reason (positional or --reason)",
                    file=sys.stderr,
                )
                return 2
            rec = ledger.fail(args.task, reason)
            if args.json:
                print(json.dumps(rec, sort_keys=True))
            else:
                print(rec["status"])
            return 0

        if args.command == "status":
            rec = ledger.get_task(args.task)
            if args.json:
                print(json.dumps(rec, sort_keys=True))
            else:
                print(rec["status"])
            return 0

        if args.command == "stale":
            if args.threshold_pos is not None and args.threshold_opt is not None:
                print(
                    "retry_ledger: specify threshold positionally or via --threshold-s, not both",
                    file=sys.stderr,
                )
                return 2
            threshold_s = (
                args.threshold_opt
                if args.threshold_opt is not None
                else args.threshold_pos
            )
            if threshold_s is None:
                print(
                    "retry_ledger: stale requires threshold_s (positional or --threshold-s)",
                    file=sys.stderr,
                )
                return 2
            tasks = ledger.stale(threshold_s)
            if args.json:
                print(
                    json.dumps(
                        {
                            "count": len(tasks),
                            "stale": tasks,
                            "threshold_s": float(threshold_s),
                        },
                        sort_keys=True,
                    )
                )
            else:
                for item in tasks:
                    print(item)
            return 0

    except KeyError as exc:
        missing = exc.args[0] if exc.args else str(exc)
        print(f"retry_ledger: unknown task: {missing}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"retry_ledger: {exc}", file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    sys.exit(main())
