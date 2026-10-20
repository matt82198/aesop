"""The automated stats-refresh PR body must end with the Aesop footer.

Behavioural test: it pulls the real "Create or update pull request" step out
of .github/workflows/stats-refresh.yml, runs the body-writing heredoc under
bash with the workflow's own env, and asserts on the body file it wrote.
Nothing is matched against source text.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "stats-refresh.yml"
STEP_NAME = "Create or update pull request"
FOOTER = "\U0001f422 Generated with Aesop"
RUN_URL = "https://github.com/example/aesop/actions/runs/424242"
STATS_BRANCH = "chore/stats-refresh"
# Windows system launchers that shadow Git Bash on PATH; never run the script
# through these (the WSL launcher exits 1 when no distro is installed).
_REJECTED_BASH_DIRS = ("windowsapps", "system32")


def _resolve_bash() -> str:
    """Return a bash that runs scripts non-interactively with stdout captured.

    On POSIX this is plain "bash". On Windows it is Git Bash's own
    ``<git root>/bin/bash.exe``, derived from ``shutil.which("git")``. The
    WindowsApps and System32 launchers are never accepted. ``git-bash.exe``
    is deliberately not used: it is the mintty GUI launcher and does not
    write script output to stdout.
    """
    if os.name != "nt":
        return "bash"
    git = shutil.which("git")
    if git:
        # git.exe is at <root>\\cmd\\git.exe or <root>\\mingw64\\bin\\git.exe,
        # so <root>\\bin\\bash.exe is one of the first three ancestors.
        for ancestor in Path(git).parents[:3]:
            candidate = ancestor / "bin" / "bash.exe"
            if not candidate.is_file():
                continue
            if any(part.lower() in _REJECTED_BASH_DIRS for part in candidate.parts):
                continue
            return str(candidate)
    raise RuntimeError(
        "Git Bash not found: expected <git root>\\bin\\bash.exe derived from "
        f"shutil.which('git')={git!r}. Install Git for Windows or put it on PATH."
    )


def _body_writer_script() -> str:
    """Return the run script up to and including the body heredoc terminator."""
    with WORKFLOW.open(encoding="utf-8") as handle:
        workflow = yaml.safe_load(handle)
    run_text = None
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == STEP_NAME:
                run_text = step["run"]
    if run_text is None:
        raise AssertionError(f"step {STEP_NAME!r} not found in {WORKFLOW.name}")
    lines = run_text.splitlines()
    end = None
    for index, line in enumerate(lines):
        if line.strip() == "EOF":
            end = index
            break
    if end is None:
        raise AssertionError("body heredoc terminator EOF not found")
    return "\n".join(lines[: end + 1]) + "\n"


class StatsRefreshPrFooterTest(unittest.TestCase):
    """The PR body written by stats-refresh.yml ends with the footer line."""

    def test_pr_body_last_line_is_aesop_footer(self):
        script = _body_writer_script()
        with tempfile.TemporaryDirectory(prefix="stats-footer-") as tmp:
            tmp_path = Path(tmp)
            script_path = tmp_path / "write_body.sh"
            script_path.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env.update(
                {
                    "RUNNER_TEMP": tmp_path.as_posix(),
                    "STATS_BRANCH": STATS_BRANCH,
                    "RUN_URL": RUN_URL,
                }
            )
            result = subprocess.run(
                [_resolve_bash(), script_path.as_posix()],
                cwd=tmp,
                env=env,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            body = (tmp_path / "stats-pr-body.md").read_text(encoding="utf-8")

        lines = body.rstrip("\n").split("\n")
        self.assertEqual(lines[-1], FOOTER, body)
        non_empty = [line for line in lines if line.strip()]
        self.assertEqual(non_empty[-2], f"Run: {RUN_URL}", body)


if __name__ == "__main__":
    unittest.main()
