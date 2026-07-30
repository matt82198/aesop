#!/usr/bin/env python3
"""
Unit tests for tools/mcp_tool_inventory_lint.py

Tests both normal operation (in-sync state) and escape reproduction (the e797cca
mismatch where server.mjs was updated with 3 new tools but the test fixture was not).

Hermetic: uses tempfile for test fixtures, no cwd pollution, stdlib only.
"""

import unittest
import tempfile
import json
import subprocess
import sys
from pathlib import Path


class TestMcpToolInventoryLint(unittest.TestCase):
    """Test mcp_tool_inventory_lint.py linter."""

    def setUp(self):
        """Create a temporary directory for test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # Create directory structure
        (self.root / "mcp").mkdir()
        (self.root / "tests").mkdir()

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def _run_linter(self, json_output=False):
        """Run the linter against the test fixture directory."""
        cmd = [
            sys.executable,
            "tools/mcp_tool_inventory_lint.py",
            "--root",
            str(self.root),
        ]
        if json_output:
            cmd.append("--json")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        return result

    def _write_server_mjs(self, tool_names):
        """Write a mock mcp/server.mjs with the given tool names."""
        server_path = self.root / "mcp" / "server.mjs"
        content = "#!/usr/bin/env node\n// MCP Server\n\nconst tools = [\n"
        for tool in tool_names:
            content += f"  {{\n    name: '{tool}',\n    description: 'Test tool',\n  }},\n"
        content += "];\n"
        server_path.write_text(content, encoding="utf-8")

    def _write_test_mjs(self, tool_names):
        """Write a mock tests/mcp-fleet.test.mjs with the expected tools array at line 223."""
        test_path = self.root / "tests" / "mcp-fleet.test.mjs"
        # Create lines 1-222 as filler
        content = "\n".join([f"// line {i}" for i in range(1, 223)])
        content += "\n"
        # Line 223: the expected tools array
        json_array = json.dumps(sorted(tool_names))
        content += f"      const expected = {json_array};\n"
        # Add more lines after for realism
        content += "\n".join([f"// line {i}" for i in range(224, 240)])
        test_path.write_text(content, encoding="utf-8")

    def test_normal_case_in_sync(self):
        """Test normal operation: server and test are in sync."""
        tools = [
            "fleet_agents",
            "fleet_budget",
            "fleet_cost",
            "fleet_cost_by_wave",
            "fleet_cost_trend",
            "fleet_status",
            "fleet_tracker",
            "fleet_verify_stats",
        ]
        self._write_server_mjs(tools)
        self._write_test_mjs(tools)

        result = self._run_linter()
        self.assertEqual(result.returncode, 0, f"Expected exit 0, got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}")
        self.assertIn("in sync", result.stdout.lower())

    def test_escape_reproduction_new_tools_not_in_test(self):
        """
        Reproduce the e797cca escape: server.mjs has 3 new tools, test still expects 8.
        This is the ROOT CAUSE the guardrail must catch.
        """
        old_tools = [
            "fleet_agents",
            "fleet_budget",
            "fleet_cost",
            "fleet_cost_by_wave",
            "fleet_cost_trend",
            "fleet_status",
            "fleet_tracker",
            "fleet_verify_stats",
        ]
        new_tools = old_tools + [
            "fleet_instances",
            "fleet_claims",
            "fleet_multibox_summary",
        ]

        # Server has the new tools
        self._write_server_mjs(new_tools)
        # Test still expects only the old tools
        self._write_test_mjs(old_tools)

        result = self._run_linter()
        # The linter should detect the mismatch
        self.assertEqual(result.returncode, 1, f"Expected exit 1, got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}")
        self.assertIn("mismatch", result.stdout.lower())
        # Should mention the missing tools
        self.assertIn("fleet_instances", result.stdout)
        self.assertIn("fleet_claims", result.stdout)
        self.assertIn("fleet_multibox_summary", result.stdout)

    def test_escape_reproduction_json_output(self):
        """Reproduce escape with JSON output format."""
        old_tools = [
            "fleet_agents",
            "fleet_budget",
            "fleet_cost",
            "fleet_cost_by_wave",
            "fleet_cost_trend",
            "fleet_status",
            "fleet_tracker",
            "fleet_verify_stats",
        ]
        new_tools = old_tools + [
            "fleet_instances",
            "fleet_claims",
            "fleet_multibox_summary",
        ]

        self._write_server_mjs(new_tools)
        self._write_test_mjs(old_tools)

        result = self._run_linter(json_output=True)
        self.assertEqual(result.returncode, 1)

        # Parse JSON output
        output = json.loads(result.stdout)
        self.assertFalse(output["match"])
        self.assertEqual(output["actual_count"], 11)
        self.assertEqual(output["expected_count"], 8)
        self.assertEqual(len(output["missing_in_test"]), 3)
        self.assertIn("fleet_instances", output["missing_in_test"])
        self.assertIn("fleet_claims", output["missing_in_test"])
        self.assertIn("fleet_multibox_summary", output["missing_in_test"])

    def test_tools_removed_from_server(self):
        """Test case: tools removed from server but not from test."""
        old_tools = [
            "fleet_agents",
            "fleet_budget",
            "fleet_cost",
            "fleet_cost_by_wave",
            "fleet_cost_trend",
            "fleet_status",
            "fleet_tracker",
            "fleet_verify_stats",
        ]
        reduced_tools = [
            "fleet_agents",
            "fleet_budget",
            "fleet_cost",
            "fleet_status",
        ]

        self._write_server_mjs(reduced_tools)
        self._write_test_mjs(old_tools)

        result = self._run_linter()
        self.assertEqual(result.returncode, 1, f"Expected exit 1, got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}")
        # Should report extra tools in test
        self.assertIn("fleet_verify_stats", result.stdout)
        self.assertIn("fleet_cost_by_wave", result.stdout)

    def test_empty_tools_in_server(self):
        """Test edge case: no tools in server (malformed or early dev state)."""
        self._write_server_mjs([])
        tools = [
            "fleet_agents",
            "fleet_budget",
            "fleet_cost",
        ]
        self._write_test_mjs(tools)

        result = self._run_linter()
        self.assertEqual(result.returncode, 1, f"Expected exit 1, got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}")
        self.assertIn("mismatch", result.stdout.lower())

    def test_missing_server_file(self):
        """Test error handling: server.mjs does not exist."""
        # Don't create server.mjs
        self._write_test_mjs(["fleet_status"])

        result = self._run_linter()
        self.assertEqual(result.returncode, 2)
        self.assertIn("not found", result.stderr.lower())

    def test_missing_test_file(self):
        """Test error handling: test.mjs does not exist."""
        # Don't create test.mjs
        self._write_server_mjs(["fleet_status"])

        result = self._run_linter()
        self.assertEqual(result.returncode, 2)
        self.assertIn("not found", result.stderr.lower())

    def test_tool_order_normalized(self):
        """Test that tools are compared after alphabetical sorting."""
        tools_unordered = [
            "fleet_verify_stats",
            "fleet_status",
            "fleet_cost",
            "fleet_agents",
            "fleet_budget",
        ]
        tools_ordered = sorted(tools_unordered)

        self._write_server_mjs(tools_unordered)
        self._write_test_mjs(tools_ordered)

        result = self._run_linter()
        # Should match despite different order in source
        self.assertEqual(result.returncode, 0)
        self.assertIn("in sync", result.stdout)

    def test_duplicate_tools_in_server(self):
        """Test handling of duplicate tool names in server.mjs."""
        tools_with_dup = [
            "fleet_status",
            "fleet_status",  # Duplicate
            "fleet_agents",
            "fleet_budget",
        ]
        tools_unique = sorted(set(tools_with_dup))

        self._write_server_mjs(tools_with_dup)
        self._write_test_mjs(tools_unique)

        result = self._run_linter()
        # Should match (duplicates are deduplicated)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
