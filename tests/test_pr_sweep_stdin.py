#!/usr/bin/env python3
"""Regression tests: tools/pr_sweep.py must not inherit the parent's stdin.

The watchdog runs pr_sweep under a hidden, console-less launcher
(daemons/run-hidden.exe, CREATE_NO_WINDOW). In that context the inherited
STD_INPUT_HANDLE is invalid, so subprocess.run() with stdin left as None
raises OSError [WinError 6] inside subprocess._get_handles() before gh
even starts. Every subprocess call in pr_sweep must pass stdin=DEVNULL.

Two checks:
  * a static contract: gh() hands subprocess.run stdin=subprocess.DEVNULL;
  * a Windows behavioural check: a child Python with no console and no std
    handles (DETACHED_PROCESS, stdio unset) calls gh() and must not raise.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "pr_sweep.py"

_CHILD_CODE = r"""
import ctypes, importlib.util, sys
tool_path, out_path = sys.argv[1], sys.argv[2]
# Reproduce the hidden-launcher condition: the process's STD_INPUT_HANDLE is
# INVALID_HANDLE_VALUE (-1). GetStdHandle then raises WinError 6 inside
# subprocess._get_handles() for any subprocess.run() that leaves stdin unset.
kernel32 = ctypes.windll.kernel32
kernel32.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
kernel32.SetStdHandle.restype = ctypes.c_int
if not kernel32.SetStdHandle(0xFFFFFFF6, ctypes.c_void_p(-1)):
    raise SystemExit("could not invalidate stdin handle")
spec = importlib.util.spec_from_file_location("pr_sweep_child", tool_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
try:
    result = mod.gh("auth", "status")
    text = "OK %r" % (result,)
except BaseException as exc:
    text = "EXC %s: %s" % (type(exc).__name__, exc)
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(text)
"""


def load_module():
    spec = importlib.util.spec_from_file_location("pr_sweep_stdin_under_test", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GhStdinContractTest(unittest.TestCase):
    def test_gh_passes_devnull_stdin_to_subprocess_run(self):
        mod = load_module()
        completed = subprocess.CompletedProcess(args=["gh"], returncode=0,
                                                stdout="{}", stderr="")
        with patch.object(mod.subprocess, "run", return_value=completed) as run:
            mod.gh("auth", "status")
        self.assertEqual(run.call_count, 1)
        kwargs = run.call_args.kwargs
        self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL,
                      "gh() must pass stdin=subprocess.DEVNULL; got %r"
                      % (kwargs.get("stdin", "<unset>"),))


@unittest.skipUnless(os.name == "nt", "console-handle behaviour is Windows-only")
class GhConsolelessChildTest(unittest.TestCase):
    def test_gh_does_not_raise_without_console_or_std_handles(self):
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "result.txt")
            proc = subprocess.Popen(
                [sys.executable, "-c", _CHILD_CODE, str(TOOL_PATH), out_path],
                creationflags=detached,
                stdin=None, stdout=None, stderr=None,
            )
            proc.wait(timeout=120)
            with open(out_path, encoding="utf-8") as fh:
                text = fh.read()
        self.assertTrue(text.startswith("OK"),
                        "gh() raised in a console-less child: %s" % text)


if __name__ == "__main__":
    unittest.main()
