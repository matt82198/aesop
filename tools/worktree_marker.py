#!/usr/bin/env python3
"""Worktree ownership marker: `.aesop-worktree-owner` at a worktree root.

Records which agent owns a worktree and whether it may be mutated (rw) or is
read-only (ro). Consumed by tools/merge_train.py (create_integration_branch
refuses any cwd without an rw marker owned by the caller) and by
hooks/primary-branch-guard (sibling-worktree branch-moving verbs are denied for
ro or foreign-owned markers).

Fail closed: an unreadable or malformed marker raises MarkerError; callers must
treat that as deny.

INDEX: Worktree ownership marker (.aesop-worktree-owner) read/write with fail-closed parsing, consumed by merge_train and primary-branch-guard

CLI:
  worktree_marker.py write --root <dir> --owner <id> --mode rw|ro
  worktree_marker.py read  --root <dir>   (exit 0 + JSON, 1 absent, 2 unreadable)
"""
import argparse
import json
import os
import sys

MARKER_NAME = ".aesop-worktree-owner"
MODES = ("rw", "ro")


class MarkerError(Exception):
    """Marker exists but cannot be read or is malformed (fail closed)."""


def marker_path(root):
    return os.path.join(os.path.abspath(root), MARKER_NAME)


def write_marker(root, owner, mode):
    """Write the marker. Returns the marker path."""
    if mode not in MODES:
        raise ValueError("mode must be one of %s, got %r" % (MODES, mode))
    if not owner or not isinstance(owner, str):
        raise ValueError("owner must be a non-empty string")
    path = marker_path(root)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"owner": owner, "mode": mode}, fh)
        fh.write("\n")
    return path


def read_marker(root):
    """Return {'owner': str, 'mode': 'rw'|'ro'} or None when no marker exists.

    Raises MarkerError when the marker exists but is unreadable or malformed.
    """
    path = marker_path(root)
    if not os.path.lexists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise MarkerError("unreadable worktree marker %s: %s" % (path, exc))
    if (not isinstance(data, dict) or not isinstance(data.get("owner"), str)
            or not data.get("owner") or data.get("mode") not in MODES):
        raise MarkerError("malformed worktree marker %s" % path)
    return {"owner": data["owner"], "mode": data["mode"]}


def main(argv=None):
    p = argparse.ArgumentParser(description="Read/write .aesop-worktree-owner markers")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--root", required=True)
    w.add_argument("--owner", required=True)
    w.add_argument("--mode", required=True, choices=MODES)
    r = sub.add_parser("read")
    r.add_argument("--root", required=True)
    args = p.parse_args(argv)
    if args.cmd == "write":
        print(write_marker(args.root, args.owner, args.mode))
        return 0
    try:
        data = read_marker(args.root)
    except MarkerError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if data is None:
        print("no marker at %s" % marker_path(args.root), file=sys.stderr)
        return 1
    print(json.dumps(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
