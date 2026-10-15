#!/usr/bin/env python3
"""
Red-first tests for the native Linux-shape leg of linux_shape_check.py.

INDEX: Test suite proving the native (Linux host) leg runs the Node and Shell suites
through tools/test_isolation_tripwire.py, the same command shape as the WSL leg and
package.json (test:node / test:sh), and that the two legs cannot drift apart.

Why: the tripwire is the independent proof that a test run did not touch the real
developer profile. Without it the native leg's green is weaker than CI's.

Test cases:
  - native Node command is wrapped by the tripwire, args identical to the WSL leg
  - native Shell command is wrapped by the tripwire
  - native and WSL command lines differ only in the host prefix (and interpreter token)
  - a tripwire failure (profile drift, exit 1) fails the native leg (fail closed)
"""

import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import linux_shape_check as lsc

NODE_FILES = ["tests/x.test.mjs"]
WSL_PATH = "/mnt/c/fake/aesop"
WSL_PREFIX = f"cd '{WSL_PATH}' && unset USERPROFILE && "


def _completed(rc=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def _captured_call(fn, *args):
    """Run fn with subprocess.run mocked; return the argv it was asked to execute."""
    with mock.patch("linux_shape_check.subprocess.run", return_value=_completed()) as run:
        fn(*args)
    return run.call_args.args[0]


class TestNativeLegUsesTripwire(unittest.TestCase):

    def test_native_node_command_is_tripwire_wrapped(self):
        argv = _captured_call(lsc.run_node_tests_native, Path("."), NODE_FILES)
        self.assertIn("tools/test_isolation_tripwire.py", argv)
        self.assertEqual(argv[argv.index("tools/test_isolation_tripwire.py") + 1], "--")
        self.assertEqual(
            argv[argv.index("--") + 1:],
            ["node", "--import", "./tests/helpers/isolated-env.mjs", "--test", *NODE_FILES],
        )

    def test_native_shell_command_is_tripwire_wrapped(self):
        argv = _captured_call(lsc.run_shell_tests_native, Path("."))
        self.assertIn("tools/test_isolation_tripwire.py", argv)
        self.assertEqual(argv[argv.index("--") + 1:], ["bash", "tools/run_shell_tests.sh"])


class TestNativeAndWslShareOneCommandShape(unittest.TestCase):

    def test_node_native_and_wsl_differ_only_in_host_prefix(self):
        native = _captured_call(lsc.run_node_tests_native, Path("."), NODE_FILES)
        wsl_argv = _captured_call(lsc.run_node_tests_wsl, Path("."), WSL_PATH, NODE_FILES)
        self.assertEqual(wsl_argv[:3], ["wsl.exe", "bash", "-lc"])
        inner = wsl_argv[3]
        self.assertTrue(inner.startswith(WSL_PREFIX), inner)
        # Strip the WSL host prefix; the rest must be the same argv the native leg built,
        # apart from the interpreter token (sys.executable on the host, python inside WSL).
        wsl_suite = shlex.split(inner[len(WSL_PREFIX):])
        self.assertIn("tools/test_isolation_tripwire.py", wsl_suite)
        self.assertEqual(wsl_suite[1:], native[1:])

    def test_shell_native_and_wsl_differ_only_in_host_prefix(self):
        native = _captured_call(lsc.run_shell_tests_native, Path("."))
        wsl_argv = _captured_call(lsc.run_shell_tests_wsl, Path("."), WSL_PATH)
        inner = wsl_argv[3]
        self.assertTrue(inner.startswith(f"cd '{WSL_PATH}' && "), inner)
        wsl_suite = shlex.split(inner[len(f"cd '{WSL_PATH}' && "):])
        self.assertIn("tools/test_isolation_tripwire.py", wsl_suite)
        self.assertEqual(wsl_suite[1:], native[1:])


class TestNativeLegFailsClosed(unittest.TestCase):

    def test_tripwire_profile_drift_fails_native_leg(self):
        drift = _completed(
            rc=1,
            stderr=b"TRIPWIRE: real profile changed: /home/u/.claude/settings.json\n",
        )
        with mock.patch("linux_shape_check.subprocess.run", return_value=drift), \
                mock.patch("builtins.print"):
            rc = lsc.run_native_leg(Path("."), NODE_FILES)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
