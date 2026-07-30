#!/usr/bin/env python3
"""
MCP Tool Inventory Linter (Guardrail: mcp-tool-inventory-lint)

Ensures mcp/server.mjs tool definitions stay in sync with tests/mcp-fleet.test.mjs expectations.

Extracts actual tools from mcp/server.mjs (regex: name: 'fleet_[^']+') and expected tools
from tests/mcp-fleet.test.mjs (line 223 JSON array), compares alphabetically sorted lists.

Exit codes:
  0: Tools match
  1: Tool mismatch detected (prints diff)
  2: Usage error or file not found

Hermetic: stdlib only, no external deps. Resolves paths relative to AESOP_ROOT (env var
or --root flag).
"""

import sys
import re
import os
import json
import argparse
from pathlib import Path


def extract_tools_from_server(server_path):
    """Extract fleet tool names from mcp/server.mjs via regex."""
    try:
        with open(server_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: {server_path} not found", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error reading {server_path}: {e}", file=sys.stderr)
        return None

    # Pattern: name: 'fleet_[^']+'
    pattern = r"name:\s*'(fleet_[^']+)'"
    matches = re.findall(pattern, content)
    if not matches:
        print(f"Warning: No fleet tools found in {server_path}", file=sys.stderr)
        return []
    return sorted(set(matches))


def extract_tools_from_test(test_path):
    """Extract expected tool names from tests/mcp-fleet.test.mjs line 223 JSON array."""
    try:
        with open(test_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: {test_path} not found", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error reading {test_path}: {e}", file=sys.stderr)
        return None

    # Line 223 (0-indexed: line 222) contains the expected tools array
    if len(lines) < 223:
        print(
            f"Error: {test_path} has fewer than 223 lines (found {len(lines)})",
            file=sys.stderr,
        )
        return None

    expected_line = lines[222].strip()  # Line 223 is index 222

    # Extract array from the line
    # Expected format: const expected = ['fleet_agents', 'fleet_budget', ...];
    # We'll match the array literal: ['...', '...', ...]
    match = re.search(r"\[([^\]]*)\]", expected_line)
    if not match:
        print(
            f"Error: Could not find array on line 223 of {test_path}",
            file=sys.stderr,
        )
        return None

    array_content = f"[{match.group(1)}]"

    # Convert single quotes to double quotes for JSON parsing
    # This is safe because tool names don't contain quotes
    array_content_json = array_content.replace("'", '"')

    try:
        tools = json.loads(array_content_json)
        if not isinstance(tools, list):
            print(
                f"Error: Line 223 of {test_path} does not contain an array",
                file=sys.stderr,
            )
            return None
        return sorted(tools)
    except json.JSONDecodeError as e:
        print(f"Error parsing array on line 223 of {test_path}: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(
        description="MCP tool inventory lint: verify server.mjs and test.mjs are in sync"
    )
    parser.add_argument(
        "--root",
        default=os.environ.get("AESOP_ROOT", "."),
        help="AESOP_ROOT directory (default: env AESOP_ROOT or current dir)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of human-readable diff",
    )
    args = parser.parse_args()

    aesop_root = Path(args.root).resolve()
    server_path = aesop_root / "mcp" / "server.mjs"
    test_path = aesop_root / "tests" / "mcp-fleet.test.mjs"

    # Extract tools from both sources
    actual_tools = extract_tools_from_server(server_path)
    expected_tools = extract_tools_from_test(test_path)

    if actual_tools is None or expected_tools is None:
        sys.exit(2)

    # Compare
    match = actual_tools == expected_tools
    actual_set = set(actual_tools)
    expected_set = set(expected_tools)
    missing_in_test = sorted(actual_set - expected_set)  # In server but not in test
    extra_in_test = sorted(expected_set - actual_set)  # In test but not in server

    if args.json:
        result = {
            "match": match,
            "actual_count": len(actual_tools),
            "expected_count": len(expected_tools),
            "actual_tools": actual_tools,
            "expected_tools": expected_tools,
            "missing_in_test": missing_in_test,
            "extra_in_test": extra_in_test,
        }
        print(json.dumps(result, indent=2))
    else:
        if match:
            print(f"[OK] MCP tool inventory in sync ({len(actual_tools)} tools)")
        else:
            print("[FAIL] MCP tool inventory mismatch:")
            print(f"\n  Actual tools in server.mjs ({len(actual_tools)}):")
            for tool in actual_tools:
                prefix = "  + " if tool in missing_in_test else "    "
                print(f"{prefix}{tool}")

            print(f"\n  Expected tools in test.mjs ({len(expected_tools)}):")
            for tool in expected_tools:
                prefix = "  - " if tool in extra_in_test else "    "
                print(f"{prefix}{tool}")

            if missing_in_test:
                print(f"\n  Missing in test (add to line 223):")
                for tool in missing_in_test:
                    print(f"    + {tool}")

            if extra_in_test:
                print(f"\n  Extra in test (remove from line 223):")
                for tool in extra_in_test:
                    print(f"    - {tool}")

    sys.exit(0 if match else 1)


if __name__ == "__main__":
    main()
