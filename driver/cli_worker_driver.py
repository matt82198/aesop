#!/usr/bin/env python3
"""cli_worker_driver -- a headless vendor coding CLI as an AgentDriver worker.

Runs an arbitrary command line (argv list, no shell) as ONE isolated worker
subprocess per dispatch_worker() call. The prompt is written to the child's
stdin; the child runs with cwd=request.workdir. Any vendor CLI that can run
headless and edit files in its cwd fits this seam -- the Google CLI is one
config, not the design.

Honesty contract:
  * probe_capabilities() reports worker_filesystem_access=False. The child may
    write files, but the orchestrator (not a harness) discovers them, so the
    wave bridge routes this backend through the driver path.
  * Changed files are discovered by diffing `git status --porcelain -z`
    (content digests before vs after), not by trusting the CLI's self-report.
  * Any write outside owned_files rejects the whole dispatch (ok=False).
  * A non-zero exit, spawn failure, timeout, or non-git workdir is ok=False.
  * Green is never decided here: wave_bridge decides from run_command exit 0.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import hashlib
import os
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from typing import Dict, Optional, Sequence, Set, Tuple

from agent_driver import (
    AgentDriver,
    DriverCapabilities,
    WorkerRequest,
    WorkerResult,
    WorkerStatus,
    CommandResult,
    ROLE_WORKER,
    WORKER_DONE,
    WORKER_FAILED,
    WORKER_UNKNOWN,
)
from proc_util import kill_process_tree, run_shell_bounded, _DRAIN_TIMEOUT_S

_STDERR_TAIL_CHARS = 400
_GIT_TIMEOUT_S = 60.0
_PROMPT_PLACEHOLDER = "{prompt}"


def _validate_argv(argv: Sequence[str]) -> Tuple[str, ...]:
    """Reject anything that is not a non-empty list/tuple of non-empty strings."""
    if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)):
        raise TypeError("cli worker 'argv' must be a list of strings")
    if not argv:
        raise ValueError("cli worker 'argv' must not be empty")
    for part in argv:
        if not isinstance(part, str) or not part:
            raise ValueError("cli worker 'argv' entries must be non-empty strings")
    return tuple(argv)


def _normalize_owned(path: str) -> str:
    """Return the owned path as a POSIX-relative string, or raise ValueError."""
    if not isinstance(path, str) or not path:
        raise ValueError("owned_files entries must be non-empty strings")
    posix = path.replace("\\", "/")
    pure = PurePosixPath(posix)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"owned path escapes workdir: {path!r}")
    return pure.as_posix()


def _is_owned(rel: str, owned: Set[str]) -> bool:
    """A changed file is owned if listed exactly or under an owned directory."""
    if rel in owned:
        return True
    return any(rel.startswith(d.rstrip("/") + "/") for d in owned if d.endswith("/"))


def _git_snapshot(workdir: str) -> Dict[str, Tuple[str, str]]:
    """Map each dirty path under workdir (relative to it) to (status, digest).

    Raises RuntimeError if workdir is not inside a git work tree.
    """
    prefix_run = subprocess.run(
        ["git", "-C", workdir, "rev-parse", "--show-prefix"],
        capture_output=True,
        timeout=_GIT_TIMEOUT_S,
    )
    if prefix_run.returncode != 0:
        raise RuntimeError(f"workdir is not a git work tree: {workdir}")
    prefix = prefix_run.stdout.decode("utf-8", "surrogateescape").strip()

    status_run = subprocess.run(
        ["git", "-C", workdir, "status", "--porcelain", "-z", "--untracked-files=all"],
        capture_output=True,
        timeout=_GIT_TIMEOUT_S,
    )
    if status_run.returncode != 0:
        raise RuntimeError("git status failed in workdir")

    entries = status_run.stdout.decode("utf-8", "surrogateescape").split("\0")
    snapshot: Dict[str, Tuple[str, str]] = {}
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            i += 1  # rename/copy: the next NUL-separated field is the origin path
        if prefix:
            if not path.startswith(prefix):
                continue
            path = path[len(prefix):]
        full = Path(workdir) / path
        if full.is_file():
            digest = hashlib.sha256(full.read_bytes()).hexdigest()
        else:
            digest = ""
        snapshot[path] = (xy, digest)
    return snapshot


def _changed(before: Dict[str, Tuple[str, str]], after: Dict[str, Tuple[str, str]]) -> Set[str]:
    """Paths whose status or content differs between two snapshots."""
    keys = set(before) | set(after)
    return {k for k in keys if before.get(k) != after.get(k)}


class CliWorkerDriver(AgentDriver):
    """AgentDriver backed by an arbitrary headless CLI invoked via argv."""

    name = "cli"

    def __init__(
        self,
        argv: Sequence[str],
        timeout_s: float = 120.0,
        model_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self._argv = _validate_argv(argv)
        self._timeout_s = float(timeout_s)
        self._model_map = dict(model_map or {})

    # -- Operation 1: capability probe -------------------------------------
    def probe_capabilities(self) -> DriverCapabilities:
        """Honest, conservative probe: an unknown CLI gets the strictest tier."""
        return DriverCapabilities(
            name=self.name,
            parallel_dispatch=False,
            worker_filesystem_access=False,
            worker_shell_access=False,
            structured_output=False,
            worktree_isolation=False,
            native_cost_tracking=False,
            native_stall_detection=False,
            tool_use_accuracy=0.0,
            recommended_verification_tier=4,
            notes=(
                "Headless CLI subprocess. Writes are discovered via git status and "
                "ownership-checked by the driver; no native stall or cost reporting."
            ),
        )

    # -- Operation 2: dispatch an isolated worker --------------------------
    def dispatch_worker(self, request: WorkerRequest) -> WorkerResult:
        """Run the CLI once, in workdir, with the prompt on stdin."""
        worker_id = f"cli-{uuid.uuid4().hex[:12]}"
        try:
            owned = {_normalize_owned(p) for p in request.owned_files}
        except ValueError as exc:
            return WorkerResult(worker_id=worker_id, status=WORKER_FAILED, ok=False, error=str(exc))

        workdir = request.workdir or "."
        try:
            before = _git_snapshot(workdir)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            return WorkerResult(worker_id=worker_id, status=WORKER_FAILED, ok=False, error=str(exc))

        run = self._run_child(request.prompt, workdir)
        if run is None:
            return WorkerResult(
                worker_id=worker_id,
                status=WORKER_FAILED,
                ok=False,
                error=f"cli worker timed out after {self._timeout_s}s; process tree killed",
            )
        exit_code, stdout, stderr = run
        if exit_code != 0:
            tail = stderr[-_STDERR_TAIL_CHARS:]
            return WorkerResult(
                worker_id=worker_id,
                status=WORKER_FAILED,
                ok=False,
                text=stdout,
                error=f"cli worker exited {exit_code}: {tail}".rstrip(),
            )

        try:
            after = _git_snapshot(workdir)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            return WorkerResult(worker_id=worker_id, status=WORKER_FAILED, ok=False, error=str(exc))

        changed = sorted(_changed(before, after))
        outside = [p for p in changed if not _is_owned(p, owned)]
        if outside:
            return WorkerResult(
                worker_id=worker_id,
                status=WORKER_FAILED,
                ok=False,
                text=stdout,
                files_written=tuple(changed),
                error="ownership violation: wrote outside owned_files: " + ", ".join(outside),
            )

        patch_files = []
        for rel in changed:
            full = Path(workdir) / rel
            if full.is_file():
                patch_files.append(
                    {
                        "path": rel,
                        "contents": full.read_text(encoding="utf-8", errors="replace"),
                    }
                )
        structured = {
            "files": patch_files,
            "summary": (stdout or "").strip()[:200] or "cli worker patch",
            "done": True,
        }

        return WorkerResult(
            worker_id=worker_id,
            status=WORKER_DONE,
            ok=True,
            text=stdout,
            structured=structured,
            files_written=tuple(changed),
        )

    def _run_child(self, prompt: str, workdir: str):
        """Run argv with prompt on stdin. Returns (exit, stdout, stderr) or None on timeout."""
        popen_kwargs: Dict[str, object] = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        # A "{prompt}" argv element receives the prompt as an argument (agy -p
        # takes its prompt this way). When present, stdin is left empty.
        args = [prompt if part == _PROMPT_PLACEHOLDER else part for part in self._argv]
        stdin_text = "" if _PROMPT_PLACEHOLDER in self._argv else prompt
        try:
            proc = subprocess.Popen(
                args,
                cwd=workdir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **popen_kwargs,
            )
        except OSError as exc:
            return 127, "", str(exc)

        try:
            stdout, stderr = proc.communicate(input=stdin_text, timeout=self._timeout_s)
            return proc.returncode, stdout or "", stderr or ""
        except subprocess.TimeoutExpired:
            kill_process_tree(proc)
            try:
                proc.communicate(timeout=_DRAIN_TIMEOUT_S)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                pass
            return None

    def resolve_model(self, role: str) -> str:
        """Model from model_map for role, else the worker mapping, else ''.

        '' means the CLI chooses its own model; no id is invented here.
        """
        return self._model_map.get(role) or self._model_map.get(ROLE_WORKER, "")

    # -- Operation 3: stall detection / status -----------------------------
    def worker_status(self, worker_id: str) -> WorkerStatus:
        """Dispatch is synchronous, so liveness is not tracked after it returns."""
        return WorkerStatus(
            worker_id=worker_id,
            state=WORKER_UNKNOWN,
            stalled=False,
            detail="cli worker dispatch is synchronous; no liveness tracking",
        )

    # -- Operation 4: orchestrator-side command execution ------------------
    def run_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        shell: Optional[str] = None,
    ) -> CommandResult:
        """Run a verification command on the orchestrator host (bounded)."""
        return run_shell_bounded(command, cwd=cwd, timeout_s=self._timeout_s)
