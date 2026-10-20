#!/usr/bin/env python3
"""Box fence: refuse to run a box whose aesop checkout is behind the shared BOX_PROTOCOL.

INDEX: Compares the checkout's BOX_PROTOCOL integer with origin's; fast-forwards a clean strictly-behind tree, FAILs (never resets) otherwise, WARNs offline with a push-time re-check marker.

Why: two boxes share GitHub repos. A box that has not pulled keeps old write behaviour and
can overwrite the other box's work. BOX_PROTOCOL (a single integer at the repo root) is bumped
only when a change makes old boxes unsafe to run, so a box needs a compatible checkout, not the
latest commit.

Usage:
    python tools/box_fence.py [--repo DIR] [--timeout SECONDS] [--json] [--push-check]

Outcomes (exit code 0 = the box may run, 1 = it must not):
    PASS  local >= origin (or origin has no BOX_PROTOCOL: feature not adopted yet).
          If the tree was clean and strictly behind it is fast-forwarded first (merge --ff-only)
          and the output says it updated. Missing locally but present on origin counts as behind.
    FAIL  behind and the tree is dirty or has diverged. Prints plain-language steps. This tool
          never resets, stashes, cleans or discards anything.
    WARN  fetch failed (offline/auth). The box is allowed to run because an offline box cannot
          collide until it pushes; a marker file is written inside the git dir (never dirties the
          tree) so the push path re-checks with --push-check before any push.

--push-check: same comparison, but a failed fetch is FAIL (a box that cannot reach origin must
not push), and a successful check clears the offline marker.

Stdlib only. Every git call is bounded by --timeout and has stdin=DEVNULL.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

PROTOCOL_FILE = "BOX_PROTOCOL"
MARKER_NAME = "aesop-box-fence-offline"
DEFAULT_TIMEOUT_S = 30


def _git(repo, args, timeout):
    """Run git; return (rc, stdout, stderr). A timeout or missing git returns rc 124/127."""
    try:
        r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout,
                           env=_env())
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout
    except OSError as exc:
        return 127, "", str(exc)


def _env():
    import os
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # an auth prompt must fail, never hang
    return env


def parse_protocol(text):
    """Return the integer in a BOX_PROTOCOL body, or None when it is not a single integer."""
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return None


def marker_path(repo, timeout=DEFAULT_TIMEOUT_S):
    rc, out, _ = _git(repo, ["rev-parse", "--absolute-git-dir"], timeout)
    if rc != 0 or not out.strip():
        return None
    return Path(out.strip()) / MARKER_NAME


def _origin_ref(repo, timeout):
    rc, out, _ = _git(repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], timeout)
    if rc == 0 and out.strip():
        return out.strip()
    for cand in ("origin/main", "origin/master"):
        rc, _, _ = _git(repo, ["rev-parse", "--verify", "--quiet", "refs/remotes/" + cand], timeout)
        if rc == 0:
            return cand
    return None


def _result(status, message, **extra):
    d = {"status": status, "message": message, "updated": False}
    d.update(extra)
    return d


def _fail_message(behind_by, local, origin_val, why, branch):
    return (
        "THIS BOX IS BEHIND AND MUST NOT RUN.\n"
        "Your aesop checkout uses BOX_PROTOCOL %s but the shared repo requires %s. Running old "
        "code could overwrite the other box's work.\n"
        "Reason it was not updated automatically: %s.\n"
        "Nothing was changed. To fix it, save your work and update:\n"
        "  git status\n"
        "  git add -A && git commit -m \"save my work\"   (or: git stash -u)\n"
        "  git pull --rebase origin %s   (or, if you have nothing to keep: git merge --ff-only origin/%s)\n"
        "Then start aesop again." % (local if local is not None else "(missing)", origin_val, why, branch, branch)
    )


def check(repo, timeout=DEFAULT_TIMEOUT_S, push_check=False):
    """Run the fence for ``repo``. Returns a dict with status PASS|WARN|FAIL and a message."""
    repo = Path(repo)
    marker = marker_path(repo, timeout)
    marker_s = str(marker) if marker else None
    rc, _, err = _git(repo, ["fetch", "--quiet", "origin"], timeout)
    if rc != 0:
        reason = (err or "").strip().splitlines()[-1:] or ["fetch failed"]
        if push_check:
            return _result("FAIL", "Cannot reach origin (%s). A box that cannot verify its "
                           "BOX_PROTOCOL must not push. Get online and retry." % reason[0],
                           marker=marker_s)
        if marker is not None:
            try:
                marker.write_text("fetch failed: %s\n" % reason[0], encoding="utf-8")
            except OSError:
                pass
        return _result("WARN", "Could not reach origin (%s); allowing this box to run. The push "
                       "path will re-check BOX_PROTOCOL before any push." % reason[0], marker=marker_s)

    ref = _origin_ref(repo, timeout)
    if ref is None:
        return _result("PASS", "No origin default branch found; fence not applicable.", marker=marker_s)
    branch = ref.split("/", 1)[1]
    rc, body, _ = _git(repo, ["show", "%s:%s" % (ref, PROTOCOL_FILE)], timeout)
    if rc != 0:
        _clear(marker)
        return _result("PASS", "origin has no %s yet (feature not adopted); nothing to enforce." % PROTOCOL_FILE,
                       marker=marker_s)
    origin_val = parse_protocol(body)
    if origin_val is None:
        _clear(marker)
        return _result("PASS", "origin %s is not a single integer; ignoring." % PROTOCOL_FILE, marker=marker_s)

    local_path = repo / PROTOCOL_FILE
    local_val = None
    if local_path.is_file():
        local_val = parse_protocol(local_path.read_text(encoding="utf-8", errors="replace"))
    if local_val is not None and local_val >= origin_val:
        _clear(marker)
        return _result("PASS", "BOX_PROTOCOL %s is current (origin %s)." % (local_val, origin_val),
                       local=local_val, origin=origin_val, marker=marker_s)

    # Behind (or missing locally). Update only a clean, strictly-behind tree.
    _, status_out, _ = _git(repo, ["status", "--porcelain"], timeout)
    dirty = bool(status_out.strip())
    arc, _, _ = _git(repo, ["merge-base", "--is-ancestor", "HEAD", ref], timeout)
    strictly_behind = arc == 0
    if dirty or not strictly_behind:
        why = ("you have uncommitted or untracked changes" if dirty
               else "your checkout has commits that origin/%s does not (it has diverged)" % branch)
        if dirty and not strictly_behind:
            why = "you have uncommitted changes and your checkout has diverged from origin/%s" % branch
        return _result("FAIL", _fail_message(None, local_val, origin_val, why, branch),
                       local=local_val, origin=origin_val, marker=marker_s)
    mrc, _, merr = _git(repo, ["merge", "--ff-only", ref], timeout)
    if mrc != 0:
        return _result("FAIL", _fail_message(None, local_val, origin_val,
                                             "fast-forward failed (%s)" % merr.strip()[:200], branch),
                       local=local_val, origin=origin_val, marker=marker_s)
    new_val = None
    if local_path.is_file():
        new_val = parse_protocol(local_path.read_text(encoding="utf-8", errors="replace"))
    if new_val is None or new_val < origin_val:
        return _result("FAIL", _fail_message(None, new_val, origin_val,
                                             "still behind after fast-forward", branch),
                       local=new_val, origin=origin_val, marker=marker_s)
    _clear(marker)
    res = _result("PASS", "Updated: fast-forwarded this clean checkout to origin/%s (BOX_PROTOCOL %s -> %s)."
                  % (branch, local_val if local_val is not None else "missing", new_val),
                  local=new_val, origin=origin_val, marker=marker_s)
    res["updated"] = True
    return res


def _clear(marker):
    if marker is not None:
        try:
            marker.unlink()
        except OSError:
            pass


def main(argv=None):
    p = argparse.ArgumentParser(description="BOX_PROTOCOL fence (see module docstring).")
    p.add_argument("--repo", default=".", help="aesop checkout to check (default: cwd)")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="per-git-call timeout seconds")
    p.add_argument("--json", action="store_true", help="print one JSON object")
    p.add_argument("--push-check", action="store_true",
                   help="push-time re-check: offline is a FAIL, success clears the offline marker")
    args = p.parse_args(argv)
    res = check(args.repo, timeout=args.timeout, push_check=args.push_check)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("FENCE %s: %s" % (res["status"], res["message"]))
    return 1 if res["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
