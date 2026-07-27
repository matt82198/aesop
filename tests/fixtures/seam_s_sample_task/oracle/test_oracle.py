"""Oracle test for seam-starter-001 — verify the fix is correct."""

import sys
from pathlib import Path

# Import the fixed test_sample module.
sys.path.insert(0, str(Path(__file__).parent.parent / "repo"))

from test_sample import add


def test_oracle_add_basic():
    """Oracle: verify basic addition works."""
    assert add(2, 3) == 5, f"Expected 5, got {add(2, 3)}"


def test_oracle_add_zeros():
    """Oracle: verify addition with zeros."""
    assert add(0, 0) == 0, f"Expected 0, got {add(0, 0)}"
    assert add(1, 0) == 1, f"Expected 1, got {add(1, 0)}"


def test_oracle_add_negative():
    """Oracle: verify addition with negative numbers."""
    assert add(-1, 1) == 0, f"Expected 0, got {add(-1, 1)}"
    assert add(-5, -3) == -8, f"Expected -8, got {add(-5, -3)}"


def test_oracle_add_large():
    """Oracle: verify addition with large numbers."""
    assert add(1000, 2000) == 3000, f"Expected 3000, got {add(1000, 2000)}"
