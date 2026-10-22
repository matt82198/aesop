"""Store: one project's append-only event log under <path>/events/YYYY-MM-DD.jsonl.

Schema 2 (new stores): one hash chain across the whole store. Each line's prev is the
sha256 of the previous line's bytes (no trailing newline), across day-file boundaries,
and seq counts store-wide. The first event has prev "".

Schema 1 (frozen stores written by conductor3): every day file restarts the chain at
prev "". Verified under those rules only.
"""
import hashlib
import json
import re
import time
from pathlib import Path

DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")
V2_KEYS = ("v", "ts", "seq", "kind", "actor", "session", "text", "refs", "prev")


class StoreError(Exception):
    """Raised for an unusable store or a refused append."""


def line_sha(line_bytes):
    return hashlib.sha256(line_bytes).hexdigest()


def encode_event(event):
    return json.dumps(event, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        meta_path = self.path / "store.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError("cannot read %s: %s" % (meta_path, exc))
        self.project = meta.get("project")
        self.schema = meta.get("schema")
        if not self.project or self.schema not in (1, 2):
            raise StoreError("%s needs project and schema 1|2" % meta_path)
        self.events_dir = self.path / "events"

    def day_files(self):
        if not self.events_dir.is_dir():
            return []
        return sorted(p for p in self.events_dir.iterdir() if DAY_FILE.match(p.name))

    @staticmethod
    def _lines(path):
        return [x for x in path.read_bytes().split(b"\n") if x]

    def load(self):
        """All events in file order, parsed."""
        out = []
        for f in self.day_files():
            for raw in self._lines(f):
                out.append(json.loads(raw.decode("utf-8")))
        return out

    def _tail(self):
        """(last line bytes, last seq) across the store, or (None, 0)."""
        for f in reversed(self.day_files()):
            lines = self._lines(f)
            if lines:
                return lines[-1], json.loads(lines[-1].decode("utf-8"))["seq"]
        return None, 0

    def append(self, kind, actor, text, session="", refs=None, ts=None):
        if self.schema != 2:
            raise StoreError("store %s is schema %s; only schema 2 accepts appends"
                             % (self.project, self.schema))
        ts = ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        day = ts[:10]
        files = self.day_files()
        if files and day < files[-1].stem:
            raise StoreError("ts %s is older than latest day file %s" % (ts, files[-1].name))
        last, seq = self._tail()
        event = {"v": 2, "ts": ts, "seq": seq + 1, "kind": kind, "actor": actor,
                 "session": session or "", "text": text, "refs": dict(refs or {}),
                 "prev": line_sha(last) if last is not None else ""}
        self.events_dir.mkdir(parents=True, exist_ok=True)
        target = self.events_dir / (day + ".jsonl")
        with open(target, "ab") as fh:
            fh.write(encode_event(event) + b"\n")
        return event

    def verify(self):
        """Return a list of problem strings; empty means the chain is intact."""
        problems = []
        v2 = self.schema == 2
        prev_line = None
        last_seq = 0
        for f in self.day_files():
            if not v2:
                prev_line = None  # v1: chain restarts each day file
            for n, raw in enumerate(self._lines(f), 1):
                where = "%s:%d" % (f.name, n)
                try:
                    ev = json.loads(raw.decode("utf-8"))
                except ValueError:
                    problems.append("%s: unparseable line" % where)
                    prev_line = raw
                    continue
                want = line_sha(prev_line) if prev_line is not None else ""
                if ev.get("prev") != want:
                    problems.append("%s: prev mismatch (chain broken or earlier line removed)"
                                    % where)
                seq = ev.get("seq")
                if v2:
                    if ev.get("v") != 2:
                        problems.append("%s: v is not 2" % where)
                    if seq != last_seq + 1:
                        problems.append("%s: seq %r, expected %d" % (where, seq, last_seq + 1))
                elif not isinstance(seq, int) or seq <= last_seq:
                    problems.append("%s: seq %r not increasing" % (where, seq))
                if isinstance(seq, int):
                    last_seq = seq
                prev_line = raw
        return problems
