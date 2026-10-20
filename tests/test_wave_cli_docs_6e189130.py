"""Behavioural tests for the CLI surface wave_history and wave_preflight advertise.

Tracker item 6e189130e166: the tool docstrings (which feed tools/INDEX.md and --help)
advertise flags the CLI rejects. wave_history.py's index line advertises
`--check | --dry-run`, which argparse rejects with exit 2; wave_preflight.py documents
`--text` and `--help`, which fall through to "Unknown argument" with exit 2.

The tests drive the real CLIs in a subprocess and assert that every flag the docstring
advertises is accepted by the parser. They never grep the source for specific text.
"""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "tools" / "wave_history.py"
PREFLIGHT = ROOT / "tools" / "wave_preflight.py"

# Flags that take a value. Any value works: the test only checks parser acceptance.
VALUE_FLAGS = {"--latest", "--state-root", "--root", "--tracker", "--ledger"}

# Substrings argparse or the hand-rolled parser print when they reject an argument.
REJECTION_MARKERS = (
    "Unknown argument",
    "unrecognized arguments",
    "expected one argument",
    "invalid int value",
)


def _advertised_flags(script: Path) -> list[str]:
    """Return the long flags named in the script's module docstring, sorted and unique."""
    text = script.read_text(encoding="utf-8")
    doc = text.split('"""', 2)[1]
    return sorted(set(re.findall(r"--[a-z][a-z-]*", doc)))


def _run(script: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    """Run a tool with -I (isolated mode) under the current interpreter."""
    return subprocess.run(
        [sys.executable, "-I", str(script), *args],
        input=stdin,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=120,
    )


def _rejected(proc: subprocess.CompletedProcess) -> bool:
    """True when the parser refused an argument (not when the tool ran and reported a result)."""
    return any(marker in proc.stderr for marker in REJECTION_MARKERS)


class WaveHistoryAdvertisedFlagsTest(unittest.TestCase):
    """Every flag in wave_history.py's docstring must be accepted by its CLI."""

    def test_every_advertised_flag_is_accepted(self):
        state_dir = tempfile.mkdtemp()
        flags = _advertised_flags(HISTORY)
        self.assertTrue(flags, "docstring advertises no flags; the parity check would be vacuous")
        for flag in flags:
            with self.subTest(flag=flag):
                args = [flag]
                if flag in VALUE_FLAGS:
                    args.append("1" if flag == "--latest" else state_dir)
                proc = _run(HISTORY, *args)
                self.assertFalse(
                    _rejected(proc),
                    f"{flag} is advertised by wave_history.py but rejected (exit {proc.returncode}): "
                    f"{proc.stderr.strip()}",
                )


class WaveHistoryHelpTest(unittest.TestCase):
    """--help must exit 0 and print usage."""

    def test_help_exits_zero(self):
        proc = _run(HISTORY, "--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--json", proc.stdout)


class WaveHistoryParityTest(unittest.TestCase):
    """The advertised exit-code contract must match what the tool does for a usage error."""

    def test_unknown_flag_is_a_usage_error(self):
        proc = _run(HISTORY, "--no-such-flag-6e189130")
        self.assertEqual(proc.returncode, 2, proc.stderr)


class WavePreflightAdvertisedFlagsTest(unittest.TestCase):
    """Every flag in wave_preflight.py's docstring must be accepted by its CLI."""

    def test_every_advertised_flag_is_accepted(self):
        scratch = tempfile.mkdtemp()
        flags = _advertised_flags(PREFLIGHT)
        self.assertTrue(flags, "docstring advertises no flags; the parity check would be vacuous")
        for flag in flags:
            with self.subTest(flag=flag):
                args = [flag]
                if flag in VALUE_FLAGS:
                    args.append(scratch)
                proc = _run(PREFLIGHT, *args, stdin="")
                self.assertFalse(
                    _rejected(proc),
                    f"{flag} is advertised by wave_preflight.py but rejected (exit {proc.returncode}): "
                    f"{proc.stderr.strip()}",
                )


class WavePreflightTextFlagTest(unittest.TestCase):
    """--text is documented as the default output mode; it must run the text report."""

    def test_text_flag_runs_text_report(self):
        proc = _run(PREFLIGHT, "--text")
        self.assertFalse(_rejected(proc), proc.stderr)
        # 0 = ready, 1 = blocked; anything else means the tool did not produce a verdict.
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        self.assertIn("1.", proc.stdout, "text report should print a numbered list of checks")


class WavePreflightHelpTest(unittest.TestCase):
    """--help must exit 0 and print the usage block."""

    def test_help_exits_zero_with_usage(self):
        proc = _run(PREFLIGHT, "--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Usage:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
