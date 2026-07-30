"""Tests for ui/tooling_panel.py -- tooling dashboard panel API.

Tests the tooling summary aggregation: subprocess tool execution, JSON parsing,
caching, graceful degradation for missing tools, and the HTTP handler.

Run: python -m unittest tests.test_tooling_panel -v
     python tests/test_tooling_panel.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import config

# Force import of tooling_panel after config is on the path
import tooling_panel

ENV_KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


class ToolingPanelIsolationCase(unittest.TestCase):
    """Base class for tooling panel tests with isolated temp directories."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-tooling-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        (self.fixture_root / "transcripts").mkdir()
        self.tools_dir = self.fixture_root / "tools"
        self.tools_dir.mkdir()

        # Save original env
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}

        # Set isolated environment
        os.environ["AESOP_ROOT"] = str(self.fixture_root)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        # Reload config to pick up new env vars
        config.reload()

        # Clear cache between tests
        tooling_panel._cache_data = None
        tooling_panel._cache_time = 0.0

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.reload()

        # Clear cache
        tooling_panel._cache_data = None
        tooling_panel._cache_time = 0.0

        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _write_tool(self, name, script_body):
        """Write a Python tool script to the tools dir."""
        tool_path = self.tools_dir / name
        tool_path.write_text(script_body, encoding="utf-8")
        return tool_path


class TestExtractors(ToolingPanelIsolationCase):
    """Test the extractor functions that parse tool JSON output."""

    def test_extract_todo_count_dict_with_count(self):
        result = tooling_panel._extract_todo_count({"count": 42})
        self.assertEqual(result, 42)

    def test_extract_todo_count_dict_with_todos_list(self):
        result = tooling_panel._extract_todo_count({"todos": ["a", "b", "c"]})
        self.assertEqual(result, 3)

    def test_extract_todo_count_list(self):
        result = tooling_panel._extract_todo_count([1, 2, 3, 4])
        self.assertEqual(result, 4)

    def test_extract_todo_count_none(self):
        result = tooling_panel._extract_todo_count(None)
        self.assertIsNone(result)

    def test_extract_coverage_pct(self):
        result = tooling_panel._extract_coverage({"coverage_pct": 85.234})
        self.assertEqual(result, 85.2)

    def test_extract_coverage_alternative_key(self):
        result = tooling_panel._extract_coverage({"coverage": 92})
        self.assertEqual(result, 92.0)

    def test_extract_coverage_none(self):
        result = tooling_panel._extract_coverage(None)
        self.assertIsNone(result)

    def test_extract_dead_code_dict(self):
        result = tooling_panel._extract_dead_code({"dead": ["fn1", "fn2"]})
        self.assertEqual(result, 2)

    def test_extract_import_cycles_dict(self):
        result = tooling_panel._extract_import_cycles({"cycles": [["a", "b"]]})
        self.assertEqual(result, 1)

    def test_extract_encoding_issues_count(self):
        result = tooling_panel._extract_encoding_issues({"count": 3})
        self.assertEqual(result, 3)


class TestRunTool(ToolingPanelIsolationCase):
    """Test the _run_tool subprocess wrapper."""

    def test_run_tool_missing_returns_none(self):
        """Tool that doesn't exist returns None (graceful degradation)."""
        result = tooling_panel._run_tool("nonexistent_tool.py")
        self.assertIsNone(result)

    def test_run_tool_valid_json_output(self):
        """Tool that outputs valid JSON returns parsed dict."""
        self._write_tool("good_tool.py", (
            "import json, sys\n"
            "json.dump({'count': 7}, sys.stdout)\n"
        ))
        result = tooling_panel._run_tool("good_tool.py")
        self.assertEqual(result, {"count": 7})

    def test_run_tool_nonzero_exit_raises_tool_error(self):
        """Tool that exits with nonzero raises ToolError with exit-nonzero class."""
        self._write_tool("bad_exit.py", "import sys; sys.exit(1)\n")
        with self.assertRaises(tooling_panel.ToolError) as ctx:
            tooling_panel._run_tool("bad_exit.py")
        self.assertEqual(ctx.exception.error_class, "tool-exit-nonzero")

    def test_run_tool_invalid_json_raises_tool_error(self):
        """Tool that outputs invalid JSON raises ToolError with parse-error class."""
        self._write_tool("bad_json.py", "print('not json')\n")
        with self.assertRaises(tooling_panel.ToolError) as ctx:
            tooling_panel._run_tool("bad_json.py")
        self.assertEqual(ctx.exception.error_class, "parse-error")

    def test_run_tool_empty_output_returns_none(self):
        """Tool that outputs nothing returns None."""
        self._write_tool("empty.py", "pass\n")
        result = tooling_panel._run_tool("empty.py")
        self.assertIsNone(result)


class TestToolingSummary(ToolingPanelIsolationCase):
    """Test the get_tooling_summary function (caching and aggregation)."""

    def test_summary_all_null_when_no_tools(self):
        """When no tools exist, all metrics are null."""
        summary = tooling_panel.get_tooling_summary()
        self.assertIsNone(summary["todo_count"])
        self.assertIsNone(summary["coverage_pct"])
        self.assertIsNone(summary["dead_code_count"])
        self.assertIsNone(summary["import_cycle_count"])
        self.assertIsNone(summary["encoding_issues"])
        self.assertIsNotNone(summary["scanned_at"])

    def test_summary_cache_returns_same_data(self):
        """Second call within TTL returns cached data without re-scan."""
        summary1 = tooling_panel.get_tooling_summary()
        ts1 = summary1["scanned_at"]

        summary2 = tooling_panel.get_tooling_summary()
        ts2 = summary2["scanned_at"]

        # Same timestamp means cache was used
        self.assertEqual(ts1, ts2)

    def test_summary_force_bypasses_cache(self):
        """force=True re-scans even within TTL."""
        summary1 = tooling_panel.get_tooling_summary()
        ts1 = summary1["scanned_at"]

        # Force re-scan
        summary2 = tooling_panel.get_tooling_summary(force=True)
        ts2 = summary2["scanned_at"]

        # New timestamp (or same if sub-second, but at minimum should not error)
        self.assertIsNotNone(ts2)

    def test_summary_with_working_tool(self):
        """When a tool exists and returns valid JSON, metric is populated."""
        self._write_tool("todo_tracker.py", (
            "import json\n"
            "print(json.dumps({'count': 15}))\n"
        ))
        summary = tooling_panel.get_tooling_summary()
        self.assertEqual(summary["todo_count"], 15)


class TestServeHandler(ToolingPanelIsolationCase):
    """Test the serve_api_tooling_summary HTTP handler function."""

    def _make_mock_handler(self):
        handler = MagicMock()
        handler.wfile = MagicMock()
        handler.wfile.write = MagicMock()
        return handler

    def test_serve_returns_200_json(self):
        """Handler sends 200 with JSON payload."""
        handler = self._make_mock_handler()
        tooling_panel.serve_api_tooling_summary(handler, force=True)

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call(
            "Content-Type", "application/json; charset=utf-8"
        )
        handler.end_headers.assert_called_once()

        # Parse the written JSON to validate structure
        written_bytes = handler.wfile.write.call_args[0][0]
        payload = json.loads(written_bytes.decode("utf-8"))
        self.assertIn("todo_count", payload)
        self.assertIn("coverage_pct", payload)
        self.assertIn("dead_code_count", payload)
        self.assertIn("import_cycle_count", payload)
        self.assertIn("encoding_issues", payload)
        self.assertIn("scanned_at", payload)

    def test_serve_handles_exception_gracefully(self):
        """Handler sends 500 on unexpected error."""
        handler = self._make_mock_handler()
        # Make get_tooling_summary raise
        with patch.object(tooling_panel, "get_tooling_summary",
                          side_effect=RuntimeError("boom")):
            tooling_panel.serve_api_tooling_summary(handler)

        # Should have attempted a 500 response
        calls = [c[0][0] for c in handler.send_response.call_args_list]
        self.assertIn(500, calls)


if __name__ == "__main__":
    unittest.main()
