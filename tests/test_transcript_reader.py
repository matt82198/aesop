#!/usr/bin/env python3
"""
Tests for transcript_reader.py shared JSONL utilities.

Tests:
  - walk_jsonl() with empty dir, nested dir, finds .jsonl files
  - parse_jsonl_file() skips malformed lines, returns valid objects
  - extract_tool_uses() filters by name
  - filter_by_project() handles project paths and normalizes slashes
"""

import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from transcript_reader import (
    walk_jsonl,
    parse_jsonl_file,
    extract_tool_uses,
    filter_by_project,
    parse_timestamp,
)


class TestWalkJsonl(unittest.TestCase):
    """Tests for walk_jsonl() function."""

    def test_walk_empty_directory(self):
        """walk_jsonl() returns empty list for directory with no .jsonl files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = walk_jsonl(tmpdir)
            self.assertEqual(result, [])

    def test_walk_single_jsonl_file(self):
        """walk_jsonl() finds a .jsonl file in root directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            jsonl_file = tmppath / "test.jsonl"
            jsonl_file.write_text("{}\n", encoding="utf-8")

            result = walk_jsonl(tmpdir)
            self.assertEqual(len(result), 1)
            self.assertTrue(result[0].endswith("test.jsonl"))

    def test_walk_nested_directories(self):
        """walk_jsonl() recursively finds .jsonl files in nested directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "subdir").mkdir()
            (tmppath / "test1.jsonl").write_text("{}\n", encoding="utf-8")
            (tmppath / "subdir" / "test2.jsonl").write_text("{}\n", encoding="utf-8")

            result = walk_jsonl(tmpdir)
            self.assertEqual(len(result), 2)

    def test_walk_ignores_non_jsonl(self):
        """walk_jsonl() ignores non-.jsonl files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "test.json").write_text("{}\n", encoding="utf-8")
            (tmppath / "test.txt").write_text("text\n", encoding="utf-8")
            (tmppath / "test.jsonl").write_text("{}\n", encoding="utf-8")

            result = walk_jsonl(tmpdir)
            self.assertEqual(len(result), 1)
            self.assertTrue(result[0].endswith("test.jsonl"))


class TestParseJsonlFile(unittest.TestCase):
    """Tests for parse_jsonl_file() function."""

    def test_parse_valid_jsonl(self):
        """parse_jsonl_file() parses valid JSONL lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            jsonl_file = tmppath / "test.jsonl"
            content = '{"a": 1}\n{"b": 2}\n{"c": 3}\n'
            jsonl_file.write_text(content, encoding="utf-8")

            result = parse_jsonl_file(jsonl_file)
            self.assertEqual(len(result), 3)
            self.assertEqual(result[0], {"a": 1})
            self.assertEqual(result[1], {"b": 2})
            self.assertEqual(result[2], {"c": 3})

    def test_parse_skips_malformed_lines(self):
        """parse_jsonl_file() skips malformed JSON lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            jsonl_file = tmppath / "test.jsonl"
            content = '{"a": 1}\n{invalid json}\n{"b": 2}\n'
            jsonl_file.write_text(content, encoding="utf-8")

            result = parse_jsonl_file(jsonl_file)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0], {"a": 1})
            self.assertEqual(result[1], {"b": 2})

    def test_parse_skips_empty_lines(self):
        """parse_jsonl_file() skips empty lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            jsonl_file = tmppath / "test.jsonl"
            content = '{"a": 1}\n\n\n{"b": 2}\n  \n'
            jsonl_file.write_text(content, encoding="utf-8")

            result = parse_jsonl_file(jsonl_file)
            self.assertEqual(len(result), 2)

    def test_parse_missing_file(self):
        """parse_jsonl_file() returns empty list for missing file."""
        result = parse_jsonl_file("/nonexistent/path/test.jsonl")
        self.assertEqual(result, [])

    def test_parse_empty_file(self):
        """parse_jsonl_file() returns empty list for empty file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            jsonl_file = tmppath / "test.jsonl"
            jsonl_file.write_text("", encoding="utf-8")

            result = parse_jsonl_file(jsonl_file)
            self.assertEqual(result, [])


class TestExtractToolUses(unittest.TestCase):
    """Tests for extract_tool_uses() function."""

    def test_extract_all_tool_uses(self):
        """extract_tool_uses() extracts all tool_use items."""
        content = [
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "name": "Write", "id": "1", "input": {}},
            {"type": "tool_use", "name": "Edit", "id": "2", "input": {}},
            {"type": "tool_result", "content": "done"},
        ]

        result = extract_tool_uses(content)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "Write")
        self.assertEqual(result[1]["name"], "Edit")

    def test_extract_filtered_tool_uses(self):
        """extract_tool_uses() filters by tool name."""
        content = [
            {"type": "tool_use", "name": "Write", "id": "1", "input": {}},
            {"type": "tool_use", "name": "Edit", "id": "2", "input": {}},
            {"type": "tool_use", "name": "Read", "id": "3", "input": {}},
        ]

        result = extract_tool_uses(content, filter_names=["Write", "Edit"])
        self.assertEqual(len(result), 2)
        names = [r["name"] for r in result]
        self.assertIn("Write", names)
        self.assertIn("Edit", names)
        self.assertNotIn("Read", names)

    def test_extract_empty_content(self):
        """extract_tool_uses() returns empty list for no tool_use items."""
        content = [
            {"type": "text", "text": "hello"},
            {"type": "tool_result", "content": "done"},
        ]

        result = extract_tool_uses(content)
        self.assertEqual(result, [])

    def test_extract_non_list_content(self):
        """extract_tool_uses() returns empty list for non-list input."""
        result = extract_tool_uses({"type": "text"})
        self.assertEqual(result, [])


class TestFilterByProject(unittest.TestCase):
    """Tests for filter_by_project() function."""

    def test_filter_forward_slashes(self):
        """filter_by_project() extracts relative path with forward slashes."""
        path = "C:/Users/matt/aesop/tools/foo.py"
        result = filter_by_project(path, "aesop")
        self.assertEqual(result, "tools/foo.py")

    def test_filter_backslashes(self):
        """filter_by_project() normalizes backslashes to forward slashes."""
        path = r"C:\Users\matt\aesop\tools\foo.py"
        result = filter_by_project(path, "aesop")
        self.assertEqual(result, "tools/foo.py")

    def test_filter_no_match(self):
        """filter_by_project() returns None if project not in path."""
        path = "C:/Users/matt/other/tools/foo.py"
        result = filter_by_project(path, "aesop")
        self.assertIsNone(result)

    def test_filter_nested_project_path(self):
        """filter_by_project() handles nested paths within project."""
        path = "/home/user/aesop/deep/nested/dir/file.py"
        result = filter_by_project(path, "aesop")
        self.assertEqual(result, "deep/nested/dir/file.py")

    def test_filter_case_sensitive(self):
        """filter_by_project() is case-sensitive."""
        path = "/home/user/AESOP/tools/foo.py"
        result = filter_by_project(path, "aesop")
        self.assertIsNone(result)


class TestParseTimestamp(unittest.TestCase):
    """Tests for parse_timestamp() function."""

    def test_parse_iso_with_z(self):
        """parse_timestamp() parses ISO8601 with Z suffix."""
        result = parse_timestamp("2026-01-15T10:30:00Z")
        self.assertGreater(result, 0)

    def test_parse_iso_without_z(self):
        """parse_timestamp() parses ISO8601 without Z suffix."""
        result = parse_timestamp("2026-01-15T10:30:00")
        self.assertGreater(result, 0)

    def test_parse_empty_string(self):
        """parse_timestamp() returns 0 for empty string."""
        result = parse_timestamp("")
        self.assertEqual(result, 0)

    def test_parse_none(self):
        """parse_timestamp() returns 0 for None."""
        result = parse_timestamp(None)
        self.assertEqual(result, 0)

    def test_parse_invalid_format(self):
        """parse_timestamp() returns 0 for invalid format."""
        result = parse_timestamp("not-a-date")
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
