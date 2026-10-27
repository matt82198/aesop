#!/usr/bin/env python3
"""
Regression tests: a vite-rebuilt ui/web dist is not a ui/ code change.

A dependabot ui/web bump re-cut contains only build output (ui/web/dist/...)
plus package manifests (ui/web/package.json, ui/web/package-lock.json). None of
those are hand-written ui/ code, so the pre-push sync gate must not demand a
ui/CLAUDE.md edit for them. A real source change under ui/web/src/ must still
report drift (positive control, fail-closed).

TDD: the dist-only test is expected to FAIL on origin/main (RED) until the gate
exempts build output. The positive control must PASS on origin/main.
"""

import sys
import unittest
from pathlib import Path


class TestUiDistOnlyIsNotDrift(unittest.TestCase):
    """Dist rebuilds and manifest bumps under ui/web/ must not read as ui/ drift."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        import claudemd_sync_gate

        cls.gate = claudemd_sync_gate

    def _drift_for(self, changed_files):
        """Classify a change set and run the gate's drift computation (same path as main())."""
        classified = self.gate.classify_changes(changed_files)
        findings, exit_code = self.gate.check_domain_claudemd_sync(Path("."), classified)
        return findings, exit_code

    def test_dist_rebuild_with_manifest_bump_is_not_ui_drift(self):
        """A vite dist rebuild plus ui/web manifest bumps must NOT report ui/ drift.

        Regression for the dependabot ui/web re-cut blocked with
        '[DRIFT] 1 domain(s) ... ui/CLAUDE.md' (recut/1299-uiweb-minor-patch, 9fe68250).
        """
        changed_files = [
            "ui/web/dist/index.html",
            "ui/web/dist/assets/index-abc.js",
            "ui/web/package.json",
            "ui/web/package-lock.json",
        ]
        findings, exit_code = self._drift_for(changed_files)
        self.assertEqual(
            findings,
            [],
            f"dist-only ui/web change reported as drift: {findings}",
        )
        self.assertEqual(exit_code, 0)

    def test_ui_source_change_without_claudemd_still_reports_drift(self):
        """Positive control: a real ui/web source change with no ui/CLAUDE.md edit is drift."""
        changed_files = ["ui/web/src/App.tsx"]
        findings, exit_code = self._drift_for(changed_files)
        self.assertEqual(exit_code, 1, f"expected drift for ui source change, got: {findings}")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["domain"], "ui")
        self.assertEqual(findings[0]["changed_files"], ["ui/web/src/App.tsx"])


if __name__ == "__main__":
    unittest.main()
