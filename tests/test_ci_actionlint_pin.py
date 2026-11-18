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

The expected digests are GitHub's published sha256 digests for rhysd/actionlint
v1.7.12 release assets (darwin_arm64, darwin_amd64, linux_amd64). The tarball
itself is never downloaded by this test.
"""

import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

ACTIONLINT_VERSION = "1.7.12"
ACTIONLINT_RELEASE_BASE = (
    f"https://github.com/rhysd/actionlint/releases/download/v{ACTIONLINT_VERSION}/"
)
ACTIONLINT_ASSETS = {
    f"actionlint_{ACTIONLINT_VERSION}_darwin_arm64.tar.gz": (
        "aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f"
    ),
    f"actionlint_{ACTIONLINT_VERSION}_darwin_amd64.tar.gz": (
        "5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644"
    ),
    f"actionlint_{ACTIONLINT_VERSION}_linux_amd64.tar.gz": (
        "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
    ),
}
MACOS_MIGRATED_CI_JOBS = (
    "docs-only-gate",
    "windows-sensitive-paths",
    "ci",
    "windows",
    "browser-proofs",
    "state-drift-gate",
    "pr-footprint-gate",
    "pr-symbol-survival",
    "incident-report-gate",
)
WINDOWS_CI_JOBS = ("windows-shard", "ps1-syntax-check")

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

        self.assertIn(
            ACTIONLINT_RELEASE_BASE,
            run,
            "actionlint must download from the exact pinned v1.7.12 release base URL",
        )
        for tarball, digest in ACTIONLINT_ASSETS.items():
            self.assertIn(
                tarball,
                run,
                f"actionlint install must include pinned tarball {tarball}",
            )
            self.assertIn(
                digest,
                run,
                f"actionlint install must pin SHA-256 digest for {tarball}",
            )
        self.assertRegex(
            run,
            r"uname\s+-s",
            "actionlint install must detect OS dynamically via uname -s",
        )
        self.assertRegex(
            run,
            r"uname\s+-m",
            "actionlint install must detect architecture dynamically via uname -m",
        )
        self.assertIn(
            "shasum -a 256 -c -",
            run,
            "the pinned digest must be verified with shasum -a 256 -c - on macOS",
        )
        self.assertIn(
            "sha256sum -c -",
            run,
            "the pinned digest must fall back to sha256sum -c - when shasum is absent",
        )
        # Both digest checks must run before the archive is extracted or installed.
        tar_pos = run.index("tar -xzf")
        self.assertLess(
            run.index("shasum -a 256 -c -"),
            tar_pos,
            "shasum verification must precede tar extraction",
        )
        self.assertLess(
            run.index("sha256sum -c -"),
            tar_pos,
            "sha256sum fallback verification must precede tar extraction",
        )
        self.assertNotIn(
            "download-actionlint.bash",
            run,
            "the unpinned downloader script must not be used",
        )

    def test_primary_ci_shards_run_on_macos_latest(self):
        data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
        jobs = data.get("jobs") or {}
        self.assertEqual(jobs["ci"]["runs-on"], "macos-latest")
        for job_id in MACOS_MIGRATED_CI_JOBS:
            self.assertIn(job_id, jobs, f"expected job {job_id!r} in ci.yml")
            self.assertEqual(
                jobs[job_id]["runs-on"],
                "macos-latest",
                f"expected job {job_id!r} to run on macos-latest",
            )
        for job_id in WINDOWS_CI_JOBS:
            self.assertIn(job_id, jobs, f"expected job {job_id!r} in ci.yml")
            self.assertEqual(
                jobs[job_id]["runs-on"],
                "windows-latest",
                f"expected job {job_id!r} to stay on windows-latest",
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
