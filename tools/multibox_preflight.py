#!/usr/bin/env python3
"""Multibox preflight probe + network-filesystem guard (multibox increment 0).
INDEX: Multibox increment 0 preflight probe + network-FS guard: `detect_fs_kind()` returns local/network/unknown (POSIX longest-prefix match over /proc/mounts against {nfs,nfs4,cifs,smbfs,smb3,fuse.sshfs,glusterfs}, octal-escape decoded, component-boundary anchored; Windows UNC prefix or GetDriveTypeW==DRIVE_REMOTE via ctypes), unknown treated as network by `is_network_kind()` (fail-closed); `assert_local_sqlite()` raises `NetworkFilesystemError` when the event-store DB sits on a share — the rules-to-code artifact making WAL-over-SMB/NFS structurally impossible, since SQLite's `-shm` index is coherent only between processes on one host; `measure_visibility_delay()` writes fsynced probes (file + parent dir) and force-revalidates the listing for p50/p95/p99, raising `VisibilityTimeout` rather than reporting a bound it never observed; `measure_clock_skew()` estimates local-vs-server skew from server-stamped mtime; `write_peer_probe`/`observe_peer_delays`/`run_peer_probe` implement two-sided `--peer-probe` mode (single-process path exercised in CI, corrupt peer records skipped); CLI `--check --shared-dir DIR --db PATH [--json] [--samples N] [--settle-seconds S] [--max-skew-seconds S] [--peer-probe --peer-id ID --peer-wait-seconds S]`, exit 0=clean/1=findings/2=error; finding ids DB-ON-NETWORK-FS, VISIBILITY-DELAY-EXCEEDS-SETTLE, VISIBILITY-UNBOUNDED, VISIBILITY-PROBE-FAILED, CLOCK-SKEW-EXCEEDS-BOUND, CLOCK-SKEW-PROBE-FAILED; advisory-only until the multibox flag ships, then a hard startup gate; stdlib-only, ASCII, hermetic (temp dirs, no network)

The multibox design puts a *claim log* on a shared filesystem but keeps each
instance's SQLite event store on local disk. SQLite WAL requires a shared-memory
index (`-shm`) that is only coherent between processes on the same host, so a
WAL database on SMB/NFS is silently unsafe. This module turns that prose rule
into an enforced precondition (`assert_local_sqlite`) and measures the one
property the shared filesystem must actually supply: a written+fsynced file
becomes visible in another host's directory listing within a bounded time D.

Public surface:
  detect_fs_kind(path)            -> "local" | "network" | "unknown"
  is_network_kind(kind)           -> bool   (unknown is treated as network)
  assert_local_sqlite(db_path)    -> None   (raises NetworkFilesystemError)
  measure_visibility_delay(dir)   -> {p50,p95,p99,max} seconds
  measure_clock_skew(dir)         -> local-vs-server clock skew, seconds
  write_peer_probe / observe_peer_delays  -> two-sided (--peer-probe) mode
  run_preflight(...)              -> report dict with findings

CLI:
  multibox_preflight.py --check --shared-dir DIR --db PATH [--json]
Exit codes: 0 = clean, 1 = findings, 2 = error.

Advisory-only today; wired as a hard startup gate when multibox.enabled ships.
Stdlib only, ASCII output, hermetic (no network).
"""

import argparse
import json
import os
import statistics
import sys
import time
import uuid

# Mount filesystem types that are network-backed and therefore unsafe for a
# WAL SQLite database and for any lock-based coordination primitive.
NETWORK_FS_TYPES = frozenset({
    "nfs", "nfs4", "cifs", "smbfs", "smb3", "fuse.sshfs", "glusterfs",
})

# GetDriveTypeW return codes (winbase.h).
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6
_LOCAL_DRIVE_TYPES = frozenset({
    DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_CDROM, DRIVE_RAMDISK,
})

PROC_MOUNTS_PATH = "/proc/mounts"

# Distinguishes "caller did not supply a mount table" (read /proc/mounts) from
# "caller supplied None" (no mount table is available). Without this the same
# call classifies as local on Linux and unknown on Windows.
_UNSET = object()
PROBE_DIRNAME = ".multibox-preflight"
PEER_DIRNAME = "peers"

DEFAULT_SAMPLES = 5
DEFAULT_SETTLE_SECONDS = 5.0
DEFAULT_MAX_SKEW_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_INTERVAL_SECONDS = 0.01

MOUNT_GUIDANCE = (
    "Keep the SQLite event store on local storage. If a shared directory must "
    "be used for the claim log, disable client caching "
    "(NFS: nfsvers=4.1,actimeo=1,lookupcache=none; "
    "SMB: cache=none / directoryCacheLifetime=0)."
)


class PreflightError(Exception):
    """Environment/usage error that makes the preflight impossible to run."""


class VisibilityTimeout(PreflightError):
    """A probe file never appeared in a revalidated directory listing."""


class NetworkFilesystemError(PreflightError):
    """A path that must be local storage resolved to a network filesystem."""

    def __init__(self, message, path=None, kind=None):
        super().__init__(message)
        self.path = path
        self.kind = kind


# --------------------------------------------------------------------------
# Filesystem-kind detection
# --------------------------------------------------------------------------

def is_unc_path(path):
    """True for a Windows UNC path.

    Checked on every platform, deliberately: a `\\\\server\\share` string is
    never a legitimate local path, and classifying it as network everywhere
    keeps the guard fail-closed regardless of where the config was authored.
    """
    text = str(path)
    return text.startswith("\\\\")


def is_network_kind(kind):
    """Fail-closed predicate: anything that is not proven local is network."""
    return kind != "local"


def _decode_mount_field(field):
    """Decode the octal escapes /proc/mounts uses for space/tab/newline."""
    out = []
    index = 0
    while index < len(field):
        char = field[index]
        if char == "\\" and index + 3 < len(field) + 1:
            chunk = field[index + 1:index + 4]
            if len(chunk) == 3 and all(c in "01234567" for c in chunk):
                out.append(chr(int(chunk, 8)))
                index += 4
                continue
        out.append(char)
        index += 1
    return "".join(out)


def parse_proc_mounts(text):
    """Parse /proc/mounts text into a list of (mountpoint, fstype) pairs."""
    entries = []
    for line in (text or "").splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        entries.append((_decode_mount_field(fields[1]), fields[2]))
    return entries


def _read_proc_mounts(proc_mounts_path):
    try:
        with open(proc_mounts_path, "r", encoding="utf-8",
                  errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _normalise_posix(path):
    text = str(path).replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    if len(text) > 1 and text.endswith("/"):
        text = text.rstrip("/") or "/"
    return text


def _mount_covers(mountpoint, path):
    """True when `path` lies at or under `mountpoint` on a component boundary."""
    if mountpoint == "/":
        return path.startswith("/")
    return path == mountpoint or path.startswith(mountpoint + "/")


def _detect_posix(path, mounts_text):
    entries = parse_proc_mounts(mounts_text)
    if not entries:
        return "unknown"
    target = _normalise_posix(os.path.abspath(str(path)) if not
                              str(path).startswith("/") else str(path))
    best_point = None
    best_type = None
    for raw_point, fstype in entries:
        point = _normalise_posix(raw_point)
        if not _mount_covers(point, target):
            continue
        if best_point is None or len(point) > len(best_point):
            best_point = point
            best_type = fstype
    if best_point is None:
        return "unknown"
    return "network" if best_type in NETWORK_FS_TYPES else "local"


def _default_drive_type_fn(root):
    import ctypes  # local import: absent/unusable outside Windows

    return int(ctypes.windll.kernel32.GetDriveTypeW(root))


def _detect_windows(path, drive_type_fn):
    text = str(path)
    if text.startswith("//"):
        return "network"
    drive = os.path.splitdrive(text)[0]
    if not drive or ":" not in drive:
        return "unknown"
    root = drive + "\\"
    fn = drive_type_fn or _default_drive_type_fn
    try:
        code = fn(root)
    except Exception:
        return "unknown"
    if code == DRIVE_REMOTE:
        return "network"
    if code in _LOCAL_DRIVE_TYPES:
        return "local"
    return "unknown"


def detect_fs_kind(path, os_name=None, mounts_text=_UNSET,
                   proc_mounts_path=None, drive_type_fn=None):
    """Classify the filesystem backing `path` as local, network, or unknown.

    POSIX: longest-prefix match against /proc/mounts, network iff the mount's
    fstype is in NETWORK_FS_TYPES. Windows: UNC prefix, else GetDriveTypeW.
    Anything unclassifiable returns "unknown", which callers must treat as
    network (see is_network_kind). Injection parameters exist for tests.
    """
    if is_unc_path(path):
        return "network"
    name = os_name if os_name is not None else os.name
    if name == "nt":
        return _detect_windows(path, drive_type_fn)
    if mounts_text is not _UNSET:
        return _detect_posix(path, mounts_text)
    return _detect_posix(
        path, _read_proc_mounts(proc_mounts_path or PROC_MOUNTS_PATH))


def assert_local_sqlite(db_path, os_name=None, mounts_text=_UNSET,
                        proc_mounts_path=None, drive_type_fn=None):
    """Raise NetworkFilesystemError unless `db_path` sits on local storage.

    Rules-to-code artifact for the WAL-over-network verdict: SQLite WAL uses an
    mmap-backed `-shm` index that is coherent only between processes on one
    host, so a shared-filesystem event store can lose writes or corrupt without
    error. Unknown filesystem kinds fail closed.
    """
    kind = detect_fs_kind(db_path, os_name=os_name, mounts_text=mounts_text,
                          proc_mounts_path=proc_mounts_path,
                          drive_type_fn=drive_type_fn)
    if not is_network_kind(kind):
        return
    detail = ("network filesystem" if kind == "network"
              else "filesystem of unknown kind (treated as network)")
    raise NetworkFilesystemError(
        "SQLite event store %s is on a %s. SQLite WAL requires a shared-memory "
        "index that is coherent only between processes on the same host, so a "
        "WAL database on a network share is unsafe. %s"
        % (str(db_path), detail, MOUNT_GUIDANCE),
        path=str(db_path), kind=kind)


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------

def percentile(values, pct):
    """Linear-interpolated percentile over an unsorted list of numbers."""
    if not values:
        raise ValueError("percentile of an empty sample")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (float(pct) / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _require_dir(path, label):
    if not os.path.isdir(str(path)):
        raise PreflightError("%s is not an existing directory: %s"
                             % (label, path))


def _probe_dir(shared_dir, *parts):
    target = os.path.join(str(shared_dir), PROBE_DIRNAME, *parts)
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        raise PreflightError("cannot create probe directory %s: %s"
                             % (target, exc))
    return target


def _fsync_dir(dirpath):
    """fsync a directory so a new entry is durable and peer-visible (POSIX)."""
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd = None
    try:
        fd = os.open(dirpath, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(fd)
    except OSError:
        return
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def write_durable_file(dirpath, name, payload):
    """Write `payload` to dirpath/name durably: fsync file, then parent dir."""
    tmp_path = os.path.join(dirpath, name + ".tmp")
    final_path = os.path.join(dirpath, name)
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, final_path)
    _fsync_dir(dirpath)
    return final_path


def revalidated_listdir(dirpath):
    """List a directory with client caches given every chance to revalidate.

    Python opens a fresh handle per call, and stat()ing the directory first
    forces an attribute lookup, which is what invalidates an NFS/SMB client's
    cached listing when the server-side mtime has moved.
    """
    try:
        os.stat(dirpath)
    except OSError:
        pass
    try:
        return list(os.listdir(dirpath))
    except OSError as exc:
        raise PreflightError("cannot list %s: %s" % (dirpath, exc))


def _await_visibility(dirpath, name, timeout_seconds, interval_seconds,
                      listdir_fn, clock):
    """Return seconds until `name` shows up in a revalidated listing."""
    started = clock()
    deadline = started + float(timeout_seconds)
    while True:
        if name in listdir_fn(dirpath):
            return clock() - started
        if clock() >= deadline:
            raise VisibilityTimeout(
                "probe %s not visible in %s after %.3fs; directory-visibility "
                "delay is unbounded for this share. %s"
                % (name, dirpath, float(timeout_seconds), MOUNT_GUIDANCE))
        time.sleep(float(interval_seconds))


def measure_visibility_delay(shared_dir, samples=DEFAULT_SAMPLES,
                             timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                             interval_seconds=DEFAULT_INTERVAL_SECONDS,
                             listdir_fn=None, clock=None):
    """Measure how long a written+fsynced file takes to become listable.

    Single-process mode (the CI path) exercises the whole code path and reports
    a near-zero delay; the number is only meaningful when the reader is a
    different box, which is what --peer-probe provides. Raises VisibilityTimeout
    rather than reporting a bounded delay it did not observe.
    """
    _require_dir(shared_dir, "shared-dir")
    count = int(samples)
    if count < 1:
        raise ValueError("samples must be >= 1")
    listdir = listdir_fn or revalidated_listdir
    now = clock or time.monotonic
    probe_dir = _probe_dir(shared_dir)
    delays = []
    try:
        for index in range(count):
            name = "visprobe-%d-%s.json" % (index, uuid.uuid4().hex)
            payload = json.dumps({"v": 1, "seq": index,
                                  "epoch_ms": int(time.time() * 1000)})
            write_durable_file(probe_dir, name, payload)
            delays.append(_await_visibility(probe_dir, name, timeout_seconds,
                                            interval_seconds, listdir, now))
    finally:
        _cleanup_probes(probe_dir, "visprobe-")
    return {
        "samples": count,
        "delays_seconds": [round(d, 6) for d in delays],
        "p50_seconds": round(percentile(delays, 50), 6),
        "p95_seconds": round(percentile(delays, 95), 6),
        "p99_seconds": round(percentile(delays, 99), 6),
        "max_seconds": round(max(delays), 6),
    }


def _cleanup_probes(probe_dir, prefix):
    try:
        names = os.listdir(probe_dir)
    except OSError:
        return
    for name in names:
        if not name.startswith(prefix):
            continue
        try:
            os.remove(os.path.join(probe_dir, name))
        except OSError:
            continue


def measure_clock_skew(shared_dir, samples=3):
    """Measure local-clock vs share-server-clock skew, in seconds.

    The mtime stamped on a freshly written file is assigned by whichever host
    owns the filesystem, so (local_now - file_mtime) is a direct estimate of
    skew plus a small write latency. Positive means the local clock is ahead.
    """
    _require_dir(shared_dir, "shared-dir")
    count = int(samples)
    if count < 1:
        raise ValueError("samples must be >= 1")
    probe_dir = _probe_dir(shared_dir)
    skews = []
    try:
        for index in range(count):
            name = "skewprobe-%d-%s.json" % (index, uuid.uuid4().hex)
            path = write_durable_file(
                probe_dir, name,
                json.dumps({"v": 1, "epoch_ms": int(time.time() * 1000)}))
            local_now = time.time()
            try:
                mtime = os.stat(path).st_mtime
            except OSError as exc:
                raise PreflightError("cannot stat probe %s: %s" % (path, exc))
            skews.append(local_now - mtime)
    finally:
        _cleanup_probes(probe_dir, "skewprobe-")
    return {
        "samples_seconds": [round(s, 6) for s in skews],
        "median_seconds": round(statistics.median(skews), 6),
        "max_abs_seconds": round(max(abs(s) for s in skews), 6),
        "method": "server-mtime",
    }


# --------------------------------------------------------------------------
# Two-sided (--peer-probe) mode
# --------------------------------------------------------------------------

def write_peer_probe(shared_dir, peer_id, seq, now_ms=None):
    """Publish one probe record other boxes can observe."""
    _require_dir(shared_dir, "shared-dir")
    peer_dir = _probe_dir(shared_dir, PEER_DIRNAME)
    record = {
        "v": 1,
        "peer_id": str(peer_id),
        "seq": int(seq),
        "epoch_ms": int(now_ms if now_ms is not None else time.time() * 1000),
    }
    name = "peerprobe-%s-%d-%s.json" % (
        _safe_component(peer_id), int(seq), uuid.uuid4().hex)
    return write_durable_file(peer_dir, name, json.dumps(record))


def _safe_component(text):
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in str(text)]
    return ("".join(keep) or "peer")[:64]


def observe_peer_delays(shared_dir, self_peer_id, now_ms=None):
    """Report observed visibility delay for every OTHER peer's probe records.

    delay = local_now - record.epoch_ms, so it conflates transit delay with
    clock skew between the two boxes; pair it with measure_clock_skew to
    separate them. Corrupt records are skipped, never fatal.
    """
    _require_dir(shared_dir, "shared-dir")
    peer_dir = os.path.join(str(shared_dir), PROBE_DIRNAME, PEER_DIRNAME)
    if not os.path.isdir(peer_dir):
        return []
    observed_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    results = []
    for name in sorted(revalidated_listdir(peer_dir)):
        if not name.startswith("peerprobe-") or not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(peer_dir, name), "r",
                      encoding="utf-8") as handle:
                record = json.load(handle)
            peer_id = str(record["peer_id"])
            epoch_ms = int(record["epoch_ms"])
            seq = int(record.get("seq", 0))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if peer_id == str(self_peer_id):
            continue
        results.append({
            "peer_id": peer_id,
            "seq": seq,
            "delay_seconds": round((observed_ms - epoch_ms) / 1000.0, 6),
        })
    return results


def run_peer_probe(shared_dir, peer_id, samples=DEFAULT_SAMPLES,
                   wait_seconds=0.0, interval_seconds=0.05):
    """Publish this box's probes, then report what peers' probes look like."""
    for seq in range(int(samples)):
        write_peer_probe(shared_dir, peer_id, seq)
    deadline = time.monotonic() + float(wait_seconds)
    observed = observe_peer_delays(shared_dir, peer_id)
    while not observed:
        if time.monotonic() >= deadline:
            break
        time.sleep(float(interval_seconds))
        observed = observe_peer_delays(shared_dir, peer_id)
    delays = [rec["delay_seconds"] for rec in observed]
    summary = {
        "peer_id": str(peer_id),
        "published": int(samples),
        "observed_records": len(observed),
        "peers_seen": sorted({rec["peer_id"] for rec in observed}),
    }
    if delays:
        summary["p50_seconds"] = round(percentile(delays, 50), 6)
        summary["p99_seconds"] = round(percentile(delays, 99), 6)
        summary["max_seconds"] = round(max(delays), 6)
    return summary


# --------------------------------------------------------------------------
# Aggregate preflight
# --------------------------------------------------------------------------

def _finding(finding_id, severity, detail):
    return {"id": finding_id, "severity": severity, "detail": detail}


def run_preflight(shared_dir, db_path, samples=DEFAULT_SAMPLES,
                  settle_seconds=DEFAULT_SETTLE_SECONDS,
                  max_skew_seconds=DEFAULT_MAX_SKEW_SECONDS,
                  peer_id=None, peer_wait_seconds=0.0,
                  fs_kind_fn=None, visibility_fn=None, clock_skew_fn=None):
    """Run every Inc 0 check and return a JSON-serialisable report dict."""
    _require_dir(shared_dir, "shared-dir")
    kind_of = fs_kind_fn or detect_fs_kind
    measure_visibility = visibility_fn or measure_visibility_delay
    measure_skew = clock_skew_fn or measure_clock_skew

    findings = []
    db_kind = kind_of(str(db_path))
    if is_network_kind(db_kind):
        findings.append(_finding(
            "DB-ON-NETWORK-FS", "error",
            "event-store db %s resolves to fs kind '%s'; SQLite WAL is only "
            "coherent on local storage. %s"
            % (str(db_path), db_kind, MOUNT_GUIDANCE)))

    visibility = None
    try:
        visibility = measure_visibility(shared_dir, samples=samples)
    except VisibilityTimeout as exc:
        findings.append(_finding("VISIBILITY-UNBOUNDED", "error", str(exc)))
    except PreflightError as exc:
        findings.append(_finding("VISIBILITY-PROBE-FAILED", "error", str(exc)))
    if visibility is not None:
        p99 = float(visibility["p99_seconds"])
        if p99 > float(settle_seconds):
            findings.append(_finding(
                "VISIBILITY-DELAY-EXCEEDS-SETTLE", "error",
                "measured p99 visibility delay %.3fs exceeds settle window "
                "%.3fs; a fold-decided claim can double-grant. %s"
                % (p99, float(settle_seconds), MOUNT_GUIDANCE)))

    clock_skew = None
    try:
        clock_skew = measure_skew(shared_dir)
    except PreflightError as exc:
        findings.append(_finding("CLOCK-SKEW-PROBE-FAILED", "error", str(exc)))
    if clock_skew is not None:
        max_abs = float(clock_skew["max_abs_seconds"])
        if max_abs > float(max_skew_seconds):
            findings.append(_finding(
                "CLOCK-SKEW-EXCEEDS-BOUND", "error",
                "measured clock skew %.3fs exceeds max_skew %.3fs; lease TTLs "
                "cannot be bounded" % (max_abs, float(max_skew_seconds))))

    report = {
        "v": 1,
        "ok": not findings,
        "findings": findings,
        "db": {"path": str(db_path), "fs_kind": db_kind},
        "shared_dir": {"path": str(shared_dir),
                       "fs_kind": kind_of(str(shared_dir))},
        "settle_seconds": float(settle_seconds),
        "max_skew_seconds": float(max_skew_seconds),
        "visibility": visibility,
        "clock_skew": clock_skew,
    }
    if peer_id is not None:
        report["peer_probe"] = run_peer_probe(
            shared_dir, peer_id, samples=samples,
            wait_seconds=peer_wait_seconds)
    return report


def render_report(report):
    """Render an ASCII summary of a preflight report."""
    lines = []
    lines.append("multibox preflight: %s" % ("OK" if report["ok"] else "FINDINGS"))
    lines.append("  db          : %s [%s]"
                 % (report["db"]["path"], report["db"]["fs_kind"]))
    lines.append("  shared-dir  : %s [%s]"
                 % (report["shared_dir"]["path"],
                    report["shared_dir"]["fs_kind"]))
    visibility = report.get("visibility")
    if visibility:
        lines.append("  visibility  : p50=%.4fs p95=%.4fs p99=%.4fs max=%.4fs "
                     "(n=%d, settle=%.2fs)"
                     % (visibility["p50_seconds"], visibility["p95_seconds"],
                        visibility["p99_seconds"], visibility["max_seconds"],
                        visibility["samples"], report["settle_seconds"]))
    else:
        lines.append("  visibility  : not measured")
    skew = report.get("clock_skew")
    if skew:
        lines.append("  clock skew  : median=%.4fs max_abs=%.4fs (bound=%.2fs)"
                     % (skew["median_seconds"], skew["max_abs_seconds"],
                        report["max_skew_seconds"]))
    else:
        lines.append("  clock skew  : not measured")
    peer = report.get("peer_probe")
    if peer:
        lines.append("  peer probe  : id=%s published=%d observed=%d peers=%s"
                     % (peer["peer_id"], peer["published"],
                        peer["observed_records"],
                        ",".join(peer["peers_seen"]) or "none"))
    for finding in report["findings"]:
        lines.append("  [%s] %s: %s"
                     % (finding["severity"].upper(), finding["id"],
                        finding["detail"]))
    return "\n".join(lines)


def build_parser():
    """Build the CLI argument parser (unknown flags exit 2 via argparse)."""
    parser = argparse.ArgumentParser(
        prog="multibox_preflight.py",
        description="Multibox preflight probe and network-filesystem guard.")
    parser.add_argument("--check", action="store_true",
                        help="Run all checks (default action).")
    parser.add_argument("--shared-dir", required=False,
                        help="Shared coordination directory to probe.")
    parser.add_argument("--db", required=False,
                        help="Path to the local SQLite event store.")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES,
                        help="Probe samples (default: %d)." % DEFAULT_SAMPLES)
    parser.add_argument("--settle-seconds", type=float,
                        default=DEFAULT_SETTLE_SECONDS,
                        help="Settle window the p99 delay must fit inside.")
    parser.add_argument("--max-skew-seconds", type=float,
                        default=DEFAULT_MAX_SKEW_SECONDS,
                        help="Maximum tolerated local-vs-server clock skew.")
    parser.add_argument("--peer-probe", action="store_true",
                        help="Two-sided mode: publish and observe peer probes.")
    parser.add_argument("--peer-id", default=None,
                        help="Identity used for --peer-probe records.")
    parser.add_argument("--peer-wait-seconds", type=float, default=0.0,
                        help="Seconds to wait for a peer's probes to appear.")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON.")
    return parser


def main(argv=None):
    """CLI entry point. Returns 0 clean, 1 findings, 2 error."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.shared_dir or not args.db:
        sys.stderr.write("error: --shared-dir and --db are required\n")
        return 2
    peer_id = None
    if args.peer_probe:
        peer_id = args.peer_id or ("%s-%d" % (_safe_component(
            os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME")
            or "box"), os.getpid()))
    try:
        report = run_preflight(
            shared_dir=args.shared_dir, db_path=args.db, samples=args.samples,
            settle_seconds=args.settle_seconds,
            max_skew_seconds=args.max_skew_seconds,
            peer_id=peer_id, peer_wait_seconds=args.peer_wait_seconds)
    except ValueError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    except PreflightError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_report(report) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
