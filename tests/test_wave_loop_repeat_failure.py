"""Red/green: the wave repair loop must not re-ask a model that just produced an
identical test failure.

Contract (task haiku-equivalence-loop-repeat-failure-redispatch):
  - A failure fingerprint normalizes timings, temp paths, memory addresses and
    line numbers, so timing noise alone never changes it.
  - The first identical repeat takes a fresh-context redispatch: the next round
    carries only the task, the current owned file content and one failing case
    (not the accumulated repair noise).
  - A second identical repeat ends the item as failed with reason
    "identical-failure-repeat". The repair cap is not changed.
  - A differing failure keeps the normal repair path.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO / "driver"), str(REPO / "tools"), str(REPO / "state_store"), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import repeat_failure  # noqa: E402
import wave_loop  # noqa: E402

FAIL_RUN_1 = (
    "test_value (mod_tests.T) ... FAIL\n"
    "AssertionError: 2 != 3\n"
    "Ran 1 test in 0.012s\n"
    "FAILED (failures=1)\n"
    "  File \"C:\\Users\\Mattt\\AppData\\Local\\Temp\\tmpk3_9\\t.py\", line 12\n"
    "  obj at 0x7f3a1c20\n"
    "NOISE-LINE-ALPHA unrelated progress output\n"
)

# Same failure, different timing, temp directory, memory address and line number.
FAIL_RUN_2 = (
    "test_value (mod_tests.T) ... FAIL\n"
    "AssertionError: 2 != 3\n"
    "Ran 1 test in 4.587s\n"
    "FAILED (failures=1)\n"
    "  File \"/tmp/tmpzz91/t.py\", line 19\n"
    "  obj at 0x7fb2ee10\n"
    "NOISE-LINE-ALPHA unrelated progress output\n"
)

FAIL_DIFFERENT = (
    "test_value (mod_tests.T) ... FAIL\n"
    "AssertionError: 2 != 5\n"
    "Ran 1 test in 0.013s\n"
    "FAILED (failures=1)\n"
)


class TestFailureFingerprint(unittest.TestCase):
    def test_fingerprint_stable_across_timing_temp_path_address_noise(self):
        a = repeat_failure.failure_fingerprint(FAIL_RUN_1, "", 1)
        b = repeat_failure.failure_fingerprint(FAIL_RUN_2, "", 1)
        self.assertEqual(a, b)

    def test_fingerprint_changes_when_failing_case_changes(self):
        a = repeat_failure.failure_fingerprint(FAIL_RUN_1, "", 1)
        c = repeat_failure.failure_fingerprint(FAIL_DIFFERENT, "", 1)
        self.assertNotEqual(a, c)

    def test_fingerprint_changes_when_exit_code_changes(self):
        a = repeat_failure.failure_fingerprint(FAIL_RUN_1, "", 1)
        b = repeat_failure.failure_fingerprint(FAIL_RUN_1, "", 2)
        self.assertNotEqual(a, b)


class TestRecordFailureClassification(unittest.TestCase):
    def test_first_failure_is_a_normal_repair(self):
        state = {}
        verdict = repeat_failure.record_failure(state, FAIL_RUN_1, "", 1)
        self.assertEqual(verdict, "repair")
        self.assertEqual(state["failureRepeats"], 0)

    def test_first_identical_repeat_is_fresh(self):
        state = {}
        repeat_failure.record_failure(state, FAIL_RUN_1, "", 1)
        verdict = repeat_failure.record_failure(state, FAIL_RUN_2, "", 1)
        self.assertEqual(verdict, "fresh")
        self.assertTrue(repeat_failure.needs_fresh_redispatch(state))

    def test_second_identical_repeat_ends(self):
        state = {}
        repeat_failure.record_failure(state, FAIL_RUN_1, "", 1)
        repeat_failure.record_failure(state, FAIL_RUN_2, "", 1)
        verdict = repeat_failure.record_failure(state, FAIL_RUN_1, "", 1)
        self.assertEqual(verdict, "end")

    def test_differing_failure_resets_to_normal_repair(self):
        state = {}
        repeat_failure.record_failure(state, FAIL_RUN_1, "", 1)
        repeat_failure.record_failure(state, FAIL_RUN_2, "", 1)
        verdict = repeat_failure.record_failure(state, FAIL_DIFFERENT, "", 1)
        self.assertEqual(verdict, "repair")
        self.assertFalse(repeat_failure.needs_fresh_redispatch(state))


class TestFreshPrompt(unittest.TestCase):
    def test_fresh_prompt_has_task_file_and_one_case_only(self):
        prompt = repeat_failure.build_fresh_prompt(
            "TASK: make VALUE 2",
            {"mod.py": "VALUE = 3\n"},
            "AssertionError: 2 != 3",
        )
        self.assertIn("TASK: make VALUE 2", prompt)
        self.assertIn("VALUE = 3", prompt)
        self.assertIn("AssertionError: 2 != 3", prompt)
        self.assertNotIn("NOISE-LINE-ALPHA", prompt)

    def test_one_failing_case_excludes_unrelated_output(self):
        case = repeat_failure.one_failing_case(FAIL_RUN_1, "")
        self.assertIn("AssertionError: 2 != 3", case)
        self.assertNotIn("NOISE-LINE-ALPHA", case)


class _FakeClaim:
    instance_id = "test-instance"

    def fence_ok(self, slug):
        return True


class TestRepairRoundIntegration(unittest.TestCase):
    """Drives wave_loop._repair_one_round with a scripted dispatch_item."""

    def _run(self, scripted_outputs, tmp):
        item = {
            "slug": "s1",
            "prompt": "TASK: make VALUE 2",
            "ownsFiles": ["mod.py"],
            "workDir": tmp,
        }
        item_result = {
            "verified": False,
            "testExit": 1,
            "testStdout": FAIL_RUN_1,
            "testStderr": "",
            "error": None,
            "repairs": 0,
            "filesWritten": [],
        }
        prompts = []
        outputs = iter(scripted_outputs)

        def fake_build(driver, repair_item):
            prompts.append(repair_item["prompt"])
            return dict(repair_item)

        def fake_dispatch(driver, manifest_item, workdir=None):
            return {
                "verified": False,
                "testExit": 1,
                "testStdout": next(outputs),
                "testStderr": "",
                "error": None,
                "filesWritten": [],
            }

        with mock.patch.object(wave_loop, "build_manifest_item", side_effect=fake_build), \
                mock.patch.object(wave_loop, "dispatch_item", side_effect=fake_dispatch):
            r1 = wave_loop._repair_one_round([(0, item, item_result)], None, None, _FakeClaim())
            r2 = wave_loop._repair_one_round(r1, None, None, _FakeClaim())
            r3 = wave_loop._repair_one_round(r2, None, None, _FakeClaim())
        return prompts, item_result, r1, r2, r3

    def test_identical_repeat_redispatches_fresh_then_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "mod.py").write_text("VALUE = 3\n", encoding="utf-8")
            prompts, item_result, r1, r2, r3 = self._run(
                [FAIL_RUN_2, FAIL_RUN_1, FAIL_RUN_2], tmp
            )
        # Round 1 is a normal repair (first repeat detected after it runs).
        self.assertEqual(len(r1), 1)
        # Round 2 is the fresh-context redispatch: task + file + one failing case.
        self.assertEqual(len(prompts), 2)
        self.assertIn("VALUE = 3", prompts[1])
        self.assertIn("AssertionError: 2 != 3", prompts[1])
        self.assertNotIn("NOISE-LINE-ALPHA", prompts[1])
        # Round 2 output is identical again: the second repeat ends the item.
        self.assertEqual(len(r2), 0)
        self.assertEqual(item_result["error"], "identical-failure-repeat")
        self.assertEqual(r3, [])

    def test_differing_failure_keeps_normal_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "mod.py").write_text("VALUE = 3\n", encoding="utf-8")
            prompts, item_result, r1, r2, r3 = self._run(
                [FAIL_DIFFERENT, FAIL_RUN_2, FAIL_RUN_1], tmp
            )
        self.assertEqual(len(r1), 1)
        self.assertEqual(len(r2), 1)
        self.assertNotEqual(item_result.get("error"), "identical-failure-repeat")
        # Round 2 still uses the normal repair prompt (carries the repair header).
        self.assertIn("Test failed with exit code", prompts[1])

    def test_repair_round_two_carries_latest_test_output(self):
        """Each repair round must send the LATEST test output, not round 0's.

        Initial dispatch prints FAIL-A; repair round 1 dispatches and prints
        FAIL-B; repair round 2 dispatches and prints FAIL-C. The LAST TEST OUTPUT
        handed to the worker in repair round 2 must be FAIL-B, not FAIL-A.
        """
        sent_last_outputs = []
        dispatch_outputs = iter(["FAIL-B\n", "FAIL-C\n"])

        def fake_build(driver, repair_item):
            sent_last_outputs.append(repair_item.get("lastTestOutput", ""))
            return dict(repair_item)

        def fake_dispatch(driver, manifest_item, workdir=None):
            return {
                "verified": False,
                "testExit": 1,
                "testStdout": next(dispatch_outputs),
                "testStderr": "",
                "error": None,
                "filesWritten": [],
            }

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "mod.py").write_text("VALUE = 3\n", encoding="utf-8")
            item = {
                "slug": "s2",
                "prompt": "TASK: make VALUE 2",
                "ownsFiles": ["mod.py"],
                "workDir": tmp,
            }
            item_result = {
                "verified": False,
                "testExit": 1,
                "testStdout": "FAIL-A\n",
                "testStderr": "",
                "error": None,
                "repairs": 0,
                "filesWritten": [],
            }
            with mock.patch.object(wave_loop, "build_manifest_item", side_effect=fake_build), \
                    mock.patch.object(wave_loop, "dispatch_item", side_effect=fake_dispatch):
                r1 = wave_loop._repair_one_round([(0, item, item_result)], None, None, _FakeClaim())
                wave_loop._repair_one_round(r1, None, None, _FakeClaim())

        self.assertEqual(len(sent_last_outputs), 2)
        # Repair round 1 carries the initial dispatch output (FAIL-A).
        self.assertIn("FAIL-A", sent_last_outputs[0])
        # Repair round 2 must carry the latest output (FAIL-B), not the stale FAIL-A.
        self.assertIn("FAIL-B", sent_last_outputs[1])
        self.assertNotIn("FAIL-A", sent_last_outputs[1])


if __name__ == "__main__":
    unittest.main()
