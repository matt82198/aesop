"""F-03 red/green: wave_loop pre-dispatch test runs must honour their timeout.

A grandchild that outlives the shell must not stall the wave. On Windows a
plain subprocess.run(shell=True, timeout=N) kills only cmd.exe and then blocks
on the orphaned grandchild's inherited pipes; proc_util.run_shell_bounded kills
the whole tree and returns. The pre-dispatch repro must route through it.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO / "driver"), str(REPO / "tools"), str(REPO / "state_store"), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wave_loop  # noqa: E402

GRANDCHILD_SECONDS = 40
TIMEOUT_S = 2
WALL_BOUND_S = 5.0


class TestPreDispatchTestTimeoutBounded(unittest.TestCase):
    def test_pretest_repro_returns_within_bound_when_grandchild_outlives_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = '"%s" -c "import time; time.sleep(%d)"' % (sys.executable, GRANDCHILD_SECONDS)
            start = time.monotonic()
            output, passed = wave_loop._run_and_capture_test_output(
                tmp, cmd, timeout_sec=TIMEOUT_S
            )
            wall = time.monotonic() - start
        self.assertLess(
            wall, WALL_BOUND_S,
            "pre-dispatch test ran %.1fs under a %ds timeout (bound %.1fs)"
            % (wall, TIMEOUT_S, WALL_BOUND_S),
        )
        self.assertEqual((output, passed), ("", False))

    def test_pretest_passing_command_still_reports_pass(self):
        # Control: the bounded path must not change the success contract.
        with tempfile.TemporaryDirectory() as tmp:
            output, passed = wave_loop._run_and_capture_test_output(
                tmp, '"%s" -c "print(1)"' % sys.executable, timeout_sec=30
            )
        self.assertEqual((output, passed), ("", True))


if __name__ == "__main__":
    unittest.main()
