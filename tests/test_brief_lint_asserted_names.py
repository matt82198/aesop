#!/usr/bin/env python3
"""Tests for the brief lint: asserted strings and imported names must reach the prompt.

The lint runs in the wave loop after the item prompt is built and before the
first model call. It reads the item's acceptance test with ``ast`` (never
regex), collects string literals used as assertion arguments and names
imported from the module under test, and reports the ones the prompt is
missing. Default mode appends a "Required names and strings" section and logs
"brief-lint-added"; strict mode refuses the dispatch.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from brief_lint import (  # noqa: E402
    BriefLintRefusal,
    LOG_EVENT,
    SECTION_HEADER,
    apply_brief_lint,
    find_acceptance_test_file,
    lint_prompt,
)
import wave_loop  # noqa: E402


ACCEPTANCE_TEST = (
    "import unittest\n"
    "from wave_widget import render_banner, BannerError\n"
    "\n"
    "class TestBanner(unittest.TestCase):\n"
    "    def test_banner_has_title(self):\n"
    '        """Docstring-only phrase: Ghostly receipt wording."""\n'
    '        out = render_banner("x")\n'
    '        self.assertIn("Quarterly Receipt Summary", out)\n'
    '        self.assertEqual(out.count("|"), 2)\n'
    '        self.assertEqual(out, "ok")\n'
)


def _write_acceptance(workdir: Path) -> Path:
    tests_dir = workdir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    path = tests_dir / "test_banner.py"
    path.write_text(ACCEPTANCE_TEST, encoding="utf-8")
    return path


class TestLintPrompt(unittest.TestCase):
    """lint_prompt reports asserted literals and imported names missing from the prompt."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.test_path = _write_acceptance(Path(self._tmp.name))

    def test_asserted_string_missing_from_prompt_is_reported(self):
        prompt = "Implement render_banner and BannerError."
        missing = lint_prompt(prompt, self.test_path)
        self.assertIn("Quarterly Receipt Summary", missing)

    def test_imported_name_missing_from_prompt_is_reported(self):
        prompt = "Implement render_banner. Must include Quarterly Receipt Summary."
        missing = lint_prompt(prompt, self.test_path)
        self.assertEqual(missing, ["BannerError"])

    def test_items_present_in_prompt_are_not_reported(self):
        prompt = (
            "Implement render_banner and raise BannerError. "
            "Must include Quarterly Receipt Summary."
        )
        self.assertEqual(lint_prompt(prompt, self.test_path), [])

    def test_trivial_literals_are_ignored(self):
        # "|" (len 1) and "ok" (len 2) are asserted in the test but are trivial.
        prompt = "render_banner BannerError Quarterly Receipt Summary"
        missing = lint_prompt(prompt, self.test_path)
        self.assertNotIn("|", missing)
        self.assertNotIn("ok", missing)

    def test_docstring_and_test_name_literals_are_ignored(self):
        prompt = "render_banner BannerError Quarterly Receipt Summary"
        missing = lint_prompt(prompt, self.test_path)
        self.assertFalse(any("Ghostly" in m for m in missing))
        self.assertFalse(any("banner_has_title" in m for m in missing))


class TestFindAcceptanceTestFile(unittest.TestCase):
    """The acceptance test is found from an explicit key or a .py token in testCmd."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        self.expected = _write_acceptance(self.workdir)

    def test_explicit_key_wins(self):
        item = {"acceptanceTestFile": "tests/test_banner.py", "testCmd": "python x.py"}
        self.assertEqual(find_acceptance_test_file(item, str(self.workdir)), self.expected)

    def test_py_token_in_test_cmd_is_found(self):
        item = {"testCmd": "python tests/test_banner.py"}
        self.assertEqual(find_acceptance_test_file(item, str(self.workdir)), self.expected)

    def test_missing_file_returns_none(self):
        item = {"testCmd": "python tests/test_nope.py"}
        self.assertIsNone(find_acceptance_test_file(item, str(self.workdir)))


class TestApplyBriefLint(unittest.TestCase):
    """apply_brief_lint: default appends and logs, strict refuses, no-op when complete."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        _write_acceptance(self.workdir)
        self.state_dir = self.workdir / "state"
        self.state_dir.mkdir()

    def _item(self, prompt: str) -> dict:
        return {
            "slug": "banner",
            "prompt": prompt,
            "testCmd": "python tests/test_banner.py",
            "ownsFiles": ["wave_widget.py"],
        }

    def test_default_mode_appends_required_section_and_logs(self):
        item = self._item("Implement render_banner.")
        out = apply_brief_lint(item, str(self.workdir), str(self.state_dir), strict=False)
        self.assertIn(SECTION_HEADER, out["prompt"])
        self.assertIn("Quarterly Receipt Summary", out["prompt"])
        self.assertIn("BannerError", out["prompt"])
        log_text = (self.state_dir / "brief_lint.log").read_text(encoding="utf-8")
        self.assertIn(LOG_EVENT, log_text)
        self.assertEqual(LOG_EVENT, "brief-lint-added")

    def test_default_mode_does_not_mutate_input_item(self):
        item = self._item("Implement render_banner.")
        apply_brief_lint(item, str(self.workdir), str(self.state_dir), strict=False)
        self.assertEqual(item["prompt"], "Implement render_banner.")

    def test_complete_prompt_is_untouched_and_unlogged(self):
        prompt = (
            "render_banner BannerError Quarterly Receipt Summary"
        )
        item = self._item(prompt)
        out = apply_brief_lint(item, str(self.workdir), str(self.state_dir), strict=False)
        self.assertEqual(out["prompt"], prompt)
        self.assertFalse((self.state_dir / "brief_lint.log").exists())

    def test_strict_mode_refuses_when_names_missing(self):
        item = self._item("Implement render_banner.")
        with self.assertRaises(BriefLintRefusal) as ctx:
            apply_brief_lint(item, str(self.workdir), str(self.state_dir), strict=True)
        self.assertIn("Quarterly Receipt Summary", str(ctx.exception))

    def test_no_acceptance_file_is_a_noop(self):
        item = {"slug": "bare", "prompt": "Do a thing.", "testCmd": "python tests/none.py"}
        out = apply_brief_lint(item, str(self.workdir), str(self.state_dir), strict=True)
        self.assertEqual(out["prompt"], "Do a thing.")


class TestCallSiteStrictRefusal(unittest.TestCase):
    """_dispatch_and_journal refuses via the wave loop when strict mode is on."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        _write_acceptance(self.workdir)
        self.state_dir = self.workdir / "state"
        self.state_dir.mkdir()

    def test_strict_item_key_blocks_dispatch(self):
        item = {
            "slug": "banner",
            "prompt": "Implement render_banner.",
            "testCmd": "python tests/test_banner.py",
            "ownsFiles": ["wave_widget.py"],
            "briefLintStrict": True,
        }
        dispatch = Mock(name="dispatch_item")
        with patch.object(wave_loop, "build_manifest_item", side_effect=lambda d, i: dict(i)), \
                patch.object(wave_loop, "dispatch_item", dispatch):
            _idx, result = wave_loop._dispatch_and_journal(
                0,
                item,
                "banner",
                str(self.workdir),
                None,
                "inst-1",
                driver=Mock(name="driver"),
                state_dir=str(self.state_dir),
                pre_dispatch_repro_enabled=False,
                pre_dispatch_repro_timeout=5,
            )
        dispatch.assert_not_called()
        self.assertFalse(result.get("verified", False))


if __name__ == "__main__":
    unittest.main()
