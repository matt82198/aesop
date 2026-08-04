#!/usr/bin/env python3
"""Generate tools/INDEX.md from per-tool INDEX: docstring lines.

INDEX: Generated tool-index builder; walks `git ls-files tools/`, extracts each tool's `INDEX:` docstring/header line, emits sorted tools/INDEX.md between GENERATED-BY markers; modes `--check` (byte-compare, exit 1 + regenerate hint) / `--regenerate` / `--json`; a scanned tool with NO `INDEX:` line FAILS CLOSED (exit 1) so a new tool cannot land undocumented; deterministic + ASCII-safe; stdlib-only.

Design (A2 of the merge-pipeline debottleneck): the ~120-entry tool index used to
live inline in tools/CLAUDE.md, so every PR that added a tool edited that one file
and collided in the merge queue. The index now lives in each tool's own module
docstring on a line beginning `INDEX:`; this generator collects those lines into a
single generated file (tools/INDEX.md) that no human hand-edits. The byte-identity
gate in claudemd_lint.py keeps the generated file honest.

Extraction: the first line in a scanned file (within the header window) whose
content, after stripping a leading comment marker (`#`, `//`, `*`), begins with
`INDEX:`. Works for Python docstrings (bare `INDEX:`), shell/JS headers (`# INDEX:`
/ `// INDEX:`).

Scan scope: top-level tracked files under tools/ with extensions .py/.sh/.mjs/.js
(the tool languages). Subdirectory files and other extensions are not indexed.

Exit codes: 0 = clean (regenerated, or --check matched), 1 = drift or a tool
missing its INDEX: line (fail-closed), 2 = usage/environment error.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SENTINEL = "<!-- GENERATED-BY: tools/gen_tool_index.py -->"
END_MARKER = "<!-- END-GENERATED -->"
INDEX_PATH = "tools/INDEX.md"
SCAN_EXTS = (".py", ".sh", ".mjs", ".js")

# A header line carrying the index one-liner. Strips an optional leading comment
# marker so the same marker is found in a Python docstring (bare) or a shell/JS
# header comment (# / //). Only the header window is scanned (see HEADER_LINES).
INDEX_RE = re.compile(r"^\s*(?:#+\s*|//+\s*|\*+\s*)?INDEX:\s*(.*\S)\s*$")
HEADER_LINES = 200


def list_tool_files(repo_root: Path):
    """Return sorted repo-relative paths of top-level tools/ files we index."""
    res = subprocess.run(
        ["git", "ls-files", "tools/"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        raise RuntimeError("git ls-files tools/ failed: " + (res.stderr or "").strip())
    out = []
    for line in res.stdout.splitlines():
        rel = line.strip()
        if not rel.startswith("tools/"):
            continue
        tail = rel[len("tools/"):]
        if "/" in tail:  # skip subdirectory files
            continue
        if rel.endswith(SCAN_EXTS):
            out.append(rel)
    return sorted(out)


def extract_index_line(path: Path):
    """Return the tool's INDEX: one-liner, or None if it has none."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for raw in text.splitlines()[:HEADER_LINES]:
        m = INDEX_RE.match(raw)
        if m:
            return m.group(1).strip()
    return None


def collect(repo_root: Path):
    """Return (entries, missing).

    entries: sorted list of (basename, description).
    missing: sorted list of basenames with no INDEX: line (fail-closed trigger).
    """
    entries = []
    missing = []
    for rel in list_tool_files(repo_root):
        name = rel[len("tools/"):]
        desc = extract_index_line(repo_root / rel)
        if desc is None:
            missing.append(name)
        else:
            entries.append((name, desc))
    entries.sort(key=lambda e: e[0])
    missing.sort()
    return entries, missing


def render(entries):
    """Render the deterministic, ASCII INDEX.md body from sorted entries."""
    lines = [
        SENTINEL,
        "# tools/ index (generated -- do not hand-edit)",
        "",
        "One-line purpose per tool, collected from each tool's `INDEX:` docstring line.",
        "Regenerate with `python tools/gen_tool_index.py --regenerate`; a new tool is",
        "listed by adding an `INDEX: <one-liner>` line to its module docstring/header.",
        "",
    ]
    for name, desc in sorted(entries, key=lambda e: e[0]):
        lines.append(f"- `{name}` -- {desc}")
    lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate tools/INDEX.md from per-tool INDEX: docstring lines"
    )
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(), help="Repository root (default: cwd)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="Verify tools/INDEX.md is byte-identical to freshly generated output (default)",
    )
    mode.add_argument(
        "--regenerate", action="store_true", help="Write tools/INDEX.md"
    )
    mode.add_argument("--json", action="store_true", help="Emit entries/missing as JSON")
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    try:
        entries, missing = collect(repo_root)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(
            {
                "entries": [{"name": n, "index": d} for n, d in entries],
                "missing": missing,
                "count": len(entries),
            },
            indent=2,
        ))
        return 1 if missing else 0

    # Fail closed: any scanned tool missing its INDEX: line is an error, and we
    # never write a partial index that silently drops it.
    if missing:
        print(
            "ERROR: tools/ files missing an INDEX: docstring line (fail-closed):",
            file=sys.stderr,
        )
        for name in missing:
            print(f"  - tools/{name}", file=sys.stderr)
        print(
            "Add `INDEX: <one-liner>` to each file's module docstring/header.",
            file=sys.stderr,
        )
        return 1

    expected = render(entries)
    index_file = repo_root / INDEX_PATH

    if args.regenerate:
        index_file.write_text(expected, encoding="utf-8", newline="\n")
        print(f"[OK] wrote {INDEX_PATH} ({len(entries)} tools)")
        return 0

    # Default mode is --check.
    if not index_file.exists():
        print(
            f"ERROR: {INDEX_PATH} is missing; run: python tools/gen_tool_index.py --regenerate",
            file=sys.stderr,
        )
        return 1
    actual = index_file.read_text(encoding="utf-8")
    if actual != expected:
        print(
            f"ERROR: {INDEX_PATH} is out of date; run: "
            f"python tools/gen_tool_index.py --regenerate",
            file=sys.stderr,
        )
        return 1
    print(f"[OK] {INDEX_PATH} is in sync ({len(entries)} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
