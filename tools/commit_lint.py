#!/usr/bin/env python3
"""Conventional commit message linter.

Validates commit messages against conventional commits format and project
conventions:
  1. Format: type(scope): description  OR  type: description
  2. Allowed types: feat, fix, refactor, test, docs, chore, ci, perf, style, build
  3. Subject line max 72 chars, no trailing period
  4. Body separated by blank line (if present)
  5. Co-Authored-By trailer format validation
  6. Inputs: --message MSG | --range RANGE | stdin

Exit: 0=clean, 1=violations found, 2=error.
CLI: commit_lint.py [--message MSG] [--range RANGE] [--json] [--check]
"""

import argparse
import json
import re
import subprocess
import sys

ALLOWED_TYPES = frozenset(
    ["feat", "fix", "refactor", "test", "docs", "chore", "ci", "perf", "style", "build"]
)

# type(scope): desc  or  type: desc
SUBJECT_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[a-z0-9_/.-]+)\))?:\s+(?P<desc>.+)$"
)

CO_AUTHOR_RE = re.compile(
    r"^Co-Authored-By:\s+.+\s+<[^>]+>$", re.IGNORECASE
)


def lint_message(raw: str) -> list:
    """Return a list of violation dicts for a single commit message."""
    violations = []

    def add(rule: str, msg: str) -> None:
        violations.append({"rule": rule, "message": msg})

    lines = raw.replace("\r\n", "\n").split("\n")

    # Strip trailing empty lines
    while lines and lines[-1].strip() == "":
        lines = lines[:-1]

    if not lines or not lines[0].strip():
        add("empty-message", "Commit message is empty")
        return violations

    subject = lines[0]

    # --- Subject format ---
    m = SUBJECT_RE.match(subject)
    if not m:
        add("subject-format", f"Subject does not match 'type(scope): desc' or 'type: desc': {subject!r}")
    else:
        ctype = m.group("type")
        if ctype not in ALLOWED_TYPES:
            add("unknown-type", f"Unknown commit type '{ctype}'; allowed: {', '.join(sorted(ALLOWED_TYPES))}")

    # --- Subject length ---
    if len(subject) > 72:
        add("subject-length", f"Subject line is {len(subject)} chars (max 72)")

    # --- Trailing period ---
    if subject.rstrip().endswith("."):
        add("trailing-period", "Subject line must not end with a period")

    # --- Blank line separator ---
    if len(lines) > 1:
        if lines[1].strip() != "":
            add("blank-line", "Second line must be blank (separates subject from body)")

    # --- Co-Authored-By trailer ---
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower().startswith("co-authored-by"):
            if not CO_AUTHOR_RE.match(stripped):
                add("co-authored-by", f"Malformed Co-Authored-By trailer on line {i + 1}: {stripped!r}")

    return violations


def get_commits_from_range(commit_range: str) -> list:
    """Return list of (hash, message) tuples from a git commit range."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H%n%B%n---commit-lint-sep---", commit_range],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    if result.returncode != 0:
        print(f"ERROR: git log failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(2)

    commits = []
    chunks = result.stdout.split("---commit-lint-sep---")
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        first_nl = chunk.find("\n")
        if first_nl == -1:
            continue
        sha = chunk[:first_nl].strip()
        msg = chunk[first_nl + 1:].strip()
        if sha and msg:
            commits.append((sha, msg))
    return commits


def main() -> int:
    parser = argparse.ArgumentParser(description="Conventional commit message linter")
    parser.add_argument("--message", "-m", help="Lint a single commit message string")
    parser.add_argument("--range", "-r", help="Lint commits in a git range (e.g. HEAD~5..HEAD)")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--check", action="store_true", help="Exit 1 on any violation (CI gate mode)")
    args = parser.parse_args()

    # Reject unknown flags (argparse handles this, but be explicit about --help)
    results = []  # list of {"commit": str|None, "violations": list}

    if args.message is not None:
        vs = lint_message(args.message)
        results.append({"commit": None, "violations": vs})
    elif args.range:
        commits = get_commits_from_range(args.range)
        if not commits:
            print("No commits found in range", file=sys.stderr)
            return 2
        for sha, msg in commits:
            vs = lint_message(msg)
            results.append({"commit": sha[:8], "violations": vs})
    else:
        # Read from stdin
        try:
            msg = sys.stdin.read()
        except KeyboardInterrupt:
            return 2
        if not msg.strip():
            print("No input on stdin", file=sys.stderr)
            return 2
        vs = lint_message(msg)
        results.append({"commit": None, "violations": vs})

    # Output
    total_violations = sum(len(r["violations"]) for r in results)

    if args.json:
        out = {"total_violations": total_violations, "results": results}
        print(json.dumps(out, indent=2))
    else:
        for r in results:
            prefix = f"[{r['commit']}] " if r["commit"] else ""
            if r["violations"]:
                for v in r["violations"]:
                    print(f"{prefix}{v['rule']}: {v['message']}")
            else:
                if prefix:
                    print(f"{prefix}OK")

    if total_violations > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
