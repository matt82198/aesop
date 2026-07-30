# Cross-Platform Process-Tree Kill

*The timeout that didn't actually bound wall-clock -- and the orphaned grandchildren it left behind.*

## Context

Aesop orchestrates fleets of coding agents. Each agent runs shell commands --
test suites, git operations, builds -- as subprocesses. When an agent times
out (the default is 120 seconds), the orchestrator needs to kill it and move
on. If it doesn't, a single hung test suite can stall an entire wave of
parallel work. The system must run identically on Windows (local development)
and Linux (CI), which is where the trouble starts.

The straightforward implementation looks like this:

```python
result = subprocess.run(command, shell=True, timeout=120)
```

Python's `subprocess.run` with `timeout=N` calls `communicate(timeout=N)`
under the hood. When the timeout fires, it raises `TimeoutExpired`. You catch
it, kill the process, move on. Clean, simple, and subtly wrong.

## The Bug

The timeout in `subprocess.communicate()` bounds the **wait time**, not the
**run time**. On POSIX this distinction rarely matters because `communicate()`
blocks on pipe reads until the child's stdout/stderr handles close, and those
handles close when the process (and its children) exit. But on Windows, the
implementation is different. When you call `proc.kill()` after a timeout, it
kills the direct child -- `cmd.exe` -- but not the child's children.

Here's the scenario that broke us:

1. The orchestrator spawns `cmd.exe /c "python -m unittest tests.test_big_suite"`.
2. `cmd.exe` is the child. The Python test runner is the grandchild.
3. After 120s, `TimeoutExpired` fires. The orchestrator calls `proc.kill()`.
4. `cmd.exe` dies. But the Python test runner -- the grandchild -- is still alive.
5. The grandchild inherited the pipe handles from `cmd.exe`.
6. `proc.communicate()` (called to drain remaining output) blocks on those handles.
7. The pipe won't close until the grandchild exits.
8. The grandchild is running a 10-minute test suite.

Result: the 120-second timeout actually took 600+ seconds. The timeout
mechanism was present, correct in its contract ("I waited 120s"), and
completely ineffective at its purpose ("bound wall-clock for this operation").

This was proven live: a `timeout_s=0.5` against a 6-second sleep returned
exit 124 only after 6.1 seconds. The timeout fired at 0.5s, killed the shell,
then `communicate()` re-blocked for the remaining 5.6s waiting for the orphan
to release the pipe handles.

## Discovery

Wave hangs. Agents that should have timed out and been relaunched instead sat
silently, doing nothing visible, while the wave clock ticked. The watchdog
(heartbeat-based stall detection) flagged them as stalled, but the stall was
in the orchestrator's own subprocess management, not in the agent itself.
Forensics on the hung process revealed the orphaned grandchild pattern.

## The Fix

The fix lives in `driver/proc_util.py` and has three parts:

**1. Spawn in a process group.** The child must be in its own group/session
so we can kill the entire tree, not just the direct child.

```python
if os.name == "nt":
    popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
else:
    popen_kwargs["start_new_session"] = True
```

**2. Kill the tree, not just the child.** On timeout, kill every process in
the group before attempting to drain output.

```python
def kill_process_tree(proc):
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True, timeout=5.0,
        )
    else:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    # Belt-and-braces: direct child, idempotent if already dead
    proc.kill()
```

On Windows, `taskkill /T /F /PID` walks the parent-child tree and forcefully
kills every descendant. On POSIX, `os.killpg()` sends `SIGKILL` to every
process in the group created by `start_new_session=True`. Both approaches
reach the grandchildren that `proc.kill()` alone misses.

**3. Bounded drain with preserved partial output.** After the tree kill,
drain whatever the pipe readers captured, but cap the drain with its own
timeout so a handle-holding survivor (e.g., a detached daemon that inherited
the pipe) cannot reintroduce the unbounded hang:

```python
except subprocess.TimeoutExpired as exc:
    partial_out = exc.stdout  # POSIX populates this; Windows does not
    kill_process_tree(proc)
    try:
        drained_out, drained_err = proc.communicate(timeout=5.0)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        drained_out, drained_err = None, None
    # Merge: prefer post-kill drain, fall back to pre-kill partial
    out = drained_out or partial_out
```

This preserves diagnostics from a timed-out 119-second test suite (the first
119 seconds of output are still useful) while guaranteeing the orchestrator
reclaims control within `timeout_s + 5` seconds.

The exit code convention follows `coreutils timeout`: exit 124 on timeout,
127 on spawn failure.

## Design Lesson

Cross-platform subprocess management is one of those domains where the
abstraction (Python's `subprocess` module) hides critical differences between
the underlying OS primitives. `timeout=N` means something subtly different on
Windows vs. POSIX, and `proc.kill()` has different blast radii on each. The
only way to build reliable timeouts is to control the process group from spawn
time and kill the group explicitly on timeout.

The deeper insight is about what "timeout" means. There are two contracts a
timeout can offer: "I will stop *waiting* after N seconds" (which is what
`communicate(timeout=N)` provides) and "this operation will complete within N
seconds" (which is what the caller actually needs). The gap between those
contracts is exactly where the bug lives.

> **Design Principle**
>
> A timeout must bound **wall-clock from the caller's perspective**, not just
> the wait-time of a single syscall.
>
> To kill a subprocess reliably:
> 1. Spawn it in its own process group (`CREATE_NEW_PROCESS_GROUP` /
>    `start_new_session=True`).
> 2. On timeout, kill the **group** (`taskkill /T` / `os.killpg`), not just
>    the direct child.
> 3. Cap the post-kill output drain with its own timeout -- dead processes
>    should release handles immediately, but a survivor must not
>    reintroduce the hang.
> 4. Preserve partial output: a timed-out process's stdout is diagnostic
>    gold. Don't discard it.
