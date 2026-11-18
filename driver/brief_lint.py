#!/usr/bin/env python3
"""Brief lint: the prompt must carry the names and strings the acceptance test checks.

Runs in the wave loop after the item prompt is built and before the first model
call (see ``wave_loop._dispatch_and_journal``). It reads the item's acceptance
test with ``ast`` and collects:

- string literals used as arguments to ``assert*`` calls, or compared with
  ``in`` / ``not in`` (docstrings and test names are NOT literals and are skipped);
- names imported from the module under test (``from X import a, b``; stdlib
  imports are skipped).

Literals shorter than ``MIN_LITERAL_LEN`` after stripping are trivial and ignored.
Anything the prompt does not contain is "missing".

Modes:
- default: append a ``SECTION_HEADER`` section listing the missing items to the
  prompt and log ``LOG_EVENT`` to ``state_dir/LOG_FILENAME``;
- strict (per item ``briefLintStrict: true`` or env ``AESOP_BRIEF_LINT_STRICT=1``):
  raise ``BriefLintRefusal`` so the dispatch is refused (fail closed).

stdlib-only, ASCII-only, Windows + Linux safe.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SECTION_HEADER = "Required names and strings"
LOG_EVENT = "brief-lint-added"
LOG_FILENAME = "brief_lint.log"
MIN_LITERAL_LEN = 4
STRICT_ENV = "AESOP_BRIEF_LINT_STRICT"


class BriefLintRefusal(RuntimeError):
    """Raised in strict mode when the prompt is missing required names or strings."""


def find_acceptance_test_file(item: Dict[str, Any], workdir: Optional[str]) -> Optional[Path]:
    """Locate the item's acceptance test file under ``workdir``.

    An explicit ``acceptanceTestFile`` key wins. Otherwise the first ``.py``
    token in ``testCmd`` that exists under ``workdir`` is used. Returns None
    when no existing file is found.
    """
    root = Path(workdir) if workdir else Path(".")
    explicit = item.get("acceptanceTestFile")
    if explicit:
        candidate = root / str(explicit)
        return candidate if candidate.is_file() else None
    for token in str(item.get("testCmd") or "").split():
        cleaned = token.strip("\"'")
        if cleaned.endswith(".py"):
            candidate = root / cleaned
            if candidate.is_file():
                return candidate
    return None


def _expectations(test_path: Path) -> Tuple[Set[str], Set[str]]:
    """Return (asserted string literals, names imported from the module under test)."""
    tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
    literals: Set[str] = set()
    imported: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            else:
                name = ""
            if name.startswith("assert"):
                for arg in node.args:
                    for sub in ast.walk(arg):
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                            literals.add(sub.value)
        elif isinstance(node, ast.Compare) and any(
            isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
        ):
            for sub in [node.left, *node.comparators]:
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    literals.add(sub.value)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split(".")[0] in sys.stdlib_module_names:
                continue
            for alias in node.names:
                if alias.name != "*":
                    imported.add(alias.name)

    return literals, imported


def lint_prompt(prompt: str, test_path: Path) -> List[str]:
    """Return the sorted asserted literals and imported names absent from ``prompt``."""
    literals, imported = _expectations(test_path)
    missing: Set[str] = set()
    for literal in literals:
        stripped = literal.strip()
        if len(stripped) < MIN_LITERAL_LEN:
            continue
        if stripped not in prompt:
            missing.add(stripped)
    for name in imported:
        if name not in prompt:
            missing.add(name)
    return sorted(missing)


def is_strict(item: Dict[str, Any]) -> bool:
    """Strict mode is on per item (``briefLintStrict: true``) or via the env var."""
    return item.get("briefLintStrict") is True or os.environ.get(STRICT_ENV) == "1"


def apply_brief_lint(
    item: Dict[str, Any],
    workdir: Optional[str],
    state_dir: Optional[str],
    strict: bool,
) -> Dict[str, Any]:
    """Return an item whose prompt carries every required name and string.

    Never mutates ``item``. No acceptance test, or nothing missing, returns a
    copy with the prompt unchanged and no log entry. Strict mode with missing
    names raises ``BriefLintRefusal``. Default mode appends the section and logs.
    """
    result = dict(item)
    test_path = find_acceptance_test_file(item, workdir)
    if test_path is None:
        return result

    prompt = str(item.get("prompt") or "")
    missing = lint_prompt(prompt, test_path)
    if not missing:
        return result

    slug = item.get("slug", "unknown")
    if strict:
        raise BriefLintRefusal(
            f"brief lint refused {slug}: prompt missing required names: {', '.join(missing)}"
        )

    section = SECTION_HEADER + ":\n" + "\n".join(f"- {m}" for m in missing) + "\n"
    result["prompt"] = prompt.rstrip() + "\n\n" + section

    if state_dir:
        try:
            log_path = Path(state_dir) / LOG_FILENAME
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{LOG_EVENT} {slug} missing={len(missing)}\n")
        except Exception:
            # Logging is advisory; the section is already appended, so dispatch continues.
            pass
    return result
