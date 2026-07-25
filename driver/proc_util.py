#!/usr/bin/env python3
"""proc_util -- bounded shell execution shared by the concrete AgentDriver adapters.

run_shell_bounded() backs run_command for ClaudeCodeDriver and CodexDriver
(OpenAICompatibleDriver inherits it). It exists because plain
``subprocess.run(command, shell=True, timeout=N)`` does NOT bound wall-clock on
Windows: on timeout CPython kills only the shell (cmd.exe), then its cleanup
``communicate()`` re-blocks until the orphaned grandchild -- the actual
test/git process -- closes the inherited pipe handles. A deadlocked grandchild
therefore stalled the caller indefinitely: the exact wave-hang class the
timeout was meant to prevent (RS-A F1; live-proven: timeout_s=0.5 vs a 6s
sleep returned exit 124 only after 6.1s).

Guarantees:
  * The timeout truly bounds wall-clock (plus a small fixed drain allowance).
  * The whole process TREE is killed on timeout: the child is spawned in its
    own group/session, then ``taskkill /T /F`` on Windows (kills grandchildren
    by parent-child walk) or ``os.killpg(SIGKILL)`` on POSIX.
  * Exit code 124 on timeout (shell convention); 127 when spawn fails.
  * Partial stdout/stderr captured before the kill is preserved (RS-A F7), so
    a timed-out 119s suite still yields its printed diagnostics instead of a
    blind rerun.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import os
import signal
import subprocess

from agent_driver import CommandResult

# Post-kill drain bound. With the tree dead the pipes close and communicate()
# returns immediately; this cap only protects against a survivor that
# inherited the handles (e.g. a detached daemon) re-introducing the hang.
_DRAIN_TIMEOUT_S = 5.0

_TIMEOUT_NOTE = "Command timed out after {t}s; process tree killed (exit 124)"


def kill_process_tree(proc):
    """Best-effort, bounded kill of proc and every descendant."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=_DRAIN_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    # Belt and braces: direct child, idempotent if already dead.
    try:
        proc.kill()
    except OSError:
        pass


def _as_text(value):
    """Normalize partial-output payloads (None/bytes/str) to str."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_shell_bounded(command, cwd=None, timeout_s=120.0):
    """Run `command` through the platform shell, hard-bounded by timeout_s.

    Returns CommandResult. On timeout: exit 124, partial output preserved,
    stderr carries a "Command timed out" note. On spawn failure: exit 127.
    """
    popen_kwargs = {}
    if os.name == "nt":
        # Own process group so the child tree is a coherent kill target.
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # Own session -> killpg reaches every descendant.
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **popen_kwargs,
        )
    except OSError as exc:
        return CommandResult(exit_code=127, stdout="", stderr=str(exc))

    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        return CommandResult(
            exit_code=proc.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        # POSIX populates partial output on the exception; Windows does not
        # (its reader threads still hold it). Keep the exception's copy as a
        # fallback, but prefer the post-kill drain below (complete on both).
        partial_out = _as_text(exc.stdout)
        partial_err = _as_text(exc.stderr)

        # Kill the WHOLE tree first -- never re-block on live grandchildren.
        kill_process_tree(proc)

        # Drain what the pipe readers captured. Tree dead -> pipes closed ->
        # this returns promptly; still bounded so a handle-holding survivor
        # cannot re-introduce the unbounded hang.
        try:
            drained_out, drained_err = proc.communicate(timeout=_DRAIN_TIMEOUT_S)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            drained_out, drained_err = None, None

        out = _as_text(drained_out) or partial_out
        err = _as_text(drained_err) or partial_err
        note = _TIMEOUT_NOTE.format(t=timeout_s)
        return CommandResult(
            exit_code=124,
            stdout=out,
            stderr=(err + "\n" + note) if err else note,
        )
