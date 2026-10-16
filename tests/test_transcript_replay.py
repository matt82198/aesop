#!/usr/bin/env python3
"""
Tests for tools/transcript_replay.py output-directory safety.

-o <dir> must never delete an existing directory it did not create. An existing
directory is cleared only when it is empty or carries the marker file this tool
wrote on a previous run; otherwise the tool exits 2 naming the path and the marker
and leaves every file in place.

All paths live under temp directories; the tool runs as a subprocess with -I.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "transcript_replay.py"
MARKER = ".transcript-replay-output"
SENTINEL = "sentinel-do-not-delete.txt"


def run_replay(output_dir, transcript_root, repo_dir):
    """Run transcript_replay.py with -o output_dir; return (rc, stdout, stderr)."""
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(TOOL),
            str(transcript_root),
            "-p",
            "proj",
            "-r",
            str(repo_dir),
            "-c",
            "2026-01-01T00:00:00Z",
            "-o",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestOutputDirectorySafety(unittest.TestCase):
    """-o must never delete an unmarked, non-empty directory."""

    def test_unmarked_nonempty_directory_is_refused_and_preserved(self):
        """An unmarked non-empty -o directory exits 2 and its sentinel survives."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcripts = tmp_path / "transcripts"
            transcripts.mkdir()
            repo_dir = tmp_path / "repo"
            repo_dir.mkdir()
            target = tmp_path / "not-ours"
            target.mkdir()
            sentinel = target / SENTINEL
            sentinel.write_text("keep me\n", encoding="utf-8")

            rc, _out, err = run_replay(target, transcripts, repo_dir)

            self.assertEqual(rc, 2, msg=f"stderr={err!r}")
            self.assertTrue(sentinel.is_file(), "sentinel was deleted")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep me\n")
            self.assertIn(str(target), err)
            self.assertIn(MARKER, err)

    def test_empty_directory_is_accepted(self):
        """An existing empty -o directory is accepted and the run succeeds."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcripts = tmp_path / "transcripts"
            transcripts.mkdir()
            repo_dir = tmp_path / "repo"
            repo_dir.mkdir()
            target = tmp_path / "empty-out"
            target.mkdir()

            rc, _out, err = run_replay(target, transcripts, repo_dir)

            self.assertEqual(rc, 0, msg=f"stderr={err!r}")
            self.assertTrue((target / MARKER).is_file())

    def test_marked_directory_from_previous_run_is_reused(self):
        """A directory carrying the tool's marker is cleared and reused on a rerun."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcripts = tmp_path / "transcripts"
            transcripts.mkdir()
            repo_dir = tmp_path / "repo"
            repo_dir.mkdir()
            target = tmp_path / "replay-out"

            rc1, _o1, err1 = run_replay(target, transcripts, repo_dir)
            self.assertEqual(rc1, 0, msg=f"first run stderr={err1!r}")
            self.assertTrue((target / MARKER).is_file())
            self.assertTrue((target / "_REPLAY_REPORT.txt").is_file())

            rc2, _o2, err2 = run_replay(target, transcripts, repo_dir)
            self.assertEqual(rc2, 0, msg=f"second run stderr={err2!r}")
            self.assertTrue((target / MARKER).is_file())
            self.assertTrue((target / "_REPLAY_REPORT.txt").is_file())


if __name__ == "__main__":
    unittest.main()
