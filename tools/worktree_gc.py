#!/usr/bin/env python3
"""
INDEX: Worktree garbage-collection report and cap check; classifies every non-primary worktree of the registered primaries as SAFE, DIRTY, UNPUSHED, LIVE or UNKNOWN, WARNs over the cap (default 15), and removes SAFE worktrees only under --apply with --confirm-token equal to AESOP_WORKTREE_GC_TOKEN (an operator-held value)

Worktree garbage-collection report (read-only by default).

Nothing else in aesop removes finished worktrees; lane_liveness.py only lists
them. This tool inventories every non-primary worktree of each registered
primary repo (worktree discovery is reused from lane_liveness.discover_lanes),
classifies each one, and compares the total against a cap (default 15).

Classes, first match wins:
  LIVE      path on KEEP_PATHS (runtime pinned, never removed), or an OPEN PR on
            the branch, or newest file mtime younger than --live-minutes (default 120)
  DIRTY     uncommitted changes (changed file list is reported)
  UNPUSHED  commits reachable from HEAD on no origin ref, and no merged PR/ancestry
  SAFE      clean, and HEAD is an ancestor of the origin default branch OR a PR for
            the branch is MERGED; the only class --apply may remove
  UNKNOWN   cannot be decided (path missing, git error, PR lookup error, not merged)

Read-only by design: no fetch, no writes, no removal. Stale origin refs only
push classes toward UNKNOWN/UNPUSHED, never toward SAFE.

Removal (hard stop: deleting waits for Matt). `--apply --confirm-token T` removes
SAFE worktrees with `git worktree remove` (never --force) then prunes, and
refuses unless T equals the AESOP_WORKTREE_GC_TOKEN environment variable.
Without a valid token the tool prints the exact commands and removes nothing.
The orchestrator never passes --confirm-token and never sets the variable.

Usage:
  worktree_gc.py [--json] [--check] [--cap 15] [--live-minutes 120]
  worktree_gc.py --apply --confirm-token TOKEN [--live-minutes N]

Exit: 0 report ok; --check: 1 total over cap, 2 a primary is unreadable;
--apply: 2 refused (missing or wrong token) or a git failure.
"""

import argparse
import hmac
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lane_liveness  # noqa: E402  (reused: discover_lanes, newest_mtime)

DEFAULT_CAP = 15
DEFAULT_LIVE_MINUTES = 120
TOKEN_ENV = "AESOP_WORKTREE_GC_TOKEN"
CLASS_ORDER = ("SAFE", "DIRTY", "UNPUSHED", "LIVE", "UNKNOWN")

HOME = Path.home()
# Registered primaries (the trees whose worktrees form the inventory).
# lane_liveness has no primary registry of its own; this list is the registry.
PRIMARIES = (
    ("aesop", HOME / "Desktop" / "aesop"),
    ("keyrun", HOME / "Desktop" / "keyrun"),
    ("psinasty", HOME / "Desktop" / "psinasty"),
    ("claude-config", HOME / ".claude"),
    ("claude-scripts", HOME / "scripts"),
    ("portfolio", HOME / "portfolio-wt" / "matt82198"),
    ("portfolio-io", HOME / "portfolio-wt" / "matt82198.github.io"),
)
# Runtime worktrees that are never lanes (the watchdog resets this tree every tick).
KEEP_PATHS = (HOME / "aesop-daemon-runtime",)

REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$")


class PrLookupError(Exception):
    """A PR lookup could not be answered; the caller must fail closed."""


def _git(args, cwd):
    """Run git in cwd; return (returncode, stdout, stderr). Never raises."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return result.returncode, result.stdout or "", result.stderr or ""


def _norm(path):
    return os.path.normcase(os.path.realpath(str(path)))


def origin_slug(primary):
    """Return 'owner/repo' from the primary's origin URL, or None."""
    rc, out, _ = _git(["remote", "get-url", "origin"], primary)
    if rc != 0:
        return None
    match = REMOTE_RE.search(out.strip())
    return f"{match.group(1)}/{match.group(2)}" if match else None


def default_ref(primary):
    """Return the origin default ref (e.g. origin/main); falls back to origin/main."""
    rc, out, _ = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], primary)
    return out.strip() if rc == 0 and out.strip() else "origin/main"


def load_pr_index(slug):
    """Return {headRefName: [(number, state), ...]} for every PR of slug (one gh call).

    Raises PrLookupError when gh is unavailable or its output is unreadable.
    """
    if not slug:
        raise PrLookupError("no origin github slug for this primary")
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", slug, "--state", "all",
             "--json", "number,headRefName,state", "--limit", "5000"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrLookupError(f"gh unavailable: {exc}")
    if result.returncode != 0:
        raise PrLookupError((result.stderr or "").strip() or f"gh exit {result.returncode}")
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError as exc:
        raise PrLookupError(f"unparseable gh output: {exc}")
    index = {}
    for row in rows:
        index.setdefault(row.get("headRefName", ""), []).append(
            (int(row["number"]), row.get("state", "")))
    return index


def make_pr_view(index, error):
    """Build pr_view(branch) -> (merged_numbers, open_numbers) over a loaded index."""
    def pr_view(branch):
        if error is not None:
            raise PrLookupError(error)
        rows = index.get(branch, [])
        merged = [n for n, s in rows if s == "MERGED"]
        opened = [n for n, s in rows if s == "OPEN"]
        return merged, opened
    return pr_view


def _set(rec, cls, reason):
    rec["class"] = cls
    rec["reason"] = reason
    return rec


def classify_worktree(lane, primary_name, primary_path, *, live_minutes, now,
                      pr_view, keep_norms=frozenset()):
    """Classify one non-primary worktree. Returns a record dict; never raises."""
    rec = {"primary": primary_name, "primary_path": str(primary_path),
           "name": lane.get("name", ""), "path": lane.get("path", ""),
           "branch": lane.get("branch", ""), "class": "UNKNOWN", "reason": "",
           "dirty_files": [], "unpushed": 0, "age_min": None}
    path = Path(rec["path"])
    if _norm(path) in keep_norms:
        return _set(rec, "LIVE", "runtime-pinned path (KEEP_PATHS)")
    if not path.is_dir():
        return _set(rec, "UNKNOWN", "path missing on disk")
    rc, out, err = _git(["status", "--porcelain"], path)
    if rc != 0:
        return _set(rec, "UNKNOWN", f"git status failed: {err.strip()}")
    dirty = [line[3:] for line in out.splitlines() if line.strip()]
    rec["dirty_files"] = dirty
    if dirty:
        return _set(rec, "DIRTY", f"{len(dirty)} uncommitted change(s)")
    rc, out, err = _git(["merge-base", "--is-ancestor", "HEAD", default_ref(primary_path)], path)
    if rc not in (0, 1):
        return _set(rec, "UNKNOWN", f"ancestry check failed: {err.strip()}")
    ancestor = rc == 0
    rc, out, err = _git(["rev-list", "--count", "HEAD", "--not", "--remotes=origin"], path)
    if rc != 0 or not out.strip().isdigit():
        return _set(rec, "UNKNOWN", f"unpushed count failed: {err.strip()}")
    rec["unpushed"] = int(out.strip())
    merged_prs, open_prs, pr_error = [], [], None
    if rec["branch"]:
        try:
            merged_prs, open_prs = pr_view(rec["branch"])
        except PrLookupError as exc:
            pr_error = str(exc)
    merged_evidence = ancestor or bool(merged_prs)
    if rec["unpushed"] and not merged_evidence:
        return _set(rec, "UNPUSHED", f"{rec['unpushed']} commit(s) on no origin ref")
    try:
        newest = lane_liveness.newest_mtime(path)
    except lane_liveness.LaneUnreadable as exc:
        return _set(rec, "UNKNOWN", f"mtime scan failed: {exc}")
    if newest is not None:
        rec["age_min"] = round(max(0.0, (now - newest) / 60.0), 1)
    if open_prs:
        return _set(rec, "LIVE", f"open PR #{open_prs[0]}")
    if rec["age_min"] is not None and rec["age_min"] < live_minutes:
        return _set(rec, "LIVE", f"touched {rec['age_min']}m ago (< {live_minutes}m)")
    if pr_error is not None:
        return _set(rec, "UNKNOWN", f"PR lookup failed: {pr_error}")
    if ancestor:
        return _set(rec, "SAFE", "clean; HEAD is an ancestor of origin default")
    if merged_prs:
        return _set(rec, "SAFE", f"clean; PR #{merged_prs[0]} MERGED")
    return _set(rec, "UNKNOWN", "not merged into origin default and no MERGED PR")


def inventory(primaries):
    """Return ([(primary_name, primary_path, lane), ...], unreadable) without classifying.

    Cheap (git worktree list only, no gh, no status): this is what --check counts.
    unreadable is a list of (primary_name, reason); a primary that cannot be listed
    is reported, never silently counted as zero.
    """
    entries, unreadable, seen = [], [], set()
    for name, primary in primaries:
        try:
            lanes = lane_liveness.discover_lanes(primary)
        except lane_liveness.LaneDiscoveryError as exc:
            unreadable.append((name, str(exc)))
            continue
        for lane in lanes:
            key = _norm(lane.get("path", ""))
            if key in seen:
                continue
            seen.add(key)
            entries.append((name, primary, lane))
    return entries, unreadable


def build_report(primaries, *, live_minutes, now, pr_view_factory, keep_paths=KEEP_PATHS):
    """Inventory and classify every non-primary worktree of every primary.

    pr_view_factory(primary_name, primary_path) -> pr_view callable. Returns
    (records, unreadable) where unreadable is a list of (name, reason).
    """
    keep_norms = frozenset(_norm(p) for p in keep_paths)
    entries, unreadable = inventory(primaries)
    views = {}
    records = []
    for name, primary, lane in entries:
        if name not in views:
            views[name] = pr_view_factory(name, primary)
        records.append(classify_worktree(
            lane, name, primary, live_minutes=live_minutes, now=now,
            pr_view=views[name], keep_norms=keep_norms))
    order = {cls: i for i, cls in enumerate(CLASS_ORDER)}
    records.sort(key=lambda r: (order[r["class"]], r["primary"], r["name"]))
    return records, unreadable


def summarize(records, cap):
    """Return the counts dict and the OVER|OK status for the cap."""
    counts = {cls.lower(): 0 for cls in CLASS_ORDER}
    for rec in records:
        counts[rec["class"].lower()] += 1
    total = len(records)
    counts["total"] = total
    counts["cap"] = cap
    counts["status"] = "OVER" if total > cap else "OK"
    return counts


def summary_line(counts):
    return ("WORKTREES: total=%d safe=%d dirty=%d unpushed=%d live=%d cap=%d %s"
            % (counts["total"], counts["safe"], counts["dirty"], counts["unpushed"],
               counts["live"], counts["cap"], counts["status"]))


def print_table(records):
    """Print a fixed-width ASCII table of every record."""
    headers = ("CLASS", "PRIMARY", "NAME", "BRANCH", "REASON", "PATH")
    rows = [(r["class"], r["primary"], r["name"], r["branch"] or "(detached)",
             r["reason"], r["path"]) for r in records]
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(str(c))) for w, c in zip(widths, row)]
    fmt = "  ".join("%-" + str(w) + "s" for w in widths)
    print(fmt % headers)
    for row in rows:
        print(fmt % tuple(row))
    for rec in records:
        if rec["class"] == "DIRTY":
            print("DIRTY %s files:" % rec["path"])
            for item in rec["dirty_files"]:
                print("    %s" % item)


def removal_plan(records):
    """Return the exact git commands for SAFE worktrees, grouped by primary."""
    commands = []
    primaries = []
    for rec in records:
        if rec["class"] != "SAFE":
            continue
        commands.append('git -C "%s" worktree remove "%s"' % (rec["primary_path"], rec["path"]))
        if rec["primary_path"] not in primaries:
            primaries.append(rec["primary_path"])
    for primary in primaries:
        commands.append('git -C "%s" worktree prune' % primary)
    return commands


def apply_removals(records, token_arg, reclassify):
    """Remove SAFE worktrees if token_arg matches AESOP_WORKTREE_GC_TOKEN.

    reclassify(rec) -> fresh record; a worktree is removed only if it is still SAFE.
    Returns (exit_code, messages).
    """
    expected = os.environ.get(TOKEN_ENV, "")
    if not expected or not token_arg or not hmac.compare_digest(expected, token_arg):
        return 2, ["REFUSED: --confirm-token missing or not equal to %s; nothing removed."
                   % TOKEN_ENV]
    messages, code, primaries = [], 0, []
    for rec in records:
        if rec["class"] != "SAFE":
            continue
        fresh = reclassify(rec)
        if fresh["class"] != "SAFE":
            messages.append("SKIP %s: now %s (%s)" % (rec["path"], fresh["class"], fresh["reason"]))
            continue
        rc, _, err = _git(["worktree", "remove", rec["path"]], rec["primary_path"])
        if rc != 0:
            code = 2
            messages.append("FAILED %s: %s" % (rec["path"], err.strip()))
        else:
            messages.append("REMOVED %s" % rec["path"])
            if rec["primary_path"] not in primaries:
                primaries.append(rec["primary_path"])
    for primary in primaries:
        _git(["worktree", "prune"], primary)
    return code, messages


def main(argv=None):
    parser = argparse.ArgumentParser(description="Worktree GC report and cap check.")
    parser.add_argument("--json", action="store_true", help="Emit JSON on stdout.")
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 when total exceeds the cap, 2 when a primary is unreadable.")
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP)
    parser.add_argument("--live-minutes", type=int, default=DEFAULT_LIVE_MINUTES)
    parser.add_argument("--apply", action="store_true",
                        help="Remove SAFE worktrees (requires --confirm-token).")
    parser.add_argument("--confirm-token", default="",
                        help="Operator-held token; must equal AESOP_WORKTREE_GC_TOKEN.")
    args = parser.parse_args(argv)

    now = time.time()

    def pr_view_factory(name, primary):
        slug = origin_slug(primary)
        try:
            return make_pr_view(load_pr_index(slug), None)
        except PrLookupError as exc:
            return make_pr_view({}, str(exc))

    def reclassify(rec):
        lane = {"name": rec["name"], "path": rec["path"], "branch": rec["branch"]}
        return classify_worktree(lane, rec["primary"], Path(rec["primary_path"]),
                                 live_minutes=args.live_minutes, now=time.time(),
                                 pr_view=pr_view_factory(rec["primary"], Path(rec["primary_path"])),
                                 keep_norms=frozenset(_norm(p) for p in KEEP_PATHS))

    if args.check and not (args.json or args.apply):
        # Cap check for the pre-push WARNING: counts only, no classification (no gh, no status).
        entries, unreadable = inventory(PRIMARIES)
        total = len(entries)
        status = "OVER" if total > args.cap else "OK"
        print("WORKTREES: total=%d cap=%d %s" % (total, args.cap, status))
        for name, reason in unreadable:
            print("UNREADABLE PRIMARY %s: %s" % (name, reason))
        if unreadable:
            return 2
        return 1 if status == "OVER" else 0

    records, unreadable = build_report(
        PRIMARIES, live_minutes=args.live_minutes, now=now,
        pr_view_factory=pr_view_factory)
    counts = summarize(records, args.cap)

    exit_code = 0
    if args.check:
        if counts["status"] == "OVER":
            exit_code = 1
        if unreadable:
            exit_code = 2

    if args.json:
        print(json.dumps({"records": records, "summary": counts,
                          "unreadable": [{"primary": n, "reason": r} for n, r in unreadable]},
                         indent=2))
    else:
        print_table(records)
        print(summary_line(counts))
        if counts["unknown"]:
            print("UNKNOWN: %d (not removable; see REASON)" % counts["unknown"])
        for name, reason in unreadable:
            print("UNREADABLE PRIMARY %s: %s" % (name, reason))

    if args.apply:
        code, messages = apply_removals(records, args.confirm_token, reclassify)
        for line in messages:
            print(line)
        if code:
            return code
    elif not args.json:
        print("NOTHING REMOVED (report only). Commands Matt would run for SAFE worktrees:")
        for command in removal_plan(records):
            print("  " + command)
        print("Matt only: set %s in his own shell, then run "
              "worktree_gc.py --apply --confirm-token <that value>. The orchestrator never does this."
              % TOKEN_ENV)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
