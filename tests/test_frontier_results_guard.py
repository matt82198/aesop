#!/usr/bin/env python3
"""Regression tests: frontier_slice must never clobber a measured results file.

Covers:
- live mode with no ANTHROPIC_API_KEY exits non-zero and writes nothing
- live skip paths (offline seam) exit non-zero and write nothing
- offline default output path is distinct from the live default path
- offline refuses to overwrite a live-mode results file without --overwrite

No test here reaches the network: every live invocation either lacks a key
or sets AESOP_BENCH_OFFLINE=1, which returns before any client is built.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent.parent / "bench"
SCRIPT = str(BENCH_DIR / "frontier_slice.py")
TASKS = str(BENCH_DIR / "tasks_frontier.jsonl")
GROUND_TRUTH = str(BENCH_DIR / "ground_truth_frontier.jsonl")
_STRIPPED = ("ANTHROPIC_API_KEY", "AESOP_BENCH_OFFLINE", "FRONTIER_SLICE_OUTPUT")


def _base_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED}
    if extra:
        env.update(extra)
    return env


def _run(args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=cwd,
        env=env if env is not None else _base_env(),
    )


def _seed_live_file(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mode": "live", "model": "claude-test", "accuracy_percent": 42.0}),
        encoding="utf-8",
    )
    return path.read_bytes()


class TestLiveModeNeverWritesWithoutRun(unittest.TestCase):
    """A live invocation that does not complete a run must not write results."""

    def test_live_without_key_exits_nonzero_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            r = _run([
                "--mode", "live", "--confirm-spend",
                "--tasks", TASKS, "--ground-truth", GROUND_TRUTH,
                "--output", str(out),
            ])
            self.assertNotEqual(r.returncode, 0, f"stdout={r.stdout!r} stderr={r.stderr!r}")
            self.assertFalse(out.exists(), "live run without a key must not write results")
            self.assertIn("ANTHROPIC_API_KEY", r.stderr)

    def test_live_without_key_leaves_existing_live_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "frontier_slice_results.json"
            before = _seed_live_file(out)
            r = _run([
                "--mode", "live", "--confirm-spend",
                "--tasks", TASKS, "--ground-truth", GROUND_TRUTH,
                "--output", str(out),
            ])
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(out.read_bytes(), before)

    def test_offline_seam_skip_exits_nonzero_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            r = _run(
                [
                    "--mode", "live", "--confirm-spend",
                    "--tasks", TASKS, "--ground-truth", GROUND_TRUTH,
                    "--output", str(out),
                ],
                env=_base_env({"ANTHROPIC_API_KEY": "dummy-not-a-key", "AESOP_BENCH_OFFLINE": "1"}),
            )
            self.assertNotEqual(r.returncode, 0, f"stdout={r.stdout!r} stderr={r.stderr!r}")
            self.assertFalse(out.exists(), "seam-skipped live run must not write results")


class TestOfflineNeverClobbersLive(unittest.TestCase):
    """An offline run must not destroy a paid live measurement."""

    def test_offline_default_path_does_not_touch_live_results(self):
        # The loaders resolve bench/*.jsonl relative to cwd, so stage copies
        # under a scratch cwd; the default output path is then also scratch.
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            bench = Path(tmp) / "bench"
            bench.mkdir()
            shutil.copy(TASKS, bench / "tasks_frontier.jsonl")
            shutil.copy(GROUND_TRUTH, bench / "ground_truth_frontier.jsonl")
            live = Path(tmp) / "state" / "bench" / "frontier_slice_results.json"
            before = _seed_live_file(live)
            r = _run(["--mode", "offline"], cwd=tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(live.read_bytes(), before, "offline default path clobbered live results")

    def test_offline_refuses_to_overwrite_live_file_at_explicit_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.json"
            before = _seed_live_file(live)
            r = _run([
                "--mode", "offline",
                "--tasks", TASKS, "--ground-truth", GROUND_TRUTH,
                "--output", str(live),
            ])
            self.assertNotEqual(r.returncode, 0, f"stdout={r.stdout!r} stderr={r.stderr!r}")
            self.assertEqual(live.read_bytes(), before)

    def test_offline_overwrite_flag_replaces_live_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.json"
            _seed_live_file(live)
            r = _run([
                "--mode", "offline", "--overwrite",
                "--tasks", TASKS, "--ground-truth", GROUND_TRUTH,
                "--output", str(live),
            ])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(live.read_text(encoding="utf-8"))["mode"], "offline")


if __name__ == "__main__":
    unittest.main()
