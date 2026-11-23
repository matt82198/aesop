#!/usr/bin/env python3
"""HeadlessCliDriver -- AgentDriver for local headless agent CLIs.

Each dispatch_worker() call runs ONE isolated headless CLI process in the
request's workdir and returns its outcome. Profiles:

  * "codex-exec"   -- ``codex exec``. The prompt is written to stdin (``-``),
    ``--cd <workdir>`` and ``--sandbox workspace-write`` bound the worker, and
    ``--output-last-message <tmpfile>`` is the text channel. Open-weight:
    ``--oss --local-provider {ollama,lmstudio}`` plus ``-m <model>``.
  * "agy-headless" -- ``agy -p <prompt> --output-format json --print-timeout 0
    --mode accept-edits [--model <model>]``. agy has no provider flag, so a
    provider override is rejected instead of being silently ignored.

Isolation and secrets:
  * argv is a list and no shell is ever used.
  * The child environment is an explicit allow-list: DEFAULT_ENV_ALLOWLIST plus
    the names an operator lists in the env_passthrough list of the worker seat
    config. Cloud API keys are not in the default list; codex authenticates with
    its own CODEX_HOME login. Env values are never logged, placed in argv, or
    echoed into errors.
  * Never sets WorkerResult.verified; wave_bridge owns that field.

Unverified: the ``agy --output-format json`` payload shape has not been checked
against a live agy run. _parse_agy_text() therefore reads a known text field if
one is present and otherwise falls back to the raw stdout.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from agent_driver import (
    WORKER_DONE,
    WORKER_FAILED,
    WORKER_UNKNOWN,
    AgentDriver,
    CommandResult,
    DriverCapabilities,
    WorkerRequest,
    WorkerResult,
    WorkerStatus,
)
from proc_util import run_shell_bounded

PROFILES = ("codex-exec", "agy-headless")
OPEN_WEIGHT_PROVIDERS = ("ollama", "lmstudio")

DEFAULT_TIMEOUT_S = 600.0
_COMMAND_TIMEOUT_S = 600.0
_GIT_TIMEOUT_S = 60.0
_STDERR_TAIL_CHARS = 400

# Names copied from the orchestrator environment into every child. Keys for
# cloud providers are deliberately absent. OS and home variables are here so
# the CLIs can locate their binaries and login state on Windows and POSIX.
DEFAULT_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "CODEX_HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_AGY_TEXT_FIELDS = ("result", "text", "response", "output")

# Captured before any caller can patch subprocess.run. git status is a local,
# read-only probe, not a worker invocation, so it must not appear as the
# "last call" on the patched runner that drivers and tests inspect.
_GIT_RUN = subprocess.run


def validate_headless_options(
    profile: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    env_passthrough: Iterable[str] = (),
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> None:
    """Validate a headless profile and its options; raise on anything unsafe.

    Raises:
        ValueError: unknown profile, unsupported provider, agy provider
            override, malformed model, invalid env name, or non-positive timeout.
        TypeError: env_passthrough is not a list or tuple of names.
    """
    if profile not in PROFILES:
        raise ValueError(
            f"unknown headless profile {profile!r}; valid: {', '.join(PROFILES)}"
        )
    if provider is not None:
        if profile == "agy-headless":
            raise ValueError(
                "agy-headless has no provider flag; remove 'provider' "
                "(open-weight routing is codex-exec only)"
            )
        if provider not in OPEN_WEIGHT_PROVIDERS:
            raise ValueError(
                f"provider {provider!r} not supported for codex-exec; "
                f"valid: {', '.join(OPEN_WEIGHT_PROVIDERS)}"
            )
    if model is not None:
        if not isinstance(model, str) or not model.strip() or model.startswith("-"):
            raise ValueError("'model' must be a non-empty string not starting with '-'")
    if isinstance(env_passthrough, str) or not isinstance(env_passthrough, (list, tuple)):
        raise TypeError("'env_passthrough' must be a list of environment variable names")
    for name in env_passthrough:
        if not isinstance(name, str) or not _ENV_NAME_RE.match(name):
            raise ValueError(f"env_passthrough entry {name!r} is not a valid variable name")
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or timeout_s <= 0
    ):
        raise ValueError("'timeout_s' must be a positive number")


def _parse_agy_text(stdout: str) -> str:
    """Return the text payload from agy stdout (UNVERIFIED JSON shape).

    Reads the first string field named in _AGY_TEXT_FIELDS when stdout is a JSON
    object. Otherwise returns the stripped raw stdout.
    """
    raw = (stdout or "").strip()
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(payload, dict):
        for key in _AGY_TEXT_FIELDS:
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return raw


def _changed_files(workdir: str, env: Dict[str, str]) -> Tuple[str, ...]:
    """List paths changed in workdir (tracked and untracked) per git status.

    Returns an empty tuple when workdir is not a git repository or git fails.
    """
    try:
        proc = _GIT_RUN(
            ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
            cwd=workdir,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if proc.returncode != 0:
        return ()
    entries = (proc.stdout or "").split("\0")
    changed = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            # Rename/copy records carry the source path as the next NUL token.
            i += 1
        changed.append(path)
    return tuple(changed)


class HeadlessCliDriver(AgentDriver):
    """AgentDriver that runs one headless agent CLI per dispatch_worker() call."""

    name = "headless-cli"

    def __init__(
        self,
        profile: str,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        env_passthrough: Iterable[str] = (),
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ):
        env_names = tuple(env_passthrough)
        validate_headless_options(
            profile,
            provider=provider,
            model=model,
            env_passthrough=env_names,
            timeout_s=timeout_s,
        )
        self.name = profile
        self._profile = profile
        self._model = model
        self._provider = provider
        self._timeout_s = float(timeout_s)
        self._env_allowlist = DEFAULT_ENV_ALLOWLIST + env_names
        self._statuses: Dict[str, WorkerStatus] = {}

    # -- Operation 1: capabilities -----------------------------------------
    def probe_capabilities(self) -> DriverCapabilities:
        """Describe the headless CLI honestly; accuracy is unmeasured."""
        return DriverCapabilities(
            name=self._profile,
            parallel_dispatch=False,
            worker_filesystem_access=True,
            worker_shell_access=True,
            structured_output=False,
            worktree_isolation=False,
            native_cost_tracking=False,
            native_stall_detection=False,
            tool_use_accuracy=0.0,
            recommended_verification_tier=4,
            available_models=(self._model,) if self._model else (),
            notes=(
                "headless CLI, one process per dispatch; tool-use accuracy "
                "unmeasured, so verify everything"
            ),
        )

    # -- Operation 2: dispatch ---------------------------------------------
    def dispatch_worker(self, request: WorkerRequest) -> WorkerResult:
        """Run one headless CLI process in request.workdir and report its outcome."""
        worker_id = f"{self._profile}-{uuid.uuid4().hex[:12]}"
        model = request.model if request.model is not None else self._model
        try:
            validate_headless_options(self._profile, model=model)
        except ValueError as exc:
            return self._result(worker_id, ok=False, error=f"invalid model: {exc}")

        env = self._build_env()
        workdir = request.workdir
        with tempfile.TemporaryDirectory(prefix="headless-cli-") as scratch:
            last_message = os.path.join(scratch, "last-message.txt")
            if self._profile == "codex-exec":
                argv = self._codex_argv(workdir, model, last_message)
            else:
                argv = self._agy_argv(request.prompt, model)
            argv[0] = _resolve_binary(argv[0], env)

            kwargs = {
                "cwd": workdir,
                "env": env,
                "capture_output": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": self._timeout_s,
                "check": False,
            }
            if self._profile == "codex-exec":
                kwargs["input"] = request.prompt

            try:
                completed = subprocess.run(argv, **kwargs)
            except subprocess.TimeoutExpired:
                return self._result(
                    worker_id,
                    ok=False,
                    files_written=_changed_files(workdir, env),
                    error=f"{self._profile} timeout after {self._timeout_s:g}s",
                )
            except FileNotFoundError:
                return self._result(
                    worker_id,
                    ok=False,
                    error=f"{self._profile} binary not found on PATH: {argv[0]}",
                )
            except OSError as exc:
                return self._result(
                    worker_id,
                    ok=False,
                    error=f"failed to launch {self._profile}: {type(exc).__name__}",
                )

            files = _changed_files(workdir, env)
            if completed.returncode != 0:
                tail = (completed.stderr or "").strip()[-_STDERR_TAIL_CHARS:]
                return self._result(
                    worker_id,
                    ok=False,
                    files_written=files,
                    error=(
                        f"{self._profile} exited with code {completed.returncode}"
                        + (f": {tail}" if tail else "")
                    ),
                )

            if self._profile == "codex-exec":
                text = _read_text_if_exists(last_message)
            else:
                text = _parse_agy_text(completed.stdout)
            return self._result(worker_id, ok=True, text=text, files_written=files)

    # -- Operation 3: status -----------------------------------------------
    def worker_status(self, worker_id: str) -> WorkerStatus:
        """Return the recorded outcome of a dispatch, or WORKER_UNKNOWN."""
        return self._statuses.get(
            worker_id, WorkerStatus(worker_id=worker_id, state=WORKER_UNKNOWN)
        )

    # -- Operation 4: orchestrator-side command execution ------------------
    def run_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        shell: Optional[str] = None,
    ) -> CommandResult:
        """Run a command on the orchestrator host (not the headless child)."""
        return run_shell_bounded(command, cwd=cwd, timeout_s=_COMMAND_TIMEOUT_S)

    # -- Operation 5: model selection --------------------------------------
    def resolve_model(self, role: str) -> str:
        """Return the configured model id, or "" to use the CLI's own default.

        `role` is accepted for interface compatibility; this driver has one model.
        """
        return self._model or ""

    # -- helpers -----------------------------------------------------------
    def _build_env(self) -> Dict[str, str]:
        """Build the child environment from the allow-list only."""
        env = {}
        for name in self._env_allowlist:
            value = os.environ.get(name)
            if value is not None:
                env[name] = value
        return env

    def _codex_argv(
        self, workdir: str, model: Optional[str], last_message: str
    ) -> list:
        """Build the codex exec argv. The prompt is supplied on stdin via '-'."""
        argv = [
            "codex",
            "exec",
            "--cd",
            workdir,
            "--sandbox",
            "workspace-write",
            "--output-last-message",
            last_message,
        ]
        if self._provider is not None:
            argv += ["--oss", "--local-provider", self._provider]
        if model is not None:
            argv += ["-m", model]
        argv.append("-")
        return argv

    def _agy_argv(self, prompt: str, model: Optional[str]) -> list:
        """Build the agy -p argv.

        --print-timeout 0 defers the wall-clock bound to this driver's subprocess
        timeout. The unit of a non-zero --print-timeout is unverified.
        """
        argv = [
            "agy",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--print-timeout",
            "0",
            "--mode",
            "accept-edits",
        ]
        if model is not None:
            argv += ["--model", model]
        return argv

    def _result(
        self,
        worker_id: str,
        *,
        ok: bool,
        text: str = "",
        files_written: Tuple[str, ...] = (),
        error: Optional[str] = None,
    ) -> WorkerResult:
        """Record the outcome for worker_status() and return the WorkerResult.

        `verified` is deliberately left at its default None.
        """
        state = WORKER_DONE if ok else WORKER_FAILED
        self._statuses[worker_id] = WorkerStatus(
            worker_id=worker_id, state=state, detail=error or ""
        )
        return WorkerResult(
            worker_id=worker_id,
            status=state,
            ok=ok,
            text=text,
            files_written=tuple(files_written),
            error=error,
        )


def _resolve_binary(name: str, env: Dict[str, str]) -> str:
    """Return the full path of `name` on the child's PATH, else `name` itself.

    Resolving the path lets Windows run npm-style shims (.cmd) through their
    PATHEXT entries. An unresolved name is left for subprocess to report.
    """
    found = shutil.which(name, path=env.get("PATH"))
    return found or name


def _read_text_if_exists(path: str) -> str:
    """Return the file's text, or '' when it was never written."""
    target = Path(path)
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8", errors="replace")
