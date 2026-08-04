#!/usr/bin/env python3
"""
launch_tui.py — Open a bash TUI script in its own terminal window.
INDEX: Spawn bash TUI script in detached terminal

Usage:
  python launch_tui.py --script <path-to-bash-script> [--title <window-title>] [--pidfile <path>]

Behavior:
  - Finds a terminal (prefer Git Bash, else Windows Terminal wt.exe)
  - Opens a NEW visible window running `bash <script>`
  - Idempotent via pidfile: if process already running, prints "already running (pid N)"
  - Always outputs exactly one line: spawned pid, already-running, or error
  - Direct git-bash spawn avoids cmd /c start quoting bugs
"""

import argparse
import os
import sys
import subprocess
import shutil
import time
from pathlib import Path


def resolve_git_bash_path():
    r"""
    Resolve git-bash.exe path portably in priority order:
    1. env var AESOP_GIT_BASH (if set and exists)
    2. shutil.which('git-bash') (on PATH)
    3. Derived from shutil.which('git') -> ../git-bash.exe on Windows
    4. Hardcoded default: C:\Program Files\Git\git-bash.exe

    Raises FileNotFoundError with clear message if none found.
    """
    attempted = []

    # 1. Check env var
    env_path = os.environ.get("AESOP_GIT_BASH")
    if env_path:
        attempted.append(f"AESOP_GIT_BASH={env_path}")
        if os.path.exists(env_path):
            return env_path

    # 2. Check which('git-bash')
    which_bash = shutil.which("git-bash")
    if which_bash:
        attempted.append(f"which('git-bash')={which_bash}")
        if os.path.exists(which_bash):
            return which_bash

    # 3. Derive from which('git')
    which_git = shutil.which("git")
    if which_git:
        # git is typically at C:\Program Files\Git\cmd\git.exe
        # We want C:\Program Files\Git\git-bash.exe (two levels up, then git-bash.exe)
        git_path = Path(which_git)
        derived_bash = git_path.parent.parent / "git-bash.exe"
        attempted.append(f"derived from git={derived_bash}")
        if derived_bash.exists():
            return str(derived_bash)

    # 4. Hardcoded default
    default_path = r"C:\Program Files\Git\git-bash.exe"
    attempted.append(f"default={default_path}")
    if os.path.exists(default_path):
        return default_path

    # None found
    raise FileNotFoundError(
        "git-bash.exe not found. Tried (in order): " + ", ".join(attempted)
    )


def find_terminal():
    """
    Locate a terminal executable.
    Prefer Git Bash (process persists → pidfile valid), fallback to Windows Terminal.
    Returns (terminal_path, command_builder_fn, is_wt_bool).
    command_builder_fn(script_path, title) returns command list to spawn.
    is_wt_bool: True if using wt.exe (needs bash process tracking); False if git-bash (stable pid).
    """
    git_bash = resolve_git_bash_path()
    wt_exe = shutil.which("wt.exe")
    bash_exe = r"C:\Program Files\Git\bin\bash.exe"

    # Prefer Git Bash (its process persists, so pidfile/idempotency works)
    if os.path.exists(git_bash):
        def git_bash_cmd(script_path, title):
            # Spawn git-bash.exe -c "bash '/path/to/script.sh'"
            # Convert Windows path to POSIX: C:\Users\...\... -> /c/Users/...\...
            script_abs = os.path.abspath(script_path)
            script_posix = "/" + script_abs[0].lower() + script_abs[2:].replace("\\", "/")
            return [git_bash, "-c", f"bash '{script_posix}'"]
        return git_bash, git_bash_cmd, False

    # Fallback to Windows Terminal (spawns and exits, so we track bash process instead)
    if wt_exe and os.path.exists(bash_exe):
        def wt_cmd(script_path, title):
            cmd = [wt_exe]
            if title:
                cmd.extend(["-w", title])
            # Start a new tab/window running bash with the script
            cmd.extend(["-d", str(Path(script_path).parent), bash_exe, script_path])
            return cmd
        return wt_exe, wt_cmd, True

    raise FileNotFoundError(
        "Terminal not found. Tried: Git Bash, Windows Terminal (wt.exe) with bash at {bash_exe}"
    )


def find_bash_process_by_script(script_path):
    r"""
    Find bash.exe process running the given script using PowerShell.
    Searches for the script path in the bash process CommandLine.
    Returns PID if found, None otherwise.
    """
    try:
        script_abs = os.path.abspath(script_path)
        # Bash processes may show the path in either Windows or POSIX format
        # Try both: C:\Users\... and /c/Users/...
        search_patterns = [
            script_abs,  # Windows format: C:\Users\...
            "/" + script_abs[0].lower() + script_abs[2:].replace("\\", "/"),  # POSIX: /c/Users/...
        ]

        # Use PowerShell to find bash process matching either path format
        ps_cmd = (
            f"$patterns = @('{script_abs}', '/{script_abs[0].lower()}{script_abs[2:].replace(chr(92), '/')}'); "
            f"Get-CimInstance Win32_Process -Filter \"name='bash.exe'\" | "
            f"Where-Object {{ $cmd = $_.CommandLine; $patterns | Where-Object {{ $cmd -like \"*$_*\" }} }} | "
            f"Select-Object -First 1 -ExpandProperty ProcessId"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pid_str = result.stdout.strip()
            if pid_str.isdigit():
                return int(pid_str)
        return None
    except Exception:
        return None


def check_bash_running(script_path):
    """
    Check if bash is currently running the script by looking at running processes.
    Returns True if any bash/git-bash process is active.
    """
    try:
        result = subprocess.run(
            ["tasklist"],
            capture_output=True,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
        # If bash or git-bash is running, assume script might be active
        return "bash.exe" in result.stdout or "git-bash.exe" in result.stdout
    except Exception:
        return False


def is_pidfile_valid_and_recent(pidfile_path, script_path):
    """
    Check if pidfile exists, is recent (created in last 60 seconds),
    and bash processes are running. This is an idempotency heuristic.
    """
    try:
        if not pidfile_path.exists():
            return False

        mtime = os.path.getmtime(pidfile_path)
        age = time.time() - mtime
        # If pidfile is less than 60 seconds old and bash is running, assume still active
        if age < 60 and check_bash_running(script_path):
            with open(pidfile_path, "r", encoding="utf-8") as f:
                old_pid = f.read().strip()
            return int(old_pid)

        return False
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Open a bash TUI script in its own terminal window.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--script",
        required=True,
        help="Path to bash script to run",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Window title (optional)",
    )
    parser.add_argument(
        "--pidfile",
        default=None,
        help="Path to pidfile for idempotency (optional)",
    )

    args = parser.parse_args()

    script_path = args.script
    title = args.title
    pidfile = args.pidfile

    # Validate script exists
    if not os.path.exists(script_path):
        print(f"ERROR: Script not found: {script_path}")
        sys.exit(1)

    # Check pidfile for idempotency
    if pidfile:
        pidfile_path = Path(pidfile)
        old_pid = is_pidfile_valid_and_recent(pidfile_path, script_path)
        if old_pid:
            print(f"already running (pid {old_pid})")
            sys.exit(0)

    # Find terminal
    try:
        terminal_path, cmd_builder, is_wt = find_terminal()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Build and spawn command
    cmd = cmd_builder(script_path, title)

    try:
        # Windows process creation flags
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200

        # For git-bash: spawn detached so process persists after parent exits
        # For wt.exe: spawn normally (it exits immediately anyway)
        if is_wt:
            creationflags = 0
        else:
            creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

        # Spawn the process without waiting
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        # For git-bash, its pid persists and we can use it directly
        if not is_wt:
            new_pid = proc.pid
        else:
            # For wt.exe, find the actual bash process running the script
            # Poll for bash process running the script (up to 2 seconds)
            new_pid = None
            for _ in range(20):  # 20 attempts * 0.1s = 2s max wait
                new_pid = find_bash_process_by_script(script_path)
                if new_pid:
                    break
                time.sleep(0.1)

            if not new_pid:
                print(f"ERROR: Failed to locate bash process for {script_path}")
                sys.exit(1)

        # Write pidfile if specified
        if pidfile:
            pidfile_path = Path(pidfile)
            pidfile_path.parent.mkdir(parents=True, exist_ok=True)
            with open(pidfile_path, "w", encoding="utf-8") as f:
                f.write(str(new_pid))

        print(f"spawned (pid {new_pid})")
        sys.exit(0)

    except Exception as e:
        print(f"ERROR: Failed to spawn terminal: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
