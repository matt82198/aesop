#!/usr/bin/env python3
"""Receipt matrix env scrub: push-control / lane-control variables are inputs to the
pre-push HOOK, not to the test parts. A part launched by emit_receipt must not inherit
them (incident 2026-10-15, PR #1008: AESOP_ALLOW_GENERATED=1 leaked into the matrix and
two TestBatchRegeneration tests that assert it is unset went red on a false red receipt)."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import emit_receipt  # noqa: E402


_CHECK_ABSENT = (
    "import os, sys\n"
    "sys.exit(0 if 'AESOP_ALLOW_GENERATED' not in os.environ else 3)\n"
)


class _ProbeSpec:
    """Stands in for a PartSpec: cmd(repo) -> argv, runs a child that inspects its env."""

    def __init__(self, script):
        self._script = script

    def cmd(self, repo):
        return [sys.executable, "-c", self._script]


class TestReceiptMatrixEnvScrub(unittest.TestCase):

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="envscrub-repo-"))
        self.addCleanup(shutil.rmtree, str(self.repo), True)
        self._saved = {}
        for var in ("AESOP_ALLOW_GENERATED", "AESOP_STATE_ROOT"):
            self._saved[var] = os.environ.get(var)

        def restore():
            for var, val in self._saved.items():
                if val is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = val
        self.addCleanup(restore)

    def test_child_part_does_not_inherit_allow_generated(self):
        """Behaviour: the part's child process must NOT see AESOP_ALLOW_GENERATED
        even when the parent (the lane's push) exports it."""
        os.environ["AESOP_ALLOW_GENERATED"] = "1"
        res = emit_receipt.run_part(self.repo, "probe", _ProbeSpec(_CHECK_ABSENT))
        self.assertEqual(res["exit_code"], 0,
                         "child part inherited AESOP_ALLOW_GENERATED from the push environment")

    def test_child_part_does_not_inherit_state_root(self):
        """A lane's state root must never reach a matrix part."""
        os.environ["AESOP_STATE_ROOT"] = str(self.repo / "lane-state")
        script = ("import os, sys\n"
                  "sys.exit(0 if 'AESOP_STATE_ROOT' not in os.environ else 5)\n")
        res = emit_receipt.run_part(self.repo, "probe", _ProbeSpec(script))
        self.assertEqual(res["exit_code"], 0)

    def test_child_part_keeps_path_home_and_python_variables(self):
        """Scrub is a denylist of hook inputs only: PATH/HOME/PYTHON* must survive."""
        os.environ["PATH"] = os.environ.get("PATH", "") or os.defpath
        expected_path = os.environ["PATH"]
        script = ("import os, sys\n"
                  "ok = os.environ.get('PATH') == %r and ('HOME' in os.environ or 'USERPROFILE' in os.environ)\n"
                  "sys.exit(0 if ok else 6)\n") % expected_path
        res = emit_receipt.run_part(self.repo, "probe", _ProbeSpec(script))
        self.assertEqual(res["exit_code"], 0, "PATH (or HOME/USERPROFILE) was scrubbed from the child")

    def test_scrubbed_names_are_reported_without_values(self):
        """The receipt records WHICH variables were removed, never their values."""
        env = {"AESOP_ALLOW_GENERATED": "1", "AESOP_STATE_ROOT": "/secret/lane/root", "PATH": "/bin"}
        child_env, removed = emit_receipt.scrub_child_env(env)
        self.assertIn("AESOP_ALLOW_GENERATED", removed)
        self.assertIn("AESOP_STATE_ROOT", removed)
        self.assertNotIn("AESOP_ALLOW_GENERATED", child_env)
        self.assertEqual(child_env.get("PATH"), "/bin")
        self.assertNotIn("/secret/lane/root", repr(removed))
        self.assertEqual(removed, sorted(removed))


if __name__ == "__main__":
    unittest.main()
