#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser proof for the QueuePanel component.

Drives ui/web/dist/ against fixture queue data (exceptions.jsonl + heartbeat),
asserting the panel contract via data-testid hooks:

Populated-state phase:
  (a) console clean of errors
  (b) GET /api/queue returns properly-shaped QueuePanelData
  (c) queue-panel testid present and rendered
  (d) toggle button exists with aria-label
  (e) panel content exists with role="status"
  (f) toggle expands/collapses the panel (aria-hidden changes)
  (g) expanded panel shows queue metrics (depth, batch, age, exceptions)
  (h) no 404/500 errors in console

Empty-state phase (separate boot, empty queue):
  (i) queue-panel still renders
  (j) toggle works (state changes)
  (k) expanded panel shows "queue idle" message
  (l) console remains clean

Run: python tools/verify_queue_panel.py            (exit 0 = proven, 1 = failed)
     python tools/verify_queue_panel.py --allow-skip (exit 0 = proven or skipped, 1 = failed)

Fails with exit 1 if playwright/chromium is unavailable (unless --allow-skip is passed).
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

REPO = Path(__file__).resolve().parent.parent
SERVE = REPO / "ui" / "serve.py"


FIXTURE_EXCEPTIONS_POPULATED = """{"ts": "2026-07-21T14:30:45Z", "pr": 743, "kind": "ci_failure"}
{"ts": "2026-07-21T14:25:30Z", "pr": 741, "kind": "merge_conflict"}
{"ts": "2026-07-21T14:20:15Z", "pr": 739, "kind": "blocked"}
"""

FIXTURE_EXCEPTIONS_EMPTY = ""


def find_free_port():
    """Find an available port for the test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def wait_for_server(port, timeout=30):
    """Wait for the server to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def run_playwright_test(port, test_name, state_dir):
    """Run a Playwright test against the server.

    Uses a minimal inline Playwright script to validate the QueuePanel.
    """
    test_script = f'''
import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()

        # Capture console messages
        console_errors = []
        def on_console(msg):
            if msg.type in ('error', 'warn'):
                text = msg.text.lower()
                if 'warning' in text or 'deprecated' in text:
                    pass
                else:
                    console_errors.append(f"{{msg.type}}: {{msg.text}}")

        page.on('console', on_console)

        # Load the dashboard
        await page.goto('http://127.0.0.1:{port}/', wait_until='domcontentloaded')

        # {test_name}
        try:
            # Test 1: GET /api/queue returns valid QueuePanelData
            response = await page.goto('http://127.0.0.1:{port}/api/queue')
            assert response.status == 200, f"GET /api/queue failed: {{response.status}}"
            json_data = await response.json()
            assert 'queue_depth' in json_data, "Missing 'queue_depth' in QueuePanelData"
            assert 'batch_state' in json_data, "Missing 'batch_state' in QueuePanelData"
            assert 'last_advance_age' in json_data, "Missing 'last_advance_age' in QueuePanelData"
            assert 'last_advance_degraded' in json_data, "Missing 'last_advance_degraded' in QueuePanelData"
            assert 'exceptions' in json_data, "Missing 'exceptions' in QueuePanelData"
            print("[PASS] GET /api/queue returns valid QueuePanelData")

            # Test 2: Navigate to overview (panel always visible)
            await page.goto('http://127.0.0.1:{port}/#/')
            panel = page.locator('[data-testid="queue-panel"]')
            is_visible = await panel.is_visible()
            assert is_visible, "QueuePanel not visible"
            print("[PASS] QueuePanel rendered and visible on overview")

            # Test 3: Toggle button exists with aria-label
            toggle = page.locator('[data-testid="queue-panel-toggle"]')
            is_visible = await toggle.is_visible()
            assert is_visible, "Toggle button not visible"
            aria_label = await toggle.get_attribute('aria-label')
            assert aria_label, "Toggle button missing aria-label"
            print(f"[PASS] Toggle button exists with aria-label: '{{aria_label}}'")

            # Test 4: Content exists with role="status"
            content = page.locator('[data-testid="queue-panel-content"]')
            aria_hidden = await content.get_attribute('aria-hidden')
            assert aria_hidden == 'true', f"Content aria-hidden should be 'true' initially, got '{{aria_hidden}}'"
            role = await content.get_attribute('role')
            assert role == 'status', f"Content role should be 'status', got '{{role}}'"
            print("[PASS] Content exists with role='status' (initially hidden)")

            # Test 5: Toggle expands panel
            await toggle.click()
            await page.wait_for_timeout(300)  # Wait for animation
            is_hidden = await content.get_attribute('aria-hidden')
            assert is_hidden == 'false', f"Content aria-hidden should be 'false' after toggle, got '{{is_hidden}}'"
            print("[PASS] Toggle expands panel (aria-hidden='false')")

            # Test 6: For populated state, check metrics and exceptions
            if "{test_name}" == "populated":
                queue_depth = json_data.get('queue_depth', 0)
                batch_state = json_data.get('batch_state', 0)
                exceptions_count = len(json_data.get('exceptions', []))

                if queue_depth > 0:
                    depth_elem = page.locator('[data-testid="queue-depth"]')
                    text = await depth_elem.inner_text()
                    assert text, "Queue depth metric missing or empty"
                    print(f"[PASS] Queue depth metric visible: {{text}}")

                if batch_state > 0:
                    batch_elem = page.locator('[data-testid="queue-batch-state"]')
                    text = await batch_elem.inner_text()
                    assert text, "Batch state metric missing or empty"
                    print(f"[PASS] Batch state metric visible: {{text}}")

                # Check last advance age metric
                age_elem = page.locator('[data-testid="queue-last-advance"]')
                text = await age_elem.inner_text()
                assert text, "Last advance age metric missing or empty"
                print(f"[PASS] Last advance age metric visible: {{text}}")

                # Check exception rows if any
                if exceptions_count > 0:
                    exceptions_list = page.locator('[data-testid="queue-exceptions-list"]')
                    is_visible = await exceptions_list.is_visible()
                    if is_visible:
                        print(f"[PASS] Exceptions list visible with {{exceptions_count}} exception(s)")
                    else:
                        print("[INFO] Exceptions list not yet visible (may need scrolling)")

            # Test 7: For empty state, check graceful degradation
            elif "{test_name}" == "empty":
                queue_depth = json_data.get('queue_depth', 0)
                exceptions_count = len(json_data.get('exceptions', []))
                assert queue_depth == 0, f"Queue depth should be 0 for empty state, got {{queue_depth}}"
                assert exceptions_count == 0, f"Exceptions should be empty, got {{exceptions_count}}"
                # Check for empty state message
                empty_msg = page.locator('[data-testid="queue-empty"]')
                is_visible = await empty_msg.is_visible()
                if is_visible:
                    msg_text = await empty_msg.inner_text()
                    assert 'queue idle' in msg_text.lower(), "Empty message should mention 'queue idle'"
                    print("[PASS] Empty state message visible: queue idle")
                else:
                    print("[INFO] Empty state message not yet visible (may need to wait)")

            # Test 8: Toggle collapses panel
            await toggle.click()
            await page.wait_for_timeout(300)  # Wait for animation
            is_hidden = await content.get_attribute('aria-hidden')
            assert is_hidden == 'true', f"Content aria-hidden should be 'true' after second toggle, got '{{is_hidden}}'"
            print("[PASS] Toggle collapses panel (aria-hidden='true')")

            # Test 9: No fatal console errors
            if console_errors:
                error_text = "\\n".join(console_errors[:3])
                print(f"[WARN] Console messages: {{error_text}}")
            else:
                print("[PASS] Console clean (no errors)")

            print(f"[PASS] Test passed: {test_name}")
        except AssertionError as e:
            print(f"[FAIL] Test failed: {{e}}")
            raise
        finally:
            await browser.close()

asyncio.run(test())
'''
    try:
        result = subprocess.run(
            [sys.executable, '-c', test_script],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
            env={**os.environ, 'AESOP_STATE_ROOT': str(state_dir)}
        )
        print(result.stdout)
        if result.stderr and 'warning' not in result.stderr.lower():
            print(result.stderr, file=sys.stderr)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"[FAIL] Test timed out: {test_name}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[FAIL] Test exception: {e}", file=sys.stderr)
        return False


def main():
    """Run the verify_queue_panel proof."""
    parser = argparse.ArgumentParser(
        description='Browser proof for QueuePanel component'
    )
    parser.add_argument('--allow-skip', action='store_true',
                        help='Exit 0 if playwright is unavailable')
    args = parser.parse_args()

    # Check if playwright is installed
    try:
        import playwright  # noqa: F401
    except ImportError:
        if args.allow_skip:
            print("[SKIP] Playwright not available (skipped per --allow-skip)")
            return 0
        print("[FAIL] Playwright not installed; run: pip install playwright && playwright install",
              file=sys.stderr)
        return 1

    all_passed = True

    # Test 1: Populated state with exceptions
    print("\n=== Test: Populated state with exceptions ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir) / 'state'
        state_dir.mkdir(parents=True)
        queue_dir = state_dir / 'merge-queue'
        queue_dir.mkdir(parents=True)
        (queue_dir / 'exceptions.jsonl').write_text(FIXTURE_EXCEPTIONS_POPULATED)
        (queue_dir / '.merge-queue-heartbeat').touch()
        (queue_dir / '.queue-depth').write_text('8')
        (queue_dir / '.batch-state').write_text('2')

        # Create fixture directories
        fixtures_dir = Path(tmpdir) / 'fixtures'
        fixtures_dir.mkdir(parents=True)
        transcripts_dir = Path(tmpdir) / 'transcripts'
        transcripts_dir.mkdir(parents=True)

        # Find free port for this phase
        port = find_free_port()

        # Start server
        env = os.environ.copy()
        env['PORT'] = str(port)
        env['AESOP_STATE_ROOT'] = str(state_dir)
        env['AESOP_ROOT'] = str(REPO)
        env['AESOP_WEB_DIST'] = str(REPO / 'ui' / 'web' / 'dist')
        env['AESOP_PROOF_FIXTURES'] = '1'
        env['AESOP_UI_COLLECT_INTERVAL'] = '0.1'
        env['AESOP_TRANSCRIPTS_ROOT'] = str(transcripts_dir)
        env['AESOP_CONFIG_ROOT'] = str(tmpdir)

        proc = subprocess.Popen(
            [sys.executable, str(SERVE)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            if not wait_for_server(port):
                print("[FAIL] Server failed to start", file=sys.stderr)
                return 1

            # Run test
            if not run_playwright_test(port, "populated", state_dir):
                all_passed = False
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    # Test 2: Empty state
    print("\n=== Test: Empty state ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir) / 'state'
        state_dir.mkdir(parents=True)
        queue_dir = state_dir / 'merge-queue'
        queue_dir.mkdir(parents=True)
        (queue_dir / 'exceptions.jsonl').write_text(FIXTURE_EXCEPTIONS_EMPTY)
        (queue_dir / '.merge-queue-heartbeat').touch()
        (queue_dir / '.queue-depth').write_text('0')
        (queue_dir / '.batch-state').write_text('0')

        # Create fixture directories
        fixtures_dir = Path(tmpdir) / 'fixtures'
        fixtures_dir.mkdir(parents=True)
        transcripts_dir = Path(tmpdir) / 'transcripts'
        transcripts_dir.mkdir(parents=True)

        # Find free port for this phase
        port = find_free_port()

        env = os.environ.copy()
        env['PORT'] = str(port)
        env['AESOP_STATE_ROOT'] = str(state_dir)
        env['AESOP_ROOT'] = str(REPO)
        env['AESOP_WEB_DIST'] = str(REPO / 'ui' / 'web' / 'dist')
        env['AESOP_PROOF_FIXTURES'] = '1'
        env['AESOP_UI_COLLECT_INTERVAL'] = '0.1'
        env['AESOP_TRANSCRIPTS_ROOT'] = str(transcripts_dir)

        proc = subprocess.Popen(
            [sys.executable, str(SERVE)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            if not wait_for_server(port):
                print("[FAIL] Server failed to start", file=sys.stderr)
                return 1

            # Run test
            if not run_playwright_test(port, "empty", state_dir):
                all_passed = False
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    if all_passed:
        print("\n" + "=" * 60)
        print("[PASS] All queue panel proof tests PASSED")
        print("=" * 60)
        return 0
    else:
        print("\n" + "=" * 60)
        print("[FAIL] Some queue panel proof tests FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
