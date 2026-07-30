#!/usr/bin/env python3
"""
tools.stateapi_lint — Scanner for direct state file opens outside the API.

Detects violations of the "reads go through state_store.read_api" rule by scanning
ui/ and tools/ for direct opens of state files (tracker.json, orchestrator-status.json,
heartbeat files, OUTCOMES-LEDGER.md) outside the read_api module.

Writers (state_store/export.py, state_store/ingest.py, etc.) are allowlisted.

RATCHET MODE (hardening):
  Baseline is keyed by file + pattern-id (NOT line numbers).
  Without --update-baseline flag:
    - FAILS if baseline contains entries absent from current scan (stale baseline)
    - FAILS if current violations missing from baseline (new violations)
    - PASS only if: current violations exactly match baseline entries
  With --update-baseline flag:
    - Allows regenerating the baseline (sets current as new baseline)

  CI MUST NEVER pass --update-baseline (hand-edits of baseline detected & fail).

Exit codes:
  0 = all checks pass
  1 = violations detected or baseline mismatch
  2 = error

Usage:
  python tools/stateapi_lint.py [--root REPO_ROOT] [--json] [--update-baseline]

Options:
  --root REPO_ROOT: Repository root (default: cwd)
  --json: Output JSON instead of text
  --update-baseline: Regenerate baseline from current violations (CI must never use)
"""
import json
import re
import sys
from pathlib import Path


# State files that should only be read via the API
STATE_FILES_TO_PROTECT = [
    "tracker.json",
    "orchestrator-status.json",
    "OUTCOMES-LEDGER.md",
    ".watchdog-heartbeat",
    ".monitor-heartbeat",
    ".orchestrator-heartbeat",
]

# File patterns that are WRITERS and allowed to access state files directly
WRITER_ALLOWLIST = [
    "state_store/export.py",
    "state_store/ingest.py",
    "state_store/materialize.py",  # Canonical materializer: renders all derived views
    "state_store/read_api.py",  # The read API facade itself (reads the state files)
    "state_store/write_api.py",  # The write API facade (reads/writes the projection atomically)
    "ui/collectors.py",  # Some readers also export/flush
    "tools/cost.py",  # Parses ledger
]

# Markdown files that should only be written via the WriteAPI facade
MARKDOWN_FILES_TO_PROTECT = [
    "STATE.md",
    "BUILDLOG.md",
]

# Directories to scan for violations
SCAN_DIRS = ["ui", "tools", "state_store"]  # Include state_store to catch internal issues


def find_direct_opens(repo_root):
    """Scan repo for direct opens/writes of protected state files outside the API.

    Returns violations keyed by file + pattern-id (not line numbers).
    Pattern-id is the matched pattern index, making the key stable across line edits.

    Args:
        repo_root: Path to repository root

    Returns:
        list: List of violation keys (file@pattern-id format)
    """
    repo_root = Path(repo_root)
    violations = []

    # Pattern to detect file opens: Path(...).read_text(), open(...), json.load(open(...)), etc.
    # Each pattern gets an ID for stable keying across line number changes
    read_patterns = [
        (r'["\']tracker\.json["\']', "tracker-json"),
        (r'["\']orchestrator-status\.json["\']', "status-json"),
        (r'["\']OUTCOMES-LEDGER\.md["\']', "ledger-md"),
        (r'["\']\.watchdog-heartbeat["\']', "watchdog-hb"),
        (r'["\']\.monitor-heartbeat["\']', "monitor-hb"),
        (r'["\']\.orchestrator-heartbeat["\']', "orchestrator-hb"),
        (r'state\s*/\s*tracker\.json', "state-tracker-json"),
        (r'state\s*/\s*orchestrator-status', "state-status-json"),
        (r'state\s*/\s*.*heartbeat', "state-heartbeat"),
    ]

    # Patterns to detect direct writes to markdown control files
    # These should only be written through WriteAPI
    write_patterns = [
        (r'["\']STATE\.md["\']', "state-md-write"),
        (r'["\']BUILDLOG\.md["\']', "buildlog-md-write"),
        (r'state\s*/\s*STATE\.md', "state-state-md-write"),
        (r'state\s*/\s*BUILDLOG\.md', "state-buildlog-md-write"),
    ]

    for scan_dir in SCAN_DIRS:
        scan_path = repo_root / scan_dir
        if not scan_path.exists():
            continue

        for py_file in scan_path.rglob("*.py"):
            # Check if this file is in the allowlist
            relative_path = py_file.relative_to(repo_root)
            is_allowed = any(
                str(relative_path).replace("\\", "/") == allow.replace("\\", "/")
                for allow in WRITER_ALLOWLIST
            )

            if is_allowed:
                continue

            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Skip if file imports read_api or write_api (it's using the facade correctly)
            if "from state_store.read_api import" in content or "import state_store.read_api" in content:
                continue
            if "from state_store.write_api import" in content or "import state_store.write_api" in content:
                continue

            # Scan for violations
            for line_num, line in enumerate(content.split("\n"), 1):
                # Check read violations
                for pattern, pattern_id in read_patterns:
                    if re.search(pattern, line):
                        # Additional filter: skip comment lines and string literals in docstrings
                        if line.strip().startswith("#"):
                            continue
                        if '"""' in line or "'''" in line:
                            continue

                        # Key is file@pattern-id (stable across line edits)
                        # Use posix separators for cross-platform consistency
                        violation_key = f"{relative_path.as_posix()}@{pattern_id}"
                        if violation_key not in violations:
                            violations.append(violation_key)
                        break  # Only report once per line per file

                # Check write violations
                for pattern, pattern_id in write_patterns:
                    if re.search(pattern, line):
                        # Skip comment lines and docstrings
                        if line.strip().startswith("#"):
                            continue
                        if '"""' in line or "'''" in line:
                            continue

                        violation_key = f"{relative_path.as_posix()}@{pattern_id}"
                        if violation_key not in violations:
                            violations.append(violation_key)
                        break

    return sorted(violations)


def save_baseline(baseline_file, data):
    """Save violations baseline to JSON file.

    Args:
        baseline_file: Path to baseline file
        data: dict with "violations" list
    """
    baseline_file = Path(baseline_file)
    baseline_file.parent.mkdir(parents=True, exist_ok=True)
    baseline_file.write_text(json.dumps(data, indent=2))


def load_baseline(baseline_file):
    """Load violations baseline from JSON file.

    Returns:
        dict with "violations" list, or empty dict if file missing.
    """
    baseline_file = Path(baseline_file)
    if not baseline_file.exists():
        return {"violations": []}

    try:
        return json.loads(baseline_file.read_text())
    except Exception:
        return {"violations": []}


def normalize_key(key):
    """Normalize violation keys to forward-slash separators for cross-platform consistency.

    Converts backslash separators (legacy Windows baseline entries) to forward slashes.

    Args:
        key: Violation key string

    Returns:
        str: Key with forward-slash separators
    """
    return key.replace("\\", "/")


def check_ratchet(baseline_violations, current_violations):
    """Check ratchet: baseline and current must match exactly.

    Fails if:
      1. Baseline contains entries absent from current (stale baseline)
      2. Current violations missing from baseline (new violations)

    Only passes if current violations exactly match baseline (bidirectional check).
    Normalizes both sets to forward-slash separators for cross-platform consistency.

    Args:
        baseline_violations: list of violation keys from baseline
        current_violations: list of violation keys from current scan

    Returns:
        tuple: (is_ok, stale_entries, new_violations)
          is_ok (bool): True only if baseline == current
          stale_entries (list): Entries in baseline not in current
          new_violations (list): Entries in current not in baseline
    """
    # Normalize both to forward slashes for comparison
    baseline_set = set(normalize_key(v) for v in baseline_violations)
    current_set = set(normalize_key(v) for v in current_violations)

    stale = list(baseline_set - current_set)
    new = list(current_set - baseline_set)

    is_ok = (len(stale) == 0 and len(new) == 0)
    return is_ok, sorted(stale), sorted(new)


def main(argv=None):
    """CLI entry point."""
    argv = sys.argv[1:] if argv is None else argv

    repo_root = None
    output_format = "text"
    baseline_file = None
    update_baseline = False

    # Parse arguments
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(
                "Usage: stateapi_lint.py [--root PATH] [--json] "
                "[--baseline FILE] [--update-baseline]"
            )
            return 0
        elif arg == "--root":
            i += 1
            if i < len(argv):
                repo_root = argv[i]
            i += 1
        elif arg.startswith("--root="):
            repo_root = arg[len("--root="):]
            i += 1
        elif arg == "--json":
            output_format = "json"
            i += 1
        elif arg == "--baseline":
            i += 1
            if i < len(argv):
                baseline_file = argv[i]
            i += 1
        elif arg == "--update-baseline":
            update_baseline = True
            i += 1
        else:
            print(f"Unknown argument: {arg}", file=sys.stderr)
            return 2

    if repo_root is None:
        repo_root = Path.cwd()
    else:
        repo_root = Path(repo_root)

    if baseline_file is None:
        baseline_file = repo_root / ".stateapi-baseline.json"

    # Scan for violations
    violations = find_direct_opens(str(repo_root))

    # Load baseline
    baseline_data = load_baseline(str(baseline_file))
    baseline_violations = baseline_data.get("violations", [])

    # If --update-baseline, save current as baseline and exit
    if update_baseline:
        save_baseline(baseline_file, {"violations": violations})
        if output_format == "json":
            print(json.dumps({"ok": True, "message": "Baseline updated", "count": len(violations)}, indent=2))
        else:
            print(f"Baseline updated: {len(violations)} violation(s) recorded")
        return 0

    # Check ratchet: baseline must match current exactly
    is_ok, stale_entries, new_violations = check_ratchet(baseline_violations, violations)

    if output_format == "json":
        result = {
            "ok": is_ok,
            "violations": violations,
            "baseline_count": len(baseline_violations),
            "current_count": len(violations),
            "stale_entries": stale_entries,
            "new_violations": new_violations,
        }
        print(json.dumps(result, indent=2))
    else:
        # Text format
        print(f"State API lint: {len(violations)} violation(s) found")
        if baseline_violations:
            print(f"  (baseline: {len(baseline_violations)})")

        if violations:
            for v in sorted(violations):
                print(f"  {v}")

        # Report stale entries (in baseline but not current)
        if stale_entries:
            print(f"\nSTALE baseline entries ({len(stale_entries)}) — hand-edited or fixed?:")
            for v in stale_entries:
                print(f"  {v}")

        # Report new violations (in current but not baseline)
        if new_violations:
            print(f"\nNEW violations ({len(new_violations)}):")
            for v in new_violations:
                print(f"  {v}")

        # Final verdict
        if is_ok:
            if violations:
                print(f"\nPASS: All {len(violations)} violations match baseline")
            else:
                print("\nPASS: No violations")
            return 0
        else:
            if stale_entries and new_violations:
                print(f"\nFAIL: {len(stale_entries)} stale + {len(new_violations)} new violation(s)")
            elif stale_entries:
                print(f"\nFAIL: {len(stale_entries)} stale baseline entries (hand-edited?)")
            else:
                print(f"\nFAIL: {len(new_violations)} new violation(s) detected")
            return 1


if __name__ == "__main__":
    sys.exit(main())
