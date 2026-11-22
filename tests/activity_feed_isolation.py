"""Pins AESOP_ACTIVITY_FEED to a throwaway file for the whole test process.

The driver mirrors every model call to the shared per-user activity feed
(driver/activity_feed.py). Test modules that drive run_turn or the OpenAI-compatible
transport import this module first, so their calls land in a temp file and never in
the real ~/.aesop/state/chat/activity.jsonl. The temp directory is removed at exit.

Usage at module import:  import activity_feed_isolation; activity_feed_isolation.pin()

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import atexit
import os
import shutil
import tempfile

_pinned = {}


def pin() -> str:
    """Point AESOP_ACTIVITY_FEED at a temp file (created once per process) and return its path."""
    if "path" not in _pinned:
        directory = tempfile.mkdtemp(prefix="aesop-feed-test-")
        _pinned["path"] = os.path.join(directory, "activity.jsonl")
        atexit.register(shutil.rmtree, directory, True)
    os.environ["AESOP_ACTIVITY_FEED"] = _pinned["path"]
    return _pinned["path"]
