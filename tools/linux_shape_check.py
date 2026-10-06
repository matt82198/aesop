#!/usr/bin/env python3
"""
Linux shape checker — detect platform-specific failures via WSL before push.
INDEX: Linux shape check (cross-platform test runner, detects Windows-only red CI via WSL)

Runs shell, Node, and workflow tests under WSL to catch platform-specific failures
locally before pushing (e.g., isolated-home USERPROFILE assumption, shell test failures
on Ubuntu).

Trigger rules:
  - If commit range touches: *.sh, hooks/*, tools/run_shell_tests.sh, .github/workflows/*.yml
    run: steps, or tests/**/*.test.mjs
  - Then: run owning test suites under WSL with timeout
  - Shell: wsl bash -lc 'cd <wsl-path> && bash tools/run_shell_tests.sh' (or hooks/pre-push-policy.sh --test)
  - Node: wsl bash -lc '... node --import ./tests/helpers/isolated-env.mjs --test <files>' + USERPROFILE unset

Exit contract:
  - Range without shell/node changes → exit 0 (skipped)
  - With changes + WSL unavailable → print NOTICE + exit 0 (CI remains gate)
  - With changes + WSL unavailable + AESOP_REQUIRE_LINUX_SHAPE=1 → exit 1
  - With changes + WSL available + suite fails → exit 1 (with Linux output)
  - Mocked/test invocation: honesty on success/failure

CLI:
  python tools/linux_shape_check.py [--range <base>..HEAD] [--require-wsl]
    --range: commit range to check (default: origin/main..HEAD)
    --require-wsl: fail closed if WSL unavailable (AESOP_REQUIRE_LINUX_SHAPE override)

Usage in hook:
  python tools/linux_shape_check.py --range <commit-range>
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Optional


def get_repo_root() -> Path:
    """Resolve repo root via git rev-parse or AESOP_ROOT."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.decode('utf-8', errors='replace').strip())
    except Exception:
        pass
    # Fallback to AESOP_ROOT env var or current directory
    return Path(os.environ.get("AESOP_ROOT", "."))


def wsl_available() -> bool:
    """Check if wsl.exe is available and a distro is installed and executable.

    Requires BOTH:
      (a) wsl.exe -l -q returns ≥1 distro (UTF-16LE decoded, no BOM/NULs)
      (b) wsl.exe -e true exits 0 (verify WSL actually works)

    Note: wsl.exe -l -q returns UTF-16LE with BOM, not UTF-8.
    """
    # Check (a): wsl.exe -l -q lists ≥1 distro
    try:
        result = subprocess.run(
            ["wsl.exe", "-l", "-q"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        # wsl.exe -l -q returns UTF-16LE (with BOM), not UTF-8
        # Decode the byte output as UTF-16LE, stripping BOM and NULs
        output_text = result.stdout.decode('utf-16-le', errors='replace')
        distros = [d.strip() for d in output_text.strip().split("\n") if d.strip()]
        if len(distros) == 0:
            return False
    except Exception:
        return False

    # Check (b): wsl.exe -e true exits 0 (verify WSL is executable)
    try:
        result = subprocess.run(
            ["wsl.exe", "-e", "true"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def compute_wsl_path(windows_path: Path) -> str:
    """Convert Windows path to WSL path using wslpath -a."""
    try:
        result = subprocess.run(
            ["wsl.exe", "wslpath", "-a", str(windows_path)],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            # wslpath output is UTF-8 (unlike wsl.exe -l -q which is UTF-16LE)
            return result.stdout.decode('utf-8', errors='replace').strip()
    except Exception:
        pass
    # Fallback: rough /mnt/<drive>/... conversion. Parse the path TEXT directly
    # (never Path.resolve()/.parts) -- on a POSIX host, pathlib has no concept
    # of a Windows drive or backslash separator, so a WindowsPath-shaped string
    # like "C:\\Users\\matt8\\aesop" resolves against the POSIX cwd instead of
    # being recognized as already absolute, producing a mangled result. This
    # repo's only caller always passes an already-absolute path (get_repo_root
    # via `git rev-parse --show-toplevel`), so no resolve() is needed here.
    normalized = str(windows_path).replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2)
        return f"/mnt/{drive}/{rest}"
    # Already POSIX-shaped or unrecognized; return the normalized string as-is.
    return normalized


def get_changed_files(commit_range: str, repo_root: Path) -> List[str]:
    """Get list of changed files in commit range."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", commit_range],
            cwd=repo_root,
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout.decode('utf-8', errors='replace')
            return [f.strip() for f in output.strip().split("\n") if f.strip()]
    except Exception:
        pass
    return []


def should_check_linux_shape(changed_files: List[str]) -> bool:
    """Determine if any changes trigger Linux shape check."""
    patterns = [
        r"\.sh$",  # Any shell script
        r"^hooks/",  # hooks directory
        r"^tools/run_shell_tests\.sh$",  # Shell test runner
        r"^\.github/workflows/.*\.yml$",  # Workflow files
        r"^tests/.*\.test\.mjs$",  # Node tests
    ]
    for file_path in changed_files:
        for pattern in patterns:
            if re.search(pattern, file_path):
                return True
    return False


def run_shell_tests_wsl(repo_root: Path, wsl_path: str, timeout: int = 120) -> Tuple[int, str]:
    """Run shell tests under WSL."""
    cmd = f"cd '{wsl_path}' && bash tools/run_shell_tests.sh"
    try:
        result = subprocess.run(
            ["wsl.exe", "bash", "-lc", cmd],
            capture_output=True,
            timeout=timeout,
        )
        # Decode output with UTF-8 (WSL bash output is UTF-8, not UTF-16LE)
        stdout = result.stdout.decode('utf-8', errors='replace')
        stderr = result.stderr.decode('utf-8', errors='replace')
        output = stdout + stderr
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 124, f"Shell tests timed out after {timeout}s"
    except Exception as e:
        return 2, f"Error running shell tests: {e}"


def run_node_tests_wsl(repo_root: Path, wsl_path: str, test_files: List[str], timeout: int = 120) -> Tuple[int, str]:
    """Run Node tests under WSL with USERPROFILE unset."""
    files_str = " ".join(f"'{f}'" for f in test_files)
    cmd = (
        f"cd '{wsl_path}' && "
        f"unset USERPROFILE && "
        f"node --import ./tests/helpers/isolated-env.mjs --test {files_str}"
    )
    try:
        result = subprocess.run(
            ["wsl.exe", "bash", "-lc", cmd],
            capture_output=True,
            timeout=timeout,
        )
        # Decode output with UTF-8 (WSL bash output is UTF-8, not UTF-16LE)
        stdout = result.stdout.decode('utf-8', errors='replace')
        stderr = result.stderr.decode('utf-8', errors='replace')
        output = stdout + stderr
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 124, f"Node tests timed out after {timeout}s"
    except Exception as e:
        return 2, f"Error running Node tests: {e}"


def main():
    parser = argparse.ArgumentParser(
        description="Check shell/Node tests on Linux shape (via WSL) before push"
    )
    parser.add_argument(
        "--range",
        default="origin/main..HEAD",
        help="Commit range to check (default: origin/main..HEAD)",
    )
    parser.add_argument(
        "--require-wsl",
        action="store_true",
        help="Fail closed if WSL unavailable (AESOP_REQUIRE_LINUX_SHAPE override)",
    )
    args = parser.parse_args()

    repo_root = get_repo_root()
    changed_files = get_changed_files(args.range, repo_root)

    # Check if any files trigger Linux shape check
    if not should_check_linux_shape(changed_files):
        print("SKIP: No shell/workflow/Node-test changes detected")
        return 0

    print(f"TRIGGER: {len(changed_files)} changed file(s) include shell/workflow/Node changes")

    # Check WSL availability
    is_wsl_available = wsl_available()
    require_wsl = args.require_wsl or os.environ.get("AESOP_REQUIRE_LINUX_SHAPE") == "1"

    if not is_wsl_available:
        if require_wsl:
            print("ERROR: WSL required (AESOP_REQUIRE_LINUX_SHAPE=1) but unavailable")
            print("  WSL present but no distro installed? Run: wsl --install -d Ubuntu")
            print("  WSL not present? Run: wsl --install")
            return 1
        else:
            print("NOTICE: WSL unavailable; Linux shape check skipped (CI gate remains)")
            print("  WSL present but no distro installed? Run: wsl --install -d Ubuntu")
            print("  WSL not present? Run: wsl --install")
            print("  To enforce local Linux testing: export AESOP_REQUIRE_LINUX_SHAPE=1")
            return 0

    # Convert repo path to WSL path
    wsl_repo_path = compute_wsl_path(repo_root)
    print(f"Running tests under WSL: {wsl_repo_path}")

    # Determine which suites to run
    has_shell_changes = any(
        re.search(r"\.sh$|^hooks/|^tools/run_shell_tests\.sh$|^\.github/workflows/.*\.yml$", f)
        for f in changed_files
    )
    has_node_changes = any(re.search(r"^tests/.*\.test\.mjs$", f) for f in changed_files)

    exit_code = 0

    # Run shell tests if relevant
    if has_shell_changes:
        print("\nRunning shell tests...")
        rc, output = run_shell_tests_wsl(repo_root, wsl_repo_path)
        if rc != 0:
            print(f"FAIL: Shell tests exited {rc}")
            print("--- WSL Output ---")
            print(output)
            print("--- End Output ---")
            exit_code = 1
        else:
            print("PASS: Shell tests")

    # Run Node tests if relevant
    if has_node_changes:
        # Find changed test files
        test_files = [f for f in changed_files if re.search(r"^tests/.*\.test\.mjs$", f)]
        if test_files:
            print(f"\nRunning Node tests ({len(test_files)} file(s))...")
            rc, output = run_node_tests_wsl(repo_root, wsl_repo_path, test_files)
            if rc != 0:
                print(f"FAIL: Node tests exited {rc}")
                print("--- WSL Output ---")
                print(output)
                print("--- End Output ---")
                exit_code = 1
            else:
                print("PASS: Node tests")

    if exit_code == 0:
        print("\nLinux shape check: PASS")
    else:
        print("\nLinux shape check: FAIL (see output above)")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
