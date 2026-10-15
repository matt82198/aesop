"""F-10: `aesop init` must git-ignore state/ runtime files.

Behavioural contract: after init in a fresh git repo, a runtime event store
written under state/ (state/tracker_events.db, state/tracker.json) is NOT
staged by `git add -A`, while state/.gitkeep stays trackable.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from init_project import init_project  # noqa: E402


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


class TestInitGitignoresState(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name) / "demo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        init_project(str(self.repo), project_name="demo")

    def tearDown(self):
        self._td.cleanup()

    def _write_runtime_state(self):
        (self.repo / "state" / "tracker_events.db").write_bytes(b"SQLite format 3\x00")
        (self.repo / "state" / "tracker.json").write_text("{}\n", encoding="utf-8")

    def test_runtime_state_files_are_ignored(self):
        self._write_runtime_state()
        check = _git(self.repo, "check-ignore", "-q", "state/tracker_events.db")
        self.assertEqual(check.returncode, 0, "state/tracker_events.db must be git-ignored")
        check = _git(self.repo, "check-ignore", "-q", "state/tracker.json")
        self.assertEqual(check.returncode, 0, "state/tracker.json must be git-ignored")

    def test_git_add_all_stages_no_state_runtime_files(self):
        self._write_runtime_state()
        _git(self.repo, "add", "-A")
        staged = _git(self.repo, "diff", "--cached", "--name-only").stdout.splitlines()
        state_staged = [p for p in staged if p.startswith("state/") and p != "state/.gitkeep"]
        self.assertEqual(state_staged, [], f"git add -A staged runtime state: {state_staged}")

    def test_gitkeep_stays_trackable(self):
        check = _git(self.repo, "check-ignore", "-q", "state/.gitkeep")
        self.assertNotEqual(check.returncode, 0, "state/.gitkeep must NOT be ignored")

    def test_rerun_init_is_idempotent(self):
        init_project(str(self.repo), project_name="demo", force=True)
        text = (self.repo / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(text.count("aesop:state-ignore-begin"), 1)


if __name__ == "__main__":
    unittest.main()
