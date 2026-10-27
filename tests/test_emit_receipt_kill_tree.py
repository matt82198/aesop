"""Red-first gap tests for tools/emit_receipt.py::_kill_process_tree.

Gap: a descendant that is no longer reachable from the part's process tree at
kill time survives the kill. On Windows, taskkill /T walks parent links, so a
grandchild whose parent already exited (an orphan) is not reached. On POSIX,
killpg reaches only the process group, so a descendant that called setsid
(start_new_session=True) survives.
"""
import ctypes
import os
import signal
import subprocess
import sys
import time
import unittest
from ctypes import wintypes
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import emit_receipt  # noqa: E402

SLEEP_CODE = "import time; time.sleep(60)"

# Part spawns a grandchild, prints its pid, and exits. The grandchild is an orphan.
ORPHAN_PARENT_CODE = (
    "import subprocess, sys\n"
    "g = subprocess.Popen([sys.executable, '-c', %r],"
    " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "print(g.pid, flush=True)\n"
) % SLEEP_CODE

# Part spawns a grandchild in a new session, prints its pid, and keeps sleeping.
NEW_SESSION_PARENT_CODE = (
    "import subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', %r],"
    " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)\n"
    "print(g.pid, flush=True)\n"
    "time.sleep(60)\n"
) % SLEEP_CODE


def _pid_alive(pid):
    if os.name == "nt":
        synchronize = 0x00100000
        query_limited = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(synchronize | query_limited, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_gone(pid, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


def _force_kill(pid):
    try:
        os.kill(pid, signal.SIGTERM)  # on Windows os.kill terminates the process
    except OSError:
        pass


class KillProcessTreeGapTests(unittest.TestCase):

    def setUp(self):
        self.part = None
        self.grandchild_pid = None

    def tearDown(self):
        if self.grandchild_pid is not None and _pid_alive(self.grandchild_pid):
            _force_kill(self.grandchild_pid)
        if self.part is not None:
            if self.part.poll() is None:
                self.part.kill()
                self.part.wait(timeout=5)
            if self.part.stdout is not None:
                self.part.stdout.close()

    def _start_part(self, code, **popen_kwargs):
        self.part = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            **popen_kwargs
        )
        self.grandchild_pid = int(self.part.stdout.readline().strip())
        return self.part

    @unittest.skipUnless(os.name == "nt", "Windows keeps a dead parent's pid as the orphan's ppid; POSIX reparents orphans to init")
    def test_orphaned_grandchild_is_killed_windows_tree(self):
        part = self._start_part(ORPHAN_PARENT_CODE)
        part.wait(timeout=10)  # the part exits; the grandchild is now an orphan
        self.assertTrue(_pid_alive(self.grandchild_pid),
                        "precondition: orphaned grandchild %d must be alive before the kill"
                        % self.grandchild_pid)

        emit_receipt._kill_process_tree(part)

        self.assertTrue(_wait_gone(self.grandchild_pid, 5.0),
                        "orphaned grandchild %d survived _kill_process_tree"
                        % self.grandchild_pid)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group gap; skipped on Windows")
    def test_new_session_descendant_is_killed_posix(self):
        part = self._start_part(NEW_SESSION_PARENT_CODE, start_new_session=True)
        self.assertTrue(_pid_alive(self.grandchild_pid),
                        "precondition: new-session grandchild %d must be alive before the kill"
                        % self.grandchild_pid)

        emit_receipt._kill_process_tree(part)
        part.wait(timeout=5)

        self.assertTrue(_wait_gone(self.grandchild_pid, 5.0),
                        "new-session grandchild %d survived _kill_process_tree (killpg missed it)"
                        % self.grandchild_pid)


if __name__ == "__main__":
    unittest.main()
