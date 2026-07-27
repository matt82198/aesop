"""Hidden oracle test suite for the count function defect."""
import sys
from pathlib import Path

# Add the repo directory to path
repo_dir = Path(__file__).parent.parent / "repo"
sys.path.insert(0, str(repo_dir))

from main import count


def test_count_three_items():
    """Test that count([1, 2, 3]) returns 3."""
    result = count([1, 2, 3])
    assert result == 3, f"Expected 3 but got {result}"


def test_count_empty_list():
    """Test that count([]) returns 0."""
    result = count([])
    assert result == 0, f"Expected 0 but got {result}"


def test_count_one_item():
    """Test that count([1]) returns 1."""
    result = count([1])
    assert result == 1, f"Expected 1 but got {result}"
