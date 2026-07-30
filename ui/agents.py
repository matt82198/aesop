#!/usr/bin/env python3
"""Aesop UI — agent transcript reading + path-traversal-safe id handling (wave-9 split)."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import config


def _path_is_contained(child, root):
    """Check if child path is contained within root path (no traversal).

    Returns True if child is under root, False if it escapes (e.g., via ..).
    Uses Path.is_relative_to (Python 3.9+) with a fallback for older runtimes.

    Args:
        child: Path object (typically resolved)
        root: Path object (typically resolved)

    Returns:
        bool: True if child is contained within root, False otherwise
    """
    try:
        return child.is_relative_to(root.resolve())
    except AttributeError:
        # Path.is_relative_to requires Python 3.9+; fall back for older runtimes.
        try:
            child.relative_to(root.resolve())
            return True
        except ValueError:
            return False


def sanitize_agents_for_broadcast(agents):
    """Strip large prompt fields from agents before SSE broadcast.

    Wave-19 fix: Remove promptFull, dispatch_prompt, and other multi-KB fields
    from agents snapshots before sending via SSE. The React app never reads
    these full prompts (it lazy-fetches them via /agent?id= endpoint), so
    stripping them saves bandwidth on every tick. Keeps summary fields only.
    """
    sanitized = []
    for agent in agents:
        if not isinstance(agent, dict):
            sanitized.append(agent)
            continue
        # Drop only the multi-KB prompt fields; keep everything else the
        # frontend Agent contract reads (ui/web/src/lib/types.ts) — id,
        # project, status, age_s, hint, startedAt, lastActivity,
        # runtimeSeconds, tokensUsed, taskLabel.
        strip_fields = {"promptFull", "dispatch_prompt"}
        summary = {k: v for k, v in agent.items() if k not in strip_fields}
        sanitized.append(summary)
    return sanitized


def get_fleet_agents():
    """Detect running subagents by calling dash-extra.mjs --json.

    Fallback (test-only): when AESOP_PROOF_FIXTURES=1 is set, read fixture agents from
    _collector.json if dash-extra.mjs returns no agents (used by browser proofs that
    don't have running agent processes). This env var is NEVER set in production, so
    the fallback is safely gated.

    dash-extra.mjs truncates agent ids to 13 characters for display. With enough
    concurrently-active agents, two distinct agents can share the same 13-char
    prefix and collide onto the same id. The dashboard keys DOM rows (and the
    click-to-expand lookup) by this id, so a collision silently merges two
    different agents into one row and can show mismatched detail on click. Since
    dash-extra.mjs is out of scope here, disambiguate post-hoc: keep the original
    (display-friendly) id as a prefix, but suffix it to guarantee uniqueness.
    """
    # Zero-key demo mode: the fleet-agent list is the one collector that cannot
    # be file-seeded (it shells out to node dash-extra.mjs), so serve the
    # fabricated, now-relative demo roster directly. No-op in default mode.
    import demo
    if demo.enabled():
        return demo.get_demo_agents()

    agents = []
    try:
        # Call the working detector (dash-extra.mjs) with --json flag
        dash_extra_path = config.AESOP_ROOT / "dash" / "dash-extra.mjs"
        if dash_extra_path.exists():
            result = subprocess.run(
                ["node", str(dash_extra_path), "--json"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=5
            )
            if result.returncode == 0 and result.stdout:
                agents = json.loads(result.stdout.strip())
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    except Exception:
        pass

    # Fallback (test-only): if no agents detected by dash-extra.mjs AND
    # AESOP_PROOF_FIXTURES is set, try reading from _collector.json
    # (used by browser proofs that create fixture agents in state/_collector.json).
    # This is gated by an explicit env var so it never fires in production.
    if not agents and os.getenv("AESOP_PROOF_FIXTURES"):
        try:
            collector_json = config.STATE_DIR / "_collector.json"
            if collector_json.exists():
                with open(collector_json, encoding='utf-8') as f:
                    data = json.load(f)
                    agents = data.get("agents", [])
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    seen = {}
    for a in agents:
        if not isinstance(a, dict):
            continue
        aid = a.get("id", "")
        if aid in seen:
            seen[aid] += 1
            a["id"] = f"{aid}-{seen[aid]}"
        else:
            seen[aid] = 1
    return agents

_AGENT_ID_FORBIDDEN = re.compile(r'\.\.|[/\\*?\[\]]')

# Fingerprint caching: prevent expensive recursive glob on every collector tick (~1Hz).
# Cache the fingerprint for N seconds, recompute only when window expires.
# This keeps the cache-busting purpose (detect real changes), but throttles the cost.
_FINGERPRINT_CACHE = {"value": None, "expires": 0.0}
_FINGERPRINT_CACHE_TTL = 5.0  # seconds; can be overridden by tests

# Transcript-tail bounds (defense against loading a whole multi-MB transcript
# into memory and against emitting an unbounded payload to the browser).
TRANSCRIPT_TAIL_LINES = 40          # last N NDJSON lines rendered in the drawer
TRANSCRIPT_TAIL_MAX_BYTES = 256 * 1024   # only ever seek-read this much from EOF
TRANSCRIPT_TAIL_ENTRY_MAXLEN = 2000      # per-entry text cap (chars)

# Best-effort credential redaction for the transcript tail. These are the
# high-confidence, unambiguous secret FORMATS (mirroring the fatal rules in
# tools/secret_scan.py) plus bearer/authorization values. This is defense in
# depth, NOT a guarantee: a novel or bespoke credential format can still slip
# through, so the tail is a read-only convenience, never an audited-clean feed.
# tools/secret_scan.py is deliberately NOT imported (it is not on the UI import
# path and pulling it in would couple the dashboard to the push-gate tool); the
# patterns are duplicated here in a tiny, stdlib-only form.
_REDACTION_PATTERNS = [
    re.compile(r'-----BEGIN[^\n]*PRIVATE KEY-----', re.IGNORECASE),
    re.compile(r'AKIA[0-9A-Z]{16}'),
    re.compile(r'(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{20,}'),
    re.compile(r'xox[baprs]-[A-Za-z0-9-]{10,}'),
    re.compile(r'sk-[A-Za-z0-9_\-]{20,}'),
    re.compile(r'(?i:bearer)\s+[A-Za-z0-9._\-]{20,}'),
    # key/secret/password/token = <value> (quoted or bare) — mask the value.
    re.compile(
        r'(?i)((?:api[_-]?key|secret|password|passwd|token|authorization)'
        r'\s*[:=]\s*)([^\s"\',}]{12,})'
    ),
]


def _redact_secrets(text):
    """Best-effort masking of high-confidence credential formats in free text.

    Returns text with any matched secret replaced by a short prefix + a
    REDACTED marker (so an operator can still eyeball that *something* was
    there). Never raises. See _REDACTION_PATTERNS for the (intentionally
    limited) coverage and its documented limits.
    """
    if not text:
        return text

    def _mask(match):
        groups = match.groups()
        if len(groups) == 2:
            # key/value form: keep the "key = " prefix, mask only the value.
            return f"{groups[0]}***REDACTED***"
        token = match.group(0)
        prefix = token[:4] if len(token) > 8 else ""
        return f"{prefix}***REDACTED***"

    for pat in _REDACTION_PATTERNS:
        try:
            text = pat.sub(_mask, text)
        except re.error:
            continue
    return text


def _resolve_transcript_path(agent_id):
    """Resolve an agent id to its on-disk transcript file, path-traversal-safe.

    Shared by extract_agent_dispatch_prompt() and get_agent_detail() so the
    security contract (reject `.. / \\ * ? [ ]` before any glob, then verify the
    resolved match stays inside config.TRANSCRIPTS_ROOT) can never drift between
    the two endpoints.

    Returns (Path, None) on a clean match, or (None, error_dict) otherwise.
    error_dict carries "invalid": True for a rejected input (caller maps to
    HTTP 400); its absence means a well-formed id that simply had no transcript
    (caller maps to HTTP 404). Never raises for the expected failure modes.
    """
    if not agent_id or _AGENT_ID_FORBIDDEN.search(agent_id):
        return None, {"error": "invalid agent id", "invalid": True}

    if not config.TRANSCRIPTS_ROOT.exists():
        return None, {"error": f"transcripts root not found at {config.TRANSCRIPTS_ROOT}"}

    matches = sorted(
        config.TRANSCRIPTS_ROOT.glob(f"**/agent-{agent_id}*.jsonl"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not matches:
        return None, {"error": f"transcript not found for {agent_id}"}

    output_file = matches[0]
    if not _path_is_contained(output_file.resolve(), config.TRANSCRIPTS_ROOT):
        return None, {"error": "resolved path outside transcripts root", "invalid": True}

    return output_file, None


def _read_tail_lines(path, max_lines=TRANSCRIPT_TAIL_LINES,
                     max_bytes=TRANSCRIPT_TAIL_MAX_BYTES):
    """Return roughly the last `max_lines` text lines of a file, bounded.

    Seeks to at most `max_bytes` from EOF so a huge transcript is never fully
    read into memory. Decodes utf-8 with errors='replace' (wave-27 lesson) so
    undecodable bytes never raise. When the read started mid-file the first
    (probably partial) line is dropped. Returns (lines, truncated) where
    truncated is True if the file was larger than the window we read.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return [], False
    to_read = min(size, max_bytes)
    try:
        with open(path, 'rb') as f:
            if size > to_read:
                f.seek(size - to_read)
            raw = f.read()
    except OSError:
        return [], False
    text = raw.decode('utf-8', errors='replace')
    lines = text.splitlines()
    windowed = size > to_read
    if windowed and len(lines) > 1:
        lines = lines[1:]  # drop the partial leading line
    truncated = windowed or len(lines) > max_lines
    return lines[-max_lines:], truncated


def _extract_line_text(obj):
    """Pull a readable text summary out of one parsed transcript record.

    Handles the shapes Claude Code emits: message.content as a plain string, or
    as a list of content blocks (text / tool_use / tool_result / thinking).
    Returns a plain string (never HTML); the frontend renders it as text.
    """
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if content is None:
        content = obj.get("content")

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type")
                if btype == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif btype == "thinking" and isinstance(block.get("thinking"), str):
                    parts.append(f"[thinking] {block['thinking']}")
                elif btype == "tool_use":
                    parts.append(f"[tool_use: {block.get('name', '?')}]")
                elif btype == "tool_result":
                    parts.append("[tool_result]")
        return "\n".join(p for p in parts if p)
    return ""


def _summarize_transcript_line(raw_line):
    """Turn one NDJSON line into a {"type", "text"} tail entry, or None to skip.

    Robust to non-JSON / non-dict lines (returned as a "raw" entry). Text is
    secret-redacted and length-capped so the payload stays bounded and XSS-safe
    (plain strings only — the client escapes on render).
    """
    raw_line = raw_line.strip()
    if not raw_line:
        return None
    try:
        obj = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        return {"type": "raw", "text": _redact_secrets(raw_line[:TRANSCRIPT_TAIL_ENTRY_MAXLEN])}
    if not isinstance(obj, dict):
        return {"type": "raw", "text": _redact_secrets(str(obj)[:TRANSCRIPT_TAIL_ENTRY_MAXLEN])}
    entry_type = str(obj.get("type", "unknown"))
    text = _extract_line_text(obj)
    text = _redact_secrets(text[:TRANSCRIPT_TAIL_ENTRY_MAXLEN])
    return {"type": entry_type, "text": text}


def get_agent_detail(agent_id, tail_lines=TRANSCRIPT_TAIL_LINES):
    """Agent detail for the inspector drawer: dispatch prompt + bounded tail.

    Read-only. Reuses _resolve_transcript_path() for the same path-traversal /
    glob-injection protection as extract_agent_dispatch_prompt(), then reads
    ONLY the last ~tail_lines lines of the transcript (never the whole file) and
    returns them as a list of {type, text} entries with best-effort credential
    redaction applied.

    Success shape:
        {id, dispatch_prompt, dispatcher, model, message_count, first_seen,
         last_activity, transcript_tail: [{type, text}, ...], tail_truncated}

    Error shape (same convention as extract_agent_dispatch_prompt):
        {"error": str}                 -> caller returns 404
        {"error": str, "invalid": True} -> caller returns 400
    Never raises: an unexpected failure degrades to a generic error dict.
    """
    try:
        output_file, err = _resolve_transcript_path(agent_id)
        if err is not None:
            return err

        stat = output_file.stat()
        first_seen = int(stat.st_mtime)
        last_activity = int(stat.st_mtime)

        dispatch_prompt = None
        model = None
        parent_uuid = None
        message_count = 0

        # Header parse: first line = dispatch prompt; scan a few for the model.
        # Bounded read of just the head lines (the tail is read separately).
        try:
            with open(output_file, 'r', encoding='utf-8', errors='replace') as f:
                head = []
                for i, line in enumerate(f):
                    if i < 20:
                        head.append(line)
                    message_count += 1
            if head:
                try:
                    first = json.loads(head[0])
                    if first.get('type') == 'user':
                        msg = first.get('message', {})
                        dispatch_prompt = msg.get('content', '')
                        parent_uuid = first.get('parentUuid')
                except (json.JSONDecodeError, KeyError):
                    pass
                for line in head[1:]:
                    try:
                        obj = json.loads(line)
                        if obj.get('type') == 'assistant' and not model and 'model' in obj:
                            model = obj.get('model')
                    except (json.JSONDecodeError, KeyError):
                        pass
        except OSError:
            pass

        # dispatch_prompt may be a content-block list; normalise to a string.
        if isinstance(dispatch_prompt, list):
            dispatch_prompt = _extract_line_text({"content": dispatch_prompt})
        dispatch_prompt = _redact_secrets(dispatch_prompt or "")

        tail_raw, truncated = _read_tail_lines(output_file, max_lines=tail_lines)
        transcript_tail = []
        for line in tail_raw:
            entry = _summarize_transcript_line(line)
            if entry is not None:
                transcript_tail.append(entry)

        dispatcher = "main thread" if parent_uuid is None else "parent agent"

        return {
            "id": agent_id,
            "dispatch_prompt": dispatch_prompt,
            "dispatcher": dispatcher,
            "model": model or "unknown",
            "message_count": message_count,
            "first_seen": first_seen,
            "last_activity": last_activity,
            "transcript_tail": transcript_tail,
            "tail_truncated": truncated,
        }
    except Exception as e:
        print(f"[get_agent_detail] Uncaught exception: {e}", file=sys.stderr)
        return {"error": "Failed to load agent detail"}


def extract_agent_dispatch_prompt(agent_id):
    """
    Extract dispatch prompt and metadata from agent jsonl transcript.
    Returns dict with prompt, dispatcher, model, activity times, and message count.
    Robust: missing/invalid file -> {error: "..."}
    Security: rejects agent_id containing path-traversal or glob-metacharacter
    sequences before building any glob pattern, and refuses to return a match
    that resolves outside config.TRANSCRIPTS_ROOT (defense in depth). Error results
    carry "invalid": True when the input itself was rejected, so callers can
    map that to an HTTP 400 rather than a plain 404.

    CRITICAL: Use prefix-matching via glob, not exact match. The dashboard (dash-extra.mjs)
    scans for agent-*.jsonl files and emits a 13-char truncated ID; files on disk carry
    full IDs (e.g., agent-a77b995bcdb953e9c1234567.jsonl). This function must search for
    the same file format that the dashboard actually finds.
    """
    try:
        # Path-traversal / glob-injection protection + prefix-match resolution
        # live in the shared _resolve_transcript_path() so this endpoint and the
        # inspector's get_agent_detail() can never drift apart on security.
        output_file, err = _resolve_transcript_path(agent_id)
        if err is not None:
            return err

        dispatch_prompt = None
        message_count = 0
        model = None
        parent_uuid = None
        first_seen = None
        last_activity = None

        # Parse NDJSON (one JSON per line)
        with open(output_file, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            message_count = len(lines)

            # Get file mtime for activity time
            stat = output_file.stat()
            first_seen = int(stat.st_mtime)
            last_activity = int(stat.st_mtime)

            # First line should be type="user" with the dispatch prompt
            if lines:
                try:
                    first_line = json.loads(lines[0])
                    if first_line.get('type') == 'user':
                        msg = first_line.get('message', {})
                        dispatch_prompt = msg.get('content', '')
                        parent_uuid = first_line.get('parentUuid')
                except (json.JSONDecodeError, KeyError):
                    pass

            # Scan for model info in assistant messages
            for line in lines[1:20]:  # Check first ~20 lines
                try:
                    obj = json.loads(line)
                    if obj.get('type') == 'assistant' and not model:
                        if 'model' in obj:
                            model = obj.get('model')
                except (json.JSONDecodeError, KeyError):
                    pass

        if not dispatch_prompt:
            return {"error": f"no dispatch prompt found"}

        # dispatch_prompt may be a content-block list; normalise to a string,
        # then redact credentials before this leaves the process (same
        # contract as get_agent_detail() — wave-32 fix, this endpoint had
        # drifted and was returning secrets unredacted).
        if isinstance(dispatch_prompt, list):
            dispatch_prompt = _extract_line_text({"content": dispatch_prompt})
        dispatch_prompt = _redact_secrets(dispatch_prompt or "")

        # Infer dispatcher: if parentUuid is null, it's main thread; otherwise parent agent
        dispatcher = "main thread" if parent_uuid is None else "parent agent"

        return {
            "id": agent_id,
            "dispatch_prompt": dispatch_prompt,
            "dispatcher": dispatcher,
            "model": model or "unknown",
            "message_count": message_count,
            "first_seen": first_seen,
            "last_activity": last_activity,
        }
    except Exception as e:
        print(f"[extract_agent_dispatch_prompt] Uncaught exception: {e}", file=sys.stderr)
        return {"error": "Failed to extract dispatch prompt"}

def _transcripts_fingerprint_uncached():
    """Cheap fs-stat-only fingerprint of the transcripts tree (no caching).

    Used to decide whether it's worth re-invoking `node dash-extra.mjs` (which is
    comparatively expensive: process spawn + re-parsing every agent transcript).
    Only file count + max mtime — no file content is read.

    This is the raw implementation; see _transcripts_fingerprint() for the
    cache-throttled wrapper.
    """
    try:
        if not config.TRANSCRIPTS_ROOT.exists():
            return (0, 0.0)
        count = 0
        latest = 0.0
        for p in config.TRANSCRIPTS_ROOT.glob("**/agent-*.jsonl"):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            count += 1
            if mtime > latest:
                latest = mtime
        return (count, latest)
    except Exception:
        return (0, 0.0)


def _transcripts_fingerprint():
    """Cached wrapper around _transcripts_fingerprint_uncached().

    Throttles fingerprint recomputation to at most once per _FINGERPRINT_CACHE_TTL
    seconds (default 5s, configurable). Returns cached value until window expires.
    This keeps the cache-busting purpose (detect real agent changes) but prevents
    the expensive recursive glob from running on every collector tick (~1Hz).

    Returns:
        tuple: (file_count, latest_mtime) — same format as uncached version.
    """
    global _FINGERPRINT_CACHE
    now = time.time()

    # If cache is still valid, return cached value
    if _FINGERPRINT_CACHE["value"] is not None and now < _FINGERPRINT_CACHE["expires"]:
        return _FINGERPRINT_CACHE["value"]

    # Cache expired or uninitialized; recompute
    value = _transcripts_fingerprint_uncached()
    _FINGERPRINT_CACHE["value"] = value
    _FINGERPRINT_CACHE["expires"] = now + _FINGERPRINT_CACHE_TTL
    return value
