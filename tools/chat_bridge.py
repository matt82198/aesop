#!/usr/bin/env python3
"""Chat bridge: the orchestrator side of `aesop chat` (F1).
INDEX: Post chat replies and serve pending chat turns

Usage:
    python chat_bridge.py reply <turn_id> <text> [--final]
    python chat_bridge.py serve [--max-iterations N] [--interval SECONDS]

Transport (matches chat/backends/orchestrator_source.mjs):
    inbox line  `- [ISO-ts] chat(<turn_id>): <escaped text>` in <state root>/ui-inbox.md
                (backslash, CR, LF and the Unicode line breaks escaped as \\\\, \\r, \\n, \\uXXXX);
    reply file  <state root>/chat/<turn_id>.jsonl, one JSON object per line
                {turn_id, seq, chunk, final}; `final: true` ends the turn.

State root: AESOP_STATE_ROOT, else <repo>/state. Inbox: AESOP_INBOX_PATH, else <root>/ui-inbox.md.

reply:  appends one record (seq continues from the existing file).
serve:  bounded foreground loop (never unbounded: --max-iterations, default 1). Each iteration
        answers every chat turn that has no final reply yet by routing its text to the configured
        orchestrator seat, `AESOP_CHAT_SEAT=module:callable`. The callable takes the turn text and
        returns an iterable of string chunks. With no seat configured (or one that fails to load)
        the turn is answered with "no orchestrator listening". Exit 0.

This tool never calls a model, the claude CLI or any API itself: the seat is whatever the
operator configured, reached only through that callable.
"""

import argparse
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path

NO_LISTENER = "no orchestrator listening"
LINE_RE = re.compile(r"^- \[[^\]]*\] chat\(([A-Za-z0-9_-]+)\): (.*)$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
UNESCAPE_RE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)", re.DOTALL)


def state_root() -> Path:
    env = os.environ.get("AESOP_STATE_ROOT")
    return Path(env) if env else Path(__file__).resolve().parent.parent / "state"


def inbox_path() -> Path:
    env = os.environ.get("AESOP_INBOX_PATH")
    return Path(env) if env else state_root() / "ui-inbox.md"


def reply_path(turn_id: str) -> Path:
    if not SAFE_ID.match(turn_id):
        raise ValueError(f"bad turn id: {turn_id!r}")
    return state_root() / "chat" / f"{turn_id}.jsonl"


def _unescape_one(m) -> str:
    tok = m.group(1)
    if len(tok) == 5 and tok[0] == "u":
        return chr(int(tok[1:], 16))
    return {"n": "\n", "r": "\r", "\\": "\\"}.get(tok, "\\" + tok)


def unescape(text: str) -> str:
    """Inverts escape_inbox_text: one left-to-right pass, so an escaped backslash never starts an escape."""
    return UNESCAPE_RE.sub(_unescape_one, text)


def read_records(turn_id: str) -> list:
    p = reply_path(turn_id)
    if not p.exists():
        return []
    recs = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            recs.append(json.loads(line))
        except ValueError:
            continue
    return recs


def append_reply(turn_id: str, chunk: str, final: bool = False) -> None:
    p = reply_path(turn_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"turn_id": turn_id, "seq": len(read_records(turn_id)), "chunk": chunk, "final": bool(final)}
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def pending_turns() -> list:
    p = inbox_path()
    if not p.exists():
        return []
    turns = []
    # Split on LF only. str.splitlines() also breaks on U+2028/U+2029, \x0b, \x0c, \x1c-\x1e and
    # \x85, which let a pasted separator forge a turn. The final element has no terminating LF
    # yet (a partial append), so it is ignored until it is complete.
    raw = p.read_bytes().decode("utf-8", errors="replace")
    for line in raw.split("\n")[:-1]:
        if line.endswith("\r"):
            line = line[:-1]
        m = LINE_RE.match(line)
        if m and not any(r.get("final") for r in read_records(m.group(1))):
            turns.append((m.group(1), unescape(m.group(2))))
    return turns


def load_seat():
    spec = os.environ.get("AESOP_CHAT_SEAT", "").strip()
    if ":" not in spec:
        return None
    mod, _, fn = spec.partition(":")
    try:
        return getattr(importlib.import_module(mod), fn)
    except Exception as exc:  # a broken seat is "not seated", never a crash
        print(f"chat_bridge: seat {spec!r} not loadable: {exc}", file=sys.stderr)
        return None


def answer(turn_id: str, text: str, seat) -> None:
    if seat is None:
        append_reply(turn_id, NO_LISTENER, final=True)
        return
    try:
        for chunk in seat(text):
            append_reply(turn_id, str(chunk))
    except Exception as exc:
        append_reply(turn_id, f"orchestrator seat failed: {exc}")
    append_reply(turn_id, "", final=True)


def serve(max_iterations: int, interval: float) -> int:
    seat = load_seat()
    for i in range(max(1, max_iterations)):
        for turn_id, text in pending_turns():
            answer(turn_id, text, seat)
        if i + 1 < max_iterations:
            time.sleep(interval)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="aesop chat bridge (orchestrator side)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reply")
    r.add_argument("turn_id")
    r.add_argument("text")
    r.add_argument("--final", action="store_true")
    s = sub.add_parser("serve")
    s.add_argument("--max-iterations", type=int, default=1)
    s.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args(argv)
    if args.cmd == "reply":
        append_reply(args.turn_id, args.text, args.final)
        return 0
    return serve(args.max_iterations, args.interval)


if __name__ == "__main__":
    sys.exit(main())
