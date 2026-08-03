#!/usr/bin/env python3
"""
transcript_digest.py — Digest agent transcripts into compact briefs.
INDEX: Digest agent-*.jsonl transcripts into compact redacted per-agent briefs (state/ledger/transcripts-brief.jsonl; deterministic, idempotent, strips paths/emails/tokens)

Reads agent-*.jsonl transcripts from a session subagents directory and appends
compact ~200-byte per-agent briefs to state/ledger/transcripts-brief.jsonl.

Usage:
  python -m tools.transcript_digest --transcripts-dir /path/to/session/subagents \
    --wave rc.6 [--state-root /path/to/state]

Briefs include: agent label, files touched, tool-call count, pass/fail outcome,
token count, and a 1-2 sentence summary. Aggressive redaction removes absolute
paths, usernames, emails, tokens, and repo names.

Deterministic + idempotent (skips agents already in the ledger).
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# Redaction patterns (derived from secret_scan.py)
REDACTION_PATTERNS = {  # secretscan: allow-pattern-docs
    "pem_private_key": (r"-----BEGIN .* PRIVATE " r"KEY-----.*?-----END .* PRIVATE " r"KEY-----", re.DOTALL | re.IGNORECASE),
    "aws_access_key": (r"AKIA[0-9A-Z]{16}", 0),
    "aws_secret_pattern": (r"aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*[^\s\$\<\{]", re.IGNORECASE),
    "github_token": (r"(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{20,}", 0),
    "slack_token": (r"xox[baprs]-[A-Za-z0-9-]{10,}", 0),
    "openai_anthropic_key": (r"sk-[A-Za-z0-9_\-]{20,}", 0),
    "connection_string": (r"://[^:]+:[^@/\s]+@(?!localhost|127\.|example|test)[^\s]+", 0),
}

# Patterns to redact by category
EMAIL_PATTERN = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
# Windows path: C:\ or POSIX path: /
PATH_PATTERN = r"[A-Za-z]:\\[^\\/:*<>|]*|/[^/:*<>|]*"
REPO_NAME_PATTERN = r"\b(?:aesop|conductor3|tr-sample-tracker|ecm-ai|TR-Automation-Scripts)\b"
USERNAME_PATTERN = r"\b(?:matt8|matt82198|John|Jack|Doe)\b"


def redact_text(text: str) -> str:
    """Aggressively redact secrets, paths, emails, usernames, and repo names."""
    if not text:
        return text

    # Redact keys and credentials
    for pattern, flags in REDACTION_PATTERNS.values():
        text = re.sub(pattern, "[REDACTED]", text, flags=flags)

    # Redact emails
    text = re.sub(EMAIL_PATTERN, "[EMAIL]", text, flags=re.IGNORECASE)

    # Redact absolute paths (Windows and POSIX)
    text = re.sub(PATH_PATTERN, "[PATH]", text)

    # Redact repo names
    text = re.sub(REPO_NAME_PATTERN, "[REPO]", text, flags=re.IGNORECASE)

    # Redact usernames
    text = re.sub(USERNAME_PATTERN, "[USER]", text, flags=re.IGNORECASE)

    return text


def infer_outcome(messages: List[Dict], errors: List[Dict]) -> str:
    """Infer outcome from transcript structure: completed|stalled|failed|timeout."""
    # Check for explicit timeout markers first
    if errors:
        if any("timeout" in e.get("message", "").lower() for e in errors):
            return "timeout"
        return "stalled"

    if not messages:
        return "failed"

    last_msg = messages[-1] if isinstance(messages, list) else {}
    msg_type = last_msg.get("type", "")

    # If last message is a tool result with error, mark as stalled
    if msg_type == "tool_result" and "error" in last_msg:
        return "stalled"

    # Default to completed
    return "completed"


def extract_files_from_calls(messages: List[Dict]) -> Tuple[Set[str], Set[str]]:
    """Extract created and modified file paths from tool calls."""
    created = set()
    modified = set()

    if not isinstance(messages, list):
        return created, modified

    for msg in messages:
        if msg.get("type") == "tool_result" or "content" in msg:
            content = msg.get("content", "")
            if isinstance(content, str):
                # Look for Write, Edit, Read operations in logs
                if "Write" in content or "write" in content:
                    # Heuristic: files mentioned after write ops are likely created
                    matches = re.findall(r"(?:Write|write|created?|added?)\s+(?:to\s+)?['\"]?([^\s'\"]+\.(?:py|js|md|json|sh))", content)
                    created.update(matches)
                if "Edit" in content or "edit" in content or "modified?" in content:
                    matches = re.findall(r"(?:Edit|edit|modified?|updated?)\s+(?:file\s+)?['\"]?([^\s'\"]+\.(?:py|js|md|json|sh))", content)
                    modified.update(matches)

    # Redact file paths
    created = {redact_text(f) for f in created}
    modified = {redact_text(f) for f in modified}

    return created, modified


def extract_tool_calls(messages: List[Dict]) -> Tuple[List[str], int]:
    """Extract tool call types and count."""
    tools = {}

    if not isinstance(messages, list):
        return [], 0

    for msg in messages:
        if msg.get("type") == "tool_use":
            tool_name = msg.get("name", "Unknown")
            tools[tool_name] = tools.get(tool_name, 0) + 1

    # Sort by frequency, take top 3
    top_tools = sorted(tools.keys(), key=lambda t: tools[t], reverse=True)[:3]
    total_calls = sum(tools.values())

    return top_tools, total_calls


def extract_errors(messages: List[Dict]) -> List[Dict]:
    """Extract error messages with timestamps."""
    errors = []

    if not isinstance(messages, list):
        return errors

    for i, msg in enumerate(messages):
        if msg.get("type") == "tool_result" and msg.get("is_error"):
            error_msg = msg.get("content", "Unknown error")
            # Redact sensitive data from error messages
            error_msg = redact_text(error_msg)
            # Truncate to ~100 chars
            error_msg = error_msg[:100]
            errors.append({
                "tool": msg.get("name", "Unknown"),
                "at_sec": i * 5,  # Rough estimate: ~5 sec per message
                "message": error_msg
            })

    return errors[:3]  # Keep top 3 errors


def extract_token_usage(metadata: Dict) -> Dict:
    """Extract token usage from metadata."""
    usage = metadata.get("usage", {})
    return {
        "input": usage.get("input_tokens", 0),
        "output": usage.get("output_tokens", 0),
        "model": metadata.get("model", "haiku")
    }


def generate_brief(
    messages: List[Dict],
    tool_calls: List[str],
    files_created: Set[str],
    files_modified: Set[str],
    errors: List[Dict]
) -> str:
    """Generate a 1-2 sentence summary of the transcript."""
    parts = []

    if tool_calls:
        parts.append(f"Used {len(tool_calls)} tool types ({', '.join(tool_calls[:2])})")

    total_files = len(files_created) + len(files_modified)
    if total_files > 0:
        parts.append(f"modified {total_files} file(s)")

    if errors:
        parts.append(f"encountered {len(errors)} error(s)")
    else:
        parts.append("completed without errors")

    brief = "; ".join(parts) + "."

    # Truncate to ~150 chars max
    if len(brief) > 150:
        brief = brief[:147] + "..."

    return brief


def stream_jsonl_transcripts(transcripts_dir: Path) -> Dict[str, Tuple[Dict, List]]:
    """Stream parse all agent-*.jsonl files in directory."""
    agents = {}

    if not transcripts_dir.exists():
        return agents

    for jsonl_file in sorted(transcripts_dir.glob("agent-*.jsonl")):
        agent_id = jsonl_file.stem.replace("agent-", "")
        messages = []
        metadata = {}

        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("type") == "metadata":
                            metadata = obj
                        else:
                            messages.append(obj)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue

        if messages or metadata:
            agents[agent_id] = (metadata, messages)

    return agents


def create_brief(
    wave: str,
    agent_id: str,
    metadata: Dict,
    messages: List[Dict]
) -> Dict:
    """Create a brief dict from transcript data."""
    start_time = metadata.get("start_time", datetime.now(timezone.utc).isoformat())
    end_time = metadata.get("end_time", datetime.now(timezone.utc).isoformat())

    # Parse timestamps to compute duration
    try:
        start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        duration_sec = int((end - start).total_seconds())
    except Exception:
        duration_sec = 0

    tool_calls, tool_count = extract_tool_calls(messages)
    files_created, files_modified = extract_files_from_calls(messages)
    errors = extract_errors(messages)
    outcome = infer_outcome(messages, errors)
    token_usage = extract_token_usage(metadata)
    brief_text = generate_brief(messages, tool_calls, files_created, files_modified, errors)

    return {
        "wave": wave,
        "agent_id": agent_id,
        "start_time": start_time,
        "end_time": end_time,
        "duration_sec": duration_sec,
        "outcome": outcome,
        "top_tool_calls": tool_calls,
        "files_created": sorted(list(files_created)),
        "files_modified": sorted(list(files_modified)),
        "errors": errors,
        "token_usage": token_usage,
        "brief": brief_text,
        "brief_schema_version": 1
    }


def get_existing_agent_ids(ledger_path: Path) -> Set[str]:
    """Read already-digested agent IDs from ledger."""
    agent_ids = set()

    if not ledger_path.exists():
        return agent_ids

    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    agent_ids.add(obj.get("agent_id", ""))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return agent_ids


def append_briefs(ledger_path: Path, briefs: List[Dict]) -> int:
    """Append briefs to ledger file (create parent dir if needed)."""
    if not briefs:
        return 0

    # Ensure parent directory exists
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    # Append briefs
    try:
        with open(ledger_path, "a", encoding="utf-8") as f:
            for brief in briefs:
                f.write(json.dumps(brief) + "\n")
        return len(briefs)
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--transcripts-dir",
        type=Path,
        required=True,
        help="Directory containing agent-*.jsonl files"
    )
    parser.add_argument(
        "--wave",
        default="unknown",
        help="Wave ID for the briefs (default: unknown)"
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path(os.environ.get("AESOP_STATE_ROOT", "./state")),
        help="State directory root (default: $AESOP_STATE_ROOT or ./state)"
    )

    args = parser.parse_args()

    # Resolve paths
    transcripts_dir = args.transcripts_dir.resolve()
    ledger_path = args.state_root / "ledger" / "transcripts-brief.jsonl"

    if not transcripts_dir.exists():
        print(f"ERROR: transcripts directory not found: {transcripts_dir}", file=sys.stderr)
        sys.exit(1)

    # Read existing agent IDs (for idempotency)
    existing_ids = get_existing_agent_ids(ledger_path)

    # Stream and digest transcripts
    agents = stream_jsonl_transcripts(transcripts_dir)
    briefs_to_append = []

    for agent_id, (metadata, messages) in sorted(agents.items()):
        # Skip if already digested
        if agent_id in existing_ids:
            continue

        brief = create_brief(args.wave, agent_id, metadata, messages)
        briefs_to_append.append(brief)

    # Append to ledger
    count = append_briefs(ledger_path, briefs_to_append)

    if count > 0:
        print(f"Appended {count} brief(s) to {ledger_path}")

    sys.exit(0)


if __name__ == "__main__":
    main()
