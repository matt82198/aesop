#!/usr/bin/env python3
"""
Shared subprocess wrapper for gh and git CLI commands.
INDEX: Unified subprocess wrapper (gh/git CLI commands with explicit timeouts ~30s/~60s, UTF-8 encoding, no shell=True, timeout vs non-zero-exit distinction); exports: `run()`, `gh()`, `git()`, `json_output()`; used by auto_merge, ci_merge_wait, defect_escape, incident_report; stdlib-only

Provides unified timeout, encoding, and error handling across all subprocess calls.
Never uses shell=True; always uses explicit argument lists.

Key features:
  - Explicit timeout on every call with sensible defaults (~30s for gh, ~60s for git)
  - Always UTF-8 encoding (avoids Windows cp1252 trap)
  - Per-call timeout override
  - Clear distinction between timeout and other failures
  - Safe JSON parsing helper for --json calls

Exit code semantics:
  - subprocess.CalledProcessError: command ran but returned non-zero
  - subprocess.TimeoutExpired: command exceeded timeout
  - FileNotFoundError: command not found
"""

import json
import subprocess
import sys
from typing import List, Optional, Dict, Any


# Default timeouts
GH_TIMEOUT = 30  # GitHub API calls are network-bound
GIT_TIMEOUT = 60  # Git operations may involve disk I/O and network


class SubprocessError(Exception):
    """Base exception for subprocess wrapper errors."""
    pass


class TimeoutError(SubprocessError):
    """Raised when a subprocess times out (wrapper for subprocess.TimeoutExpired)."""
    pass


class CommandError(SubprocessError):
    """Raised when a command returns non-zero exit code."""
    pass


def run(
    cmd: List[str],
    *,
    check: bool = False,
    capture: bool = True,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """
    Run a command safely with explicit timeout and encoding.

    NEVER uses shell=True; cmd must be a list of command + arguments.

    Args:
        cmd: List of command and arguments (e.g., ['git', 'log', '--oneline']).
             MUST NOT contain shell syntax; must be an explicit argument list.
        check: If True, raise CalledProcessError on non-zero exit.
        capture: If True, capture stdout/stderr; if False, inherit from parent.
        timeout: Timeout in seconds (int). If None, no timeout (not recommended).
                 On timeout, raises subprocess.TimeoutExpired.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.

    Raises:
        subprocess.TimeoutExpired: If timeout exceeded.
        subprocess.CalledProcessError: If check=True and returncode != 0.
        FileNotFoundError: If command not found on PATH.

    Example:
        result = run(['git', 'log', '--oneline'], timeout=60)
        if result.returncode == 0:
            print(result.stdout)
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=timeout,
            check=False,  # We handle errors ourselves
        )
    except subprocess.TimeoutExpired as e:
        # Re-raise TimeoutExpired with preserved output
        raise subprocess.TimeoutExpired(
            cmd=e.cmd,
            timeout=e.timeout,
            output=e.stdout,
            stderr=e.stderr,
        ) from e
    except FileNotFoundError as e:
        # Command not found
        raise FileNotFoundError(f"Command not found: {cmd[0]}") from e

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            result.stdout,
            result.stderr,
        )

    return result


def gh(
    args: List[str],
    *,
    timeout: Optional[int] = None,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """
    Run a gh (GitHub CLI) command with default timeout of ~30s.

    Args:
        args: List of gh arguments (e.g., ['pr', 'list', '--state', 'open']).
              Do NOT include 'gh' itself; it will be prepended.
        timeout: Override default timeout (default: 30s). Use None for no timeout (not recommended).
        check: If True, raise CalledProcessError on non-zero exit.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.

    Raises:
        subprocess.TimeoutExpired: If timeout exceeded.
        subprocess.CalledProcessError: If check=True and returncode != 0.
        FileNotFoundError: If gh not found on PATH.

    Example:
        result = gh(['pr', 'view', '42', '--json', 'state'])
        data = json_output(result)
    """
    if timeout is None:
        timeout = GH_TIMEOUT
    return run(['gh'] + args, check=check, timeout=timeout)


def git(
    args: List[str],
    *,
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """
    Run a git command with default timeout of ~60s.

    Args:
        args: List of git arguments (e.g., ['log', '--oneline', 'main']).
              Do NOT include 'git' itself; it will be prepended.
        cwd: Working directory for the command (optional).
        timeout: Override default timeout (default: 60s). Use None for no timeout (not recommended).
        check: If True, raise CalledProcessError on non-zero exit.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.

    Raises:
        subprocess.TimeoutExpired: If timeout exceeded.
        subprocess.CalledProcessError: If check=True and returncode != 0.
        FileNotFoundError: If git not found on PATH.

    Example:
        result = git(['log', '--oneline'], cwd='/path/to/repo')
        for line in result.stdout.strip().split('\\n'):
            print(line)
    """
    if timeout is None:
        timeout = GIT_TIMEOUT

    cmd = ['git'] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=timeout,
            cwd=cwd,
            check=False,  # We handle errors ourselves
        )
    except subprocess.TimeoutExpired as e:
        # Re-raise with preserved output
        raise subprocess.TimeoutExpired(
            cmd=e.cmd,
            timeout=e.timeout,
            output=e.stdout,
            stderr=e.stderr,
        ) from e
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Command not found: {cmd[0]}") from e

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            result.stdout,
            result.stderr,
        )

    return result


def json_output(result: subprocess.CompletedProcess) -> Any:
    """
    Parse JSON from subprocess stdout.

    Raises a clear error on malformed JSON rather than returning None or empty.

    Args:
        result: CompletedProcess from run(), gh(), or git().

    Returns:
        Parsed JSON object/array.

    Raises:
        ValueError: If stdout is empty or not valid JSON.
        json.JSONDecodeError: If JSON is malformed.

    Example:
        result = gh(['pr', 'list', '--json', 'number,title'])
        prs = json_output(result)
        for pr in prs:
            print(f"#{pr['number']}: {pr['title']}")
    """
    if not result.stdout or not result.stdout.strip():
        raise ValueError(
            f"Expected JSON output but got empty stdout. stderr: {result.stderr}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            msg=f"Malformed JSON in stdout: {e.msg}",
            doc=result.stdout,
            pos=e.pos,
        ) from e
