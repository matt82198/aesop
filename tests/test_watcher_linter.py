"""Tests for tools.watcher_linter -- watcher/polling anti-pattern linter (Guardrail G3).

Covers the AST anti-pattern detectors (while-True+sleep, watcher-named
infinite loops, subprocess-in-loop), the prompt-string pattern detectors,
`# watcher-ok` suppression, and the CLI's --json output shape.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.watcher_linter import (
    find_prompt_patterns,
    find_subprocess_in_infinite_loop,
    find_watcher_named_infinite_loops,
    find_while_true_sleep,
    lint_paths,
    lint_source,
)


class WatcherLinterTest(unittest.TestCase):
    """Core detector behavior against in-memory fixture source."""

    def test_detects_while_true_sleep(self):
        """`while True:` with a time.sleep() body call is flagged."""
        source = (
            "import time\n"
            "def run():\n"
            "    while True:\n"
            "        do_work()\n"
            "        time.sleep(5)\n"
        )
        import ast

        tree = ast.parse(source)
        findings = find_while_true_sleep(tree, source.splitlines())
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["category"], "while-true-sleep")
        self.assertEqual(findings[0]["line"], 3)

    def test_detects_watcher_named_function_infinite_loop(self):
        """A watch_*/monitor_*/poll_* function with an infinite loop is flagged."""
        source = (
            "def monitor_fleet():\n"
            "    while True:\n"
            "        check_status()\n"
        )
        import ast

        tree = ast.parse(source)
        findings = find_watcher_named_infinite_loops(tree, source.splitlines())
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["category"], "watcher-named-infinite-loop")
        self.assertIn("monitor_fleet", findings[0]["message"])

    def test_watcher_ok_suppresses_while_true_sleep(self):
        """A `# watcher-ok` comment on the while-line suppresses the finding."""
        source = (
            "import time\n"
            "def run():\n"
            "    while True:  # watcher-ok\n"
            "        time.sleep(5)\n"
        )
        findings = lint_source(source)
        while_true_findings = [
            f for f in findings if f["category"] == "while-true-sleep"
        ]
        self.assertEqual(while_true_findings, [])

    def test_legitimate_sleep_not_in_infinite_loop_passes(self):
        """time.sleep() outside an unconditional loop is not flagged."""
        source = (
            "import time\n"
            "def retry(n):\n"
            "    count = 0\n"
            "    while count < n:\n"
            "        time.sleep(1)\n"
            "        count += 1\n"
        )
        findings = lint_source(source)
        self.assertEqual(findings, [])

    def test_detects_subprocess_in_infinite_loop(self):
        """A subprocess call inside `while True:` is flagged (detach-and-watch)."""
        source = (
            "import subprocess\n"
            "def run():\n"
            "    while True:\n"
            "        subprocess.Popen(['echo', 'hi'])\n"
        )
        import ast

        tree = ast.parse(source)
        findings = find_subprocess_in_infinite_loop(tree, source.splitlines())
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["category"], "subprocess-in-infinite-loop")

    def test_detects_prompt_wait_for_monitor_pattern(self):
        """A dispatch-prompt string telling an agent to wait for a monitor/signal
        is flagged."""
        import ast

        source = "PROMPT = 'Wait for the monitor to signal completion.'\n"
        tree = ast.parse(source)
        findings = find_prompt_patterns(tree, source.splitlines())
        categories = {f["category"] for f in findings}
        self.assertIn("prompt-wait-for-watcher", categories)

    def test_detects_prompt_polling_pattern(self):
        """The word 'polling' (or 'poll for') in a prompt string is flagged."""
        import ast

        source = "PROMPT = 'Keep polling until the file appears.'\n"
        tree = ast.parse(source)
        findings = find_prompt_patterns(tree, source.splitlines())
        categories = {f["category"] for f in findings}
        self.assertIn("prompt-polling", categories)

    def test_detects_prompt_watch_for_changes_pattern(self):
        """'watch for changes' style phrasing in a prompt string is flagged."""
        import ast

        source = "PROMPT = 'Watch for changes in the output directory.'\n"
        tree = ast.parse(source)
        findings = find_prompt_patterns(tree, source.splitlines())
        categories = {f["category"] for f in findings}
        self.assertIn("prompt-watch-for-changes", categories)

    def test_prompt_pattern_suppressed_by_watcher_ok(self):
        """`# watcher-ok` on the prompt-string line suppresses that finding."""
        import ast

        source = "PROMPT = 'Keep polling until done.'  # watcher-ok\n"
        tree = ast.parse(source)
        findings = find_prompt_patterns(tree, source.splitlines())
        self.assertEqual(findings, [])

    def test_non_prompt_string_with_poll_word_is_not_flagged(self):
        """A plain (non-prompt-named) string mentioning 'polling' -- e.g. a
        legitimate bounded CI-poll status message -- is NOT flagged; only
        strings bound to a prompt-ish name/kwarg/dict-key are in scope."""
        import ast

        source = 'print(f"CI PENDING... waiting {interval}s, polling again")\n'
        tree = ast.parse(source)
        findings = find_prompt_patterns(tree, source.splitlines())
        self.assertEqual(findings, [])

    def test_detects_prompt_pattern_via_call_keyword(self):
        """A prompt passed as a call keyword argument (e.g. Task(prompt=...))
        is in scope for the string-scan even without a `prompt =` assignment."""
        import ast

        source = 'spawn_agent(prompt="Wait for the watcher signal before continuing.")\n'
        tree = ast.parse(source)
        findings = find_prompt_patterns(tree, source.splitlines())
        categories = {f["category"] for f in findings}
        self.assertIn("prompt-wait-for-watcher", categories)


class WatcherLinterDirectoryScanTest(unittest.TestCase):
    """Directory-walk behavior (lint_paths) against a hermetic temp repo."""

    def setUp(self):
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp_ctx.name)
        (self.repo_root / "tools").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp_ctx.cleanup()

    def test_json_output_format_via_cli(self):
        """--json emits the documented {ok, findings, ...} shape via the CLI."""
        bad_file = self.repo_root / "tools" / "bad_watcher.py"
        bad_file.write_text(
            "import time\n"
            "def watch_state():\n"
            "    while True:\n"
            "        time.sleep(2)\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "watcher_linter.py"),
                "--json",
                "--root",
                str(self.repo_root),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertGreaterEqual(len(payload["findings"]), 1)
        self.assertTrue(
            any(f["category"] == "while-true-sleep" for f in payload["findings"])
        )
        self.assertTrue(
            any(
                f["category"] == "watcher-named-infinite-loop"
                for f in payload["findings"]
            )
        )

    def test_clean_directory_exits_zero(self):
        """A directory with no anti-patterns exits 0 with an empty finding list."""
        good_file = self.repo_root / "tools" / "good.py"
        good_file.write_text(
            "def helper(n):\n"
            "    total = 0\n"
            "    for i in range(n):\n"
            "        total += i\n"
            "    return total\n"
        )
        findings, scanned = lint_paths(["tools"], self.repo_root)
        self.assertEqual(findings, [])
        self.assertEqual(scanned, 1)

    def test_paths_override_scans_only_given_directory(self):
        """--paths (lint_paths override) scans only the requested directory."""
        (self.repo_root / "monitor").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "tools" / "bad.py").write_text(
            "import time\nwhile True:\n    time.sleep(1)\n"
        )
        (self.repo_root / "monitor" / "clean.py").write_text("x = 1\n")

        findings, scanned = lint_paths(["monitor"], self.repo_root)
        self.assertEqual(scanned, 1)
        self.assertEqual(findings, [])

    def test_self_exclusion_skips_the_linter_file_itself(self):
        """The linter's own file is excluded from directory scans (it
        necessarily spells out its own trigger words in pattern definitions
        and documentation)."""
        findings, scanned = lint_paths(["tools"], ROOT)
        self_file_findings = [
            f for f in findings if f["file"].endswith("watcher_linter.py")
        ]
        self.assertEqual(self_file_findings, [])


if __name__ == "__main__":
    unittest.main()
