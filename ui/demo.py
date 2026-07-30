#!/usr/bin/env python3
"""Aesop UI -- zero-key demo mode (seeded fleet snapshot provider).

`python ui/serve.py --demo` serves the dashboard populated with a realistic,
ANONYMIZED fleet snapshot so a stranger with no API key, no gh auth, and no
prior runs sees a live-looking (but clearly labelled) dashboard instead of a
dead shell.

Design:
  - activate() materializes a throwaway demo root (heartbeats, tracker.json,
    orchestrator-status.json, outcomes ledger, audit backlog, fabricated agent
    transcripts) and points the standard config env vars at it BEFORE
    config.reload() runs. Every collector then reads the demo snapshot through
    its normal code path -- no collector logic is forked.
  - Timestamps are generated now-relative at seed time and a background
    refresher rewrites heartbeats / orchestrator status / transcript mtimes
    every ~45s, so ages always read fresh no matter how long the demo runs.
  - The two collectors that cannot be file-seeded (fleet agents shell out to
    node dash-extra.mjs; the PR board shells out to gh) read from
    get_demo_agents() / get_demo_wave_prs() when AESOP_DEMO=1.
  - HONESTY: demo mode self-identifies everywhere -- a fixed "DEMO DATA"
    banner is injected into the served HTML (BANNER_HTML) and /api/state
    carries a top-level "demo": true marker. Default mode (no flag, no env)
    is byte-identical to before.

Env vars:
  AESOP_DEMO=1        demo mode on (set by the --demo flag; may be set directly)
  AESOP_DEMO_ROOT     optional fixed demo root (tests); default: mkdtemp
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEMO_ENV = "AESOP_DEMO"
DEMO_ROOT_ENV = "AESOP_DEMO_ROOT"

DEMO_WAVE_BANNER = "wave-2: guardrail wiring + stats hardening"
DEMO_MODEL = "claude-haiku-4-5"

# Visible self-identification strip injected into the served HTML (handler.py
# serve_html). Inline-styled, fixed, high z-index: never mistaken for live state.
BANNER_HTML = (
    '<div id="aesop-demo-banner" role="note" style="position:fixed;top:0;'
    'left:0;right:0;z-index:2147483647;background:#b45309;color:#fff;'
    'font:600 12px/1.7 system-ui,-apple-system,sans-serif;text-align:center;'
    'padding:2px 10px;letter-spacing:0.06em;">'
    'DEMO DATA &#8212; seeded snapshot, not live fleet state '
    '(started with: python ui/serve.py --demo)</div>'
)

_REFRESH_INTERVAL_SECONDS = 45.0
_BACKUP_LOG_MAX_LINES = 40

_state_lock = threading.Lock()
_refresh_started = False
_refresh_stop = threading.Event()
_demo_root = None

# Backlog status glyphs (unicode escapes keep this source ASCII-only).
_DONE = "✅"      # check mark
_INFLIGHT = "\U0001F535"  # blue circle
_TODO = "⬜"      # white square


def enabled():
    """True when demo mode is active for this process."""
    return os.environ.get(DEMO_ENV) == "1"


def maybe_activate(argv=None):
    """Activate demo mode if --demo is in argv or AESOP_DEMO=1 is already set.

    Must run BEFORE config.reload() (serve.py calls it first) so the demo env
    vars are visible when config resolves its paths. Returns True if active.
    """
    argv = sys.argv if argv is None else argv
    if "--demo" not in argv and not enabled():
        return False
    activate()
    return True


def activate():
    """Materialize the demo root, point config env vars at it, start refresher.

    Idempotent: re-seeding an existing root just rewrites the snapshot files;
    the refresher thread starts at most once per process.
    """
    global _demo_root
    with _state_lock:
        root_env = os.environ.get(DEMO_ROOT_ENV)
        if root_env:
            root = Path(root_env)
            root.mkdir(parents=True, exist_ok=True)
        else:
            root = Path(tempfile.mkdtemp(prefix="aesop-demo-"))
            atexit.register(shutil.rmtree, str(root), ignore_errors=True)
        _demo_root = root

    seed(root)

    os.environ[DEMO_ENV] = "1"
    os.environ["AESOP_STATE_ROOT"] = str(root / "state")
    os.environ["AESOP_WATCHDOG_HEARTBEAT"] = str(root / "state" / ".watchdog-heartbeat")
    os.environ["AESOP_MONITOR_HEARTBEAT"] = str(root / "state" / ".monitor-heartbeat")
    os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(root / "transcripts")
    os.environ["AESOP_AUDIT_BACKLOG"] = str(root / "AUDIT-BACKLOG.md")

    _start_refresher(root)
    return root


# ==============================================================================
# Seeding
# ==============================================================================

def _iso(dt):
    """UTC ISO-8601 with Z suffix, seconds precision."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


def seed(root):
    """Write the full demo snapshot under `root` with now-relative timestamps."""
    root = Path(root)
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    epoch = str(int(time.time()))

    (state / ".watchdog-heartbeat").write_text(epoch, encoding="utf-8")
    (state / ".monitor-heartbeat").write_text(epoch, encoding="utf-8")

    (state / ".watchdog-repos.json").write_text(json.dumps([
        {"repo": "acme-api", "state": "clean, backed up"},
        {"repo": "acme-web", "state": "clean, backed up"},
        {"repo": "acme-fleet", "state": "wave-2 in flight"},
    ], indent=2), encoding="utf-8")

    _write_backup_log(state, now)
    _write_tracker(state, now)
    _write_orchestrator_status(state, now)
    _write_ledger(state, now)
    _write_backlog(root)
    _write_transcripts(root, now)


def _write_backup_log(state, now):
    def stamp(minutes_ago):
        return _iso(now - timedelta(minutes=minutes_ago))

    lines = [
        f"{stamp(34)} backup OK acme-api (bundle 1.1M, 0.8s)",
        f"{stamp(34)} backup OK acme-web (bundle 2.3M, 1.2s)",
        f"{stamp(29)} secret-scan clean: 0 findings across 3 repos",
        f"{stamp(21)} wave-2 dispatch: 6 workers on feature lanes",
        f"{stamp(14)} merge train: PR #44 merged (CI green, 4m11s)",
        f"{stamp(9)} merge train: PR #45 merged (CI green, 3m47s)",
        f"{stamp(4)} watchdog heartbeat OK (all daemons alive)",
        f"{stamp(1)} backup OK acme-fleet (bundle 0.6M, 0.5s)",
    ]
    (state / "FLEET-BACKUP.log").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")


def _write_tracker(state, now):
    def ago(hours):
        return _iso(now - timedelta(hours=hours))

    items = [
        {"id": "b41c09e2d7f3", "title": "Tracker auto-close on merged PRs (guardrail G1)",
         "priority": "P0", "status": "done", "lane": "done", "source": "audit",
         "tags": ["guardrail"], "notes": None, "pr_link": None,
         "created_at": ago(30), "completed_at": ago(6)},
        {"id": "7f2a51c8e90b", "title": "Spec-contract linter for dispatch prompts (G4)",
         "priority": "P0", "status": "in-progress", "lane": "in-progress",
         "source": "audit", "tags": ["guardrail"], "notes": "linter wired; gate pending",
         "pr_link": None, "created_at": ago(28), "completed_at": None},
        {"id": "c93d47a1b5e6", "title": "Stats gate: single-source stats.json regeneration",
         "priority": "P1", "status": "in-progress", "lane": "in-progress",
         "source": "audit", "tags": ["stats"], "notes": None, "pr_link": None,
         "created_at": ago(26), "completed_at": None},
        {"id": "e15f82d4a7c0", "title": "Subprocess hygiene guard for tests (G6)",
         "priority": "P1", "status": "todo", "lane": "ranked", "source": "audit",
         "tags": ["tests"], "notes": None, "pr_link": None,
         "created_at": ago(25), "completed_at": None},
        {"id": "a28b63f5c1d9", "title": "Watcher/polling anti-pattern linter (G3)",
         "priority": "P1", "status": "todo", "lane": "ranked", "source": "audit",
         "tags": ["guardrail"], "notes": None, "pr_link": None,
         "created_at": ago(24), "completed_at": None},
        {"id": "f36c94e6d2a1", "title": "Dashboard SSE cost section + analytics panel",
         "priority": "P1", "status": "done", "lane": "done", "source": "ideation",
         "tags": ["ui"], "notes": None, "pr_link": None,
         "created_at": ago(52), "completed_at": ago(20)},
        {"id": "d47e05a7b3c2", "title": "Wave PR board: failure drill-down polish",
         "priority": "P2", "status": "todo", "lane": "proposed", "source": "ideation",
         "tags": ["ui"], "notes": None, "pr_link": None,
         "created_at": ago(18), "completed_at": None},
        {"id": "58af16c8d4e3", "title": "Cost ceiling alert wired to burn-rate projection",
         "priority": "P2", "status": "todo", "lane": "proposed", "source": "ideation",
         "tags": ["cost"], "notes": None, "pr_link": None,
         "created_at": ago(16), "completed_at": None},
        {"id": "69ba27d9e5f4", "title": "Pre-push secret-scan gate hardening",
         "priority": "P0", "status": "done", "lane": "done", "source": "audit",
         "tags": ["security"], "notes": None, "pr_link": None,
         "created_at": ago(54), "completed_at": ago(31)},
        {"id": "1ac538e0f6a5", "title": "Quickstart docs: zero-key demo mode",
         "priority": "P2", "status": "todo", "lane": "ranked", "source": "ideation",
         "tags": ["docs"], "notes": None, "pr_link": None,
         "created_at": ago(3), "completed_at": None},
    ]
    (state / "tracker.json").write_text(
        json.dumps({"version": 1, "items": items}, indent=2), encoding="utf-8")


def _write_orchestrator_status(state, now):
    status = {
        "id": "orchestrator-main",
        "role": "orchestrator",
        "phase": DEMO_WAVE_BANNER,
        "activity": "merge train: 2 of 5 PRs merged; dispatching stats-gate fix lane",
        "wave_start_time": _iso(now - timedelta(minutes=16)),
        "started_at": _iso(now - timedelta(minutes=16)),
        "updated_at": _iso(now),
    }
    (state / "orchestrator-status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8")


def _write_ledger(state, now):
    """Outcomes ledger: 9-column markdown table (see ui/cost.py docstring)."""
    ledger_dir = state / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)

    def ts(hours_ago):
        return (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S")

    # (hours_ago, agent_type, model, duration_s, tok_in, tok_out, verdict, phase, wave)
    rows = [
        # wave 1 (yesterday): build + verify
        (30.0, "general-purpose", DEMO_MODEL, 412, 5200, 18400, "OK", "build", "1"),
        (29.6, "general-purpose", DEMO_MODEL, 388, 4900, 16200, "OK", "build", "1"),
        (29.2, "test-writer", DEMO_MODEL, 355, 4100, 14800, "OK", "build", "1"),
        (28.9, "general-purpose", DEMO_MODEL, 501, 6100, 21500, "FAILED", "build", "1"),
        (28.4, "general-purpose", DEMO_MODEL, 366, 4400, 15900, "OK", "repair", "1"),
        (27.8, "doc-writer", DEMO_MODEL, 214, 2600, 9800, "OK", "build", "1"),
        (27.1, "adversarial-reviewer", "claude-sonnet-4-5", 298, 8800, 6400, "OK", "verify", "1"),
        (26.5, "adversarial-reviewer", "claude-sonnet-4-5", 312, 9100, 7100, "OK", "verify", "1"),
        (26.0, "general-purpose", DEMO_MODEL, 190, 2100, 7400, "EMPTY", "build", "1"),
        (25.4, "orchestrator", "claude-opus-4-5", 122, 14200, 3800, "OK", "merge", "1"),
        # wave 2 (today): mid-execution
        (5.8, "general-purpose", DEMO_MODEL, 402, 5100, 17600, "OK", "build", "2"),
        (5.2, "general-purpose", DEMO_MODEL, 377, 4700, 16800, "OK", "build", "2"),
        (4.7, "test-writer", DEMO_MODEL, 341, 3900, 14100, "OK", "build", "2"),
        (4.1, "general-purpose", DEMO_MODEL, 458, 5600, 19300, "FAILED", "build", "2"),
        (3.6, "general-purpose", DEMO_MODEL, 344, 4200, 15200, "OK", "repair", "2"),
        (3.0, "doc-writer", DEMO_MODEL, 201, 2400, 9200, "OK", "build", "2"),
        (2.4, "adversarial-reviewer", "claude-sonnet-4-5", 288, 8600, 6100, "OK", "verify", "2"),
        (1.8, "general-purpose", DEMO_MODEL, 296, 3600, 12800, "OK", "build", "2"),
        (1.2, "general-purpose", DEMO_MODEL, 274, 3300, 11900, "OK", "build", "2"),
        (0.7, "orchestrator", "claude-opus-4-5", 98, 11800, 3100, "OK", "merge", "2"),
        (0.3, "general-purpose", DEMO_MODEL, 189, 2300, 8600, "OK", "build", "2"),
        (0.1, "test-writer", DEMO_MODEL, 176, 2100, 7900, "OK", "build", "2"),
    ]
    lines = [
        "| timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict | phase | wave |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for hours_ago, agent, model, dur, tin, tout, verdict, phase, wave in rows:
        lines.append(
            f"| {ts(hours_ago)} | {agent} | {model} | {dur} | {tin} | {tout} "
            f"| {verdict} | {phase} | {wave} |")
    (ledger_dir / "OUTCOMES-LEDGER.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def _write_backlog(root):
    """AUDIT-BACKLOG.md mid-wave: done + inflight + todo across tiers."""
    content = "\n".join([
        "# Audit backlog (demo fleet)",
        "",
        "## P0 (do first)",
        f"- {_DONE} **[ci] Re-enable windows shard aggregator after rename**",
        f"- {_INFLIGHT} **[sec] Pin subprocess cwd in watchdog test helpers**",
        f"- {_TODO} **[state] Reconcile tracker drift after merge train**",
        "",
        "## P1 (wave 2)",
        f"- {_DONE} **[ui] SSE reconnect drops stale agent rows**",
        f"- {_INFLIGHT} **[guard] Spec-contract linter false-positive on quoted flags**",
        f"- {_TODO} **[perf] Cache transcripts fingerprint between collector ticks**",
        f"- {_TODO} **[docs] Quickstart: zero-key demo mode**",
        "",
        "## P2 (nice to have)",
        f"- {_TODO} **[ui] Dark-mode contrast on cost chart axes**",
        f"- {_TODO} **[tools] Merge train must verify MERGED state, not exit 0**",
        "",
    ])
    (root / "AUDIT-BACKLOG.md").write_text(content, encoding="utf-8")


# ==============================================================================
# Fabricated fleet agents + transcripts
# ==============================================================================

# (full_hex_id, status, project, task_label, phase_style, runtime_s, tokens)
# phase_style drives the fabricated transcript tail so wave_dispatch's real
# phase inference produces the intended label (tool-use/thinking/dispatch/done).
_AGENT_SPECS = [
    ("3f9a1c7e5b2d84a6c0e1f2a3", "running", "acme-fleet",
     "guard: wire spec-contract linter into pre-push gate", "tool-use", 780, 21400),
    ("8b2e4d6f1a3c95b7d2f0e1c4", "running", "acme-fleet",
     "ui: SSE reconnect drops stale agent rows", "tool-use", 645, 18900),
    ("5c7d9e1f3a5b86c9e4a2d0b5", "running", "acme-fleet",
     "stats: single-source stats.json regeneration gate", "thinking", 512, 14200),
    ("2d4f6a8c0e2b97d1f6c3a5e6", "running", "acme-fleet",
     "tests: subprocess hygiene guard (G6) for tests/", "tool-use", 433, 12800),
    ("9e1f3a5c7d4e08a2b8d5c7f7", "running", "acme-web",
     "docs: quickstart walkthrough for zero-key demo", "thinking", 388, 9600),
    ("6a8c0e2d4f5a19b3c1e7d9a8", "running", "acme-api",
     "state: tracker auto-close on merged PRs (G1)", "dispatch", 95, 2100),
    ("4b6d8f0a2c6b20c4d3f9e1b9", "idle", "acme-fleet",
     "review: adversarial pass on watcher linter", "done", 1240, 30800),
    ("1c3e5a7f9b7c31d5e5a0f2c0", "idle", "acme-api",
     "bench: quality scorer calibration run", "done", 1105, 26700),
    ("7d9f1b3e5d8e42a6f7b1a3d1", "idle", "acme-fleet",
     "monitor: heartbeat staleness thresholds audit", "done", 990, 22300),
]


def get_demo_agents():
    """Fleet-agent rows in the dash-extra.mjs --json shape (ui Agent contract).

    Timestamps are computed from now at every call so ages always read fresh
    and advance between SSE ticks.
    """
    now = datetime.now(timezone.utc)
    rows = []
    for i, (hex_id, status, project, label, _style, runtime_s, tokens) in \
            enumerate(_AGENT_SPECS):
        running = status == "running"
        age_s = (6 + i * 7) if running else (420 + i * 45)
        last = now - timedelta(seconds=age_s)
        started = last - timedelta(seconds=runtime_s)
        rows.append({
            "id": hex_id[:13],
            "project": project,
            "status": status,
            "age_s": age_s,
            "hint": label[:60],
            "startedAt": _iso(started),
            "lastActivity": _iso(last),
            "runtimeSeconds": runtime_s,
            "tokensUsed": tokens,
            "taskLabel": label[:80],
        })
    return rows


def _dispatch_prompt(label, project):
    return (
        f"You are a Haiku worker on the {project} fleet (demo snapshot). "
        f"Task: {label}. Read exactly one domain CLAUDE.md, work on a feature "
        "branch, keep the change scoped, run the targeted test, then push. "
        "Report a one-line summary when green."
    )


def _write_transcripts(root, now):
    """Fabricate agent-*.jsonl transcripts so the real transcript-driven
    collectors (wave dispatch/gantt/reasoning tail, agent inspector) light up.

    Content per line mirrors the Claude Code NDJSON shapes ui/agents.py and
    ui/wave_dispatch.py parse: a user dispatch line, then assistant lines with
    text / tool-use content blocks. The phase_style controls the tail so phase
    inference lands on the intended label.
    """
    subagents = root / "transcripts" / "demo-session" / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)

    for hex_id, status, project, label, style, runtime_s, _tokens in _AGENT_SPECS:
        started = now - timedelta(seconds=runtime_s + 60)
        lines = [{
            "type": "user",
            "parentUuid": None,
            "timestamp": _iso(started),
            "message": {"role": "user", "content": _dispatch_prompt(label, project)},
        }]

        def assistant(text, minutes):
            return {
                "type": "assistant",
                "model": DEMO_MODEL,
                "timestamp": _iso(started + timedelta(minutes=minutes)),
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": text}]},
            }

        if style == "dispatch":
            # Last line is the user dispatch: freshly dispatched agent.
            pass
        elif style == "thinking":
            lines.append(assistant(
                "Reading the domain guide and the current gate wiring to map "
                "the smallest change that keeps the invariants.", 1))
            lines.append(assistant(
                "Plan: add the check behind the existing gate entry point, "
                "then extend the targeted suite before touching the wiring.", 3))
        elif style == "tool-use":
            lines.append(assistant(
                "Located the seam; applying the edit now. [tool_use: Edit]", 2))
            lines.append(assistant(
                "Edit applied; running the targeted suite. [tool_use: Bash]", 4))
        else:  # done
            lines.append(assistant("Change applied on the feature branch.", 2))
            lines.append(assistant("Targeted suite green on first run.", 5))
            lines.append(assistant("Pushed the branch; summary written.", 7))
            lines.append(assistant(
                "Done: change scoped, tests green, branch pushed.", 8))

        path = subagents / f"agent-{hex_id}.jsonl"
        path.write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n",
            encoding="utf-8")

    # Main-thread session transcript feeds the messages panel
    # (collectors.get_main_thread_messages reads top-level role/content).
    session = root / "transcripts" / "demo-session" / "session-demo.jsonl"
    msgs = [
        ("user", "Kick off wave-2: guardrail wiring plus stats hardening. "
                 "Six disjoint lanes, merge train when CI is green.", 18),
        ("assistant", "Wave-2 dispatched: 6 Haiku workers on feature lanes. "
                      "Watching heartbeats and the PR board.", 17),
        ("assistant", "PR #44 green, merged. PR #45 in the merge train.", 9),
        ("user", "Status on the stats gate lane?", 6),
        ("assistant", "Stats gate lane is mid-edit (spec-contract linter "
                      "wired); targeted suite running now.", 5),
        ("assistant", "Merge train 2 of 5 done. No stalls; all lanes fresh "
                      "under the 5-minute activity threshold.", 1),
    ]
    session_lines = []
    for role, text, minutes_ago in msgs:
        session_lines.append(json.dumps({
            "role": role,
            "content": text,
            "timestamp": _iso(now - timedelta(minutes=minutes_ago)),
        }))
    session.write_text("\n".join(session_lines) + "\n", encoding="utf-8")


# ==============================================================================
# Fabricated PR board (gh is never invoked in demo mode)
# ==============================================================================

def get_demo_wave_prs():
    """Wave PR board payload in the wave_prs.get_wave_prs() shape.

    URLs stay empty on purpose: demo rows must never link to real PRs.
    """
    now = datetime.now(timezone.utc)

    def created(hours_ago):
        return _iso(now - timedelta(hours=hours_ago))

    prs = [
        {"number": 48, "title": "feat(ui): zero-key demo mode -- seeded dashboard for strangers",
         "branch": "feat/dashboard-demo-mode", "url": "", "ci": "pending",
         "mergeable": "MERGEABLE", "is_draft": False, "review_decision": "",
         "created_at": created(1), "blocker": "CI pending", "has_pr": True},
        {"number": 47, "title": "fix(state): tracker auto-close on merged PRs (guardrail G1)",
         "branch": "feat/tracker-autoclose", "url": "", "ci": "passing",
         "mergeable": "MERGEABLE", "is_draft": False,
         "review_decision": "APPROVED", "created_at": created(4),
         "blocker": None, "has_pr": True},
        {"number": 46, "title": "feat(tools): spec-contract linter for dispatch prompts (G4)",
         "branch": "feat/spec-contract-linter", "url": "", "ci": "passing",
         "mergeable": "MERGEABLE", "is_draft": False,
         "review_decision": "REVIEW_REQUIRED", "created_at": created(7),
         "blocker": "Review required", "has_pr": True},
        {"number": 45, "title": "test(guard): adversarial regression traps for incident classes",
         "branch": "feat/regression-traps", "url": "", "ci": "failing",
         "mergeable": "UNKNOWN", "is_draft": False, "review_decision": "",
         "created_at": created(9), "blocker": "CI failing", "has_pr": True},
        {"number": None, "title": "feat/stats-hardening",
         "branch": "feat/stats-hardening", "url": "", "ci": "none",
         "mergeable": "UNKNOWN", "is_draft": False, "review_decision": "",
         "created_at": "", "blocker": "No PR opened yet", "has_pr": False},
    ]
    return {
        "available": True,
        "error": None,
        "generated_at": _iso(now),
        "prs": prs,
    }


# ==============================================================================
# Freshness refresher
# ==============================================================================

def _refresh_once(root):
    """Rewrite the time-sensitive demo files so ages always read fresh."""
    root = Path(root)
    state = root / "state"
    now_dt = datetime.now(timezone.utc)
    now = time.time()
    epoch = str(int(now))

    for name in (".watchdog-heartbeat", ".monitor-heartbeat"):
        try:
            (state / name).write_text(epoch, encoding="utf-8")
        except OSError:
            pass

    # Keep the orchestrator banner fresh without moving the wave start time.
    status_file = state / "orchestrator-status.json"
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        data["updated_at"] = _iso(now_dt)
        status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass

    # Append a live-looking event line (also nudges the SSE data section so
    # heartbeat ages re-emit); trim so the log never grows unbounded.
    log_file = state / "FLEET-BACKUP.log"
    try:
        lines = []
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        lines.append(f"{_iso(now_dt)} watchdog heartbeat OK (all daemons alive)")
        log_file.write_text(
            "\n".join(lines[-_BACKUP_LOG_MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass

    # Keep fabricated transcripts inside the <30min "recent" window forever.
    transcripts = root / "transcripts"
    try:
        for path in transcripts.glob("**/agent-*.jsonl"):
            os.utime(str(path), (now, now))
        session = transcripts / "demo-session" / "session-demo.jsonl"
        if session.exists():
            os.utime(str(session), (now + 2, now + 2))
    except OSError:
        pass


def _refresh_loop(root, stop_event):
    while not stop_event.wait(_REFRESH_INTERVAL_SECONDS):
        try:
            _refresh_once(root)
        except Exception as e:
            print(f"[demo] refresh error: {e}", file=sys.stderr)


def _start_refresher(root):
    global _refresh_started
    with _state_lock:
        if _refresh_started:
            return
        _refresh_started = True
        t = threading.Thread(target=_refresh_loop, args=(root, _refresh_stop),
                             daemon=True)
        t.start()
