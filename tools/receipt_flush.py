#!/usr/bin/env python3
"""Flush spooled receipts to GitHub once their shas exist on the remote.
INDEX: Receipt-gate spool-and-flush (increment 2) -- for each JSON envelope in the
spool directory state/receipts/spool/, check if the head sha exists on GitHub
(`gh api repos/<slug>/commits/<sha>` 200); if yes, call the same post_receipt
function (check-run then comment fallback) to publish; on success delete the spool
file, on failure keep it (except stale ones deleted by --max-age-days). Idempotent,
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
    res = subprocess.run(["gh"] + list(args), capture_output=True, encoding="utf-8", errors="replace", timeout=60)
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


def main(argv=None, gh_runner=None, environ=None):
    environ = os.environ if environ is None else environ
    gh_runner = _default_gh_runner if gh_runner is None else gh_runner

    parser = argparse.ArgumentParser(prog="receipt_flush.py", description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=".", help="repo path (default: cwd)")
    parser.add_argument("--max-age-days", type=int, default=7, help="drop spools older than this (default: 7)")
    parser.add_argument("--strict", action="store_true", help="exit 2 on any error (default: exit 0)")
    parser.add_argument("--dry-run", action="store_true", help="print plan, perform no posts or deletes")

    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()

    # Collect spool files from all search paths (primary + legacy fallback)
    spool_search_paths = rc.get_spool_search_paths(repo=str(repo), environ=environ)
    spool_files_dict = {}  # basename -> path, to deduplicate if same file in multiple locations
    for spool_dir in spool_search_paths:
        if spool_dir.exists():
            for f in spool_dir.glob("*.json"):
                if f.name not in spool_files_dict:
                    spool_files_dict[f.name] = f

    spool_files = sorted(spool_files_dict.values())
    if not spool_files:
        return 0

    slug = emit.repo_slug(repo)
    if not slug:
        if args.strict:
            print("ERROR: could not determine repo slug from origin", file=sys.stderr)
            return 2
        return 0

    now = datetime.now(timezone.utc)
    max_age_delta = timedelta(days=args.max_age_days)
    posted = 0
    dropped = 0
    kept = 0
    errors = []

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
        return 0

    # Summary
    print("receipt_flush: posted=%d dropped=%d kept=%d" % (posted, dropped, kept))

    if args.strict and errors:
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
