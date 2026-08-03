"""Replay post-commit edits/writes from Claude Code transcripts to recover uncommitted work.

INDEX: Replay post-commit edits from transcripts to recover work
"""
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


def apply_edit(text, old_string, new_string, replace_all=False):
    """Apply a single edit to text. Returns updated text or None/undefined."""
    if not isinstance(old_string, str) or not old_string or old_string not in text:
        return None
    if replace_all:
        return text.split(old_string)[0] + new_string + text.split(old_string)[1]
    idx = text.find(old_string)
    return text[:idx] + new_string + text[idx + len(old_string) :]


def count_lines(text):
    """Count lines in text."""
    return len(text.split("\n"))


def fmt_time(ts_ms):
    """Format timestamp in milliseconds to ISO format with Z suffix."""
    if not ts_ms:
        return "?"
    return datetime.fromtimestamp(ts_ms / 1000).isoformat().replace("T", " ")[:19] + "Z"


def main():
    parser = argparse.ArgumentParser(
        description="Replay post-commit edits/writes from Claude Code transcripts to recover uncommitted work."
    )
    parser.add_argument(
        "roots", nargs="+", help="Root directories containing .jsonl transcripts to scan"
    )
    parser.add_argument(
        "-p", "--project", required=True, help="Project name/path substring to match"
    )
    parser.add_argument(
        "-r", "--repo-dir", required=True, help="Root directory of the live repository"
    )
    parser.add_argument(
        "-c", "--commit-time", required=True, help="Timestamp of last commit (ISO format, e.g., '2026-06-23T17:22:04Z')"
    )
    parser.add_argument(
        "-o", "--output", required=True, help="Output directory for replayed files"
    )

    args = parser.parse_args()

    # Parse commit time
    commit_ms = int(datetime.fromisoformat(args.commit_time.replace("Z", "+00:00")).timestamp() * 1000)

    output_dir = pathlib.Path(args.output)
    if output_dir.exists():
        import shutil
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Gather .jsonl files
    files = []
    for root in args.roots:
        if os.path.exists(root):
            files.extend(walk_jsonl(root))

    # Collect Write/Edit/MultiEdit operations
    ops = []  # {rel, ts, tool, input, src}
    for fp in files:
        # Parse JSONL file, skipping malformed lines
        objs = parse_jsonl_file(fp)

        src = os.path.basename(fp)
        for obj in objs:
            ts = parse_timestamp(obj.get("timestamp"))

            content = obj.get("message", {}).get("content")
            if not isinstance(content, list):
                continue

            for c in content:
                if c.get("type") != "tool_use":
                    continue
                if c.get("name") not in ["Write", "Edit", "MultiEdit"]:
                    continue

                inp = c.get("input") or {}
                file_path = inp.get("file_path") or ""
                file_path_normalized = file_path.replace("\\", "/")
                if f"/{args.project}/" not in file_path_normalized:
                    continue

                rel = file_path_normalized.split(f"/{args.project}/")[1]
                ops.append(
                    {"rel": rel, "ts": ts, "tool": c["name"], "input": inp, "src": src}
                )

    ops.sort(key=lambda x: x["ts"])

    # Filter post-commit ops
    post = [o for o in ops if o["ts"] > commit_ms]

    # Group by file
    by_file = {}
    for o in post:
        if o["rel"] not in by_file:
            by_file[o["rel"]] = []
        by_file[o["rel"]].append(o)

    report = [
        f"# POST-COMMIT REPLAY — {len(post)} uncommitted ops across {len(by_file)} files "
        f"(after {fmt_time(commit_ms)})\n"
    ]
    recovered = []

    for rel in sorted(by_file.keys()):
        ops_list = by_file[rel]
        live_path = os.path.join(args.repo_dir, rel)
        live_ok = os.path.isfile(live_path)
        text = ""
        if live_ok:
            try:
                with open(live_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                live_ok = False

        disk_len = len(text)
        applied = 0
        missed = 0
        miss_details = []

        for o in ops_list:
            if o["tool"] == "Write":
                text = o["input"].get("content") or text
                applied += 1
            elif o["tool"] == "Edit":
                r = apply_edit(
                    text,
                    o["input"].get("old_string"),
                    o["input"].get("new_string"),
                    o["input"].get("replace_all"),
                )
                if isinstance(r, str):
                    text = r
                    applied += 1
                else:
                    missed += 1
                    miss_details.append(
                        f"{fmt_time(o['ts'])} {(o['input'].get('old_string') or '')[:50].replace(chr(10), '\\n')}"
                    )
            elif o["tool"] == "MultiEdit":
                for e in o["input"].get("edits") or []:
                    r = apply_edit(
                        text,
                        e.get("old_string"),
                        e.get("new_string"),
                        e.get("replace_all"),
                    )
                    if isinstance(r, str):
                        text = r
                        applied += 1
                    else:
                        missed += 1
                        miss_details.append(
                            f"{fmt_time(o['ts'])} [multi] {(e.get('old_string') or '')[:50].replace(chr(10), '\\n')}"
                        )

        changed = live_ok and text != (
            open(live_path, "r", encoding="utf-8").read() if live_ok else ""
        )
        out_path = output_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)

        report.append(f"\n## {rel}")
        report.append(
            f"   post-commit ops={len(ops_list)} "
            f"(Write={len([x for x in ops_list if x['tool'] == 'Write'])} "
            f"Edit={len([x for x in ops_list if x['tool'] != 'Write'])}) "
            f"span {fmt_time(ops_list[0]['ts'])} → {fmt_time(ops_list[-1]['ts'])}"
        )
        report.append(
            f"   base(disk)={disk_len}b/{count_lines(open(live_path, 'r', encoding='utf-8').read() if live_ok else '')}L "
            f"->  replayed={len(text)}b/{count_lines(text)}L "
            f"| applied={applied} missed={missed} "
            f"| {'*** DIFFERS FROM DISK ***' if changed else 'same as disk'}"
        )
        if miss_details:
            report.append("   MISSED edits (old_string not found in base):")
            for detail in miss_details:
                report.append(f"      - {detail}")
        report.append("")

        if changed:
            recovered.append(rel)

    report.append(f"\n# FILES WITH RECOVERABLE UNCOMMITTED WORK: {len(recovered)}")
    for r in recovered:
        report.append(f"  {r}")

    report_path = output_dir / "_REPLAY_REPORT.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    print("\n".join(report))
    print(f"\noutput dir: {output_dir}")


if __name__ == "__main__":
    main()
