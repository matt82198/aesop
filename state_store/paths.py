"""state_store.paths — canonical path normalization for multibox coordination.

Provides host-independent path canonicalization for safe multi-instance coordination
across heterogeneous boxes (Windows + Linux). Ensures the same path normalizes
identically regardless of the platform running the code, preventing split-brain
claims where two instances would both claim the same file.

Key invariants:
- Separators: always forward slashes (/) in output
- Case folding: configurable per case_policy, not per os.name
- Relative vs absolute: repo_root option converts absolute paths to repo-relative
- Unicode: NFC-normalized (composed form)
- Idempotent: applying normalization twice yields same result
"""

from __future__ import annotations

import os
import posixpath
import unicodedata
from typing import Optional, Literal

# ---------------------------------------------------------------------------
# SQLite database path resolver (single source for every reader and writer).
#
# Two roles exist on disk today and their filenames are deliberately NOT
# unified here (on-disk data stays where it is; the consolidation decision is
# scheduled separately):
#   "tracker"   -> tracker_events.db : WriteAPI, StateAPI readers, state_rebuild,
#                  gen_state_md, ReadAPI.
#   "instances" -> state.db          : instance_manager writer, instances-claims
#                  helper read by mcp/server.mjs (fleet_instances, fleet_claims).
# Every caller resolves through resolve_db_path(); no module may hardcode a
# database filename literal.
# ---------------------------------------------------------------------------

TRACKER_DB_FILENAME = "tracker_events.db"
INSTANCES_DB_FILENAME = "state.db"
_DB_FILENAMES = {"tracker": TRACKER_DB_FILENAME, "instances": INSTANCES_DB_FILENAME}


def resolve_db_path(state_dir, role: str = "tracker") -> str:
    """Return the SQLite database path for ``role`` under ``state_dir``.

    Args:
        state_dir: state directory (str or os.PathLike).
        role: "tracker" (default) or "instances".

    Raises:
        ValueError: if ``role`` is unknown.
    """
    try:
        filename = _DB_FILENAMES[role]
    except KeyError:
        raise ValueError(f"unknown state db role: {role!r}") from None
    return os.path.join(os.fspath(state_dir), filename)


def canonical_claim_path(
    path: str,
    repo_root: Optional[str] = None,
    case_policy: Literal["platform", "insensitive", "sensitive"] = "platform",
) -> str:
    """Canonicalize a file path for multi-instance coordination.

    Ensures host-independent normalization: the same path normalizes identically
    whether running on Windows or Linux (when case_policy is specified).

    Args:
        path: The file path to normalize
        repo_root: Optional repo root path. If provided, the result becomes repo-relative.
                   Absolute paths are made relative to repo_root.
        case_policy: Controls case folding behavior:
                     - "platform": Follows os.name (case-fold on Windows, preserve on Linux)
                     - "insensitive": Always case-fold (safe for heterogeneous boxes)
                     - "sensitive": Always preserve case (case-sensitive filesystems)

    Returns:
        Canonical form of the path:
        - Forward slashes only (no backslashes)
        - No redundant separators or . and .. components
        - NFC-normalized Unicode
        - Case-folding applied per policy
        - Repo-relative if repo_root provided

    Raises:
        None (robust: returns a sensible result for any input)
    """
    if not path:
        # Empty path normalizes to current directory
        return "."

    # Step 1: Fold separators to forward slashes BEFORE any collapsing.
    # This must not go through pathlib.Path or os.path, both of which are
    # platform-flavoured: on Linux they treat "\" as an ordinary filename
    # character, so a Windows-style path would survive uncollapsed and two
    # boxes would derive different canonical forms for the same file.
    # posixpath is used unconditionally so the result is host-independent.
    normalized_str = str(path).replace("\\", "/")

    # Step 2: Make relative to repo_root if provided
    if repo_root:
        root = posixpath.normpath(str(repo_root).replace("\\", "/")).rstrip("/")
        candidate = posixpath.normpath(normalized_str)
        if root and root != "." and candidate.startswith(root + "/"):
            normalized_str = candidate[len(root) + 1:]
        else:
            # Not under repo_root: use as-is
            normalized_str = candidate

    # Step 3: Collapse redundant separators plus . and .. components
    normalized_str = posixpath.normpath(normalized_str)

    # Step 4: Remove trailing slashes
    normalized_str = normalized_str.rstrip("/")

    # Step 5: Apply Unicode NFC normalization (composed form)
    # This ensures 'café' (U+00E9) and 'café' (U+0065 + U+0301) normalize identically
    normalized_str = unicodedata.normalize("NFC", normalized_str)

    # Step 6: Apply case folding policy
    if case_policy == "insensitive":
        # Always case-fold (safe for heterogeneous boxes)
        normalized_str = normalized_str.lower()
    elif case_policy == "platform":
        # Follow platform default: case-fold on Windows, preserve on Linux
        if os.name == "nt":
            normalized_str = normalized_str.lower()
        # else: preserve case on Unix
    elif case_policy == "sensitive":
        # Always preserve case
        pass

    return normalized_str
