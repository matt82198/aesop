#!/usr/bin/env python3
"""Wave scheduler: single-cycle orchestration of backlog intake, manifest build, and wave dispatch.

WS3a pilot: deterministic one-cycle loop with CRITICAL GUARDRAILS:
  1. Intakes up to N file-disjoint todo items from tracker.json (respects ownsFiles)
  2. Validates required fields + path normalization (platform-independent, no symlink TOCTOU)
  3. Builds a run_wave manifest via wave_templates conventions
  4. Invokes driver.wave_loop.run_wave with recovery journal + git ship config
  5. STOPS before merge: emits Report JSON for human/orchestrator review
  6. Double-dispatch prevention: write "in_progress" status to tracker.json (atomic, conflict-detecting)
  7. Bounded by: HALT file check (final gate before dispatch) + cost ceiling check

SINGLE-WRITER ASSUMPTION: This pilot assumes tracker.json is NOT edited concurrently by other
processes. Concurrent-writer safety checks detect conflicts and abort; full lock/StateAPI
integration is filed for next wave. Do NOT run multiple schedulers against the same tracker.

CLI: python driver/wave_scheduler.py --tracker <path> --max-items N --dry-run|--execute

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple

# Add driver/ and tools/ to path
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
TOOLS_DIR = REPO / "tools"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# Import core modules
from wave_loop import run_wave
from agent_driver import AgentDriver
from verification_policy import verification_policy

# Import safety gates (P1-3: fail-closed if unavailable)
try:
    import halt
except ImportError:
    halt = None

try:
    import cost_ceiling
except ImportError:
    cost_ceiling = None

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir


# ========================================================================
# Path Normalization (P1-2: PLATFORM-INDEPENDENT)
# ========================================================================

def _normalize_path(path: str) -> str:
    """Normalize a path for comparison: posixify, strip ./, casefold ALWAYS (P1-2).

    CRITICAL: Casefolding ALWAYS (not just on Windows) ensures platform-independent
    ownership semantics — same tracker selects identically on all OS.

    Args:
        path: file path (potentially with backslashes, ./ prefix)

    Returns:
        normalized path (forward slashes, no leading ./, lowercased)
    """
    # Replace backslashes with forward slashes (posixify)
    normalized = path.replace("\\", "/")

    # Strip leading ./
    if normalized.startswith("./"):
        normalized = normalized[2:]

    # Casefold ALWAYS for platform-independent semantics (P1-2)
    normalized = normalized.lower()

    return normalized


def _is_valid_owned_path(path: str) -> bool:
    """Validate an ownsFiles entry: reject absolute paths and traversal attacks (P5).

    Args:
        path: normalized file path

    Returns:
        True iff path is relative, has no .. traversal, and is safe to dispatch
    """
    # Reject absolute paths (starting with /)
    if path.startswith("/"):
        return False

    # Reject traversal attacks (.. after normalization)
    if ".." in path:
        return False

    return True


# ========================================================================
# Tracker & Manifest Loading
# ========================================================================

def load_tracker_items(tracker_path: str) -> List[Dict[str, Any]]:
    """Load tracker.json items.

    Args:
        tracker_path: absolute path to tracker.json

    Returns:
        list of item dicts, or [] if file missing/invalid
    """
    p = Path(tracker_path)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Handle both {"items": [...]} and [...]
        if isinstance(data, dict):
            return data.get("items", [])
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []


def _read_tracker_with_hash(tracker_path: str) -> Tuple[List[Dict], Optional[str]]:
    """Load tracker.json and compute content hash for conflict detection (P6).

    Returns:
        (items_list, content_hash)
    """
    items = load_tracker_items(tracker_path)
    p = Path(tracker_path)

    if p.exists():
        try:
            with open(p, "rb") as f:
                content_hash = hashlib.sha256(f.read()).hexdigest()
        except IOError:
            content_hash = None
    else:
        content_hash = None

    return items, content_hash


def _validate_item(item: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validate required fields and ownsFiles for an item (P1-6, P5).

    Args:
        item: tracker item dict

    Returns:
        (is_valid: bool, error_reason: str|None)
    """
    # Check ownsFiles first (P1-1: special handling for empty)
    owns = item.get("ownsFiles")
    if not owns or (isinstance(owns, list) and len(owns) == 0):
        return False, "no_file_ownership"

    # Ensure all entries in ownsFiles are non-empty strings and valid paths (P5)
    if isinstance(owns, list):
        for entry in owns:
            if not isinstance(entry, str) or not entry:
                return False, "invalid_ownsFiles_entries"
            # Normalize and validate (reject absolute paths, traversal)
            normalized = _normalize_path(entry)
            if not _is_valid_owned_path(normalized):
                return False, "invalid_path"

    # Check other required fields
    required = ["slug", "prompt", "testCmd"]
    for field in required:
        if field not in item or not item[field]:
            return False, f"missing_or_empty_{field}"

    return True, None


def filter_todo_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter items to only status=todo, sorted by priority then creation date.

    Priority order: P1 > P2 > P3
    Within same priority: oldest first
    """
    todo = [item for item in items if item.get("status") == "todo"]

    # Sort by priority (P1=0, P2=1, P3=2), then by createdAt
    def priority_rank(item):
        prio = item.get("priority", "P3")
        rank = {"P1": 0, "P2": 1, "P3": 2}.get(prio, 2)
        created = item.get("createdAt", "2999-01-01")
        return (rank, created)

    return sorted(todo, key=priority_rank)


def select_disjoint_items(
    items: List[Dict[str, Any]], max_count: int
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Greedily select up to max_count items with no file overlap (P1-1, P1-2).

    Greedy algorithm: sort by (file_count, priority), pick items that don't
    overlap ownsFiles (after normalization) with already-selected items.

    Returns:
        (selected_items, skipped_item_ids) — skipped due to overlap
    """
    selected = []
    used_files: Set[str] = set()
    skipped = []

    # Sort by file count (ascending) to pack smaller items first
    def item_sort_key(item):
        files = item.get("ownsFiles", [])
        prio = item.get("priority", "P3")
        rank = {"P1": 0, "P2": 1, "P3": 2}.get(prio, 2)
        return (len(files), rank)

    items_to_process = sorted(items, key=item_sort_key)

    for item in items_to_process:
        if len(selected) >= max_count:
            break

        owns = [_normalize_path(f) for f in item.get("ownsFiles", [])]
        owns_set = set(owns)

        # Check for overlap
        if owns_set & used_files:
            skipped.append(item.get("id", "unknown"))
            continue

        # No overlap, select it
        selected.append(item)
        used_files.update(owns_set)

    return selected, skipped


def _check_halt_file(state_dir: Optional[Path] = None) -> Tuple[bool, Optional[str]]:
    """Check if .HALT file exists (P1-3, P1-4).

    Returns:
        (is_halted: bool, reason: str|None)

    Raises:
        RuntimeError if halt module is unavailable or check fails
    """
    if halt is None:
        raise RuntimeError("halt module unavailable (import failed)")

    try:
        if halt.is_halted(state_dir):
            info = halt.get_halt_info(state_dir)
            reason = info.get("reason", "Unknown halt") if info else "Unknown halt"
            return True, reason
    except Exception as e:
        # P1-4: gate check error = halt, not pass
        raise RuntimeError(f"halt file check failed: {e}")

    return False, None


def _check_cost_ceiling(state_dir: Optional[Path] = None) -> Tuple[bool, Optional[str]]:
    """Check cost ceiling (P1-3, P1-4, P2a).

    Returns:
        (ceiling_exceeded: bool, reason: str|None)

    Raises:
        RuntimeError if cost_ceiling module is unavailable or check fails
    """
    if cost_ceiling is None:
        raise RuntimeError("cost_ceiling module unavailable (import failed)")

    try:
        # P2a: use trip=False to enforce (fail-closed on exceeded)
        result = cost_ceiling.check(spent=None, period="wave", state_dir=state_dir, trip=False)
        if result.get("exceeded"):
            return True, f"Cost ceiling exceeded: {result.get('spent', 0)}/{result.get('ceiling', 0)} tokens"
    except Exception as e:
        # P1-4: gate check error = halt, not pass
        raise RuntimeError(f"cost ceiling check failed: {e}")

    return False, None


def _verify_gate_availability() -> Tuple[bool, Optional[str]]:
    """Pre-flight gate availability check (P1-3).

    Returns:
        (gates_available: bool, error_reason: str|None)
    """
    if halt is None:
        return False, "halt module unavailable"
    if cost_ceiling is None:
        return False, "cost_ceiling module unavailable"
    return True, None


# ========================================================================
# Manifest Building
# ========================================================================

def build_wave_manifest(
    selected_items: List[Dict[str, Any]], driver: AgentDriver
) -> Tuple[Dict[str, Any], List[str]]:
    """Build a wave manifest from selected tracker items (P1-6).

    Returns:
        (manifest_dict, items_failed_build_ids)

    Items that fail to build are excluded from manifest and reported separately.
    """
    try:
        from wave_bridge import build_manifest_item
    except ImportError:
        from driver.wave_bridge import build_manifest_item

    manifest_items = []
    failed_ids = []

    for item in selected_items:
        try:
            m_item = build_manifest_item(driver, item)
            manifest_items.append(m_item)
        except Exception as e:
            # P1-6: failed builds are recorded, not selected
            failed_ids.append(item.get("id", "unknown"))
            print(f"[wave_scheduler] Failed to build manifest for {item.get('id')}: {e}", file=sys.stderr)
            continue

    wave_id = str(uuid.uuid4())
    return (
        {
            "wave_id": wave_id,
            "items": manifest_items,
            "wave_description": f"WS3a pilot wave {wave_id[:8]}",
        },
        failed_ids,
    )


# ========================================================================
# Tracker Update (P1-5, P6: ATOMIC WITH CONFLICT DETECTION)
# ========================================================================

def _write_tracker_status_atomic(
    tracker_path: str, items_to_update: List[str], new_status: str, wave_id: str,
    expected_hash: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Write item status updates to tracker.json atomically (P1-5, P6, HIGH).

    Uses tempfile.NamedTemporaryFile + os.replace for atomicity and TOCTOU safety.
    Detects concurrent writes via content-hash comparison.

    Args:
        tracker_path: path to tracker.json
        items_to_update: list of item IDs to mark "in_progress"
        new_status: new status (should be "in_progress" for pilot)
        wave_id: wave ID to record in notes
        expected_hash: content hash from intake; if current content differs, abort with conflict

    Returns:
        (success: bool, error_reason: str|None)
    """
    try:
        # Load current tracker
        p = Path(tracker_path)
        if not p.exists():
            return True, None  # No tracker to update (dry-run or first pass)

        # P6: Detect concurrent writes (conflict detection)
        if expected_hash:
            try:
                with open(p, "rb") as f:
                    current_content = f.read()
                    current_hash = hashlib.sha256(current_content).hexdigest()
                if current_hash != expected_hash:
                    return False, "tracker_conflict"
            except IOError:
                return False, "tracker_conflict"

        # Load tracker for mutation
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Normalize to items list
        if isinstance(data, dict):
            items = data.get("items", [])
        elif isinstance(data, list):
            items = data
        else:
            return False, "invalid_tracker_format"

        # Update items
        items_to_update_set = set(items_to_update)
        for item in items:
            if item.get("id") in items_to_update_set:
                item["status"] = new_status
                notes = item.get("notes", "")
                item["notes"] = f"{notes} [wave {wave_id[:8]}]".strip()

        # P1-5, HIGH: Write atomically using tempfile.NamedTemporaryFile (TOCTOU safe)
        # Do NOT use predictable .tmp suffix (symlink vulnerability)
        temp_fd = None
        try:
            temp_fd, temp_path = tempfile.mkstemp(dir=p.parent, prefix=".tracker-", suffix=".tmp")
            temp_path = Path(temp_path)

            # Write to temp file
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                if isinstance(data, dict):
                    data["items"] = items
                    json.dump(data, f, indent=2)
                else:
                    json.dump(items, f, indent=2)
            temp_fd = None

            # Atomic replace
            os.replace(temp_path, p)
            return True, None

        except Exception as e:
            if temp_fd is not None:
                os.close(temp_fd)
            # Clean up temp file if it exists
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return False, f"Failed to update tracker: {e}"

    except Exception as e:
        return False, f"Failed to update tracker: {e}"


# ========================================================================
# Reporting
# ========================================================================

def emit_report(
    phase: str,
    wave_id: str,
    items_selected: List[str],
    items_shipped: Optional[List[Dict[str, Any]]] = None,
    items_failed_build: Optional[List[str]] = None,
    items_skipped: Optional[List[Dict[str, str]]] = None,
    branch: Optional[str] = None,
    sha: Optional[str] = None,
    halt_reason: Optional[str] = None,
    ceiling_reason: Optional[str] = None,
    error: Optional[str] = None,
    tracker_update_error: Optional[str] = None,
    tracker_update_attempted: bool = False,
    tracker_unmapped_slugs: Optional[List[str]] = None,
    success: bool = False,
    merged: bool = False,
) -> Dict[str, Any]:
    """Emit a Report JSON structure (GATE-1 HANDOFF KIT).

    Per-item observability: items_shipped includes full details {slug, backend, tier, verified, testExit}.

    Returns:
        report dict (ready to serialize)
    """
    report = {
        "phase": phase,
        "wave_id": wave_id,
        "items_selected": items_selected,
        "items_shipped": items_shipped or [],
        "merged": merged,  # P2c: explicit merged=false in pilot
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": success,
    }

    if items_failed_build:
        report["items_failed_build"] = items_failed_build
    if items_skipped:
        report["items_skipped"] = items_skipped
    if branch:
        report["branch"] = branch
    if sha:
        report["sha"] = sha
    if halt_reason:
        report["halt_reason"] = halt_reason
    if ceiling_reason:
        report["ceiling_reason"] = ceiling_reason
    if error:
        report["error"] = error
    if tracker_update_error:
        report["tracker_update_error"] = tracker_update_error
    if tracker_update_attempted:
        report["tracker_update_attempted"] = True
    if tracker_unmapped_slugs:
        report["tracker_unmapped_slugs"] = tracker_unmapped_slugs

    return report


# ========================================================================
# Main Orchestrator
# ========================================================================

def run_wave_scheduler(
    tracker_path: str,
    max_items: int = 5,
    dry_run: bool = False,
    driver: Optional[AgentDriver] = None,
    state_dir: Optional[Path] = None,
    orchestrator_backend: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run one complete wave cycle (intake -> manifest -> dispatch -> report).

    Args:
        tracker_path: path to tracker.json
        max_items: max items to select
        dry_run: if True, print manifest without dispatch
        driver: AgentDriver instance (defaults to FakeDriver for testing)
        state_dir: state directory (defaults to ./state)
        orchestrator_backend: optional OrchestratorBackend for the decision
            seat (HS-2). None (default) keeps the live harness as the
            orchestrator -- byte-identical to pre-HS-2, no key required.
            A live backend (see resolve_orchestrator_backend) routes the
            final_catch decision per verified item through run_wave's
            Phase 6; verdict 'block' stops that item from shipping. The
            Report JSON shape is IDENTICAL either way (swap transparency).

    Returns:
        Report dict (phase, wave_id, items_selected, items_shipped, etc.)
    """
    if state_dir is None:
        try:
            state_dir = get_state_dir()
        except Exception:
            state_dir = Path("./state")

    state_dir = Path(state_dir)
    wave_id = str(uuid.uuid4())

    # ====== PHASE 0: GATE AVAILABILITY CHECK (P1-3) ======
    gates_ok, gate_error = _verify_gate_availability()
    if not gates_ok:
        return emit_report(
            phase="gate_unavailable",
            wave_id=wave_id,
            items_selected=[],
            error=gate_error,
            success=False,
        )

    # ====== PHASE 1: HALT CHECK ======
    try:
        is_halted, halt_reason = _check_halt_file(state_dir)
    except RuntimeError as e:
        return emit_report(
            phase="halt",
            wave_id=wave_id,
            items_selected=[],
            error=str(e),
            success=False,
        )

    if is_halted:
        return emit_report(
            phase="halt",
            wave_id=wave_id,
            items_selected=[],
            halt_reason=halt_reason,
            success=False,
        )

    # ====== PHASE 2: INTAKE + VALIDATION (P1-6, P5) ======
    # P6: Read tracker with hash for conflict detection
    all_items, intake_hash = _read_tracker_with_hash(tracker_path)
    todo_items = filter_todo_items(all_items)

    # Separate valid from invalid items
    valid_items = []
    items_skipped = []
    for item in todo_items:
        is_valid, error_reason = _validate_item(item)
        if is_valid:
            valid_items.append(item)
        else:
            items_skipped.append({"id": item.get("id", "unknown"), "reason": error_reason})

    if not valid_items:
        return emit_report(
            phase="intake",
            wave_id=wave_id,
            items_selected=[],
            items_skipped=items_skipped if items_skipped else None,
            success=True,
        )

    # ====== PHASE 3: DISJOINT SELECTION ======
    selected_items, skipped_ids = select_disjoint_items(valid_items, max_items)

    if not selected_items:
        return emit_report(
            phase="intake",
            wave_id=wave_id,
            items_selected=[],
            items_skipped=items_skipped if items_skipped else None,
            success=True,
        )

    selected_ids = [item.get("id", "unknown") for item in selected_items]

    # ====== PHASE 4: MANIFEST BUILD (P1-6) ======
    if driver is None:
        from tests.test_wave_loop import FakeDriver
        driver = FakeDriver()

    try:
        manifest, failed_build_ids = build_wave_manifest(selected_items, driver)
    except Exception as e:
        return emit_report(
            phase="manifest",
            wave_id=wave_id,
            items_selected=selected_ids,
            error=str(e),
            success=False,
        )

    # Remove failed items from selected
    if failed_build_ids:
        selected_ids = [id for id in selected_ids if id not in failed_build_ids]

    # ====== PHASE 5: DRY-RUN CHECK ======
    if dry_run:
        return emit_report(
            phase="manifest",
            wave_id=wave_id,
            items_selected=selected_ids,
            items_failed_build=failed_build_ids if failed_build_ids else None,
            success=True,
        )

    # ====== PHASE 6: COST CEILING CHECK (P2b: before final HALT) ======
    try:
        ceiling_exceeded, ceiling_reason = _check_cost_ceiling(state_dir)
    except RuntimeError as e:
        return emit_report(
            phase="ceiling",
            wave_id=wave_id,
            items_selected=selected_ids,
            error=str(e),
            success=False,
        )

    if ceiling_exceeded:
        return emit_report(
            phase="ceiling",
            wave_id=wave_id,
            items_selected=selected_ids,
            ceiling_reason=ceiling_reason,
            success=False,
        )

    # ====== PHASE 7: FINAL HALT CHECK (P2b, P4: immediately before dispatch) ======
    try:
        is_halted, halt_reason = _check_halt_file(state_dir)
    except RuntimeError as e:
        return emit_report(
            phase="halt",
            wave_id=wave_id,
            items_selected=selected_ids,
            error=str(e),
            success=False,
        )

    if is_halted:
        return emit_report(
            phase="halt",
            wave_id=wave_id,
            items_selected=selected_ids,
            halt_reason=halt_reason,
            success=False,
        )

    # ====== PHASE 8: RUN WAVE ======
    try:
        state_dir.mkdir(parents=True, exist_ok=True)

        wave_result = run_wave(
            driver=driver,
            manifest=manifest,
            state_dir=state_dir,
            git={"expectTopLevel": str(REPO)},
            resume_journal=True,
            orchestrator_backend=orchestrator_backend,
        )

        # P2c: verify no merged=True, record merged=false
        # GATE-1: per-item observability {slug, backend, tier, verified, testExit}.
        # REAL run_wave shape (live-pilot fix): "shipped" is a list of SLUG
        # STRINGS; the per-item records live in "built". Join them.
        backend_name = driver.probe_capabilities().name
        built_by_slug = {
            b.get("slug"): b for b in (wave_result.get("built") or []) if isinstance(b, dict)
        }
        items_shipped = []
        shipped_slugs = []
        for slug in wave_result.get("shipped", []) or []:
            if isinstance(slug, dict):  # tolerate dict-shaped fakes
                slug = slug.get("slug", "unknown")
            shipped_slugs.append(slug)
            b = built_by_slug.get(slug, {})
            items_shipped.append({
                "slug": slug,
                "backend": backend_name,
                # tier None = no build record (unknown), never a fabricated 1.
                "tier": b.get("verificationTier") if b else None,
                # verified False = NOT PROVEN (conservative), see REPORT-CONTRACT.
                "verified": b.get("verified", False),
                "testExit": b.get("testExit"),
                "buildRecord": bool(b),
            })

        # TRACKER UPDATE FIRST (live-pilot lesson: a crash in Report assembly
        # must never leave shipped-but-unmarked items). Attempted as soon as
        # shipped_slugs is known; outcome fields survive into ANY report,
        # including the exception envelope below.
        tracker_update_attempted = False
        tracker_update_error = None
        tracker_unmapped = []
        if shipped_slugs:
            tracker_update_attempted = True
            try:
                slug_to_id = {it.get("slug"): it.get("id") for it in selected_items}
                shipped_item_ids = []
                for s_ in shipped_slugs:
                    id_ = slug_to_id.get(s_)
                    if id_:
                        shipped_item_ids.append(id_)
                    else:
                        # LOUD, never silent: an unmapped shipped slug means the
                        # tracker cannot be marked -> double-dispatch risk.
                        tracker_unmapped.append(s_)
                if shipped_item_ids:
                    success_update, update_error = _write_tracker_status_atomic(
                        tracker_path,
                        shipped_item_ids,
                        "in_progress",
                        wave_id,
                        expected_hash=intake_hash,  # P6: conflict detection
                    )
                    if not success_update:
                        tracker_update_error = update_error
                if tracker_unmapped and tracker_update_error is None:
                    tracker_update_error = "unmapped_shipped_slugs"
            except Exception as te:
                tracker_update_error = f"tracker_update_exception: {te}"

        # Ship sha comes from the per-repo ship results (no top-level sha key).
        repo_results = wave_result.get("shipped_repos") or []
        sha = next((r.get("sha") for r in repo_results if isinstance(r, dict) and r.get("sha")), None)
        branch = None  # run_wave ships on the current branch; scheduler does not switch branches

        # run_wave has NO top-level "success" key: derive honestly. success
        # additionally requires EVERY shipped item verified (contract: a
        # shipped-but-unproven item is not a successful wave).
        wave_ok = bool(wave_result.get("preflight_ok")) and not wave_result.get("aborted")
        all_verified = all(i.get("verified") for i in items_shipped) if items_shipped else True
        return emit_report(
            phase="dispatch",
            wave_id=wave_id,
            items_selected=selected_ids,
            items_shipped=items_shipped,
            items_failed_build=failed_build_ids if failed_build_ids else None,
            branch=branch,
            sha=sha,
            tracker_update_attempted=tracker_update_attempted,
            tracker_update_error=tracker_update_error,
            tracker_unmapped_slugs=tracker_unmapped if tracker_unmapped else None,
            success=wave_ok and all_verified and tracker_update_error is None,
            merged=False,  # P2c: pilot stops before merge
        )

    except Exception as e:
        # The exception envelope must never hide ship-vs-tracker divergence:
        # carry whatever tracker outcome was reached before the crash.
        return emit_report(
            phase="dispatch",
            wave_id=wave_id,
            items_selected=selected_ids,
            error=str(e),
            tracker_update_attempted=locals().get("tracker_update_attempted", False),
            tracker_update_error=locals().get("tracker_update_error"),
            success=False,
            merged=False,
        )


# ========================================================================
# CLI
# ========================================================================

def resolve_worker_driver(
    driver_override: Optional[str] = None,
    config_path: Optional[str] = None,
    execute: bool = False,
) -> Tuple[Optional[AgentDriver], Optional[str]]:
    """Resolve the worker-seat driver (HS-1): config-first, --driver overrides.

    Resolution order:
      1. driver_override 'claude'/'codex' -> that driver, exactly as the
         legacy hardcoded CLI path behaved (including the codex+execute
         OPENAI_API_KEY gate).
      2. Otherwise: load aesop.config.json and activate a configured worker
         ONLY when a seats.worker block is present (build_driver on the
         validated seat). The seats block is the opt-in surface; it can
         select ANY backend, including openai-compatible (previously
         unreachable from this CLI).
      3. No config file, no seats.worker -> ClaudeCodeDriver, byte-identical
         to pre-0.4.0. A bare LEGACY FLAT backend block ({"backend": ...}
         with no seats) stays INERT here: it was dead config before 0.4.0
         (documented but consumed by nothing), and silently activating it
         would change behavior on existing installs. Migrate it to
         seats.worker to opt in. It still fails loud if malformed.

    For hosted seats with execute=True, the seat's api_key_env (default
    OPENAI_API_KEY) must be set; is_local seats need no key. Dry runs never
    require a key (building a driver is offline-safe).

    Returns:
        (driver, None) on success; (None, error_message) on failure.
    """
    key_env_default = "OPENAI" + "_" + "API" + "_" + "KEY"

    if driver_override == "codex":
        # CodexDriver requires OPENAI_API_KEY for execute; dry-run works without it
        try:
            from codex_driver import CodexDriver
        except ImportError:
            return None, "--driver codex requires codex_driver.py"
        if execute and not os.environ.get(key_env_default):
            return None, (
                f"--driver codex --execute requires {key_env_default} "
                f"environment variable"
            )
        return CodexDriver(), None

    if driver_override == "claude":
        try:
            from claude_code_driver import ClaudeCodeDriver
        except ImportError:
            return None, "claude_code_driver.py not found"
        return ClaudeCodeDriver(), None

    if driver_override is not None:
        return None, f"unknown --driver '{driver_override}' (claude|codex)"

    # Default: read the config (seats.worker or legacy backend block).
    try:
        from backend_config import build_driver, load_backend_config
    except ImportError:
        return None, "backend_config.py not found"

    try:
        config = load_backend_config(config_path)
    except (TypeError, ValueError) as exc:
        return None, f"invalid aesop.config.json: {exc}"

    seats = config.get("seats")
    has_worker_seat = isinstance(seats, dict) and isinstance(
        seats.get("worker"), dict
    )
    if not has_worker_seat:
        # No seats.worker opt-in: byte-identical to pre-0.4.0 (Claude
        # worker), even when a legacy flat backend block is present -- that
        # block was dead config before 0.4.0 and stays inert here.
        try:
            from claude_code_driver import ClaudeCodeDriver
        except ImportError:
            return None, "claude_code_driver.py not found"
        return ClaudeCodeDriver(), None

    backend_name = config.get("backend", "claude")
    if execute and backend_name in ("codex", "openai-compatible"):
        if backend_name == "codex":
            key_env = key_env_default
            is_local = False
        else:
            key_env = config.get("api_key_env") or key_env_default
            is_local = bool(config.get("is_local", False))
        if not is_local and not os.environ.get(key_env):
            return None, (
                f"configured backend '{backend_name}' with --execute requires "
                f"{key_env} environment variable"
            )

    try:
        return build_driver(config), None
    except (TypeError, ValueError, RuntimeError) as exc:
        return None, f"failed to build driver from config: {exc}"


def resolve_orchestrator_backend(
    config_path: Optional[str] = None,
    execute: bool = False,
) -> Tuple[Optional[Any], Optional[str]]:
    """Resolve the orchestrator-seat backend (HS-2): config-first, opt-in.

    Resolution:
      - No config file, no seats block, no seats.orchestrator, or backend
        'harness'/'claude' -> (None, None): the live harness stays the
        orchestrator. run_wave's Phase 6 is byte-identical to pre-HS-2 --
        no OpenAI backend constructed, no key required.
      - seats.orchestrator backend 'openai-compatible' -> a live
        OpenAICompatibleOrchestratorBackend built via
        build_orchestrator_backend(load_backend_config()). Construction is
        offline-safe (no key read until decide_call time); with execute=True
        a hosted (non-is_local) seat requires its api_key_env to be set,
        mirroring the worker-seat gate. Dry runs never require a key.
      - Malformed config -> (None, error_message): fail loud, never a
        silent fallback to the harness.

    Returns:
        (backend_or_None, error_message_or_None).
    """
    key_env_default = "OPENAI" + "_" + "API" + "_" + "KEY"

    try:
        from backend_config import build_orchestrator_backend, load_backend_config
    except ImportError:
        return None, "backend_config.py not found"

    try:
        config = load_backend_config(config_path)
    except (TypeError, ValueError) as exc:
        return None, f"invalid aesop.config.json: {exc}"

    seats = config.get("seats")
    orch_seat = seats.get("orchestrator") if isinstance(seats, dict) else None
    if not isinstance(orch_seat, dict) or not orch_seat:
        return None, None
    if orch_seat.get("backend", "harness") in ("harness", "claude"):
        return None, None

    try:
        backend = build_orchestrator_backend(config)
    except (TypeError, ValueError, RuntimeError) as exc:
        return None, f"failed to build orchestrator backend from config: {exc}"

    # Defensive: the builder returns the null harness backend for any
    # residual default path -> that is the no-op seat, pass None through.
    try:
        from orchestrator_backend import HarnessOrchestratorBackend
        if isinstance(backend, HarnessOrchestratorBackend):
            return None, None
    except ImportError:
        pass

    if execute and not bool(orch_seat.get("is_local", False)):
        key_env = orch_seat.get("api_key_env") or key_env_default
        if not os.environ.get(key_env):
            return None, (
                f"configured seats.orchestrator with --execute requires "
                f"{key_env} environment variable"
            )

    return backend, None


def main():
    """CLI entry point (HS-1: config-driven worker seat; --driver overrides)."""
    parser = argparse.ArgumentParser(
        description="Wave scheduler: intake -> manifest -> dispatch -> report"
    )
    parser.add_argument(
        "--tracker",
        required=True,
        help="Path to tracker.json",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=5,
        help="Maximum items to select (default: 5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print manifest without dispatch",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the wave (default: dry-run)",
    )
    parser.add_argument(
        "--state-dir",
        help="State directory (default: ./state)",
    )
    parser.add_argument(
        "--driver",
        choices=["claude", "codex"],
        default=None,
        help=(
            "OVERRIDE the configured worker seat (claude|codex). "
            "Default: read aesop.config.json seats.worker (no seats block "
            "-> claude; a legacy flat backend block stays inert -- migrate "
            "it to seats.worker); the config path also supports "
            "openai-compatible backends"
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to aesop.config.json (default: ./aesop.config.json)",
    )

    args = parser.parse_args()

    dry_run = not args.execute

    # HS-1: worker seat from config; --driver claude|codex remains an override.
    driver, driver_error = resolve_worker_driver(
        driver_override=args.driver,
        config_path=args.config,
        execute=args.execute,
    )
    if driver_error:
        print(f"ERROR: {driver_error}", file=sys.stderr)
        sys.exit(1)

    # HS-2: orchestrator seat from config (seats.orchestrator). None = the
    # live harness stays the orchestrator (byte-identical default).
    orchestrator_backend, orch_error = resolve_orchestrator_backend(
        config_path=args.config,
        execute=args.execute,
    )
    if orch_error:
        print(f"ERROR: {orch_error}", file=sys.stderr)
        sys.exit(1)
    if orchestrator_backend is not None:
        print(
            "[wave_scheduler] orchestrator seat: "
            f"{type(orchestrator_backend).__name__} "
            f"(model={getattr(orchestrator_backend, 'model', None)})",
            file=sys.stderr,
        )

    # Run scheduler
    report = run_wave_scheduler(
        tracker_path=args.tracker,
        max_items=args.max_items,
        dry_run=dry_run,
        driver=driver,
        state_dir=Path(args.state_dir) if args.state_dir else None,
        orchestrator_backend=orchestrator_backend,
    )

    # Output report as JSON
    print(json.dumps(report, indent=2))

    # Exit with success/failure
    sys.exit(0 if report.get("success") else 1)


if __name__ == "__main__":
    main()
