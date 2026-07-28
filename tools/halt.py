#!/usr/bin/env python3
"""
Fleet kill switch — a .HALT sentinel file that daemons/run-watchdog.sh (and any
other cardinal-rule-abiding loop) must check every cycle and refuse to do work
while it exists.

Why: wave-26 critique — autonomy expanded to self-merging portfolio PRs on green
with no ceiling/cap/limit/abort anywhere in the harness. This is the abort.

API:
  halt(reason, state_dir=None) -> Path
    Write the .HALT sentinel (reason + UTC timestamp, JSON) under the resolved
    state dir. Creates the state dir if missing. Overwrites any existing sentinel
    (last halt() call wins — reason is always the most recent one).

  is_halted(state_dir=None) -> bool
    True iff the sentinel file exists.

  get_halt_info(state_dir=None) -> dict | None
    Parsed sentinel contents ({"reason": ..., "timestamp": ...}), or None if not halted.

  clear_halt(state_dir=None) -> bool
    Remove the sentinel if present. Returns True if a sentinel was removed, False
    if there was nothing to clear (idempotent no-op).

  resolve_state_dir(config=None) -> Path
    Precedence: AESOP_STATE_ROOT env var > aesop.config.json "state_root" > default
    (tools/common.get_state_dir(): ./state relative to cwd). Mirrors the precedence
    documented in aesop.config.example.json's top-level "description" field.

CLI:
  python tools/halt.py set "<reason>"   -> writes sentinel, exit 0
  python tools/halt.py --status         -> prints halted/not-halted; exit 1 if halted, 0 if not
  python tools/halt.py --clear          -> removes sentinel if present, exit 0

Sentinel location: <state_dir>/.HALT
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir

SENTINEL_NAME = ".HALT"


def load_config():
    """Load aesop.config.json from current directory, return dict (or {} if absent/bad)."""
    config_file = Path("aesop.config.json")
    if not config_file.exists():
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[halt] Failed to load config: {e}", file=sys.stderr)
        return {}


def resolve_state_dir(config=None):
    """Resolve the state directory: AESOP_STATE_ROOT env > config state_root > default.

    A relative config state_root is resolved against AESOP_ROOT (if set) else cwd,
    matching how daemons/run-watchdog.sh resolves $AESOP_ROOT/state.
    """
    if os.environ.get("AESOP_STATE_ROOT"):
        return Path(os.environ["AESOP_STATE_ROOT"])

    if config is None:
        config = load_config()

    state_root = config.get("state_root") if isinstance(config, dict) else None
    if state_root:
        p = Path(state_root).expanduser()
        if not p.is_absolute():
            root = Path(os.environ.get("AESOP_ROOT", Path.cwd()))
            p = root / p
        return p

    return get_state_dir()


def _sentinel_path(state_dir=None):
    if state_dir is None:
        state_dir = resolve_state_dir()
    return Path(state_dir) / SENTINEL_NAME


def halt(reason, state_dir=None):
    """Write the .HALT sentinel. Returns the sentinel Path."""
    if state_dir is None:
        state_dir = resolve_state_dir()
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    sentinel = state_dir / SENTINEL_NAME
    payload = {
        "reason": str(reason),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    sentinel.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return sentinel


def is_halted(state_dir=None):
    """True iff the .HALT sentinel exists."""
    return _sentinel_path(state_dir).exists()


def get_halt_info(state_dir=None):
    """Return parsed sentinel dict, or None if not halted / unreadable."""
    sentinel = _sentinel_path(state_dir)
    if not sentinel.exists():
        return None
    try:
        return json.loads(sentinel.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Sentinel exists but is corrupt — still halted, just no parsed detail.
        return {"reason": "(unreadable sentinel)", "timestamp": None}


def clear_halt(state_dir=None):
    """Remove the sentinel if present. Returns True if removed, False if absent."""
    sentinel = _sentinel_path(state_dir)
    if sentinel.exists():
        sentinel.unlink()
        return True
    return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if not argv:
        print('Usage: halt.py set "<reason>" | --status | --clear', file=sys.stderr)
        return 2

    if argv[0] in ("-h", "--help"):
        print('Usage: halt.py set "<reason>" | --status | --clear')
        return 0

    if argv[0] == "set":
        if len(argv) < 2 or not argv[1].strip():
            print('Usage: halt.py set "<reason>"', file=sys.stderr)
            return 2
        reason = " ".join(argv[1:])
        sentinel = halt(reason)
        print(f"HALTED: {reason}")
        print(f"sentinel: {sentinel}")
        return 0

    if argv[0] == "--status":
        info = get_halt_info()
        if info:
            print(f"HALTED: {info.get('reason')} (since {info.get('timestamp')})")
            return 1
        print("not halted")
        return 0

    if argv[0] == "--clear":
        cleared = clear_halt()
        print("cleared" if cleared else "not halted (nothing to clear)")
        return 0

    print(f"Unknown argument: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
