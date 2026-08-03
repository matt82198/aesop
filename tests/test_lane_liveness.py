"""
Test suite for tools/lane_liveness.py -- runtime lane-liveness enforcement.

Covers: fresh lane passes, stalled lane is named, unreadable input exits 2,
transcript evidence joins by agent id, missing lane path is stalled, and the
CLI contract (exit 0 clean / 1 stalled / 2 unreadable) end to end.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import lane_liveness

TOOL = str(Path(__file__).parent.parent / "tools" / "lane_liveness.py")


def _touch(path, age_seconds, now=None):
    """Create a file with an mtime `age_seconds` in the past."""
    now = time.time() if now is None else now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    stamp = now - age_seconds
    os.utime(path, (stamp, stamp))
    # Directory mtimes are set by the write; pin them too so the walk cannot
    # be rescued by an incidentally-fresh parent directory.
    os.utime(path.parent, (stamp, stamp))
    return stamp


def _lane(name, path, branch="feature/x", agent_id=None):
    entry = {"name": name, "path": str(path), "branch": branch}
    if agent_id:
        entry["agent_id"] = agent_id
    return entry


class TestNewestMtime(unittest.TestCase):
    def test_missing_path_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            gone = Path(tmp) / "not-there"
            self.assertIsNone(lane_liveness.newest_mtime(gone))

    def test_returns_newest_of_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "lane"
            now = time.time()
            _touch(root / "old.txt", 5000, now)
            _touch(root / "sub" / "new.txt", 10, now)
            newest = lane_liveness.newest_mtime(root)
            self.assertIsNotNone(newest)
            self.assertLess(now - newest, 60)

    def test_skips_noise_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "lane"
            now = time.time()
            _touch(root / "src.py", 5000, now)
            # Fresh churn inside .git / __pycache__ must NOT count as lane work.
            _touch(root / ".git" / "index", 1, now)
            _touch(root / "__pycache__" / "x.pyc", 1, now)
            newest = lane_liveness.newest_mtime(root)
            self.assertGreater(now - newest, 4000)


class TestCheckLanes(unittest.TestCase):
    def test_fresh_lane_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            root = Path(tmp) / "fresh-lane"
            _touch(root / "impl.py", 30, now)
            report = lane_liveness.check_lanes(
                [_lane("fresh-lane", root)], max_silence=900,
                transcript_index={}, now=now)
            self.assertEqual(report["stalled"], [])
            self.assertEqual(report["unreadable"], [])
            self.assertEqual(report["exit_code"], 0)
            self.assertEqual(report["lanes"][0]["verdict"], "live")
            self.assertEqual(report["lanes"][0]["evidence"], "worktree")

    def test_stalled_lane_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            fresh = Path(tmp) / "fresh-lane"
            stale = Path(tmp) / "silent-lane"
            _touch(fresh / "impl.py", 30, now)
            _touch(stale / "impl.py", 4000, now)
            report = lane_liveness.check_lanes(
                [_lane("fresh-lane", fresh), _lane("silent-lane", stale)],
                max_silence=900, transcript_index={}, now=now)
            self.assertEqual(report["stalled"], ["silent-lane"])
            self.assertEqual(report["exit_code"], 1)
            verdicts = {l["name"]: l["verdict"] for l in report["lanes"]}
            self.assertEqual(verdicts["fresh-lane"], "live")
            self.assertEqual(verdicts["silent-lane"], "stalled")
            silent = [l for l in report["lanes"] if l["name"] == "silent-lane"][0]
            self.assertGreaterEqual(silent["age_s"], 3900)

    def test_boundary_exactly_at_max_silence_is_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            root = Path(tmp) / "edge"
            _touch(root / "impl.py", 900, now)
            report = lane_liveness.check_lanes(
                [_lane("edge", root)], max_silence=900,
                transcript_index={}, now=now)
            self.assertEqual(report["lanes"][0]["verdict"], "live")

    def test_transcript_evidence_rescues_quiet_worktree(self):
        """An agent thinking/reading (no file writes yet) is live, not stalled."""
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            root = Path(tmp) / "agent-abc123"
            _touch(root / "impl.py", 4000, now)
            report = lane_liveness.check_lanes(
                [_lane("agent-abc123", root, agent_id="abc123")],
                max_silence=900,
                transcript_index={"abc123": now - 20},
                now=now)
            self.assertEqual(report["stalled"], [])
            self.assertEqual(report["lanes"][0]["evidence"], "transcript")
            self.assertEqual(report["exit_code"], 0)

    def test_stale_transcript_does_not_rescue(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            root = Path(tmp) / "agent-dead99"
            _touch(root / "impl.py", 4000, now)
            report = lane_liveness.check_lanes(
                [_lane("agent-dead99", root, agent_id="dead99")],
                max_silence=900,
                transcript_index={"dead99": now - 5000},
                now=now)
            self.assertEqual(report["stalled"], ["agent-dead99"])
            self.assertEqual(report["exit_code"], 1)

    def test_missing_lane_path_is_stalled_not_ignored(self):
        """A claimed lane whose worktree vanished is definitively not live."""
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            report = lane_liveness.check_lanes(
                [_lane("ghost", Path(tmp) / "does-not-exist")],
                max_silence=900, transcript_index={}, now=now)
            self.assertEqual(report["stalled"], ["ghost"])
            self.assertEqual(report["lanes"][0]["verdict"], "missing")
            self.assertEqual(report["exit_code"], 1)

    def test_no_evidence_at_all_is_stalled(self):
        """Empty worktree, no transcript: zero evidence must never read clean."""
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            root = Path(tmp) / "empty-lane"
            root.mkdir()
            os.utime(root, (now - 9000, now - 9000))
            report = lane_liveness.check_lanes(
                [_lane("empty-lane", root)], max_silence=900,
                transcript_index={}, now=now)
            self.assertEqual(report["stalled"], ["empty-lane"])
            self.assertEqual(report["exit_code"], 1)

    def test_unreadable_lane_is_exit_2_not_clean(self):
        """Fail-closed: cannot determine liveness => exit 2, never a silent pass."""
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            root = Path(tmp) / "sealed"
            _touch(root / "impl.py", 30, now)

            original = lane_liveness.newest_mtime

            def boom(path, _orig=original):
                if str(path).endswith("sealed"):
                    raise lane_liveness.LaneUnreadable("permission denied")
                return _orig(path)

            lane_liveness.newest_mtime = boom
            try:
                report = lane_liveness.check_lanes(
                    [_lane("sealed", root)], max_silence=900,
                    transcript_index={}, now=now)
            finally:
                lane_liveness.newest_mtime = original

            self.assertEqual(report["unreadable"], ["sealed"])
            self.assertEqual(report["lanes"][0]["verdict"], "unreadable")
            self.assertEqual(report["exit_code"], 2)

    def test_unreadable_outranks_stalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            stale = Path(tmp) / "silent-lane"
            sealed = Path(tmp) / "sealed"
            _touch(stale / "impl.py", 4000, now)
            _touch(sealed / "impl.py", 30, now)

            original = lane_liveness.newest_mtime

            def boom(path, _orig=original):
                if str(path).endswith("sealed"):
                    raise lane_liveness.LaneUnreadable("permission denied")
                return _orig(path)

            lane_liveness.newest_mtime = boom
            try:
                report = lane_liveness.check_lanes(
                    [_lane("silent-lane", stale), _lane("sealed", sealed)],
                    max_silence=900, transcript_index={}, now=now)
            finally:
                lane_liveness.newest_mtime = original

            self.assertEqual(report["exit_code"], 2)
            self.assertEqual(report["stalled"], ["silent-lane"])


class TestClaimsParsing(unittest.TestCase):
    def test_object_claims_become_the_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = Path(tmp) / "claims.json"
            claims.write_text(json.dumps(
                [{"name": "a", "path": str(Path(tmp) / "a")}]), encoding="utf-8")
            lanes, names = lane_liveness.load_claims(claims)
            self.assertEqual(len(lanes), 1)
            self.assertEqual(lanes[0]["name"], "a")
            self.assertIsNone(names)

    def test_name_only_claims_become_a_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = Path(tmp) / "claims.json"
            claims.write_text(json.dumps(["a", "b"]), encoding="utf-8")
            lanes, names = lane_liveness.load_claims(claims)
            self.assertIsNone(lanes)
            self.assertEqual(names, ["a", "b"])

    def test_malformed_claims_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = Path(tmp) / "claims.json"
            claims.write_text("{not json", encoding="utf-8")
            with self.assertRaises(lane_liveness.LaneUnreadable):
                lane_liveness.load_claims(claims)

    def test_missing_claims_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(lane_liveness.LaneUnreadable):
                lane_liveness.load_claims(Path(tmp) / "nope.json")

    def test_claim_name_with_no_matching_lane_is_kept_as_missing(self):
        lanes = lane_liveness.apply_name_filter(
            [_lane("real", "/tmp/real")], ["real", "phantom"])
        names = [l["name"] for l in lanes]
        self.assertIn("real", names)
        self.assertIn("phantom", names)
        phantom = [l for l in lanes if l["name"] == "phantom"][0]
        self.assertEqual(phantom["path"], "")


class TestWorktreeParsing(unittest.TestCase):
    def test_parses_porcelain_and_drops_primary(self):
        porcelain = (
            "worktree C:/repo\nHEAD abc\nbranch refs/heads/main\n\n"
            "worktree C:/repo/../wt-one\nHEAD def\nbranch refs/heads/feature/one\n\n"
            "worktree C:/tmp/agent-a1b2c3\nHEAD 999\ndetached\n\n"
        )
        lanes = lane_liveness.parse_worktree_porcelain(porcelain)
        names = [l["name"] for l in lanes]
        self.assertNotIn("repo", names)
        self.assertEqual(len(lanes), 2)
        self.assertEqual(lanes[0]["branch"], "feature/one")
        self.assertEqual(lanes[1]["agent_id"], "a1b2c3")
        self.assertEqual(lanes[1]["branch"], "")

    def test_empty_porcelain_yields_no_lanes(self):
        self.assertEqual(lane_liveness.parse_worktree_porcelain(""), [])


class TestTranscriptIndex(unittest.TestCase):
    def test_index_reuses_stall_check_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            root = Path(tmp)
            _touch(root / "proj" / "agent-abc123.jsonl", 45, now)
            index = lane_liveness.build_transcript_index(root)
            self.assertIn("abc123", index)
            self.assertLess(now - index["abc123"], 120)

    def test_missing_transcripts_root_is_empty_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = lane_liveness.build_transcript_index(Path(tmp) / "gone")
            self.assertEqual(index, {})


class TestCli(unittest.TestCase):
    def _run(self, args, cwd):
        return subprocess.run(
            [sys.executable, TOOL] + args,
            capture_output=True, text=True, encoding="utf-8",
            timeout=120, cwd=cwd)

    def test_cli_exit_0_on_fresh_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            lane = Path(tmp) / "fresh"
            _touch(lane / "impl.py", 20, now)
            claims = Path(tmp) / "claims.json"
            claims.write_text(json.dumps(
                [{"name": "fresh", "path": str(lane)}]), encoding="utf-8")
            res = self._run(["--check", "--claims", str(claims), "--json"], tmp)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["stalled"], [])

    def test_cli_exit_1_names_stalled_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            lane = Path(tmp) / "silent"
            _touch(lane / "impl.py", 4000, now)
            claims = Path(tmp) / "claims.json"
            claims.write_text(json.dumps(
                [{"name": "silent", "path": str(lane)}]), encoding="utf-8")
            res = self._run(["--check", "--claims", str(claims)], tmp)
            self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
            self.assertIn("silent", res.stdout)

    def test_cli_max_silence_flag_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            lane = Path(tmp) / "quietish"
            _touch(lane / "impl.py", 1200, now)
            claims = Path(tmp) / "claims.json"
            claims.write_text(json.dumps(
                [{"name": "quietish", "path": str(lane)}]), encoding="utf-8")
            tight = self._run(
                ["--check", "--claims", str(claims), "--max-silence", "900"], tmp)
            self.assertEqual(tight.returncode, 1)
            loose = self._run(
                ["--check", "--claims", str(claims), "--max-silence", "3600"], tmp)
            self.assertEqual(loose.returncode, 0, loose.stdout + loose.stderr)

    def test_cli_exit_2_on_unreadable_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = Path(tmp) / "claims.json"
            claims.write_text("{broken", encoding="utf-8")
            res = self._run(["--check", "--claims", str(claims)], tmp)
            self.assertEqual(res.returncode, 2, res.stdout + res.stderr)

    def test_cli_exit_2_outside_a_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self._run(["--check", "--repo", tmp], tmp)
            self.assertEqual(res.returncode, 2, res.stdout + res.stderr)

    def test_cli_unknown_flag_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = self._run(["--check", "--nonsense"], tmp)
            self.assertNotEqual(res.returncode, 0)

    def test_cli_json_is_ascii_and_parseable(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            lane = Path(tmp) / "silent"
            _touch(lane / "impl.py", 4000, now)
            claims = Path(tmp) / "claims.json"
            claims.write_text(json.dumps(
                [{"name": "silent", "path": str(lane)}]), encoding="utf-8")
            res = self._run(["--check", "--claims", str(claims), "--json"], tmp)
            self.assertEqual(res.returncode, 1)
            res.stdout.encode("ascii")
            payload = json.loads(res.stdout)
            self.assertEqual(payload["exit_code"], 1)
            self.assertEqual(payload["stalled"], ["silent"])


if __name__ == "__main__":
    unittest.main()
