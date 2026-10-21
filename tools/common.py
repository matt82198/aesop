#!/usr/bin/env python3
"""
Common utilities shared across tools.
INDEX: Shared utilities (state directory resolution, heartbeat staleness, CLI layer delegation to cli.py)

Functions (state layer):
  get_state_dir() -> Path
    Resolve state directory from AESOP_STATE_ROOT env var or default to ./state

  get_state_db_path() -> Path
    Return the canonical SQLite DB path for the event store.

  check_heartbeat_staleness(hb_file, threshold_s) -> (is_stale, age_s, info)
    Check if a heartbeat file is stale and return staleness, age, and descriptive info

Functions (CLI layer — delegate to tools/cli.py):
  run_subprocess(cmd, timeout=30, cwd=None) -> (rc, stdout, stderr)
    Execute subprocess with fail-closed error handling.

  resolve_repo_root(args=None, env_key='AESOP_STATE_ROOT') -> Path
    Resolve repo root from args.root / args.repo / env var / cwd.

  mask_secrets(text) -> str
    Replace known secret patterns with MASKED-<TYPE>.

  deterministic_json_dumps(obj, pretty=True) -> str
    JSON output with sorted keys for hermetic/reproducible output.

  exit_code(findings=None, error=None) -> int
    Return deterministic exit code (0/1/2).

Constants:
  STATE_DB_FILENAME: The canonical filename for the event-sourced state DB.
    Multi-instance coordination requires all instances to point to the same file.
"""

import json
import os
import subprocess
import time
from pathlib import Path

# Canonical filename for the event-sourced state database.
# Multi-instance requires all instances (including reconcile.py, ui/collectors.py, etc.)
# to point at the SAME shared file. Previously inconsistent (tracker_events.db vs events.db).
STATE_DB_FILENAME = "tracker_events.db"


def _config_state_root():
    """Return the state_root from aesop.config.json, or None.

    The config file lives in AESOP_ROOT (env) or the cwd, the same place the
    ui/config.py loader reads it. A relative state_root resolves against that
    config file's directory, not the process cwd (ui/config.py parity).
    """
    base = Path(os.environ["AESOP_ROOT"]) if os.environ.get("AESOP_ROOT") else Path.cwd()
    try:
        with open(base / "aesop.config.json", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    value = data.get("state_root") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def get_state_dir():
    """Resolve state directory: env > aesop.config.json state_root > cwd default.

    Precedence matches ui/config.py (env AESOP_STATE_ROOT > config state_root), so
    the dashboard and the tools read the same directory when a config is present.

    Returns:
        Path: Directory path for state files. Either from AESOP_STATE_ROOT env var,
              the config state_root, or defaults to ./state relative to cwd.
    """
    if os.environ.get("AESOP_STATE_ROOT"):
        return Path(os.environ["AESOP_STATE_ROOT"])
    configured = _config_state_root()
    if configured is not None:
        return configured
    # Default to ./state (relative to cwd)
    return Path.cwd() / "state"


def get_state_db_path():
    """Return the canonical SQLite DB path for the event store.

    Multi-instance coordination requires all orchestrators to point at the
    SAME shared database file. This function centralizes the DB path resolution.

    Returns:
        Path: The canonical path to the state database (state/tracker_events.db).
    """
    return get_state_dir() / STATE_DB_FILENAME


class ConductorRootUnresolved(RuntimeError):
    """CONDUCTOR_ROOT cannot be resolved by the conductor-root rule. Callers fail closed."""


def try_resolve_conductor_root(aesop_root=None):
    """Conductor fleet-state root by the daemons/conductor-root.sh rule, or None.

    1. An explicit non-empty CONDUCTOR_ROOT always wins.
    2. Otherwise the sibling fleet root is derived ONLY when the basename of the
       AESOP_ROOT is exactly "aesop" (the canonical tree).
    3. Any other shape is unresolved: None. Never a cwd guess, a child-folder guess,
       or a home fleet root (the 2026-10-06/07 heartbeat-pollution class).

    Args:
        aesop_root: AESOP_ROOT to use; defaults to the AESOP_ROOT environment variable.
    Returns:
        Path (not resolved) or None.
    """
    explicit = os.environ.get("CONDUCTOR_ROOT")
    if explicit:
        return Path(explicit)
    if aesop_root is None:
        aesop_root = os.environ.get("AESOP_ROOT", "")
    if aesop_root and Path(aesop_root).name == "aesop":
        return Path(aesop_root).parent / "conductor3"  # default sibling (canonical tree only)
    return None


def get_conductor_root():
    """Resolve the conductor fleet-state root directory (absolute, normalized).

    Follows try_resolve_conductor_root(). Raises ConductorRootUnresolved when the
    rule yields nothing, so no caller ever writes to a guessed fleet root.
    """
    root = try_resolve_conductor_root()
    if root is None:
        raise ConductorRootUnresolved(
            "CONDUCTOR_ROOT is unset and AESOP_ROOT is not the canonical 'aesop' tree; "
            "set CONDUCTOR_ROOT to the fleet-state root (refusing to guess)"
        )
    return root.resolve()


def check_heartbeat_staleness(hb_file, threshold_s):
    """Check if a heartbeat file is stale.

    Args:
        hb_file: Path to heartbeat file (contains epoch timestamp as first line)
        threshold_s: Staleness threshold in seconds; age >= threshold is stale

    Returns:
        Tuple of (is_stale, age_s, info):
          is_stale (bool): True if file missing, unreadable, or age >= threshold_s
          age_s (int): Age in seconds (0 if file missing/unreadable)
          info (str or None): Descriptive message if stale/missing, None if fresh
    """
    try:
        if not hb_file.exists():
            return True, 0, "Heartbeat file missing"
    except OSError:
        # Parent dir unreadable (permissions) — cannot verify, report stale
        # (fail-closed, per the documented contract: unreadable => stale)
        return True, 0, "Heartbeat file unreadable"

    try:
        content = hb_file.read_text(encoding="utf-8").strip()
        if not content:
            return True, 0, "Heartbeat file empty"

        timestamp = int(content)
    except (ValueError, IOError):
        return True, 0, "Heartbeat file unreadable"

    age_seconds = int(time.time()) - timestamp

    # Check for future-dated timestamp (clock skew beyond tolerance)
    # More than 120s in the future is treated as stale, not clamped-to-fresh
    if age_seconds < -120:
        return True, 0, "Heartbeat timestamp in future (clock skew)"

    # Clamp small negative ages to 0 (normal clock skew recovery)
    age_seconds = max(0, age_seconds)

    if age_seconds >= threshold_s:
        return True, age_seconds, f"Heartbeat stale ({age_seconds}s >= {threshold_s}s)"

    return False, age_seconds, None


# ============================================================================
# CLI layer — Delegation to tools/cli.py (fail-open compatibility)
# ============================================================================

def run_subprocess(cmd, timeout=30, cwd=None):
    """
    Execute a subprocess with explicit timeout and Windows/Linux compatibility.

    Delegates to tools/cli.run_subprocess() for centralized subprocess handling.

    Args:
        cmd: Command as list (no shell=True; safe across platforms)
        timeout: Timeout in seconds (default 30)
        cwd: Working directory (default None = inherit from parent)

    Returns:
        Tuple of (returncode, stdout, stderr) as strings

    Raises:
        Exception: On timeout, file not found, or other OS errors
    """
    try:
        from tools import cli
        return cli.run_subprocess(cmd, timeout=timeout, cwd=cwd)
    except ImportError:
        # Fallback: raise ImportError if cli module not available
        raise ImportError("tools.cli module not found; install it or use cli.run_subprocess() directly")


def resolve_repo_root(args=None, env_key="AESOP_STATE_ROOT"):
    """
    Resolve repository/state root from multiple sources.

    Delegates to tools/cli.resolve_repo_root() for centralized root discovery.

    Args:
        args: argparse.Namespace with optional .root or .repo attribute
        env_key: Environment variable name for state root (default AESOP_STATE_ROOT)

    Returns:
        Resolved Path (always absolute, normalized)
    """
    try:
        from tools import cli
        return cli.resolve_repo_root(args=args, env_key=env_key)
    except ImportError:
        # Fallback: simple cwd() resolution
        if args:
            root_arg = getattr(args, "root", None) or getattr(args, "repo", None)
            if root_arg:
                return Path(root_arg).resolve()
        env_root = os.environ.get(env_key)
        if env_root:
            return Path(env_root).resolve()
        return Path.cwd()


def mask_secrets(text):
    """
    Replace known secret patterns with MASKED-<TYPE>.

    Delegates to tools/cli.mask_secrets() for centralized secret masking.

    Args:
        text: Input text to mask

    Returns:
        Text with secret patterns replaced by MASKED-<TYPE>
    """
    try:
        from tools import cli
        return cli.mask_secrets(text)
    except ImportError:
        # Fallback: no masking, return text as-is
        return text


def deterministic_json_dumps(obj, pretty=True):
    """
    JSON output with sorted keys for hermetic/reproducible output.

    Delegates to tools/cli.deterministic_json_dumps().

    Args:
        obj: Object to serialize
        pretty: If True, indent=2 for readability; else compact

    Returns:
        JSON string
    """
    try:
        from tools import cli
        return cli.deterministic_json_dumps(obj, pretty=pretty)
    except ImportError:
        # Fallback: basic json.dumps with sorted keys
        import json
        return json.dumps(
            obj,
            indent=2 if pretty else None,
            sort_keys=True,
            ensure_ascii=True,
        )


def exit_code(findings=None, error=None):
    """
    Return deterministic exit code.

    Delegates to tools/cli.exit_code() for centralized exit semantics.

    Convention:
      - 0 = success/clean (no findings, no error)
      - 1 = findings/violations detected (gate mode)
      - 2 = error (file read failure, subprocess failure, etc.)

    Args:
        findings: Number of findings (0 → exit 0, >0 → exit 1)
        error: Exception that occurred (if any, exit 2)

    Returns:
        Exit code (0, 1, or 2)
    """
    try:
        from tools import cli
        return cli.exit_code(findings=findings, error=error)
    except ImportError:
        # Fallback: simple logic
        if error is not None:
            return 2
        if findings is not None:
            return 1 if findings > 0 else 0
        return 0


# ============================================================================
# CI mode config -- the `ci` block of aesop.config.json
# MIRRORED in tools/ci_config.js (Node reads the same file: bin/cli.js, tools/doctor.js).
# Keep the two validators code-for-code in sync; tests/test_ci_config.py drives both
# on the same fixtures and fails on any divergence.
# ============================================================================

CI_MODES = ("hosted", "self-hosted-runner", "local-receipt-gate")
CI_WINDOWS_MATRIX = ("hosted", "self-hosted", "skip-on-pr")
CI_RECEIPT_GATES = ("off", "alongside", "required")
CI_DEFAULT_LABELS = ("self-hosted", "windows", "aesop-box")
CI_KNOWN_KEYS = ("mode", "windowsMatrix", "receiptGate", "selfHostedLabels")
VERIFY_RECEIPT_WORKFLOW = ".github/workflows/verify-receipt.yml"


def default_ci_config():
    """The `ci` block `aesop init` writes when no mode is chosen (hosted)."""
    return {
        "mode": ["hosted"],
        "windowsMatrix": "hosted",
        "receiptGate": "off",
        "selfHostedLabels": list(CI_DEFAULT_LABELS),
    }


def _finding(code, message):
    return {"code": code, "message": message}


def validate_ci_config(config, repo_root):
    """Validate the optional `ci` block of a parsed aesop.config.json.

    Args:
        config: the parsed config object (dict)
        repo_root: directory the config lives in; `receiptGate: required` needs
            `.github/workflows/verify-receipt.yml` to exist under it.

    Returns:
        list of {"code", "message"} findings; empty list means valid. An absent
        `ci` block is valid (every key has a default).
    """
    findings = []
    if not isinstance(config, dict) or "ci" not in config:
        return findings
    ci = config["ci"]
    if not isinstance(ci, dict):
        return [_finding("CI_NOT_OBJECT", "ci must be an object")]

    for key in ci:
        if key not in CI_KNOWN_KEYS:
            findings.append(_finding("CI_UNKNOWN_KEY", "ci.%s is not a known key (known: %s)"
                                     % (key, ", ".join(CI_KNOWN_KEYS))))

    modes = set()
    mode = ci.get("mode", ["hosted"])
    if not isinstance(mode, list):
        findings.append(_finding("CI_MODE_NOT_LIST", "ci.mode must be a list of modes"))
    elif not mode:
        findings.append(_finding("CI_MODE_EMPTY", "ci.mode must name at least one mode"))
    else:
        unknown = [m for m in mode if not isinstance(m, str) or m not in CI_MODES]
        if unknown:
            findings.append(_finding("CI_MODE_UNKNOWN", "ci.mode has unknown entries %r (known: %s)"
                                     % (unknown, ", ".join(CI_MODES))))
        if len(mode) != len(set(str(m) for m in mode)):
            findings.append(_finding("CI_MODE_DUPLICATE", "ci.mode lists a mode more than once"))
        modes = set(m for m in mode if isinstance(m, str) and m in CI_MODES)

    windows_matrix = ci.get("windowsMatrix", "hosted")
    if windows_matrix not in CI_WINDOWS_MATRIX:
        findings.append(_finding("CI_WINDOWS_MATRIX_UNKNOWN", "ci.windowsMatrix must be one of %s"
                                 % ", ".join(CI_WINDOWS_MATRIX)))
    elif windows_matrix == "self-hosted" and "self-hosted-runner" not in modes:
        findings.append(_finding("CI_WINDOWS_MATRIX_NEEDS_RUNNER_MODE",
                                 "ci.windowsMatrix=self-hosted requires 'self-hosted-runner' in ci.mode"))

    receipt_gate = ci.get("receiptGate", "off")
    if receipt_gate not in CI_RECEIPT_GATES:
        findings.append(_finding("CI_RECEIPT_GATE_UNKNOWN", "ci.receiptGate must be one of %s"
                                 % ", ".join(CI_RECEIPT_GATES)))
    else:
        if receipt_gate != "off" and "local-receipt-gate" not in modes:
            findings.append(_finding("CI_RECEIPT_GATE_NEEDS_RECEIPT_MODE",
                                     "ci.receiptGate=%s requires 'local-receipt-gate' in ci.mode" % receipt_gate))
        if receipt_gate == "required":
            workflow = Path(repo_root) / VERIFY_RECEIPT_WORKFLOW
            if not workflow.is_file():
                findings.append(_finding("CI_RECEIPT_GATE_REQUIRES_WORKFLOW",
                                         "ci.receiptGate=required but %s is not present; scaffold it with "
                                         "`aesop init --ci-mode local-receipt-gate` first" % VERIFY_RECEIPT_WORKFLOW))

    labels = ci.get("selfHostedLabels", list(CI_DEFAULT_LABELS))
    if not isinstance(labels, list):
        findings.append(_finding("CI_LABELS_NOT_LIST", "ci.selfHostedLabels must be a list of strings"))
    else:
        if any((not isinstance(x, str)) or not x.strip() for x in labels):
            findings.append(_finding("CI_LABELS_ITEM_INVALID", "ci.selfHostedLabels entries must be non-empty strings"))
        if "self-hosted-runner" in modes and "self-hosted" not in labels:
            findings.append(_finding("CI_LABELS_MISSING_SELF_HOSTED",
                                     "ci.selfHostedLabels must include 'self-hosted' (GitHub routes on it)"))
    return findings


def load_aesop_config(repo_root):
    """Read aesop.config.json under repo_root and validate its `ci` block.

    Returns:
        (config, findings): config is None when the file is missing or not JSON
        (findings then carry CONFIG_MISSING / CONFIG_INVALID_JSON); otherwise the
        parsed dict plus validate_ci_config() findings.
    """
    path = Path(repo_root) / "aesop.config.json"
    if not path.is_file():
        return None, [_finding("CONFIG_MISSING", "aesop.config.json not found in %s" % repo_root)]
    try:
        import json
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [_finding("CONFIG_INVALID_JSON", "aesop.config.json unreadable: %s" % exc)]
    return config, validate_ci_config(config, repo_root)


# ============================================================================
# Git hooks dir — the single resolver for "where does git run hooks from"
# ============================================================================

def _git_answer(cwd, args):
    """Run one read-only git query in cwd. Returns (stdout, None) or (None, error_text)."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "git %s could not run: %s" % (" ".join(args), exc)
    if result.returncode != 0:
        return None, result.stderr.strip() or "git %s exited %d" % (" ".join(args), result.returncode)
    return result.stdout.strip(), None


def resolve_hooks_dir(cwd):
    """Resolve the directory git runs pre-push hooks from for the repo at cwd.

    Asks git, so linked worktrees (where .git is a FILE and hooks live in the
    common dir) and core.hooksPath are both honoured. Falls back to
    `git rev-parse --git-common-dir` + "hooks" only when `--git-path` is unavailable.

    Returns:
        (Path, None) with the resolved (Path.resolve) hooks dir, or
        (None, error_text) carrying git's own error when git cannot answer.
        Never guesses a path on failure.
    """
    cwd = Path(cwd)
    out, err = _git_answer(cwd, ["rev-parse", "--git-path", "hooks"])
    if out is None:
        common, _common_err = _git_answer(cwd, ["rev-parse", "--git-common-dir"])
        if common is None:
            return None, err
        out = str(Path(common) / "hooks")
    hooks = Path(out)
    if not hooks.is_absolute():
        hooks = cwd / hooks
    return hooks.resolve(), None
