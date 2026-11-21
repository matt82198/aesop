"""Tests for tools/build_lane_contract.py and the contract/ step files.

Behavioural: the builder runs against the real repo (freshness) and against a
throwaway checkout copy (stale and missing-source detection).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import build_lane_contract as blc  # noqa: E402


def _fixture():
    root = tempfile.mkdtemp()
    shutil.copytree(os.path.join(REPO_ROOT, "contract"), os.path.join(root, "contract"))
    shutil.copy(os.path.join(REPO_ROOT, "LANE-CONTRACT.md"), root)
    return root


class FreshnessTests(unittest.TestCase):
    def test_committed_contract_is_fresh(self):
        self.assertTrue(blc.is_fresh(REPO_ROOT),
                        "run python tools/build_lane_contract.py --write")

    def test_check_cli_exit_zero_on_real_repo(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "tools", "build_lane_contract.py"), "--check"],
            capture_output=True, encoding="utf-8", errors="replace", cwd=REPO_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_edit_to_step_file_makes_contract_stale(self):
        root = _fixture()
        self.addCleanup(shutil.rmtree, root, True)
        self.assertTrue(blc.is_fresh(root))
        with open(os.path.join(root, "contract", "review.md"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n- a new review rule\n")
        self.assertFalse(blc.is_fresh(root))

    def test_missing_source_raises(self):
        root = _fixture()
        self.addCleanup(shutil.rmtree, root, True)
        os.remove(os.path.join(root, "contract", "judgement.md"))
        with self.assertRaises(FileNotFoundError):
            blc.build(root)

    def test_build_is_idempotent_and_contains_every_source(self):
        text = blc.build(REPO_ROOT)
        self.assertEqual(text, blc.build(REPO_ROOT))
        for name in blc.ORDER:
            body = blc._read(os.path.join(REPO_ROOT, "contract", name + ".md")).strip("\n")
            first = body.split("\n", 2)[-1] if name == "floor" else body
            self.assertIn(first.split("\n")[0], text)


class SplitShapeTests(unittest.TestCase):
    def _lines(self, name):
        text = blc._read(os.path.join(REPO_ROOT, "contract", name + ".md"))
        return [ln.strip() for ln in text.split("\n") if len(ln.strip()) >= 40]

    def test_floor_text_is_not_duplicated_in_any_other_file(self):
        floor = set(self._lines("floor"))
        for name in blc.ORDER[1:]:
            dup = floor.intersection(self._lines(name))
            self.assertEqual(dup, set(), name + " repeats floor lines")

    def test_each_step_file_has_a_step_marker_and_is_smaller_than_full(self):
        full = len(blc.build(REPO_ROOT).encode("utf-8"))
        for step, size in blc.sizes(REPO_ROOT).items():
            if step == "full":
                continue
            self.assertLess(size, full, step)
            text = blc._read(os.path.join(REPO_ROOT, "contract", step + ".md"))
            self.assertIn("# STEP: " + step, text)

    def test_receipt_text_matches_the_pre_push_hook(self):
        floor = blc._read(os.path.join(REPO_ROOT, "contract", "floor.md"))
        hook = blc._read(os.path.join(REPO_ROOT, "hooks", "pre-push-policy.sh"))
        self.assertIn("AESOP_RECEIPT_EMIT=0` is INERT", floor)
        self.assertIn("receipt_emit_optout_ignored", hook)
        judgement = blc._read(os.path.join(REPO_ROOT, "contract", "judgement.md"))
        self.assertNotIn("emits a signed receipt automatically (`hooks", judgement)

    def test_usage_error_exit_two(self):
        self.assertEqual(blc.main([]), 2)
        self.assertEqual(blc.main(["--bogus"]), 2)


if __name__ == "__main__":
    unittest.main()
