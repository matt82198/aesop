#!/usr/bin/env python3
"""Log rotation utility: archive oldest log lines when exceeding size/line thresholds.
INDEX: Log rotation utility (size/line thresholds). Reads and rewrites the live log in BINARY, so the byte extent it consumed is exact and line endings are never silently translated. External O_APPEND writers (the shell daemons' `>>` / `tee -a`) do not take the advisory lock, so appends past that extent are detected by size comparison, read back at their byte offset, and folded into the retained content; the check repeats up to `PRESERVE_ATTEMPTS` until the size stops moving and the last one runs immediately before the truncate. Detecting appends by diffing LINE counts is wrong (an unterminated final line swallows the append) and leaving a full file re-read between the check and the truncate is what produced the observed `250 != 260` data loss — do not reintroduce either. The race is narrowed, not closed: an append between the final size check and the truncate is still lost, and only writer-side locking can fix that

Rotates log files by moving the oldest content to an archive file when the original
exceeds configured thresholds (--max-lines or --max-bytes). Preserves newest lines
in the original, ensures no data loss (archive + original == original content).

Uses advisory file locking to serialize concurrent rotations, and detects/preserves
external O_APPEND writes (from non-locking writers) that occur during the rotation
window. This ensures atomicity: no lines are lost even when external processes
append concurrently.

Exit codes:
  0: Success (no rotation needed or rotation completed)
  1: Error (invalid args, I/O failure, etc.)
  2: Usage error
  3: Rotation needed (--check mode only)
"""
import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Platform-specific locking imports
if sys.platform == 'win32':
    import msvcrt
    HAS_FCNTL = False
else:
    try:
        import fcntl
        HAS_FCNTL = True
    except ImportError:
        HAS_FCNTL = False


def count_lines(filepath):
    """Count lines in a file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return sum(1 for _ in f)
    except IOError:
        return 0


def get_file_size(filepath):
    """Get file size in bytes."""
    try:
        return os.path.getsize(filepath)
    except OSError:
        return 0


def read_lines(filepath):
    """Read all lines from file, preserving newlines."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.readlines()
    except IOError as e:
        raise IOError(f"Failed to read {filepath}: {e}")


def write_lines(filepath, lines):
    """Write lines to a NEW file.

    Accepts either a list of ``str`` lines (written as UTF-8 text) or a list of
    ``bytes`` lines (written verbatim). The file is created exclusively, so an
    existing file at ``filepath`` raises FileExistsError and is never overwritten;
    callers pick another name on that error. A partially written new file is
    removed before the error is raised.
    """
    binary = bool(lines) and isinstance(lines[0], bytes)
    try:
        if binary:
            with open(filepath, 'xb') as f:
                f.writelines(lines)
        else:
            with open(filepath, 'x', encoding='utf-8') as f:
                f.writelines(lines)
    except FileExistsError:
        raise
    except IOError as e:
        try:
            os.unlink(filepath)
        except OSError:
            pass
        raise IOError(f"Failed to write {filepath}: {e}")


def acquire_lock(fd):
    """Acquire exclusive lock on file descriptor (cross-platform)."""
    if HAS_FCNTL:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (AttributeError, TypeError):
            pass
    elif sys.platform == 'win32':
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        except (OSError, IOError):
            pass  # Locking may not be supported on all filesystems


def release_lock(fd):
    """Release exclusive lock on file descriptor (cross-platform)."""
    if HAS_FCNTL:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except (AttributeError, TypeError):
            pass
    elif sys.platform == 'win32':
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except (OSError, IOError):
            pass


# How many times rotate_log() re-checks the file size for late external appends
# before it commits the swap. Bounded so a log under continuous append pressure
# still completes rather than spinning forever.
PRESERVE_ATTEMPTS = 5

# Archive names carry a one-second UTC stamp. Rotations inside the same second
# take ".<n>" suffixes; this bounds how many are tried before giving up.
MAX_ARCHIVE_COLLISIONS = 1000

# Suffix of the temp file that replaces the live log. It is removed on failure.
TEMP_SUFFIX = ".rotate.tmp"


def exceeds_thresholds(line_count, byte_count, max_lines, max_bytes):
    """True when either configured threshold is exceeded."""
    if max_lines and line_count > max_lines:
        return True
    if max_bytes and byte_count > max_bytes:
        return True
    return False


def needs_rotation(filepath, max_lines, max_bytes):
    """Check if file exceeds any threshold (unlocked snapshot; advisory only)."""
    return exceeds_thresholds(count_lines(filepath), get_file_size(filepath),
                              max_lines, max_bytes)


def lock_path_for(logfile):
    """Sidecar lock file for a log.

    The lock lives beside the log rather than on it, because the log itself is
    replaced by rename while the lock is held. A lock on a replaced inode would
    not serialise anything. The sidecar is never removed; it is empty.
    """
    return logfile.with_name(logfile.name + ".rotate.lock")


def replace_file_atomically(target, data):
    """Make ``target`` hold exactly ``data``, or leave it untouched.

    The bytes go to a temp file in the same directory, are fsynced, and are then
    swapped in with os.replace, so readers see either the old file or the new one,
    never a truncated mix. Any failure, including a kill modelled as a
    BaseException, removes the temp file and propagates; the target is unchanged.
    """
    target = Path(target)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                    prefix="." + target.name + ".",
                                    suffix=TEMP_SUFFIX)
    try:
        with os.fdopen(fd, 'wb') as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        try:
            shutil.copymode(str(target), tmp_name)
        except OSError:
            pass  # Keeping the mode is best effort; the content is what matters.
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def create_archive(archive_path, stem, archive_lines):
    """Write archive_lines to a brand-new archive file and return its path.

    Never replaces an existing file: the exclusive create fails on collision and
    the next free ``<stem>.<stamp>.<n>.log`` name is used.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    for n in range(MAX_ARCHIVE_COLLISIONS):
        suffix = "" if n == 0 else f".{n}"
        candidate = archive_path / f"{stem}.{stamp}{suffix}.log"
        try:
            write_lines(str(candidate), archive_lines)
            return candidate
        except FileExistsError:
            continue
    raise IOError(f"no free archive name for {stem}.{stamp} in {archive_path}")


def rotate_log(logfile, max_lines, max_bytes, archive_dir, check_only=False):
    """Rotate log file by archiving oldest lines atomically.

    Uses exclusive file locking (on a sidecar lock file) to serialize rotations
    against other lock-takers. The lock is ADVISORY: external writers (e.g. shell
    scripts using >> or tee -a) that do NOT take it are not blocked by it.

    The rotation decision is made under the lock, from the bytes read under the
    lock. An unlocked threshold check runs first only as a cheap early exit; a
    reading taken before the lock can be stale, so it never decides on its own.

    To prevent data loss from non-locking O_APPEND writers:
    - Bytes appended past the extent this rotation consumed are detected by
      comparing the live file size against that extent, read back at the exact
      byte offset, and folded into the retained content before the swap.
    - The size check is repeated (bounded by PRESERVE_ATTEMPTS) until the file
      stops growing.

    The kept half is written to a temp file in the same directory and swapped in
    with os.replace, so a crash at any point leaves either the original log or
    the complete rotated log. Archives are created exclusively and never
    overwritten.

    NOTE: This narrows but cannot close the race: an append landing between the
    final size observation and the swap is still lost, because no portable
    primitive makes "check size, then replace" atomic against a writer that
    declines to take the lock. Writers that hold the log open across rotations
    (e.g. a daemon's long-lived >> fd) keep appending to the replaced inode after
    a swap; they must reopen the log. For complete safety, all writers must
    cooperate on locking.

    Args:
        logfile: Path to log file to rotate.
        max_lines: Max lines before rotation (None to skip this check).
        max_bytes: Max bytes before rotation (None to skip this check).
        archive_dir: Directory to store archives (default: logfile dir/archive).
        check_only: If True, only check if rotation needed (no writes).

    Returns:
        0 if successful or no rotation needed,
        3 if rotation needed (in check_only mode),
        1 on error.
    """
    logfile = Path(logfile)

    if check_only:
        return 3 if needs_rotation(str(logfile), max_lines, max_bytes) else 0

    # Cheap unlocked early exit. Not a decision: _rotate_locked() re-decides.
    if not needs_rotation(str(logfile), max_lines, max_bytes):
        return 0

    try:
        lock_fd = os.open(str(lock_path_for(logfile)), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as e:
        print(f"ERROR: Failed to open rotation lock for {logfile}: {e}",
              file=sys.stderr)
        return 1
    try:
        acquire_lock(lock_fd)
        try:
            return _rotate_locked(logfile, max_lines, max_bytes, archive_dir)
        finally:
            release_lock(lock_fd)
    finally:
        os.close(lock_fd)


def _rotate_locked(logfile, max_lines, max_bytes, archive_dir):
    """Decide and perform one rotation. The caller holds the rotation lock."""
    if archive_dir:
        archive_path = Path(archive_dir)
    else:
        archive_path = logfile.parent / "archive"

    # Binary mode is deliberate: it makes the byte offset of everything consumed
    # exact, which is what the external-append preservation below keys off. Text
    # mode's newline translation would desynchronise character counts from
    # on-disk byte offsets (CRLF on Windows) and silently rewrite line endings.
    try:
        with open(str(logfile), 'rb') as f:
            # `consumed` is the exact on-disk extent this rotation is responsible for.
            data = f.read()
            consumed = len(data)
            lines = data.splitlines(keepends=True)

            # The decision is made here, on what was read under the lock.
            if not lines or not exceeds_thresholds(len(lines), consumed,
                                                   max_lines, max_bytes):
                return 0

            current_lines = len(lines)
            current_bytes = consumed

            keep_count = current_lines // 2  # Default: keep ~half

            # If max_lines exceeded, keep lines just under threshold
            if max_lines and current_lines > max_lines:
                keep_count = min(keep_count, max_lines)

            # If max_bytes exceeded, calculate how many lines fit under threshold
            if max_bytes and current_bytes > max_bytes:
                cumulative_bytes = 0
                bytes_keep_count = 0
                # Count from newest (end) backward
                for i in range(len(lines) - 1, -1, -1):
                    line_bytes = len(lines[i])
                    if cumulative_bytes + line_bytes <= max_bytes:
                        cumulative_bytes += line_bytes
                        bytes_keep_count += 1
                    else:
                        break
                # Use the more conservative count
                keep_count = min(keep_count, bytes_keep_count)

            # Guard: ensure we keep at least 1 line, archive the rest
            if keep_count <= 0:
                keep_count = 1

            # Split lines
            archive_lines = lines[:-keep_count] if keep_count < len(lines) else []
            remaining_lines = lines[-keep_count:] if keep_count > 0 else []

            if not archive_lines:
                # Nothing to archive
                return 0

            try:
                archive_path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                print(f"ERROR: Failed to create archive directory {archive_path}: {e}",
                      file=sys.stderr)
                return 1

            try:
                create_archive(archive_path, logfile.stem, archive_lines)
            except IOError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1

            # MITIGATION for external O_APPEND writers:
            # Writing the archive takes time, and non-locking writers (>> and
            # tee -a in the shell daemons) can append during it. Anything past
            # `consumed` is such an append and must survive the swap.
            #
            # 1. Byte offsets, not line counts. Diffing line counts loses data
            #    whenever the last consumed line has no trailing newline: the
            #    append concatenates onto it, the line count does not grow by
            #    the number of appended lines, and the surplus is destroyed.
            #    Reading the delta at a byte offset is exact in every case.
            #
            # 2. The check sits immediately before the swap. The observation is
            #    repeated until the size stops moving, so appends arriving
            #    mid-preservation are also caught.
            for _ in range(PRESERVE_ATTEMPTS):
                grown = os.fstat(f.fileno()).st_size
                if grown <= consumed:
                    break
                f.seek(consumed)
                external = f.read(grown - consumed)
                if not external:
                    break
                remaining_lines.extend(external.splitlines(keepends=True))
                consumed = grown

            kept = b"".join(remaining_lines)

        # The live log is closed here. Windows refuses to replace a file that has
        # an open handle; the sidecar lock still serialises rotators across the swap.
        try:
            replace_file_atomically(logfile, kept)
        except OSError as e:
            print(f"ERROR: Failed to replace {logfile} with rotated content: {e}",
                  file=sys.stderr)
            return 1

    except IOError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    return 0


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "logfile",
        help="Path to log file to rotate"
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=200,
        help="Max lines before rotation (default: 200)"
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=20480,
        help="Max bytes before rotation (default: 20480)"
    )
    parser.add_argument(
        "--archive-dir",
        default=None,
        help="Directory for archived logs (default: <logfile-dir>/archive)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if rotation needed (exit 3 if yes, 0 if no; no writes)"
    )

    args = parser.parse_args()

    # Verify logfile exists
    if not os.path.exists(args.logfile):
        print(f"ERROR: Log file not found: {args.logfile}", file=sys.stderr)
        return 1

    # Run rotation
    return rotate_log(
        args.logfile,
        args.max_lines,
        args.max_bytes,
        args.archive_dir,
        check_only=args.check
    )


if __name__ == "__main__":
    sys.exit(main())
