"""Pins AESOP_ACTIVITY_FEED to a throwaway file for the whole test process.

The driver mirrors every model call to the shared per-user activity feed
(driver/activity_feed.py). Test modules that drive run_turn or the OpenAI-compatible
transport import this module first, so their calls land in a temp file and never in
the real ~/.aesop/state/chat/activity.jsonl. The temp directory is removed at exit.

Import for side effect only:  import activity_feed_isolation  # noqa: F401

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import atexit
import os
import shutil
import tempfile

_DIR = tempfile.mkdtemp(prefix="aesop-feed-test-")
os.environ["AESOP_ACTIVITY_FEED"] = os.path.join(_DIR, "activity.jsonl")
atexit.register(shutil.rmtree, _DIR, True)
