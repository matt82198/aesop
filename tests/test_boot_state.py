"""Tests for tools/boot_state.py -- the ONE canonical boot brief.

Contract under test:
  - Sections in fixed order: GOAL, STATE.md ACTIVE block, BUILDLOG.md tail,
    EVENT STORE, GIT.
  - GOAL comes only from the event store projection; no goal event means the literal
    line `GOAL: none recorded` and nothing else for that section.
  - The brief is byte-capped (--max-bytes, default 12000) and the cap always holds.
  - Deterministic: same inputs give byte-identical stdout and --json output.
  - Unreadable (or missing) store: exit 2, one stderr line, empty stdout. Never a
    partial brief that looks complete.

Isolation: temp git repo + temp state dir; AESOP_STATE_ROOT pinned in the child env.
The live store and the live STATE.md/BUILDLOG.md are never read.

Run: python -m unittest tests.test_boot_state
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from state_store.api import StateAPI  # noqa: E402
from state_store.paths import resolve_db_path  # noqa: E402
from tools import goal_state  # noqa: E402

TOOL = REPO_ROOT / "tools" / "boot_state.py"

STATE_MD = """# STATE

## ACTIVE: RELEASE TEST

- active line alpha
- active line beta

## NEXT STEPS

- next step that must NOT appear in the brief
"""


def _git(repo, *args):
    # Identity is passed per-invocation so no global or repo config is written.
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
        check=True, capture_output=True, encoding="utf-8", errors="replace",
    )


class BootStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        self.state_dir = root / "state"
        self.state_dir.mkdir()
        _git(self.repo, "init", "-q")
        (self.repo / "STATE.md").write_text(STATE_MD, encoding="utf-8")
        buildlog = "".join(f"### [2026-10-15 00:{i:02d}] buildlog entry {i}\n" for i in range(40))
        (self.repo / "BUILDLOG.md").write_text("# BUILDLOG\n" + buildlog, encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "fixture commit one")

    def tearDown(self):
        self._tmp.cleanup()

    def _brief(self, *extra):
        env = dict(os.environ)
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        return subprocess.run(
            [sys.executable, str(TOOL), "--repo", str(self.repo), "--state-dir", str(self.state_dir), *extra],
            capture_output=True, encoding="utf-8", errors="replace", env=env, timeout=60,
        )

    def _seed_goal(self, goal="Finish boot brief"):
        with StateAPI(resolve_db_path(self.state_dir, "tracker")) as api:
            goal_state.record_goal(api, goal, phase="build", next_action="write tests", refs=["PR #1"])

    def _seed_tasks(self, n):
        with StateAPI(resolve_db_path(self.state_dir, "tracker")) as api:
            for i in range(n):
                api.append(
                    "task", "task_event",
                    {"ts": f"2026-10-15T00:{i:02d}:00Z", "agent_type": "fixture-agent",
                     "summary": f"task summary number {i}", "kind": "agent", "event_id": f"e{i}"},
                )

    def test_section_order_is_fixed(self):
        self._seed_goal()
        self._seed_tasks(2)
        r = self._brief()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        markers = ["GOAL:", "## STATE.md", "## BUILDLOG.md", "## EVENT STORE", "## GIT"]
        positions = [out.index(m) for m in markers]
        self.assertEqual(positions, sorted(positions), out)

    def test_state_section_is_active_block_only(self):
        self._seed_goal()
        out = self._brief().stdout
        self.assertIn("active line alpha", out)
        self.assertNotIn("NEXT STEPS", out)
        self.assertNotIn("must NOT appear", out)

    def test_buildlog_section_is_last_n_lines(self):
        self._seed_goal()
        out = self._brief("--buildlog-lines", "3").stdout
        self.assertIn("buildlog entry 39", out)
        self.assertIn("buildlog entry 37", out)
        self.assertNotIn("buildlog entry 36", out)

    def test_no_goal_case_says_none_recorded_and_nothing_else_for_goal(self):
        self._seed_tasks(1)  # store exists, holds no goal_set event
        r = self._brief()
        self.assertEqual(r.returncode, 0, r.stderr)
        goal_lines = [ln for ln in r.stdout.splitlines() if ln.startswith("GOAL:")]
        self.assertEqual(goal_lines, ["GOAL: none recorded"])
        self.assertNotIn("phase:", r.stdout)
        self.assertNotIn("next_action:", r.stdout)

    def test_goal_projection_appears_in_goal_section(self):
        self._seed_goal("Ship the thing")
        out = self._brief().stdout
        self.assertIn("GOAL: Ship the thing", out)
        self.assertIn("next_action: write tests", out)

    def test_cap_holds_and_goal_survives(self):
        self._seed_goal("Keep the goal even when everything is trimmed")
        self._seed_tasks(5)
        for cap in (4000, 1200, 600):
            r = self._brief("--max-bytes", str(cap))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLessEqual(len(r.stdout.encode("utf-8")), cap, f"cap {cap} exceeded")
            self.assertIn("GOAL: Keep the goal", r.stdout)

    def test_default_cap_is_12000_bytes(self):
        self._seed_goal()
        big = "".join(f"- active filler line {i} " + "x" * 80 + "\n" for i in range(400))
        (self.repo / "STATE.md").write_text("## ACTIVE: BIG\n" + big + "## NEXT STEPS\n", encoding="utf-8")
        r = self._brief()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLessEqual(len(r.stdout.encode("utf-8")), 12000)

    def test_deterministic_two_runs_byte_identical(self):
        self._seed_goal()
        self._seed_tasks(3)
        a = self._brief()
        b = self._brief()
        self.assertEqual(a.stdout.encode("utf-8"), b.stdout.encode("utf-8"))
        ja = self._brief("--json")
        jb = self._brief("--json")
        self.assertEqual(ja.stdout.encode("utf-8"), jb.stdout.encode("utf-8"))

    def test_json_mode_same_sections_in_order(self):
        self._seed_goal()
        r = self._brief("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        obj = json.loads(r.stdout)
        self.assertEqual(
            list(obj["sections"].keys()),
            ["goal", "state_active", "buildlog_tail", "event_store", "git"],
        )
        self.assertIn("Finish boot brief", obj["sections"]["goal"])

    def test_event_store_section_cites_projections(self):
        self._seed_goal()
        self._seed_tasks(2)
        out = self._brief().stdout
        self.assertIn("project_tracker", out)
        self.assertIn("task_event", out)
        self.assertIn("summary number 1", out)

    def test_git_section_has_log_and_status(self):
        self._seed_goal()
        (self.repo / "dirty.txt").write_text("untracked\n", encoding="utf-8")
        out = self._brief().stdout
        self.assertIn("fixture commit one", out)
        self.assertIn("?? dirty.txt", out)

    def test_unreadable_store_exits_2_with_one_stderr_line(self):
        bad = Path(self._tmp.name) / "bad-state"
        bad.mkdir()
        Path(resolve_db_path(bad, "tracker")).write_bytes(b"garbage garbage garbage" * 100)
        env = dict(os.environ)
        env["AESOP_STATE_ROOT"] = str(bad)
        r = subprocess.run(
            [sys.executable, str(TOOL), "--repo", str(self.repo), "--state-dir", str(bad)],
            capture_output=True, encoding="utf-8", errors="replace", env=env, timeout=60,
        )
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)

    def test_missing_store_exits_2(self):
        missing = Path(self._tmp.name) / "no-such-state"
        env = dict(os.environ)
        env["AESOP_STATE_ROOT"] = str(missing)
        r = subprocess.run(
            [sys.executable, str(TOOL), "--repo", str(self.repo), "--state-dir", str(missing)],
            capture_output=True, encoding="utf-8", errors="replace", env=env, timeout=60,
        )
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertFalse(missing.exists(), "a read must not create the state dir")


if __name__ == "__main__":
    unittest.main()
