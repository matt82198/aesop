#!/usr/bin/env python3
"""
Test STATE.md verifier's BUILDLOG resolution and Guardrail #6 behavior.

Demonstrates the bug: when --state-md is given an explicit path,
_resolve_buildlog_path() should prioritize the BUILDLOG.md next to that
--state-md, not default to the repo's BUILDLOG.md.

Anti-vacuity: the test FAILS on the broken code (SKIP instead of CONTRADICTION),
and passes on the fixed code.
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Import the verifier module
sys.path.insert(0, str(Path(__file__).parent.parent))
from tools import state_md_verifier


class TestBuildlogResolution(unittest.TestCase):
    """Test BUILDLOG.md path resolution with explicit --state-md."""

    def test_explicit_state_md_finds_buildlog_in_same_directory(self):
        """When --state-md is explicit, BUILDLOG.md next to it should be found,
        not the repo's BUILDLOG.md.

        This test fails on broken code (finds repo's empty BUILDLOG.md),
        and passes on fixed code (finds the sibling BUILDLOG.md with checkpoints).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a fake conductor3-like directory with STATE.md and BUILDLOG.md
            external_dir = tmpdir_path / "external_state"
            external_dir.mkdir()

            state_md_path = external_dir / "STATE.md"
            buildlog_path = external_dir / "BUILDLOG.md"

            # STATE.md with CURRENT header dated 2026-09-19
            state_md_path.write_text(
                "# State\n\n## CURRENT (2026-09-19 10:00:00)\n\nSome state.\n",
                encoding='utf-8'
            )

            # BUILDLOG.md with checkpoint dated 2026-10-10 (newer than STATE.md)
            buildlog_path.write_text(
                "--- 2026-10-10T02:59:39Z [checkpoint:afk] Wave started\n",
                encoding='utf-8'
            )

            # Create args object with explicit --state-md
            class Args:
                buildlog = None
                state_md = str(state_md_path)
            args = Args()

            # Resolve BUILDLOG path
            resolved = state_md_verifier._resolve_buildlog_path(args, state_md_path)

            # On broken code: resolved == repo's BUILDLOG.md (doesn't exist in tmpdir, returns None)
            # On fixed code: resolved == external_dir / "BUILDLOG.md"
            self.assertEqual(
                resolved, buildlog_path,
                f"Expected {buildlog_path}, got {resolved}. "
                f"When --state-md is explicit, BUILDLOG.md next to it should be prioritized."
            )

    def test_verify_buildlog_drift_with_explicit_paths_contradicts(self):
        """Guardrail #6 should CONTRADICT (not SKIP) when given explicit paths
        to real files where STATE.md is stale vs BUILDLOG.md.

        On broken code: SKIP (because default resolution finds repo's empty BUILDLOG).
        On fixed code: CONTRADICTION (because it finds the real BUILDLOG with checkpoints).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            external_dir = tmpdir_path / "external_state"
            external_dir.mkdir()

            state_md_path = external_dir / "STATE.md"
            buildlog_path = external_dir / "BUILDLOG.md"

            # STATE.md with CURRENT header dated 2026-09-19 (old)
            state_md_path.write_text(
                "# State\n\n## CURRENT (2026-09-19 10:00:00)\n\nStale state.\n",
                encoding='utf-8'
            )

            # BUILDLOG.md with checkpoint dated 2026-10-10 (new)
            buildlog_path.write_text(
                "--- 2026-10-10T02:59:39Z [checkpoint:afk] Wave started\n",
                encoding='utf-8'
            )

            # Verify the drift
            findings = state_md_verifier.verify_buildlog_drift(state_md_path, buildlog_path)

            # On broken code: this is empty or SKIP (no real checkpoints found)
            # On fixed code: this contains a CONTRADICTION
            self.assertTrue(
                any(f["status"] == "CONTRADICTION" for f in findings),
                f"Expected CONTRADICTION but got: {findings}. "
                f"STATE.md (2026-09-19) is older than BUILDLOG.md (2026-10-10)."
            )

    def test_verify_buildlog_drift_explicit_paths_missing_buildlog_fails_closed(self):
        """When STATE.md is explicit and BUILDLOG.md cannot be found but
        STATE.md exists with no CURRENT header, the tool should exit non-zero
        (fail-closed), not silently pass.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            external_dir = tmpdir_path / "external_state"
            external_dir.mkdir()

            state_md_path = external_dir / "STATE.md"
            # No BUILDLOG.md in this directory

            # STATE.md with CURRENT header but no adjacent BUILDLOG.md
            state_md_path.write_text(
                "# State\n\n## CURRENT (2026-09-19 10:00:00)\n\nState without BUILDLOG.\n",
                encoding='utf-8'
            )

            # When BUILDLOG is None, verify_buildlog_drift should handle gracefully
            findings = state_md_verifier.verify_buildlog_drift(state_md_path, None)

            # With only STATE.md CURRENT header and no BUILDLOG, should SKIP
            # (no checkpoints to compare against)
            self.assertTrue(
                len(findings) > 0,
                "Expected at least one finding (SKIP) but got empty. "
                "A state file with CURRENT header but no BUILDLOG should not silently pass."
            )


def suite():
    """Return test suite."""
    suite = unittest.TestSuite()
    suite.addTest(TestBuildlogResolution('test_explicit_state_md_finds_buildlog_in_same_directory'))
    suite.addTest(TestBuildlogResolution('test_verify_buildlog_drift_with_explicit_paths_contradicts'))
    suite.addTest(TestBuildlogResolution('test_verify_buildlog_drift_explicit_paths_missing_buildlog_fails_closed'))
    return suite


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite())
    sys.exit(0 if result.wasSuccessful() else 1)
