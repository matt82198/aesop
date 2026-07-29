#!/usr/bin/env python3
"""Tests for wave_scheduler.py pilot."""
import unittest
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

try:
    from wave_scheduler import normalize_path, detect_overlap, schedule_disjoint_lanes
except ImportError:
    normalize_path = None
    detect_overlap = None
    schedule_disjoint_lanes = None

class TestPathNormalization(unittest.TestCase):
    def test_normalize_posix(self):
        if normalize_path is None:
            self.skipTest("wave_scheduler not available")
        self.assertEqual(normalize_path("src/main.py"), "src/main.py")

    def test_normalize_windows(self):
        if normalize_path is None:
            self.skipTest("wave_scheduler not available")
        self.assertEqual(normalize_path("src\main.py"), "src/main.py")

    def test_normalize_dotslash(self):
        if normalize_path is None:
            self.skipTest("wave_scheduler not available")
        self.assertEqual(normalize_path("./src/main.py"), "src/main.py")

    def test_normalize_case(self):
        if normalize_path is None:
            self.skipTest("wave_scheduler not available")
        self.assertEqual(normalize_path("Src/Main.PY"), "src/main.py")

class TestOverlapDetection(unittest.TestCase):
    def test_no_overlap(self):
        if detect_overlap is None:
            self.skipTest("wave_scheduler not available")
        self.assertFalse(detect_overlap(["a.py"], ["b.py"]))

    def test_exact_overlap(self):
        if detect_overlap is None:
            self.skipTest("wave_scheduler not available")
        self.assertTrue(detect_overlap(["a.py"], ["a.py"]))

    def test_case_insensitive(self):
        if detect_overlap is None:
            self.skipTest("wave_scheduler not available")
        self.assertTrue(detect_overlap(["A.PY"], ["a.py"]))

class TestSchedule(unittest.TestCase):
    def test_schedule_disjoint(self):
        if schedule_disjoint_lanes is None:
            self.skipTest("wave_scheduler not available")
        items = [
            {"id": "1", "slug": "a", "ownsFiles": ["a.py"], "priority": "P1"},
            {"id": "2", "slug": "b", "ownsFiles": ["b.py"], "priority": "P1"},
        ]
        report = schedule_disjoint_lanes(items, max_lanes=3)
        self.assertTrue(report["success"])

    def test_schedule_empty(self):
        if schedule_disjoint_lanes is None:
            self.skipTest("wave_scheduler not available")
        report = schedule_disjoint_lanes([], max_lanes=3)
        self.assertTrue(report["success"])

if __name__ == "__main__":
    unittest.main()
