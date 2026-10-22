"""Create a per-project event store: orphan branch, worktree, store.json and the guard.

Usage (stdlib only, run isolated):

    python -I tools/event_store/init.py --repo <repo> --project <name> \
        --branch events/<name> --worktree <path>

What it creates:

- an orphan branch ``--branch`` whose first commit has no parent;
- a worktree at ``--worktree`` checked out on that branch;
- ``store.json`` as ``{"project": <name>, "schema": 2}``;
- ``.gitattributes`` containing ``*.jsonl -text``;
- empty ``events/`` and ``runs/`` directories (each with ``.gitkeep``) and a ``README.md``;
- the append-only pre-commit guard, installed for this worktree only (see below).

Guard install: the guard script is copied to ``<git-common-dir>/event-store-hooks/<name>/pre-commit``
and the worktree-scoped ``core.hooksPath`` is pointed at that directory. Enabling
``extensions.worktreeConfig`` lets the setting stay per worktree, so the primary tree and every
other worktree keep their own hooks. The hook is never written into a tracked path.

Exit codes: 0 created; 1 the branch or the worktree path already exists (nothing changed);
2 any other failure (a partly created store is rolled back before exit).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

GUARD_SOURCE = Path(__file__).resolve().parent / "guard" / "events_append_only.sh"
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
EXIT_CREATED = 0
EXIT_EXISTS = 1
EXIT_ERROR = 2

README_TEXT = """# Event store: {project}

Append-only event log for the `{project}` project. Code does not live here; it lives in aesop.

Layout:

- `store.json`: `{{"project": "{project}", "schema": 2}}`
- `events/YYYY-MM-DD.jsonl`: one event per line, append-only
- `runs/`: run records
- `.gitattributes`: `*.jsonl -text`, so git never rewrites event lines

The pre-commit guard (`events_append_only.sh`) refuses any commit that deletes, rewrites or
renames an existing event line. It is installed for this worktree only, through
`core.hooksPath` set with `git config --worktree`, which lives under the store's git common
directory and not in any tracked path. Re-create it with `python -I tools/event_store/init.py`
from the aesop checkout if the worktree is moved or the hooks dir is lost.
"""


class InitError(RuntimeError):
    """A git or filesystem step failed. The message is shown to the operator."""


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git -C cwd with args and return the completed process."""
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and result.returncode != 0:
        raise InitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def _branch_exists(repo: Path, branch: str) -> bool:
    """True when refs/heads/<branch> exists in repo."""
    result = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    return result.returncode == 0


def _write_store_files(worktree: Path, project: str) -> None:
    """Write the store's data and metadata files into the worktree (not yet committed)."""
    (worktree / "store.json").write_text(
        json.dumps({"project": project, "schema": 2}) + "\n", encoding="utf-8", newline="\n"
    )
    (worktree / ".gitattributes").write_text("*.jsonl -text\n", encoding="utf-8", newline="\n")
    for directory in ("events", "runs"):
        (worktree / directory).mkdir(exist_ok=True)
        (worktree / directory / ".gitkeep").write_bytes(b"")
    (worktree / "README.md").write_text(
        README_TEXT.format(project=project), encoding="utf-8", newline="\n"
    )


def _install_guard(worktree: Path, project: str) -> Path:
    """Copy the guard into the common-dir hooks location and point this worktree at it.

    Returns the hooks directory that core.hooksPath now names.
    """
    common = Path(_git(worktree, "rev-parse", "--git-common-dir").stdout.strip())
    if not common.is_absolute():
        common = (worktree / common).resolve()
    hooks_dir = common / "event-store-hooks" / project
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-commit"
    shutil.copyfile(GUARD_SOURCE, hook)
    hook.chmod(0o755)
    _git(worktree, "config", "extensions.worktreeConfig", "true")
    _git(worktree, "config", "--worktree", "core.hooksPath", str(hooks_dir))
    return hooks_dir


def create_store(repo: Path, project: str, branch: str, worktree: Path) -> int:
    """Create the event store. Return an exit code; never leaves a half-made store behind."""
    if not PROJECT_RE.match(project):
        print(f"init: invalid project name {project!r} (use lowercase letters, digits, - and _)",
              file=sys.stderr)
        return EXIT_ERROR
    if not branch:
        print("init: --branch must not be empty", file=sys.stderr)
        return EXIT_ERROR

    _git(repo, "rev-parse", "--git-dir")  # fails with a clear message when repo is not a git repo
    if _branch_exists(repo, branch):
        print(f"init: branch {branch} already exists in {repo}; nothing changed", file=sys.stderr)
        return EXIT_EXISTS
    if worktree.exists():
        print(f"init: worktree path {worktree} already exists; nothing changed", file=sys.stderr)
        return EXIT_EXISTS

    created_worktree = False
    try:
        _git(repo, "worktree", "add", "--orphan", "-b", branch, str(worktree))
        created_worktree = True
        _write_store_files(worktree, project)
        _git(worktree, "add", "--all")
        _git(worktree, "commit", "-q", "-m", f"init: event store for {project}")
        _install_guard(worktree, project)
    except (InitError, OSError) as exc:
        print(f"init: failed, rolling back: {exc}", file=sys.stderr)
        if created_worktree:
            _git(repo, "worktree", "remove", "--force", str(worktree), check=False)
            _git(repo, "branch", "-D", branch, check=False)
        shutil.rmtree(worktree, ignore_errors=True)
        return EXIT_ERROR

    print(f"init: created store {project} on {branch} at {worktree}")
    return EXIT_CREATED


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for init.py."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", required=True, help="git repo that will hold the store branch")
    parser.add_argument("--project", required=True, help="project name, e.g. keyrun")
    parser.add_argument("--branch", required=True, help="orphan branch to create, e.g. events/keyrun")
    parser.add_argument("--worktree", required=True, help="path for the store worktree")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Return the exit code."""
    args = build_parser().parse_args(argv)
    try:
        return create_store(
            Path(args.repo).resolve(), args.project, args.branch, Path(args.worktree).resolve()
        )
    except InitError as exc:
        print(f"init: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
