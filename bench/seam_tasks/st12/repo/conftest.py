"""Pytest configuration for visible tests."""
import sys
from pathlib import Path

repo_dir = Path(__file__).parent
sys.path.insert(0, str(repo_dir))
