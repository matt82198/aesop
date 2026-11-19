"""Shared helpers for the harness-neutral comparison corpus.

The corpus is plain files: each task has a seed working copy, a hidden check
script and a known-good reference. A harness (Antigravity, Claude Code with
aesop machinery, or anything else) is given only the prompt and a fresh copy of
the seed. Pass/fail is decided by the check script, never by the harness.

Stdlib only. No model or API call happens anywhere in this module.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HC_DIR = Path(__file__).resolve().parent
MANIFEST = HC_DIR / "corpus" / "manifest.jsonl"
TASKS_DIR = HC_DIR / "corpus" / "tasks"
RESULTS_ROOT = HC_DIR / "results"
CHECK_TIMEOUT_CAP_S = 600
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def valid_name(value: str) -> bool:
    """Return True for a safe harness or run id: no separators, no '..', no leading dot."""
    return bool(NAME_RE.fullmatch(value)) and ".." not in value


@dataclass(frozen=True)
class CheckResult:
    """Outcome of running a task's hidden check against a working copy."""

    passed: bool
    exit_code: int
    reason: str


def load_manifest(path: Path = MANIFEST) -> list[dict]:
    """Return every manifest row, in file order."""
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def task_by_id(task_id: str) -> dict:
    """Return the manifest row for ``task_id`` or raise KeyError."""
    for row in load_manifest():
        if row["id"] == task_id:
            return row
    raise KeyError(task_id)


def task_dir(row: dict) -> Path:
    """Return the on-disk directory of a task (contains seed/, check/, reference/)."""
    return TASKS_DIR / row["id"]


def prepare_task(row: dict, dest: Path) -> Path:
    """Copy the seed working copy to ``dest`` and return it.

    Refuses to write into a directory that already holds files, so every run
    starts from a fresh copy of the seed.
    """
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError("destination is not empty: %s" % dest)
    shutil.copytree(task_dir(row) / "seed", dest, dirs_exist_ok=True)
    return dest


def overlay_reference(row: dict, workdir: Path) -> Path:
    """Copy the known-good reference files over ``workdir``. Used to prove the check passes."""
    shutil.copytree(task_dir(row) / "reference", Path(workdir), dirs_exist_ok=True)
    return Path(workdir)


def run_check(row: dict, workdir: Path) -> CheckResult:
    """Run the hidden check for ``row`` against ``workdir``.

    The manifest ``check`` field is a command template with ``{task_dir}`` and
    ``{workdir}`` placeholders. The leading ``python`` is replaced by the
    interpreter running this module so the check never picks up a different one.
    """
    command = row["check"].format(task_dir=str(task_dir(row)), workdir=str(Path(workdir)))
    parts = command.split(" ", 1)
    if parts[0] != "python":
        raise ValueError("check command must start with python: %r" % command)
    argv = [sys.executable] + (parts[1].split(" ") if len(parts) > 1 else [])
    timeout = min(int(row["timeout_s"]), CHECK_TIMEOUT_CAP_S)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(workdir),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(False, -1, "check timed out after %ss" % timeout)
    passed = proc.returncode == 0
    reason = "" if passed else (proc.stdout + proc.stderr).strip()[-400:]
    return CheckResult(passed, proc.returncode, reason)


def write_json(path: Path, data: dict) -> None:
    """Write ``data`` as UTF-8 JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    """Read a UTF-8 JSON object from ``path``."""
    return json.loads(path.read_text(encoding="utf-8"))
