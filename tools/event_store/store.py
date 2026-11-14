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
import socket
import time
import uuid
from pathlib import Path

DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")
V2_KEYS = ("v", "ts", "seq", "kind", "actor", "session", "text", "refs", "prev")

# Append lock: a lock file in the store root, created with O_EXCL. An append holds it for
# tens of milliseconds on Windows. A lock whose owner is provably gone (same host, pid not
# alive) is broken at once; any lock older than STALE_LOCK_SECONDS is broken whatever its
# owner, because no append legitimately holds it that long. The wait limit is 60s: eight
# processes appending 50 events each serialize to about 14s on Windows (15 ms timer
# granularity bounds each handoff), so a shorter limit times out healthy waiters.
LOCK_NAME = ".append.lock"
LOCK_TIMEOUT_SECONDS = 60.0
STALE_LOCK_SECONDS = 60.0
LOCK_POLL_SECONDS = 0.01


class StoreError(Exception):
    """Raised for an unusable store or a refused append."""


def line_sha(line_bytes):
    return hashlib.sha256(line_bytes).hexdigest()


def encode_event(event):
    return json.dumps(event, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _pid_alive(pid):
    """True unless the pid is provably gone. Unknown answers count as alive (fail closed)."""
    if os.name == "nt":
        import ctypes
        query_limited = 0x1000
        still_active = 259
        error_invalid_parameter = 87
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(query_limited, False, pid)
        if not handle:
            return ctypes.GetLastError() != error_invalid_parameter
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _break_if_stale(lock):
    """Break the lock only if its owner is provably gone or it is older than the limit.

    Returns True when the caller should retry acquiring at once (lock already gone or
    broken), False when the lock is live and must be waited on.
    """
    try:
        raw = lock.read_bytes()
        age = time.time() - lock.stat().st_mtime
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        record = json.loads(raw.decode("utf-8"))
    except ValueError:
        record = None
    if record is None:
        stale = age > STALE_LOCK_SECONDS
    else:
        owner_gone = (record.get("host") == socket.gethostname()
                      and not _pid_alive(int(record.get("pid", 0))))
        stale = owner_gone or age > STALE_LOCK_SECONDS
    if not stale:
        return False
    # Move the lock aside first, then check what was moved. If it is not the stale record
    # we decided on (a fresh owner replaced it in the gap), put it back and do not break.
    parked = lock.with_name("%s.broken-%s" % (lock.name, uuid.uuid4().hex))
    try:
        os.replace(lock, parked)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        moved = parked.read_bytes()
    except OSError:
        moved = None
    if moved == raw:
        try:
            parked.unlink()
        except OSError:
            pass
        return True
    try:
        os.link(parked, lock)
        parked.unlink()
    except OSError:
        pass
    return False


@contextlib.contextmanager
def _append_lock(store_root):
    """Exclusive lock over the store's read-tail / append / head-update section.

    Waits up to LOCK_TIMEOUT_SECONDS, then raises StoreError: an append never proceeds
    unlocked and never silently skips. The lock is released on any exit, including an
    exception, and only if this call still owns it (matched by a per-acquire token).
    """
    lock = Path(store_root) / LOCK_NAME
    token = "%d:%s" % (os.getpid(), uuid.uuid4().hex)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _break_if_stale(lock):
                continue
            if time.monotonic() >= deadline:
                raise StoreError("timed out after %.1fs waiting for append lock %s"
                                 % (LOCK_TIMEOUT_SECONDS, lock))
            time.sleep(LOCK_POLL_SECONDS)  # sleep-ok: lock poll, bounded by the deadline
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                                     "ts": time.time(), "token": token}))
        except BaseException:
            with contextlib.suppress(OSError):
                lock.unlink()
            raise
        break
    try:
        yield
    finally:
        _release_lock(lock, token)


def _release_lock(lock, token):
    """Remove the lock if this acquire still owns it.

    On Windows a delete fails while another process has the file open (a waiter reading
    the record), so the delete is retried briefly instead of being dropped: a dropped
    release would leave a live-looking lock that blocks every other append.
    """
    for _ in range(200):
        try:
            if json.loads(lock.read_text(encoding="utf-8")).get("token") != token:
                return
            lock.unlink()
            return
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            time.sleep(0.01)  # sleep-ok: retry a sharing violation on release, bounded
    # Still held after ~2s: leave it. Its pid is ours and alive, so waiters will time out
    # with a clear error rather than steal it; the next run breaks it as stale.


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

        Head is an optional key: a store.json without it verifies exactly as before, so
        existing stores do not go red. Only the one key changes; other keys are kept.
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
        head = self._meta.get("head") if v2 else None
        if head is not None:
            # The chain proves each line against its predecessor, not the final line's own
            # bytes. The recorded head covers the tail, so an edit to the last line is caught.
            want_head = line_sha(prev_line) if prev_line is not None else ""
            if head != want_head:
                problems.append("store.json: head does not match the last line (last line "
                                "edited, or lines removed or appended outside the store)")
        return problems
