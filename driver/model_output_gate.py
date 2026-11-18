#!/usr/bin/env python3
"""Pre-test gates for worker patch output.

Runs after a worker response has been parsed and its files applied, and before
the item's test command runs. Two cheap checks:

1. Schema gate: the structured patch is valid per the worker schema, and the
   files list is non-empty. Reuses codex_driver._validate_patch_schema (the same
   validator the in-turn JSON retry uses) and adds the non-empty check that the
   shared validator does not enforce.
2. Parse gate: each produced file is checked without executing it.
   - .py: compile() only (no exec).
   - .json: json.loads().
   - .js/.mjs/.cjs: `node --check` on a temp copy, the same syntax check CI
     already runs. Skipped when node is not on PATH.
   Other extensions are not parsed.

This module only decides pass or fail. The regeneration loop and the
malformed-output reason live in wave_bridge.dispatch_item.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from codex_driver import _validate_patch_schema

MAX_REGENS = 2
REASON_MALFORMED = "malformed-output"

_JS_SUFFIXES = (".js", ".mjs", ".cjs")
_NODE_CHECK_TIMEOUT_S = 30


@dataclass
class GateOutcome:
    """Result of running the pre-test gates over one worker response.

    Attributes:
        ok: True only when every gate passed.
        failures: One human-readable message per failing check. Empty when ok.
    """

    ok: bool
    failures: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """Return all failures joined on one line, for logs and regen prompts."""
        return "; ".join(self.failures)


def check_schema(structured: Any) -> Optional[str]:
    """Check the structured patch against the worker schema.

    Args:
        structured: Parsed patch object from WorkerResult.structured.

    Returns:
        None when the patch is valid, otherwise a message naming the failure.
    """
    if structured is None:
        return "schema: no structured output was returned"
    try:
        _validate_patch_schema(structured)
    except ValueError as exc:
        return f"schema: {exc}"
    files = structured.get("files", [])
    if not files:
        return "schema: files list is empty; the patch must write at least one file"
    return None


def _check_node_syntax(path: str, contents: str) -> Optional[str]:
    """Run `node --check` on contents. Returns None when skipped or passing."""
    node = shutil.which("node")
    if node is None:
        return None
    suffix = Path(path).suffix or ".js"
    with tempfile.TemporaryDirectory(prefix="model-gate-") as tmp:
        target = Path(tmp) / f"check{suffix}"
        target.write_text(contents, encoding="utf-8")
        proc = subprocess.run(
            [node, "--check", str(target)],
            capture_output=True,
            text=True,
            timeout=_NODE_CHECK_TIMEOUT_S,
        )
    if proc.returncode == 0:
        return None
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    first = detail[0] if detail else f"exit {proc.returncode}"
    return f"parse: {path}: node --check failed: {first}"


def check_parse(path: str, contents: str) -> Optional[str]:
    """Parse-check one produced file without executing it.

    Args:
        path: Repo-relative path of the produced file (decides the parser).
        contents: File contents as produced by the worker.

    Returns:
        None when the file parses or has no parser, otherwise a failure message.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        try:
            compile(contents, path, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            lineno = getattr(exc, "lineno", None)
            where = f" (line {lineno})" if lineno else ""
            return f"parse: {path}: SyntaxError: {getattr(exc, 'msg', exc)}{where}"
        return None
    if suffix == ".json":
        try:
            json.loads(contents)
        except json.JSONDecodeError as exc:
            return f"parse: {path}: invalid JSON: {exc.msg} (line {exc.lineno})"
        return None
    if suffix in _JS_SUFFIXES:
        return _check_node_syntax(path, contents)
    return None


def gate_worker_output(structured: Any) -> GateOutcome:
    """Run the schema gate, then the parse gate on every produced file.

    The parse gate only runs when the schema gate passes, because it needs a
    well-formed files list.

    Args:
        structured: Parsed patch object from WorkerResult.structured.

    Returns:
        GateOutcome with ok=True when no gate fails.
    """
    schema_failure = check_schema(structured)
    if schema_failure is not None:
        return GateOutcome(ok=False, failures=[schema_failure])

    failures: List[str] = []
    for entry in structured["files"]:
        failure = check_parse(entry["path"], entry["contents"])
        if failure is not None:
            failures.append(failure)
    return GateOutcome(ok=not failures, failures=failures)
