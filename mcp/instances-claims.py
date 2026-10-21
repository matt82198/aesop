#!/usr/bin/env python3
"""MCP helper: output active instances and file claims as JSON.

Usage:
  python mcp/instances-claims.py --db <db> [--root <aesop_root>] [--config <path>]

Outputs a JSON object with:
  {
    "instances": [...],        # active instances list
    "claims": {...},           # all current file claims by instance
    "summary": {...}           # dashboard-ready summary, incl. "backend"
  }

summary.backend reports the ACTIVE coordination backend, resolved through the
same config seam the dispatch path uses (tools/multibox_config; precedence
env > aesop.config.json > default) so the dashboard cannot advertise a
coordination mode the running fleet is not actually in:
  {"kind": "advisory" | "local-lease" | "fs-claim-log",
   "enabled": bool, "transport": str, "shared_dir": str|null,
   "settle_seconds": float, "lease_ttl_seconds": int, "error": str|null}
"advisory" is the shipped default (config resolved, multibox off): claims are a
projection feed, not mutual exclusion. A config that will not read or parse
reports kind "unknown" with "error" set -- NOT "advisory", because "advisory" is
a positive claim about the fleet and this is an admission that we did not
observe one. A read-only summary must never invent a coordination guarantee.

All timestamps use Unix epoch (seconds). Staleness threshold: 300 seconds.
Exit 0 on success, 1 on error.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlite3

from state_store.instance_projection import (
    list_active_instances,
    get_all_claimed_files,
    detect_stale_instances,
)
from tools.multibox_config import MultiboxConfigError, load_multibox_config

#: kind reported when the config RESOLVED and multibox is off: claims are
#: advisory projection data, not mutual exclusion.
ADVISORY_KIND = "advisory"
#: kind reported when the config did NOT resolve. Deliberately distinct from
#: ADVISORY_KIND: "advisory" is a positive claim about the fleet's coordination
#: mode, "unknown" is an admission that we did not observe one.
UNKNOWN_KIND = "unknown"
_TRANSPORT_KINDS = {"local": "local-lease", "shared-fs": "fs-claim-log"}


class ReadOnlyEventSource:
    """Read-only view of the ``events`` table for the projection helpers.

    Opens the DB with ``mode=ro`` and issues only SELECTs. Unlike
    ``state_store.StateAPI`` (EventStore), it never runs CREATE TABLE/INDEX or
    ``PRAGMA journal_mode=WAL``, and it never creates a missing file: a missing
    DB raises at open time, which main() reports as an error. A DB with no
    ``events`` table reads as an empty stream, the same answer a fresh
    EventStore would give. Honours the mcp/CLAUDE.md read-only invariant
    (tracker e9a72d86760f).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True)

    def get(self, stream: str) -> list:
        """Projection-helper alias for read()."""
        return self.read(stream)

    def read(self, stream: str) -> list:
        """Return events for ``stream`` ascending by version (empty if none)."""
        try:
            cur = self._conn.execute(
                "SELECT id, ts, actor, stream, type, payload, version FROM events "
                "WHERE stream = ? ORDER BY version ASC",
                (stream,),
            )
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise
        rows = []
        for r in cur.fetchall():
            try:
                payload = json.loads(r[5])
            except json.JSONDecodeError as e:
                print(
                    f"WARNING: corrupt JSON payload in stream={r[3]} id={r[0]}: {e}",
                    file=sys.stderr,
                )
                continue
            rows.append({
                "id": r[0], "ts": r[1], "actor": r[2], "stream": r[3],
                "type": r[4], "payload": payload, "version": r[6],
            })
        return rows

    def close(self) -> None:
        """Release the read-only connection."""
        self._conn.close()


def describe_backend(config_path):
    """Describe the active coordination backend for the summary.

    Read-only and never raises: an unreadable or invalid config downgrades to
    the advisory description with ``error`` set, because a status surface that
    cannot resolve the config must report less confidence, not more.

    Args:
        config_path: path to aesop.config.json, or None to use defaults only.

    Returns:
        dict with kind/enabled/transport/shared_dir/settle_seconds/
        lease_ttl_seconds/error.
    """
    config = None
    error = None
    if config_path and Path(config_path).is_file():
        try:
            with open(config_path, encoding="utf-8") as handle:
                config = json.load(handle)
        except (OSError, ValueError) as exc:
            error = "cannot read %s: %s" % (config_path, exc)
    try:
        settings = load_multibox_config(config if error is None else None)
    except MultiboxConfigError as exc:
        settings = load_multibox_config(None)
        error = str(exc)

    enabled = bool(settings["enabled"]) and error is None
    if error is not None:
        kind = UNKNOWN_KIND
    elif enabled:
        kind = _TRANSPORT_KINDS[settings["transport"]]
    else:
        kind = ADVISORY_KIND
    return {
        "kind": kind,
        "enabled": enabled,
        "transport": settings["transport"],
        "shared_dir": settings["shared_dir"],
        "settle_seconds": float(settings["settle_seconds"]),
        "lease_ttl_seconds": int(settings["lease_ttl_seconds"]),
        "error": error,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Output active instances and file claims as JSON for MCP"
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to state_store SQLite database",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Aesop root directory (for reference)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to aesop.config.json (default: <root>/aesop.config.json)",
    )

    args = parser.parse_args()

    config_path = args.config
    if config_path is None:
        config_path = str(Path(args.root or ROOT) / "aesop.config.json")
    backend = describe_backend(config_path)

    try:
        store = ReadOnlyEventSource(args.db)
    except Exception as e:
        print(
            json.dumps(
                {
                    "error": f"Failed to open database: {e}",
                    "instances": [],
                    "claims": {},
                    "summary": {
                        "instance_count": 0,
                        "active_count": 0,
                        "stale_count": 0,
                        "claim_count": 0,
                        "backend": backend,
                    },
                }
            ),
            file=sys.stdout,
        )
        return 1

    try:
        # Get active instances (using 300s stale threshold)
        stale_threshold = 300.0
        active_instances = list_active_instances(store, stale_threshold)

        # Get stale instances
        stale_instances = detect_stale_instances(store, stale_threshold)

        # Get all file claims
        all_claims = get_all_claimed_files(store)

        # Count active claims (from active instances only)
        active_claim_count = sum(
            len(paths)
            for inst_id, paths in all_claims.items()
            if any(i["instance_id"] == inst_id for i in active_instances)
        )

        # Format instances with heartbeat age
        now = time.time()
        instances_output = []
        for inst in active_instances:
            last_hb = inst.get("last_heartbeat", now)
            age_sec = max(0, int(now - last_hb))
            instances_output.append(
                {
                    "id": inst["instance_id"],
                    "hostname": inst["hostname"],
                    "pid": inst["pid"],
                    "status": inst["status"],
                    "registered_at": int(inst.get("registered_at", now)),
                    "last_heartbeat": int(last_hb),
                    "heartbeat_age_seconds": age_sec,
                    "stale": age_sec > stale_threshold,
                }
            )

        # Format stale instances
        stale_output = []
        for inst in stale_instances:
            last_hb = inst.get("last_heartbeat", now)
            age_sec = max(0, int(now - last_hb))
            stale_output.append(
                {
                    "id": inst["instance_id"],
                    "status": "stale",
                    "last_heartbeat": int(last_hb),
                    "heartbeat_age_seconds": age_sec,
                }
            )

        # Combine active and stale (active first)
        all_instances_output = instances_output + stale_output

        # Format claims
        claims_output = {}
        for inst_id, paths in all_claims.items():
            if paths:
                claims_output[inst_id] = sorted(paths)

        # Build summary
        summary = {
            "instance_count": len(all_instances_output),
            "active_count": len(active_instances),
            "stale_count": len(stale_instances),
            "claim_count": sum(len(p) for p in all_claims.values()),
            "stale_threshold_seconds": int(stale_threshold),
            "backend": backend,
        }

        result = {
            "instances": all_instances_output,
            "claims": claims_output,
            "summary": summary,
        }

        print(json.dumps(result, indent=2))
        return 0

    except Exception as e:
        print(
            json.dumps(
                {
                    "error": str(e),
                    "instances": [],
                    "claims": {},
                    "summary": {
                        "instance_count": 0,
                        "active_count": 0,
                        "stale_count": 0,
                        "claim_count": 0,
                        "backend": backend,
                    },
                }
            ),
            file=sys.stdout,
        )
        return 1
    finally:
        try:
            store.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
