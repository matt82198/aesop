#!/usr/bin/env python3
"""Post one spooled receipt as soon as its sha appears on GitHub (detached, bounded).
INDEX: Receipt-gate auto-flush (pre-push companion to receipt_flush.py). The pre-push
hook spools a receipt when its head sha is not on GitHub yet (422), then calls this
script with --spawn: that forks a DETACHED copy of itself and returns at once, so the
push never waits. The detached copy polls `gh api repos/<slug>/commits/<sha>` every
--interval seconds (default 15) for up to --timeout seconds (default 600), posts the
spooled envelope for that sha through receipt_flush.flush_spooled_receipt() when the
sha exists, and deletes the spool file on success. It logs every outcome to
<spool-dir>/<sha>.flush.log and exits 0 in all cases; if the sha never appears the
spool file is kept for the watchdog's receipt_flush cycle. stdlib-only.
Usage: receipt_autoflush.py --repo PATH --sha SHA [--spawn] [--interval S] [--timeout S]
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))

import emit_receipt as emit  # noqa: E402
import receipt_common as rc  # noqa: E402
import receipt_flush as rf  # noqa: E402

DEFAULT_INTERVAL = 15
DEFAULT_TIMEOUT = 600
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(log_path, message):
    line = "[%s] %s" % (_stamp(), message)
    if log_path is None:
        print(line, flush=True)
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        print(line, flush=True)


def spool_dir_for(repo):
    return Path(rc.resolve_spool_dir(repo=str(repo)))


def spool_file_for(repo, sha):
    """Find the spool envelope for sha in the primary or legacy spool locations."""
    for spool_dir in rc.get_spool_search_paths(repo=str(repo)):
        candidate = Path(spool_dir) / (sha + ".json")
        if candidate.is_file():
            return candidate
    return None


def poll_and_flush(repo, sha, slug, gh_runner, interval, timeout, log_path,
                   sleep=time.sleep, clock=time.monotonic):
    """Poll GitHub for sha, post the spooled receipt when it appears.

    Returns one of "posted", "gone", "timeout", "failed" (the reason is logged).
    The spool file is deleted only on a successful post; every other outcome keeps it.

    The loop is BOUNDED: at most ``timeout // interval + 1`` polls (one poll at t=0 and one
    per interval up to the timeout), so it can never spin forever. ``clock`` is accepted for
    call-site compatibility; the bound is a poll count, not a wall-clock read.
    """
    max_polls = max(1, timeout // interval + 1) if interval > 0 else 1
    for attempt in range(1, max_polls + 1):
        spool = spool_file_for(repo, sha)
        if spool is None:
            _log(log_path, "no spool file for %s (already posted or dropped); nothing to do" % sha)
            return "gone"
        if rf.sha_exists_on_github(sha, slug, gh_runner):
            ok, reason = rf.flush_spooled_receipt(spool, slug, gh_runner, strict=False)
            if ok:
                _log(log_path, "posted receipt for %s after %d poll(s): %s" % (sha, attempt, reason))
                return "posted"
            _log(log_path, "sha %s is on GitHub but post failed: %s (spool kept; watchdog flush retries)" % (sha, reason))
            return "failed"
        if attempt < max_polls:
            sleep(interval)
    _log(log_path, "gave up: sha %s not on GitHub after %ds and %d poll(s); spool kept, "
                   "watchdog receipt_flush will retry" % (sha, timeout, max_polls))
    return "timeout"


def _detached_popen_kwargs(stdout_handle):
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": stdout_handle, "stderr": stdout_handle, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return kwargs


def spawn_detached(repo, sha, interval, timeout):
    """Fork a detached copy of this script that runs the poll loop. Returns its pid."""
    log_path = spool_dir_for(repo) / (sha + ".flush.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(Path(__file__).resolve()), "--repo", str(repo), "--sha", sha,
            "--interval", str(interval), "--timeout", str(timeout)]
    with open(log_path, "a", encoding="utf-8") as out:
        proc = subprocess.Popen(argv, **_detached_popen_kwargs(out))
    return proc.pid


def main(argv=None, gh_runner=None, sleep=time.sleep, clock=time.monotonic):
    parser = argparse.ArgumentParser(prog="receipt_autoflush.py", description=__doc__.split("\n")[0])
    parser.add_argument("--repo", required=True, help="repo path whose spool holds the receipt")
    parser.add_argument("--sha", required=True, help="head sha of the spooled receipt")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="seconds between polls (default 15)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="give up after this many seconds (default 600)")
    parser.add_argument("--spawn", action="store_true", help="detach a background poller and return immediately")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not args.sha.isalnum() or not 7 <= len(args.sha) <= 64:
        print("receipt_autoflush: refusing malformed sha %r" % args.sha, file=sys.stderr)
        return 0

    if args.spawn:
        try:
            pid = spawn_detached(repo, args.sha, args.interval, args.timeout)
            print("receipt_autoflush: spawned pid %d for %s (log: %s)"
                  % (pid, args.sha[:12], spool_dir_for(repo) / (args.sha + ".flush.log")))
        except OSError as exc:
            print("receipt_autoflush: could not spawn poller: %s (watchdog flush will post it)" % exc, file=sys.stderr)
        return 0

    log_path = spool_dir_for(repo) / (args.sha + ".flush.log")
    slug = emit.repo_slug(repo)
    if not slug:
        _log(log_path, "no GitHub slug resolvable from origin; spool kept")
        return 0
    runner = gh_runner if gh_runner is not None else rf._default_gh_runner
    poll_and_flush(repo, args.sha, slug, runner, args.interval, args.timeout, log_path, sleep=sleep, clock=clock)
    return 0


if __name__ == "__main__":
    sys.exit(main())
