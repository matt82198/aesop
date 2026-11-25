#!/usr/bin/env python3
"""Flush spooled receipts to GitHub once their shas exist on the remote.
INDEX: Receipt-gate spool-and-flush (increment 2; prunes stale *.flush.log by --max-age-days) -- for each JSON envelope in the
spool directory state/receipts/spool/, check if the head sha exists on GitHub
(`gh api repos/<slug>/commits/<sha>` 200); if yes, call the same post_receipt
function (check-run then comment fallback) to publish; on success delete the spool
file, on failure keep it (except stale ones deleted by --max-age-days). Stale
*.flush.log files in the spool directory are pruned by the same --max-age-days rule
(never following symlinks). Idempotent,
exit 0 always unless --strict. Meant to be called (a) at the START of check_emit_receipt
in pre-push-policy.sh (after a push, to flush any prior spool files now that shas exist),
and (b) in tools/pr_sweep.py (session-independent actor under the watchdog). stdlib-only.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))

import emit_receipt as emit  # noqa: E402
import receipt_common as rc  # noqa: E402

ReceiptError = rc.ReceiptError


def _default_gh_runner(args):
    # AESOP_GH_BIN overrides the gh executable (test seam: a stub gh on disk).
    gh_bin = os.environ.get("AESOP_GH_BIN") or "gh"
    try:
        res = subprocess.run([gh_bin] + list(args), capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    except OSError as exc:
        # gh missing or not executable: report it as a failed call, never crash the
        # poller (the detached flusher must always reach its log line).
        return 127, "", "gh not runnable: %s" % exc
    return res.returncode, res.stdout or "", res.stderr or ""


def spool_dir_path(repo_path, environ=None):
    """Resolve the primary spool directory path from a repo path.

    Uses the new worktree-durable location via rc.resolve_spool_dir().
    """
    return Path(rc.resolve_spool_dir(repo=repo_path, environ=environ))


def sha_exists_on_github(sha, slug, gh_runner):
    """Check if a commit sha exists on GitHub. Return True if 200, False otherwise."""
    code, out, err = gh_runner(["api", "repos/%s/commits/%s" % (slug, sha)])
    return code == 0


def flush_spooled_receipt(spool_file, slug, gh_runner, strict=False):
    """Post a spooled receipt envelope. Return (success, reason).

    On success: delete the spool file, return (True, "posted via X")
    On 404/missing sha: keep file, return (False, "sha not on github yet")
    On other failure: keep file, return (False, "error message") or exit 2 if strict
    """
    try:
        envelope = json.loads(spool_file.read_text(encoding="utf-8"))
    except Exception as e:
        if strict:
            print("ERROR: could not read spool file %s: %s" % (spool_file, e), file=sys.stderr)
            return False, "malformed spool file"
        return False, "malformed spool file (kept)"

    receipt = envelope.get("receipt", {})
    head_sha = receipt.get("head_sha")
    if not head_sha:
        if strict:
            print("ERROR: spool file %s has no head_sha" % spool_file, file=sys.stderr)
            return False, "no head_sha"
        return False, "no head_sha (kept)"

    # Check if sha exists on GitHub
    if not sha_exists_on_github(head_sha, slug, gh_runner):
        return False, "sha not on github yet (kept)"

    # Sha exists; try to post the receipt
    try:
        channel = emit.post_receipt(envelope, slug, gh_runner)
        # post_receipt succeeded; delete the spool file
        try:
            spool_file.unlink()
        except OSError as e:
            if strict:
                print("ERROR: could not delete spool file %s: %s" % (spool_file, e), file=sys.stderr)
                return False, "could not delete after posting"
            return False, "posted but could not delete spool file"
        return True, "posted via %s" % channel
    except ReceiptError as e:
        if strict:
            print("ERROR: could not post spooled receipt %s: %s" % (spool_file, e), file=sys.stderr)
            return False, str(e)
        return False, "post failed: %s (kept)" % str(e)


FLUSH_LOG_GLOB = "*.flush.log"


def _max_age_days_arg(text):
    """argparse type for --max-age-days: a whole number of days, 0 or more.

    0 expires everything older than now (json spools and flush logs alike, the same `age > window`
    rule). Negative values would make the window point into the future and prune fresh logs; non-finite
    or fractional values are not whole days. All are refused at parse time, before anything is pruned.
    """
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number of days, got %r" % text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be 0 or more, got %d" % value)
    return value


def _is_link_or_junction(path):
    """True when path is a symlink, or a Windows directory junction. Never follows the path."""
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)  # Python 3.12+ on Windows
    return bool(is_junction is not None and is_junction(str(path)))


def prune_flush_logs(spool_dirs, now, max_age_delta, dry_run=False):
    """Prune top-level *.flush.log files older than max_age_delta. Return (pruned_count, failures).

    Same age rule as spooled receipts. Only exact top-level matches are candidates (no recursion).
    Symlinks are never followed or removed. A spool dir that is itself a symlink or junction is refused
    (reported, not followed). A file that cannot be stat'd or removed is reported,
    skipped, and returned in failures; the caller decides whether that is fatal (--strict only).
    """
    pruned = 0
    failures = []
    seen = set()
    for spool_dir in spool_dirs:
        if _is_link_or_junction(spool_dir):
            msg = "refused to prune spool directory %s: it is a symlink or junction (not followed)" % spool_dir
            print(msg)
            failures.append(msg)
            continue
        if not spool_dir.is_dir():
            continue
        key = str(spool_dir.resolve())
        if key in seen:
            continue
        seen.add(key)
        for log in sorted(spool_dir.glob(FLUSH_LOG_GLOB)):
            try:
                if log.is_symlink():
                    continue
                mtime = datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
            except OSError as e:
                msg = "could not stat flush log %s: %s (skipped)" % (log, e)
                print(msg)
                failures.append(msg)
                continue
            age = now - mtime
            if age <= max_age_delta:
                continue
            if dry_run:
                print("would prune flush log (age %.1f days): %s" % (age.total_seconds() / 86400, log.name))
                continue
            try:
                log.unlink()
            except OSError as e:
                msg = "could not delete flush log %s: %s (skipped)" % (log, e)
                print(msg)
                failures.append(msg)
                continue
            print("pruned flush log (age %.1f days): %s" % (age.total_seconds() / 86400, log.name))
            pruned += 1
    return pruned, failures


def prune_refused_log(spool_dirs, now, max_age_delta, dry_run=False):
    """Prune the refused-receipts log beside each spool dir by the same age rule. Return (pruned, failures).

    The log is append-only, so its mtime is the newest record's time: once that is past the window
    every record in it is past the window too, which makes the file-level rule the same rule the
    spooled receipts and flush logs use. A symlink is never followed or removed.
    """
    pruned = 0
    failures = []
    seen = set()
    for spool_dir in spool_dirs:
        log = emit.refused_log_path(spool_dir)
        key = str(log.parent.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            if log.is_symlink() or not log.is_file():
                continue
            mtime = datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
        except OSError as e:
            msg = "could not stat refused-receipts log %s: %s (skipped)" % (log, e)
            print(msg)
            failures.append(msg)
            continue
        age = now - mtime
        if age <= max_age_delta:
            continue
        if dry_run:
            print("would prune refused-receipts log (age %.1f days): %s" % (age.total_seconds() / 86400, log.name))
            continue
        try:
            log.unlink()
        except OSError as e:
            msg = "could not delete refused-receipts log %s: %s (skipped)" % (log, e)
            print(msg)
            failures.append(msg)
            continue
        print("pruned refused-receipts log (age %.1f days): %s" % (age.total_seconds() / 86400, log.name))
        pruned += 1
    return pruned, failures


def main(argv=None, gh_runner=None, environ=None):
    environ = os.environ if environ is None else environ
    gh_runner = _default_gh_runner if gh_runner is None else gh_runner

    parser = argparse.ArgumentParser(prog="receipt_flush.py", description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=".", help="repo path (default: cwd)")
    parser.add_argument("--max-age-days", type=_max_age_days_arg, default=7,
                        help="drop spools and prune logs older than this many days; a whole number, 0 or more (default: 7)")
    parser.add_argument("--strict", action="store_true", help="exit 2 on any error (default: exit 0)")
    parser.add_argument("--dry-run", action="store_true", help="print plan, perform no posts or deletes")

    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()

    now = datetime.now(timezone.utc)
    max_age_delta = timedelta(days=args.max_age_days)
    errors = []

    # Collect spool files from all search paths (primary + legacy fallback)
    spool_search_paths = rc.get_spool_search_paths(repo=str(repo), environ=environ)

    # A spool dir that is itself a symlink or junction is refused: it is neither pruned nor read through.
    # The run still reports it and exits 2, with or without --strict.
    linked_dirs = [d for d in spool_search_paths if _is_link_or_junction(d)]
    for d in linked_dirs:
        print("refused spool directory %s: it is a symlink or junction (not followed; nothing pruned or flushed through it)" % d)
    spool_search_paths = [d for d in spool_search_paths if d not in linked_dirs]
    link_refused = bool(linked_dirs)

    # Prune stale flush logs and refused-receipts logs by the same age rule. Independent of slug and of json spools.
    _pruned, log_failures = prune_flush_logs(spool_search_paths, now, max_age_delta, dry_run=args.dry_run)
    _pruned_refused, refused_failures = prune_refused_log(spool_search_paths, now, max_age_delta, dry_run=args.dry_run)
    if args.strict:
        errors.extend(log_failures)
        errors.extend(refused_failures)

    spool_files_dict = {}  # basename -> path, to deduplicate if same file in multiple locations
    for spool_dir in spool_search_paths:
        if spool_dir.exists():
            for f in spool_dir.glob("*.json"):
                if f.name not in spool_files_dict:
                    spool_files_dict[f.name] = f

    spool_files = sorted(spool_files_dict.values())
    if not spool_files:
        return 2 if (link_refused or (args.strict and errors)) else 0

    slug = emit.repo_slug(repo)
    if not slug:
        if args.strict:
            print("ERROR: could not determine repo slug from origin", file=sys.stderr)
            return 2
        return 2 if link_refused else 0

    posted = 0
    dropped = 0
    kept = 0

    for spool_file in spool_files:
        # Check age; drop if older than max_age_days
        try:
            mtime = datetime.fromtimestamp(spool_file.stat().st_mtime, tz=timezone.utc)
            age = now - mtime
            if age > max_age_delta:
                if args.dry_run:
                    print("would drop (age %.1f days): %s" % (age.total_seconds() / 86400, spool_file.name))
                else:
                    try:
                        spool_file.unlink()
                        print("dropped stale spool (age %.1f days): %s" % (age.total_seconds() / 86400, spool_file.name))
                        dropped += 1
                    except OSError as e:
                        msg = "could not delete stale spool %s: %s" % (spool_file, e)
                        if args.strict:
                            print("ERROR: %s" % msg, file=sys.stderr)
                            errors.append(msg)
                        kept += 1
                continue
        except OSError as e:
            msg = "could not stat spool file %s: %s" % (spool_file, e)
            if args.strict:
                print("ERROR: %s" % msg, file=sys.stderr)
                errors.append(msg)
            kept += 1
            continue

        # Try to post the spooled receipt
        if args.dry_run:
            try:
                envelope = json.loads(spool_file.read_text(encoding="utf-8"))
                head_sha = envelope.get("receipt", {}).get("head_sha", "?")
                print("would flush: %s (head=%s)" % (spool_file.name, head_sha[:12]))
            except Exception:
                print("would attempt to flush: %s (but file is malformed)" % spool_file.name)
        else:
            success, reason = flush_spooled_receipt(spool_file, slug, gh_runner, strict=args.strict)
            if success:
                print("flushed receipt %s: %s" % (spool_file.name, reason))
                posted += 1
            else:
                print("%s: %s" % (spool_file.name, reason))
                if "kept" in reason or "not on github" in reason:
                    kept += 1
                else:
                    errors.append(reason)

    if args.dry_run:
        return 2 if link_refused else 0

    # Summary
    print("receipt_flush: posted=%d dropped=%d kept=%d" % (posted, dropped, kept))

    if link_refused or (args.strict and errors):
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
