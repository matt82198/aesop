#!/usr/bin/env python3
"""
Shared JSONL transcript reading utilities.

Consolidates duplicated walk_jsonl(), JSON parsing, timestamp parsing, and project
filtering logic from transcript_replay.py, transcript_timeline.py, and
fleet_prompt_extractor.py into a single reusable module.

Functions:
  walk_jsonl(directory: str | Path) -> list[str]
    Recursively find all .jsonl files in a directory.

  parse_jsonl_file(path: str | Path) -> list[dict]
    Parse a JSONL file and return list of JSON objects (skips malformed lines).

  extract_tool_uses(messages: list, filter_names: list[str] | None = None) -> list[dict]
    Extract tool use items from Claude message content.

  filter_by_project(path_str: str, project_substr: str) -> str | None
    Extract relative path from a file path if it matches project substring.
"""

import json
import os
from datetime import datetime
from pathlib import Path


def walk_jsonl(directory):
    """Recursively find all .jsonl files in a directory.

    Args:
        directory: Root directory to search (str or Path).

    Returns:
        list[str]: Absolute paths to .jsonl files found.
    """
    result = []
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.endswith(".jsonl"):
                result.append(os.path.join(root, f))
    return result


def parse_jsonl_file(path):
    """Parse a JSONL file and return list of JSON objects.

    Skips malformed lines without crashing. Handles file read errors gracefully.

    Args:
        path: Path to JSONL file (str or Path).

    Returns:
        list[dict]: List of parsed JSON objects (malformed lines skipped).
    """
    result = []
    path = Path(path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().split("\n")
    except Exception:
        return result

    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            result.append(obj)
        except json.JSONDecodeError:
            # Skip malformed lines, do not crash
            continue

    return result


def extract_tool_uses(messages, filter_names=None):
    """Extract tool use items from Claude message content.

    Args:
        messages: List of message dictionaries (or a single message dict's content).
        filter_names: Optional list of tool names to filter by (e.g., ['Write', 'Edit']).

    Returns:
        list[dict]: List of tool use items with structure:
                    {'name': str, 'id': str, 'input': dict, 'type': 'tool_use', ...}
    """
    tool_uses = []

    # Handle case where messages is actually a content list
    if isinstance(messages, list):
        content = messages
    else:
        return tool_uses

    for c in content:
        if c.get("type") != "tool_use":
            continue

        name = c.get("name")
        if filter_names and name not in filter_names:
            continue

        tool_uses.append(c)

    return tool_uses


def parse_timestamp(timestamp_str):
    """Parse ISO8601 timestamp to milliseconds since epoch.

    Args:
        timestamp_str: ISO8601 timestamp string (with or without Z suffix).

    Returns:
        int: Milliseconds since epoch, or 0 if unparseable.
    """
    if not timestamp_str:
        return 0

    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        return 0


def filter_by_project(path_str, project_substr):
    """Extract relative path from a file path if it matches project substring.

    Args:
        path_str: File path (may use backslashes on Windows).
        project_substr: Project substring to match (e.g., 'aesop').

    Returns:
        str | None: Relative path after the project name, or None if no match.
                    Normalized to forward slashes.

    Example:
        filter_by_project('/path/to/aesop/tools/foo.py', 'aesop')
        => 'tools/foo.py'
    """
    path_normalized = path_str.replace("\\", "/")
    project_marker = f"/{project_substr}/"

    if project_marker not in path_normalized:
        return None

    try:
        rel = path_normalized.split(project_marker)[1]
        return rel
    except IndexError:
        return None
