#!/usr/bin/env python3
"""Record and print the orchestrator's current GOAL as event-store events.

INDEX: Orchestrator GOAL recorder: set/show/history over an append-only `orchestrator_goal` stream in the state_store tracker DB (StateAPI append path, WAL, no file copies).

Usage:
    python tools/goal_state.py set --goal "<one line>" [--phase P] [--next-action A]
                                   [--ref R ...] [--session ID] [--state-dir DIR]
    python tools/goal_state.py show [--state-dir DIR]
    python tools/goal_state.py history [--limit N] [--state-dir DIR]

Store location: --state-dir, else $AESOP_STATE_ROOT, else <repo>/state.
The DB is state_store.paths.resolve_db_path(state_dir, "tracker").

Behavior:
    - set appends ONE event (type goal_set, stream orchestrator_goal) per call. Events are
      never patched or deleted; setting the same goal twice appends two events.
    - show prints the projection of the latest goal event (project_goal). With no goal event
      it prints the literal line "GOAL: none recorded" and exits 0.
    - history prints the last N goal events (default 10), oldest of those first.
    - A read never creates a store: a missing or unreadable DB exits 2 with one stderr line.

Exit codes: 0 ok; 1 invalid input (nothing appended); 2 store missing/unreadable or usage error.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from state_store.api import StateAPI  # noqa: E402
from state_store.paths import resolve_db_path  # noqa: E402

GOAL_STREAM = "orchestrator_goal"
GOAL_EVENT = "goal_set"
NONE_LINE = "GOAL: none recorded"
DEFAULT_HISTORY_LIMIT = 10


class StoreUnreadable(RuntimeError):
    """The goal store is missing or cannot be read (fail closed)."""


class InvalidGoal(ValueError):
    """Goal input failed validation; nothing is appended."""


def default_state_dir() -> Path:
    """AESOP_STATE_ROOT if set, else <repo>/state (same rule as ingest_task_events)."""
    env = os.environ.get("AESOP_STATE_ROOT")
    return Path(env) if env else REPO_ROOT / "state"


def _clean_line(value: str, field: str) -> str:
    text = (value or "").strip()
    if "\n" in text or "\r" in text:
        raise InvalidGoal(f"{field} must be a single line")
    return text


def _clean_refs(refs) -> list[str]:
    out: list[str] = []
    for ref in refs or ():
        text = _clean_line(ref, "ref")
        if text:
            out.append(text)
    return out


def record_goal(
    api: StateAPI,
    goal: str,
    *,
    phase: str = "",
    next_action: str = "",
    refs=(),
    session_id: str | None = None,
    set_at: str | None = None,
    actor: str = "orchestrator",
) -> int:
    """Append one goal_set event through ``api``; return its per-stream version."""
    goal_text = _clean_line(goal, "goal")
    if not goal_text:
        raise InvalidGoal("goal must not be empty")
    payload = {
        "goal": goal_text,
        "phase": _clean_line(phase, "phase"),
        "next_action": _clean_line(next_action, "next_action"),
        "refs": _clean_refs(refs),
        "set_at": set_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": (_clean_line(session_id, "session_id") or None) if session_id else None,
    }
    return api.append(GOAL_STREAM, GOAL_EVENT, payload, actor=actor)


def _goal_events(events: list) -> list:
    return [e for e in events if e.get("type") == GOAL_EVENT]


def project_goal(events: list) -> dict | None:
    """Return the projection of the latest goal event, or None if there is none."""
    goals = _goal_events(events)
    if not goals:
        return None
    latest = max(goals, key=lambda e: e.get("version", 0))
    payload = latest.get("payload") or {}
    return {
        "goal": payload.get("goal", ""),
        "phase": payload.get("phase", ""),
        "next_action": payload.get("next_action", ""),
        "refs": list(payload.get("refs") or []),
        "set_at": payload.get("set_at", ""),
        "session_id": payload.get("session_id"),
        "version": latest.get("version", 0),
    }


def goal_history(events: list, limit: int) -> list[dict]:
    """Return the last ``limit`` goal projections, oldest of those first."""
    if limit <= 0:
        return []
    ordered = sorted(_goal_events(events), key=lambda e: e.get("version", 0))
    tail = ordered[-limit:]
    rows = []
    for ev in tail:
        proj = project_goal([ev])
        if proj is not None:
            rows.append(proj)
    return rows


def render_goal_block(proj: dict | None) -> str:
    """Render the goal projection as a short markdown block (or the none line)."""
    if proj is None:
        return NONE_LINE + "\n"
    refs = ", ".join(proj["refs"]) if proj["refs"] else "-"
    lines = [
        f"GOAL: {proj['goal']}",
        f"- phase: {proj['phase'] or '-'}",
        f"- next_action: {proj['next_action'] or '-'}",
        f"- refs: {refs}",
        f"- set_at: {proj['set_at'] or '-'}",
        f"- session: {proj['session_id'] or '-'}",
        f"- version: {proj['version']}",
    ]
    return "\n".join(lines) + "\n"


def render_history(rows: list[dict]) -> str:
    """Render history rows one per line, oldest first."""
    if not rows:
        return NONE_LINE + "\n"
    out = []
    for row in rows:
        refs = ", ".join(row["refs"]) if row["refs"] else "-"
        out.append(
            f"- v{row['version']} {row['set_at'] or '-'} | {row['goal']} "
            f"| phase: {row['phase'] or '-'} | next: {row['next_action'] or '-'} "
            f"| refs: {refs} | session: {row['session_id'] or '-'}"
        )
    return "\n".join(out) + "\n"


def open_store(state_dir: Path, *, create: bool) -> StateAPI:
    """Open the tracker DB. Reads refuse to create a store (StoreUnreadable)."""
    db = resolve_db_path(state_dir, "tracker")
    if create:
        state_dir.mkdir(parents=True, exist_ok=True)
    elif not os.path.isfile(db) or os.path.getsize(db) == 0:
        raise StoreUnreadable(f"goal store missing or empty: {db}")
    try:
        return StateAPI(db)
    except Exception as exc:  # sqlite3.DatabaseError and friends
        raise StoreUnreadable(f"goal store unreadable: {db}: {exc}") from None


def _read_events(state_dir: Path) -> list:
    api = open_store(state_dir, create=False)
    try:
        return api.get(GOAL_STREAM)
    except Exception as exc:
        raise StoreUnreadable(f"goal store unreadable: {exc}") from None
    finally:
        api.close()


def _cmd_set(args, state_dir: Path) -> int:
    api = open_store(state_dir, create=True)
    try:
        version = record_goal(
            api, args.goal, phase=args.phase, next_action=args.next_action,
            refs=args.ref, session_id=args.session,
        )
    except InvalidGoal as exc:
        print(f"goal_state: {exc}", file=sys.stderr)
        return 1
    finally:
        api.close()
    print(f"goal_state: recorded goal version {version}")
    return 0


def _cmd_show(args, state_dir: Path) -> int:
    sys.stdout.write(render_goal_block(project_goal(_read_events(state_dir))))
    return 0


def _cmd_history(args, state_dir: Path) -> int:
    sys.stdout.write(render_history(goal_history(_read_events(state_dir), args.limit)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-dir", default=None,
                        help="state directory (default: AESOP_STATE_ROOT or <repo>/state)")
    parser = argparse.ArgumentParser(
        description="Record and print the orchestrator GOAL in the event store.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_set = sub.add_parser("set", parents=[common], help="append a goal_set event")
    p_set.add_argument("--goal", required=True, help="the goal, one line")
    p_set.add_argument("--phase", default="", help="current phase, one line")
    p_set.add_argument("--next-action", dest="next_action", default="", help="next action, one line")
    p_set.add_argument("--ref", action="append", default=[], help="bound ref (PR, run id, sha, lane); repeatable")
    p_set.add_argument("--session", default=None, help="session id, if any")
    p_set.set_defaults(func=_cmd_set)

    p_show = sub.add_parser("show", parents=[common], help="print the latest goal")
    p_show.set_defaults(func=_cmd_show)

    p_hist = sub.add_parser("history", parents=[common], help="print the last N goals")
    p_hist.add_argument("--limit", type=int, default=DEFAULT_HISTORY_LIMIT, help="how many (default 10)")
    p_hist.set_defaults(func=_cmd_history)
    return parser


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    try:
        return args.func(args, state_dir)
    except StoreUnreadable as exc:
        print(f"goal_state: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
