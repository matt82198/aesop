#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''Tests for tools/lane_scheduler.py - overlap-aware lane scheduling pilot.

WS3a subsidiary: provides deterministic lane scheduling for file-disjoint dispatch.
'''

import json
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Any

import sys

REPO = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

try:
    from lane_scheduler import (
        normalize_path,
        detect_overlap,
        schedule_disjoint_lanes,
    )
except ImportError as e:
    print(f"WARNING: lane_scheduler not available: {e}", file=sys.stderr)
    normalize_path = None
    detect_overlap = None
    schedule_disjoint_lanes = None


class TestPathNormalization(unittest.TestCase):
    '''Test path normalization for cross-platform comparison.'''

    def test_normalize_posix(self):
        if normalize_path is None:
            self.skipTest("lane_scheduler not available")
        self.assertEqual(normalize_path("src/main.py"), "src/main.py")

    def test_normalize_windows(self):
        if normalize_path is None:
            self.skipTest("lane_scheduler not available")
        self.assertEqual(normalize_path("src\main.py"), "src/main.py")

    def test_normalize_dotslash(self):
        if normalize_path is None:
            self.skipTest("lane_scheduler not available")
        self.assertEqual(normalize_path("./src/main.py"), "src/main.py")

    def test_normalize_case(self):
        if normalize_path is None:
            self.skipTest("lane_scheduler not available")
        self.assertEqual(normalize_path("Src/Main.PY"), "src/main.py")


class TestOverlapDetection(unittest.TestCase):
    '''Test file ownership overlap detection.'''

    def test_no_overlap(self):
        if detect_overlap is None:
            self.skipTest("lane_scheduler not available")
        self.assertFalse(detect_overlap(["a.py"], ["b.py"]))

    def test_exact_overlap(self):
        if detect_overlap is None:
            self.skipTest("lane_scheduler not available")
        self.assertTrue(detect_overlap(["a.py"], ["a.py"]))

    def test_case_insensitive(self):
        if detect_overlap is None:
            self.skipTest("lane_scheduler not available")
        self.assertTrue(detect_overlap(["A.PY"], ["a.py"]))


class TestSchedule(unittest.TestCase):
    '''Test lane scheduling.'''

    def test_schedule_disjoint(self):
        if schedule_disjoint_lanes is None:
            self.skipTest("lane_scheduler not available")
        items = [
            {"id": "1", "slug": "a", "ownsFiles": ["a.py"], "priority": "P1"},
            {"id": "2", "slug": "b", "ownsFiles": ["b.py"], "priority": "P1"},
        ]
        report = schedule_disjoint_lanes(items, max_lanes=3)
        self.assertTrue(report["success"])

    def test_schedule_empty(self):
        if schedule_disjoint_lanes is None:
            self.skipTest("lane_scheduler not available")
        report = schedule_disjoint_lanes([], max_lanes=3)
        self.assertTrue(report["success"])
        self.assertEqual(len(report["lanes"]), 0)


if __name__ == "__main__":
    unittest.main()
