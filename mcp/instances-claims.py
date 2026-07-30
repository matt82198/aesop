#!/usr/bin/env python3
"""MCP helper: output active instances and file claims as JSON.

Usage:
  python mcp/instances-claims.py --db <state_db_path> [--root <aesop_root>]

Outputs a JSON object with:
  {
    "instances": [...],        # active instances list
    "claims": {...},           # all current file claims by instance
    "summary": {...}           # dashboard-ready summary
  }

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

from state_store import StateAPI
from state_store.instance_projection import (
    list_active_instances,
    get_all_claimed_files,
    detect_stale_instances,
)


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

    args = parser.parse_args()

    try:
        store = StateAPI(args.db)
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
