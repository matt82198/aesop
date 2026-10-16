#!/usr/bin/env python3
"""Tests for tools/lane_ab_report.py -- the lane-contract A/B report.

Behavioural tests only: every fixture is written into a temp directory at
runtime, the CLI is driven through a subprocess, and assertions are made on the
observable output and exit code. No personal paths appear anywhere here.
Written as unittest.TestCase classes (repo convention: test_no_bare_test_functions);
pytest also collects them.
"""

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "lane_ab_report.py"
sys.path.insert(0, str(REPO_ROOT / "tools"))

import lane_ab_report  # noqa: E402

BASE = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)


def _ts(minutes):
    return (BASE + timedelta(minutes=minutes)).isoformat()


def _sha(label):
    return hashlib.sha256(("prompt-" + label).encode("utf-8")).hexdigest()


def _dispatch(minutes, arm, label, reason="", project="proj", agent_type="lane"):
    return {
        "ts": _ts(minutes),
        "arm": arm,
        "reason": reason,
        "prompt_sha256": _sha(label),
        "agent_type": agent_type,
        "model": "claude-haiku-5-5",
        "project": project,
    }


def _denial(minutes, rule, project="proj", decision="deny"):
    return {"ts": _ts(minutes), "decision": decision, "hook": rule, "project": project}


def _run(args, cwd):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


class _TmpCase(unittest.TestCase):
    """Gives each test a private temp directory, removed afterwards."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_jsonl(self, name, records):
        lines = [json.dumps(r) for r in records]
        path = self.tmp_path / name
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    def _args(self, ab, res, events=None):
        ab_path = self._write_jsonl("lane-ab.jsonl", ab)
        res_path = self._write_jsonl("resistance-ledger.jsonl", res)
        args = ["--ab-ledger", str(ab_path), "--resistance-ledger", str(res_path)]
        if events is not None:
            args += ["--task-events", str(self._write_jsonl("task-events.jsonl", events))]
        return args

    def _main_stdout(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = lane_ab_report.main(argv)
        return rc, buf.getvalue()


class FaultDetectionTests(_TmpCase):
    def test_arm_a_repeat_of_arm_b_prompt_inside_window_is_a_fault(self):
        ab = [_dispatch(0, "B", "x"), _dispatch(30, "A", "x")]
        report = lane_ab_report.build_report(ab, [], window_minutes=60)
        self.assertEqual(report["arms"]["B"]["faults"], 1)

    def test_repeat_outside_window_is_not_a_fault(self):
        ab = [_dispatch(0, "B", "x"), _dispatch(120, "A", "x")]
        report = lane_ab_report.build_report(ab, [], window_minutes=60)
        self.assertEqual(report["arms"]["B"]["faults"], 0)

    def test_different_prompt_sha_is_not_a_fault(self):
        ab = [_dispatch(0, "B", "x"), _dispatch(10, "A", "y")]
        report = lane_ab_report.build_report(ab, [], window_minutes=60)
        self.assertEqual(report["arms"]["B"]["faults"], 0)

    def test_window_boundary_is_inclusive(self):
        ab = [_dispatch(0, "B", "x"), _dispatch(60, "A", "x")]
        report = lane_ab_report.build_report(ab, [], window_minutes=60)
        self.assertEqual(report["arms"]["B"]["faults"], 1)

    def test_arm_b_repeat_is_not_counted_as_fault_of_arm_a(self):
        ab = [_dispatch(0, "A", "x"), _dispatch(10, "B", "x")]
        report = lane_ab_report.build_report(ab, [], window_minutes=60)
        self.assertEqual(report["arms"]["B"]["faults"], 0)


class DenialAndForcedTests(_TmpCase):
    def test_forced_count_comes_from_reason_field(self):
        ab = [
            _dispatch(0, "A", "a1", reason="forced"),
            _dispatch(5, "A", "a2"),
            _dispatch(10, "B", "b1", reason="FORCED"),
        ]
        report = lane_ab_report.build_report(ab, [], window_minutes=60)
        self.assertEqual(report["arms"]["A"]["forced"], 1)
        self.assertEqual(report["arms"]["B"]["forced"], 1)

    def test_denial_attributed_to_latest_preceding_dispatch_in_same_project(self):
        ab = [_dispatch(0, "B", "b1", project="p"), _dispatch(20, "A", "a1", project="p")]
        res = [_denial(5, "rule-one", project="p")]
        report = lane_ab_report.build_report(ab, res, window_minutes=60)
        self.assertEqual(report["arms"]["B"]["denials"], 1)
        self.assertEqual(report["arms"]["A"]["denials"], 0)

    def test_denial_before_any_dispatch_is_unattributed(self):
        ab = [_dispatch(10, "B", "b1")]
        res = [_denial(0, "rule-one")]
        report = lane_ab_report.build_report(ab, res, window_minutes=60)
        self.assertEqual(report["arms"]["B"]["denials"], 0)
        self.assertEqual(report["unattributed_denials"], 1)

    def test_rules_by_fault_count_lists_rules_on_faulting_dispatches_only(self):
        ab = [
            _dispatch(0, "B", "x", project="p"),
            _dispatch(30, "A", "x", project="p"),
            _dispatch(100, "B", "clean", project="p"),
        ]
        res = [
            _denial(1, "rule-hot", project="p"),
            _denial(2, "rule-hot", project="p"),
            _denial(3, "rule-cold", project="p"),
            _denial(101, "rule-clean-only", project="p"),
        ]
        report = lane_ab_report.build_report(ab, res, window_minutes=60)
        self.assertEqual(
            report["rules_by_fault_count"],
            [{"rule": "rule-hot", "faults": 2}, {"rule": "rule-cold", "faults": 1}],
        )

    def test_resistance_record_accepts_kind_instead_of_decision(self):
        ab = [_dispatch(0, "B", "b1")]
        res = [{"ts": _ts(1), "kind": "deny", "rule": "rule-kind", "project": "proj"}]
        report = lane_ab_report.build_report(ab, res, window_minutes=60)
        self.assertEqual(report["arms"]["B"]["denials"], 1)


class VerdictTests(_TmpCase):
    def test_insufficient_when_an_arm_is_below_min_n(self):
        ab = [_dispatch(i, "A", f"a{i}") for i in range(12)]
        ab += [_dispatch(100 + i, "B", f"b{i}") for i in range(3)]
        rc, out = self._main_stdout(self._args(ab, []) + ["--min-n", "10"])
        self.assertEqual(rc, 0)
        self.assertIn("INSUFFICIENT N", out)
        self.assertNotIn("verdict:", out)

    def test_verdict_printed_when_both_arms_meet_min_n(self):
        ab = [_dispatch(i, "A", f"a{i}") for i in range(10)]
        ab += [_dispatch(200 + i, "B", f"b{i}") for i in range(10)]
        rc, out = self._main_stdout(self._args(ab, []) + ["--min-n", "10"])
        self.assertEqual(rc, 0)
        verdict_lines = [ln for ln in out.splitlines() if ln.startswith("verdict:")]
        self.assertEqual(len(verdict_lines), 1)
        self.assertNotIn("INSUFFICIENT", out)


class TaskEventTests(_TmpCase):
    def test_task_events_not_provided_line_when_flag_absent(self):
        ab = [_dispatch(0, "A", "a1")]
        rc, out = self._main_stdout(self._args(ab, []))
        self.assertEqual(rc, 0)
        self.assertIn("task-events: not provided", out)

    def test_tokens_per_dispatch_from_events_that_carry_tokens(self):
        ab = [
            _dispatch(0, "A", "a1", agent_type="lane-a"),
            _dispatch(5, "A", "a2", agent_type="lane-a"),
        ]
        events = [
            {"ts": _ts(1), "agent_type": "lane-a", "tokens": 100},
            {"ts": _ts(6), "agent_type": "lane-a", "tokens": 300},
        ]
        report = lane_ab_report.build_report(ab, [], window_minutes=60, task_events=events)
        self.assertEqual(report["arms"]["A"]["tokens_per_dispatch"], 200.0)

    def test_tokens_per_dispatch_is_null_when_events_have_no_token_field(self):
        ab = [_dispatch(0, "A", "a1", agent_type="lane-a")]
        events = [{"ts": _ts(1), "agent_type": "lane-a", "stop_reason": "end_turn"}]
        report = lane_ab_report.build_report(ab, [], window_minutes=60, task_events=events)
        self.assertIsNone(report["arms"]["A"]["tokens_per_dispatch"])


class OutputShapeTests(_TmpCase):
    def test_json_output_has_arms_rules_and_verdict_fields(self):
        ab = [_dispatch(i, "A", f"a{i}") for i in range(10)]
        ab += [_dispatch(300 + i, "B", f"b{i}") for i in range(10)]
        proc = _run(self._args(ab, []) + ["--json", "--min-n", "10"], self.tmp_path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(set(data["arms"]), {"A", "B"})
        self.assertEqual(data["arms"]["A"]["dispatches"], 10)
        self.assertEqual(data["arms"]["B"]["dispatches"], 10)
        self.assertIn("rules_by_fault_count", data)
        self.assertIsNotNone(data["verdict"])
        self.assertFalse(data["insufficient"])

    def test_text_table_contains_arm_rows(self):
        ab = [_dispatch(0, "A", "a1"), _dispatch(5, "B", "b1")]
        proc = _run(self._args(ab, []), self.tmp_path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("arm", proc.stdout.lower())
        self.assertIn("INSUFFICIENT N", proc.stdout)


class FailClosedInputTests(_TmpCase):
    def test_malformed_ab_line_exits_2(self):
        ab_path = self.tmp_path / "lane-ab.jsonl"
        ab_path.write_text("{not json\n", encoding="utf-8")
        res_path = self._write_jsonl("resistance-ledger.jsonl", [])
        proc = _run(["--ab-ledger", str(ab_path), "--resistance-ledger", str(res_path)], self.tmp_path)
        self.assertEqual(proc.returncode, 2)

    def test_missing_ledger_file_exits_2(self):
        res_path = self._write_jsonl("resistance-ledger.jsonl", [])
        proc = _run(
            ["--ab-ledger", str(self.tmp_path / "absent.jsonl"), "--resistance-ledger", str(res_path)],
            self.tmp_path,
        )
        self.assertEqual(proc.returncode, 2)

    def test_unknown_arm_value_exits_2(self):
        ab = [_dispatch(0, "C", "c1")]
        proc = _run(self._args(ab, []), self.tmp_path)
        self.assertEqual(proc.returncode, 2)

    def test_resistance_record_without_ts_exits_2(self):
        ab = [_dispatch(0, "A", "a1")]
        bad = {"decision": "deny", "hook": "rule-x"}
        proc = _run(self._args(ab, [bad]), self.tmp_path)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
