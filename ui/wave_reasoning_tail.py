#!/usr/bin/env python3
"""
Wave reasoning tail — per-agent live transcript activity summary.

Shows latest reasoning/transcript activity for each live agent in a compact,
redacted format. Reuses transcript_digest.py's redaction + summarization patterns.

Returns reasoning tail data for the Activity view.
"""
import json
import re
from datetime import datetime, timezone
from typing import Dict, Optional

import config
import wave_dispatch


# Redaction patterns (from transcript_digest.py, simplified)
def redact_text(text: str) -> str:
    """Aggressively redact secrets, paths, emails from transcript text."""
    if not text:
        return text

    # Redact common secrets
    text = re.sub(r'sk-[A-Za-z0-9_\-]{20,}', '[REDACTED]', text)  # OpenAI/Anthropic keys
    text = re.sub(r'ghp_[A-Za-z0-9_]{20,}', '[REDACTED]', text)   # GitHub tokens
    text = re.sub(r'AKIA[0-9A-Z]{16}', '[REDACTED]', text)         # AWS keys

    # Redact paths (Windows and POSIX)
    text = re.sub(r'[A-Za-z]:\\[^\\/:*<>|]*', '[PATH]', text)     # Windows paths
    text = re.sub(r'/[^/:*<>|]*', '[PATH]', text)                  # POSIX paths

    # Redact emails
    text = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[EMAIL]', text)

    # Redact usernames
    text = re.sub(r'\b(?:matt8|matt82198|John|Jack)\b', '[USER]', text, flags=re.IGNORECASE)

    # Redact repo names
    text = re.sub(r'\b(?:aesop|conductor3|tr-sample-tracker|ecm-ai)\b', '[REPO]', text, flags=re.IGNORECASE)

    return text


def _extract_agent_reasoning(agent_id: str) -> Optional[str]:
    """Extract latest reasoning/activity summary from agent transcript.

    Looks for the agent's transcript in ~/.claude/projects/*/memory/agent-*.jsonl
    and extracts the latest thinking/tool-call sequence as a brief summary.

    Returns:
        Brief summary string (redacted) or None if transcript not found/readable.
    """
    try:
        transcripts_root = config.TRANSCRIPTS_ROOT
        if not transcripts_root or not transcripts_root.exists():
            return None

        # Search for agent transcript file matching this agent_id
        for jsonl_file in transcripts_root.glob('**/agent-*.jsonl'):
            # Check if this agent file matches
            if agent_id not in str(jsonl_file):
                continue

            # Read last few lines (most recent messages)
            try:
                lines = jsonl_file.read_text(encoding='utf-8', errors='ignore').split('\n')
                recent_lines = [l for l in lines if l.strip()][-5:]  # Last 5 messages

                if not recent_lines:
                    return None

                # Extract and summarize message types
                summary_parts = []
                for line in recent_lines:
                    try:
                        msg = json.loads(line)
                        msg_type = msg.get('type', 'unknown')
                        role = msg.get('role', msg_type)

                        # Extract brief content
                        content = msg.get('content', '')
                        if isinstance(content, list):
                            content = str(content)[:50]
                        elif isinstance(content, str):
                            content = content[:50]

                        if msg_type == 'tool_use':
                            summary_parts.append(f'tool:{msg.get("name", "?")[:8]}')
                        elif msg_type == 'tool_result':
                            summary_parts.append('result')
                        elif role == 'assistant':
                            summary_parts.append('thinking')
                        elif role == 'user':
                            summary_parts.append('prompt')
                    except (json.JSONDecodeError, ValueError):
                        pass

                if summary_parts:
                    summary = ' → '.join(summary_parts)
                    return redact_text(summary)

            except (OSError, IOError):
                pass

        return None
    except Exception as e:
        print(f"[wave_reasoning_tail] Error extracting reasoning: {e}")
        return None


def get_wave_reasoning_tail() -> Dict:
    """Get reasoning tail data for live agents in current wave.

    Returns per-agent latest transcript activity summary (redacted).

    Returns:
        dict: {
            "available": bool,
            "agents": [
                {
                    "id": "agent-id",
                    "phase": "dispatch|thinking|tool-use|stall|done",
                    "reasoning": "brief redacted summary",
                    "activity_age_sec": int,
                    "token_estimate": int,
                },
                ...
            ],
            "at": "2026-07-21T12:34:56Z"
        }
    """
    try:
        dispatch_data = wave_dispatch.get_wave_dispatch()
        if not dispatch_data or not dispatch_data.get("available"):
            return {
                "available": False,
                "error": "No active workflow",
                "agents": [],
                "at": datetime.now(timezone.utc).isoformat() + "Z"
            }

        agents = []
        for agent in dispatch_data.get("agents", []):
            agent_id = agent.get("id", "unknown")

            # Extract reasoning/activity summary
            reasoning = _extract_agent_reasoning(agent_id)

            agents.append({
                "id": agent_id,
                "phase": agent.get("phase", "unknown"),
                "reasoning": reasoning or "(no recent activity)",
                "activity_age_sec": agent.get("last_activity_age_sec", 0),
                "token_estimate": agent.get("token_estimate", 0),
                "warnings": agent.get("warnings", []),
            })

        return {
            "available": True,
            "agents": agents,
            "at": datetime.now(timezone.utc).isoformat() + "Z",
        }
    except Exception as e:
        print(f"[wave_reasoning_tail] Uncaught error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "available": False,
            "error": str(e),
            "agents": [],
            "at": datetime.now(timezone.utc).isoformat() + "Z"
        }
