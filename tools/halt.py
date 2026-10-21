#!/usr/bin/env python3
"""
Fleet kill switch — a .HALT sentinel file that daemons/run-watchdog.sh (and any
INDEX: Kill-switch: writes/reads/clears `.HALT` sentinel (daemons/dispatch check it)
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
Box-wide sentinel: <CONDUCTOR_ROOT>/state/.HALT (see resolve_conductor_root). The CLI
  `set` writes it and `--status` / `--clear` honor it, unless AESOP_STATE_ROOT is set
  (an explicit isolated override never touches the shared box-wide root). Every
  checkout's run-watchdog.sh honors the box-wide sentinel regardless of which
  checkout it runs from (2026-10-14 N-01: operator halt in primary was not honored
  by the runtime-worktree daemon).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from common import ConductorRootUnresolved, get_state_dir, try_resolve_conductor_root
except ImportError:
    from tools.common import ConductorRootUnresolved, get_state_dir, try_resolve_conductor_root

SENTINEL_NAME = ".HALT"
# Exit status when the box-wide root cannot be resolved (CONDUCTOR_ROOT unset and AESOP_ROOT
# not the canonical "aesop" tree). Distinct from 0 (not halted) and 1 (halted), so every
# caller treats it as UNKNOWN and fails closed.
EXIT_UNRESOLVED = 3


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

    # No AESOP_STATE_ROOT, no config state_root: default to $AESOP_ROOT/state
    # when AESOP_ROOT is set (matches the pre-delegation daemon behavior of
    # always reading $AESOP_ROOT/state/.HALT regardless of cwd — a caller
    # invoking the daemon with AESOP_ROOT pointing elsewhere must not have its
    # halt sentinel silently missed just because cwd differs). Only fall back
    # to get_state_dir()'s cwd-relative default when AESOP_ROOT is unset too.
    if os.environ.get("AESOP_ROOT"):
        return Path(os.environ["AESOP_ROOT"]) / "state"

    return get_state_dir()


def resolve_conductor_root():
    """Box-wide fleet state root by the daemons/conductor-root.sh rule, or None.

    CONDUCTOR_ROOT wins; the sibling fleet root is derived only for the canonical
    "aesop" AESOP_ROOT; anything else is None (fail closed, no home fleet-root fallback).
    AESOP_ROOT defaults to this checkout's root, the same default the daemons use.
    """
    aesop_root = os.environ.get("AESOP_ROOT") or str(Path(__file__).resolve().parent.parent)
    return try_resolve_conductor_root(aesop_root)


def box_wide_sentinel_path(conductor_root=None):
    """Path of the box-wide kill switch, honored by every checkout's watchdog.

    Raises ConductorRootUnresolved when no root can be resolved (fail closed).
    """
    root = Path(conductor_root) if conductor_root is not None else resolve_conductor_root()
    if root is None:
        raise ConductorRootUnresolved(
            "CONDUCTOR_ROOT is unset and AESOP_ROOT is not the canonical 'aesop' tree; "
            "set CONDUCTOR_ROOT to the fleet-state root (box-wide halt cannot be located)"
        )
    return root / "state" / SENTINEL_NAME


def _sentinel_path(state_dir=None):
    if state_dir is None:
        state_dir = resolve_state_dir()
    return Path(state_dir) / SENTINEL_NAME


def _write_sentinel(sentinel, reason):
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "reason": str(reason),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    sentinel.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return sentinel


def _read_sentinel(sentinel):
    """Parsed sentinel dict for an existing file, or None if absent."""
    if not sentinel.exists():
        return None
    try:
        return json.loads(sentinel.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Sentinel exists but is corrupt — still halted, just no parsed detail.
        return {"reason": "(unreadable sentinel)", "timestamp": None}


def halt(reason, state_dir=None):
    """Write the .HALT sentinel. Returns the sentinel Path."""
    if state_dir is None:
        state_dir = resolve_state_dir()
    return _write_sentinel(Path(state_dir) / SENTINEL_NAME, reason)


def halt_box_wide(reason, conductor_root=None):
    """Write the box-wide .HALT sentinel. Returns the sentinel Path."""
    return _write_sentinel(box_wide_sentinel_path(conductor_root), reason)


def is_halted(state_dir=None):
    """True iff the .HALT sentinel exists."""
    return _sentinel_path(state_dir).exists()


def get_halt_info(state_dir=None):
    """Return parsed sentinel dict, or None if not halted / unreadable."""
    return _read_sentinel(_sentinel_path(state_dir))


def clear_halt(state_dir=None):
    """Remove the sentinel if present. Returns True if removed, False if absent."""
    sentinel = _sentinel_path(state_dir)
    if sentinel.exists():
        sentinel.unlink()
        return True
    return False


def _box_wide_applies():
    """The box-wide sentinel is in scope unless AESOP_STATE_ROOT explicitly scopes the halt.

    An explicit AESOP_STATE_ROOT is a deliberate isolated override (tests, relocated
    state), so it must never write to or read the shared box-wide root.
    """
    return not os.environ.get("AESOP_STATE_ROOT")


def active_halt():
    """First active halt as (label, info), box-wide before per-checkout; None if not halted."""
    if _box_wide_applies():
        box = box_wide_sentinel_path()
        info = _read_sentinel(box)
        if info is not None:
            return ("box-wide " + str(box), info)
    local = _sentinel_path()
    info = _read_sentinel(local)
    if info is not None:
        return ("checkout " + str(local), info)
    return None


def clear_all_halts():
    """Clear the scoped sentinel and, when in scope, the box-wide one. True if any removed."""
    removed = clear_halt()
    if _box_wide_applies():
        box = box_wide_sentinel_path()
        if box.exists():
            box.unlink()
            removed = True
    return removed


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
        if _box_wide_applies():
            # Operator kill switch: the box-wide sentinel is what every checkout's
            # watchdog honors, including a runtime worktree running from elsewhere.
            try:
                print(f"sentinel: {halt_box_wide(reason)} (box-wide)")
            except ConductorRootUnresolved as exc:
                # The local halt is already in effect; the box-wide one could not be placed.
                print(f"ERROR: box-wide sentinel NOT written: {exc}", file=sys.stderr)
                return EXIT_UNRESOLVED
        return 0

    if argv[0] == "--status":
        try:
            active = active_halt()
        except ConductorRootUnresolved as exc:
            print(f"HALT-STATUS-UNKNOWN: {exc}", file=sys.stderr)
            return EXIT_UNRESOLVED
        if active is not None:
            label, info = active
            print(f"HALTED: {info.get('reason')} (since {info.get('timestamp')}) [{label}]")
            return 1
        print("not halted")
        return 0

    if argv[0] == "--clear":
        try:
            cleared = clear_all_halts()
        except ConductorRootUnresolved as exc:
            print(f"ERROR: box-wide sentinel cannot be cleared: {exc}", file=sys.stderr)
            return EXIT_UNRESOLVED
        print("cleared" if cleared else "not halted (nothing to clear)")
        return 0

    print(f"Unknown argument: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
