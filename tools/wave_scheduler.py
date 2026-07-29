#!/usr/bin/env python3
import argparse
import fnmatch
import json
import sys
from typing import Dict, List, Any, Optional, Tuple


def normalize_path(path: str) -> str:
    """Normalize path: posixify, strip ./, casefold."""
    n = path.replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    return n.lower()


def detect_overlap(owns_a: List[str], owns_b: List[str]) -> bool:
    """Check if two file sets overlap (case-insensitive)."""
    if not owns_a or not owns_b:
        return False
    a_norm = [normalize_path(p) for p in owns_a]
    b_norm = [normalize_path(p) for p in owns_b]
    for a in a_norm:
        for b in b_norm:
            if a == b or fnmatch.fnmatch(b, a) or fnmatch.fnmatch(a, b):
                return True
    return False


def _validate_item(item: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate item has required fields."""
    o = item.get("ownsFiles")
    if not o or (isinstance(o, list) and not o):
        return False, "no_file_ownership"
    if isinstance(o, list):
        for e in o:
            if not isinstance(e, str) or not e:
                return False, "invalid_ownsFiles"
    if not item.get("id") or not item.get("slug"):
        return False, "missing_id_or_slug"
    return True, None


def schedule_disjoint_lanes(
    items: List[Dict[str, Any]], max_lanes: int = 8
) -> Dict[str, Any]:
    """Schedule items into lanes with disjoint file ownership."""
    valid = []
    invalid = []
    for item in items:
        v, r = _validate_item(item)
        if v:
            valid.append(item)
        else:
            invalid.append({"id": item.get("id", "?"), "reason": r})

    if not valid:
        return {
            "success": not invalid,
            "lanes": [],
            "unscheduled": [],
            "invalid_items": invalid,
            "debug_info": {"total": len(items), "scheduled": 0},
        }

    valid.sort(
        key=lambda i: (
            {"P1": 0, "P2": 1, "P3": 2}.get(i.get("priority", "P3"), 2),
            i.get("createdAt", "2999"),
        )
    )

    lanes = []
    unscheduled = []

    for item in valid:
        owns = item.get("ownsFiles", [])
        placed = False

        for lane in lanes:
            overlaps = any(
                detect_overlap(owns, li.get("ownsFiles", []))
                for li in lane["items"]
            )
            if not overlaps:
                lane["items"].append(item)
                placed = True
                break

        if not placed:
            if len(lanes) < max_lanes:
                lanes.append({"lane_id": len(lanes), "items": [item]})
            else:
                unscheduled.append({"id": item.get("id", "?"), "reason": "max_lanes"})

    return {
        "success": not unscheduled,
        "lanes": lanes,
        "unscheduled": unscheduled,
        "invalid_items": invalid,
        "debug_info": {
            "total": len(items),
            "scheduled": sum(len(l["items"]) for l in lanes),
        },
    }


def main():
    """CLI entry point."""
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", required=True, metavar="M")
    p.add_argument("--max-lanes", type=int, default=8)
    a = p.parse_args()

    try:
        with open(a.dry_run) as f:
            m = json.load(f)
        items = m.get("items", m if isinstance(m, list) else [])
    except Exception:
        items = []

    r = schedule_disjoint_lanes(items, a.max_lanes)
    print(json.dumps(r, indent=2))
    sys.exit(0 if r["success"] else 1)


if __name__ == "__main__":
    main()
