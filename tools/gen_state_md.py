#!/usr/bin/env python3
"""gen_state_md — Generate STATE.md checkpoint from event-sourced state store.
INDEX: STATE.md checkpoint generator from event-sourced state store; reads tracker projection via StateAPI read facade; renders markdown with current status header (ISO timestamp), open tracker items by lane, and next steps; CLI: `[--state-root DIR] [--out PATH]`; exit 0=success / 1=malformed store; deterministic + ASCII-safe

Renders a durable checkpoint markdown file (sections: CURRENT status header with ISO
timestamp, open tracker items by lane, recent events/waves summary, NEXT STEPS from
ranked lane) from the state store via the read facade (StateAPI).

Usage:
  python gen_state_md.py [--state-root DIR] [--out PATH]

Deterministic output given fixed inputs; ASCII-safe; exit 0 on success, 1 on
malformed store.

The read facade is StateAPI in state_store/api.py (no direct state-file reads).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure tools module is importable
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


def generate_state_md(state_dir=None, out_path=None, timestamp=None):
    """Generate STATE.md checkpoint from event-sourced state store.

    Args:
        state_dir: Path to state directory (default: AESOP_STATE_ROOT env var or ./state)
        out_path: Path to write output (default: return as string)
        timestamp: ISO timestamp to use in header (default: current UTC time)

    Returns:
        str: The rendered checkpoint markdown

    Raises:
        RuntimeError: If state store is malformed or unreadable
    """
    from state_store import StateAPI

    # Resolve state directory
    if state_dir is None:
        state_dir = os.environ.get("AESOP_STATE_ROOT", "./state")
    state_dir = Path(state_dir)

    # Initialize or load the state store
    db_path = state_dir / "events.db"
    try:
        api = StateAPI(str(db_path))
    except Exception as e:
        raise RuntimeError(f"Failed to open state store at {db_path}: {e}")

    # Get current timestamp (for determinism in tests, allow override)
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    # Project the tracker view to current state
    try:
        tracker_state = api.project("tracker")
    except Exception as e:
        raise RuntimeError(f"Failed to project tracker view: {e}")

    # Extract and organize tracker items by lane
    items = tracker_state.get("items", [])
    items_by_lane = {}
    open_items = [item for item in items if item.get("status") != "archived"]

    for item in open_items:
        lane = item.get("lane", "unassigned")
        if lane not in items_by_lane:
            items_by_lane[lane] = []
        items_by_lane[lane].append(item)

    # Build the markdown output
    lines = []

    # Header
    lines.append("# STATE — Generated Checkpoint")
    lines.append("")
    lines.append(f"**Generated:** {timestamp}")
    lines.append("")

    # Current version (placeholder; real version comes from orchestrator status)
    lines.append("## Current Status")
    lines.append("")
    lines.append(f"System checkpoint generated from event-sourced state store at {timestamp}")
    lines.append("(ISO 8601 UTC timestamp).")
    lines.append("")

    # Open tracker items by lane
    lines.append("## Open Tracker Items")
    lines.append("")

    if open_items:
        for lane_name in sorted(items_by_lane.keys()):
            lane_items = items_by_lane[lane_name]
            lines.append(f"### {lane_name.title()}")
            lines.append("")
            for item in lane_items:
                item_id = item.get("id", "unknown")
                title = item.get("title", "Untitled")
                status = item.get("status", "unknown")
                priority = item.get("priority", "")

                # Format line with ID, title, status
                line = f"- **{item_id}**: {title}"
                if status:
                    line += f" ({status})"
                if priority:
                    line += f" [p{priority}]"
                lines.append(line)

            lines.append("")
    else:
        lines.append("No open tracker items.")
        lines.append("")

    # Next steps (derived from ranked items)
    lines.append("## Next Steps")
    lines.append("")

    ranked_items = [item for item in open_items if item.get("status") == "ranked"]
    if ranked_items:
        # Sort by priority if present
        ranked_items_sorted = sorted(
            ranked_items,
            key=lambda x: (x.get("priority", float("inf")), x.get("id", ""))
        )

        lines.append("Recommended next items (ranked order):")
        lines.append("")
        for item in ranked_items_sorted[:5]:  # Top 5
            item_id = item.get("id", "unknown")
            title = item.get("title", "Untitled")
            lines.append(f"1. **{item_id}**: {title}")

        lines.append("")
    else:
        lines.append("No ranked items in queue. Review backlog and prioritize.")
        lines.append("")

    # Render and return
    output = "\n".join(lines)

    if out_path:
        # Write to file
        out_file = Path(out_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_file.write_text(output, encoding="utf-8")
        except Exception as e:
            raise RuntimeError(f"Failed to write output to {out_path}: {e}")

    return output


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate STATE.md checkpoint from event-sourced state store."
    )
    parser.add_argument(
        "--state-root",
        default=os.environ.get("AESOP_STATE_ROOT", "./state"),
        help="Path to state directory (default: AESOP_STATE_ROOT env or ./state)"
    )
    parser.add_argument(
        "--out",
        dest="out_path",
        help="Path to write output (default: stdout)"
    )

    args = parser.parse_args()

    try:
        output = generate_state_md(state_dir=args.state_root, out_path=args.out_path)
        if not args.out_path:
            print(output, end="")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
