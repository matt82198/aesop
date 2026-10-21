#!/usr/bin/env python3
"""
tools.stateapi_lint — Scanner for direct state file opens outside the API.
INDEX: StateAPI migration ratchet: AST-scans for direct state-file reads outside state_store/read_api.py facade; violations keyed file@pattern-id against committed baseline (new violation = exit 1; fixed violation = must shrink baseline); `--update-baseline` regenerates (forbidden in CI)

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
import ast
import io
import json
import re
import sys
import tokenize
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

# Files allowed direct state-file access: the state layer's own implementation
# (writers and the facade itself), plus tools whose contract is to inspect the
# literal bytes on disk. Everything else must go through the facade.
WRITER_ALLOWLIST = [
    "state_store/export.py",
    "state_store/ingest.py",
    "state_store/read_api.py",  # The read API facade itself (reads the state files)
    "state_store/write_api.py",  # The write API facade (reads/writes the projection atomically)
    "state_store/materialize.py",  # Canonical materializer: the component that PRODUCES
                                   # tracker.json/STATE.md/status.json from the event log.
                                   # Same class as export.py/write_api.py; routing it through
                                   # the read facade would be circular.
    "ui/collectors.py",  # Some readers also export/flush
    "tools/cost.py",  # Parses ledger
    "tools/state_md_verifier.py",  # Verifies the on-disk STATE.md against git truth; it must
                                   # read the literal file, since reading the projection
                                   # instead would defeat the drift check it exists to make.
    "tools/test_isolation_tripwire.py",  # Hashes the literal bytes of the REAL fleet-state root
                                   # heartbeat/state files (watchdog/monitor heartbeats,
                                   # state/*.json) before/after a wrapped command to prove a
                                   # test run did not leak into live state; it must reference
                                   # those literal on-disk paths, since reading via the
                                   # projection facade would defeat the drift check itself.
]

# Markdown files that should only be written via the WriteAPI facade
MARKDOWN_FILES_TO_PROTECT = [
    "STATE.md",
    "BUILDLOG.md",
]

# Directories to scan for violations
SCAN_DIRS = ["ui", "tools", "state_store", "driver"]  # Include state_store to catch internal issues

# Directories where a raw tracker.json WRITE is a violation (F-01). The write check is
# AST-based and runs even when a file imports the WriteAPI facade: a facade import
# exempts a file from the string-literal read patterns, which must not also exempt a
# regression that writes tracker.json directly (the driver/wave_scheduler.py bug).
TRACKER_WRITE_GATE_DIRS = ["driver"]
_TRACKER_TOKENS = ("tracker.json", "tracker_path", "tracker_file")


def _mentions_tracker(node):
    """True if the expression's source text names tracker.json or a tracker path var."""
    try:
        src = ast.unparse(node)
    except Exception:
        return False
    return any(tok in src for tok in _TRACKER_TOKENS)


def tracker_raw_write_sites(source):
    """Return line numbers of raw writes whose target is a tracker.json path.

    Taint-lite: a name is tracked if it is a tracker_path/tracker_file parameter or is
    assigned from an expression naming tracker.json or a tracked name. Sinks:
    os.replace/os.rename/shutil.move destination, Path.write_text receiver, and
    open() in a write/append/create mode on a tracked path.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    tracked = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.arg in ("tracker_path", "tracker_file"):
            tracked.add(node.arg)

    def _is_tracked(expr):
        if isinstance(expr, ast.Name) and expr.id in tracked:
            return True
        return _mentions_tracker(expr)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not _is_tracked(node.value):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in tracked:
                    tracked.add(target.id)
                    changed = True

    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in ("replace", "rename", "move") and len(node.args) >= 2 and _is_tracked(node.args[1]):
            lines.append(node.lineno)
        elif name == "write_text" and isinstance(func, ast.Attribute) and _is_tracked(func.value):
            lines.append(node.lineno)
        elif name == "open" and node.args and _is_tracked(node.args[0]):
            mode = ""
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if any(ch in mode for ch in "wax"):
                lines.append(node.lineno)
    return sorted(set(lines))


def _byte_col_to_char(line, byte_col):
    """Convert an ast (UTF-8 byte) column into a character index for `line`."""
    return len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))


def python_code_view(source):
    """Return `source` with docstrings and comments blanked to spaces.

    Line count and column positions are preserved, so the regex patterns below only
    ever match code and non-docstring string literals: a state path named in a module,
    class or function docstring, or in a ``#`` comment, is documentation, not an access.
    Real accesses (open/Path/os calls, any other string literal) are left intact.

    Falls back to the raw source when the file cannot be parsed or tokenized, so a
    broken file keeps the old (stricter) raw-text detection instead of hiding accesses.
    """
    try:
        tree = ast.parse(source)
        lines = source.split("\n")
        spans = []  # (start_row, start_char, end_row, end_char); end is exclusive
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.body:
                continue
            first = node.body[0]
            if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                continue
            s_line = lines[first.lineno - 1]
            e_line = lines[first.end_lineno - 1]
            spans.append((
                first.lineno,
                _byte_col_to_char(s_line, first.col_offset),
                first.end_lineno,
                _byte_col_to_char(e_line, first.end_col_offset),
            ))
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                spans.append((row, col, row, len(lines[row - 1])))
    except Exception:
        return source

    grid = [list(line) for line in lines]
    for r1, c1, r2, c2 in spans:
        for row in range(r1, r2 + 1):
            start = c1 if row == r1 else 0
            end = c2 if row == r2 else len(grid[row - 1])
            for col in range(start, min(end, len(grid[row - 1]))):
                grid[row - 1][col] = " "
    return "\n".join("".join(row) for row in grid)


# Write-capable call shapes. A module is a "writer" only when it contains one of these
# (AST-detected on real code, so a docstring or comment naming a write does not count).
_WRITE_METHODS = frozenset({"write_text", "write_bytes"})
_SHUTIL_WRITERS = frozenset({"copy", "copy2", "copyfile", "copytree", "move"})
_OS_WRITERS = frozenset({"replace", "rename"})
_OS_WRITE_FLAGS = frozenset({"O_WRONLY", "O_RDWR", "O_APPEND", "O_CREAT", "O_TRUNC"})
_SUBPROCESS_CALLS = frozenset({"run", "Popen", "call", "check_call", "check_output"})
_WRITE_MODE_CHARS = frozenset("wax+")

# Patterns whose firing additionally requires a write operation in the same module.
# buildlog-md-write: a tool that only READS BUILDLOG.md (e.g. a diff/metrics gate naming
# it in a constant) is not a writer; a module that names it AND writes is still flagged.
WRITE_OPERATION_GATED_IDS = frozenset({"buildlog-md-write"})

# STATE.md write rules fire only when a write-shaped call's own arguments name the file
# (state_md_written_in_call), so a set/list literal or comparison naming STATE.md is not a write.
STATE_MD_WRITE_IDS = frozenset({"state-md-write", "state-state-md-write"})


def _mode_is_write(mode_node):
    """A literal open() mode with w/a/x/+ writes; a non-literal mode fails closed."""
    if mode_node is None:
        return False
    if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
        return any(ch in _WRITE_MODE_CHARS for ch in mode_node.value)
    return True


def _walk_constants(node):
    """Yield every string constant under the call's arguments."""
    for part in [*node.args, *(k.value for k in node.keywords)]:
        for sub in ast.walk(part):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                yield sub.value


def _call_is_write(node):
    """True when this ast.Call can write a file."""
    func = node.func
    if isinstance(func, ast.Name):
        if func.id != "open":
            return False
        mode = next((k.value for k in node.keywords if k.arg == "mode"), None)
        if mode is None and len(node.args) >= 2:
            mode = node.args[1]
        return _mode_is_write(mode)
    if not isinstance(func, ast.Attribute):
        return False
    attr = func.attr
    owner = func.value.id if isinstance(func.value, ast.Name) else None
    if attr in _WRITE_METHODS:
        return True
    if attr == "open":
        if owner == "os":
            return any(
                isinstance(sub, ast.Attribute) and sub.attr in _OS_WRITE_FLAGS
                for part in node.args
                for sub in ast.walk(part)
            )
        # Path(...).open(mode): the mode is the first positional argument.
        mode = next((k.value for k in node.keywords if k.arg == "mode"), None)
        if mode is None and node.args:
            mode = node.args[0]
        return _mode_is_write(mode)
    if owner == "shutil" and attr in _SHUTIL_WRITERS:
        return True
    if owner == "os" and attr in _OS_WRITERS:
        return True
    if owner == "subprocess" and attr in _SUBPROCESS_CALLS:
        return any(
            ">>" in text or text in ("-a", "--append") for text in _walk_constants(node)
        )
    return False


def module_has_write_operation(source):
    """Return True when `source` contains a write-capable call.

    Docstrings and comments are ignored (only real Call nodes count). A module that
    cannot be parsed returns True, failing closed so the gated rules keep their reach.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return True
    return any(isinstance(node, ast.Call) and _call_is_write(node) for node in ast.walk(tree))


def state_md_written_in_call(source):
    """Return True when a write-capable call's own arguments name STATE.md.

    Per-statement, not per-module: a STATE.md literal in a set, list or comparison,
    or passed to a read-only call, is not a write, even in a module that writes some
    other file elsewhere. Only a write-shaped Call whose subtree contains the literal
    counts. An unparseable module returns True (fail closed, the regex still applies).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_is_write(node):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and "STATE.md" in sub.value:
                    return True
    return False


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

            # F-01: raw tracker.json writes are checked BEFORE the facade-import skip below.
            if scan_dir in TRACKER_WRITE_GATE_DIRS and tracker_raw_write_sites(content):
                violation_key = f"{relative_path.as_posix()}@tracker-json-raw-write"
                if violation_key not in violations:
                    violations.append(violation_key)

            # Skip if file imports read_api or write_api (it's using the facade correctly)
            if "from state_store.read_api import" in content or "import state_store.read_api" in content:
                continue
            if "from state_store.write_api import" in content or "import state_store.write_api" in content:
                continue
            # StateAPI re-exported from the package root is the same facade; a caller
            # doing `from state_store import StateAPI` is using it correctly and was
            # previously misreported as a bypass.
            if "from state_store import StateAPI" in content or "from state_store import (" in content and "StateAPI" in content:
                continue

            # Scan for violations over the code view: docstrings and comments are blanked
            # (AST/tokenize-aware), so only code and non-docstring string literals match.
            can_write = module_has_write_operation(content)
            state_md_written = state_md_written_in_call(content)

            for line_num, line in enumerate(python_code_view(content).split("\n"), 1):
                # Check read violations
                for pattern, pattern_id in read_patterns:
                    if re.search(pattern, line):
                        # Key is file@pattern-id (stable across line edits)
                        # Use posix separators for cross-platform consistency
                        violation_key = f"{relative_path.as_posix()}@{pattern_id}"
                        if violation_key not in violations:
                            violations.append(violation_key)
                        break  # Only report once per line per file

                # Check write violations
                for pattern, pattern_id in write_patterns:
                    if pattern_id in WRITE_OPERATION_GATED_IDS and not can_write:
                        continue
                    if pattern_id in STATE_MD_WRITE_IDS and not state_md_written:
                        continue
                    if re.search(pattern, line):
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
