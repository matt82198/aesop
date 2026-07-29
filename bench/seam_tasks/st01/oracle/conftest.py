"""Pytest configuration for oracle tests."""
import sys
from pathlib import Path

# Add repo directory to path so we can import the code being tested
repo_dir = Path(__file__).parent.parent / "repo"
sys.path.insert(0, str(repo_dir))
