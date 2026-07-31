#!/usr/bin/env python3
"""
CLI Base — Unified command-line interface factory for tools.

Consolidates common boilerplate across 89 tools:
  - argparse setup (--check, --json, --root, --repo, --help)
  - subprocess execution with timeout + fail-closed error handling
  - repo/state directory discovery (args + env vars + cwd fallback)
  - output formatting (text vs JSON, with secret masking)
  - deterministic exit codes (0=success, 1=findings, 2=error)

Design: Factory pattern (CLIBuilder) for maximal compatibility.
Constraints: stdlib-only, Windows + Linux clean, fail-closed.

Classes:
  CLIBuilder — Fluent factory for common argparse patterns
  OutputFormatter — Consistent text/JSON output with secret masking
  SubprocessError — Raised on subprocess failure (timeout, non-zero exit)

Functions:
  run_subprocess(cmd, timeout=30, cwd=None) -> (rc, stdout, stderr)
    Execute subprocess with fail-closed error handling.

  resolve_repo_root(args, env_key='AESOP_STATE_ROOT') -> Path
    Resolve repo root from args.root / args.repo / env var / cwd.

  mask_secrets(text) -> str
    Replace known secret patterns with MASKED-<TYPE>.

  deterministic_json_dumps(obj, pretty=True) -> str
    JSON output with sorted keys for hermetic/reproducible runs.

  exit_code(findings=None, error=None) -> int
    Return exit code: 0 on success, 1 on findings, 2 on error.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any


class SubprocessError(Exception):
    """Raised when subprocess fails (timeout, non-zero exit, etc.)."""
    pass


def run_subprocess(
    cmd: List[str],
    timeout: int = 30,
    cwd: Optional[Path] = None,
) -> Tuple[int, str, str]:
    """
    Execute a subprocess with explicit timeout and Windows/Linux compatibility.

    Args:
        cmd: Command as list (no shell=True; safe across platforms)
        timeout: Timeout in seconds (default 30; fail-closed on timeout)
        cwd: Working directory (default None = inherit from parent)

    Returns:
        Tuple of (returncode, stdout, stderr) as strings

    Raises:
        SubprocessError: On timeout, file not found, or other OS errors
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=timeout,
            cwd=cwd,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        raise SubprocessError(f"Subprocess timeout after {timeout}s: {' '.join(cmd[:2])}") from exc
    except FileNotFoundError as exc:
        raise SubprocessError(f"Command not found: {cmd[0]}") from exc
    except OSError as exc:
        raise SubprocessError(f"Subprocess error: {exc}") from exc


def resolve_repo_root(
    args: Optional[argparse.Namespace] = None,
    env_key: str = "AESOP_STATE_ROOT",
) -> Path:
    """
    Resolve repository/state root from multiple sources (fail-open order).

    Priority:
      1. args.root or args.repo (explicit CLI arg)
      2. Environment variable (env_key, default AESOP_STATE_ROOT)
      3. Current working directory (fallback)

    Args:
        args: argparse.Namespace with optional .root or .repo attribute
        env_key: Environment variable name for state root (default AESOP_STATE_ROOT)

    Returns:
        Resolved Path (always absolute, normalized)
    """
    # Try explicit CLI argument
    if args:
        root_arg = getattr(args, "root", None) or getattr(args, "repo", None)
        if root_arg:
            return Path(root_arg).resolve()

    # Try environment variable
    env_root = os.environ.get(env_key)
    if env_root:
        return Path(env_root).resolve()

    # Fallback to cwd
    return Path.cwd()


def mask_secrets(text: str) -> str:
    """
    Replace known secret patterns with MASKED-<TYPE>.

    Never prints raw credentials; only pattern name + masked marker.
    Protects: PEM keys, AWS tokens, GitHub tokens, Slack tokens, OpenAI/Anthropic keys.

    Args:
        text: Input text to mask

    Returns:
        Text with secret patterns replaced by MASKED-<TYPE>
    """
    patterns = {
        r"-----BEGIN .*PRIVATE KEY-----[\s\S]*?-----END .*PRIVATE KEY-----": "MASKED-PEM-KEY",
        r"AKIA[0-9A-Z]{16}": "MASKED-AWS-KEY",
        r"(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{20,}": "MASKED-GITHUB-TOKEN",
        r"xox[baprs]-[A-Za-z0-9-]{10,}": "MASKED-SLACK-TOKEN",
        r"sk-[A-Za-z0-9_\-]{20,}": "MASKED-API-KEY",
    }
    result = text
    for pattern, replacement in patterns.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE | re.DOTALL)
    return result


def deterministic_json_dumps(obj: Any, pretty: bool = True) -> str:
    """
    JSON output with sorted keys for hermetic/reproducible output.

    Ensures identical output regardless of dict insertion order.
    Useful for tests, diffs, and auditable machine-readable output.

    Args:
        obj: Object to serialize
        pretty: If True, indent=2 for readability; else compact

    Returns:
        JSON string
    """
    return json.dumps(
        obj,
        indent=2 if pretty else None,
        sort_keys=True,
        ensure_ascii=True,
    )


def exit_code(findings: Optional[int] = None, error: Optional[Exception] = None) -> int:
    """
    Return deterministic exit code.

    Convention:
      - 0 = success/clean (no findings, no error)
      - 1 = findings/violations detected (gate mode)
      - 2 = error (file read failure, subprocess failure, etc.) — fail-closed

    Args:
        findings: Number of findings (0 → exit 0, >0 → exit 1)
        error: Exception that occurred (if any, exit 2)

    Returns:
        Exit code (0, 1, or 2)

    Usage:
        try:
            findings = run_checks()
            sys.exit(exit_code(findings=findings))
        except Exception as e:
            sys.exit(exit_code(error=e))
    """
    if error is not None:
        return 2
    if findings is not None:
        return 1 if findings > 0 else 0
    return 0


class OutputFormatter:
    """
    Consistent output formatting (text vs JSON) with secret masking.

    Usage:
        fmt = OutputFormatter(json_mode=args.json)
        fmt.text("Processing file X", level="INFO")
        fmt.json({"result": data})
    """

    def __init__(self, json_mode: bool = False):
        """
        Initialize formatter.

        Args:
            json_mode: If True, output JSON; else text with prefixes
        """
        self.json_mode = json_mode
        self._json_buffer: List[Dict[str, Any]] = []

    def text(
        self,
        msg: str,
        level: str = "INFO",
    ) -> None:
        """
        Print a text message (no-op if json_mode=True).

        Args:
            msg: Message text (may contain secrets; will be masked)
            level: Log level (INFO, WARN, ERROR) — prefixes output
        """
        if self.json_mode:
            return
        masked_msg = mask_secrets(msg)
        if level == "ERROR":
            print(f"ERROR: {masked_msg}", file=sys.stderr)
        elif level == "WARN":
            print(f"WARN: {masked_msg}", file=sys.stderr)
        else:
            print(masked_msg)

    def json(
        self,
        data: Dict[str, Any],
        pretty: bool = True,
    ) -> None:
        """
        Output JSON (or buffer for deferred output in text mode).

        Args:
            data: Dict to serialize
            pretty: If True, indent for readability
        """
        if self.json_mode:
            output = deterministic_json_dumps(data, pretty=pretty)
            print(output)
        else:
            # In text mode, may buffer or ignore
            self._json_buffer.append(data)

    def finalize_json(self, items_key: str = "results") -> None:
        """
        Output all buffered JSON items as a single JSON array.

        Call once at end of text-mode run to emit JSON summary.

        Args:
            items_key: Top-level key for buffered items (default "results")
        """
        if not self.json_mode and self._json_buffer:
            output = deterministic_json_dumps({items_key: self._json_buffer}, pretty=True)
            print(output)


class CLIBuilder:
    """
    Fluent factory for common argparse patterns.

    Reduces boilerplate by auto-wiring common flags:
      - --check (gate/validation mode)
      - --json (machine-readable output)
      - --root/--repo (repository/state discovery)

    Usage:
        parser = (CLIBuilder("My tool")
                  .add_check_mode()
                  .add_json_mode()
                  .add_repo_root()
                  .build())
        args = parser.parse_args()
    """

    def __init__(self, description: str):
        """Initialize with tool description."""
        self.parser = argparse.ArgumentParser(description=description)
        self._has_repo_root = False

    def add_check_mode(self, default: bool = True) -> "CLIBuilder":
        """
        Add --check flag (gate/validation mode).

        Exit 1 on violations/findings (typical for pre-push gates).

        Args:
            default: If True, --check is default mode (use explicit action)

        Returns:
            self (for method chaining)
        """
        self.parser.add_argument(
            "--check",
            action="store_true",
            default=default,
            help="Check mode: exit 1 on violations (default)",
        )
        return self

    def add_json_mode(self) -> "CLIBuilder":
        """
        Add --json flag (machine-readable output).

        When set, output deterministic JSON instead of text.

        Returns:
            self (for method chaining)
        """
        self.parser.add_argument(
            "--json",
            action="store_true",
            help="Output as JSON (deterministic, sorted keys)",
        )
        return self

    def add_repo_root(self, env_key: str = "AESOP_STATE_ROOT") -> "CLIBuilder":
        """
        Add --root or --repo flag (repository/state discovery).

        Prioritizes: CLI arg > env var (AESOP_STATE_ROOT) > cwd.

        Args:
            env_key: Environment variable name (default AESOP_STATE_ROOT)

        Returns:
            self (for method chaining)
        """
        group = self.parser.add_mutually_exclusive_group()
        group.add_argument(
            "--root",
            type=Path,
            default=None,
            help=f"Repository root (default: {env_key} env var or cwd)",
        )
        group.add_argument(
            "--repo",
            type=Path,
            default=None,
            help=f"Repository root (alias for --root)",
        )
        self._has_repo_root = True
        return self

    def add_argument(self, *args, **kwargs) -> "CLIBuilder":
        """
        Add custom argument (pass-through to argparse).

        Allows extending with tool-specific flags.

        Returns:
            self (for method chaining)
        """
        self.parser.add_argument(*args, **kwargs)
        return self

    def build(self) -> argparse.ArgumentParser:
        """
        Return the configured ArgumentParser.

        Safe to call multiple times (returns same parser instance).

        Returns:
            argparse.ArgumentParser (ready for parse_args())
        """
        return self.parser


# Utility: Common CLI pattern template (not required, but shows best-practice structure)
def standard_main_template(
    args: argparse.Namespace,
    run_fn,
) -> int:
    """
    Standard main() template for tools using CLIBuilder.

    Handles common exception handling + exit code wrapping.

    Args:
        args: Parsed CLI arguments (from parser.parse_args())
        run_fn: Callable(args) -> int (exit code)

    Returns:
        Exit code (0, 1, or 2)

    Usage:
        def main():
            parser = CLIBuilder("My tool").add_check_mode().add_json_mode().build()
            args = parser.parse_args()
            return standard_main_template(args, run)

        def run(args: argparse.Namespace) -> int:
            findings = do_work()
            return exit_code(findings=findings)

        if __name__ == "__main__":
            sys.exit(main())
    """
    try:
        return run_fn(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
