#!/usr/bin/env python3
"""
reconcile.py — drift detection between STATE.md (git) and state_store (SQLite).
INDEX: Detect/resolve drift (git STATE.md vs. state_store projection; git-authoritative; --resolve appends to SQLite only, never rewrites git-side state)

## Why this exists

Critique: "SQLite state_store and git-committed STATE.md drift with NO reconcile
logic." This tool is the conservative first step: detect + report drift by
default; fix it only with an explicit ``--resolve``.

## What was actually investigated (read STATE.md + state_store/*.py end to end)

STATE.md (see ensure_state.py's own template) is unstructured orchestrator
narrative: Intent / locked decisions / current Phase / NEXT STEPS, written and
owned by the orchestrator (single-writer, per this project's CLAUDE.md Cardinal
Rule #7 — "Single-writer: MEMORY.md (keeper), STATE.md (orchestrator)").

state_store/ (store.py + api.py + projections.py) is an event-sourced SQLite
log. Today it registers exactly ONE projection: ``tracker`` — a UI Kanban
board of backlog items (``id``/``title``/``lane``/``status``/``tags``/
``pr_link``/``completed_at``). That projection is a DIFFERENT domain from
STATE.md: it was one-time-migrated from AUDIT-BACKLOG.md
(``ui/collectors.py:migrate_tracker_from_backlog``), not from STATE.md, and is
independently mutated via the UI CRUD endpoints thereafter. There is no
shared id, key, or semantic concept between STATE.md's prose and a tracker
item. Reconciling STATE.md against ``tracker`` would mean inventing a mapping
that does not exist in the codebase's actual design — the task instructions
explicitly warn against exactly that ("do not invent overlap").

**Honest finding: STATE.md and state_store are almost entirely DISJOINT.**

The ONE field that genuinely is meant to be "the same fact, told twice" is the
orchestrator's **current phase/wave identifier** — STATE.md carries it today
in a structured, reliably-parseable heading (``## Phase: `wave-N-name` ``);
state_store has no field for it yet (it predates the "additive prototype... a
later dual-read cutover" state_store/CLAUDE.md describes). Rather than
pretend a comparable field exists where none does, this tool defines that
field explicitly — a ``meta`` event stream, ``phase_set`` events, payload
``{"phase": <str>}`` — read directly off the generic ``EventStore`` (no
change to state_store/ itself; any stream name is legal there). Until
something else populates that stream, real runs will simply show "state_store
has no phase recorded" every time, which is itself an honest, useful signal:
it names precisely the gap the critique is pointing at, rather than
manufacturing a false "in sync".

## Authority

- ``phase`` is git-authoritative: STATE.md is the durable, human-reviewed,
  single-writer checkpoint (Cardinal Rule #7); state_store's copy is a mirror
  for read-side consumers (dashboards, MCP, etc.) that would rather not parse
  markdown. ``--resolve`` therefore only ever WRITES to state_store (a new
  ``phase_set`` event) and NEVER writes to STATE.md — the tool must not
  become a second writer of the orchestrator's single-writer file.
- The field registry (``FIELDS``) and ``decide_resolution`` are generic and
  support a `state_store`-authoritative field the same way, for when
  state_store gains its own independently event-derived facts (e.g. real
  wave-tagged item counts) that STATE.md would want mirrored back — see
  ``test_reconcile.py``'s ``DecideResolutionTest`` for that direction
  exercised in isolation, since no current field actually uses it.

## Usage

    python tools/reconcile.py --state-md STATE.md
    python tools/reconcile.py --state-md STATE.md --resolve
    python tools/reconcile.py --state-md STATE.md --json

(DB path defaults to state/tracker_events.db; override with --db if needed.)

Exit codes: 0 = no drift (or drift cleanly resolved), 1 = drift detected and
NOT resolved (report mode), 2 = usage/file error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_store.store import EventStore  # noqa: E402
from tools.common import get_state_db_path  # noqa: E402

PHASE_RE = re.compile(r"^##\s*Phase:\s*`([^`]+)`", re.MULTILINE)

META_STREAM = "meta"
PHASE_EVENT_TYPE = "phase_set"


# ---------------------------------------------------------------------------
# Readers (read-only; never create/mutate anything)
# ---------------------------------------------------------------------------

def read_git_phase(state_md_path: str) -> str | None:
    """Parse the current phase identifier out of STATE.md's ``## Phase:`` heading.

    Returns None if the file has no such heading (or is missing entirely —
    callers that need a hard error on a missing file should stat() first).
    """
    path = Path(state_md_path)
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    match = PHASE_RE.search(content)
    return match.group(1) if match else None


def read_store_phase(db_path: str) -> str | None:
    """Read the most recent ``phase_set`` event's ``phase`` payload from state_store.

    Deliberately avoids ever constructing an EventStore against a
    non-existent db_path in report mode: EventStore.__init__ creates the
    sqlite file + tables as a side effect of merely connecting, which would
    make a read-only "detect" invocation leave a footprint on disk. If the
    file does not exist yet, there is by definition no phase recorded there.
    """
    if not db_path or not os.path.exists(db_path):
        return None
    store = EventStore(db_path)
    events = store.read(META_STREAM)
    phase = None
    for ev in events:
        if ev.get("type") == PHASE_EVENT_TYPE:
            payload = ev.get("payload") or {}
            if "phase" in payload:
                phase = payload["phase"]
    return phase


def write_store_phase(db_path: str, phase: str, actor: str = "reconcile") -> int:
    """Append a phase_set event to state_store's meta stream. Returns new version.

    This is the ONLY write path in this module, and it only ever targets
    state_store (SQLite) — STATE.md (git) is never written by this tool.
    """
    store = EventStore(db_path)
    return store.append(META_STREAM, PHASE_EVENT_TYPE, {"phase": phase}, actor)


# ---------------------------------------------------------------------------
# Field registry — generic so a future state_store-authoritative field can be
# added without restructuring the detect/resolve flow.
# ---------------------------------------------------------------------------

FIELDS = {
    "phase": {
        "authority": "git",
        "read_git": read_git_phase,
        "read_store": read_store_phase,
    },
}


def decide_resolution(git_value, store_value, authority: str):
    """Pure decision: given both current values and which side is authoritative,
    return (drift: bool, target: 'git'|'store'|None, value) where ``target`` is
    which side WOULD need writing to clear the drift (None if no drift, or if
    already in agreement).

    ``authority`` must be 'git' or 'store'. This function performs no I/O —
    it only decides; callers apply the write.
    """
    if authority not in ("git", "store"):
        raise ValueError(f"unknown authority: {authority!r}")

    drift = git_value != store_value
    if not drift:
        return (False, None, git_value)

    if authority == "git":
        return (True, "store", git_value)
    return (True, "git", store_value)


# ---------------------------------------------------------------------------
# Detect / resolve over the whole registry
# ---------------------------------------------------------------------------

def detect_drift(state_md_path: str, db_path: str) -> dict:
    """Read both sources for every registered field; report drift per field.

    Read-only: never creates or mutates STATE.md or the state_store db.
    """
    results = []
    any_drift = False
    for name, spec in FIELDS.items():
        git_value = spec["read_git"](state_md_path)
        store_value = spec["read_store"](db_path)
        drift, target, _value = decide_resolution(git_value, store_value, spec["authority"])
        any_drift = any_drift or drift
        results.append({
            "field": name,
            "authority": spec["authority"],
            "git_value": git_value,
            "store_value": store_value,
            "drift": drift,
            "fix_target": target,
        })
    return {"drift": any_drift, "fields": results}


def resolve_drift(state_md_path: str, db_path: str) -> dict:
    """Detect drift, then apply the authoritative value to the non-authoritative
    side for every drifted field. Idempotent: fields already in agreement are
    left untouched (no event appended). Only ever writes to state_store.

    A field whose authority is 'git' but is drifted is fixed by writing to
    state_store. A field whose authority is 'store' but is drifted would
    require writing to STATE.md — this tool refuses to do that (STATE.md is
    single-writer/orchestrator-owned) and instead reports it unresolved with
    a clear reason, rather than silently corrupting the git checkpoint.
    """
    report = detect_drift(state_md_path, db_path)
    for entry in report["fields"]:
        if not entry["drift"]:
            entry["action"] = "noop"
            continue
        if entry["fix_target"] == "store" and entry["field"] == "phase":
            write_store_phase(db_path, entry["git_value"])
            entry["action"] = "resolved"
            entry["store_value"] = entry["git_value"]
        elif entry["fix_target"] == "git":
            entry["action"] = "refused"
            entry["reason"] = "STATE.md is single-writer (orchestrator); reconcile.py never writes it"
        else:
            entry["action"] = "unsupported"
    report["drift"] = any(e["drift"] and e.get("action") not in ("resolved",) for e in report["fields"])
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_human(report: dict) -> str:
    lines = []
    for entry in report["fields"]:
        status = "DRIFT" if entry["drift"] else "OK"
        line = (
            f"[{status}] {entry['field']} (authority={entry['authority']}): "
            f"git={entry['git_value']!r} store={entry['store_value']!r}"
        )
        if "action" in entry:
            line += f" action={entry['action']}"
        lines.append(line)
    lines.append("DRIFT DETECTED" if report["drift"] else "no drift")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-md", default="STATE.md", help="Path to STATE.md (default: ./STATE.md)")
    parser.add_argument("--db", default=str(get_state_db_path()), help="Path to state_store SQLite db (default: state/tracker_events.db)")
    parser.add_argument("--resolve", action="store_true", help="Fix drift by writing the authoritative value to the non-authoritative side (opt-in; default is detect+report only)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of human text")
    args = parser.parse_args(argv)

    if not os.path.exists(args.state_md):
        print(f"error: STATE.md not found at {args.state_md}", file=sys.stderr)
        return 2

    if args.resolve:
        report = resolve_drift(args.state_md, args.db)
    else:
        report = detect_drift(args.state_md, args.db)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_format_human(report))

    return 1 if report["drift"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
