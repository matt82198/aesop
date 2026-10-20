"""
Tracker 768c26047468 -- the CI actionlint install must be pinned and checksum-verified.

ci.yml previously installed actionlint with
    bash <(curl -fsSL .../download-actionlint.bash) 1.7.12 ...
which executes a remotely fetched script with no integrity check.

These tests parse .github/workflows/ci.yml and assert, over the parsed step
`run:` bodies:
  1. no step pipes a downloaded payload into a shell or runs one via process
     substitution (curl|bash, wget|sh, bash <(curl ...), ...);
  2. the actionlint install step fetches a pinned release tarball by an exact
     version URL and verifies its sha256 before extracting or installing it;
  3. the actionlint install step and the CI workflow linter gate that requires
     actionlint still exist (the step was hardened, not removed).

The expected digest is GitHub's published sha256 for
actionlint_1.7.12_linux_amd64.tar.gz (release asset digest, rhysd/actionlint
v1.7.12). The tarball itself is never downloaded by this test.
"""

import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

ACTIONLINT_VERSION = "1.7.12"
ACTIONLINT_TARBALL = f"actionlint_{ACTIONLINT_VERSION}_linux_amd64.tar.gz"
ACTIONLINT_URL = (
    f"https://github.com/rhysd/actionlint/releases/download/"
    f"v{ACTIONLINT_VERSION}/{ACTIONLINT_TARBALL}"
)
ACTIONLINT_SHA256 = "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"

# A network fetch whose output reaches a shell interpreter on the same pipeline,
# or a shell interpreter fed by process substitution from a fetch.
PIPE_TO_SHELL = re.compile(
    r"(curl|wget)\b[^\n|;]*\|\s*(sudo\s+)?(env\s+)?(ba|da|z|k)?sh\b"
)
PROCESS_SUBST_TO_SHELL = re.compile(
    r"\b(ba|da|z|k)?sh\s+<\(\s*(curl|wget)\b"
)


def _load_steps():
    """Yield (job_name, step_index, step_dict) for every step in ci.yml."""
    data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    for job_name, job in (data.get("jobs") or {}).items():
        for idx, step in enumerate(job.get("steps") or []):
            if isinstance(step, dict):
                yield job_name, idx, step


def _run_bodies():
    """Yield (label, run_text) for every step that carries a shell body."""
    for job_name, idx, step in _load_steps():
        run = step.get("run")
        if isinstance(run, str):
            label = f"{job_name}[{idx}] {step.get('name', '<unnamed>')}"
            yield label, run


def _actionlint_install_steps():
    """Steps that install actionlint (identified by the -version probe they run)."""
    found = []
    for job_name, idx, step in _load_steps():
        run = step.get("run")
        if isinstance(run, str) and "actionlint" in run and "GITHUB_PATH" in run:
            found.append((job_name, idx, step))
    return found


class CiActionlintPinTest(unittest.TestCase):

    def test_no_workflow_step_pipes_download_into_shell(self):
        offenders = []
        for label, run in _run_bodies():
            if PIPE_TO_SHELL.search(run) or PROCESS_SUBST_TO_SHELL.search(run):
                offenders.append(label)
        self.assertEqual(
            offenders,
            [],
            "these steps execute a downloaded payload through a shell: "
            + ", ".join(offenders),
        )

    def test_actionlint_install_is_pinned_release_with_sha256_check(self):
        steps = _actionlint_install_steps()
        self.assertEqual(len(steps), 1, "expected exactly one actionlint install step")
        _job, _idx, step = steps[0]
        run = step["run"]

        self.assertIn(ACTIONLINT_URL, run, "actionlint must download the exact pinned release URL")
        self.assertIn(
            f"{ACTIONLINT_SHA256}  ",
            run,
            "actionlint tarball sha256 must be verified against the pinned digest",
        )
        self.assertRegex(
            run,
            r"sha256sum\s+-c\b",
            "the pinned digest must be checked with sha256sum -c",
        )
        # The digest check must run before the archive is extracted or installed.
        self.assertLess(
            run.index("sha256sum -c"),
            run.index("tar "),
            "sha256 verification must precede extraction",
        )
        self.assertNotIn(
            "download-actionlint.bash",
            run,
            "the unpinned downloader script must not be used",
        )

    def test_actionlint_install_and_required_linter_gate_still_present(self):
        self.assertEqual(len(_actionlint_install_steps()), 1)
        data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
        gate_found = False
        for _job, _idx, step in _load_steps():
            env = step.get("env") or {}
            if str(env.get("CI_WORKFLOW_LINT_REQUIRE_ACTIONLINT")) == "1":
                gate_found = True
        self.assertTrue(
            gate_found,
            "the CI workflow linter gate must still require actionlint (fail-closed)",
        )
        self.assertTrue(data.get("jobs"))


if __name__ == "__main__":
    unittest.main()
