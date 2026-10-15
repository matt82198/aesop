"""Fail-closed claim reads in tools/multi_dispatch (sibling of PR #990).

get_all_claimed_files() returns {} on a read/projection error, so an unreadable
or corrupt claims store used to read as "no conflict" and a second lane could be
granted a file another lane holds. check_conflict must use the strict reader:
a ClaimStateUnknown propagates (naming the store path), and main() refuses the
dispatch with exit 1 instead of proceeding.

Red-first: against the lenient reader, the unreadable-store case returns None.
"""

import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from state_store import StateAPI  # noqa: E402
from state_store.instance_projection import ClaimStateUnknown, claim_files  # noqa: E402
from tools import multi_dispatch  # noqa: E402


class _UnreadableStore:
    """A claims store whose instances stream cannot be read."""

    def __init__(self, db_path):
        self.db_path = db_path

    def get(self, stream):
        raise sqlite3.DatabaseError("database disk image is malformed")


class _CorruptEventStore:
    """A claims store whose instances stream returns a malformed event."""

    def __init__(self, db_path):
        self.db_path = db_path

    def get(self, stream):
        return ["not-an-event-mapping"]


class TestCheckConflictFailClosed(unittest.TestCase):

    def test_unreadable_store_refuses_with_unknown_state_and_path(self):
        """Unreadable claims store must NOT read as 'no conflict'."""
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "state.db")
            store = _UnreadableStore(db)
            err = io.StringIO()
            with redirect_stderr(err):
                with self.assertRaises(ClaimStateUnknown) as ctx:
                    multi_dispatch.check_conflict(store, ["a.py"], "lane-b")
            self.assertIn(db, str(ctx.exception))
            self.assertIn("unreadable", str(ctx.exception))

    def test_corrupt_event_refuses(self):
        """A corrupt event in the instances stream (projection fails) must also refuse."""
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "state.db")
            store = _CorruptEventStore(db)
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(ClaimStateUnknown):
                    multi_dispatch.check_conflict(store, ["a.py"], "lane-b")

    def test_readable_store_no_claims_allows(self):
        """Control: readable store, no claims -> no conflict."""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateAPI(str(Path(tmp) / "state.db"))
            try:
                self.assertIsNone(multi_dispatch.check_conflict(store, ["a.py"], "lane-b"))
            finally:
                store.close()

    def test_readable_store_held_file_conflicts(self):
        """Control: readable store, another lane holds a.py -> that lane is returned."""
        with tempfile.TemporaryDirectory() as tmp:
            store = StateAPI(str(Path(tmp) / "state.db"))
            try:
                self.assertTrue(claim_files(store, "lane-a", ["a.py"]))
                self.assertEqual(
                    multi_dispatch.check_conflict(store, ["a.py"], "lane-b"), "lane-a"
                )
            finally:
                store.close()


class TestMainRefusesUnknownClaimState(unittest.TestCase):

    def test_main_exits_1_with_unknown_state_reason(self):
        """main() must refuse the dispatch (exit 1), never run the command."""
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "state.db")
            store = _UnreadableStore(db)
            argv = ["multi_dispatch", "--db", db, "--files", "a.py",
                    "--instance-id", "lane-b", "--", "echo", "hi"]
            err = io.StringIO()
            with mock.patch.object(multi_dispatch, "StateAPI", return_value=store), \
                    mock.patch.object(multi_dispatch.subprocess, "run") as run, \
                    mock.patch.object(sys, "argv", argv), \
                    redirect_stderr(err):
                rc = multi_dispatch.main()
            self.assertEqual(rc, 1)
            run.assert_not_called()
            self.assertIn("claim state unknown", err.getvalue())
            self.assertIn(db, err.getvalue())


if __name__ == "__main__":
    unittest.main()
