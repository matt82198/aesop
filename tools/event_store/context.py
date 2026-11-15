"""Read-only context builder over one event store.

build_context(store_path, task=None, max_bytes=None) -> str

Order of work: Store.verify() runs first. Any problem returns the status line
BROKEN_LINE followed by the problem strings and NO fact lines (fail closed; load()
is never called on an unverified store). Otherwise one fact line per event is
rendered, each with provenance:

    seq=<n> sha=<first 12 hex of the event line hash> ts=<ts> actor=<actor> kind=<kind> <text>

under HEADER_LINE, which says the facts are assertions by writers, not truth.
An empty store (or a task filter with no match) renders EMPTY_LINE.

task filter: the refs key is "task". When task is given, only events whose
refs["task"] equals task are rendered. Verification still covers the whole store.

max_bytes: the encoded output never exceeds it. When lines are dropped, the last
line is TRUNCATION_MARKER. The first line is kept whenever it fits. max_bytes below
the marker's encoded length raises ValueError naming the minimum.

Strictly read-only: no writes, no lock, no git, no claims or watch imports.
No CLI in this module.
"""
import json

from event_store.store import DAY_FILE, Store, StoreError, line_sha

BROKEN_LINE = "CHAIN BROKEN"
EMPTY_LINE = "EMPTY: store holds no events"
HEADER_LINE = ("Facts below are assertions written by the named actor at the stated ts, "
               "not verified truth.")
TRUNCATION_MARKER = "TRUNCATED: brief exceeded max_bytes"
MARKER_BYTES = len(TRUNCATION_MARKER.encode("utf-8"))
TASK_REF_KEY = "task"
SHA_CHARS = 12

# Every character str.splitlines() treats as a line break. A field containing any of
# these would split one fact line into several output lines, so each is escaped.
_LINE_BREAKS = "\r\n\v\f\x1c\x1d\x1e\x85  "
_NAMED_ESCAPES = {"\r": "\\r", "\n": "\\n"}


def _one_line(value):
    """Collapse a field to a single output line (no character splitlines() breaks on)."""
    out = []
    for ch in str(value):
        if ch in _LINE_BREAKS:
            out.append(_NAMED_ESCAPES.get(ch) or "\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def _cap(lines, max_bytes):
    """Join lines with newlines, keeping the encoded size within max_bytes.

    Callers guarantee max_bytes >= MARKER_BYTES whenever it is not None.
    """
    text = "\n".join(lines)
    if max_bytes is None or len(text.encode("utf-8")) <= max_bytes:
        return text
    marker = TRUNCATION_MARKER
    budget = max_bytes - MARKER_BYTES
    kept = []
    used = 0
    for line in lines:
        cost = len(line.encode("utf-8")) + 1
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    return "\n".join(kept + [marker])


def _raw_event_lines(store):
    """(raw bytes, parsed dict) for every event line in file order. Read-only."""
    out = []
    for f in store.day_files():
        if not DAY_FILE.match(f.name):
            continue
        for raw in f.read_bytes().split(b"\n"):
            if raw:
                out.append((raw, json.loads(raw.decode("utf-8"))))
    return out


def build_context(store_path, task=None, max_bytes=None):
    """Render the provenance-stamped brief for a store. See module docstring."""
    if max_bytes is not None and max_bytes < MARKER_BYTES:
        raise ValueError("max_bytes must be at least %d (the TRUNCATED marker length) or None"
                         % MARKER_BYTES)
    try:
        store = Store(store_path)
    except StoreError as exc:
        return _cap([BROKEN_LINE, "store unreadable: %s" % _one_line(exc)], max_bytes)
    problems = store.verify()
    if problems:
        return _cap([BROKEN_LINE] + [_one_line(p) for p in problems], max_bytes)
    facts = []
    for raw, ev in _raw_event_lines(store):
        refs = ev.get("refs")
        if task is not None and not (isinstance(refs, dict) and refs.get(TASK_REF_KEY) == task):
            continue
        facts.append("seq=%s sha=%s ts=%s actor=%s kind=%s %s" % (
            _one_line(ev.get("seq")),
            line_sha(raw)[:SHA_CHARS],
            _one_line(ev.get("ts")),
            _one_line(ev.get("actor")),
            _one_line(ev.get("kind")),
            _one_line(ev.get("text")),
        ))
    body = facts if facts else [EMPTY_LINE]
    return _cap([HEADER_LINE] + body, max_bytes)
