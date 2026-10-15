#!/usr/bin/env python3
"""Print the ONE canonical boot brief for the orchestrator.

INDEX: Canonical deterministic boot brief (GOAL from the event store, STATE.md ACTIVE block, BUILDLOG tail, event-store projections, git) with a byte cap; exit 2 when the store is unreadable.

Usage:
    python tools/boot_state.py [--repo DIR] [--state-dir DIR] [--max-bytes N]
                               [--buildlog-lines N] [--json]

Sections, in this order:
    GOAL         the latest goal_set projection (tools/goal_state.py). With no goal event:
                 the single line "GOAL: none recorded" and nothing else for that section.
    STATE.md     the "## ACTIVE" block of <repo>/STATE.md only.
    BUILDLOG.md  the last N lines (default 25) of <repo>/BUILDLOG.md.
    EVENT STORE  open tracker lanes via StateAPI.project("tracker") (project_tracker) and the
                 last task_event rows of stream "task" (StateAPI.get). Goal via project_goal.
    GIT          `git log --oneline -5` and the first 20 lines of `git status --short`.

Cap: the markdown brief is at most --max-bytes (default 12000) UTF-8 bytes and always holds.
When over the cap, the ACTIVE block is trimmed to half the cap first, then the oldest
BUILDLOG lines, then ACTIVE, event-store and git lines (tail first). The GOAL section is
never line-trimmed. --json carries the same section texts as one object; the cap applies to
the markdown.

Determinism: no wall-clock values are printed; the same store, STATE.md, BUILDLOG.md and git
state give byte-identical output.

Store: --state-dir, else $AESOP_STATE_ROOT, else <repo>/state (the tracker DB). The store is
read, never created. A missing, empty or unreadable store exits 2 with one stderr line and
prints NOTHING on stdout (fail closed: no partial brief that looks complete).

Exit codes: 0 ok; 2 store unreadable or brief unavailable.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from state_store.read_api import read_text_file  # noqa: E402
from tools import goal_state  # noqa: E402

DEFAULT_MAX_BYTES = 12000
DEFAULT_BUILDLOG_LINES = 25
TASK_LIMIT = 5
LANE_LIMIT = 15
GIT_STATUS_LIMIT = 20
SUMMARY_CHARS = 160
TASK_STREAM = "task"
TASK_EVENT = "task_event"
TRACKER_VIEW = "tracker"
CLOSED_STATUSES = frozenset({
    "done", "closed", "closed-foreign", "resolved", "archived", "merged", "cancelled", "wontfix",
})

SECTION_KEYS = ("goal", "state_active", "buildlog_tail", "event_store", "git")
GOAL, STATE, BUILD, EVENT, GIT = range(5)

MARK_STATE = "... (ACTIVE block trimmed to --max-bytes)"
MARK_BUILD = "... (older BUILDLOG lines trimmed to --max-bytes)"
MARK_EVENT = "... (event-store lines trimmed to --max-bytes)"
MARK_GIT = "... (git lines trimmed to --max-bytes)"


# Same exception the goal store raises: one fail-closed type for every store read.
StoreUnreadable = goal_state.StoreUnreadable


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------

def _one_line(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _read_text(path: Path) -> str | None:
    return read_text_file(path)


def state_active_block(text: str) -> list[str] | None:
    """Return the ``## ACTIVE`` block (heading included) or None if absent."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("## ACTIVE")), None)
    if start is None:
        return None
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
    block = lines[start:end]
    while block and not block[-1].strip():
        block.pop()
    return block


def read_store(state_dir: Path) -> dict:
    """Read goal, task and tracker projections from the store. Raises StoreUnreadable."""
    api = goal_state.open_store(state_dir, create=False)  # raises StoreUnreadable
    try:
        with api:
            goal_events = api.get(goal_state.GOAL_STREAM)
            task_events = [e for e in api.get(TASK_STREAM) if e.get("type") == TASK_EVENT]
            tracker = api.project(TRACKER_VIEW)
    except Exception as exc:
        raise StoreUnreadable(f"event store unreadable: {exc}") from None
    return {
        "goal": goal_state.project_goal(goal_events),
        "tasks": task_events,
        "tracker_items": tracker.get("items", []),
    }


def _git_lines(repo: Path) -> list[str]:
    def run(args: list[str]) -> list[str]:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, encoding="utf-8", errors="replace", timeout=10, check=True,
        )
        return [ln for ln in proc.stdout.splitlines() if ln.strip()]

    try:
        log = run(["log", "--oneline", "-5"])
        status = run(["status", "--short"])
    except subprocess.CalledProcessError as exc:
        reason = (exc.stderr or "").strip().splitlines()[:1]
        return [f"(git unavailable: {reason[0] if reason else 'exit ' + str(exc.returncode)})"]
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"(git unavailable: {exc})"]
    lines = ["git log --oneline -5:"] + (log or ["(none)"])
    lines.append(f"git status --short (first {GIT_STATUS_LIMIT}):")
    lines += status[:GIT_STATUS_LIMIT] or ["(clean)"]
    if len(status) > GIT_STATUS_LIMIT:
        lines.append(f"(+{len(status) - GIT_STATUS_LIMIT} more)")
    return lines


def _event_store_lines(store: dict) -> list[str]:
    lanes = [it for it in store["tracker_items"] if it.get("status") not in CLOSED_STATUSES]
    tasks = store["tasks"]
    lines = [
        "## EVENT STORE",
        "projections: StateAPI.project('tracker') -> project_tracker (open lanes); "
        "StateAPI.get('task') type task_event (last task events); "
        "StateAPI.get('orchestrator_goal') -> project_goal (GOAL)",
        f"open lanes: {len(lanes)} (tracker items not in the closed-status set)",
    ]
    for it in lanes[:LANE_LIMIT]:
        title = _one_line(it.get("title") or it.get("summary") or "", SUMMARY_CHARS)
        lines.append(f"- {it.get('id', '-')} [{it.get('status', '-')}] {title}")
    if len(lanes) > LANE_LIMIT:
        lines.append(f"(+{len(lanes) - LANE_LIMIT} more open lanes)")
    tail = tasks[-TASK_LIMIT:]
    lines.append(f"last task events: {len(tail)} of {len(tasks)} (stream task, type task_event):")
    for ev in tail:
        p = ev.get("payload") or {}
        lines.append(
            f"- v{ev.get('version', '-')} {p.get('ts', '-')} agent={p.get('agent_type') or '-'} "
            f"kind={p.get('kind') or '-'}: {_one_line(p.get('summary', ''), SUMMARY_CHARS)}"
        )
    return lines


def gather(repo: Path, state_dir: Path, buildlog_lines: int) -> list[list[str]]:
    """Build the five sections as line lists, in canonical order. Raises StoreUnreadable."""
    store = read_store(state_dir)
    goal_block = goal_state.render_goal_block(store["goal"]).rstrip("\n").splitlines()

    state_text = _read_text(repo / "STATE.md")
    if state_text is None:
        state_lines = ["## STATE.md (ACTIVE block)", "(STATE.md missing)"]
    else:
        block = state_active_block(state_text)
        if block is None:
            state_lines = ["## STATE.md (ACTIVE block)", "(no ## ACTIVE block in STATE.md)"]
        else:
            state_lines = ["## STATE.md (ACTIVE block)"] + block

    build_text = _read_text(repo / "BUILDLOG.md")
    heading = f"## BUILDLOG.md (last {buildlog_lines} lines)"
    if build_text is None:
        build_lines = [heading, "(BUILDLOG.md missing)"]
    else:
        entries = build_text.splitlines()[-buildlog_lines:] if buildlog_lines > 0 else []
        build_lines = [heading] + entries

    return [goal_block, state_lines, build_lines, _event_store_lines(store),
            ["## GIT"] + _git_lines(repo)]


# ---------------------------------------------------------------------------
# Byte cap
# ---------------------------------------------------------------------------

def _nbytes(lines: list[str]) -> int:
    return len("\n".join(lines).encode("utf-8"))


def _assemble(sections: list[list[str]]) -> str:
    return "\n\n".join("\n".join(sec) for sec in sections) + "\n"


def _size(sections: list[list[str]]) -> int:
    return len(_assemble(sections).encode("utf-8"))


def _size_with(sections: list[list[str]], idx: int, block: list[str]) -> int:
    trial = list(sections)
    trial[idx] = block
    return _size(trial)


def _fit_drop_tail(block: list[str], allow: int, marker: str) -> list[str]:
    """Keep the heading, drop body lines from the end until the block fits ``allow`` bytes."""
    if _nbytes(block) <= allow:
        return block
    head, body = block[:1], block[1:]
    keep = list(body)
    while keep and _nbytes(head + keep + [marker]) > allow:
        keep.pop()
    return head + keep + [marker]


def _fit_drop_head(block: list[str], allow: int, marker: str) -> list[str]:
    """Keep the heading and the NEWEST body lines (drop oldest first) until the block fits."""
    if _nbytes(block) <= allow:
        return block
    head, body = block[:1], block[1:]
    keep = list(body)
    while keep and _nbytes(head + [marker] + keep) > allow:
        keep.pop(0)
    return head + [marker] + keep


def fit_to_cap(sections: list[list[str]], max_bytes: int) -> list[list[str]]:
    """Deterministically shrink ``sections`` so the assembled brief is <= ``max_bytes``."""
    if _size(sections) <= max_bytes:
        return sections
    out = [list(s) for s in sections]
    # 1. ACTIVE gets its share (half the cap), never more than what is left.
    allow = min(max_bytes // 2, max_bytes - _size_with(out, STATE, []))
    out[STATE] = _fit_drop_tail(out[STATE], allow, MARK_STATE)
    # 2. BUILDLOG: keep the newest lines.
    if _size(out) > max_bytes:
        allow = max_bytes - _size_with(out, BUILD, [])
        out[BUILD] = _fit_drop_head(out[BUILD], allow, MARK_BUILD)
    # 3. Remaining sections, tail first. GOAL is never line-trimmed.
    for idx, marker in ((STATE, MARK_STATE), (EVENT, MARK_EVENT), (GIT, MARK_GIT)):
        if _size(out) <= max_bytes:
            break
        allow = max_bytes - _size_with(out, idx, [])
        out[idx] = _fit_drop_tail(out[idx], allow, marker)
    return out


def render_brief(sections: list[list[str]], max_bytes: int) -> str:
    """Return the markdown brief, hard-cut at ``max_bytes`` only as a last resort."""
    text = _assemble(fit_to_cap(sections, max_bytes))
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    return data[:max_bytes].decode("utf-8", errors="ignore")


def build_json(sections: list[list[str]], max_bytes: int) -> dict:
    fitted = fit_to_cap(sections, max_bytes)
    text = _assemble(fitted)
    return {
        "max_bytes": max_bytes,
        "bytes": len(text.encode("utf-8")),
        "sections": {key: "\n".join(sec) for key, sec in zip(SECTION_KEYS, fitted)},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print the canonical orchestrator boot brief.")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="git repo holding STATE.md and BUILDLOG.md (default: this checkout)")
    parser.add_argument("--state-dir", default=None, help="state directory (default: AESOP_STATE_ROOT or <repo>/state)")
    parser.add_argument("--max-bytes", type=_positive, default=DEFAULT_MAX_BYTES, help="byte cap (default 12000)")
    parser.add_argument("--buildlog-lines", type=int, default=DEFAULT_BUILDLOG_LINES, help="BUILDLOG tail lines (default 25)")
    parser.add_argument("--json", action="store_true", help="print one JSON object with the same sections")
    return parser


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    repo = Path(args.repo)
    state_dir = Path(args.state_dir) if args.state_dir else (
        Path(os.environ["AESOP_STATE_ROOT"]) if os.environ.get("AESOP_STATE_ROOT") else repo / "state"
    )
    try:
        sections = gather(repo, state_dir, args.buildlog_lines)
    except StoreUnreadable as exc:
        print(f"boot_state: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # fail closed: never print a partial brief
        print(f"boot_state: brief unavailable: {exc}", file=sys.stderr)
        return 2
    if args.json:
        sys.stdout.write(json.dumps(build_json(sections, args.max_bytes), indent=2) + "\n")
    else:
        sys.stdout.write(render_brief(sections, args.max_bytes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
