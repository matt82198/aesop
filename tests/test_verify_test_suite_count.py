#!/usr/bin/env python3
"""
Test suite for verify_test_suite_count.py (CI-shard-coverage gate).

Structural fix #830 ("guard: compute suite counts live") removed the
committed tests/SUITE-COUNTS.json artifact this tool used to gate (two clean
merges drifted it anyway -- PR #828 postmortem -- and nothing but the
generator/gate/registry triangle ever consumed its committed value). This
tool is repointed at a distinct, still-real risk the old count-drift check
never caught: the CI workflow's own shard matrix (`.github/workflows/ci.yml`)
drifting out of sync with the `total_shards` argument `ci_shard_runner.py` is
invoked with, which leaves a shard index no job ever requests -- so every
test file round-robin-assigned to it is silently never executed in CI, even
though the file is tracked and would normally be collected. That is the
modern instance of the original PR #605 fake-green class ("a test file is
added but not picked up by CI") that survives even with counts computed
fresh on every call.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TOOL = REPO_ROOT / "tools" / "verify_test_suite_count.py"

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("verify_test_suite_count_under_test", TOOL)
vtsc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vtsc)


def _write_ci_yml(path: Path, jobs_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("name: ci\non: [push]\njobs:\n" + jobs_text, encoding="utf-8")


def _good_job(total: int = 4, ids=None) -> str:
    ids = list(range(total)) if ids is None else ids
    ids_str = ", ".join(str(i) for i in ids)
    return (
        "  test:\n"
        "    strategy:\n"
        "      matrix:\n"
        f"        python-shard: [{ids_str}]\n"
        "    steps:\n"
        f"      - run: python tools/ci_shard_runner.py ${{{{ matrix.python-shard }}}} {total}\n"
    )


class TestParsing(unittest.TestCase):
    """White-box tests for the pure regex/pairing helpers."""

    def test_parse_matrix_arrays_extracts_key_and_ids(self):
        text = "        python-shard: [0, 1, 2, 3]\n"
        found = vtsc.parse_matrix_arrays(text)
        self.assertEqual(len(found), 1)
        key, ids, _pos = found[0]
        self.assertEqual(key, "python-shard")
        self.assertEqual(ids, [0, 1, 2, 3])

    def test_parse_shard_invocations_extracts_key_and_total(self):
        text = "run: python tools/ci_shard_runner.py ${{ matrix.python-shard }} 4 --timing-file x\n"
        found = vtsc.parse_shard_invocations(text)
        self.assertEqual(len(found), 1)
        key, total, _pos = found[0]
        self.assertEqual(key, "python-shard")
        self.assertEqual(total, 4)

    def test_pairing_uses_nearest_preceding_matrix(self):
        text = _good_job(total=4)
        pairs = vtsc.pair_invocations_with_matrices(text)
        self.assertEqual(len(pairs), 1)
        key, ids, total = pairs[0]
        self.assertEqual(key, "python-shard")
        self.assertEqual(ids, [0, 1, 2, 3])
        self.assertEqual(total, 4)

    def test_invocation_with_no_preceding_matrix_is_skipped(self):
        text = "steps:\n  - run: python tools/ci_shard_runner.py ${{ matrix.python-shard }} 4\n"
        pairs = vtsc.pair_invocations_with_matrices(text)
        self.assertEqual(pairs, [])

    def test_find_shard_gaps_detects_missing_id(self):
        gaps = vtsc.find_shard_gaps([("python-shard", [0, 1, 2], 4)])
        self.assertEqual(len(gaps), 1)
        self.assertIn("3", gaps[0])

    def test_find_shard_gaps_clean_on_contiguous_ids(self):
        gaps = vtsc.find_shard_gaps([("python-shard", [0, 1, 2, 3], 4)])
        self.assertEqual(gaps, [])

    def test_find_shard_gaps_detects_nonpositive_total(self):
        gaps = vtsc.find_shard_gaps([("python-shard", [0], 0)])
        self.assertEqual(len(gaps), 1)


class TestCheckModeEndToEnd(unittest.TestCase):
    """Black-box subprocess tests against real fixture repos."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        (self.repo_root / "tests").mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=str(self.repo_root),
                        check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo_root), "config",
                         "user.email", "tester@example.invalid"],
                        check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo_root), "config",
                         "user.name", "tester"], check=True, capture_output=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _add_python_tests(self, n: int):
        for i in range(n):
            (self.repo_root / "tests" / f"test_fixture_{i}.py").write_text(
                "# fixture\n", encoding="utf-8"
            )

    def _commit(self):
        subprocess.run(["git", "-C", str(self.repo_root), "add", "-A"],
                        check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo_root), "commit", "-q", "-m", "fixture"],
                        check=True, capture_output=True)

    def _run(self, *extra_args):
        cmd = [sys.executable, str(TOOL), "--check", "--repo", str(self.repo_root)]
        cmd.extend(extra_args)
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               timeout=30)

    def test_no_ci_config_passes(self):
        """A repo with no .github/workflows/ci.yml has nothing to verify."""
        self._add_python_tests(3)
        self._commit()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not found", result.stdout)

    def test_ci_config_with_no_shard_runner_passes(self):
        """ci.yml with no ci_shard_runner.py step is a different CI shape, not a failure."""
        _write_ci_yml(self.repo_root / ".github" / "workflows" / "ci.yml",
                      "  test:\n    steps:\n      - run: echo hi\n")
        self._add_python_tests(3)
        self._commit()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing to verify", result.stdout)

    def test_contiguous_matrix_covers_all_tracked_files(self):
        """GREEN: matrix ids exactly 0..total-1 fully covers every tracked test file."""
        _write_ci_yml(self.repo_root / ".github" / "workflows" / "ci.yml",
                      _good_job(total=4))
        self._add_python_tests(10)
        self._commit()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fully cover", result.stdout)

    def test_red_first_gap_in_matrix_drops_tracked_files(self):
        """RED-FIRST: matrix configures shard ids [0,1,2] but total_shards=4 is
        passed to ci_shard_runner.py -- shard index 3 never runs in CI, so any
        tracked test file round-robin-assigned to it is silently dropped. This
        reproduces "a test file is added but not picked up by CI shards" at the
        CI-configuration level, the thing this gate exists to catch now that
        there is no stored count to compare against.
        """
        _write_ci_yml(self.repo_root / ".github" / "workflows" / "ci.yml",
                      _good_job(total=4, ids=[0, 1, 2]))
        # Enough tracked files that at least one lands in the missing bucket
        # (index 3 mod 4) deterministically.
        self._add_python_tests(8)
        self._commit()

        result = self._run()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("never run in CI", result.stderr)
        self.assertIn("not assigned to any", result.stderr)

    def test_fix_confirms_green_after_matrix_corrected(self):
        """After the red-first gap, widening the matrix to 0..3 clears the gate."""
        ci_path = self.repo_root / ".github" / "workflows" / "ci.yml"
        _write_ci_yml(ci_path, _good_job(total=4, ids=[0, 1, 2]))
        self._add_python_tests(8)
        self._commit()
        self.assertEqual(self._run().returncode, 1)

        _write_ci_yml(ci_path, _good_job(total=4, ids=[0, 1, 2, 3]))
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_multiple_jobs_each_validated_independently(self):
        """One good job and one gapped job in the same ci.yml: still caught."""
        jobs = _good_job(total=4, ids=[0, 1, 2, 3]).replace("test:", "ubuntu:", 1)
        jobs += (
            "  windows:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        python-shard: [0, 1]\n"
            "    steps:\n"
            "      - run: python tools/ci_shard_runner.py ${{ matrix.python-shard }} 3\n"
        )
        _write_ci_yml(self.repo_root / ".github" / "workflows" / "ci.yml", jobs)
        self._add_python_tests(6)
        self._commit()

        result = self._run()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("python-shard", result.stderr)

    def test_ci_config_flag_overrides_default_path(self):
        """--ci-config points at an arbitrary file, not just the default location."""
        alt_path = self.repo_root / "alt-ci.yml"
        _write_ci_yml(alt_path, _good_job(total=4, ids=[0, 1, 2]))
        self._add_python_tests(8)
        self._commit()

        result = self._run("--ci-config", str(alt_path))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_strict_is_accepted_as_alias(self):
        _write_ci_yml(self.repo_root / ".github" / "workflows" / "ci.yml",
                      _good_job(total=4))
        self._add_python_tests(3)
        self._commit()
        cmd = [sys.executable, str(TOOL), "--strict", "--repo", str(self.repo_root)]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_regenerate_and_fix_flags_no_longer_exist(self):
        """There is nothing left to regenerate; argparse rejects the old write flags."""
        for flag in ("--regenerate", "--fix", "--dry-run", "--claudemd"):
            cmd = [sys.executable, str(TOOL), flag, "--repo", str(self.repo_root)]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(result.returncode, 0,
                                 f"{flag} should no longer be a recognised argument")

    def test_fail_closed_on_non_git_repo(self):
        """Cannot-evaluate (exit 2) when the shard config is found but the repo
        is not a usable git work tree -- never silently reads as covered."""
        non_git = Path(tempfile.mkdtemp())
        try:
            _write_ci_yml(non_git / ".github" / "workflows" / "ci.yml",
                          _good_job(total=4))
            (non_git / "tests").mkdir(parents=True, exist_ok=True)
            (non_git / "tests" / "test_x.py").write_text("# x\n", encoding="utf-8")

            cmd = [sys.executable, str(TOOL), "--check", "--repo", str(non_git)]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 2, result.stderr)
        finally:
            import shutil
            shutil.rmtree(non_git, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
