"""Offline tests for the shared results home and its record/compare scripts.

record_result.py must refuse invalid rows, and must measure passed itself.
compare_results.py must show missing results as missing (never pass or fail)
and list the tasks on which the harnesses disagree.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HC_DIR = REPO_ROOT / "bench" / "harness-compare"
if str(HC_DIR) not in sys.path:
    sys.path.insert(0, str(HC_DIR))

RECORD = HC_DIR / "record_result.py"
COMPARE = HC_DIR / "compare_results.py"
GOOD_STARTED = "2026-11-18T10:00:00+00:00"


def _script(path, args):
    return subprocess.run(
        [sys.executable, str(path)] + list(args),
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def _write_row(root, harness, run_id, task_id, passed, **overrides):
    row = {
        "harness": harness,
        "model": "fixture-model",
        "run_id": run_id,
        "task_id": task_id,
        "passed": passed,
        "check_exit": 0 if passed else 1,
        "wall_seconds": 10.0,
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.01,
        "turns": 3,
        "tool_calls": 4,
        "started_at": GOOD_STARTED,
        "notes": "",
    }
    row.update(overrides)
    path = Path(root) / harness / run_id / (task_id + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
    return path


class RecordResultTests(unittest.TestCase):
    def setUp(self):
        import harness_corpus as hc

        self.hc = hc
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "results"
        self.wd_ref = Path(self.tmp.name) / "wd-ref"
        self.wd_seed = Path(self.tmp.name) / "wd-seed"
        row = hc.task_by_id("bug-paginate")
        hc.prepare_task(row, self.wd_seed)
        hc.prepare_task(row, self.wd_ref)
        hc.overlay_reference(row, self.wd_ref)

    def tearDown(self):
        self.tmp.cleanup()

    def _args(self, workdir, **overrides):
        base = {
            "--harness": "agy",
            "--model": "fixture-model",
            "--run-id": "r1",
            "--task": "bug-paginate",
            "--workdir": str(workdir),
            "--wall-seconds": "12.5",
            "--started-at": GOOD_STARTED,
            "--input-tokens": "100",
            "--output-tokens": "50",
            "--cost-usd": "0.02",
            "--turns": "3",
            "--tool-calls": "4",
            "--results-root": str(self.root),
        }
        base.update(overrides)
        args = []
        for key, value in base.items():
            if value is None:
                continue
            args.extend([key, value])
        return args

    def _json_files(self):
        return list(self.root.rglob("*.json")) if self.root.exists() else []

    def test_refuses_unknown_task_and_writes_nothing(self):
        proc = _script(RECORD, self._args(self.wd_ref, **{"--task": "no-such-task"}))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self._json_files(), [])

    def test_refuses_negative_tokens(self):
        proc = _script(RECORD, self._args(self.wd_ref, **{"--input-tokens": "-5"}))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self._json_files(), [])

    def test_refuses_timestamp_without_timezone(self):
        proc = _script(RECORD, self._args(self.wd_ref, **{"--started-at": "2026-11-18T10:00:00"}))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self._json_files(), [])

    def test_refuses_path_like_run_id(self):
        proc = _script(RECORD, self._args(self.wd_ref, **{"--run-id": "../escape"}))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self._json_files(), [])

    def test_records_measured_pass_for_reference_workdir(self):
        proc = _script(RECORD, self._args(self.wd_ref))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads((self.root / "agy" / "r1" / "bug-paginate.json").read_text(encoding="utf-8"))
        self.assertIs(data["passed"], True)
        self.assertEqual(data["check_exit"], 0)
        self.assertEqual(data["input_tokens"], 100)

    def test_records_measured_fail_for_seed_workdir_even_if_flags_claim_pass(self):
        proc = _script(RECORD, self._args(self.wd_seed, **{"--notes": "claims pass"}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads((self.root / "agy" / "r1" / "bug-paginate.json").read_text(encoding="utf-8"))
        self.assertIs(data["passed"], False)
        self.assertNotEqual(data["check_exit"], 0)

    def test_refuses_overwrite_of_recorded_row(self):
        first = _script(RECORD, self._args(self.wd_ref))
        self.assertEqual(first.returncode, 0, first.stderr)
        second = _script(RECORD, self._args(self.wd_ref))
        self.assertEqual(second.returncode, 2, second.stderr)


class CompareResultsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "results"

    def tearDown(self):
        self.tmp.cleanup()

    def _compare_json(self, run_id="r1"):
        proc = _script(COMPARE, ["--run-id", run_id, "--results-root", str(self.root), "--json"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_missing_result_is_shown_as_missing_not_counted_as_fail(self):
        _write_row(self.root, "agy", "r1", "bug-paginate", True)
        _write_row(self.root, "agy", "r1", "bug-slugify", True)
        _write_row(self.root, "claude-code-aesop", "r1", "bug-paginate", True)
        data = self._compare_json()
        self.assertEqual(data["harnesses"]["claude-code-aesop"]["attempted"], 1)
        self.assertEqual(data["harnesses"]["claude-code-aesop"]["passed"], 1)
        self.assertEqual(data["harnesses"]["claude-code-aesop"]["missing"], data["total_tasks"] - 1)
        self.assertEqual(data["matrix"]["bug-slugify"]["claude-code-aesop"], "missing")
        self.assertEqual(data["disagreements"], [])

    def test_disagreement_is_listed(self):
        _write_row(self.root, "agy", "r1", "bug-paginate", True)
        _write_row(self.root, "claude-code-aesop", "r1", "bug-paginate", False)
        _write_row(self.root, "agy", "r1", "bug-slugify", True)
        _write_row(self.root, "claude-code-aesop", "r1", "bug-slugify", True)
        data = self._compare_json()
        self.assertEqual(data["disagreements"], ["bug-paginate"])

    def test_null_numbers_are_excluded_from_totals(self):
        _write_row(self.root, "agy", "r1", "bug-paginate", True, input_tokens=None, cost_usd=None)
        _write_row(self.root, "agy", "r1", "bug-slugify", True, input_tokens=40, cost_usd=0.5)
        data = self._compare_json()
        totals = data["harnesses"]["agy"]["totals"]
        self.assertEqual(totals["input_tokens"], 40)
        self.assertAlmostEqual(totals["cost_usd"], 0.5)
        self.assertEqual(totals["unreported_input_tokens"], 1)

    def test_table_output_prints_missing_and_task_ids(self):
        _write_row(self.root, "agy", "r1", "bug-paginate", True)
        proc = _script(COMPARE, ["--run-id", "r1", "--results-root", str(self.root)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("bug-paginate", proc.stdout)
        self.assertIn("missing", proc.stdout)


if __name__ == "__main__":
    unittest.main()
