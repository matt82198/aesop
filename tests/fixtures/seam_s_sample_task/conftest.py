"""Oracle conftest for seam_s_sample_task.

Makes the repo code importable from oracle tests.
"""

import sys
from pathlib import Path

# Add ../repo to path so we can import from repo/.
repo_path = Path(__file__).parent.parent / "repo"
if str(repo_path) not in sys.path:
    sys.path.insert(0, str(repo_path))
