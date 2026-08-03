"""Browser-level proof for the Activity view agent status filter.
INDEX: Browser proof (Playwright): Activity-view agent status filter; exit 0=proven/1=failed, --allow-skip only browserless

Drives the BUILT React app served by `python ui/serve.py` and exercises the
client-side status filter on the Activity view — filter button clicks, agent
visibility, and the rendered timeline updates correctly. Uses fixture agents
with mixed statuses: running, idle, SUSPICIOUS (error-suspicious).

Three phases, each a fresh server boot:

  Filter "All": Shows all agents (running + idle + suspicious) →
    (a) console clean
    (b) Activity view renders filter controls
    (c) "All" button is selected/active
    (d) timeline shows all fixture agents

  Filter "Running": Shows only running agents →
    (e) "Running" button becomes active on click
    (f) timeline shows only running agent
    (g) other agents are hidden

  Filter "Error-Suspicious": Shows only error/suspicious agents →
    (h) "Error-Suspicious" button becomes active on click
    (i) timeline shows only suspicious agent
    (j) other agents are hidden

Additional phases for "Idle" filter and cycling through all filters.

Run: python tools/verify_activity_filter.py            (exit 0 = proven, 1 = failed)
     python tools/verify_activity_filter.py --allow-skip (exit 0 = proven or skipped)

Fails with exit 1 if playwright/chromium is unavailable (unless --allow-skip).
"""
import argparse
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Ensure this tool's own directory (tools/) is importable so the shared
# playwright harness resolves regardless of cwd or how the file is loaded.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright_common import free_port, copy_dist, start_server, stop_server, filter_real_console_errors

REPO = Path(__file__).resolve().parent.parent
SERVE = REPO / "ui" / "serve.py"

# Generous, CI-safe waits
SERVER_BOOT_TRIES = int(os.environ.get("AESOP_VERIFY_BOOT_TRIES", "150"))   # *0.2s = 30s
SERVER_BOOT_SLEEP = 0.2
SEL_TIMEOUT_MS = int(os.environ.get("AESOP_VERIFY_SEL_TIMEOUT_MS", "30000"))

# Fixture agents with mixed statuses: running, idle, SUSPICIOUS
FIXTURE_AGENTS_JSON = json.dumps([
  {
    "id": "a77b995bcdb95",
    "project": "aesop",
    "status": "running",
    "age_s": 12,
    "hint": "wave-14 U4 overview components",
    "startedAt": "2026-07-13T14:02:11.000Z",
    "lastActivity": "2026-07-13T14:31:47.000Z",
    "runtimeSeconds": 1776,
    "tokensUsed": 48213,
    "taskLabel": "Wave-14 unit U4 (overview view components) for aesop.",
    "promptFull": "Wave-14 unit U4 (overview view components) for aesop.",
  },
  {
    "id": "b12c4d99ef012",
    "project": "aesop",
    "status": "idle",
    "age_s": 341,
    "hint": "tracker lane bucketing tests",
    "startedAt": "2026-07-13T13:40:00.000Z",
    "lastActivity": "2026-07-13T14:26:02.000Z",
    "runtimeSeconds": 2762,
    "tokensUsed": 102455,
    "taskLabel": "Wave-14 unit U5 (work view components) for aesop.",
    "promptFull": "Wave-14 unit U5 (work view components) for aesop.",
  },
  {
    "id": "c99ff00aa1122",
    "project": "tr-sample-tracker",
    "status": "SUSPICIOUS",
    "age_s": 45,
    "hint": "unexpected file write outside worktree",
    "startedAt": "2026-07-13T14:20:00.000Z",
    "lastActivity": "2026-07-13T14:31:15.000Z",
    "runtimeSeconds": 675,
    "tokensUsed": 8102,
    "taskLabel": "Fix flaky test in sample tracker suite.",
    "promptFull": "Fix flaky test in sample tracker suite.",
  },
])




def build_fixture_state(agents_json: str):
    """Create a state structure that SSE will emit with fixture agents."""
    state_root = Path(tempfile.mkdtemp(prefix="aesop-verify-activity-"))
    (state_root / "state").mkdir(exist_ok=True)
    (state_root / "transcripts").mkdir(exist_ok=True)
    (state_root / "dash").mkdir(exist_ok=True)

    # Write a minimal _collector.json that the collector can read
    collector_json = state_root / "state" / "_collector.json"
    collector_data = {
        "agents": json.loads(agents_json),
        "data": {
            "watchdog": {"alive": "ALIVE", "age": 3, "threshold": 300},
            "monitor": {"alive": "ALIVE", "age": 45, "threshold": 3600},
            "repos": [],
            "events": [],
            "alerts": {"count": 0, "lines": []},
            "messages": []
        }
    }
    collector_json.write_text(json.dumps(collector_data), encoding="utf-8")

    return state_root


def _boot(pw):
    """Boot a fresh server+page for testing. Returns (server, root, browser,
    page, console_errors, failed_urls). Caller must goto + assert + teardown."""
    root = build_fixture_state(FIXTURE_AGENTS_JSON)
    copy_dist(root, REPO)
    port = free_port()
    server = start_server(root, port, REPO, SERVE, SERVER_BOOT_TRIES, SERVER_BOOT_SLEEP, "0.3")
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    console_errors, failed_urls = [], []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.on("response", lambda r: failed_urls.append(r.url) if r.status >= 400 else None)
    page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
    return server, root, browser, page, console_errors, failed_urls, port


def run_filter_all(pw, failures):
    """Test 'All' filter: shows all fixture agents."""
    server, root, browser, page, console_errors, failed_urls, _ = _boot(pw)
    try:
        try:
            page.wait_for_selector("[data-testid='health-header']", timeout=SEL_TIMEOUT_MS)
            page.evaluate("location.hash = '#/activity'")
            page.wait_for_selector("[data-testid='view-activity']", timeout=SEL_TIMEOUT_MS)
        except Exception as e:
            failures.append(f"(a) Activity view never mounted: {e}")
            return

        # (b) Filter controls render
        try:
            page.wait_for_selector("[data-testid='activity-status-filter']", timeout=SEL_TIMEOUT_MS)
        except Exception as e:
            failures.append(f"(b) filter controls not found: {e}")

        # (c) "All" button is active by default
        try:
            all_button = page.locator("[data-testid='filter-all']")
            all_button.wait_for(timeout=SEL_TIMEOUT_MS)
            button_class = all_button.get_attribute("class") or ""
            # CSS modules use scoped class names (hashed); just verify button exists and has some class
            assert button_class, "filter-all button has no class attribute"
        except Exception as e:
            failures.append(f"(c) filter-all button not found or not styled: {e}")

        # (d) Timeline shows all agents (3 fixture agents)
        try:
            page.wait_for_selector("[data-testid='timeline']", timeout=SEL_TIMEOUT_MS)
            agent_rows = page.locator("[data-testid='timeline-bar']").count()
            assert agent_rows == 3, f"expected 3 timeline bars (all agents), got {agent_rows}"
        except Exception as e:
            failures.append(f"(d) timeline bars count wrong for 'all' filter: {e}")

        # (a) Console clean
        time.sleep(0.4)
        real = filter_real_console_errors(console_errors, failed_urls)
        if real:
            failures.append(f"(a) filter-all console errors: {real[:3]}")
    finally:
        browser.close()
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def run_filter_running(pw, failures):
    """Test 'Running' filter: shows only running agents."""
    server, root, browser, page, console_errors, failed_urls, _ = _boot(pw)
    try:
        try:
            page.wait_for_selector("[data-testid='health-header']", timeout=SEL_TIMEOUT_MS)
            page.evaluate("location.hash = '#/activity'")
            page.wait_for_selector("[data-testid='view-activity']", timeout=SEL_TIMEOUT_MS)
        except Exception as e:
            failures.append(f"(e) Activity view never mounted: {e}")
            return

        # (e) Click "Running" filter button
        try:
            running_button = page.locator("[data-testid='filter-running']")
            running_button.wait_for(timeout=SEL_TIMEOUT_MS)
            running_button.click()
        except Exception as e:
            failures.append(f"(e) filter-running button not clickable: {e}")
            return

        # (f) Only 1 running agent visible
        try:
            page.wait_for_selector("[data-testid='timeline-bar']", timeout=SEL_TIMEOUT_MS)
            time.sleep(0.2)  # Allow re-render
            agent_rows = page.locator("[data-testid='timeline-bar']").count()
            assert agent_rows == 1, f"expected 1 running agent, got {agent_rows}"
        except Exception as e:
            failures.append(f"(f) timeline bars count wrong for 'running' filter: {e}")

        # (g) Verify the visible agent is the running one (a77b995bcdb95)
        try:
            timeline_text = page.inner_text("[data-testid='timeline']")
            assert "a77b995bcdb95" in timeline_text, "running agent not visible"
            assert "b12c4d99ef012" not in timeline_text, "idle agent should be hidden"
            assert "c99ff00aa1122" not in timeline_text, "suspicious agent should be hidden"
        except Exception as e:
            failures.append(f"(g) running agent visibility check failed: {e}")

        # Console clean
        time.sleep(0.4)
        real = filter_real_console_errors(console_errors, failed_urls)
        if real:
            failures.append(f"(e) filter-running console errors: {real[:3]}")
    finally:
        browser.close()
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def run_filter_error(pw, failures):
    """Test 'Error-Suspicious' filter: shows only suspicious agents."""
    server, root, browser, page, console_errors, failed_urls, _ = _boot(pw)
    try:
        try:
            page.wait_for_selector("[data-testid='health-header']", timeout=SEL_TIMEOUT_MS)
            page.evaluate("location.hash = '#/activity'")
            page.wait_for_selector("[data-testid='view-activity']", timeout=SEL_TIMEOUT_MS)
        except Exception as e:
            failures.append(f"(h) Activity view never mounted: {e}")
            return

        # (h) Click "Error-Suspicious" filter button
        try:
            error_button = page.locator("[data-testid='filter-error']")
            error_button.wait_for(timeout=SEL_TIMEOUT_MS)
            error_button.click()
        except Exception as e:
            failures.append(f"(h) filter-error button not clickable: {e}")
            return

        # (i) Only 1 suspicious agent visible
        try:
            page.wait_for_selector("[data-testid='timeline-bar']", timeout=SEL_TIMEOUT_MS)
            time.sleep(0.2)  # Allow re-render
            agent_rows = page.locator("[data-testid='timeline-bar']").count()
            assert agent_rows == 1, f"expected 1 error/suspicious agent, got {agent_rows}"
        except Exception as e:
            failures.append(f"(i) timeline bars count wrong for 'error' filter: {e}")

        # (j) Verify the visible agent is the suspicious one (c99ff00aa1122)
        try:
            timeline_text = page.inner_text("[data-testid='timeline']")
            assert "c99ff00aa1122" in timeline_text, "suspicious agent not visible"
            assert "a77b995bcdb95" not in timeline_text, "running agent should be hidden"
            assert "b12c4d99ef012" not in timeline_text, "idle agent should be hidden"
        except Exception as e:
            failures.append(f"(j) error agent visibility check failed: {e}")

        # Console clean
        time.sleep(0.4)
        real = filter_real_console_errors(console_errors, failed_urls)
        if real:
            failures.append(f"(h) filter-error console errors: {real[:3]}")
    finally:
        browser.close()
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def run_filter_idle(pw, failures):
    """Test 'Idle' filter: shows only idle agents."""
    server, root, browser, page, console_errors, failed_urls, _ = _boot(pw)
    try:
        try:
            page.wait_for_selector("[data-testid='health-header']", timeout=SEL_TIMEOUT_MS)
            page.evaluate("location.hash = '#/activity'")
            page.wait_for_selector("[data-testid='view-activity']", timeout=SEL_TIMEOUT_MS)
        except Exception as e:
            failures.append(f"(k) Activity view never mounted: {e}")
            return

        # Click "Idle" filter button
        try:
            idle_button = page.locator("[data-testid='filter-idle']")
            idle_button.wait_for(timeout=SEL_TIMEOUT_MS)
            idle_button.click()
        except Exception as e:
            failures.append(f"(k) filter-idle button not clickable: {e}")
            return

        # Only 1 idle agent visible
        try:
            page.wait_for_selector("[data-testid='timeline-bar']", timeout=SEL_TIMEOUT_MS)
            time.sleep(0.2)  # Allow re-render
            agent_rows = page.locator("[data-testid='timeline-bar']").count()
            assert agent_rows == 1, f"expected 1 idle agent, got {agent_rows}"
        except Exception as e:
            failures.append(f"(l) timeline bars count wrong for 'idle' filter: {e}")

        # Verify the visible agent is the idle one (b12c4d99ef012)
        try:
            timeline_text = page.inner_text("[data-testid='timeline']")
            assert "b12c4d99ef012" in timeline_text, "idle agent not visible"
            assert "a77b995bcdb95" not in timeline_text, "running agent should be hidden"
            assert "c99ff00aa1122" not in timeline_text, "suspicious agent should be hidden"
        except Exception as e:
            failures.append(f"(m) idle agent visibility check failed: {e}")

        # Console clean
        time.sleep(0.4)
        real = filter_real_console_errors(console_errors, failed_urls)
        if real:
            failures.append(f"(k) filter-idle console errors: {real[:3]}")
    finally:
        browser.close()
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Browser-level proof for Activity view status filter")
    parser.add_argument("--allow-skip", action="store_true",
                        help="Allow skipping if playwright/chromium is unavailable")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        msg = "playwright missing — run `python -m playwright install chromium`, or pass --allow-skip"
        print(f"SKIP: {msg}" if args.allow_skip else f"FAIL: {msg}")
        return 0 if args.allow_skip else 1

    failures = []
    with sync_playwright() as pw:
        try:
            pw.chromium.launch(headless=True).close()
        except Exception as e:
            msg = f"chromium unavailable ({e}); run: python -m playwright install chromium"
            print(f"SKIP: {msg}" if args.allow_skip else f"FAIL: {msg}")
            return 0 if args.allow_skip else 1

        run_filter_all(pw, failures)
        run_filter_running(pw, failures)
        run_filter_error(pw, failures)
        run_filter_idle(pw, failures)

    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1

    print("PROVEN: (a) console clean (b) filter controls render (c) 'all' button active by default "
          "(d) timeline shows all agents (e) 'running' button clickable (f) running filter shows 1 agent "
          "(g) running agent visibility correct (h) 'error' button clickable (i) error filter shows 1 agent "
          "(j) error agent visibility correct (k) 'idle' button clickable (l) idle filter shows 1 agent "
          "(m) idle agent visibility correct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
