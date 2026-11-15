"""Store: one project's append-only event log under <path>/events/YYYY-MM-DD.jsonl.

Schema 2 (new stores): one hash chain across the whole store. Each line's prev is the
sha256 of the previous line's bytes (no trailing newline), across day-file boundaries,
and seq counts store-wide. The first event has prev "".

Schema 1 (frozen stores written by conductor3): every day file restarts the chain at
prev "". Verified under those rules only.
"""
import contextlib
import hashlib
import json
import os
import re
import time
from pathlib import Path

DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")
V2_KEYS = ("v", "ts", "seq", "kind", "actor", "session", "text", "refs", "prev")

# Append lock: an OS advisory lock on an open handle of a sidecar file in the store root
# (msvcrt.locking on Windows, fcntl.flock elsewhere). The file is never deleted or replaced
# and its content is never read: the operating system drops the lock when the holder
# exits or crashes, and a live holder is never judged stale. Local disks only; network
# filesystems do not give these locks reliable semantics. The wait limit is 60s: eight
# processes appending 50 events each serialize to about 14s on Windows (15 ms timer
# granularity bounds each handoff), so a shorter limit times out healthy waiters.
LOCK_NAME = ".append.lock"
LOCK_TIMEOUT_SECONDS = 60.0
LOCK_POLL_SECONDS = 0.01


class StoreError(Exception):
    """Raised for an unusable store or a refused append."""


def line_sha(line_bytes):
    return hashlib.sha256(line_bytes).hexdigest()


def encode_event(event):
    return json.dumps(event, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _try_lock(fd):
    """Take the OS lock on the open handle without blocking. True when held."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd):
    """Drop the OS lock; closing the handle drops it anyway."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def _append_lock(store_root):
    """Exclusive lock over the store's read-tail / append / head-update section.

    Retries until LOCK_TIMEOUT_SECONDS, then raises StoreError having written nothing: an
    append never proceeds unlocked. Released on any exit, including an exception or
    KeyboardInterrupt; a crashed holder releases at process exit.
    """
    lock = Path(store_root) / LOCK_NAME
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR)
    try:
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise StoreError("timed out after %.1fs waiting for append lock %s"
                                 % (LOCK_TIMEOUT_SECONDS, lock))
            time.sleep(LOCK_POLL_SECONDS)  # sleep-ok: lock poll, bounded by the deadline
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                _unlock(fd)
    finally:
        os.close(fd)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.events_dir = self.path / "events"
        self.read_only = False
        self._meta = {}
        meta_path = self.path / "store.json"
        if not meta_path.exists() and self._is_v1_layout():
            # conductor3 v1 log: day files with no store.json. Schema 1 is implied by the
            # layout and the project is the directory name. Opened read-only.
            self.project = self.path.name
            self.schema = 1
            self.read_only = True
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError("cannot read %s: %s" % (meta_path, exc))
        self._meta = meta
        self.project = meta.get("project")
        self.schema = meta.get("schema")
        if not self.project or self.schema not in (1, 2):
            raise StoreError("%s needs project and schema 1|2" % meta_path)

    def _is_v1_layout(self):
        """True when the first event of the first non-empty day file has no 'v' key."""
        for f in self.day_files():
            for raw in self._lines(f):
                try:
                    ev = json.loads(raw.decode("utf-8"))
                except ValueError:
                    return False
                return isinstance(ev, dict) and "v" not in ev
        return False

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
        if self.read_only:
            raise StoreError("store %s at %s has no store.json (v1 log, read-only); "
                             "appends refused" % (self.project, self.path))
        if self.schema != 2:
            raise StoreError("store %s is schema %s; only schema 2 accepts appends"
                             % (self.project, self.schema))
        ts = ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        day = ts[:10]
        self.events_dir.mkdir(parents=True, exist_ok=True)
        # The lock covers read-tail, line append and head update together, so two
        # processes can never read the same tail and write the same seq and prev.
        with _append_lock(self.path):
            files = self.day_files()
            if files and day < files[-1].stem:
                raise StoreError("ts %s is older than latest day file %s"
                                 % (ts, files[-1].name))
            last, seq = self._tail()
            event = {"v": 2, "ts": ts, "seq": seq + 1, "kind": kind, "actor": actor,
                     "session": session or "", "text": text, "refs": dict(refs or {}),
                     "prev": line_sha(last) if last is not None else ""}
            target = self.events_dir / (day + ".jsonl")
            encoded = encode_event(event)
            # A crash after this line and before _write_head leaves store.json head stale:
            # verify reports it, and the next locked append rewrites head, so it self-repairs.
            with open(target, "ab") as fh:
                fh.write(encoded + b"\n")
            self._write_head(line_sha(encoded))
        return event

    def _write_head(self, head):
        """Record the sha of the store's last line as store.json "head" (atomic replace).

        Head is required once a schema-2 store holds events: verify reports a non-empty
        schema-2 store whose head is missing or malformed. A zero-event store has no head
        to check. Only the one key changes; other keys are kept.
        """
        meta_path = self.path / "store.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["head"] = head
        tmp = self.path / "store.json.tmp"
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, meta_path)
        self._meta = meta

    def verify(self):
        """Return a list of problem strings; empty means the chain is intact."""
        problems = []
        v2 = self.schema == 2
        prev_line = None
        last_seq = 0
        prev_file_ts = None  # v1: last ts of the previous day file
        for f in self.day_files():
            if not v2:
                prev_line = None  # v1: chain restarts each day file
                file_seq = 0  # v1: seq restarts at 1 in each day file
                file_ts = None  # v1: last ts seen in this day file
                first_ts = None  # v1: first ts in this day file
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
                elif not isinstance(seq, int) or seq != file_seq + 1 and not (
                        n == 1 and seq == last_seq + 1):
                    # v1 seq is the 1-based line number within its day file: no gap, no
                    # duplicate, no reorder. A file's first line may also continue a
                    # store-wide count (older v1 fixtures), so only the within-file run is
                    # held to 1..n.
                    problems.append("%s: seq %r, expected %d" % (where, seq, file_seq + 1))
                if isinstance(seq, int):
                    last_seq = seq
                    file_seq = seq
                if not v2:
                    # v1 prev and seq restart per file, so these checks bind the file to
                    # its own date and to its neighbours. A whole missing day file is still
                    # not detectable from v1 data (see the known-limit test).
                    ts = ev.get("ts")
                    if not isinstance(ts, str) or ts[:10] != f.stem:
                        problems.append("%s: ts %r is not on the file's date %s"
                                        % (where, ts, f.stem))
                    else:
                        if file_ts is not None and ts < file_ts:
                            problems.append("%s: ts %r earlier than the line before it"
                                            % (where, ts))
                        if first_ts is None:
                            first_ts = ts
                            if prev_file_ts is not None and ts < prev_file_ts:
                                problems.append("%s: ts %r earlier than the previous day "
                                                "file's last ts" % (where, ts))
                        file_ts = ts
                prev_line = raw
            if not v2 and file_ts is not None:
                prev_file_ts = file_ts
        head = self._meta.get("head") if v2 else None
        want_head = line_sha(prev_line) if prev_line is not None else ""
        if v2 and prev_line is not None and not _is_sha256(head):
            # A non-empty v2 store must carry a head. Without it, deleting the last line
            # would verify clean (finding A1), so a missing or malformed head is a problem.
            problems.append("store.json: head missing or malformed on a non-empty store "
                            "(the head anchor is required once the store holds events)")
        elif head is not None and head != want_head:
            # The chain proves each line against its predecessor, not the final line's own
            # bytes. The recorded head covers the tail, so an edit to the last line is caught.
            problems.append("store.json: head does not match the last line (last line "
                            "edited, or lines removed or appended outside the store)")
        return problems


def _is_sha256(value):
    """True when value is a lowercase 64-hex sha256 string."""
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
