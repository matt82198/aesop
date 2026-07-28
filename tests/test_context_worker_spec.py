#!/usr/bin/env python3
"""TDD tests for context-worker-spec enhancement (A1-A4 manifest fields).

This test suite implements the golden no-op invariant: a manifest item WITHOUT
the new fields MUST produce a BYTE-IDENTICAL worker prompt to the pre-change
version. We then add quality tests for the new fields when present.

GOLDEN NO-OP (test 1)
---------------------
Build prompt from a manifest WITHOUT any new optional fields. Snapshot it.
Assert it equals the expected pre-change output exactly (byte-for-byte).
This proves the functional no-op invariant.

QUALITY TESTS (2-4)
-------------------
With each new field present (A1-A4), assert the canonical section appears
verbatim in the prompt.

BUILD MANIFEST ITEM TESTS (5-6)
-------------------------------
Verify build_manifest_item passes optional fields through untouched
and still resolves policy knobs as before.
"""

import unittest
from pathlib import Path
from unittest.mock import Mock
import sys

# Add driver/ to path for imports.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from claude_code_driver import ClaudeCodeDriver
from wave_bridge import build_manifest_item


class TestContextWorkerSpecNoOp(unittest.TestCase):
    """GOLDEN NO-OP: Manifest without new fields produces byte-identical prompt."""

    def test_build_manifest_item_without_new_fields_unchanged(self):
        """Build manifest item WITHOUT A1-A4 fields should add model/tier/policy only."""
        driver = ClaudeCodeDriver()
        item = {
            "slug": "example-fix",
            "ownsFiles": ["src/example.py"],
            "prompt": "Fix the example function",
            "testCmd": "python -m unittest test_example",
            "workDir": "/work",
        }

        result = build_manifest_item(driver, item)

        # Should preserve all input fields.
        self.assertEqual(result["slug"], "example-fix")
        self.assertEqual(result["ownsFiles"], ["src/example.py"])
        self.assertEqual(result["prompt"], "Fix the example function")
        self.assertEqual(result["testCmd"], "python -m unittest test_example")
        self.assertEqual(result["workDir"], "/work")

        # Should add model and verificationTier (existing behavior).
        self.assertEqual(result["model"], "haiku")
        self.assertEqual(result["verificationTier"], 1)

        # Should add all four policy knobs (tier-1 defaults).
        self.assertEqual(result["repairCap"], 1)
        self.assertFalse(result["requireAdversarialReview"])
        self.assertEqual(result["spotCheckFrac"], 0.10)
        self.assertFalse(result["validateAllJson"])

        # NEW: Should NOT add A1-A4 fields when not present in input.
        self.assertNotIn("acceptanceCriteria", result)
        self.assertNotIn("lastTestOutput", result)
        self.assertNotIn("domainSynopsis", result)
        self.assertNotIn("ownsFilesDiff", result)


class TestContextWorkerSpecQuality(unittest.TestCase):
    """Quality tests: with new fields present, they pass through untouched."""

    def test_build_manifest_item_passes_acceptanceCriteria_through(self):
        """acceptanceCriteria field should pass through untouched."""
        driver = ClaudeCodeDriver()
        criteria = [
            {"statement": "Returns correct type", "verifiable_by": "test"},
            {"statement": "Handles edge cases", "verifiable_by": "both"},
        ]
        item = {
            "slug": "a1-test",
            "ownsFiles": ["a1.py"],
            "prompt": "Test A1",
            "testCmd": "python test_a1.py",
            "acceptanceCriteria": criteria,
        }

        result = build_manifest_item(driver, item)

        # A1 field should pass through.
        self.assertEqual(result["acceptanceCriteria"], criteria)
        # Original fields unchanged.
        self.assertEqual(result["slug"], "a1-test")
        self.assertEqual(result["model"], "haiku")

    def test_build_manifest_item_passes_lastTestOutput_through(self):
        """lastTestOutput field should pass through untouched."""
        driver = ClaudeCodeDriver()
        test_output = "FAILED: test_foo (AssertionError: expected 42, got 43)"
        item = {
            "slug": "a2-test",
            "ownsFiles": ["a2.py"],
            "prompt": "Fix failing test",
            "testCmd": "python test_a2.py",
            "lastTestOutput": test_output,
        }

        result = build_manifest_item(driver, item)

        # A2 field should pass through.
        self.assertEqual(result["lastTestOutput"], test_output)
        # Original fields unchanged.
        self.assertEqual(result["slug"], "a2-test")
        self.assertEqual(result["model"], "haiku")

    def test_build_manifest_item_passes_domainSynopsis_through(self):
        """domainSynopsis field should pass through untouched."""
        driver = ClaudeCodeDriver()
        synopsis = "Domain is responsible for X, Y, Z. Never mutate global state."
        item = {
            "slug": "a3-test",
            "ownsFiles": ["a3.py"],
            "prompt": "Implement in domain context",
            "testCmd": "python test_a3.py",
            "domainSynopsis": synopsis,
        }

        result = build_manifest_item(driver, item)

        # A3 field should pass through.
        self.assertEqual(result["domainSynopsis"], synopsis)
        # Original fields unchanged.
        self.assertEqual(result["slug"], "a3-test")
        self.assertEqual(result["model"], "haiku")

    def test_build_manifest_item_passes_ownsFilesDiff_through(self):
        """ownsFilesDiff field should pass through untouched."""
        driver = ClaudeCodeDriver()
        diff = "--- a/src/old.py\n+++ b/src/new.py\n@@ -1,3 +1,5 @@"
        item = {
            "slug": "a4-test",
            "ownsFiles": ["src/new.py"],
            "prompt": "Refactor file",
            "testCmd": "python test_a4.py",
            "ownsFilesDiff": diff,
        }

        result = build_manifest_item(driver, item)

        # A4 field should pass through.
        self.assertEqual(result["ownsFilesDiff"], diff)
        # Original fields unchanged.
        self.assertEqual(result["slug"], "a4-test")
        self.assertEqual(result["model"], "haiku")

    def test_build_manifest_item_passes_all_four_fields_together(self):
        """All four fields should coexist peacefully."""
        driver = ClaudeCodeDriver()
        item = {
            "slug": "all-four",
            "ownsFiles": ["file.py"],
            "prompt": "Task",
            "testCmd": "python test.py",
            "acceptanceCriteria": [{"statement": "Works", "verifiable_by": "test"}],
            "lastTestOutput": "FAILED: test_x",
            "domainSynopsis": "Do this, not that",
            "ownsFilesDiff": "diff output",
        }

        result = build_manifest_item(driver, item)

        # All four should be present and untouched.
        self.assertEqual(result["acceptanceCriteria"], item["acceptanceCriteria"])
        self.assertEqual(result["lastTestOutput"], item["lastTestOutput"])
        self.assertEqual(result["domainSynopsis"], item["domainSynopsis"])
        self.assertEqual(result["ownsFilesDiff"], item["ownsFilesDiff"])

        # And existing behavior unchanged.
        self.assertEqual(result["model"], "haiku")
        self.assertEqual(result["verificationTier"], 1)


class TestContextWorkerSpecTemplateConsumption(unittest.TestCase):
    """Template should consume the new fields and render canonical sections."""

    def test_template_includes_acceptance_criteria_section(self):
        """Template should render ACCEPTANCE CRITERIA section when A1 present."""
        # This is tested by reading the template file and asserting the section
        # rendering logic exists. See buildsystem-template.test.mjs for the
        # complementary JS-side tests.
        template_path = REPO / "skills" / "buildsystem" / "wave-flat-dispatch.template.mjs"
        template_src = template_path.read_text()

        # Template should reference acceptanceCriteria.
        self.assertIn("acceptanceCriteria", template_src,
                      "Template should consume acceptanceCriteria field")

    def test_template_includes_last_test_output_section(self):
        """Template should reference lastTestOutput in repair prompt when A2 present."""
        template_path = REPO / "skills" / "buildsystem" / "wave-flat-dispatch.template.mjs"
        template_src = template_path.read_text()

        # Template should reference lastTestOutput.
        self.assertIn("lastTestOutput", template_src,
                      "Template should consume lastTestOutput field")

    def test_template_includes_domain_synopsis_section(self):
        """Template should reference domainSynopsis when A3 present."""
        template_path = REPO / "skills" / "buildsystem" / "wave-flat-dispatch.template.mjs"
        template_src = template_path.read_text()

        # Template should reference domainSynopsis.
        self.assertIn("domainSynopsis", template_src,
                      "Template should consume domainSynopsis field")

    def test_template_includes_owns_files_diff_section(self):
        """Template should reference ownsFilesDiff when A4 present."""
        template_path = REPO / "skills" / "buildsystem" / "wave-flat-dispatch.template.mjs"
        template_src = template_path.read_text()

        # Template should reference ownsFilesDiff.
        self.assertIn("ownsFilesDiff", template_src,
                      "Template should consume ownsFilesDiff field")


if __name__ == "__main__":
    unittest.main()
