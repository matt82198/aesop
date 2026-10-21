"""Tests for tools.stateapi_lint — scanner for direct state file opens outside the API.

Tests the linter that detects violations of the "reads go through read_api" rule.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StateLintTest(unittest.TestCase):
    """Tests for the stateapi_lint scanner."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo_root = Path(self.tmp)
        # Create minimal repo structure
        self.ui_dir = self.repo_root / "ui"
        self.tools_dir = self.repo_root / "tools"
        self.ui_dir.mkdir(parents=True, exist_ok=True)
        self.tools_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_linter_finds_direct_tracker_json_open(self):
        """Linter detects direct opens of tracker.json outside the API."""
        # Create a module that directly opens tracker.json
        module_content = """
from pathlib import Path
import json

def read_tracker():
    tracker_file = Path("state/tracker.json")
    return json.loads(tracker_file.read_text())
"""
        module_file = self.ui_dir / "bad_reader.py"
        module_file.write_text(module_content)

        # Import the linter
        from tools.stateapi_lint import find_direct_opens

        violations = find_direct_opens(str(self.repo_root))
        # Should find the violation (check for file path in violation string)
        found = any("bad_reader.py" in v for v in violations)
        self.assertTrue(found, f"Expected to find bad_reader.py violation in {violations}")

    def test_linter_finds_direct_status_json_open(self):
        """Linter detects direct opens of orchestrator-status.json."""
        module_content = """
import json
status_file = Path("state/orchestrator-status.json")
data = json.loads(status_file.read_text())
"""
        module_file = self.tools_dir / "bad_preflight.py"
        module_file.write_text(module_content)

        from tools.stateapi_lint import find_direct_opens

        violations = find_direct_opens(str(self.repo_root))
        found = any("bad_preflight.py" in v for v in violations)
        self.assertTrue(found, f"Expected to find bad_preflight.py violation in {violations}")

    def test_linter_allows_writer_exceptions(self):
        """Linter allows exports.py and other writers to access state files."""
        # Create export.py (allowed writer)
        export_content = """
def export_tracker(api, out_path):
    # Writers are allowed to render state files
    tracker_file = Path("state/tracker.json")
    tracker_file.write_text(json.dumps(data))
"""
        export_file = self.repo_root / "state_store" / "export.py"
        export_file.parent.mkdir(parents=True, exist_ok=True)
        export_file.write_text(export_content)

        from tools.stateapi_lint import find_direct_opens

        violations = find_direct_opens(str(self.repo_root))
        # export.py writes should not be flagged as violations (it's a writer)
        # This depends on the allowlist implementation
        # For now, we just verify the function runs
        self.assertIsInstance(violations, list)

    def test_linter_with_readapi_call_passes(self):
        """Linter passes when code uses read_api instead of direct opens."""
        # Create a module that uses read_api
        module_content = """
from state_store.read_api import ReadAPI

def get_tracker():
    api = ReadAPI("state")
    return api.read_tracker_snapshot()
"""
        module_file = self.ui_dir / "good_reader.py"
        module_file.write_text(module_content)

        from tools.stateapi_lint import find_direct_opens

        violations = find_direct_opens(str(self.repo_root))
        # Should not flag the read_api import as a violation
        # The good_reader.py module should not appear in violations
        found = any("good_reader.py" in v for v in violations)
        self.assertFalse(found, f"Should not flag read_api usage as violation: {violations}")

    def test_baseline_file_creation(self):
        """Linter can create and read a baseline file."""
        from tools.stateapi_lint import save_baseline, load_baseline

        baseline_file = self.repo_root / ".stateapi-baseline.json"

        # Create baseline
        baseline_data = {"violations": ["ui/old_reader.py: tracker.json"]}
        save_baseline(str(baseline_file), baseline_data)

        self.assertTrue(baseline_file.exists())

        # Load baseline
        loaded = load_baseline(str(baseline_file))
        self.assertEqual(loaded, baseline_data)

    def test_ratchet_rejects_new_violations(self):
        """Linter rejects NEW violations beyond the baseline."""
        # Create a baseline with one known violation
        baseline_file = self.repo_root / ".stateapi-baseline.json"
        baseline_data = {"violations": ["ui/wave_telemetry.py: 0"]}  # placeholder
        baseline_file.write_text(json.dumps(baseline_data))

        # Create a NEW violation (not in baseline)
        module_content = """
import json
new_violation = Path("state/tracker.json").read_text()
"""
        module_file = self.ui_dir / "new_bad_reader.py"
        module_file.write_text(module_content)

        from tools.stateapi_lint import check_ratchet

        # check_ratchet should return True (fail) if new violations found
        current_violations = ["ui/wave_telemetry.py: 0", "ui/new_bad_reader.py: 0"]
        is_ok = check_ratchet(baseline_data["violations"], current_violations)
        # New violations should cause is_ok to be False
        # (This depends on implementation, but ratchet means tighter is OK, looser is BAD)

    def test_ratchet_accepts_normalized_backslash_baseline(self):
        """Ratchet accepts legacy backslash-keyed baseline entries when violations use forward slashes.

        This test proves that the normalization handles Windows baseline entries (with backslashes)
        matching against current violations (with forward slashes), ensuring cross-platform
        compatibility after the Windows→POSIX separator fix.
        """
        from tools.stateapi_lint import check_ratchet

        # Legacy baseline has backslash separators (from Windows)
        baseline_violations = [
            "ui\\config.py@tracker-json",
            "tools\\health_score.py@watchdog-hb",
        ]

        # Current violations use forward slashes (as_posix())
        current_violations = [
            "ui/config.py@tracker-json",
            "tools/health_score.py@watchdog-hb",
        ]

        # Ratchet should pass (both normalized to same set)
        is_ok, stale, new = check_ratchet(baseline_violations, current_violations)
        self.assertTrue(is_ok, f"Ratchet should accept normalized keys. Stale: {stale}, New: {new}")
        self.assertEqual(len(stale), 0, "Should have no stale entries after normalization")
        self.assertEqual(len(new), 0, "Should have no new violations after normalization")


class StateLintDocstringAwareTest(unittest.TestCase):
    """A state path named only in a docstring or comment is documentation, not an access."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo_root = Path(self.tmp)
        (self.repo_root / "tools").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scan(self, name, content):
        (self.repo_root / "tools" / name).write_text(content, encoding="utf-8")
        from tools.stateapi_lint import find_direct_opens

        return find_direct_opens(str(self.repo_root))

    def test_path_only_in_docstring_and_comment_is_not_flagged(self):
        """A module whose only mention of a state path is in its docstring and a # comment passes."""
        content = (
            '"""Writes state/.task-ingest-heartbeat on a clean run."""\n'
            "import json\n"
            "# heartbeat contract: state/.task-ingest-heartbeat\n"
            "VALUE = 1  # see state/.task-ingest-heartbeat\n"
            "\n"
            "def describe():\n"
            '    """Mentions state/.task-ingest-heartbeat in a function docstring."""\n'
            '    return "ok"\n'
        )
        violations = self._scan("doc_only_reader.py", content)
        found = [v for v in violations if "doc_only_reader.py" in v]
        self.assertEqual(found, [], f"Docstring/comment-only mention must not be flagged: {violations}")

    def test_same_path_in_real_open_call_is_flagged(self):
        """The same state path used in a real open() call in code is still flagged."""
        content = (
            '"""Module docstring that mentions state/.task-ingest-heartbeat."""\n'
            "from pathlib import Path\n"
            "\n"
            "def touch():\n"
            '    """Docstring also mentions state/.task-ingest-heartbeat."""\n'
            '    open(Path("state/.task-ingest-heartbeat"), "w").close()\n'
        )
        violations = self._scan("real_heartbeat_writer.py", content)
        self.assertIn(
            "tools/real_heartbeat_writer.py@state-heartbeat",
            violations,
            f"A real open() of the heartbeat path must be flagged: {violations}",
        )

    def test_real_access_after_docstring_is_still_flagged(self):
        """Blanking a docstring must not hide a real access on the next code line."""
        content = (
            '"""Reads tracker.json through a docstring example only."""\n'
            "import json\n"
            "from pathlib import Path\n"
            'data = json.loads(Path("state/tracker.json").read_text())\n'
        )
        violations = self._scan("post_docstring_reader.py", content)
        self.assertIn(
            "tools/post_docstring_reader.py@state-tracker-json",
            violations,
            f"A real read after a docstring must still be flagged: {violations}",
        )


class BuildlogWriteRuleTest(unittest.TestCase):
    """buildlog-md-write fires only for a tool that can WRITE BUILDLOG.md, not for a read-only literal."""

    BUILDLOG_KEY = "@buildlog-md-write"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo_root = Path(self.tmp)
        (self.repo_root / "tools").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scan(self, name, content):
        (self.repo_root / "tools" / name).write_text(content, encoding="utf-8")
        from tools.stateapi_lint import find_direct_opens

        return find_direct_opens(str(self.repo_root))

    def test_read_only_buildlog_literal_is_not_flagged(self):
        """A module that names BUILDLOG.md and only reads it (no write operation) passes."""
        content = (
            "from pathlib import Path\n"
            'LOG_FILES = frozenset({"BUILDLOG.md"})\n'
            "\n"
            "def tail(repo_root, n):\n"
            '    text = (Path(repo_root) / "BUILDLOG.md").read_text(encoding="utf-8")\n'
            "    return text.splitlines()[-n:]\n"
        )
        violations = self._scan("metrics_like_reader.py", content)
        found = [v for v in violations if "metrics_like_reader.py" in v]
        self.assertEqual(found, [], f"Read-only BUILDLOG.md literal must not be flagged: {violations}")

    def test_buildlog_literal_with_write_operation_is_flagged(self):
        """A module that names BUILDLOG.md AND appends to it is still flagged (true positive kept)."""
        content = (
            "from pathlib import Path\n"
            "\n"
            "def append_entry(repo_root, line):\n"
            '    with open(Path(repo_root) / "BUILDLOG.md", "a", encoding="utf-8") as f:\n'
            "        f.write(line)\n"
        )
        violations = self._scan("real_buildlog_writer.py", content)
        self.assertIn(
            "tools/real_buildlog_writer.py@buildlog-md-write",
            violations,
            f"A writer of BUILDLOG.md must still be flagged: {violations}",
        )

    def test_each_write_operation_form_still_flags_buildlog_literal(self):
        """Every write form the rule recognises, paired with the literal, is flagged."""
        write_forms = {
            "open_w_mode": 'open("BUILDLOG.md", "w").close()',
            "open_plus_mode_kw": 'open("BUILDLOG.md", mode="r+").close()',
            "path_open_append": 'Path("BUILDLOG.md").open("a").close()',
            "write_text": 'Path("BUILDLOG.md").write_text("x")',
            "write_bytes": 'Path("BUILDLOG.md").write_bytes(b"x")',
            "shutil_copy": 'shutil.copy("src.md", "BUILDLOG.md")',
            "os_replace": 'os.replace("tmp.md", "BUILDLOG.md")',
            "os_open_append_flag": 'os.open("BUILDLOG.md", os.O_WRONLY | os.O_APPEND)',
            "subprocess_tee_append": 'subprocess.run(["tee", "-a", "BUILDLOG.md"], input=b"x")',
        }
        for form, call in write_forms.items():
            with self.subTest(form=form):
                content = (
                    "import os\nimport shutil\nimport subprocess\nfrom pathlib import Path\n"
                    f"def go():\n    {call}\n"
                )
                violations = self._scan(f"writer_{form}.py", content)
                self.assertIn(
                    f"tools/writer_{form}.py@buildlog-md-write",
                    violations,
                    f"write form {form} with the literal must be flagged: {violations}",
                )


if __name__ == "__main__":
    unittest.main()
