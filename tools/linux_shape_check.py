#!/usr/bin/env python3
"""
Linux shape checker -- detect platform-specific failures via WSL before push.
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
  - Range without shell/node changes -> exit 0 (skipped)
  - Non-Windows host -> run_native_leg runs the suites natively; WSL is never probed
  - Windows, WSL probe error (wsl_status WSL_ERROR) -> exit 1 (fail closed)
  - With changes + WSL unavailable -> print NOTICE + exit 0 (CI remains gate)
  - With changes + WSL unavailable + AESOP_REQUIRE_LINUX_SHAPE=1 -> exit 1
  - With changes + WSL available + suite fails -> exit 1 (with Linux output)
  - Mocked/test invocation: honesty on success/failure

CLI:
  python tools/linux_shape_check.py [--range <base>..HEAD] [--require-wsl]
    --range: commit range to check (default: origin/main..HEAD)
    --require-wsl: fail closed if WSL unavailable (AESOP_REQUIRE_LINUX_SHAPE override)

Usage in hook:
  python tools/linux_shape_check.py --range <commit-range>
"""

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


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


WSL_OK = "ok"                  # a distro is listed and `wsl -e true` exits 0
WSL_ABSENT = "absent"          # wsl.exe is not installed on this machine
WSL_NO_DISTRO = "no-distro"    # wsl.exe runs but lists no distribution (expected on a box without one)
WSL_ERROR = "error"            # WSL claims to be there but the probe itself failed: fail closed


def _looks_like_distro_name(line: str) -> bool:
    # Distro names never contain whitespace; the "no installed distributions" message does.
    return bool(line) and not any(ch.isspace() for ch in line)


def wsl_status() -> str:
    """Tri-state WSL probe: returns WSL_OK, WSL_ABSENT, WSL_NO_DISTRO or WSL_ERROR.

    (a) wsl.exe -l -q: missing binary -> absent; non-zero exit or any exception -> error;
        no distro name in the UTF-16LE output -> no-distro.
    (b) wsl.exe -e true: a listed distro that cannot run it -> error; exit 0 -> ok.
    """
    try:
        result = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=5)
    except FileNotFoundError:
        return WSL_ABSENT
    except Exception:
        return WSL_ERROR
    if result.returncode != 0:
        return WSL_ERROR
    # wsl.exe -l -q returns UTF-16LE (with BOM), not UTF-8: strip BOM and NULs
    output_text = result.stdout.decode('utf-16-le', errors='replace').replace("\ufeff", "").replace("\x00", "")
    distros = [d.strip() for d in output_text.splitlines() if _looks_like_distro_name(d.strip())]
    if not distros:
        return WSL_NO_DISTRO
    try:
        result = subprocess.run(["wsl.exe", "-e", "true"], capture_output=True, timeout=5)
    except Exception:
        return WSL_ERROR
    return WSL_OK if result.returncode == 0 else WSL_ERROR


def wsl_available() -> bool:
    """True only when wsl_status() is WSL_OK (a distro is listed and executable)."""
    return wsl_status() == WSL_OK


def host_is_windows() -> bool:
    """True on the Windows host, where the Linux shape is reached through WSL.

    On any other host (the ubuntu CI runner) the shape IS the host, so the leg
    runs natively and never probes for WSL.
    """
    return sys.platform == "win32"


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


TRIPWIRE_SCRIPT = "tools/test_isolation_tripwire.py"


def node_suite_argv(test_files: List[str]) -> List[str]:
    """The Node suite argv, identical to the WSL leg and package.json test:node (before the wrap)."""
    return ["node", "--import", "./tests/helpers/isolated-env.mjs", "--test", *test_files]


def shell_suite_argv() -> List[str]:
    """The shell suite argv, identical to package.json test:sh (before the wrap)."""
    return ["bash", "tools/run_shell_tests.sh"]


def tripwired_argv(suite_argv: List[str], interpreter: str) -> List[str]:
    """Wrap a suite in the test-isolation tripwire. Fails closed on any real-profile drift.

    The ONE command builder for both legs. Native and WSL differ only in the host prefix
    (direct exec vs `wsl.exe bash -lc`) and the interpreter token (sys.executable on the
    host, `python` inside WSL). Nothing else may differ.
    """
    return [interpreter, TRIPWIRE_SCRIPT, "--", *suite_argv]


def run_shell_tests_native(repo_root: Path, timeout: int = 120) -> Tuple[int, str]:
    """Run the shell test suites on the host (Linux shape without WSL), tripwire-wrapped."""
    try:
        result = subprocess.run(
            tripwired_argv(shell_suite_argv(), sys.executable),
            cwd=str(repo_root),
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode, _decode(result.stdout) + _decode(result.stderr)
    except subprocess.TimeoutExpired:
        return 124, f"Shell tests timed out after {timeout}s"
    except Exception as e:
        return 2, f"Error running shell tests: {e}"


def run_node_tests_native(repo_root: Path, test_files: List[str], timeout: int = 120) -> Tuple[int, str]:
    """Run Node test files on the host with USERPROFILE unset, tripwire-wrapped."""
    env = {k: v for k, v in os.environ.items() if k != "USERPROFILE"}
    try:
        result = subprocess.run(
            tripwired_argv(node_suite_argv(test_files), sys.executable),
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode, _decode(result.stdout) + _decode(result.stderr)
    except subprocess.TimeoutExpired:
        return 124, f"Node tests timed out after {timeout}s"
    except Exception as e:
        return 2, f"Error running Node tests: {e}"


def run_native_leg(repo_root: Path, changed_files: List[str]) -> int:
    """Linux host: run the owning suites natively. Failure of a suite is exit 1."""
    print("Running tests natively (Linux host; no WSL needed)")
    exit_code = 0
    if any(re.search(r"\.sh$|^hooks/|^tools/run_shell_tests\.sh$|^\.github/workflows/.*\.yml$", f)
           for f in changed_files):
        print("\nRunning shell tests...")
        rc, output = run_shell_tests_native(repo_root)
        if rc != 0:
            print(f"FAIL: Shell tests exited {rc}")
            print(output)
            exit_code = 1
        else:
            print("PASS: Shell tests")
    test_files = [f for f in changed_files if re.search(r"^tests/.*\.test\.mjs$", f)]
    if test_files:
        print(f"\nRunning Node tests ({len(test_files)} file(s))...")
        rc, output = run_node_tests_native(repo_root, test_files)
        if rc != 0:
            print(f"FAIL: Node tests exited {rc}")
            print(output)
            exit_code = 1
        else:
            print("PASS: Node tests")
    print("\nLinux shape check: PASS" if exit_code == 0 else "\nLinux shape check: FAIL (see output above)")
    return exit_code


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
    # like "C:\\Users\\example\\repo" resolves against the POSIX cwd instead of
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
    """Run shell tests under WSL, tripwire-wrapped (same builder as the native leg)."""
    cmd = f"cd '{wsl_path}' && " + shlex.join(tripwired_argv(shell_suite_argv(), "python"))
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
    """Run Node tests under WSL with USERPROFILE unset, tripwire-wrapped (same builder as native)."""
    cmd = (
        f"cd '{wsl_path}' && "
        f"unset USERPROFILE && "
        + shlex.join(tripwired_argv(node_suite_argv(test_files), "python"))
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

    if not host_is_windows():
        # Ubuntu/Linux host: the Linux shape is the host itself. Never probe for WSL here.
        return run_native_leg(repo_root, changed_files)

    require_wsl = args.require_wsl or os.environ.get("AESOP_REQUIRE_LINUX_SHAPE") == "1"
    status = wsl_status()
    if status == WSL_ERROR:
        # The probe itself failed: we cannot tell whether the Linux shape was tested. Fail closed.
        print("ERROR: WSL probe failed; cannot verify the Linux shape (fail closed)")
        return 1

    if status != WSL_OK:
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
