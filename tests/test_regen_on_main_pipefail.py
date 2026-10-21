#!/usr/bin/env python3
"""Regression: the regen step in .github/workflows/regen-on-main.yml must go red when
`tools/regen_all.py --fix` fails.

Defect: the step runs `python tools/regen_all.py --fix --json | tee "$RUNNER_TEMP/..."`.
Without `set -o pipefail` the pipeline's status is tee's (0), so a failing regenerator is
swallowed and the regen-on-main job stays green while registered artifacts drift.

Behavioural, not textual: the step's run script is loaded from the workflow YAML and
executed under `bash -e` (the GitHub Actions default for `run` on ubuntu-latest, which
does not enable pipefail) inside a throwaway fixture whose tools/regen_all.py is a stub
that prints a JSON result and exits 1. The bare `python` token is rewritten to the
absolute interpreter path so no PATH shim is needed. A control case with a passing stub
proves the harness itself exits 0 when the regenerator succeeds.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "regen-on-main.yml"
REGEN_STEP_NAME = "Run every registered regenerator (tools/regen_all.py --fix)"

STUB_FAIL = (
    "import json, sys\n"
    "print(json.dumps({'exit_code': 1, 'fixed': []}))\n"
    "sys.exit(1)\n"
)
STUB_PASS = (
    "import json, sys\n"
    "print(json.dumps({'exit_code': 0, 'fixed': []}))\n"
    "sys.exit(0)\n"
)


def _find_bash():
    """Return a bash that accepts Windows paths (Git Bash) or the POSIX bash on PATH."""
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        shutil.which("bash"),
    ]
    for cand in candidates:
        if cand and os.path.exists(cand):
            return cand
    return None


def _regen_step_script():
    """Load the regen step's run script from the real workflow YAML."""
    with open(WORKFLOW, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    for step in doc["jobs"]["regen-on-main"]["steps"]:
        if step.get("name") == REGEN_STEP_NAME:
            return step["run"]
    raise AssertionError(f"step {REGEN_STEP_NAME!r} not found in {WORKFLOW}")


class TestRegenOnMainPipefail(unittest.TestCase):
    """The regen step must propagate a failing regen_all through its pipe to the job."""

    @classmethod
    def setUpClass(cls):
        cls.bash = _find_bash()
        if cls.bash is None:
            raise unittest.SkipTest("no bash available to execute the workflow step")
        cls.script = _regen_step_script()

    def _run_step(self, stub_source):
        tmp = Path(tempfile.mkdtemp(prefix="regen-pipefail-"))
        try:
            fixture = tmp / "repo"
            (fixture / "tools").mkdir(parents=True)
            (fixture / "tools" / "regen_all.py").write_text(stub_source, encoding="utf-8")
            runner_temp = tmp / "runner"
            runner_temp.mkdir()
            github_output = tmp / "github_output.txt"
            github_output.write_text("", encoding="utf-8")

            interpreter = Path(sys.executable).as_posix()
            script = self.script.replace("python ", f'"{interpreter}" ')
            script_file = tmp / "step.sh"
            script_file.write_text(script, encoding="utf-8")

            env = dict(os.environ)
            env["RUNNER_TEMP"] = runner_temp.as_posix()
            env["GITHUB_OUTPUT"] = github_output.as_posix()
            result = subprocess.run(
                [self.bash, "-e", script_file.as_posix()],
                cwd=fixture,
                env=env,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            outputs = github_output.read_text(encoding="utf-8")
            return result.returncode, outputs, result.stdout + result.stderr
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_failing_regen_all_turns_the_step_red(self):
        code, _outputs, log = self._run_step(STUB_FAIL)
        self.assertNotEqual(code, 0, f"regen step stayed green on a failing regen_all:\n{log}")

    def test_passing_regen_all_keeps_the_step_green(self):
        # Control: the harness must exit 0 when the regenerator succeeds, so the red
        # case above is caused by the failure and not by the harness itself.
        code, outputs, log = self._run_step(STUB_PASS)
        self.assertEqual(code, 0, log)
        self.assertIn("changed=false", outputs)


if __name__ == "__main__":
    unittest.main()
