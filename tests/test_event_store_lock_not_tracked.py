"""B1 regression: the append-lock sidecar must never be committed to a store branch.

Fixture: a temp git repo only (never the real home registry or a real store).
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from event_store import cli  # noqa: E402
from event_store.store import LOCK_NAME, Store  # noqa: E402

TS_BASE = "2026-11-14T00:00:%02dZ"


def make_store(root, project="lockcheck"):
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "store.json").write_text(
        json.dumps({"project": project, "schema": 2}), encoding="utf-8")


class LockNotTracked(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "store"
        self.root.mkdir()
        make_store(self.root)
        self._git("init", "-q")
        self._git("config", "user.email", "lock@example.invalid")
        self._git("config", "user.name", "lock")

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args],
                              capture_output=True, encoding="utf-8", errors="replace",
                              check=True).stdout

    def _tracked(self):
        return set(self._git("ls-files").splitlines())

    def _append_and_commit(self, n=1):
        # Same two steps cli.cmd_append performs: Store.append, then cli.git_commit.
        event = Store(self.root).append("note", "tester", "lock-%d" % n, ts=TS_BASE % n)
        return cli.git_commit(self.root, event)

    def test_r1_append_lock_is_not_tracked_after_commit(self):
        self.assertTrue(self._append_and_commit())
        self.assertTrue((self.root / LOCK_NAME).exists(), "precondition: append made the lock")
        self.assertNotIn(LOCK_NAME, self._tracked())

    def test_r2_control_store_json_and_day_file_are_tracked(self):
        self.assertTrue(self._append_and_commit())
        tracked = self._tracked()
        self.assertIn("store.json", tracked)
        day = sorted((self.root / "events").glob("*.jsonl"))
        self.assertEqual(len(day), 1)
        self.assertIn("events/" + day[0].name, tracked)

    def test_r3_already_tracked_lock_stays_tracked_known_limit(self):
        # Known limit: an ignore rule does not untrack a file committed by an earlier
        # version. Pinned here so the behaviour is explicit; not a fix target in this PR.
        (self.root / LOCK_NAME).write_bytes(b"")
        self._git("add", "-f", "--", LOCK_NAME)
        self._git("commit", "-q", "-m", "earlier commit with lock")
        self.assertIn(LOCK_NAME, self._tracked())
        self.assertTrue(self._append_and_commit())
        self.assertIn(LOCK_NAME, self._tracked())

    def test_r4_second_append_after_commit_still_works(self):
        self.assertTrue(self._append_and_commit(1))
        self.assertTrue(self._append_and_commit(2))
        self.assertTrue((self.root / LOCK_NAME).exists())
        self.assertEqual(len(Store(self.root).load()), 2)
        self.assertEqual(Store(self.root).verify(), [])

    def test_r6_ignore_layer_alone_keeps_lock_untracked(self):
        # Layer (b) on its own: a store made by init's file writer, committed with a
        # plain `git add -A` (no pathspec), must not track the lock.
        from event_store import init as init_mod  # noqa: PLC0415
        root = Path(self.tmp.name) / "initstore"
        root.mkdir()
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "lock@example.invalid"],
                       check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "lock"], check=True)
        init_mod._write_store_files(root, "lockcheck")
        Store(root).append("note", "tester", "r6", ts=TS_BASE % 1)
        self.assertTrue((root / LOCK_NAME).exists())
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "r6"], check=True)
        tracked = set(subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True,
                                     encoding="utf-8", check=True).stdout.splitlines())
        self.assertNotIn(LOCK_NAME, tracked)
        self.assertIn("store.json", tracked)

    def test_r5_nothing_to_commit_keeps_existing_return_value(self):
        # Pins the pre-existing empty-commit behaviour: with nothing new on disk,
        # git_commit makes no commit and returns False (git commit rc=1). The fix must
        # not change this.
        self.assertTrue(self._append_and_commit(1))
        head_before = self._git("rev-parse", "HEAD").strip()
        result = cli.git_commit(self.root, {"seq": 99, "kind": "note"})
        self.assertEqual(self._git("rev-parse", "HEAD").strip(), head_before,
                         "nothing-to-commit must not create a commit")
        self.assertIs(result, False)


if __name__ == "__main__":
    unittest.main()
