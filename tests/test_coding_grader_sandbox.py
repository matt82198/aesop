#!/usr/bin/env python3
"""Regression tests: candidate code must not inherit credentials or the repo cwd.

The grader runs model-generated code. These tests drive grade_solution() with a
harmless sentinel variable set in the test process (never a real key) and assert
the candidate cannot see it, and that the candidate does not start in the repo.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent.parent / "bench"
sys.path.insert(0, str(BENCH_DIR))

from coding_grader import HIDDEN_TEST_CASES, grade_solution  # noqa: E402

SENTINELS = ("BENCH_TEST_SENTINEL", "BENCH_TEST_SENTINEL_TOKEN")
_TEST_CWD = os.path.realpath(os.getcwd())


class TestCandidateEnvironment(unittest.TestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in SENTINELS}
        for k in SENTINELS:
            os.environ[k] = "1"
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _write_solution(self, body: str) -> str:
        path = Path(self._tmp.name) / "solution.py"
        path.write_text(f"def is_prime(n):\n{body}", encoding="utf-8")
        return str(path)

    def _actuals(self, result):
        return [r["actual"] for r in result["results"] if "actual" in r]

    def test_sentinel_is_set_in_test_process(self):
        """Control: the sentinel really exists in this process's environment."""
        self.assertEqual(os.environ.get("BENCH_TEST_SENTINEL"), "1")
        out = subprocess.run(
            [sys.executable, "-c", "import os; print(os.environ.get('BENCH_TEST_SENTINEL', 'absent'))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, env=dict(os.environ),
        )
        self.assertEqual(out.stdout.strip(), "1")

    def test_candidate_cannot_read_sentinel_env(self):
        solution = self._write_solution(
            "    import os\n"
            "    return any(k.startswith('BENCH_TEST_SENTINEL') for k in os.environ)\n"
        )
        result = grade_solution("find_prime", solution)
        actuals = self._actuals(result)
        self.assertEqual(len(actuals), len(HIDDEN_TEST_CASES["find_prime"]), result)
        self.assertTrue(all(v is False for v in actuals),
                        f"candidate saw sentinel env vars: {actuals}")

    def test_candidate_does_not_start_in_repo_cwd(self):
        solution = self._write_solution(
            "    import os\n"
            f"    return os.path.realpath(os.getcwd()) != {_TEST_CWD!r}\n"
        )
        result = grade_solution("find_prime", solution)
        actuals = self._actuals(result)
        self.assertEqual(len(actuals), len(HIDDEN_TEST_CASES["find_prime"]), result)
        self.assertTrue(all(v is True for v in actuals),
                        f"candidate started in the grader's cwd: {actuals}")

    def test_scrubbed_env_has_path_and_no_secret_names(self):
        import coding_grader

        env = coding_grader._candidate_env(self._tmp.name)
        self.assertIn("PATH", env)
        for name in env:
            upper = name.upper()
            self.assertFalse(
                any(tag in upper for tag in ("KEY", "TOKEN", "SECRET", "PASSWORD")),
                f"secret-like variable leaked into candidate env: {name}",
            )
        for k in SENTINELS:
            self.assertNotIn(k, env)


if __name__ == "__main__":
    unittest.main()
