"""Run the documented scratchpad probe with a local filename that shadows the stdlib."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


class ScratchpadProbeTest(unittest.TestCase):
    def test_documented_invocation_imports_the_standard_library(self):
        contract = (Path(__file__).resolve().parents[1] / "LANE-CONTRACT.md").read_text(encoding="utf-8")
        example = re.search(r"`python ([^\s`]+ )?probe\.py`", contract)
        self.assertIsNotNone(example, "Provide a runnable scratchpad probe example in the lane contract")
        flags = (example[1] or "").split()
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            (scratch / "enum.py").write_text("shadow = True\n", encoding="utf-8")
            (scratch / "probe.py").write_text("from enum import Enum\nprint(Enum.__name__)\n", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, *flags, "probe.py"], cwd=scratch, capture_output=True,
                text=True, timeout=10, check=False,
            )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "Enum")


if __name__ == "__main__":
    unittest.main()
