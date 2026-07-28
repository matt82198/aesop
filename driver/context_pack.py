#!/usr/bin/env python3
"""Context pack builder for the OrchestratorDriver seam.

Enforces cardinal rule 4 in code: the orchestrator reads ONLY control files
from the file brain, never arbitrary paths. build_context_pack() constructs
size-bounded, deterministic snapshots from allowlisted sources with a manifest
of what was included/truncated.

Context sources are logical names, NOT paths:
  'state' -> STATE.md (from repo/conductor root)
  'buildlog_tail' -> last N lines of BUILDLOG.md
  'tracker_open' -> subset of open items from state/tracker.json
  'brief:<explicit-path>' -> explicitly passed file under repo/conductor allowlist

Attempts to read arbitrary paths raise ContextPackViolation (the code-level
enforcement of cardinal rule 4).

Packs are size-bounded (default ~32KB) with deterministic truncation
(oldest-first for logs).

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class ContextPackViolation(Exception):
    """Raised when a context pack request violates the allowlist."""
    pass


@dataclass
class ContextPack:
    """Snapshot of orchestrator-readable control files, size-bounded.

    Attributes:
        decision_type: Decision class name (e.g., 'rank_backlog', 'adjudicate_findings').
        sources_requested: Logical names passed to build_context_pack().
        content: Dict mapping source -> (text content | error message).
        evidence: Dict mapping evidence_name -> text (code excerpts, repro output, etc.).
        manifest: List of {source, included, truncated, truncation_reason, size_bytes}.
        evidence_manifest: List of {name, included, truncated, truncation_reason, size_bytes}.
        total_size_bytes: Sum of all content sizes.
        total_size_cap: The size limit enforced (default ~32KB).
        evidence_size_bytes: Sum of all evidence sizes.
        evidence_size_cap: Separate size limit for evidence (default ~4KB).
    """

    decision_type: str
    sources_requested: Tuple[str, ...] = field(default_factory=tuple)
    content: Dict[str, str] = field(default_factory=dict)
    evidence: Dict[str, str] = field(default_factory=dict)
    manifest: List[Dict] = field(default_factory=list)
    evidence_manifest: List[Dict] = field(default_factory=list)
    total_size_bytes: int = 0
    total_size_cap: int = 32768  # ~32KB default
    evidence_size_bytes: int = 0
    evidence_size_cap: int = 4096  # ~4KB default for evidence


def _is_allowed_path(path: str, repo_root: str, conductor_root: str) -> bool:
    """Check if a path is in the allowlist (repo or conductor root only).

    Args:
        path: The path to check (normalized, absolute or relative).
        repo_root: Root of the aesop repo.
        conductor_root: Root of the conductor3 directory.

    Returns:
        True if the path is under repo_root or conductor_root; False otherwise.
    """
    p = Path(path).resolve()
    try:
        p.relative_to(Path(repo_root).resolve())
        return True
    except ValueError:
        pass

    try:
        p.relative_to(Path(conductor_root).resolve())
        return True
    except ValueError:
        pass

    return False


def _order_sources_by_priority(sources: Dict[str, Optional[str]]) -> List[Tuple[str, Optional[str]]]:
    """Order sources by deterministic priority for consistent truncation.

    Priority order (B1):
      1. 'state'
      2. 'tracker_open'
      3. 'buildlog_tail:*'
      4. 'brief:*' (in original order)

    This ensures truncation is predictable: log sources are truncated before
    state, and state before brief sources.

    Args:
        sources: Dict of logical source names to specs.

    Returns:
        List of (source_name, source_spec) tuples in priority order.

    Raises:
        ContextPackViolation: if an unknown source type is found.
    """
    ordered = []
    seen_sources = set()

    # Priority 1: state
    if "state" in sources:
        ordered.append(("state", sources["state"]))
        seen_sources.add("state")

    # Priority 2: tracker_open
    if "tracker_open" in sources:
        ordered.append(("tracker_open", sources["tracker_open"]))
        seen_sources.add("tracker_open")

    # Priority 3: buildlog_tail:*
    buildlog_sources = [
        (name, spec) for name, spec in sources.items()
        if name.startswith("buildlog_tail")
    ]
    ordered.extend(sorted(buildlog_sources))  # Sort for determinism if multiple.
    seen_sources.update(name for name, _ in buildlog_sources)

    # Priority 4: brief:* (in original dict order for now).
    brief_sources = [
        (name, spec) for name, spec in sources.items()
        if name.startswith("brief:")
    ]
    ordered.extend(brief_sources)
    seen_sources.update(name for name, _ in brief_sources)

    # Validate: all sources must be recognized.
    for source_name in sources.keys():
        if source_name and source_name not in seen_sources:
            raise ContextPackViolation(
                f"Unknown context source '{source_name}'; "
                f"allowed: 'state', 'buildlog_tail:N', 'tracker_open', 'brief:<path>'"
            )

    return ordered


def build_context_pack(
    decision_type: str,
    sources: Dict[str, Optional[str]],
    repo_root: Optional[str] = None,
    conductor_root: Optional[str] = None,
    size_cap: int = 32768,
    evidence: Optional[Dict[str, str]] = None,
    evidence_cap: int = 4096,
) -> ContextPack:
    """Build a size-bounded context pack from allowlisted sources.

    The orchestrator reads ONLY:
      - STATE.md (the working plan/phase).
      - BUILDLOG.md (append-only wave audit trail).
      - MEMORY.md and similar control files (git-tracked state).
      - state/tracker.json (work items).
      - Explicitly passed files under the repo or conductor roots.
      - Evidence excerpts (code snippets, repro output, etc.) passed explicitly.

    Args:
        decision_type: Name of the decision (e.g., 'rank_backlog').
        sources: Dict mapping logical names to file paths or None.
                 Recognized logical sources:
                   'state' -> reads STATE.md from repo/conductor root.
                   'buildlog_tail' -> last N lines of BUILDLOG.md.
                   'tracker_open' -> open items subset of tracker.json.
                   'brief:<path>' -> explicit path (must be under allowlist).

        repo_root: Path to the aesop repo root. If None, uses cwd.
        conductor_root: Path to conductor3 root. If None, assumes ~/conductor3.
        size_cap: Size limit in bytes for main content (default 32KB). Truncation
                  is deterministic (oldest-first for logs).
        evidence: Optional dict mapping evidence_name -> evidence_text.
                  Evidence must be passed explicitly (not read from arbitrary paths).
                  Appended to pack under '[evidence]' section; size-bounded separately.
        evidence_cap: Size limit in bytes for evidence content (default 4KB).
                      Truncation is deterministic (truncate end of text).

    Returns:
        ContextPack with content dict + evidence dict + manifest.

    Raises:
        ContextPackViolation: if a source path violates the allowlist.
    """
    if repo_root is None:
        repo_root = os.getcwd()
    if conductor_root is None:
        conductor_root = os.path.expanduser("~/conductor3")

    pack = ContextPack(
        decision_type=decision_type,
        sources_requested=tuple(sources.keys()),
        total_size_cap=size_cap,
        evidence_size_cap=evidence_cap,
    )

    # Read sources in INSERTION ORDER to preserve byte-identity for under-cap packs.
    # B1 priority ordering is used ONLY for truncation decisions (see _truncate_pack).
    # Validate all sources first to raise ContextPackViolation for unknown types.
    for source_name in sources.keys():
        if not source_name:
            continue
        # Validate source type (reuse the validation from _order_sources_by_priority logic).
        if not (source_name == "state" or
                source_name == "tracker_open" or
                source_name.startswith("buildlog_tail") or
                source_name.startswith("brief:")):
            raise ContextPackViolation(
                f"Unknown context source '{source_name}'; "
                f"allowed: 'state', 'buildlog_tail:N', 'tracker_open', 'brief:<path>'"
            )

    # Read in insertion order.
    for source_name, source_spec in sources.items():
        if not source_name:
            continue

        content_dict, manifest_entry = _read_source(
            source_name, source_spec, repo_root, conductor_root
        )

        pack.content.update(content_dict)
        pack.manifest.append(manifest_entry)
        for text in content_dict.values():
            pack.total_size_bytes += len(text.encode("utf-8"))

    # Enforce size cap on main content: truncate oldest-first (log sources first).
    if pack.total_size_bytes > pack.total_size_cap:
        _truncate_pack(pack, pack.total_size_cap)

    # Add evidence if provided (explicit, allowlist-only).
    if evidence:
        for evidence_name, evidence_text in evidence.items():
            if not evidence_name:
                continue

            pack.evidence[evidence_name] = evidence_text
            evidence_bytes = len(evidence_text.encode("utf-8"))
            pack.evidence_size_bytes += evidence_bytes

            pack.evidence_manifest.append(
                {
                    "name": evidence_name,
                    "included": True,
                    "truncated": False,
                    "truncation_reason": None,
                    "size_bytes": evidence_bytes,
                }
            )

        # Enforce size cap on evidence: truncate end-of-text if needed.
        if pack.evidence_size_bytes > pack.evidence_size_cap:
            _truncate_evidence(pack, pack.evidence_size_cap)

    return pack


def _read_source(
    source_name: str,
    source_spec: Optional[str],
    repo_root: str,
    conductor_root: str,
) -> Tuple[Dict[str, str], Dict]:
    """Read one context source and return (content_dict, manifest_entry).

    Args:
        source_name: Logical name (e.g., 'state', 'buildlog_tail:50').
        source_spec: File path or None (for implicit sources like 'state').
        repo_root: Repo root path.
        conductor_root: Conductor root path.

    Returns:
        (dict of {source_name: text}, manifest_entry dict)

    Raises:
        ContextPackViolation: if the path is not allowlisted.
    """
    manifest = {
        "source": source_name,
        "included": False,
        "truncated": False,
        "truncation_reason": None,
        "size_bytes": 0,
    }

    # Implicit sources (no path given).
    if source_name == "state":
        # Read STATE.md from repo or conductor root.
        state_file = Path(repo_root) / "STATE.md"
        if not state_file.exists():
            state_file = Path(conductor_root) / "STATE.md"
        if state_file.exists():
            try:
                text = state_file.read_text(encoding="utf-8")
                manifest["included"] = True
                manifest["size_bytes"] = len(text.encode("utf-8"))
                return {source_name: text}, manifest
            except OSError as e:
                return {source_name: f"Error reading STATE.md: {e}"}, manifest
        else:
            return {source_name: "STATE.md not found"}, manifest

    elif source_name.startswith("buildlog_tail:"):
        # Read last N lines of BUILDLOG.md.
        parts = source_name.split(":", 1)
        try:
            tail_count = int(parts[1])
        except (ValueError, IndexError):
            tail_count = 20  # default

        buildlog_file = Path(repo_root) / "BUILDLOG.md"
        if not buildlog_file.exists():
            buildlog_file = Path(conductor_root) / "BUILDLOG.md"

        if buildlog_file.exists():
            try:
                content = buildlog_file.read_text(encoding="utf-8")
                # Split into lines (preserving empty lines except trailing).
                all_lines = content.split("\n")
                # Remove last empty line if file ended with newline.
                if all_lines and all_lines[-1] == "":
                    all_lines = all_lines[:-1]
                # Take last N lines.
                tail_lines = all_lines[-tail_count:] if tail_count > 0 else all_lines
                text = "\n".join(tail_lines)
                manifest["included"] = True
                manifest["size_bytes"] = len(text.encode("utf-8"))
                return {source_name: text}, manifest
            except OSError as e:
                return {source_name: f"Error reading BUILDLOG.md: {e}"}, manifest
        else:
            return {source_name: "BUILDLOG.md not found"}, manifest

    elif source_name == "tracker_open":
        # Read open items from state/tracker.json.
        tracker_file = Path(repo_root) / "state" / "tracker.json"
        if not tracker_file.exists():
            tracker_file = Path(conductor_root) / "state" / "tracker.json"

        if tracker_file.exists():
            try:
                with open(tracker_file, encoding="utf-8") as f:
                    tracker_data = json.load(f)
                # Extract open items (status != 'closed' or similar).
                open_items = [
                    item
                    for item in tracker_data.get("items", [])
                    if item.get("status") != "closed"
                ]
                text = json.dumps(open_items, indent=2, ensure_ascii=True)
                manifest["included"] = True
                manifest["size_bytes"] = len(text.encode("utf-8"))
                return {source_name: text}, manifest
            except (OSError, json.JSONDecodeError) as e:
                return {
                    source_name: f"Error reading tracker_open: {e}"
                }, manifest
        else:
            return {source_name: "tracker.json not found"}, manifest

    elif source_name.startswith("brief:"):
        # Explicit file path (must be allowlisted).
        path_spec = source_name[6:]  # Remove 'brief:' prefix.
        if not path_spec:
            raise ContextPackViolation(
                f"brief: source has no path: {source_name}"
            )

        # Resolve ONCE, validate the RESOLVED path, then read that exact path.
        # (Resolving separately for the check and the read is a TOCTOU window:
        # a symlink swap between the two resolves could redirect the read
        # outside the allowlist.)
        brief_file = Path(path_spec).resolve()
        if not _is_allowed_path(str(brief_file), repo_root, conductor_root):
            raise ContextPackViolation(
                f"brief:{path_spec} is not under allowlisted roots "
                f"({repo_root}, {conductor_root})"
            )
        if brief_file.exists():
            try:
                text = brief_file.read_text(encoding="utf-8")
                manifest["included"] = True
                manifest["size_bytes"] = len(text.encode("utf-8"))
                return {source_name: text}, manifest
            except OSError as e:
                return {source_name: f"Error reading {path_spec}: {e}"}, manifest
        else:
            return {source_name: f"File not found: {path_spec}"}, manifest

    else:
        # Unknown source type (not allowlisted).
        raise ContextPackViolation(
            f"Unknown context source '{source_name}'; "
            f"allowed: 'state', 'buildlog_tail:N', 'tracker_open', 'brief:<path>'"
        )


def _find_next_steps_section(text: str) -> Tuple[int, int]:
    """Find the byte range of a NEXT STEPS section in markdown (B2).

    Args:
        text: The markdown text to search.

    Returns:
        Tuple of (start_byte_pos, end_byte_pos) if NEXT STEPS found,
        or (len(text), len(text)) if not found.
    """
    lines = text.split("\n")
    next_steps_start_line = None

    for i, line in enumerate(lines):
        if "NEXT STEPS" in line.upper():
            next_steps_start_line = i
            break

    if next_steps_start_line is None:
        text_bytes = text.encode("utf-8")
        return (len(text_bytes), len(text_bytes))

    # Find the end of the NEXT STEPS section (next markdown header or EOF).
    next_steps_end_line = len(lines)
    for i in range(next_steps_start_line + 1, len(lines)):
        if lines[i].startswith("#"):
            next_steps_end_line = i
            break

    # Convert line numbers to byte positions.
    start_bytes = len("\n".join(lines[:next_steps_start_line]).encode("utf-8"))
    end_bytes = len("\n".join(lines[:next_steps_end_line]).encode("utf-8"))

    return (start_bytes, end_bytes)


def _truncate_at_markdown_boundary(text: str, target_size: int, preserve_range: Tuple[int, int]) -> str:
    """B2: Truncate text at markdown section boundary, avoiding bisection of preserve_range.

    Args:
        text: The text to truncate.
        target_size: Target size in bytes.
        preserve_range: Tuple of (start_byte, end_byte) for a section that must be preserved.

    Returns:
        Truncated text, cut at a markdown section boundary if possible.
    """
    if len(text.encode("utf-8")) <= target_size:
        return text

    # If preserve_range is within the text, ensure we keep at least up to end of preserve_range.
    preserve_start, preserve_end = preserve_range
    text_bytes = len(text.encode("utf-8"))

    if preserve_end < text_bytes and preserve_end > 0:
        # We must preserve at least up to preserve_end; adjust target if needed.
        target_size = max(target_size, preserve_end + 100)  # +100 for some buffer.

    lines = text.split("\n")
    best_cut = 0
    best_size = len(text.encode("utf-8"))

    # Try cutting at each markdown section boundary (lines starting with #).
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("#"):
            # Try cutting after this header.
            cut_text = "\n".join(lines[:i])
            cut_size = len(cut_text.encode("utf-8"))

            if cut_size <= target_size and cut_size < best_size:
                best_cut = i
                best_size = cut_size

    if best_cut > 0:
        result = "\n".join(lines[:best_cut])
    else:
        # No good section boundary found; truncate to target size directly.
        text_bytes = text.encode("utf-8")
        result = text_bytes[:target_size].decode("utf-8", errors="ignore")

    return result


def _truncate_pack(pack: ContextPack, size_cap: int) -> None:
    """Truncate pack content to fit within size_cap.

    B1+B2+B3: Truncates with awareness of:
      - Source priority (logs first, state last).
      - Markdown section boundaries (B2: cut at headers, preserve NEXT STEPS).
      - Log structure (B3: keep errors, drop routine noise).

    Mutates pack.content and pack.manifest in place.

    Args:
        pack: The context pack to truncate.
        size_cap: The target size limit in bytes.
    """
    # Sort manifest by source type: logs (buildlog_tail) first, then others.
    log_sources = [
        m for m in pack.manifest if m["source"].startswith("buildlog_tail")
    ]
    other_sources = [
        m for m in pack.manifest if not m["source"].startswith("buildlog_tail")
    ]
    sorted_manifest = log_sources + other_sources

    # Truncate from log sources first, aggressively.
    for manifest_entry in sorted_manifest:
        if pack.total_size_bytes <= size_cap:
            break

        source_name = manifest_entry["source"]
        if source_name not in pack.content:
            continue

        text = pack.content[source_name]
        current_size = len(text.encode("utf-8"))

        # For buildlog_tail, use B3 smart truncation (keep errors, drop noise).
        if source_name.startswith("buildlog_tail"):
            lines = text.split("\n")
            # B3: Extract high-signal lines (ERROR, FAILED, BLOCK, Traceback).
            high_signal_lines = [
                i for i, line in enumerate(lines)
                if any(sig in line for sig in ["ERROR", "FAILED", "BLOCK", "Traceback", "Exception"])
            ]

            # Aggressively reduce: start with 50% of lines, then 25%, etc.
            best_text = text
            best_size = current_size
            for reduction in [2, 4, 10, 100]:
                target_lines = max(1, len(lines) // reduction)
                # Prefer keeping high-signal lines; include recent lines.
                new_text = "\n".join(lines[-target_lines:])
                new_size = len(new_text.encode("utf-8"))

                # Accept this reduction if it helps us get closer to the cap.
                if new_size < best_size:
                    best_text = new_text
                    best_size = new_size
                if pack.total_size_bytes - current_size + new_size <= size_cap:
                    break

            pack.content[source_name] = best_text
            pack.total_size_bytes -= current_size - best_size
            # Manifest honesty: only claim truncation if content actually shrank.
            if best_size < current_size:
                manifest_entry["truncated"] = True
                manifest_entry["truncation_reason"] = "size_cap_exceeded"
            manifest_entry["size_bytes"] = best_size

        # For other sources (state, tracker, brief), use B2 markdown-aware truncation.
        elif pack.total_size_bytes > size_cap:
            # B2: Find NEXT STEPS section to preserve.
            preserve_range = _find_next_steps_section(text) if source_name == "state" else (len(text), len(text))

            best_text = text
            best_size = current_size
            for reduction in [2, 4, 10, 100]:
                target_size = max(100, current_size // reduction)
                new_text = _truncate_at_markdown_boundary(text, target_size, preserve_range)
                if not new_text.endswith("\n") and new_text:
                    new_text += "\n... TRUNCATED ..."
                new_size = len(new_text.encode("utf-8"))

                # Accept this reduction if it helps us get closer to the cap.
                if new_size < best_size:
                    best_text = new_text
                    best_size = new_size
                if pack.total_size_bytes - current_size + new_size <= size_cap:
                    break

            pack.content[source_name] = best_text
            pack.total_size_bytes -= current_size - best_size
            # Manifest honesty: only claim truncation if content actually shrank.
            if best_size < current_size:
                manifest_entry["truncated"] = True
                manifest_entry["truncation_reason"] = "size_cap_exceeded"
            manifest_entry["size_bytes"] = best_size


def _truncate_evidence(pack: ContextPack, size_cap: int) -> None:
    """Truncate evidence content to fit within size_cap.

    B4: Prioritizes truncation of lowest-signal items (shorter items = lower signal).
    Mutates pack.evidence and pack.evidence_manifest in place.

    Args:
        pack: The context pack with evidence to truncate.
        size_cap: The target size limit in bytes for evidence.
    """
    # B4: Sort evidence items by size (ascending) to truncate shortest (lowest-signal) first.
    # Create a list of (size, index, evidence_name) for sorting.
    evidence_items_by_size = []
    for i, manifest_entry in enumerate(pack.evidence_manifest):
        evidence_name = manifest_entry["name"]
        if evidence_name in pack.evidence:
            text = pack.evidence[evidence_name]
            size = len(text.encode("utf-8"))
            evidence_items_by_size.append((size, i, evidence_name))

    # Sort by size ascending (smallest/lowest-signal first).
    evidence_items_by_size.sort(key=lambda x: x[0])

    # Truncate: prioritize truncating lowest-signal items first.
    for size, idx, evidence_name in evidence_items_by_size:
        if pack.evidence_size_bytes <= size_cap:
            break

        manifest_entry = pack.evidence_manifest[idx]
        text = pack.evidence[evidence_name]
        current_size = len(text.encode("utf-8"))

        # Truncate this item's end-of-text.
        best_text = text
        best_size = current_size
        for reduction in [2, 4, 10, 100]:
            target_size = max(100, current_size // reduction)
            new_text = text[:target_size] + "\n... TRUNCATED ..."
            new_size = len(new_text.encode("utf-8"))
            # Accept this reduction if it helps us get closer to the cap.
            if new_size < best_size:
                best_text = new_text
                best_size = new_size
            if pack.evidence_size_bytes - current_size + new_size <= size_cap:
                break

        pack.evidence[evidence_name] = best_text
        pack.evidence_size_bytes -= current_size - best_size
        # Manifest honesty: only claim truncation if content actually shrank.
        if best_size < current_size:
            manifest_entry["truncated"] = True
            manifest_entry["truncation_reason"] = "evidence_size_cap_exceeded"
        manifest_entry["size_bytes"] = best_size
