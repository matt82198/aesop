"""Extract timeline of file changes from Claude Code transcripts (Write/Edit/Read operations)."""
import argparse
import json
import os
import pathlib
import sys
from datetime import datetime

# Ensure this tool's own directory (tools/) is importable so the shared
# harness resolves regardless of cwd or how the file is loaded
# (the import-gate loads tools by path, without tools/ on sys.path).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcript_reader import walk_jsonl, parse_jsonl_file, parse_timestamp, filter_by_project


def fmt_time(ts_ms):
    """Format timestamp in milliseconds to ISO format with Z suffix."""
    if not ts_ms:
        return "?"
    return datetime.fromtimestamp(ts_ms / 1000).isoformat().replace("T", " ")[:19] + "Z"


def strip_line_nums(text):
    """Remove line number prefixes (e.g., '123\\t') from tool output."""
    lines = text.split("\n")
    result = []
    for line in lines:
        # Strip pattern: leading spaces + digits + tab
        stripped = line
        if "\t" in line:
            parts = line.split("\t", 1)
            if parts[0].strip().isdigit():
                stripped = parts[1]
        result.append(stripped)
    return "\n".join(result)


def result_text(content):
    """Extract text from tool result content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for x in content:
            if isinstance(x, str):
                parts.append(x)
            elif isinstance(x, dict):
                parts.append(x.get("text", ""))
        return "".join(parts)
    if isinstance(content, dict):
        return content.get("text", "")
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="Extract timeline of file changes (Write/Edit/Read) from Claude Code transcripts."
    )
    parser.add_argument(
        "roots", nargs="+", help="Root directories containing .jsonl transcripts to scan"
    )
    parser.add_argument(
        "-p", "--project", required=True, help="Project name/path substring to match"
    )
    parser.add_argument(
        "-t", "--targets", nargs="+", required=True, help="Target file paths/substrings to inspect"
    )

    args = parser.parse_args()

    # Gather .jsonl files
    files = []
    for root in args.roots:
        if os.path.exists(root):
            files.extend(walk_jsonl(root))

    # Index operations by ID
    use_by_id = {}
    snaps = []  # {rel, ts, size, kind, src, content}

    for fp in files:
        # Parse JSONL file, skipping malformed lines
        objs = parse_jsonl_file(fp)

        base = os.path.basename(fp)
        for obj in objs:
            ts = parse_timestamp(obj.get("timestamp"))

            content = obj.get("message", {}).get("content")
            if not isinstance(content, list):
                continue

            for c in content:
                if c.get("type") == "tool_use":
                    file_path = c.get("input", {}).get("file_path")
                    if file_path:
                        file_path_normalized = file_path.replace("\\", "/")
                        if f"/{args.project}/" in file_path_normalized:
                            rel = file_path_normalized.split(f"/{args.project}/")[1]
                        else:
                            rel = None
                    else:
                        rel = None

                    if c.get("name") in ["Write", "Edit", "MultiEdit", "Read"]:
                        use_by_id[c.get("id")] = {"tool": c.get("name"), "rel": rel, "ts": ts}

                    if (
                        rel
                        and c.get("name") == "Write"
                        and isinstance(c.get("input", {}).get("content"), str)
                    ):
                        snaps.append(
                            {
                                "rel": rel,
                                "ts": ts,
                                "size": len(c["input"]["content"]),
                                "kind": "Write",
                                "src": base,
                                "content": c["input"]["content"],
                            }
                        )

                elif c.get("type") == "tool_result":
                    u = use_by_id.get(c.get("tool_use_id"))
                    if u and u.get("tool") == "Read" and u.get("rel"):
                        txt = strip_line_nums(result_text(c.get("content")))
                        if txt and len(txt) > 30:
                            snaps.append(
                                {
                                    "rel": u["rel"],
                                    "ts": u.get("ts") or ts,
                                    "size": len(txt),
                                    "kind": "Read",
                                    "src": base,
                                    "content": txt,
                                }
                            )

    # Report on targets
    for target in args.targets:
        target_snaps = [s for s in snaps if s["rel"] and target in s["rel"]]
        target_snaps.sort(key=lambda x: x["ts"])

        print(f"\n===== {target} =====  ({len(target_snaps)} snapshots)")
        for s in target_snaps:
            print(
                f"  {fmt_time(s['ts'])}  {str(s['size']).rjust(6)}b  "
                f"{s['kind']:<5} {s['src']}"
            )

        if target_snaps:
            latest = target_snaps[-1]
            largest = sorted(target_snaps, key=lambda x: (-x["size"], -x["ts"]))[0]
            exact = target.split("/")[-1]

            # Write snapshots
            for snap, tag in [(latest, "LATEST"), (largest, "LARGEST")]:
                if snap is latest and tag == "LARGEST" and snap is latest:
                    continue  # Skip duplicate
                safe_name = f"{exact}.{tag}".replace(os.sep, "_")
                for c in r'<>:"|?*':
                    safe_name = safe_name.replace(c, "_")
                # Write to current dir for simplicity (no output arg in this version)
                print(f"  -> {tag}: {snap['size']}b @ {fmt_time(snap['ts'])} ({snap['src']})")


if __name__ == "__main__":
    main()
