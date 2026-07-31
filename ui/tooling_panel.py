#!/usr/bin/env python3
"""
tooling_panel.py -- Tooling dashboard panel API (wave-30 addition).

Provides GET /api/tooling/summary endpoint that aggregates results from the
repo's analysis tools (TODO tracker, test coverage gaps, dead code check,
import cycle check, encoding lint).

Results are cached for 60 seconds to avoid repeated subprocess scans.
Tools that don't exist yet gracefully degrade to null for that metric.

No external dependencies; stdlib only.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import config  # noqa: E402 — call-time reads, never frozen imports

# Cache for tooling scan results
_cache_lock = __import__("threading").Lock()
_cache_data = None  # type: dict | None
_cache_time = 0.0
_CACHE_TTL = 60  # seconds


class ToolError(Exception):
    """Error from tool execution with categorization."""
    def __init__(self, error_class, message=""):
        self.error_class = error_class  # "tool-exit-nonzero", "tool-timeout", "parse-error", "file-not-found"
        self.message = message
        super().__init__(f"{error_class}: {message}")


def _run_tool(tool_name, args=None):
    """Run a tool script and return parsed JSON output, or None if unavailable.

    Args:
        tool_name: filename under tools/ (e.g. "todo_tracker.py")
        args: extra CLI arguments (default: ["--json"])

    Returns:
        Parsed JSON dict/list, or None if tool missing/failed.

    Raises:
        ToolError: If tool execution fails (with categorized error_class).
    """
    if args is None:
        args = ["--json"]
    tool_path = Path(config.AESOP_ROOT) / "tools" / tool_name
    if not tool_path.is_file():
        return None

    try:
        result = subprocess.run(
            [sys.executable, str(tool_path)] + args,
            capture_output=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            cwd=str(config.AESOP_ROOT),
        )
        if result.returncode != 0:
            raise ToolError("tool-exit-nonzero", f"{tool_name} exited with code {result.returncode}")
        output = result.stdout.strip()
        if not output:
            return None
        return json.loads(output)
    except subprocess.TimeoutExpired as e:
        raise ToolError("tool-timeout", f"{tool_name} timed out after 30s")
    except json.JSONDecodeError as e:
        raise ToolError("parse-error", f"Invalid JSON output from {tool_name}")
    except OSError as e:
        raise ToolError("file-not-found", f"Cannot access {tool_name}")


def _extract_todo_count(data):
    """Extract TODO/FIXME count from todo_tracker.py --json output.

    Expected shapes:
      {"count": N, ...}
      {"todos": [...], "count": N}
      [item, item, ...]  (len = count)
    """
    if data is None:
        return None
    if isinstance(data, dict):
        if "count" in data:
            return data["count"]
        if "todos" in data:
            return len(data["todos"])
    if isinstance(data, list):
        return len(data)
    return None


def _extract_coverage(data):
    """Extract test coverage percentage from test_coverage_gaps.py --json output.

    Expected shapes:
      {"coverage_pct": 85.2, ...}
      {"coverage": 85.2, ...}
      {"percentage": 85.2, ...}
    """
    if data is None:
        return None
    if isinstance(data, dict):
        for key in ("coverage_pct", "coverage", "percentage"):
            if key in data:
                val = data[key]
                if isinstance(val, (int, float)):
                    return round(val, 1)
    return None


def _extract_dead_code(data):
    """Extract dead code count from dead_code_check.py --json output.

    Expected shapes:
      {"count": N, ...}
      {"dead": [...]}
      [item, ...]
    """
    if data is None:
        return None
    if isinstance(data, dict):
        if "count" in data:
            return data["count"]
        if "dead" in data:
            return len(data["dead"])
    if isinstance(data, list):
        return len(data)
    return None


def _extract_import_cycles(data):
    """Extract import cycle count from import_cycle_check.py --json output.

    Expected shapes:
      {"count": N, ...}
      {"cycles": [...]}
      [cycle, ...]
    """
    if data is None:
        return None
    if isinstance(data, dict):
        if "count" in data:
            return data["count"]
        if "cycles" in data:
            return len(data["cycles"])
    if isinstance(data, list):
        return len(data)
    return None


def _extract_encoding_issues(data):
    """Extract encoding issue count from encoding_lint.py --json output.

    Expected shapes:
      {"count": N, ...}
      {"issues": [...]}
      [issue, ...]
    """
    if data is None:
        return None
    if isinstance(data, dict):
        if "count" in data:
            return data["count"]
        if "issues" in data:
            return len(data["issues"])
    if isinstance(data, list):
        return len(data)
    return None


def _scan_tooling():
    """Run all tooling scans and return aggregated summary dict.

    Returns:
        Dict with metric keys (null if tool unavailable) + scanned_at timestamp.
        If a tool raises ToolError, logs it and continues (graceful degradation).
    """
    results = {}

    for tool_name, key in [
        ("todo_tracker.py", "todo_raw"),
        ("test_coverage_gaps.py", "coverage_raw"),
        ("dead_code_check.py", "dead_code_raw"),
        ("import_cycle_check.py", "import_cycle_raw"),
        ("encoding_lint.py", "encoding_raw"),
    ]:
        try:
            results[key] = _run_tool(tool_name)
        except ToolError as e:
            print(f"[tooling] {tool_name} error ({e.error_class}): {e.message}", file=sys.stderr, flush=True)
            results[key] = None

    return {
        "todo_count": _extract_todo_count(results.get("todo_raw")),
        "coverage_pct": _extract_coverage(results.get("coverage_raw")),
        "dead_code_count": _extract_dead_code(results.get("dead_code_raw")),
        "import_cycle_count": _extract_import_cycles(results.get("import_cycle_raw")),
        "encoding_issues": _extract_encoding_issues(results.get("encoding_raw")),
        "scanned_at": time.time(),
    }


def get_tooling_summary(force=False):
    """Return cached tooling summary, re-scanning if stale or forced.

    Args:
        force: If True, bypass cache and re-scan.

    Returns:
        Dict with metric keys (null if tool unavailable) + scanned_at timestamp.
    """
    global _cache_data, _cache_time

    now = time.time()
    with _cache_lock:
        if not force and _cache_data is not None and (now - _cache_time) < _CACHE_TTL:
            return dict(_cache_data)

    # Run outside lock to avoid blocking other requests
    summary = _scan_tooling()

    with _cache_lock:
        _cache_data = summary
        _cache_time = now

    return dict(summary)


def serve_api_tooling_summary(handler, force=False):
    """Handle GET /api/tooling/summary — aggregated tooling scan results.

    Read-only; runs tool subprocesses (short timeout, cached 60s).
    Gracefully degrades to null for any metric whose tool is missing.

    Args:
        handler: DashboardHandler instance
        force: bypass cache (query param ?force=1)
    """
    try:
        payload = get_tooling_summary(force=force)
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        handler.end_headers()
        handler.wfile.write(json.dumps(payload, default=str).encode("utf-8"))
    except ToolError as e:
        # Categorized tool execution error; return safe error class
        print(f"[serve_api_tooling_summary] Tool error: {e}", file=sys.stderr)
        try:
            handler.send_response(500)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(json.dumps({"error": e.error_class}).encode("utf-8"))
        except Exception:
            pass
    except Exception as e:
        # Import here to avoid circular import at module level
        from handler import _is_client_disconnect_error
        if _is_client_disconnect_error(e):
            return
        print(f"[serve_api_tooling_summary] Uncaught exception: {e}", file=sys.stderr)
        try:
            handler.send_response(500)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(json.dumps({"error": "internal-error"}).encode("utf-8"))
        except Exception:
            pass
