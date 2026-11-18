"""Repeat-failure detection for the wave repair loop.

A repair round that reproduces the same test failure as the previous round is
re-asking a model that has already shown it cannot fix that failure. This
module answers two questions for the repair loop:

  * record_failure(): given the latest test output, is this a new failure
    ("repair"), the first identical repeat ("fresh" -- redispatch with a
    fresh context), or the second identical repeat ("end")?
  * build_fresh_prompt(): the fresh-context prompt, carrying only the task,
    the current owned file content and one failing case.

The fingerprint is a hash of the failure itself (failing test ids, error and
assert lines, exit code) with timing, temp paths and memory addresses removed.
It is NOT the manifest identity hash (wave_loop._item_fingerprint).
"""

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List

_KEEP_RE = re.compile(
    r"\bFAIL\b|\bFAILED\b|\bERROR\b|Error\b|Exception\b|assert",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(r"Error\b|Exception\b|assert", re.IGNORECASE)
_ID_RE = re.compile(r"\bFAIL\b|\bERROR\b", re.IGNORECASE)
_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_DURATION_RE = re.compile(r"\b\d+(\.\d+)?s\b")
_TEMP_PATH_RE = re.compile(r"[\w:\\/.\-]*tmp[\w\\/.\-]*", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _failure_lines(stdout: str, stderr: str) -> List[str]:
    """Return the raw lines that describe the failure (ids, errors, summary)."""
    text = f"{stdout or ''}\n{stderr or ''}"
    return [ln.rstrip() for ln in text.splitlines() if _KEEP_RE.search(ln)]


def _normalize(line: str) -> str:
    """Strip timing, memory address and temp path noise from one line."""
    line = _HEX_RE.sub("0xADDR", line)
    line = _DURATION_RE.sub("Ns", line)
    line = _TEMP_PATH_RE.sub("TMPPATH", line)
    return _WS_RE.sub(" ", line).strip()


def failure_fingerprint(stdout: str, stderr: str, exit_code: Any) -> str:
    """Stable sha256 hex digest of a test failure, ignoring timing noise."""
    kept = [_normalize(ln) for ln in _failure_lines(stdout, stderr)]
    if not kept:
        # No recognisable failure lines: fall back to the tail of the output.
        tail = [ln for ln in f"{stdout or ''}\n{stderr or ''}".splitlines() if ln.strip()]
        kept = [_normalize(ln) for ln in tail[-3:]]
    payload = "\n".join(kept) + f"\nexit={exit_code}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def one_failing_case(stdout: str, stderr: str) -> str:
    """Return one failing case: the failing test id line and its error line.

    Unrelated progress output is excluded. Falls back to the last non-empty
    line when no failure line can be identified.
    """
    lines = _failure_lines(stdout, stderr)
    error_line = next((ln for ln in lines if _ERROR_RE.search(ln)), None)
    id_line = next((ln for ln in lines if _ID_RE.search(ln)), None)
    picked = [ln for ln in (id_line, error_line) if ln]
    if picked:
        return "\n".join(dict.fromkeys(picked))
    tail = [ln for ln in f"{stdout or ''}\n{stderr or ''}".splitlines() if ln.strip()]
    return tail[-1].strip() if tail else ""


def record_failure(state: Dict[str, Any], stdout: str, stderr: str, exit_code: Any) -> str:
    """Record one test outcome into the per-item streak state.

    Returns:
        "repair": a new failure (or the first observation). Normal repair.
        "fresh": the first identical repeat. Redispatch with a fresh context.
        "end": the second identical repeat. The item must end as failed.
    """
    fingerprint = failure_fingerprint(stdout, stderr, exit_code)
    previous = state.get("lastFingerprint")
    state["lastFingerprint"] = fingerprint
    state["lastCase"] = one_failing_case(stdout, stderr)

    if previous is None or previous != fingerprint:
        state["failureRepeats"] = 0
        return "repair"

    repeats = state.get("failureRepeats", 0) + 1
    state["failureRepeats"] = repeats
    return "fresh" if repeats == 1 else "end"


def needs_fresh_redispatch(state: Dict[str, Any]) -> bool:
    """True when the next repair round must use a fresh-context prompt."""
    return state.get("failureRepeats", 0) == 1


def build_fresh_prompt(task: str, owned_files: Dict[str, str], failing_case: str) -> str:
    """Build the fresh-context redispatch prompt: task, owned files, one case."""
    parts = [
        task.rstrip(),
        "",
        "The previous attempt reproduced the same test failure. Start again from",
        "the current file content below. Do not repeat the previous approach.",
        "",
        "Current owned file content:",
    ]
    for name, content in owned_files.items():
        parts.append(f"--- {name} ---")
        parts.append(content.rstrip())
    parts.append("")
    parts.append("One failing case (expected vs actual):")
    parts.append(failing_case.strip())
    return "\n".join(parts) + "\n"


def read_owned_files(workdir: str, owned_files: List[str]) -> Dict[str, str]:
    """Read current owned file content from workdir. Missing files are skipped."""
    contents: Dict[str, str] = {}
    for name in owned_files:
        path = Path(workdir) / name
        if path.is_file():
            contents[name] = path.read_text(encoding="utf-8", errors="replace")
    return contents
