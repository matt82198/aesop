#!/usr/bin/env python3
"""Tests for tools/cli_seat_trial.py pure helpers (offline, stdlib unittest).

Covers the pass-criteria parsing and task selection used by the real trial.
Run as a script: python tests/test_cli_seat_trial.py
"""

import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import cli_seat_trial as trial  # noqa: E402


class TestParseCounts(unittest.TestCase):
    def test_all_passed(self):
        self.assertEqual(trial.parse_counts("5 passed in 0.12s"), (5, 5))

    def test_mixed_passed_and_failed(self):
        self.assertEqual(trial.parse_counts("3 failed, 4 passed in 1.0s"), (4, 7))

    def test_no_summary_is_zero_of_zero(self):
        self.assertEqual(trial.parse_counts("collection error"), (0, 0))


class TestSelect(unittest.TestCase):
    SLUGS = ("b", "a", "c", "d", "e")

    def test_offset_then_limit_over_sorted_slugs(self):
        self.assertEqual(trial.select(self.SLUGS, 0, 3), ["a", "b", "c"])
        self.assertEqual(trial.select(self.SLUGS, 3, 3), ["d", "e"])

    def test_no_limit_returns_rest(self):
        self.assertEqual(trial.select(self.SLUGS, 4, None), ["e"])


class TestModelLabel(unittest.TestCase):
    def test_reads_value_after_model_flag(self):
        argv = ["agy", "--model", "gemini-4-hydrogen-low"]
        self.assertEqual(trial.model_label(argv), "gemini-4-hydrogen-low")

    def test_absent_flag_is_empty(self):
        self.assertEqual(trial.model_label(["agy", "-p"]), "")


class TestTaskRepoSnapshot(unittest.TestCase):
    """Each run repo must carry the same tree the local trial ran against (full worktree)."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "src"
        self.runs = self.root / "runs"
        for rel in (
            "tools/gate_stub_list.py",
            "tests/test_gate_stub_list.py",
            "hooks/pre-push-policy.sh",
            "state_store/api.py",
            "local-trial-agy/runs/old/junk.txt",
            "runs/stale.txt",
            ".git/HEAD",
            "tools/__pycache__/x.pyc",
        ):
            p = self.src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n", encoding="utf-8")
        self.runs.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_hooks_and_state_store_are_copied_into_task_repo(self):
        repo = trial.make_task_repo(self.src, self.runs, "gate_stub_list", "tools/gate_stub_list.py")
        self.assertTrue((repo / "hooks" / "pre-push-policy.sh").is_file())
        self.assertTrue((repo / "state_store" / "api.py").is_file())

    def test_target_module_is_committed_empty(self):
        repo = trial.make_task_repo(self.src, self.runs, "gate_stub_list", "tools/gate_stub_list.py")
        self.assertEqual((repo / "tools" / "gate_stub_list.py").read_text(encoding="utf-8"), "")

    def test_run_outputs_and_vcs_and_caches_are_not_copied(self):
        repo = trial.make_task_repo(self.src, self.runs, "gate_stub_list", "tools/gate_stub_list.py")
        self.assertFalse((repo / "local-trial-agy").exists())
        self.assertFalse((repo / "runs").exists())
        self.assertFalse((repo / "tools" / "__pycache__").exists())
        self.assertFalse((repo / ".git" / "HEAD").exists() and (repo / ".git" / "HEAD").read_text(encoding="utf-8") == "x\n")


class TestFailureReason(unittest.TestCase):
    """A failing task must report a real error string, never null."""

    def test_wave_error_plus_final_summary_line(self):
        wave = {"built": [{"error": "test failed with exit 2"}]}
        out = "collecting...\n1 error in 0.24s\n"
        self.assertEqual(
            trial.failure_reason(None, wave, out),
            "test failed with exit 2; 1 error in 0.24s",
        )

    def test_exception_wins_over_wave_error(self):
        wave = {"built": [{"error": "test failed with exit 2"}]}
        self.assertEqual(trial.failure_reason("RuntimeError('boom')", wave, "x\n"), "RuntimeError('boom')")

    def test_falls_back_to_last_output_line_when_wave_is_silent(self):
        self.assertEqual(trial.failure_reason(None, None, "a\n\nFAILED tests/t.py::t\n"), "FAILED tests/t.py::t")

    def test_never_empty_when_output_is_empty(self):
        self.assertTrue(trial.failure_reason(None, None, "").startswith("test failed"))


if __name__ == "__main__":
    unittest.main()
