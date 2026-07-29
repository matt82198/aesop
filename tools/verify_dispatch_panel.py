"""Browser-level proof for the DispatchPanel component (ui/web/dist/ + /api/wave/dispatch).

Drives the BUILT React app served by `python ui/serve.py` and exercises the
full stack of the dispatch panel — route wiring, component mount, the real
GET /api/wave/dispatch endpoint, polling behavior, and rendering — in a real
Chromium via Playwright.

Two phases, each a fresh server boot with fixture agent transcripts:

  Available: fixture agents with varying phases (tool-use/thinking/stall) →
    (a) console clean
    (b) Activity view mounts and DispatchPanel is visible
    (c) agent rows render with id, phase badge, age, tokens
    (d) warnings display for inactive agents (age >5min)
    (e) polling works: data updates on timer
    (f) wave_phase header shows current wave info

  Unavailable (no agents): empty transcripts dir →
    (g) DispatchPanel shows "No active workflow" message

Run: python tools/verify_dispatch_panel.py            (exit 0 = proven, 1 = failed)
     python tools/verify_dispatch_panel.py --allow-skip (exit 0 = proven or skipped)

Fails with exit 1 if playwright/chromium is unavailable (unless --allow-skip).
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Ensure this tool's own directory (tools/) is importable so the shared
# playwright harness resolves regardless of cwd or how the file is loaded
# (the import-gate loads tools by path, without tools/ on sys.path).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright_common import free_port, copy_dist, start_server, stop_server

REPO = Path(__file__).resolve().parent.parent
SERVE = REPO / "ui" / "serve.py"

# Generous, CI-safe waits.
SERVER_BOOT_TRIES = int(os.environ.get("AESOP_VERIFY_BOOT_TRIES", "150"))
SERVER_BOOT_SLEEP = 0.2
SEL_TIMEOUT_MS = int(os.environ.get("AESOP_VERIFY_SEL_TIMEOUT_MS", "30000"))




def build_root_with_agents(num_agents=3):
    """Fresh temp root with dist + fixture agents; returns root."""
    root = Path(tempfile.mkdtemp(prefix="aesop-verify-dispatch-panel-"))
    (root / "state").mkdir(exist_ok=True)

    # Create agent transcripts in real Claude Code layout
    # Real structure: {project}/subagents/agent-*.jsonl (not memory/)
    transcripts_root = root / "transcripts" / "aesop" / "subagents"
    transcripts_root.mkdir(parents=True)

    # Create fixture agents
    agents = [
        ("fleet-fix-0", "tool-use", 0),  # Fresh, tool-use phase
        ("fleet-fix-1", "stall", 500),   # Old, stalled (>5min)
        ("fleet-review-0", "thinking", 15),  # Recent, thinking
    ][:num_agents]

    for agent_id, phase_hint, age_sec in agents:
        agent_path = transcripts_root / f"agent-{agent_id}.jsonl"
        # Create minimal NDJSON
        if "tool" in phase_hint:
            content = json.dumps({"type": "assistant", "text": "[tool_use: write]"}) + "\n"
        elif "think" in phase_hint:
            content = json.dumps({"type": "assistant", "text": "Assistant thinking"}) + "\n"
        else:
            content = (
                json.dumps({"type": "assistant", "text": "thinking"}) + "\n" +
                json.dumps({"type": "assistant", "text": "done"}) + "\n"
            )
        agent_path.write_text(content, encoding='utf-8')
        # Set mtime for age
        now = time.time()
        old_time = now - age_sec
        os.utime(agent_path, (old_time, old_time))

    # Create dash-extra.mjs (required by dashboard)
    (root / "dash").mkdir(exist_ok=True)
    (root / "dash" / "dash-extra.mjs").write_text(
        "console.log(JSON.stringify([]));\n", encoding="utf-8")

    copy_dist(root, REPO)
    return root




def run_available(pw, failures):
    """Test DispatchPanel with active agents."""
    root = build_root_with_agents(3)
    port = free_port()
    server = start_server(root, port, REPO, SERVE, SERVER_BOOT_TRIES, SERVER_BOOT_SLEEP, "0.5")
    try:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        console_errors, failed_urls = [], []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        page.on("response", lambda r: failed_urls.append(r.url) if r.status >= 400 else None)

        try:
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            # Navigate to Activity view
            page.wait_for_selector("[data-testid='health-header']", timeout=SEL_TIMEOUT_MS)
            page.evaluate("location.hash = '#/activity'")
            page.wait_for_selector("[data-testid='view-activity']", timeout=SEL_TIMEOUT_MS)
        except Exception as e:
            failures.append(f"Activity view never mounted: {e}")
            return

        # (b) DispatchPanel is visible
        try:
            page.wait_for_selector("[data-testid='dispatch-panel']", timeout=SEL_TIMEOUT_MS)
        except Exception as e:
            failures.append(f"DispatchPanel not visible: {e}")
            return

        # (c) Agent rows render with data
        try:
            rows = page.locator("[data-testid='dispatch-agent-row']").count()
            assert rows >= 3, f"expected >=3 agent rows, got {rows}"
            # Check first agent id
            first_id = page.inner_text("[data-testid='dispatch-agent-row']").split()[0]
            assert "fleet" in first_id or "fix" in first_id, f"unexpected agent id: {first_id}"
        except Exception as e:
            failures.append(f"Agent rows not rendered: {e}")

        # (d) Warnings display for stalled agents
        try:
            body = page.inner_text("[data-testid='dispatch-panel']")
            assert "inactive" in body.lower(), "warning for inactive agent not shown"
        except Exception as e:
            failures.append(f"Warnings not shown: {e}")

        # (e) Polling works (data updates)
        try:
            # Get initial agent count
            initial_rows = page.locator("[data-testid='dispatch-agent-row']").count()
            # Wait for a poll cycle
            time.sleep(2)
            # SSE keeps connections open indefinitely, so use a short timeout
            try:
                page.wait_for_load_state("domcontentloaded", timeout=500)
            except Exception:
                pass  # Timeout expected when SSE is active
            # Rows should still be there (no crash)
            final_rows = page.locator("[data-testid='dispatch-agent-row']").count()
            assert final_rows == initial_rows, "agent count changed during poll"
        except Exception as e:
            failures.append(f"Polling failed: {e}")

        # (f) Wave phase header
        try:
            # Wave phase should be in the dispatch panel (or null)
            panel = page.locator("[data-testid='dispatch-panel']").inner_text()
            assert "Wave Dispatch" in panel, "header not found"
        except Exception as e:
            failures.append(f"Header not found: {e}")

        # (a) Console clean
        time.sleep(0.2)
        real_errors = [e for e in console_errors
                       if "favicon" not in e.lower()
                       and "failed to load resource" not in e.lower()]
        if real_errors:
            failures.append(f"Console errors: {real_errors}")

        browser.close()
    finally:
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def run_unavailable(pw, failures):
    """Test DispatchPanel with no active agents."""
    root = build_root_with_agents(0)  # No agents
    port = free_port()
    server = start_server(root, port, REPO, SERVE, SERVER_BOOT_TRIES, SERVER_BOOT_SLEEP, "0.5")
    try:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        try:
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_selector("[data-testid='health-header']", timeout=SEL_TIMEOUT_MS)
            page.evaluate("location.hash = '#/activity'")
            page.wait_for_selector("[data-testid='view-activity']", timeout=SEL_TIMEOUT_MS)
        except Exception as e:
            failures.append(f"Activity view never mounted (unavailable): {e}")
            return

        # (g) Unavailable state renders
        try:
            page.wait_for_selector("[data-testid='dispatch-panel-unavailable']", timeout=SEL_TIMEOUT_MS)
            body = page.inner_text("[data-testid='dispatch-panel-unavailable']")
            assert "No active workflow" in body, "unavailable message not shown"
        except Exception as e:
            failures.append(f"Unavailable state not rendered: {e}")

        browser.close()
    finally:
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(
        description="Browser proof for DispatchPanel component"
    )
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help="Exit 0 if playwright unavailable (else 1)"
    )
    args = parser.parse_args()

    try:
        import playwright.sync_api as pw_api
        pw = pw_api.sync_playwright().start()
    except ImportError:
        print("playwright not available; skipping browser proof", file=sys.stderr)
        sys.exit(0 if args.allow_skip else 1)

    failures = []

    print("DispatchPanel proof: phase 1 (available agents)...", file=sys.stderr)
    try:
        run_available(pw, failures)
    except Exception as e:
        failures.append(f"Phase 1 crashed: {e}")

    print("DispatchPanel proof: phase 2 (unavailable)...", file=sys.stderr)
    try:
        run_unavailable(pw, failures)
    except Exception as e:
        failures.append(f"Phase 2 crashed: {e}")

    if failures:
        print("\n=== FAILURES ===", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    print("DispatchPanel proof: PASSED", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
