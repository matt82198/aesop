#!/usr/bin/env python3
"""
Conflict marker detector.
INDEX: Scans tracked text files for literal unresolved git conflict markers
(<<<<<<<, =======, >>>>>>>, |||||||) -- catches a clean-merge landing a literal
conflict block that passed every other gate (PR #834 incident, merge commit
b2c4db77 landed `<<<<<<< HEAD` in tools/CLAUDE.md); CLI: `[--check]` (default,
full tracked tree) | `--staged RANGE` (pushed diff only, added lines, for the
pre-push hook) | `--files PATH...` (manual/test, worktree read) | `--root DIR`
| `--allowlist FILE` | `--json`; skips binaries (NUL-byte sniff) and paths
listed in the fixture allowlist; `# conflict-marker-ok` on the marker line
suppresses it inline; exit 0=clean/1=findings/2=error (fail-closed, a git
failure is never reported as clean); stdlib-only, fast (<2s on a normal repo).

Incident: PR #834's merge commit b2c4db77 landed a literal `<<<<<<< HEAD`
conflict block in tools/CLAUDE.md on main. claudemd_lint, claudemd_sync_gate,
and all PR CI passed -- none of them scan for literal conflict markers, so a
bad merge resolution (or a merge driver stripping one side of the markers but
leaving the start marker behind) sailed straight to main. This tool closes
that gap structurally: every push and every CI run scans for the four marker
line shapes a real git conflict leaves behind, regardless of file type.

Marker patterns (diff3-aware):
  ^<{7} <ref>   -- conflict start  (e.g. "<<<<<<< HEAD")
  ^={7}$        -- conflict middle (exactly "=======", nothing else)
  ^>{7} <ref>   -- conflict end    (e.g. ">>>>>>> feature-branch")
  ^\\|{7} <ref>  -- diff3 common-ancestor marker (e.g. "||||||| merged common ancestors")

`--staged RANGE` scans only ADDED lines (diff lines prefixed with a single
`+`, never `+++`) in the given commit range's diff -- this is what
hooks/pre-push-policy.sh feeds it (mirrors tools/secret_scan.py and
tools/import_resolution_check.py's `--range` convention; named `--staged` to
match the incident report's wording for "the pre-push hook's diff mode").
Scanning only added lines means a marker a prior commit already introduced
(and which the full-tree `--check` gate is responsible for) does not get
double-reported by every subsequent unrelated push touching the same file --
the diff mode's job is to catch a NEW marker landing in THIS push.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MARKER_PATTERNS = (
    ("conflict-start", "<<<<<<< "),
    ("conflict-end", ">>>>>>> "),
    ("conflict-base", "||||||| "),
)
MARKER_MID = "======="

SUPPRESS_MARKER = "conflict-marker-ok"

DEFAULT_ALLOWLIST_NAME = ".conflict-marker-allowlist.json"

# Extensions that are unambiguously binary; skipped without even opening them.
# The NUL-byte sniff below is the real (and sufficient) binary detector --
# this list only saves the open() call for the common cases.
BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
    ".pdf", ".zip", ".gz", ".tar", ".7z", ".whl",
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".dylib",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".wasm",
})


class GitError(Exception):
    """A git invocation failed. Callers MUST fail closed (exit 2), never
    treat this as 'nothing to check' -- that is the vacuous-green failure
    mode this whole tool class exists to avoid."""


def _git(args, cwd=None, timeout=30):
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=timeout,
        )
    except Exception as e:
        raise GitError("git %s raised %r" % (" ".join(args), e))
    if result.returncode != 0:
        raise GitError(
            "git %s failed (rc=%d): %s"
            % (" ".join(args), result.returncode, (result.stderr or "").strip())
        )
    return result.stdout


def _normalize(path):
    return path.replace("\\", "/")


def line_marker_type(line):
    """Return the marker-type label if `line` (no trailing newline) is a
    conflict marker line, else None. Checked against the RAW line (markers
    anchor at column 0); callers strip diff '+' prefixes etc. before calling."""
    if line == MARKER_MID:
        return "conflict-mid"
    for label, prefix in MARKER_PATTERNS:
        if line.startswith(prefix):
            return label
    return None


def load_allowlist(allowlist_path):
    """Load the fixture allowlist: repo-relative paths that are permitted to
    contain markers in their committed/working content (e.g. test fixtures
    that intentionally exercise this very gate). Missing file = empty
    allowlist (fail-closed: nothing is exempt by default)."""
    if not allowlist_path or not os.path.isfile(allowlist_path):
        return frozenset()
    try:
        with open(allowlist_path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise GitError("could not read allowlist %s: %s" % (allowlist_path, e))
    paths = data.get("paths", []) if isinstance(data, dict) else data
    return frozenset(_normalize(p) for p in paths)


def is_binary_path(path):
    return Path(path).suffix.lower() in BINARY_EXTENSIONS


def looks_binary(raw_bytes):
    """NUL-byte sniff over the first 8000 bytes -- the same heuristic git
    itself uses to decide whether to say 'Binary files differ'."""
    return b"\x00" in raw_bytes[:8000]


def scan_text(path_label, text, findings):
    """Scan full file text (one marker check per line, column-0 anchored)."""
    for lineno, line in enumerate(text.splitlines(), start=1):
        marker = line_marker_type(line)
        if marker is None:
            continue
        if SUPPRESS_MARKER in line:
            continue
        findings.append({
            "file": path_label,
            "line": lineno,
            "type": marker,
            "message": "literal conflict marker: %r" % line,
        })


def get_tracked_files(repo_root):
    out = _git(["ls-files"], cwd=repo_root, timeout=15)
    return [f for f in out.strip().split("\n") if f.strip()]


def check_tree(repo_root, allowlist):
    """Full-tree mode: every tracked file, read from the working tree."""
    findings = []
    for rel in get_tracked_files(repo_root):
        norm = _normalize(rel)
        if norm in allowlist:
            continue
        if is_binary_path(norm):
            continue
        abspath = os.path.join(repo_root, rel)
        try:
            with open(abspath, "rb") as fh:
                raw = fh.read()
        except OSError:
            # Deleted/renamed between ls-files and read (race) or a symlink
            # to nowhere -- not a finding, nothing to scan.
            continue
        if looks_binary(raw):
            continue
        text = raw.decode("utf-8", errors="replace")
        scan_text(norm, text, findings)
    return findings


def _range_tip_ref(commit_range):
    if "..." in commit_range:
        tip = commit_range.split("...", 1)[1]
    elif ".." in commit_range:
        tip = commit_range.split("..", 1)[1]
    else:
        tip = commit_range
    return tip or "HEAD"


def check_range_diff(repo_root, commit_range, allowlist):
    """Diff mode: only ADDED lines in the given commit range are scanned --
    this is what catches a marker introduced BY this push without
    re-flagging a marker a previous push already landed (that is the
    full-tree gate's job)."""
    findings = []
    changed = _git(
        ["diff", "--name-only", "--diff-filter=d", commit_range],
        cwd=repo_root, timeout=30,
    )
    files = [f for f in changed.strip().split("\n") if f.strip()]
    if not files:
        return findings

    tip_ref = _range_tip_ref(commit_range)

    diff_out = _git(
        ["diff", "--unified=0", "--diff-filter=d", commit_range, "--"] + files,
        cwd=repo_root, timeout=30,
    )

    current_file = None
    current_binary = False
    for raw_line in diff_out.split("\n"):
        if raw_line.startswith("diff --git "):
            current_file = None
            current_binary = False
            continue
        if raw_line.startswith("Binary files ") and raw_line.endswith(" differ"):
            current_binary = True
            continue
        if raw_line.startswith("+++ "):
            path = raw_line[4:].strip()
            if path == "/dev/null":
                current_file = None
            else:
                # "+++ b/path/to/file"
                current_file = _normalize(path[2:] if path.startswith("b/") else path)
            continue
        if current_file is None or current_binary:
            continue
        if not raw_line.startswith("+"):
            continue
        if is_binary_path(current_file):
            continue
        if current_file in allowlist:
            continue
        added = raw_line[1:]
        marker = line_marker_type(added)
        if marker is None:
            continue
        if SUPPRESS_MARKER in added:
            continue
        # Line number within the new file: look it up via blame-free re-grep
        # of the tip blob rather than tracking hunk headers (--unified=0 keeps
        # hunk math simple, but exact line numbers aren't load-bearing here --
        # the message already carries the literal marker text and file).
        findings.append({
            "file": current_file,
            "line": None,
            "type": marker,
            "message": "literal conflict marker introduced in %s: %r" % (tip_ref, added),
        })
    return findings


def check_files(repo_root, paths, allowlist):
    """Manual mode: explicit paths, read from the working tree. Used by
    tests and ad-hoc invocations."""
    findings = []
    for rel in paths:
        norm = _normalize(rel)
        if norm in allowlist:
            continue
        if is_binary_path(norm):
            continue
        abspath = rel if os.path.isabs(rel) else os.path.join(repo_root, rel)
        try:
            with open(abspath, "rb") as fh:
                raw = fh.read()
        except OSError as e:
            raise GitError("could not read %s: %s" % (rel, e))
        if looks_binary(raw):
            continue
        text = raw.decode("utf-8", errors="replace")
        scan_text(norm, text, findings)
    return findings


def format_findings_text(findings):
    lines = []
    for f in findings:
        loc = "%s:%s" % (f["file"], f["line"]) if f["line"] else f["file"]
        lines.append("%s: %s (%s)" % (loc, f["message"], f["type"]))
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Scan for literal unresolved git conflict markers."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true",
        help="Full tracked-tree scan (default mode).",
    )
    mode.add_argument(
        "--staged", metavar="RANGE",
        help="Diff mode: scan only ADDED lines in this commit range "
             "(e.g. abc123..def456). Used by the pre-push hook.",
    )
    mode.add_argument(
        "--files", nargs="+", metavar="PATH",
        help="Manual mode: scan these specific worktree paths.",
    )
    parser.add_argument("--root", help="Repo root (default: git rev-parse --show-toplevel).")
    parser.add_argument(
        "--allowlist", help="Path to the fixture allowlist JSON "
        "(default: <root>/tools/%s if present)." % DEFAULT_ALLOWLIST_NAME,
    )
    parser.add_argument("--json", action="store_true", help="JSON output.")
    return parser


def resolve_repo_root(explicit):
    if explicit:
        return explicit
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def main(argv=None):
    args = build_parser().parse_args(argv)

    repo_root = resolve_repo_root(args.root)
    if not repo_root:
        print("ERROR: not in a git repository (and no --root given)", file=sys.stderr)
        return 2

    allowlist_path = args.allowlist or os.path.join(repo_root, "tools", DEFAULT_ALLOWLIST_NAME)

    try:
        allowlist = load_allowlist(allowlist_path)

        if args.staged:
            findings = check_range_diff(repo_root, args.staged, allowlist)
        elif args.files:
            findings = check_files(repo_root, args.files, allowlist)
        else:
            findings = check_tree(repo_root, allowlist)
    except GitError as e:
        print("FATAL: %s" % e, file=sys.stderr)
        print("Failing CLOSED: refusing to report CLEAN on an unverifiable input.",
              file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"findings": findings, "clean": not findings}, sort_keys=True))
    else:
        if findings:
            print("ERROR: literal conflict marker(s) found:", file=sys.stderr)
            print(format_findings_text(findings), file=sys.stderr)
        else:
            print("OK: no conflict markers found")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
