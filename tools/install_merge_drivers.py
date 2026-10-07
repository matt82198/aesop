#!/usr/bin/env python3
"""Register aesop's custom git merge drivers in this clone's git config (idempotent).
INDEX: Idempotent per-clone registration of aesop's custom git merge drivers in the repo's local git config: `merge.aesop-json-union.driver = python tools/json_list_merge.py %O %A %B` (ratchet `*-baseline.json`) and `merge.aesop-regen.driver = python tools/generated_merge.py %O %A %B %L %P` (registered generated artifacts, `tools/INDEX.md`); git never reads driver commands out of the repository, so an unregistered clone degrades to an ordinary conflict on those paths; default writes only what differs, `--check` exits 1 listing missing/mismatched drivers without writing, `--root DIR`, `--python EXE` (default literal `python`, resolved by git's sh at merge time), `--quiet`; run fail-open from `hooks/pre-push-policy.sh` so every clone that pushes is registered (worktrees share the config); exit 0=registered, 1=--check found gaps, 2=git unavailable

`.gitattributes` names the drivers (`merge=aesop-json-union`, `merge=aesop-regen`)
but git deliberately refuses to read the driver COMMAND out of the repository --
it executes code -- so each clone registers it once in `.git/config`. Linked
worktrees share that config, so one registration covers the whole fleet on a
box. The pre-push hook calls this (fail-open, quiet) so forgetting is not
possible on any clone that pushes.

CLI:
    install_merge_drivers.py [--check] [--root DIR] [--python EXE] [--quiet]
Exit codes: 0 = all drivers registered (written or already correct);
1 = --check found an unregistered/mismatched driver; 2 = git unavailable.
"""

import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

USAGE = "usage: install_merge_drivers.py [--check] [--root DIR] [--python EXE] [--quiet]\n"

# (driver key, human name, command template). {py} is the interpreter token.
DRIVERS: Tuple[Tuple[str, str, str], ...] = (
    ("aesop-json-union", "union-and-sort JSON string lists",
     "{py} tools/json_list_merge.py %O %A %B"),
    ("aesop-regen", "regenerate registered generated artifacts",
     "{py} tools/generated_merge.py %O %A %B %L %P"),
)


def _git(root: Path, *args: str) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git"] + list(args), cwd=str(root), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return proc.returncode, proc.stdout.strip()


def desired(python: str) -> List[Tuple[str, str, str]]:
    return [(key, name, cmd.format(py=python)) for key, name, cmd in DRIVERS]


def status(root: Path, python: str) -> List[Tuple[str, str, str, Optional[str]]]:
    """Per driver: (key, name, wanted_cmd, current_cmd_or_None)."""
    out = []
    for key, name, cmd in desired(python):
        rc, current = _git(root, "config", "--get", "merge.%s.driver" % key)
        out.append((key, name, cmd, current if rc == 0 else None))
    return out


def install(root: Path, python: str, check_only: bool, quiet: bool) -> int:
    rc, _ = _git(root, "rev-parse", "--git-dir")
    if rc != 0:
        sys.stderr.write("install_merge_drivers: %s is not a git repository (or git is unavailable)\n" % root)
        return 2
    gaps = 0
    for key, name, wanted, current in status(root, python):
        if current == wanted:
            if not quiet:
                sys.stdout.write("[OK] merge.%s.driver already registered\n" % key)
            continue
        gaps += 1
        if check_only:
            sys.stderr.write("MISSING merge.%s.driver (have %r, want %r)\n" % (key, current, wanted))
            continue
        rc1, _ = _git(root, "config", "merge.%s.name" % key, name)
        rc2, _ = _git(root, "config", "merge.%s.driver" % key, wanted)
        if rc1 != 0 or rc2 != 0:
            sys.stderr.write("install_merge_drivers: git config write failed for merge.%s\n" % key)
            return 2
        if not quiet:
            sys.stdout.write("[OK] registered merge.%s.driver = %s\n" % (key, wanted))
    if check_only and gaps:
        sys.stderr.write("run: python tools/install_merge_drivers.py\n")
        return 1
    return 0


def main(argv: List[str]) -> int:
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(__doc__ + "\n" + USAGE)
        return 0
    check_only = "--check" in argv
    quiet = "--quiet" in argv
    root: Optional[str] = None
    python = "python"
    it = iter(argv)
    for arg in it:
        if arg in ("--check", "--quiet"):
            continue
        if arg == "--root":
            root = next(it, None)
            if root is None:
                sys.stderr.write("install_merge_drivers: --root needs a value\n" + USAGE)
                return 2
            continue
        if arg == "--python":
            python = next(it, None) or ""
            if not python:
                sys.stderr.write("install_merge_drivers: --python needs a value\n" + USAGE)
                return 2
            continue
        sys.stderr.write("install_merge_drivers: unknown argument %r\n%s" % (arg, USAGE))
        return 2
    root_path = Path(root).resolve() if root else Path.cwd().resolve()
    return install(root_path, python, check_only, quiet)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
