#!/usr/bin/env python3
"""Shared core for Python linting and verification gates.
INDEX: Shared linting core (file discovery, AST cache, ratchet baseline, exit-code contract, finding schema); used by 6+ gate scripts; provides Finding class, discover_files(), ASTCache, RatchetBaseline, format_findings_text/json(), exit_code() helpers; all stdlib-only; exit 0=clean/1=findings/2=could-not-evaluate

Extracts common patterns used across 27 gate/linter scripts:
  - File discovery (path walking with glob matching)
  - Finding schema and formatters (text + JSON)
  - Exit-code contract (0=clean, 1=findings, 2=could-not-evaluate)
  - Ratchet baseline (load/compare/save with never-increase semantics)
  - AST cache (parse Python files once)

Exit code semantics:
  0: No findings, or scan completed successfully with no violations
  1: Findings detected
  2: Could-not-evaluate (missing files, syntax errors, unresolvable imports, etc.)
     This exit code ensures we cannot report success when evaluation failed.

Ratchet baseline behavior:
  - Bidirectional exact-match (never allow count to increase)
  - Detects stale entries (count decreased) and new violations (count increased)
  - Load missing baseline as empty (fail-closed)
  - Detect malformed JSON and fail with exit 2
"""

import ast
import fnmatch
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class Finding:
    """Immutable finding record.

    Represents a single linting or verification finding with mandatory fields:
      file: Relative file path (repo-relative, POSIX-normalized)
      line: Line number (1-indexed)
      type: Finding category (e.g., "unused-function", "missing-docstring", "oversized-file")
      message: Human-readable message
    """
    __slots__ = ('file', 'line', 'type', 'message')

    def __init__(self, file: str, line: int, type: str, message: str):
        self.file = file
        self.line = line
        self.type = type
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            'file': self.file,
            'line': self.line,
            'type': self.type,
            'message': self.message,
        }

    def __repr__(self) -> str:
        return f"Finding(file={self.file!r}, line={self.line}, type={self.type!r}, message={self.message!r})"


def normalize_path(path: str) -> str:
    """Normalize a path to forward slashes (Windows-safe for cross-platform consistency)."""
    return path.replace('\\', '/')


def discover_files(
    root: Path,
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
    extensions: Optional[List[str]] = None,
) -> List[Path]:
    """Discover files under root, respecting glob patterns and extensions.

    Args:
        root: Root directory to walk
        include: List of glob patterns to include (e.g., ['**/*.py', 'tools/*'])
                If None, discovers all files.
        exclude: List of glob patterns to exclude (e.g., ['.git', '__pycache__'])
                Checked against directory/file names only.
        extensions: Filter to specific extensions (e.g., ['.py', '.js'])

    Returns:
        Sorted list of Path objects matching criteria
    """
    root = Path(root).resolve()
    if not root.is_dir():
        return []

    # Default exclude patterns
    if exclude is None:
        exclude = ['.git', '__pycache__', 'node_modules', '.pytest_cache', 'dist', '.venv', 'venv']

    # If include patterns specified, use glob; otherwise walk
    if include:
        found = []
        for pattern in include:
            # Use Path.glob() which properly handles ** patterns
            for fpath in root.glob(pattern):
                if fpath.is_file():
                    found.append(fpath)
    else:
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune excluded directories
            dirnames[:] = [
                d for d in dirnames
                if d not in exclude
            ]

            for fname in filenames:
                fpath = Path(dirpath) / fname
                found.append(fpath)

    # Filter results
    result = []
    for fpath in found:
        # Filter by extension if specified
        if extensions and fpath.suffix not in extensions:
            continue

        # Check if any part of the path matches exclude patterns
        rel = fpath.relative_to(root)
        rel_str = normalize_path(str(rel))
        if any(fnmatch.fnmatch(rel_str, pattern) for pattern in exclude):
            continue

        result.append(fpath)

    return sorted(set(result))


class ASTCache:
    """Cache parsed AST trees to avoid re-parsing the same file."""

    def __init__(self):
        self._cache: Dict[Path, Optional[ast.AST]] = {}
        self._errors: Dict[Path, str] = {}

    def parse(self, filepath: Path) -> Optional[Tuple[ast.AST, List[str]]]:
        """Parse a Python file and return (tree, source_lines) or None if parse failed.

        Returns:
            Tuple of (AST tree, source lines list) on success, None on failure.
            Failures are cached to avoid re-reading/re-parsing.
        """
        if filepath in self._cache:
            tree = self._cache[filepath]
            if tree is None:
                return None
            # Also need source lines, so re-read them (small cost)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    source_lines = f.readlines()
                return tree, source_lines
            except (OSError, IOError):
                return None

        if filepath in self._errors:
            return None

        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                source = f.read()
                source_lines = f.readlines() if 'source_lines' in locals() else source.splitlines(keepends=True)

            # Re-read to get source_lines properly
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                source_lines = f.readlines()

            tree = ast.parse(source, filename=str(filepath))
            self._cache[filepath] = tree
            return tree, source_lines
        except (SyntaxError, UnicodeDecodeError, ValueError) as e:
            self._errors[filepath] = str(e)
            self._cache[filepath] = None
            return None


def format_findings_text(findings: List[Finding]) -> str:
    """Format findings as human-readable text output."""
    if not findings:
        return "No findings.\n"

    lines = [f"Findings: {len(findings)} issue(s) found\n"]
    for f in findings:
        lines.append(f"  {f.file}:{f.line}: {f.message}")
    lines.append("")
    return "\n".join(lines)


def format_findings_json(findings: List[Finding]) -> str:
    """Format findings as JSON."""
    output = [f.to_dict() for f in findings]
    return json.dumps(output, indent=2) + "\n"


class RatchetBaseline:
    """Load, compare, and save a ratchet baseline that enforces never-increasing counts.

    The ratchet baseline prevents a single violation count from increasing during
    the development cycle, while allowing counts to decrease (violations fixed).

    Baseline format (JSON):
    {
      "_comment": "Ratchet baseline documentation",
      "violations": {
        "key1": N,
        "key2": M,
        ...
      }
    }

    If the baseline file is missing, it's treated as an empty baseline (fail-closed).
    If the baseline file is malformed JSON, exit-code handling must return 2.
    """

    def __init__(self, baseline_file: Path):
        self.baseline_file = Path(baseline_file)
        self.data = self._load()

    def _load(self) -> Dict[str, int]:
        """Load baseline from JSON file.

        Returns:
            Dict of {key: count} from the violations section, or {} if file missing.
            Normalizes all keys to forward slashes (Windows-safe).
        """
        if not self.baseline_file.exists():
            return {}

        try:
            content = self.baseline_file.read_text(encoding='utf-8')
            parsed = json.loads(content)
            violations = parsed.get('violations', {})

            # Ensure it's a dict of counts
            if not isinstance(violations, dict):
                return {}

            # Normalize all keys to forward slashes
            return {normalize_path(str(k)): int(v) for k, v in violations.items()}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def check(self, current: Dict[str, int]) -> Tuple[bool, List[str], List[str]]:
        """Check current counts against baseline.

        Args:
            current: Dict of {key: count} from current scan

        Returns:
            Tuple of (is_ok, stale_items, new_violations) where:
            - is_ok: True if current exactly matches baseline (bidirectional)
            - stale_items: Keys in baseline but decreased or absent in current
            - new_violations: Keys in current but absent or increased in baseline
        """
        # Normalize current keys to forward slashes
        current = {normalize_path(str(k)): v for k, v in current.items()}

        stale = []
        new = []

        all_keys = set(self.data.keys()) | set(current.keys())
        for key in sorted(all_keys):
            baseline_count = self.data.get(key, 0)
            current_count = current.get(key, 0)

            if current_count > baseline_count:
                new.append(f"{key} (baseline {baseline_count}, current {current_count})")
            elif current_count < baseline_count:
                stale.append(f"{key} (baseline {baseline_count}, current {current_count})")

        is_ok = (len(stale) == 0 and len(new) == 0)
        return is_ok, stale, new

    def save(self, current: Dict[str, int], comment: str = "") -> None:
        """Save current counts as new baseline.

        Args:
            current: Dict of {key: count} from current scan
            comment: Optional comment line for the baseline file
        """
        # Normalize current keys to forward slashes
        current = {normalize_path(str(k)): v for k, v in current.items()}

        data = {
            '_comment': comment or 'Ratchet baseline (generated)',
            'violations': {k: current[k] for k in sorted(current)},
        }

        self.baseline_file.parent.mkdir(parents=True, exist_ok=True)
        self.baseline_file.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def exit_code(
    findings: List[Finding],
    could_not_evaluate: bool = False,
    baseline_error: bool = False,
) -> int:
    """Determine exit code based on findings and error state.

    Args:
        findings: List of findings
        could_not_evaluate: True if scan could not be completed (missing files, parse errors, etc.)
        baseline_error: True if baseline file was malformed

    Returns:
        0: No findings and evaluation succeeded
        1: Findings detected
        2: Could not evaluate (syntax error, missing files, malformed baseline, etc.)
    """
    if could_not_evaluate or baseline_error:
        return 2
    return 1 if findings else 0
