"""Trigger projection: one pass over a store's events past a durable cursor.

Usage: python -I tools/event_store/watch.py (--project P | --path DIR) [--init]

The cursor is {"seq": N, "head": sha} stored under AESOP_STATE_ROOT at
<state>/event-watch/<project>.cursor.json. It is never stored inside the store.

Each pass runs under the store's append lock (store._append_lock): verify the chain, check
the recorded head, read the cursor, refuse on any unknown kind, emit TRIGGER lines for
trigger kinds past the cursor, then advance the cursor by atomic replace. Emission happens
before the cursor write, so a crash in between re-emits on the next pass (at-least-once);
consumers dedupe on (project, seq).

Output: "TRIGGER seq=N kind=K ref=P" lines, then "WATCH OK cursor=N", or "WATCH FAILED:
<reason>" with exit 1 and no cursor change. Exit codes: 0 ok, 1 failed, 2 refused/usage.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from event_store import registry as reg_mod  # noqa: E402
from event_store.cli import KINDS  # noqa: E402
from event_store.store import Store, StoreError, _append_lock, line_sha  # noqa: E402

STATE_ENV = "AESOP_STATE_ROOT"
CURSOR_DIR = "event-watch"
TRIGGER_KINDS = frozenset({"verdict", "ruling", "handback"}) & KINDS
PROJECT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
HEAD_RE = re.compile(r"^[0-9a-f]{64}$")


class WatchError(Exception):
    """A pass that must fail closed: nothing emitted past the failure, cursor unchanged."""


def cursor_file_for(state_root, project):
    """Cursor path for a project under the given state root."""
    if not PROJECT_RE.match(project or ""):
        raise WatchError("project %r is not a usable name" % (project,))
    return Path(state_root) / CURSOR_DIR / (project + ".cursor.json")


def _entries(store):
    """(raw line bytes, parsed event) for every store line, in file order."""
    out = []
    for f in store.day_files():
        for raw in f.read_bytes().split(b"\n"):
            if raw:
                out.append((raw, json.loads(raw.decode("utf-8"))))
    return out


def _read_cursor(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WatchError("cursor unreadable at %s: %s" % (path, exc))
    seq = data.get("seq") if isinstance(data, dict) else None
    head = data.get("head") if isinstance(data, dict) else None
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0 or not isinstance(head, str):
        raise WatchError("cursor at %s is malformed" % path)
    return seq, head


def _write_cursor(path, seq, head):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"seq": seq, "head": head}, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _check_store(store, entries):
    problems = store.verify()
    if problems:
        raise WatchError("store does not verify: %s" % problems[0])
    if not entries:
        return
    meta = json.loads((store.path / "store.json").read_text(encoding="utf-8"))
    head = meta.get("head") if isinstance(meta, dict) else None
    if not isinstance(head, str) or not HEAD_RE.match(head):
        raise WatchError("store.json head is missing or malformed")
    if head != line_sha(entries[-1][0]):
        raise WatchError("store.json head does not match the last line")


def _pass(store, cursor_file, init, triggers, emit):
    with _append_lock(store.path):
        entries = _entries(store)
        _check_store(store, entries)
        tail_seq = entries[-1][1]["seq"] if entries else 0
        if init:
            if cursor_file.exists():
                raise WatchError("cursor already exists at %s; refusing to re-init"
                                 % cursor_file)
            _write_cursor(cursor_file, 0, "")
        if not cursor_file.exists():
            raise WatchError("no cursor at %s; a first run needs --init" % cursor_file)
        cur_seq, cur_head = _read_cursor(cursor_file)
        if cur_seq > tail_seq:
            raise WatchError("cursor seq %d is ahead of store tail %d" % (cur_seq, tail_seq))
        expect = line_sha(entries[cur_seq - 1][0]) if cur_seq else ""
        if cur_head != expect:
            raise WatchError("cursor head does not match the store line at seq %d" % cur_seq)
        pending = [(raw, ev) for raw, ev in entries if ev["seq"] > cur_seq]
        for _, ev in pending:
            if ev.get("kind") not in KINDS:
                raise WatchError("unknown kind %r at seq %s; nothing emitted, cursor not "
                                 "advanced" % (ev.get("kind"), ev.get("seq")))
        for _, ev in pending:
            if ev["kind"] in triggers:
                emit("TRIGGER seq=%d kind=%s ref=%s" % (ev["seq"], ev["kind"], store.project))
        if not pending:
            return cur_seq
        last_raw, last_ev = pending[-1]
        _write_cursor(cursor_file, last_ev["seq"], line_sha(last_raw))
        return last_ev["seq"]


def run(store, cursor_file, init=False, triggers=TRIGGER_KINDS, emit=None):
    """One pass. Returns 0 on success, 1 on a failed pass; writes "WATCH ..." via emit."""
    emit = emit or print
    try:
        seq = _pass(store, Path(cursor_file), init, triggers, emit)
    except (WatchError, StoreError) as exc:
        emit("WATCH FAILED: %s" % exc)
        return 1
    except Exception as exc:  # fail closed on anything unexpected
        emit("WATCH FAILED: unexpected %s: %s" % (type(exc).__name__, exc))
        return 1
    emit("WATCH OK cursor=%d" % seq)
    return 0


def _refuse(msg):
    sys.stderr.write("refused: %s\n" % msg)
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog="watch")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--project")
    group.add_argument("--path")
    ap.add_argument("--init", action="store_true")
    args = ap.parse_args(argv)
    state_root = os.environ.get(STATE_ENV)
    if not state_root:
        return _refuse("%s is not set; the cursor has no state root" % STATE_ENV)
    try:
        if args.path:
            store = Store(Path(args.path))
        else:
            reg = reg_mod.load_registry()
            store = Store(reg_mod.store_path(reg, args.project))
            if store.project != args.project:
                return _refuse("store belongs to %r, not %r"
                               % (store.project, args.project))
        cursor = cursor_file_for(state_root, store.project)
    except (reg_mod.RegistryError, StoreError, WatchError) as exc:
        return _refuse(str(exc))
    if store.path.resolve() in cursor.resolve().parents:
        return _refuse("cursor %s is inside the store; it must live under %s"
                       % (cursor, STATE_ENV))
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return run(store, cursor, init=args.init, emit=lambda s: (print(s), sys.stdout.flush()))


if __name__ == "__main__":
    sys.exit(main())
