#!/usr/bin/env python3
"""
INDEX: Guardrail G11: RUNTIME lane-liveness enforcement (watcher_linter/dispatch_lint only read prompt TEXT; this catches an agent that accepted a good prompt then went silent). Lane inventory from `git worktree list --porcelain` (primary tree dropped) or `--claims FILE` ({name,path} objects replace discovery; a bare name list filters it, and a claimed name with no worktree is kept as `missing`, never dropped). Evidence = newest FILE mtime under the lane (directory mtimes ignored and `.git`/`node_modules`/`__pycache__`/build dirs skipped, so git bookkeeping cannot forge liveness) OR the matching `agent-<id>.jsonl` mtime obtained by reusing `stall_check.scan_transcripts` (composition, not a rewrite) so a thinking-but-not-yet-writing agent is not falsely stalled. CLI: `--check [--max-silence 900] [--json] [--repo DIR] [--lanes A,B] [--claims FILE] [--transcripts-root DIR]`; exit 0=all fresh / 1=STALLED or MISSING lanes named (the orchestrator's TaskStop+relaunch input) / 2=unreadable input i.e. liveness undetermined — undeterminable never degrades to a pass, and exit 2 outranks exit 1
Runtime lane-liveness enforcement (Guardrail G11).

Mechanizes the "no watcher pattern in long runs" rule at RUNTIME. tools/
watcher_linter.py (G3) and tools/dispatch_lint.py only inspect prompt TEXT;
they cannot see an agent that accepted a well-formed prompt and then went
silent ("polling in the background" and never coming back). This tool asks the
only question that falsifies that claim: does every lane the orchestrator
believes is live have on-disk evidence newer than --max-silence?

Usage:
  lane_liveness.py --check [--max-silence SEC] [--json] [--repo DIR]
                   [--lanes NAME[,NAME...]] [--claims FILE]
                   [--transcripts-root DIR]

Options:
  --check              Run the check (default action).
  --max-silence SEC    Max seconds a live lane may go without evidence
                       (default: 900). Ages <= SEC are live.
  --json               Emit the machine-readable report on stdout.
  --repo DIR           Repo root whose git worktrees form the lane inventory
                       (default: cwd).
  --lanes NAMES        Comma-separated claimed-live lane names. Narrows the
                       inventory; a claimed name with no worktree is reported
                       as missing (stalled), never dropped.
  --claims FILE        JSON claims. Either a list of lane names (filter, same
                       as --lanes) or a list of {name, path[, branch,
                       agent_id]} objects, which REPLACE git discovery.
  --transcripts-root D Root to scan for agent-*.jsonl transcripts (default:
                       AESOP_TRANSCRIPTS_ROOT or ~/.claude/projects).

Evidence (newest wins), per lane:
  - worktree: newest mtime under the lane path, ignoring .git and build/cache
    noise so that git bookkeeping cannot forge liveness.
  - transcript: mtime of the matching agent-<id>.jsonl, discovered by reusing
    tools/stall_check.py (composition, not a reimplementation). This keeps a
    thinking/reading agent that has not written a file yet from being called
    stalled.

Exit codes (fail-closed):
  0  every claimed lane has fresh evidence.
  1  at least one lane is STALLED or MISSING -- the names are printed; this is
     the orchestrator's TaskStop + relaunch input.
  2  at least one input was unreadable, i.e. liveness could NOT be determined.
     Undeterminable never degrades to a pass.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stall_check  # noqa: E402  (composition: reuse its transcript discovery)

DEFAULT_MAX_SILENCE = 900

# Directories whose churn is not lane work: git bookkeeping, dependency trees,
# byte-code and build output would otherwise make every lane look alive.
SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", "dist", "build", "_site", ".next",
    "coverage", ".turbo",
})

# Bound the walk so one pathological lane cannot hang the gate.
MAX_ENTRIES_PER_LANE = 40000


class LaneUnreadable(Exception):
    """Raised when a lane's liveness cannot be determined (fail-closed)."""


class LaneDiscoveryError(Exception):
    """Raised when the lane inventory itself cannot be built."""


def newest_mtime(path):
    """Return the newest FILE mtime under `path`, or None if there is none.

    Only file mtimes count. Directory mtimes are deliberately ignored: writing
    into a skipped directory (.git bookkeeping, __pycache__) bumps the parent
    directory's mtime, which would let git noise forge lane liveness. That is
    exactly the fail-open this gate exists to close.

    Raises LaneUnreadable if the tree exists but cannot be traversed.
    """
    root = Path(path)
    if not root.exists():
        return None

    newest = None
    seen = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            raise LaneUnreadable(f"{current}: {exc}")

        for entry in entries:
            seen += 1
            if seen > MAX_ENTRIES_PER_LANE:
                return newest
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in SKIP_DIRS:
                        continue
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    newest = max(newest or 0.0, entry.stat().st_mtime)
            except OSError as exc:
                raise LaneUnreadable(f"{entry.path}: {exc}")

    return newest


def parse_worktree_porcelain(text):
    """Parse `git worktree list --porcelain` into lane records.

    The first record is the primary worktree (the orchestrator's own tree) and
    is dropped: it is not a dispatched lane.
    """
    lanes = []
    current = {}
    records = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        records.append(current)

    for record in records[1:]:
        path = record.get("worktree", "")
        if not path:
            continue
        branch = record.get("branch", "")
        if branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/"):]
        name = Path(path).name
        lane = {"name": name, "path": path, "branch": branch}
        if name.startswith("agent-"):
            lane["agent_id"] = name[len("agent-"):]
        lanes.append(lane)
    return lanes


def discover_lanes(repo_root):
    """Build the lane inventory from git worktrees. Raises LaneDiscoveryError."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8",
            timeout=60, cwd=str(repo_root))
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaneDiscoveryError(f"git worktree list failed: {exc}")
    if result.returncode != 0:
        raise LaneDiscoveryError(
            f"git worktree list exit {result.returncode}: "
            f"{(result.stderr or '').strip()}")
    return parse_worktree_porcelain(result.stdout)


def load_claims(claims_path):
    """Load a claims file.

    Returns (lanes, names): exactly one is non-None. A list of objects is a
    full inventory; a list of strings is a name filter over git discovery.
    Raises LaneUnreadable on anything unparseable -- a claims file we cannot
    read must not silently become "no claims, all clear".
    """
    path = Path(claims_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LaneUnreadable(f"claims file unreadable: {exc}")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LaneUnreadable(f"claims file is not valid JSON: {exc}")

    if isinstance(data, dict):
        data = data.get("lanes", data.get("claims"))
    if not isinstance(data, list):
        raise LaneUnreadable("claims file must be a JSON list of names or objects")

    if not data:
        return [], None
    if all(isinstance(item, str) for item in data):
        return None, list(data)
    if all(isinstance(item, dict) for item in data):
        lanes = []
        for item in data:
            name = item.get("name") or Path(item.get("path", "")).name
            if not name:
                raise LaneUnreadable("claim object needs a name or path")
            lane = {
                "name": name,
                "path": item.get("path", ""),
                "branch": item.get("branch", ""),
            }
            agent_id = item.get("agent_id")
            if not agent_id and name.startswith("agent-"):
                agent_id = name[len("agent-"):]
            if agent_id:
                lane["agent_id"] = agent_id
            lanes.append(lane)
        return lanes, None
    raise LaneUnreadable("claims file mixes names and objects")


def apply_name_filter(lanes, names):
    """Narrow `lanes` to the claimed `names`.

    A claimed name that matches no worktree is KEPT with an empty path so it is
    reported as missing. Dropping it would be the fail-open bug this tool
    exists to prevent.
    """
    by_key = {}
    for lane in lanes:
        by_key.setdefault(lane["name"], lane)
        if lane.get("branch"):
            by_key.setdefault(lane["branch"], lane)

    selected = []
    seen = set()
    for name in names:
        lane = by_key.get(name)
        if lane is None:
            lane = {"name": name, "path": "", "branch": ""}
        if id(lane) in seen:
            continue
        seen.add(id(lane))
        selected.append(lane)
    return selected


def build_transcript_index(transcripts_root):
    """Map agent_id -> newest transcript mtime, reusing stall_check discovery.

    A missing root yields an empty index: transcripts are corroborating
    evidence, and their absence must not by itself excuse a silent lane.
    """
    # threshold is irrelevant here; we only consume the ages stall_check emits.
    results = stall_check.scan_transcripts(transcripts_root, DEFAULT_MAX_SILENCE)
    if not results:
        return {}
    now = time.time()
    index = {}
    for entry in results:
        agent = entry.get("agent")
        age = entry.get("mtime_age_s")
        if agent is None or age is None:
            continue
        mtime = now - float(age)
        if agent not in index or mtime > index[agent]:
            index[agent] = mtime
    return index


def check_lanes(lanes, max_silence, transcript_index, now=None):
    """Assert every claimed lane has evidence newer than max_silence."""
    now = time.time() if now is None else now
    report_lanes = []
    stalled = []
    unreadable = []

    for lane in lanes:
        name = lane["name"]
        path = lane.get("path", "")
        agent_id = lane.get("agent_id")
        record = {
            "name": name,
            "path": path,
            "branch": lane.get("branch", ""),
            "agent_id": agent_id or "",
        }

        if not path:
            record.update({"verdict": "missing", "age_s": None,
                           "evidence": "none",
                           "detail": "claimed lane has no worktree"})
            report_lanes.append(record)
            stalled.append(name)
            continue

        try:
            worktree_mtime = newest_mtime(path)
        except LaneUnreadable as exc:
            record.update({"verdict": "unreadable", "age_s": None,
                           "evidence": "none", "detail": str(exc)})
            report_lanes.append(record)
            unreadable.append(name)
            continue

        if worktree_mtime is None:
            detail = ("worktree path does not exist" if not Path(path).exists()
                      else "no files under worktree path")
            record.update({"verdict": "missing", "age_s": None,
                           "evidence": "none", "detail": detail})
            report_lanes.append(record)
            stalled.append(name)
            continue

        transcript_mtime = transcript_index.get(agent_id) if agent_id else None

        best = worktree_mtime
        evidence = "worktree"
        if transcript_mtime is not None and transcript_mtime > best:
            best = transcript_mtime
            evidence = "transcript"

        age = int(now - best)
        record.update({
            "age_s": age,
            "evidence": evidence,
            "verdict": "live" if age <= max_silence else "stalled",
            "detail": "",
        })
        report_lanes.append(record)
        if record["verdict"] == "stalled":
            stalled.append(name)

    # Undeterminable outranks stalled: exit 2 means "the gate could not answer".
    exit_code = 2 if unreadable else (1 if stalled else 0)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "max_silence_s": max_silence,
        "lane_count": len(report_lanes),
        "lanes": report_lanes,
        "stalled": stalled,
        "unreadable": unreadable,
        "exit_code": exit_code,
    }


def print_human(report):
    """Print an ASCII summary of the report."""
    print("LANE LIVENESS (max-silence %ds, %d lanes)"
          % (report["max_silence_s"], report["lane_count"]))
    print("-" * 78)
    print("%-40s %-10s %-12s %s" % ("LANE", "AGE (s)", "EVIDENCE", "VERDICT"))
    for lane in sorted(report["lanes"],
                       key=lambda l: (-1 if l["age_s"] is None else l["age_s"]),
                       reverse=True):
        age = "n/a" if lane["age_s"] is None else str(lane["age_s"])
        print("%-40s %-10s %-12s %s" % (
            lane["name"][:40], age, lane["evidence"], lane["verdict"]))
    print("-" * 78)
    if report["unreadable"]:
        print("UNREADABLE (liveness undetermined): %s"
              % ", ".join(report["unreadable"]))
    if report["stalled"]:
        print("STALLED (TaskStop + relaunch): %s" % ", ".join(report["stalled"]))
    if not report["stalled"] and not report["unreadable"]:
        print("OK: every claimed lane has fresh evidence.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Assert every claimed-live lane has fresh on-disk evidence.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="Run the check (default action).")
    parser.add_argument("--max-silence", type=int, default=DEFAULT_MAX_SILENCE,
                        help="Max seconds of silence for a live lane (default 900).")
    parser.add_argument("--json", action="store_true",
                        help="Emit the machine-readable report on stdout.")
    parser.add_argument("--repo", default=None,
                        help="Repo root for git worktree discovery (default cwd).")
    parser.add_argument("--lanes", default=None,
                        help="Comma-separated claimed-live lane names.")
    parser.add_argument("--claims", default=None,
                        help="JSON claims file (names or {name,path} objects).")
    parser.add_argument("--transcripts-root", default=None,
                        help="Root for agent-*.jsonl discovery.")

    args = parser.parse_args(argv)

    if args.max_silence < 0:
        sys.stderr.write("ERROR: --max-silence must be >= 0\n")
        return 2

    lanes = None
    names = None
    try:
        if args.claims:
            lanes, names = load_claims(args.claims)
        if names is None and args.lanes:
            names = [n.strip() for n in args.lanes.split(",") if n.strip()]

        if lanes is None:
            repo = args.repo or os.getcwd()
            lanes = discover_lanes(repo)
        if names:
            lanes = apply_name_filter(lanes, names)
    except (LaneUnreadable, LaneDiscoveryError) as exc:
        sys.stderr.write("ERROR: %s\n" % exc)
        if args.json:
            print(json.dumps({"error": str(exc), "exit_code": 2,
                              "lanes": [], "stalled": [],
                              "unreadable": ["<inventory>"]}, indent=2))
        return 2

    transcripts_root = (args.transcripts_root
                        or stall_check.get_transcripts_root())
    try:
        transcript_index = build_transcript_index(transcripts_root)
    except OSError as exc:
        sys.stderr.write("ERROR: transcripts root unreadable: %s\n" % exc)
        return 2

    report = check_lanes(lanes, args.max_silence, transcript_index)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report)

    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
