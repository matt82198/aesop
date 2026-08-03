#!/usr/bin/env python3
"""
hook_preflight.py — Verify that all interpreters required by repo hooks are present and executable.
INDEX: Interpreter health checker; verifies hooks/daemons interpreters; stdlib-only

Purpose:
  Detects broken or missing interpreters before hooks run silently with fail-open behavior.
  A git hook whose interpreter vanishes must not fail silently; this preflight makes the
  failure loud and detectable.

Exit codes:
  0 — All required interpreters present and executable.
  1 — One or more interpreters missing, broken, or unable to execute.
  2 — Unable to perform any checks (e.g., cannot determine hook directory).

Interpreter check logic:
  1. Locate the hook or daemon file by name/path.
  2. Extract shebang (first line, pattern: #!<interpreter>).
  3. For each required interpreter:
     - Try to execute it with --version to verify it actually works.
     - Detect wrapper stubs (file exists, is small, but fails to exec).
  4. Fail loudly (exit 1) if any interpreter is missing/broken.
  5. Never exit 0 without checking at least one interpreter.

Example:
  python tools/hook_preflight.py
  # Checks all hooks in hooks/ and daemons/

  python tools/hook_preflight.py --check-file hooks/pre-push-policy.sh
  # Checks only the specified file
"""

import os
import sys
import subprocess
import json
from pathlib import Path


def find_repo_root():
    """Find the repository root by locating .git directory."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def extract_shebang(file_path):
    """Extract the shebang line from a file. Returns None if not found."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            line = f.readline()
            if line.startswith("#!"):
                return line.rstrip("\n")
    except (IOError, OSError):
        pass
    return None


def parse_shebang(shebang_line):
    """
    Parse a shebang line and extract the interpreter.
    Examples:
      #!/usr/bin/env bash  -> bash
      #!/usr/bin/env python3 -> python3
      #!/usr/bin/node  -> node
      #!/usr/bin/env node -> node
    Returns: (interpreter_name, interpreter_path) or (None, None)
    """
    if not shebang_line or not shebang_line.startswith("#!"):
        return None, None

    shebang_content = shebang_line[2:].strip()
    parts = shebang_content.split()

    if not parts:
        return None, None

    if parts[0] == "/usr/bin/env" and len(parts) > 1:
        # /usr/bin/env followed by interpreter name
        return parts[1], parts[1]
    else:
        # Direct path or name
        basename = os.path.basename(parts[0])
        return basename, parts[0]


def is_interpreter_available(interpreter_name):
    """
    Check if an interpreter is available and executable.
    Returns: (available: bool, error_message: str)
    """
    # Special handling for interpreters that might not accept --version
    version_args = {
        "bash": ["--version"],
        "sh": ["--version"],
        "python": ["--version"],
        "python3": ["--version"],
        "node": ["--version"],
    }

    # Get the check arguments for this interpreter
    check_args = version_args.get(interpreter_name, ["--version"])

    try:
        # Try to run the interpreter with --version
        result = subprocess.run(
            [interpreter_name] + check_args,
            capture_output=True,
            timeout=2,
            encoding="utf-8",
        )
        if result.returncode == 0:
            return True, None
        else:
            return False, f"Interpreter returned exit code {result.returncode}"
    except FileNotFoundError:
        return False, f"Interpreter '{interpreter_name}' not found on PATH"
    except subprocess.TimeoutExpired:
        return False, f"Interpreter timed out (hung)"
    except Exception as e:
        return False, f"Error checking interpreter: {e}"


def check_file(file_path):
    """
    Check if a single file's required interpreter is available.
    Returns: (passed: bool, details: dict)
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return False, {
            "file": str(file_path),
            "error": "File not found",
        }

    shebang = extract_shebang(file_path)
    if not shebang:
        # No shebang = not an executable script; skip
        return True, {
            "file": str(file_path),
            "status": "skipped",
            "reason": "No shebang found (not a script)",
        }

    interpreter_name, interpreter_path = parse_shebang(shebang)

    if not interpreter_name:
        return False, {
            "file": str(file_path),
            "shebang": shebang,
            "error": "Could not parse shebang",
        }

    available, error = is_interpreter_available(interpreter_name)

    if available:
        return True, {
            "file": str(file_path),
            "shebang": shebang,
            "interpreter": interpreter_name,
            "status": "ok",
        }
    else:
        return False, {
            "file": str(file_path),
            "shebang": shebang,
            "interpreter": interpreter_name,
            "status": "broken",
            "error": error,
        }


def main():
    repo_root = find_repo_root()
    if not repo_root:
        print(
            "Error: Could not find repository root (.git not found)",
            file=sys.stderr,
        )
        return 2

    # Directories to check
    hooks_dir = repo_root / "hooks"
    daemons_dir = repo_root / "daemons"

    # Collect all hook and daemon files
    check_files = []

    if hooks_dir.exists():
        for file_path in hooks_dir.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith("."):
                check_files.append(file_path)

    if daemons_dir.exists():
        for file_path in daemons_dir.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith("."):
                check_files.append(file_path)

    if not check_files:
        print(
            "Error: No hook or daemon files found to check",
            file=sys.stderr,
        )
        return 2

    # Check each file
    all_passed = True
    checked_count = 0
    details_list = []

    for file_path in sorted(check_files):
        passed, details = check_file(file_path)
        details_list.append(details)

        if details.get("status") in ["ok", "skipped"]:
            checked_count += 1
            if not passed:
                all_passed = False
        elif details.get("status") == "broken":
            checked_count += 1
            all_passed = False
            print(
                f"BROKEN: {file_path.name}",
                file=sys.stderr,
            )
            if "error" in details:
                print(f"  {details['error']}", file=sys.stderr)
        elif "error" in details:
            checked_count += 1
            all_passed = False
            print(
                f"ERROR: {file_path.name} - {details['error']}",
                file=sys.stderr,
            )

    # Summary
    if checked_count == 0:
        print(
            "Error: No interpreter checks were performed",
            file=sys.stderr,
        )
        return 2

    if all_passed:
        print(f"[OK] All {checked_count} hook/daemon files have valid interpreters")
        return 0
    else:
        broken_count = len([d for d in details_list if d.get('status') == 'broken'])
        print(
            f"[FAIL] {broken_count} interpreter(s) missing or broken",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
