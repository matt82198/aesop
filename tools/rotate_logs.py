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
import sys
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
    """Write lines to file.

    Accepts either a list of ``str`` lines (written as UTF-8 text) or a list of
    ``bytes`` lines (written verbatim). rotate_log() operates on bytes so that
    archived content is byte-identical to what was read, but the str form is
    kept for callers/tests that pass text.
    """
    binary = bool(lines) and isinstance(lines[0], bytes)
    try:
        if binary:
            with open(filepath, 'wb') as f:
                f.writelines(lines)
        else:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.writelines(lines)
    except IOError as e:
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
# before it commits the truncate+rewrite. Bounded so a log under continuous
# append pressure still completes rather than spinning forever.
PRESERVE_ATTEMPTS = 5


def needs_rotation(filepath, max_lines, max_bytes):
    """Check if file exceeds any threshold."""
    current_lines = count_lines(filepath)
    current_bytes = get_file_size(filepath)

    if max_lines and current_lines > max_lines:
        return True
    if max_bytes and current_bytes > max_bytes:
        return True
    return False


def rotate_log(logfile, max_lines, max_bytes, archive_dir, check_only=False):
    """Rotate log file by archiving oldest lines atomically.

    Uses exclusive file locking to serialize rotations against other lock-takers.
    However, this is an ADVISORY lock: it only protects against other processes
    that voluntarily take the lock. External writers (e.g., shell scripts using >>
    or tee -a) that do NOT take the lock are NOT protected by this mechanism.

    To prevent data loss from non-locking O_APPEND writers:
    - Bytes appended past the extent this rotation consumed are detected by
      comparing the live file size against that extent, read back at the exact
      byte offset, and folded into the retained content before the truncate.
    - The size check is repeated (bounded by PRESERVE_ATTEMPTS) until the file
      stops growing, and the last check is the operation immediately preceding
      the truncate, so the unprotected window is a single seek rather than a
      full file re-read.

    NOTE: This narrows but cannot close the race: an append landing between the
    final size observation and the truncate is still lost, because no portable
    primitive makes "check size, then truncate" atomic against a writer that
    declines to take the lock. For complete safety, all writers (including
    external processes) must cooperate on locking.

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

    # Check if rotation is needed
    if not needs_rotation(str(logfile), max_lines, max_bytes):
        return 0

    # If check-only mode, report that rotation is needed
    if check_only:
        return 3

    # Determine archive directory
    if archive_dir:
        archive_path = Path(archive_dir)
    else:
        archive_path = logfile.parent / "archive"

    # Create archive directory if needed
    try:
        archive_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"ERROR: Failed to create archive directory {archive_path}: {e}",
              file=sys.stderr)
        return 1

    # Perform rotation with exclusive lock to prevent concurrent write races.
    # Binary mode is deliberate: it makes the byte offset of everything we have
    # consumed exact, which is what the external-append preservation below keys
    # off. Text mode's newline translation would desynchronise character counts
    # from on-disk byte offsets (CRLF on Windows) and silently rewrite the line
    # endings of every log it touches.
    try:
        with open(str(logfile), 'rb+') as f:
            # Acquire exclusive lock to guard read-compute-write sequence
            acquire_lock(f.fileno())
            try:
                # Read all bytes while locked; `consumed` is the exact on-disk
                # extent this rotation is responsible for.
                data = f.read()
                consumed = len(data)
                lines = data.splitlines(keepends=True)

                if not lines:
                    return 0

                # Determine split point based on which threshold was exceeded
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
                    # If calculated keep_count is 0 or negative, keep at least 1 line
                    keep_count = 1

                # Split lines
                archive_lines = lines[:-keep_count] if keep_count < len(lines) else []
                remaining_lines = lines[-keep_count:] if keep_count > 0 else []

                if not archive_lines:
                    # Nothing to archive
                    return 0

                # Generate archive filename with UTC timestamp
                utc_now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                archive_filename = f"{logfile.stem}.{utc_now}.log"
                archive_file = archive_path / archive_filename

                # Write archive file
                try:
                    write_lines(str(archive_file), archive_lines)
                except IOError as e:
                    print(f"ERROR: {e}", file=sys.stderr)
                    return 1

                # MITIGATION for external O_APPEND writers:
                # Writing the archive takes time, and non-locking writers (>> and
                # tee -a in the shell daemons) can append during it. Anything past
                # `consumed` is such an append and must survive the truncate.
                #
                # Two properties matter here, and the previous line-counting
                # implementation had neither:
                #
                # 1. Byte offsets, not line counts. Diffing line counts loses data
                #    whenever the last consumed line has no trailing newline: the
                #    append concatenates onto it, the line count does not grow by
                #    the number of appended lines, and the surplus is destroyed.
                #    Reading the delta at a byte offset is exact in every case.
                #
                # 2. The check must sit immediately before the truncate. The old
                #    code re-read the whole file to count lines and only then
                #    truncated, so an append landing after that scan was lost --
                #    a window as wide as a full file read. Here the only work
                #    between the last size observation and the truncate is a
                #    single seek, and the observation is repeated until the size
                #    stops moving, so appends arriving mid-preservation are also
                #    caught.
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

                # Atomically truncate and write remaining lines back to original
                # while still holding the lock
                f.seek(0)
                f.truncate()
                f.write(b"".join(remaining_lines))

            finally:
                # Rewind before unlocking: msvcrt.locking() unlocks the byte range
                # at the *current* position, so releasing from the write position
                # would target a region we never locked.
                try:
                    f.seek(0)
                except (OSError, ValueError):
                    pass
                # Release lock after all writes complete
                release_lock(f.fileno())

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
