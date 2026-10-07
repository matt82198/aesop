#!/usr/bin/env python3
"""
Wave audit tail — newest audit/verification outcomes (adversarial findings, verdicts).

Reads audit backlog and ledger to surface latest verification outcomes.
Compact live tail panel showing recent audit events and findings.

Returns audit tail data for the Activity view.
"""
import re
from datetime import datetime, timezone
from typing import Dict, List

import config


def _parse_audit_backlog_recent() -> List[Dict]:
    """Extract recent items from AUDIT-BACKLOG.md.

    Returns the most recent 5-10 P0/P1 items (inflight, todo, or done).

    Returns:
        list: [{"status": "✅|🔵|⬜", "tier": "P0|P1", "tag": "[tag]", "title": str, ...}, ...]
    """
    try:
        backlog_file = config.AUDIT_BACKLOG_FILE
        if not backlog_file.exists():
            return []

        content = backlog_file.read_text(encoding='utf-8')
        lines = content.split('\n')

        recent_items = []
        current_tier = None

        for line in lines:
            line_stripped = line.strip()

            # Detect tier headers
            if re.match(r'^##\s+(P0|P1|P2)', line_stripped):
                match = re.match(r'^##\s+(P0|P1|P2)', line_stripped)
                current_tier = match.group(1) if match else None
                continue

            if current_tier and line_stripped.startswith('- '):
                # Extract status emoji, tag, and title
                # Pattern: - 🔵 **[tag] Title**
                match = re.search(r'(✅|🔵|⬜|⏸)\s+\*\*\[([^\]]+)\]\s+(.+?)\*\*', line_stripped)
                if match:
                    status_emoji = match.group(1)
                    tag = match.group(2)
                    title = match.group(3)

                    recent_items.append({
                        "status": status_emoji,
                        "tier": current_tier,
                        "tag": tag,
                        "title": title,
                        "timestamp": None,  # AUDIT-BACKLOG doesn't have timestamps
                    })

        # Return most recent (last) items
        return recent_items[-10:] if recent_items else []
    except Exception as e:
        print(f"[wave_audit_tail] Error parsing audit backlog: {e}")
        return []


def _parse_ledger_recent_verdicts() -> List[Dict]:
    """Extract recent verdict outcomes from OUTCOMES-LEDGER.md.

    Looks for recent lines showing agent results: OK/FAILED/EMPTY/HUNG.

    Ledger format (9-column, optionally 7-column legacy):
      | ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |

    Verdict whitelist: only OK, FAILED, EMPTY, HUNG are valid.
    Forged/invalid verdicts are skipped.

    Returns:
        list: [{"agent": str, "verdict": "OK|FAILED|EMPTY|HUNG", "timestamp": iso, ...}, ...]
    """
    # Whitelist of valid verdicts
    VALID_VERDICTS = {"OK", "FAILED", "EMPTY", "HUNG"}

    try:
        ledger_file = config.LEDGER_FILE
        if not ledger_file.exists():
            return []

        content = ledger_file.read_text(encoding='utf-8')
        lines = content.split('\n')

        recent_verdicts = []

        # Parse ledger table rows (supports 7-column and 9-column formats)
        for line in reversed(lines[-50:]):  # Check last 50 lines
            line_stripped = line.strip()
            # Look for table rows with verdict info
            # Pattern: | timestamp | agent_type | model | ... | verdict | ... |
            if '|' not in line_stripped:
                continue

            parts = [p.strip() for p in line_stripped.split('|')]

            # Accept both 7-column (9 parts) and 9-column (11 parts) formats
            # 7 columns: ['', col1, col2, col3, col4, col5, col6, col7, '']  -> parts[7] = verdict
            # 9 columns: ['', col1, col2, col3, col4, col5, col6, col7, col8, col9, ''] -> parts[7] = verdict
            if len(parts) < 9:
                continue

            # Try to extract timestamp (parts[1]), agent_type (parts[2]), and verdict (parts[7])
            try:
                timestamp_str = parts[1]
                agent_str = parts[2]
                # IMPORTANT: verdict is at parts[7], not parts[3] (which is model)
                verdict_str = parts[7].upper()

                # Validate verdict against whitelist
                if verdict_str not in VALID_VERDICTS:
                    # Skip forged/invalid verdicts
                    continue

                # Verify timestamp looks like ISO format
                if 'T' not in timestamp_str and '-' not in timestamp_str[:10]:
                    continue

                recent_verdicts.append({
                    "timestamp": timestamp_str,
                    "agent": agent_str.split('-')[-1][:13],  # Short agent ID
                    "verdict": verdict_str,
                })
            except (IndexError, ValueError):
                pass

        # Return most recent (first in reversed list)
        return recent_verdicts[-10:] if recent_verdicts else []
    except Exception as e:
        print(f"[wave_audit_tail] Error parsing ledger: {e}")
        return []


def get_wave_audit_tail() -> Dict:
    """Get audit tail data for the current wave.

    Returns latest audit/verification outcomes as a compact tail.

    Returns:
        dict: {
            "available": bool,
            "audit_items": [
                {
                    "type": "audit_backlog|verdict",
                    "timestamp": "2026-07-21T12:34:56Z" or null,
                    "status": "✅|🔵|⬜|...",
                    "tier": "P0|P1|P2",
                    "tag": "[sec]|[ui]|...",
                    "title": str,
                    "verdict": "OK|FAILED|...",
                    "agent": str,
                },
                ...
            ],
            "at": "2026-07-21T12:34:56Z"
        }
    """
    try:
        audit_items = []

        # Get recent backlog items
        backlog_items = _parse_audit_backlog_recent()
        for item in backlog_items:
            audit_items.append({
                "type": "audit_backlog",
                **item,
            })

        # Get recent verdict outcomes
        verdict_items = _parse_ledger_recent_verdicts()
        for item in verdict_items:
            audit_items.append({
                "type": "verdict",
                **item,
            })

        # Sort by timestamp (nulls last) and limit to 15 most recent
        def sort_key(item):
            ts = item.get("timestamp")
            if ts is None:
                return ""
            return ts

        audit_items.sort(key=sort_key, reverse=True)
        audit_items = audit_items[:15]

        return {
            "available": bool(audit_items),
            "audit_items": audit_items,
            "at": datetime.now(timezone.utc).isoformat() + "Z",
        }
    except Exception as e:
        print(f"[wave_audit_tail] Uncaught error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "available": False,
            "error": str(e),
            "audit_items": [],
            "at": datetime.now(timezone.utc).isoformat() + "Z"
        }
