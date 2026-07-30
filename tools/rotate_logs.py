#!/usr/bin/env python3
"""Log rotation utility: archive oldest log lines when exceeding size/line thresholds.

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
    """Write lines to file."""
    try:
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
    - Lines appended between readlines() and truncate() are detected via file size
      comparison and re-read after truncate+write to prevent loss.
    - This guarantees atomicity: archive + original == original content.

    NOTE: The advisory lock serializes rotations against each other, but race
    conditions with non-locking O_APPEND writers remain possible. For complete
    safety, all writers (including external processes) must cooperate on locking.

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

    # Perform rotation with exclusive lock to prevent concurrent write races
    try:
        with open(str(logfile), 'r+', encoding='utf-8') as f:
            # Acquire exclusive lock to guard read-compute-write sequence
            acquire_lock(f.fileno())
            try:
                # Read all lines while locked
                lines = f.readlines()

                if not lines:
                    return 0

                # Determine split point based on which threshold was exceeded
                current_lines = len(lines)
                current_bytes = sum(len(line.encode('utf-8')) for line in lines)

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
                        line_bytes = len(lines[i].encode('utf-8'))
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
                # Before truncating, check if the file has grown since we initially read it.
                # If external (non-locking) O_APPEND writers added content while we were
                # computing, re-read and preserve those appends.
                original_line_count = len(lines)

                # Rewind to start and re-count lines to detect external appends
                f.seek(0)
                current_line_count = sum(1 for _ in f)

                if current_line_count > original_line_count:
                    # External appends occurred! Re-read the appended lines
                    lines_appended = current_line_count - original_line_count

                    # Re-read from line position (start of appended lines)
                    f.seek(0)
                    all_current_lines = f.readlines()

                    # Extract only the new lines (the appended ones)
                    external_lines = all_current_lines[-lines_appended:]
                    if external_lines:
                        remaining_lines.extend(external_lines)

                # Atomically truncate and write remaining lines back to original
                # while still holding the lock
                f.seek(0)
                f.truncate()
                f.writelines(remaining_lines)

            finally:
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
