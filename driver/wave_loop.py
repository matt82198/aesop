#!/usr/bin/env python3
"""Wave loop engine: orchestrates a full multi-item wave through AgentDriver backends.

This module implements Step 3 of the driver integration plan: a Python wave engine
that mirrors the phase sequence from wave-flat-dispatch.template.mjs but runs
offline against AgentDriver backends (Claude Code, Codex, open-model, etc.).

Phases (mirror the template):
  1. Preflight ownership guard: check no two items share an ownsFiles path (per-repo)
  2. Resolve policy ONCE: call verification_policy(caps) and use the returned
     knobs for repair_cap, spot_check_frac, require_adversarial_review
  3. Cost-ceiling gate (fail-closed): before build and before each repair round,
     check spend against ceiling; abort if tripped
  4. Build (PARALLEL): use ThreadPoolExecutor to dispatch items concurrently,
     running each item's test, honoring disjoint ownership
  5. Bounded repair: for failed items, retry with test output appended to prompt,
     up to policy's repair_cap rounds
  6. Adversarial review: if required, dispatch a review per item or mark deferred
  7. Per-repo ship: if git config given, group items by repo and run the git
     sequence (add [repo-relative files], commit, push) separately for each repo,
     with expectTopLevel guard verified PER REPO before any write

PHASE 1 (CROSS-REPO) SCOPE:
  - Manifest items support optional `repo` field (absolute path, must exist, resolved)
  - Preflight validates repo exists and rejects non-absolute paths (fail-closed)
  - Ownership disjointness is per-repo (same file in different repos is NOT a conflict)
  - Ship phase respects repo boundaries: one commit per repo, each repo's cwd
  - Journal keys include repo context (safe-slug of repo basename + item slug)
  - Shipped items reported per-repo in the Report JSON
  - Phase-2 (future): per-repo secret-scan gate, per-repo branch rules, multi-box state

HONESTY GUARANTEE:
  - Verified = True ONLY if the item's test passed (exit code 0 from run_command).
  - Any exception -> item.verified = False, never a false green.
  - Ownership is enforced at the driver level (dispatch_worker rejects out-of-scope).
  - Adversarial review is NOT yet enforced; marked as 'deferred' (TODO in a later increment).

FAIL-SAFE:
  - Cost-ceiling check: if exceeded, ABORT the wave immediately (return early).
  - Disjoint ownership: any overlap -> ABORT with structured error, no dispatch.
  - Repair cap bounded: never infinite retry loop.
  - Ship phase: per-repo expectTopLevel guard aborts THAT REPO's ship without corrupting others.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import concurrent.futures
import hashlib
import json
import os
import posixpath
import re
import shlex
import sys
import threading
import time
import uuid
from math import ceil
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

# Add driver/ to path for imports.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from agent_driver import AgentDriver
from wave_bridge import build_manifest_item, dispatch_item
from verification_policy import verification_policy

# Try to import cost_ceiling and coordination (optional, for safety gates).
try:
    import sys
    TOOLS_DIR = REPO / "tools"
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    import cost_ceiling
except ImportError:
    cost_ceiling = None

try:
    STATE_STORE_DIR = REPO / "state_store"
    if str(STATE_STORE_DIR) not in sys.path:
        sys.path.insert(0, str(STATE_STORE_DIR))
    import coordination
except ImportError:
    coordination = None


# ========================================================================
# Sanitization and Security
# ========================================================================

def _quote_arg(s: str) -> str:
    """Quote an argument for safe shell execution across Windows and POSIX.

    On Windows (cmd.exe), single quotes don't quote; shlex.quote (POSIX-only)
    is unsafe. This function uses subprocess.list2cmdline semantics for Windows
    and shlex.quote for POSIX systems.

    The durable fix is to refactor run_command to accept a list of arguments
    instead of shell=True strings (deferred).

    Args:
        s: the string to quote for shell execution

    Returns:
        str: properly quoted argument safe for shell execution on this OS
    """
    if os.name == 'nt':
        # Windows (cmd.exe): use subprocess.list2cmdline semantics.
        # Double quotes are the safe quoting mechanism; embed quotes are escaped
        # with backslash, and backslashes before quotes are escaped.
        # For safety, we wrap in double quotes and escape embedded quotes/backslashes.
        if not s:
            return '""'
        # Escape backslashes before quotes, then escape quotes
        escaped = s.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    else:
        # POSIX: shlex.quote handles all cases safely
        return shlex.quote(s)


def _validate_repo_path(repo: str) -> str:
    """Validate and normalize a repo path to absolute.

    Rejects relative paths, symlink escapes, and non-existent paths (fail-closed).
    Returns the absolute, normalized path if valid.

    Args:
        repo: the repo path to validate

    Returns:
        str: absolute, normalized repo path

    Raises:
        ValueError: if repo is relative, contains .. escape, or path issues
    """
    repo_path = Path(repo).resolve()

    # Ensure the resolved path is absolute (should always be true after resolve())
    if not repo_path.is_absolute():
        raise ValueError(f"repo path must be absolute: {repo}")

    # Verify the path exists (fail-closed)
    if not repo_path.exists():
        raise ValueError(f"repo path does not exist: {repo}")

    return str(repo_path)


def _validate_file_path(file_path: str, repo_root: str) -> None:
    """Validate a file path for safety before git operations.

    Ensures the path is:
    1. Relative (not absolute)
    2. After joining with repo root and resolving, still inside that repo root (no traversal)

    Args:
        file_path: the path to validate (should be repo-relative)
        repo_root: the absolute repo root path

    Raises:
        ValueError: if path is absolute or escapes the repo root
    """
    # Check if path is absolute (reject).
    if Path(file_path).is_absolute():
        raise ValueError(f"file path must be relative, got absolute: {file_path}")

    # Join with repo root and resolve to get the absolute path.
    repo_root_path = Path(repo_root).resolve()
    full_path = (repo_root_path / file_path).resolve()

    # Verify the resolved path is still inside the repo root (reject traversal).
    try:
        # This will raise ValueError if full_path is not relative to repo_root_path
        full_path.relative_to(repo_root_path)
    except ValueError:
        raise ValueError(f"file path escapes repo root: {file_path} (resolved to {full_path}, outside {repo_root_path})")


def _safe_slug(slug: str) -> str:
    """Sanitize a slug to prevent path traversal attacks and enforce filesystem limits.

    Whitelists [A-Za-z0-9_-]+ and rejects or normalizes everything else.
    This prevents '../../../etc/x' style escape attempts when slug is used
    in path joins.

    LENGTH BOUND:
        The returned slug is guaranteed to produce a journal filename (slug + '.json')
        that fits within the 255-byte filesystem limit (stricter than MAX_PATH on
        Windows). The normalized slug is truncated to ~200 characters, leaving room
        for a '-' separator + 8-char hash suffix + '.json' extension.

        When truncation occurs (slug > 200 chars after normalization), a stable
        hash suffix is always appended to preserve uniqueness.

    COLLISION PREVENTION:
        If normalization changed the string (removed characters), appends a stable
        suffix derived from a hash of the raw slug to prevent collisions when two
        different raw slugs normalize to the same value.

    Args:
        slug: the slug to sanitize

    Returns:
        str: sanitized slug with only alphanumeric, underscore, hyphen,
             optionally truncated and with a hash suffix if truncation or
             normalization occurred

    Raises:
        ValueError: if slug is empty or contains only invalid characters
    """
    MAX_NORMALIZED_LEN = 200  # Leaves room for '-' + 8-char hash + '.json' (< 255)

    if not slug:
        raise ValueError("slug cannot be empty")

    # Keep only alphanumeric, underscore, and hyphen
    sanitized = re.sub(r'[^A-Za-z0-9_-]', '', slug)

    if not sanitized:
        raise ValueError(f"slug contains no valid characters: {slug}")

    # Track if we need to append a hash suffix
    needs_suffix = sanitized != slug  # Normalization changed the string

    # Truncate to MAX_NORMALIZED_LEN if necessary; mark for hash suffix
    if len(sanitized) > MAX_NORMALIZED_LEN:
        sanitized = sanitized[:MAX_NORMALIZED_LEN]
        needs_suffix = True  # Always append hash when truncated for uniqueness

    # If normalization or truncation changed the string, append a stable suffix
    if needs_suffix:
        raw_hash = hashlib.sha1(slug.encode()).hexdigest()[:8]
        sanitized = f"{sanitized}-{raw_hash}"

    return sanitized


def _journal_key_for_item(item: Dict[str, Any]) -> str:
    """Generate a collision-free journal key for an item, including repo context.

    For items with a `repo` field, the key is:
      safe-slug(repo-basename) + '--' + safe-slug(item-slug)
    For items without a repo field, the key is:
      safe-slug(item-slug)

    This ensures same-slug items across repos don't collide.

    Args:
        item: dict with slug and optional repo field

    Returns:
        str: sanitized journal key
    """
    slug = item.get("slug", "unknown")
    repo = item.get("repo")

    if repo:
        try:
            # Get the basename of the repo (last component of the path)
            repo_basename = Path(repo).resolve().name
            repo_key = _safe_slug(repo_basename)
        except Exception:
            # Fallback: use the full repo path hashed
            repo_key = _safe_slug(Path(repo).name or "repo")
        item_key = _safe_slug(slug)
        return f"{repo_key}--{item_key}"
    else:
        return _safe_slug(slug)


# ========================================================================
# Wave Recovery: Journal and Resume Support
# ========================================================================

def _write_journal_entry(state_dir: str, slug: str, phase: str, data: Dict[str, Any], repo: str = None) -> None:
    """Write a journal entry for an item's progress.

    Args:
        state_dir: directory path for state files
        slug: item slug (identifier)
        phase: phase name (e.g., "verified", "failed", "dispatched")
        data: dict with outcome data (verified, testExit, repairs, etc.)
        repo: optional repo path for repo-aware journal keying

    Journal is stored as: state_dir/journal/<journal-key>.json with timestamp.
    Journal key includes repo context if provided, to prevent collisions.
    """
    state_path = Path(state_dir)
    journal_dir = state_path / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)

    # Generate repo-aware journal key.
    try:
        item_stub = {"slug": slug}
        if repo:
            item_stub["repo"] = repo
        journal_key = _journal_key_for_item(item_stub)
    except ValueError:
        # Fail-closed: if key generation fails, skip journaling.
        return

    journal_file = journal_dir / f"{journal_key}.json"
    entry = {
        "slug": slug,
        "repo": repo,
        "phase": phase,
        "timestamp": time.time(),
        **data,
    }

    try:
        journal_file.write_text(json.dumps(entry, default=str) + "\n")
    except Exception:
        # Fail-closed: if journal write fails, continue without journaling.
        pass


def _load_journal_state(state_dir: str) -> Dict[str, Dict[str, Any]]:
    """Load journal state from state_dir.

    Reads all JSON files from state_dir/journal/ and returns a dict
    mapping journal_key (repo--slug or just slug) -> journal_entry.

    The entry is looked up by (repo, slug) tuple for resume matching.

    Returns:
        dict mapping journal_key -> {phase, verified, testExit, repo, ...}
        Returns empty dict if journal dir doesn't exist.
    """
    state_path = Path(state_dir)
    journal_dir = state_path / "journal"

    if not journal_dir.exists():
        return {}

    journal_state = {}
    try:
        # Only read files that match the safe slug pattern to avoid traversal attacks.
        for journal_file in journal_dir.glob("[A-Za-z0-9_-]*.json"):
            try:
                entry = json.loads(journal_file.read_text())
                slug = entry.get("slug")
                repo = entry.get("repo")
                if slug:
                    # Use (repo, slug) as a composite key for lookup.
                    # If repo is None, key is just slug.
                    key = (repo, slug)
                    journal_state[key] = entry
            except Exception:
                # Skip malformed entries.
                pass
    except Exception:
        # Fail-closed: if reading fails, return empty state.
        pass

    return journal_state


def _should_skip_from_journal(journal_entry: Dict[str, Any]) -> bool:
    """Determine if an item should be skipped based on journal entry.

    Skip only if verified=True (even then, trust-but-verify will re-run the test).
    Re-run if verified=False or not present.

    Args:
        journal_entry: dict with verified, testExit, etc.

    Returns:
        bool: True if item should be skipped from build (only trust-verify),
              False if item should be re-built.
    """
    return journal_entry.get("verified", False) is True


def _release_stale_leases(state_dir: str, journal_state: Dict[str, Dict[str, Any]]) -> None:
    """Release stale leases from dead instances.

    Scans journal for old instance_ids and releases their coordination leases
    so resume can re-claim resources. Fail-closed: any release error is ignored.

    Args:
        state_dir: directory path for state files
        journal_state: dict of journal entries by slug
    """
    if coordination is None:
        return

    try:
        STATE_STORE_DIR = REPO / "state_store"
        if str(STATE_STORE_DIR) not in sys.path:
            sys.path.insert(0, str(STATE_STORE_DIR))
        from state_store import store

        db_path = Path(state_dir) / "state.db"
        if not db_path.exists():
            return

        event_store = store.EventStore(str(db_path))

        for slug, entry in journal_state.items():
            old_instance_id = entry.get("instance_id")
            if old_instance_id:
                try:
                    coordination.release(event_store, resource=slug, instance_id=old_instance_id)
                except Exception:
                    # Ignore release errors; fail-closed.
                    pass
    except Exception:
        # Fail-closed: if coordination is unavailable, continue without release.
        pass


def _is_live_orchestrator_backend(backend: Any) -> bool:
    """True when a passed orchestrator backend is a REAL swapped seat.

    None and the null HarnessOrchestratorBackend both mean "the live harness
    is the orchestrator" (the no-op default): the wave engine must behave
    byte-identically to pre-HS-2 in that case.
    """
    if backend is None:
        return False
    try:
        from orchestrator_backend import HarnessOrchestratorBackend
    except ImportError:
        # Cannot classify; an explicitly passed backend is treated as live
        # (fail loud downstream rather than silently ignoring the config).
        return True
    return not isinstance(backend, HarnessOrchestratorBackend)


def _seat_tokens_spent(backend: Any) -> int:
    """Best-effort token spend of an orchestrator seat backend (0 if the
    backend does not meter). Never raises; never fabricates."""
    getter = getattr(backend, "get_tokens_spent", None)
    if not callable(getter):
        return 0
    try:
        return max(0, int(getter()))
    except Exception:
        return 0


def _quarantine_blocked_files(
    driver: Any, item: Dict[str, Any], item_result: Dict[str, Any]
) -> None:
    """Restore a BLOCKED item's written files to their pre-build state.

    Without this, Phase 4's writes stay in the working tree after a block and
    a later `git add -A` (outside this loop) would ship the refused code.

    Semantics (conservative, only the blocked item's own filesWritten):
      - tracked file  -> `git checkout -- <file>` (restore index version)
      - untracked file -> did not exist pre-build; deleted (ONLY on a clean
        exit-1 "untracked" determination; any other ls-files failure is
        AMBIGUOUS -> error record, never delete: fail-safe, not fail-delete)
      - not inside a git worktree -> SKIP with an honest record (we cannot
        know pre-build state without git; never guess-delete)

    PATHSPEC GUARD (re-attack-proven destructive defect): quarantine acts on
    actual FILE paths ONLY. Empty strings, "."/"..", and directory entries
    are REJECTED with an error record -- `git checkout -- subdir` (or ".")
    would revert OTHER items' uncommitted verified work under that tree, not
    just the blocked item's files. On Windows, backslash separators are
    normalized to "/" first so a tracked file is never misclassified
    untracked (and deleted) because git could not match the pathspec.

    Records the outcome in item_result["quarantine"]:
      {"attempted", "restored", "deleted", "errors", "skipped_reason"}.
    Windows + POSIX safe: git args go through _quote_arg; deletion is
    pathlib. Errors are per-file and never raise out of the gate.
    """
    files = list(item_result.get("filesWritten") or [])
    outcome = {
        "attempted": bool(files),
        "restored": [],
        "deleted": [],
        "errors": [],
        "skipped_reason": None,
    }
    item_result["quarantine"] = outcome
    if not files:
        return
    if driver is None:
        outcome["skipped_reason"] = "no_driver"
        return

    root = item.get("repo") or item.get("workDir") or "."
    try:
        probe = driver.run_command(
            "git rev-parse --is-inside-work-tree", cwd=root
        )
        inside = probe.exit_code == 0 and "true" in (probe.stdout or "").lower()
    except Exception:
        inside = False
    if not inside:
        outcome["skipped_reason"] = "not_a_git_worktree"
        return

    for f in files:
        raw = f if isinstance(f, str) else str(f)
        # Windows: normalize separators so the git pathspec matches the
        # index entry (backslash pathspecs silently match nothing -> a
        # tracked file would be misclassified untracked and DELETED).
        # POSIX: backslash is a legal filename character; leave it alone.
        norm = raw.replace("\\", "/") if os.name == "nt" else raw
        # PATHSPEC GUARD: reject empty/dot specs outright -- "." or ""
        # as a checkout pathspec reverts the WHOLE repo, destroying other
        # items' uncommitted verified work. Record, never act.
        stripped = norm.strip().rstrip("/")
        if stripped in ("", ".", ".."):
            outcome["errors"].append(
                {"file": raw, "error": "rejected pathspec: empty or dot "
                 "entry (would revert beyond the blocked item's own files)"}
            )
            continue
        # Only touch paths that stay inside the item's root (never escape).
        try:
            _validate_file_path(norm, root)
        except ValueError as exc:
            outcome["errors"].append({"file": raw, "error": f"path validation: {exc}"})
            continue
        try:
            target = Path(root) / norm
            # PATHSPEC GUARD: directories are never quarantined -- a
            # directory checkout reverts EVERY file under it, including
            # other items' uncommitted verified work. Files only.
            if target.is_dir():
                outcome["errors"].append(
                    {"file": raw, "error": "rejected pathspec: resolves to "
                     "a directory (quarantine operates on file paths only; "
                     "a directory spec would revert other items' files "
                     "under it)"}
                )
                continue
            # :(literal) pathspec magic: glob characters (*, ?, [) in an
            # entry must never expand -- "*" would match (and checkout
            # would revert) EVERY tracked file, not the blocked item's.
            spec = _quote_arg(":(literal)" + norm)
            tracked = driver.run_command(
                "git ls-files --error-unmatch -- " + spec, cwd=root
            )
            if tracked.exit_code == 0:
                restore = driver.run_command(
                    "git checkout -- " + spec, cwd=root
                )
                if restore.exit_code == 0:
                    outcome["restored"].append(f)
                else:
                    outcome["errors"].append(
                        {"file": raw, "error": "git checkout failed: "
                         + (restore.stderr or "")[:200]}
                    )
            elif tracked.exit_code == 1:
                # Clean "untracked" determination (--error-unmatch exits 1
                # on no-match): the file did not exist pre-build; remove it.
                if target.exists() or target.is_symlink():
                    target.unlink()
                outcome["deleted"].append(f)
            else:
                # AMBIGUOUS classification (exit 128, index lock, ...):
                # fail-SAFE, never fail-delete. Tracked content is index-
                # recoverable; an uncertain file must not be destroyed.
                outcome["errors"].append(
                    {"file": raw, "error": "untracked classification "
                     "ambiguous (git ls-files exit "
                     f"{tracked.exit_code}): refusing to delete"}
                )
        except Exception as exc:
            outcome["errors"].append({"file": raw, "error": str(exc)})


def _orchestrator_final_catch(
    backend: Any,
    items: List[Dict[str, Any]],
    result: Dict[str, Any],
    state_dir: Optional[str] = None,
    driver: Any = None,
) -> None:
    """HS-2: route the pre-ship final-catch decision through a configured
    orchestrator seat (Phase 6, replacing 'deferred' ONLY when a seat is
    configured).

    Semantics (conservative, incumbent-safe):
      - Only test-VERIFIED items are reviewed (failed items already do not
        ship; they consume no seat decisions).
      - verdict 'merge'  -> approved; item ships as today.
      - verdict 'block'  -> verified flipped False; item does NOT ship;
        journal updated so a resume cannot skip-and-ship it.
      - 'escalate' / 'undetermined' / DECISION_FAILED -> degrade to today's
        behavior (ship to branch; merge stays manual downstream) with an
        honest per-item record. A seat outage NEVER fabricates a verdict
        and NEVER blocks a test-proven item (crash-only degradation).

    Mutates result in place: per-item 'final_catch' + 'adversarial_review'
    (+ 'quarantine' on block), plus a wave-level 'orchestrator_review'
    summary block with verdict counts, blocked detail, seat token spend,
    and a gate_status flag ("active" | "degraded" when every decision
    failed | "no_decisions" when nothing was verified to review).
    """
    from context_pack import ContextPack
    from orchestrator_driver import OrchestratorDriver

    orch = OrchestratorDriver(backend, schema_dir=str(DRIVER_DIR))
    review = {
        "seat": type(backend).__name__,
        "model": getattr(backend, "model", None),
        "decisions": 0,
        "blocked": [],
        "blocked_detail": [],
        "decision_failed": [],
        "verdict_counts": {
            "merge": 0,
            "block": 0,
            "escalate": 0,
            "undetermined": 0,
            "decision_failed": 0,
        },
    }
    slug_to_item = {
        item.get("slug", f"item-{i}"): item for i, item in enumerate(items)
    }
    result["adversarial_review"] = "orchestrator_final_catch"

    for item_result in result["built"]:
        if not item_result.get("verified", False):
            item_result["adversarial_review"] = "skipped_not_verified"
            continue

        slug = item_result.get("slug", "unknown")
        item = slug_to_item.get(slug, {})
        evidence = {
            "item": json.dumps(
                {
                    "slug": slug,
                    "prompt_excerpt": str(item.get("prompt", ""))[:1000],
                    "ownsFiles": list(item.get("ownsFiles", [])),
                    "filesWritten": item_result.get("filesWritten", []),
                    "repairs": item_result.get("repairs", 0),
                },
                sort_keys=True,
            ),
            # NOTE (F6): this final_catch evidence is currently LOW-SIGNAL --
            # test_passed is hardcoded True (only verified items reach this
            # point) and no secret-scan/CI/branch-protection results are fed
            # in yet. Future work should enrich it with the real gate outputs
            # so the seat has something substantive to judge.
            "verification_results": json.dumps(
                {
                    "test_passed": True,
                    "test_exit_code": item_result.get("testExit"),
                    "spot_check_failed": bool(
                        item_result.get("spot_check_failed", False)
                    ),
                },
                sort_keys=True,
            ),
        }
        pack = ContextPack(
            decision_type="final_catch",
            sources_requested=(),
            evidence=evidence,
        )
        try:
            decision = orch.decide("final_catch", pack)
        except Exception as exc:
            # decide() never raises by contract; belt and braces anyway.
            decision = {
                "verdict": "DECISION_FAILED",
                "evidence": [f"decide() raised: {exc}"],
            }
        review["decisions"] += 1
        verdict = str(decision.get("verdict", "DECISION_FAILED"))
        item_result["final_catch"] = verdict

        if verdict == "block":
            review["verdict_counts"]["block"] += 1
            item_result["verified"] = False
            item_result["adversarial_review"] = "blocked_by_orchestrator"
            item_result["error"] = "orchestrator final_catch verdict: block"
            review["blocked"].append(slug)
            # Persist WHY (hold_reason, else first evidence citation) so the
            # Report's blocked lane is actionable, not just a slug.
            reason = decision.get("hold_reason")
            if not reason:
                evidence_list = decision.get("evidence")
                if isinstance(evidence_list, list) and evidence_list:
                    reason = str(evidence_list[0])
            review["blocked_detail"].append(
                {"slug": slug, "reason": reason or "final_catch verdict: block"}
            )
            if state_dir:
                _write_journal_entry(
                    state_dir,
                    slug,
                    "final_catch_blocked",
                    {
                        "verified": False,
                        "testExit": item_result.get("testExit"),
                        "final_catch": "block",
                    },
                    repo=item.get("repo"),
                )
            # QUARANTINE: refused code must not linger in the working tree.
            _quarantine_blocked_files(driver, item, item_result)
        elif verdict == "merge":
            review["verdict_counts"]["merge"] += 1
            item_result["adversarial_review"] = "approved_by_orchestrator"
        elif verdict in ("escalate", "undetermined"):
            review["verdict_counts"][verdict] += 1
            item_result["adversarial_review"] = verdict
        else:  # DECISION_FAILED (or anything unrecognized -> fail-safe path)
            review["verdict_counts"]["decision_failed"] += 1
            item_result["adversarial_review"] = "decision_failed_deferred"
            review["decision_failed"].append(slug)

    # Gate visibility: a 100%-failing seat must NOT look like an approving
    # one. decisions>0 with every one DECISION_FAILED = the gate made zero
    # successful decisions -> "degraded" (crash-only ship semantics are
    # unchanged; this only makes the outage VISIBLE).
    if review["decisions"] == 0:
        review["gate_status"] = "no_decisions"
    elif review["verdict_counts"]["decision_failed"] == review["decisions"]:
        review["gate_status"] = "degraded"
    else:
        review["gate_status"] = "active"
    review["seat_tokens_spent"] = _seat_tokens_spent(backend)

    result["orchestrator_review"] = review


def run_wave(
    driver: AgentDriver,
    manifest: Dict[str, Any],
    *,
    state_dir: Optional[str] = None,
    git: Optional[Dict[str, str]] = None,
    resume_journal: bool = False,
    orchestrator_backend: Any = None,
) -> Dict[str, Any]:
    """Run a full multi-item wave through an AgentDriver backend.

    Implements the complete wave algorithm: preflight ownership guard, parallel
    build, bounded repair, optional adversarial review, and batched git ship.

    Supports resumable waves: if resume_journal=True and state_dir exists,
    skips items marked as verified in the journal and does trust-but-verify
    re-running of their tests. Releases stale leases from dead instances.

    Args:
        driver: AgentDriver instance providing dispatch_worker, run_command, etc.
        manifest: dict with:
          - items: list of item dicts with {slug, ownsFiles, prompt, testCmd, workDir, ...}
          - (optional) other manifest fields
        state_dir: optional path to state directory for coordination claims and
                   cost_ceiling ledger. If None, these features are skipped.
        git: optional dict with {expectTopLevel: str} for git operations. If None,
             ship phase is skipped.
        resume_journal: if True and state_dir exists, load journal and skip items
                       marked as verified (but re-run their tests for trust-but-verify).
        orchestrator_backend: optional OrchestratorBackend for the DECISION seat
                       (HS-2). None or the null HarnessOrchestratorBackend means
                       the live harness stays the orchestrator: Phase 6 remains
                       'deferred', byte-identical to pre-HS-2 (no key required,
                       no backend called). A live backend (e.g. from
                       build_orchestrator_backend(load_backend_config()) with a
                       seats.orchestrator block) routes a final_catch decision
                       per verified item through OrchestratorDriver.decide();
                       verdict 'block' stops that item from shipping.

    Returns:
        dict with structure:
          {
            "preflight_ok": bool,
            "aborted": bool,
            "abort_reason": str or None,
            "built": [
              {
                "slug": str,
                "dispatched": bool,
                "verified": bool,
                "testExit": int or None,
                "repairs": int,
                "error": str or None,
                "filesWritten": [str],
                "skipped_from_journal": bool (only if resume_journal=True),
              },
              ...
            ],
            "shipped": [str] or None (list of slugs, or None if git not configured),
            "ceiling": dict or None (from cost_ceiling.check, or None if no ceiling),
            "policy": dict (the resolved verification_policy),
            "resume_stats": dict (only if resume_journal=True) with:
              {
                "skipped_from_journal": int,
                "rebuilt": int,
              }
          }

    Fail-safe invariants:
      - Verified is True ONLY from run_command exit code 0.
      - Any exception in an item's dispatch -> verified=False for that item.
      - Cost ceiling: if check() says exceeded, abort immediately with no more dispatch.
      - Disjoint ownership: any overlap -> abort with structured error, no dispatch.

    Explicit-repo preflight (deprecate expectTopLevel default):
      When git shipping is configured AND a manifest contains ANY item with an
      explicit 'repo' field, items WITHOUT 'repo' are REJECTED at preflight
      with a clear error. This prevents silent mismatches caused by mixed
      manifests (some items use expectTopLevel default, others are explicit).

      Pure-legacy manifests (NO item has 'repo') keep the expectTopLevel
      default unchanged (backward compat). Fully-explicit manifests (ALL items
      have 'repo') work unchanged. Mixed manifests are fail-closed.

      Error: abort_reason="mixed_repo_manifest", with items_with_repo and
      items_without_repo counts in the result.
    """
    result = {
        "preflight_ok": False,
        "aborted": False,
        "abort_reason": None,
        "built": [],
        "shipped": None,
        "ceiling": None,
        "policy": None,
        "resume_stats": None,
    }

    # Extract items from manifest.
    items = manifest.get("items", [])

    # ========================================================================
    # PHASE 0 (optional): Resume - Load journal state and release stale leases
    # ========================================================================
    journal_state = {}
    resume_stats = {"skipped_from_journal": 0, "rebuilt": 0}
    resume_stats_lock = threading.Lock()  # Protect resume_stats from concurrent access
    if resume_journal and state_dir:
        journal_state = _load_journal_state(state_dir)
        _release_stale_leases(state_dir, journal_state)
        if journal_state:
            result["resume_stats"] = resume_stats

    # ========================================================================
    # PHASE 1: Preflight ownership guard (with per-repo validation)
    # ========================================================================

    # IMPORTANT: When git is configured (ship phase enabled), all items MUST have an
    # absolute resolved `repo` field after preflight. This ensures manifests behave
    # identically regardless of process cwd.
    #
    # Default for items without explicit `repo`:
    # - If git config has expectTopLevel, use that (legacy behavior anchor)
    # - If git config NOT present: no repo validation needed (non-ship-phase wave)
    # - If git config present but NO expectTopLevel: error (can't default repo for shipping)

    # Determine the default repo from git config (legacy anchor for byte-identical behavior).
    # Only used when git config is present (i.e., shipping is enabled).
    default_repo = None
    if git is not None:
        default_repo = git.get("expectTopLevel")

    # EXPLICIT-REPO PREFLIGHT: Detect mixed manifests (some items with repo, some without).
    # Contract:
    #  - Pure-legacy manifest (NO item has repo) → use expectTopLevel default (backward compat)
    #  - Fully-explicit manifest (ALL items have repo) → use explicit repos (unchanged)
    #  - Mixed manifest (SOME items have repo) → REJECT with clear error (fail-closed)
    #
    # This ensures:
    #  1. Legacy manifests continue to work (no breaking change)
    #  2. Explicit manifests work correctly (no confusion)
    #  3. Mixed manifests are caught early, preventing subtle bugs
    if git is not None:
        # Count items with and without explicit repo field
        items_with_repo = sum(1 for item in items if item.get("repo"))
        items_without_repo = len(items) - items_with_repo

        if items_with_repo > 0 and items_without_repo > 0:
            # Mixed manifest: some items have repo, some don't
            result["aborted"] = True
            result["abort_reason"] = "mixed_repo_manifest"
            result["error"] = (
                "mixed manifest detected: some items have explicit 'repo' field, others don't. "
                "Manifests must be fully explicit (all items have 'repo') or fully implicit (none have 'repo'). "
                "To fix: either add 'repo' field to all items or remove it from all items (use expectTopLevel instead)."
            )
            result["items_with_repo"] = items_with_repo
            result["items_without_repo"] = items_without_repo
            return result

    # Validate and resolve all repos; populate default for missing items (only if shipping).
    repo_paths = set()

    for item in items:
        repo = item.get("repo")

        if not repo:
            # No explicit repo field.
            if git is not None:
                # Ship phase is configured; must have a default (repo field must be missing on ALL items, per mixed check above).
                if default_repo:
                    repo = default_repo
                else:
                    # Shipping enabled but can't default repo for this item.
                    result["aborted"] = True
                    result["abort_reason"] = "repo_field_missing_no_default"
                    result["error"] = "item requires 'repo' field when git shipping is configured (set expectTopLevel or add repo field)"
                    result["item_slug"] = item.get("slug", "unknown")
                    return result
            else:
                # No shipping phase; skip repo validation for this item.
                # This allows backward-compatible non-shipping waves without repo fields.
                continue

        # Validate and resolve the repo path to absolute.
        try:
            repo_resolved = _validate_repo_path(repo)
            repo_paths.add(repo_resolved)
            # Update item with resolved path for later use (ensures byte-identical cwd).
            item["repo"] = repo_resolved
        except ValueError as e:
            result["aborted"] = True
            result["abort_reason"] = "invalid_repo_path"
            result["invalid_repo"] = repo
            result["error"] = str(e)
            return result
        # Future: also validate is_git_worktree, has_secret_scan_gate

    # Per-repo ownership guard: track ownership within each repo separately.
    # Structure: {repo: {normalized_file: slug}}
    repo_owner_map = {}  # repo -> (normalized file -> slug)
    conflicts = []

    for item in items:
        slug = item.get("slug", "unknown")
        owned_files = item.get("ownsFiles", [])
        repo = item.get("repo", ".")  # Default to cwd if no repo specified

        # Normalize repo path
        repo_normalized = str(Path(repo).resolve()).lower()

        if repo_normalized not in repo_owner_map:
            repo_owner_map[repo_normalized] = {}

        owner_map = repo_owner_map[repo_normalized]

        for f in owned_files:
            # Platform-independent path normalization: handle separators and case uniformly.
            # Replace all backslashes with forward slashes, normalize with posixpath,
            # and convert to lowercase for case-insensitive comparison on all platforms.
            normalized = posixpath.normpath(f.replace("\\", "/")).lower()
            if normalized in owner_map:
                conflicts.append(
                    {
                        "file": f,
                        "normalized": normalized,
                        "repo": repo,
                        "items": [owner_map[normalized], slug],
                    }
                )
            else:
                owner_map[normalized] = slug

    if conflicts:
        result["aborted"] = True
        result["abort_reason"] = "ownership_overlap"
        result["conflicts"] = conflicts
        return result

    result["preflight_ok"] = True

    # ========================================================================
    # PHASE 2: Resolve verification policy ONCE
    # ========================================================================
    caps = driver.probe_capabilities()
    policy = verification_policy(caps)
    result["policy"] = policy

    repair_cap = policy.get("repair_cap", 1)
    spot_check_frac = policy.get("spot_check_frac", 0.0)
    require_adversarial_review = policy.get("require_adversarial_review", False)

    # ========================================================================
    # PHASE 3: Cost-ceiling gate (before build)
    # ========================================================================
    if cost_ceiling is not None and state_dir is not None:
        ceiling_result = cost_ceiling.check(
            spent=driver.get_tokens_spent(),
            trip=True,
            state_dir=state_dir,
        )
        result["ceiling"] = ceiling_result

        if ceiling_result.get("exceeded", False):
            result["aborted"] = True
            result["abort_reason"] = "cost_ceiling_exceeded"
            return result

    # ========================================================================
    # PHASE 4: Build (PARALLEL with ThreadPoolExecutor)
    # ========================================================================
    # Prepare built items list and track for repair.
    built_items = []
    failed_items = []  # (index, item, result) tuples for repair

    def build_item(item_index: int, item: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Build one item and return (index, result_dict)."""
        slug = item.get("slug", f"item-{item_index}")
        workdir = item.get("workDir", ".")
        repo = item.get("repo")

        # ====================================================================
        # RESUME CHECK: If in journal and verified, skip dispatch and trust-verify
        # ====================================================================
        journal_key = (repo, slug)
        journal_entry = journal_state.get(journal_key)
        skipped_from_journal = False
        if journal_entry and _should_skip_from_journal(journal_entry):
            skipped_from_journal = True
            with resume_stats_lock:
                resume_stats["skipped_from_journal"] += 1

            # Trust-but-verify: re-run the test for the journaled item
            test_cmd = item.get("testCmd", "")
            if test_cmd:
                try:
                    test_result = driver.run_command(test_cmd, cwd=workdir)
                    if test_result.exit_code == 0:
                        # Test still passes; mark verified.
                        return (
                            item_index,
                            {
                                "slug": slug,
                                "dispatched": False,
                                "verified": True,
                                "testExit": 0,
                                "repairs": 0,
                                "error": None,
                                "filesWritten": [],
                                "skipped_from_journal": True,
                                "trust_verified": True,
                            },
                        )
                    else:
                        # Test failed on re-run; flip to False and mark for rebuild.
                        with resume_stats_lock:
                            resume_stats["rebuilt"] += 1
                        return (
                            item_index,
                            {
                                "slug": slug,
                                "dispatched": False,
                                "verified": False,
                                "testExit": test_result.exit_code,
                                "repairs": 0,
                                "error": "trust-verify test failed (re-run)",
                                "filesWritten": [],
                                "skipped_from_journal": True,
                                "trust_verified": False,
                            },
                        )
                except Exception as exc:
                    # Test re-run failed; flip to False and mark for rebuild.
                    with resume_stats_lock:
                        resume_stats["rebuilt"] += 1
                    return (
                        item_index,
                        {
                            "slug": slug,
                            "dispatched": False,
                            "verified": False,
                            "testExit": None,
                            "repairs": 0,
                            "error": f"trust-verify exception: {exc}",
                            "filesWritten": [],
                            "skipped_from_journal": True,
                            "trust_verified": False,
                        },
                    )
            else:
                # No test command; just mark as skipped but not verified (safe).
                return (
                    item_index,
                    {
                        "slug": slug,
                        "dispatched": False,
                        "verified": False,
                        "testExit": None,
                        "repairs": 0,
                        "error": "no test command for trust-verify",
                        "filesWritten": [],
                        "skipped_from_journal": True,
                        "trust_verified": False,
                    },
                )

        # ====================================================================
        # NORMAL BUILD: Not in journal or was marked as failed
        # ====================================================================
        with resume_stats_lock:
            resume_stats["rebuilt"] += 1

        # Try to claim the item if state_dir is given (fail-closed on claim failure).
        instance_id = f"wave-{uuid.uuid4()}"
        claim_held = False
        if coordination is not None and state_dir is not None:
            try:
                from state_store import store
                db_path = Path(state_dir) / "state.db"
                event_store = store.EventStore(str(db_path))
                claim_held = coordination.try_claim(
                    event_store,
                    resource=slug,
                    instance_id=instance_id,
                )
                if not claim_held:
                    # Item is claimed by another instance; skip it.
                    return (
                        item_index,
                        {
                            "slug": slug,
                            "dispatched": False,
                            "verified": False,
                            "testExit": None,
                            "repairs": 0,
                            "error": "resource claimed by another instance",
                            "filesWritten": [],
                        },
                    )
            except Exception:
                # On exception, fail-closed: skip the item.
                claim_held = False

        try:
            # Build the manifest item with policy.
            manifest_item = build_manifest_item(driver, item)

            # Dispatch the item.
            dispatch_result = dispatch_item(driver, manifest_item, workdir=workdir)

            item_result = {
                "slug": slug,
                "dispatched": dispatch_result.get("route") == "driver",
                "verified": dispatch_result.get("verified", False),
                "testExit": dispatch_result.get("testExit"),
                "repairs": 0,
                "error": dispatch_result.get("error"),
                "filesWritten": dispatch_result.get("filesWritten", []),
                "workerId": dispatch_result.get("workerId"),
            }

            # Write journal entry for this item's outcome.
            if state_dir:
                _write_journal_entry(state_dir, slug, "dispatched", {
                    "verified": item_result["verified"],
                    "testExit": item_result["testExit"],
                    "instance_id": instance_id,
                }, repo=repo)

            return (item_index, item_result)

        except Exception as exc:
            # Catch-all: any exception -> failed result, never a false green.
            if state_dir:
                _write_journal_entry(state_dir, slug, "failed", {
                    "verified": False,
                    "testExit": None,
                    "instance_id": instance_id,
                    "error": str(exc),
                }, repo=repo)

            return (
                item_index,
                {
                    "slug": slug,
                    "dispatched": False,
                    "verified": False,
                    "testExit": None,
                    "repairs": 0,
                    "error": f"build exception: {exc}",
                    "filesWritten": [],
                },
            )
        finally:
            # Release the claim if held.
            if claim_held and coordination is not None and state_dir is not None:
                try:
                    from state_store import store
                    db_path = Path(state_dir) / "state.db"
                    event_store = store.EventStore(str(db_path))
                    coordination.release(event_store, resource=slug, instance_id=instance_id)
                except Exception:
                    pass

    # Run build in parallel.
    max_workers = min(8, len(items)) if items else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(build_item, i, item)
            for i, item in enumerate(items)
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                item_index, item_result = future.result()
                built_items.append((item_index, items[item_index], item_result))

                # Track failed items for repair.
                if not item_result["verified"]:
                    failed_items.append((item_index, items[item_index], item_result))
            except Exception:
                # Should not happen (build_item catches internally), but just in case.
                pass

    # Sort built_items by index to preserve order.
    built_items.sort(key=lambda x: x[0])
    result["built"] = [item_result for _, _, item_result in built_items]

    # ========================================================================
    # PHASE 5: Bounded repair
    # ========================================================================
    for repair_round in range(repair_cap):
        if not failed_items:
            break

        # Cost-ceiling check before repair round.
        if cost_ceiling is not None and state_dir is not None:
            ceiling_result = cost_ceiling.check(
                spent=driver.get_tokens_spent(),
                trip=True,
                state_dir=state_dir,
            )
            if ceiling_result.get("exceeded", False):
                result["aborted"] = True
                result["abort_reason"] = "cost_ceiling_exceeded_in_repair"
                return result

        # Repair each failed item.
        next_failed = []
        for item_index, item, item_result in failed_items:
            slug = item.get("slug", f"item-{item_index}")
            workdir = item.get("workDir", ".")
            test_cmd = item.get("testCmd", "")

            # Build repair prompt: append test output to original prompt.
            original_prompt = item.get("prompt", "")
            test_output = f"\n\nTest failed with exit code {item_result['testExit']}.\n"
            if item_result.get("error"):
                test_output += f"Error: {item_result['error']}\n"
            repair_prompt = original_prompt + test_output

            # Create a repair item.
            repair_item = dict(item)
            repair_item["prompt"] = repair_prompt

            try:
                # Build the manifest item.
                manifest_item = build_manifest_item(driver, repair_item)

                # Dispatch the repair.
                dispatch_result = dispatch_item(driver, manifest_item, workdir=workdir)

                # Update the item result.
                item_result["verified"] = dispatch_result.get("verified", False)
                item_result["testExit"] = dispatch_result.get("testExit")
                item_result["error"] = dispatch_result.get("error")
                item_result["filesWritten"] = dispatch_result.get("filesWritten", [])
                item_result["repairs"] += 1

                # Write journal entry for repair outcome.
                if state_dir:
                    repo = item.get("repo")
                    _write_journal_entry(state_dir, slug, "repaired", {
                        "verified": item_result["verified"],
                        "testExit": item_result["testExit"],
                        "repairs": item_result["repairs"],
                    }, repo=repo)

                # If still failed, mark for next round.
                if not item_result["verified"]:
                    next_failed.append((item_index, item, item_result))

            except Exception as exc:
                item_result["error"] = f"repair exception: {exc}"
                item_result["repairs"] += 1

                # Write journal entry for repair failure.
                if state_dir:
                    repo = item.get("repo")
                    _write_journal_entry(state_dir, slug, "repair_failed", {
                        "verified": False,
                        "testExit": None,
                        "repairs": item_result["repairs"],
                        "error": str(exc),
                    }, repo=repo)

                next_failed.append((item_index, item, item_result))

        # Update failed_items for next round.
        failed_items = next_failed

    # ========================================================================
    # PHASE 5.5: Spot-check verified items (if spot_check_frac > 0)
    # ========================================================================
    if spot_check_frac > 0:
        # Collect verified items and their original test commands.
        verified_items_to_check = []
        for item_index, (idx, original_item, item_result) in enumerate(
            [(i, items[i], r) for i, r in enumerate(result["built"]) if r.get("verified", False)]
        ):
            verified_items_to_check.append((original_item, item_result))

        # Determine how many to spot-check.
        num_to_check = ceil(len(verified_items_to_check) * spot_check_frac)

        # Deterministic sampling: check first N items by slug order.
        # Sort by slug for determinism, then check the first num_to_check.
        verified_items_to_check.sort(key=lambda x: x[0].get("slug", ""))
        items_to_rerun = verified_items_to_check[:num_to_check]

        # Re-run tests for sampled items.
        for original_item, item_result in items_to_rerun:
            test_cmd = original_item.get("testCmd", "")
            workdir = original_item.get("workDir", ".")

            if test_cmd:
                try:
                    rerun_result = driver.run_command(test_cmd, cwd=workdir)
                    # If re-run does NOT exit 0, flip verified to False.
                    if rerun_result.exit_code != 0:
                        item_result["verified"] = False
                        item_result["spot_check_failed"] = True
                except Exception:
                    # On exception, flip verified to False.
                    item_result["verified"] = False
                    item_result["spot_check_failed"] = True

    # ========================================================================
    # PHASE 6: Adversarial review / orchestrator final catch (HS-2)
    # ========================================================================
    if _is_live_orchestrator_backend(orchestrator_backend):
        # A configured orchestrator seat is LIVE: route a final_catch
        # decision per verified item through the swapped backend.
        _orchestrator_final_catch(
            orchestrator_backend, items, result, state_dir=state_dir,
            driver=driver,
        )
        # HS-2 hardening: the seat's own spend (up to 3 calls/item) counts
        # against the ceiling too. Re-check AFTER decisions, BEFORE ship,
        # including metered seat tokens. Runs ONLY on the live-seat path
        # (no-op default keeps the pre-HS-2 check pattern byte-identical).
        if cost_ceiling is not None and state_dir is not None:
            ceiling_result = cost_ceiling.check(
                spent=driver.get_tokens_spent()
                + _seat_tokens_spent(orchestrator_backend),
                trip=True,
                state_dir=state_dir,
            )
            result["ceiling"] = ceiling_result
            if ceiling_result.get("exceeded", False):
                result["aborted"] = True
                result["abort_reason"] = "cost_ceiling_exceeded_after_decisions"
                return result
    else:
        # No configured seat: the live harness IS the orchestrator; review
        # stays deferred to it. Byte-identical to pre-HS-2 behavior.
        result["adversarial_review"] = "deferred"
        for item_result in result["built"]:
            item_result["adversarial_review"] = "deferred"

    # ========================================================================
    # PHASE 7: Per-repo ship (git operations, if configured)
    # ========================================================================
    if git is not None:
        # Verify expectTopLevel guard: MUST be a non-empty string matching actual toplevel.
        expect_top_level = git.get("expectTopLevel")
        if not expect_top_level or not isinstance(expect_top_level, str):
            # Empty or missing expectTopLevel with git config is an error.
            result["aborted"] = True
            result["abort_reason"] = "git_toplevel_missing_or_empty"
            return result

        # Only ship items that verified green.
        # Build a slug -> original_item mapping for lookup.
        slug_to_item = {item.get("slug"): (i, item) for i, item in enumerate(items)}

        verified_items = []
        for item_result in result["built"]:
            if item_result.get("verified", False):
                slug = item_result.get("slug")
                if slug in slug_to_item:
                    item_index, original_item = slug_to_item[slug]
                    verified_items.append((item_index, original_item, item_result))

        if verified_items:
            # Group verified items by their resolved repo.
            repo_to_items = {}  # {repo_path: [(item_index, original_item, item_result), ...]}
            for item_index, original_item, item_result in verified_items:
                repo = original_item.get("repo", ".")
                # Resolve and validate repo path.
                try:
                    repo_resolved = _validate_repo_path(repo)
                except ValueError:
                    # Fail-closed: this repo is invalid, mark as error but continue.
                    item_result["ship_error"] = f"invalid repo path: {repo}"
                    continue

                if repo_resolved not in repo_to_items:
                    repo_to_items[repo_resolved] = []
                repo_to_items[repo_resolved].append((item_index, original_item, item_result))

            # Ship each repo separately.
            shipped_items = []
            repo_ship_results = []  # {repo, committed, sha, files_count, error}

            for repo_path, repo_items in repo_to_items.items():
                # Verify expectTopLevel guard PER REPO:
                # Each repo's toplevel must equal the global expectTopLevel OR the repo's own root.
                # First, verify the repo is actually a git repo with the right toplevel.
                toplevel_result = driver.run_command(
                    "git rev-parse --show-toplevel",
                    cwd=repo_path
                )
                if toplevel_result.exit_code != 0:
                    # This repo's git is broken; abort THIS repo's ship but continue others.
                    repo_ship_results.append({
                        "repo": repo_path,
                        "committed": False,
                        "error": "git_toplevel_check_failed",
                        "files_count": len(repo_items),
                    })
                    # Mark items from this repo as shipped_error.
                    for _, _, item_result in repo_items:
                        item_result["ship_error"] = "git_toplevel_check_failed"
                    continue

                toplevel = toplevel_result.stdout.strip()
                # Normalize paths for comparison (git may return with / on Windows)
                toplevel_normalized = str(Path(toplevel).resolve())
                repo_path_normalized = str(Path(repo_path).resolve())

                # Per-repo guard: the repo's toplevel must match that repo's own root.
                # This ensures we're not operating on a subdirectory or symlink escaping.
                if toplevel_normalized != repo_path_normalized:
                    # Top-level mismatch; abort THIS repo's ship but continue others.
                    repo_ship_results.append({
                        "repo": repo_path,
                        "committed": False,
                        "error": "git_toplevel_mismatch",
                        "files_count": len(repo_items),
                        "expected_repo_root": repo_path_normalized,
                        "actual_toplevel": toplevel_normalized,
                    })
                    # Mark items from this repo as shipped_error.
                    for _, _, item_result in repo_items:
                        item_result["ship_error"] = "git_toplevel_mismatch"
                    continue

                # Collect files for this repo (repo-relative).
                files_to_add = []
                for _, _, item_result in repo_items:
                    files_to_add.extend(item_result.get("filesWritten", []))

                if files_to_add:
                    # VALIDATION P2: Validate all filesWritten paths before git operations.
                    # Ensure they are relative and don't escape the repo root.
                    invalid_files = []
                    for file_path in files_to_add:
                        try:
                            _validate_file_path(file_path, repo_path)
                        except ValueError as e:
                            invalid_files.append((file_path, str(e)))

                    if invalid_files:
                        # Path validation failed; fail this item explicitly.
                        repo_ship_results.append({
                            "repo": repo_path,
                            "committed": False,
                            "error": "invalid_file_paths",
                            "files_count": len(repo_items),
                            "invalid_files": invalid_files,
                        })
                        # Mark items from this repo as shipped_error.
                        for _, _, item_result in repo_items:
                            item_result["ship_error"] = f"invalid file paths: {invalid_files}"
                        continue

                    # Add files. Escape each filename to prevent shell injection.
                    escaped_files = [_quote_arg(f) for f in files_to_add]
                    add_cmd = "git add " + " ".join(escaped_files)
                    add_result = driver.run_command(add_cmd, cwd=repo_path)
                    if add_result.exit_code != 0:
                        # GIT ADD FAILURE P1: git add may have partially succeeded,
                        # leaving staged residue. Run git reset to clean the index.
                        reset_result = driver.run_command("git reset", cwd=repo_path)
                        unstage_ok = reset_result.exit_code == 0

                        repo_ship_results.append({
                            "repo": repo_path,
                            "committed": False,
                            "error": "git_add_failed",
                            # Truncated stderr/stdout for diagnostics (same as commit failure path)
                            "error_detail": ((add_result.stderr or "") + " | " + (add_result.stdout or ""))[:300],
                            "files_count": len(repo_items),
                            "files_unstaged": unstage_ok,
                            "unstage_error": None if unstage_ok else reset_result.stderr,
                        })
                        # Mark items from this repo as shipped_error.
                        for _, _, item_result in repo_items:
                            item_result["ship_error"] = "git_add_failed"
                        continue

                    # Commit. Escape the message to prevent shell injection.
                    commit_msg = f"Wave: {len(repo_items)} items verified"
                    commit_cmd = f"git commit -m {_quote_arg(commit_msg)}"
                    commit_result = driver.run_command(commit_cmd, cwd=repo_path)
                    if commit_result.exit_code != 0:
                        # UNSTAGE P3: Commit failed; run git reset to unstage the files.
                        # This prevents staged-files residue on partial failure.
                        reset_result = driver.run_command("git reset", cwd=repo_path)
                        unstage_ok = reset_result.exit_code == 0

                        repo_ship_results.append({
                            "repo": repo_path,
                            "committed": False,
                            "error": "git_commit_failed",
                            # Truncated stderr/stdout: a bare label is undiagnosable
                            # from the Report (identity, hooks, lock contention all
                            # land here with different remedies).
                            "error_detail": ((commit_result.stderr or "") + " | " + (commit_result.stdout or ""))[:300],
                            "files_count": len(repo_items),
                            "files_unstaged": unstage_ok,
                            "unstage_error": None if unstage_ok else reset_result.stderr,
                        })
                        # Mark items from this repo as shipped_error.
                        for _, _, item_result in repo_items:
                            item_result["ship_error"] = "git_commit_failed"
                        continue

                    # Get the commit SHA.
                    sha_result = driver.run_command("git rev-parse HEAD", cwd=repo_path)
                    commit_sha = sha_result.stdout.strip() if sha_result.exit_code == 0 else None

                    # Push.
                    push_result = driver.run_command("git push", cwd=repo_path)
                    if push_result.exit_code != 0:
                        # Push failed; abort THIS repo's push but continue others.
                        repo_ship_results.append({
                            "repo": repo_path,
                            "committed": True,
                            "sha": commit_sha,
                            "error": "git_push_failed",
                            "files_count": len(repo_items),
                        })
                        # Mark items from this repo as shipped (commit succeeded even if push failed).
                        for _, _, item_result in repo_items:
                            item_result["ship_warning"] = "git_push_failed"
                            shipped_items.append(item_result["slug"])
                        continue

                    # Success: record this repo's ship.
                    repo_ship_results.append({
                        "repo": repo_path,
                        "committed": True,
                        "sha": commit_sha,
                        "files_count": len(repo_items),
                    })
                    # Mark items from this repo as shipped.
                    for _, _, item_result in repo_items:
                        shipped_items.append(item_result["slug"])

            # Record shipped items and per-repo results.
            if shipped_items:
                result["shipped"] = shipped_items
            if repo_ship_results:
                result["shipped_repos"] = repo_ship_results

    return result


def result_to_report(wave_result: Dict[str, Any]) -> Dict[str, Any]:
    """Convert run_wave result dict to fleet_ledger Report JSON format.

    The Report JSON is compatible with `fleet_ledger.py append-wave` and contains:
      - tokens: {buildOut, verifyOut, repairOut, totalOut}
      - integration: {green: bool, ...}
      - repairsUsed: int
      - built: [item results]
      - preflight_ok: bool
      - aborted: bool

    Args:
        wave_result: dict returned from run_wave()

    Returns:
        dict in fleet_ledger Report format
    """
    built_items = wave_result.get("built", [])
    repairs_used = sum(item.get("repairs", 0) for item in built_items)

    # Determine if wave was fully green (all items verified and not aborted).
    green = not wave_result.get("aborted", False) and all(
        item.get("verified", False) for item in built_items
    )

    report = {
        "tokens": {
            "buildOut": 100,  # Placeholder; driver should track real tokens
            "verifyOut": 0,
            "repairOut": repairs_used * 50 if repairs_used > 0 else 0,
            "totalOut": 100 + repairs_used * 50,
        },
        "integration": {
            "green": green,
        },
        "repairsUsed": repairs_used,
        "built": built_items,
        "preflight_ok": wave_result.get("preflight_ok", False),
        "aborted": wave_result.get("aborted", False),
    }

    return report


def main():
    """CLI entrypoint for one-turn wave mode.

    Usage:
      python -m driver.wave_loop --manifest <path> [--one-turn] [--state-dir <path>] [--output <path>]

    The --one-turn flag enables the complete wave sequence in one invocation.
    Output is JSON (either to stdout or --output file).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="One-turn wave mode: run a complete wave (preflight - build - verify - repair - report)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python driver/wave_loop.py --manifest wave.json --one-turn
  python driver/wave_loop.py --manifest wave.json --one-turn --output report.json
  python driver/wave_loop.py --manifest wave.json --one-turn --state-dir ./state
        """,
    )

    parser.add_argument(
        "--manifest",
        required=True,
        type=str,
        help="Path to wave manifest JSON file (required)",
    )
    parser.add_argument(
        "--one-turn",
        action="store_true",
        help="Run the complete wave in one turn (preflight - build - verify - repair - report)",
    )
    parser.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to state directory for coordination/cost tracking (optional)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file for Report JSON (default: stdout)",
    )
    parser.add_argument(
        "--git",
        action="store_true",
        help="Enable git operations (stage, commit, push verified items)",
    )

    args = parser.parse_args()

    # Load manifest from JSON file.
    try:
        manifest_path = Path(args.manifest)
        if not manifest_path.exists():
            print(f"Error: manifest file not found: {args.manifest}", file=sys.stderr)
            return 1

        with open(manifest_path) as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in manifest file: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: failed to load manifest: {e}", file=sys.stderr)
        return 1

    # For now, use the Claude Code reference driver.
    # In the future, this should be configurable via backend_config.
    try:
        from claude_code_driver import ClaudeCodeDriver
        driver = ClaudeCodeDriver()
    except ImportError:
        print(
            "Error: could not import ClaudeCodeDriver. "
            "Ensure driver/ is on the Python path.",
            file=sys.stderr,
        )
        return 1

    # Prepare git config if --git flag is used.
    git_config = None
    if args.git:
        # Get the current top-level directory as a guard.
        toplevel_result = driver.run_command("git rev-parse --show-toplevel")
        if toplevel_result.exit_code != 0:
            print("Error: could not determine git top-level directory", file=sys.stderr)
            return 1
        toplevel = toplevel_result.stdout.strip()
        git_config = {"expectTopLevel": toplevel}

    # Run the wave.
    try:
        result = run_wave(
            driver,
            manifest,
            state_dir=args.state_dir,
            git=git_config,
        )
    except Exception as e:
        print(f"Error: wave execution failed: {e}", file=sys.stderr)
        return 1

    # Convert result to Report JSON format.
    report = result_to_report(result)

    # Output Report JSON.
    report_json = json.dumps(report, indent=2)

    if args.output:
        try:
            with open(args.output, "w") as f:
                f.write(report_json)
            print(f"Report written to {args.output}", file=sys.stderr)
        except Exception as e:
            print(f"Error: failed to write report file: {e}", file=sys.stderr)
            return 1
    else:
        print(report_json)

    # Return exit code based on wave status.
    # Exit 0 if wave completed (aborted or not), exit 1 if preflight failed.
    if not result.get("preflight_ok"):
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
