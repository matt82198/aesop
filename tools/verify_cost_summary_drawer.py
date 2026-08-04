#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser proof for the CostSummaryDrawer component.
INDEX: Playwright proof for CostSummaryDrawer: toggle, metrics, aria-hidden, populated/empty phases; both exit 0=proven/1=failed, `[--allow-skip]`.

Drives ui/web/dist/ against fixture cost data (ledger + pricing), asserting
the drawer contract via data-testid hooks:

Populated-state phase:
  (a) console clean of errors
  (b) GET /api/cost returns properly-shaped CostSummary
  (c) cost-summary-drawer testid present and rendered
  (d) toggle button exists with aria-label
  (e) drawer panel exists with role="status"
  (f) toggle expands/collapses the panel (aria-hidden changes)
  (g) expanded panel shows cost metrics (total, rate, model-mix)
  (h) no 404/500 errors in console

Empty-state phase (separate boot, empty ledger):
  (i) cost-summary-drawer still renders
  (j) toggle works (state changes)
  (k) expanded panel shows "No data yet" message
  (l) console remains clean

Falsifiability contract: EVERY claim enumerated above is an `assert`, so a
violation propagates to a non-zero exit. Nothing here is print-only -- a
`print("[PASS] ...")` line always sits AFTER the assert that earns it. Breaking
any one of the assertions locally must turn the run red; if a claim cannot be
asserted it must be deleted from this docstring rather than downgraded to a
[WARN]/[INFO] print.

Run: python tools/verify_cost_summary_drawer.py            (exit 0 = proven, 1 = failed)
     python tools/verify_cost_summary_drawer.py --allow-skip (exit 0 = proven or skipped, 1 = failed)

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


FIXTURE_LEDGER_POPULATED = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-13T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 12000 | 3500 | OK |
| 2026-07-13T14:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 14000 | 4200 | OK |
| 2026-07-13T14:10:00Z | haiku | claude-haiku-4-5-20251001 | 40 | 11000 | 3200 | FAILED |
| 2026-07-13T14:15:00Z | haiku | claude-haiku-4-5-20251001 | 55 | 13500 | 4000 | OK |
| 2026-07-13T14:20:00Z | sonnet | claude-sonnet-4-5-20250929 | 85 | 28000 | 8100 | OK |
| 2026-07-13T14:25:00Z | sonnet | claude-sonnet-4-5-20250929 | 90 | 30000 | 9000 | OK |
| 2026-07-13T14:30:00Z | orchestrator | claude-opus-4-20250805 | 120 | 50000 | 12000 | OK |
| 2026-07-14T08:00:00Z | haiku | claude-haiku-4-5-20251001 | 40 | 10000 | 2800 | OK |
| 2026-07-14T08:30:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 11000 | 3200 | OK |
| 2026-07-14T09:00:00Z | sonnet | claude-sonnet-4-5-20250929 | 80 | 26000 | 7800 | OK |
"""

FIXTURE_PRICING = {
  "pricing": {
    "claude-haiku-4-5-20251001": {
      "input_per_mtok": 0.80,
      "output_per_mtok": 4.0
    },
    "claude-sonnet-4-5-20250929": {
      "input_per_mtok": 3.0,
      "output_per_mtok": 15.0
    },
    "claude-opus-4-20250805": {
      "input_per_mtok": 15.0,
      "output_per_mtok": 75.0
    }
  }
}

FIXTURE_LEDGER_EMPTY = ""


def find_free_port():
    """Find an available port for the test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def wait_for_server(port, timeout=30, proc=None):
    """Wait for the server to be ready.

    If `proc` exits before the port opens, stop waiting immediately and report
    the failure instead of burning the whole timeout on a dead server.
    """
    start = time.time()
    while time.time() - start < timeout:
        if proc is not None and proc.poll() is not None:
            print(f"[FAIL] Server exited early with code {proc.returncode}", file=sys.stderr)
            return False
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def run_playwright_test(port, test_name, state_dir):
    """Run a Playwright test against the server.

    Uses a minimal inline Playwright script to validate the cost summary drawer.
    """
    test_script = f'''
import asyncio
import re
from playwright.async_api import async_playwright

# Console messages dropped as known, non-actionable noise. Kept as an explicit
# allowlist of FULL-STRING regexes -- the previous substring filter ("warning" or
# "deprecated" anywhere in the text) silently swallowed real failures such as
# "Uncaught TypeError: warning banner is undefined".
CONSOLE_ALLOWLIST = [
    re.compile(r'^Download the React DevTools.*', re.I),
]


def is_benign(text):
    return any(rx.match(text) for rx in CONSOLE_ALLOWLIST)


async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()

        # Capture console errors/warnings and failed responses. Both are ASSERTED
        # below; neither is print-only.
        console_errors = []
        bad_responses = []

        def on_console(msg):
            if msg.type in ('error', 'warning', 'warn') and not is_benign(msg.text):
                console_errors.append(f"{{msg.type}}: {{msg.text}}")

        def on_response(resp):
            if resp.status >= 400:
                bad_responses.append(f"{{resp.status}} {{resp.url}}")

        def on_pageerror(exc):
            console_errors.append(f"pageerror: {{exc}}")

        page.on('console', on_console)
        page.on('response', on_response)
        page.on('pageerror', on_pageerror)

        # {test_name}
        try:
            # Load the dashboard once and stay on it, so every console message
            # and response below belongs to the app under test.
            await page.goto('http://127.0.0.1:{port}/#/', wait_until='domcontentloaded')

            # Test 1: GET /api/cost returns valid CostSummary. Fetched out-of-band
            # so the app page is never navigated away from.
            response = await context.request.get('http://127.0.0.1:{port}/api/cost')
            assert response.status == 200, f"GET /api/cost failed: {{response.status}}"
            json_data = await response.json()
            for key in ('models', 'daily_totals', 'overall_scorecard', 'has_pricing'):
                assert key in json_data, f"Missing '{{key}}' in CostSummary"
            print("[PASS] GET /api/cost returns valid CostSummary")

            # Test 2: drawer renders on the overview (auto-waiting, not an
            # instantaneous is_visible() snapshot).
            drawer = page.locator('[data-testid="cost-summary-drawer"]')
            await drawer.wait_for(state='visible', timeout=15000)
            print("[PASS] CostSummaryDrawer rendered and visible on overview")

            # Test 3: Toggle button exists with aria-label
            toggle = page.locator('[data-testid="cost-summary-drawer-toggle"]')
            await toggle.wait_for(state='visible', timeout=15000)
            aria_label = await toggle.get_attribute('aria-label')
            assert aria_label, "Toggle button missing aria-label"
            print(f"[PASS] Toggle button exists with aria-label: '{{aria_label}}'")

            # Test 4: Panel exists with role="status" and starts collapsed
            panel = page.locator('[data-testid="cost-summary-drawer-panel"]')
            await panel.wait_for(state='attached', timeout=15000)
            role = await panel.get_attribute('role')
            assert role == 'status', f"Panel role should be 'status', got '{{role}}'"
            start_hidden = await panel.get_attribute('aria-hidden')
            assert start_hidden == 'true', f"Panel should start collapsed, aria-hidden='{{start_hidden}}'"
            assert not await panel.is_visible(), "Collapsed panel must not be visible"
            print("[PASS] Panel exists with role='status' (initially collapsed)")

            # Test 5: Toggle expands panel
            await toggle.click()
            expanded = await panel.get_attribute('aria-hidden')
            assert expanded == 'false', f"Panel aria-hidden should be 'false' after toggle, got '{{expanded}}'"
            print("[PASS] Toggle expands panel (aria-hidden='false')")

            # Test 6: For populated state, check metrics
            if "{test_name}" == "populated":
                assert len(json_data.get('models', {{}})) > 0, "Populated ledger produced no models"
                assert len(json_data.get('daily_totals', {{}})) > 0, "Populated ledger produced no daily totals"
                print(f"[PASS] Ledger aggregated: {{len(json_data['models'])}} model(s), "
                      f"{{len(json_data['daily_totals'])}} day(s)")

                # Total spend metric. wait_for covers the window between mount and
                # the first SSE cost payload (the drawer is in its connection state
                # until then).
                total = page.locator('[data-testid="cost-summary-total"]')
                await total.wait_for(state='visible', timeout=15000)
                text = (await total.inner_text()).strip()
                assert text, "Total spend metric missing or empty"
                print(f"[PASS] Total spend metric visible: {{text!r}}")

                # Spend rate metric
                rate = page.locator('[data-testid="cost-summary-rate"]')
                await rate.wait_for(state='visible', timeout=15000)
                text = (await rate.inner_text()).strip()
                assert text, "Spend rate metric missing or empty"
                print(f"[PASS] Spend rate metric visible: {{text!r}}")

                # Model-mix shares must be real proportions: each in (0, 100] and
                # summing to no more than 100. The pre-fix denominator (all-time
                # model tokens / latest-day tokens x model count) routinely
                # exceeded 100% per model and saturated every bar.
                rows = page.locator('[data-testid="cost-summary-model-row"]')
                row_count = await rows.count()
                assert row_count > 0, "Model-mix breakdown rendered no rows"
                shares = []
                for i in range(row_count):
                    label = (await rows.nth(i).inner_text()).strip()
                    m = re.search(r'([0-9]+(?:\\.[0-9]+)?)%', label)
                    assert m, f"Model row {{i}} has no percentage: {{label!r}}"
                    shares.append(float(m.group(1)))
                for pct in shares:
                    assert 0 < pct <= 100, f"Model share out of range: {{pct}}% (shares={{shares}})"
                assert sum(shares) <= 100.5, f"Model shares sum above 100%: {{shares}}"
                print(f"[PASS] Model-mix shares are real proportions: {{shares}} (sum={{sum(shares):.1f}}%)")

            # Test 7: For empty state, check graceful degradation
            elif "{test_name}" == "empty":
                # Non-vacuous: an empty ledger must produce zero models AND zero
                # runs. (The old `has_pricing is False or len(models) == 0` was an
                # `or` whose second disjunct was trivially true.)
                assert json_data.get('models') == {{}}, f"Empty ledger produced models: {{json_data.get('models')}}"
                assert json_data['overall_scorecard']['total_runs'] == 0, \\
                    f"Empty ledger produced runs: {{json_data['overall_scorecard']['total_runs']}}"

                # Empty-state message must actually appear in the expanded panel.
                empty_msg = page.locator('[data-testid="cost-summary-empty"]')
                await empty_msg.wait_for(state='visible', timeout=15000)
                text = (await empty_msg.inner_text()).strip()
                assert 'No data yet' in text, f"Empty state message wrong: {{text!r}}"
                print(f"[PASS] Empty state message visible: {{text.splitlines()[0]!r}}")

            # Test 8: Toggle collapses panel
            await toggle.click()
            collapsed = await panel.get_attribute('aria-hidden')
            assert collapsed == 'true', f"Panel aria-hidden should be 'true' after second toggle, got '{{collapsed}}'"
            await panel.wait_for(state='hidden', timeout=5000)
            print("[PASS] Toggle collapses panel (aria-hidden='true')")

            # Test 9: console clean + no 4xx/5xx responses. ASSERTED, not printed.
            assert not console_errors, \\
                "Console not clean: " + " | ".join(console_errors[:5])
            print("[PASS] Console clean (no errors or warnings)")
            assert not bad_responses, \\
                "Failed HTTP responses: " + " | ".join(bad_responses[:5])
            print("[PASS] No 4xx/5xx responses")

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
            timeout=120,
            env={**os.environ, 'AESOP_STATE_ROOT': str(state_dir)}
        )
        print(result.stdout)
        # Always surface stderr. The old `'warning' not in stderr` guard hid the
        # traceback of any failure whose message happened to contain "warning".
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"[FAIL] Test timed out: {test_name}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[FAIL] Test exception: {e}", file=sys.stderr)
        return False


def main():
    """Run the verify_cost_summary_drawer proof."""
    parser = argparse.ArgumentParser(
        description='Browser proof for CostSummaryDrawer component'
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

    # Test 1: Populated state with pricing
    print("\n=== Test: Populated state with pricing ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir) / 'state'
        state_dir.mkdir(parents=True)
        ledger_dir = state_dir / 'ledger'
        ledger_dir.mkdir(parents=True)
        (ledger_dir / 'OUTCOMES-LEDGER.md').write_text(FIXTURE_LEDGER_POPULATED)

        # Write pricing config
        config_file = Path(tmpdir) / 'aesop.config.json'
        config_file.write_text(json.dumps(FIXTURE_PRICING))

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
            if not wait_for_server(port, proc=proc):
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
        ledger_dir = state_dir / 'ledger'
        ledger_dir.mkdir(parents=True)
        (ledger_dir / 'OUTCOMES-LEDGER.md').write_text(FIXTURE_LEDGER_EMPTY)

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
        # Hermetic: without this the empty phase inherits the ambient config root
        # and can read the developer's real aesop.config.json pricing, so the
        # phase would not be testing a known state.
        env['AESOP_CONFIG_ROOT'] = str(tmpdir)

        proc = subprocess.Popen(
            [sys.executable, str(SERVE)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            if not wait_for_server(port, proc=proc):
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
        print("[PASS] All cost summary drawer proof tests PASSED")
        print("=" * 60)
        return 0
    else:
        print("\n" + "=" * 60)
        print("[FAIL] Some cost summary drawer proof tests FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
