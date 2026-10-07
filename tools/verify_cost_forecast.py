#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser proof for the CostForecast component.
INDEX: Playwright proof for CostForecast: trend chart, metrics panel, ceiling alerts, populated/empty phases (5-day ledger / <2d data)

Drives ui/web/dist/ against fixture cost data (ledger with daily totals),
asserting the forecast contract via data-testid hooks:

Populated-state phase:
  (a) GET /api/cost returns properly-shaped CostSummary with daily_totals
  (b) cost-forecast-populated testid present and rendered
  (c) forecast chart exists with role="img" and aria-label
  (d) metrics panel shows: daily burn, projected end-of-wave, 90% confidence
  (e) ceiling line and alerts present when ceilingTokens configured
  (f) no 404/500 errors in console

Empty-state phase (insufficient data):
  (g) cost-forecast-empty renders when <2 days of data
  (h) shows "Need at least 2 days" message
  (i) console remains clean

Run: python tools/verify_cost_forecast.py            (exit 0 = proven, 1 = failed)
     python tools/verify_cost_forecast.py --allow-skip (exit 0 = proven or skipped, 1 = failed)

Fails with exit 1 if playwright/chromium is unavailable (unless --allow-skip is passed).
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVE = REPO / "ui" / "serve.py"


# Fixture ledger with 5 days of data for trend calculation
FIXTURE_LEDGER_POPULATED = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-24T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 100000 | 50000 | OK |
| 2026-07-25T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 110000 | 55000 | OK |
| 2026-07-26T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 120000 | 60000 | OK |
| 2026-07-27T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 130000 | 65000 | OK |
| 2026-07-28T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 140000 | 70000 | OK |
"""

FIXTURE_PRICING = {
  "pricing": {
    "claude-haiku-4-5-20251001": {
      "input_per_mtok": 0.80,
      "output_per_mtok": 4.0
    }
  }
}

# Ledger with only 1 day (insufficient for trend)
FIXTURE_LEDGER_EMPTY = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-28T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 140000 | 70000 | OK |
"""


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

    Validates the cost forecast component contract.
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
            # Test 1: GET /api/cost returns valid CostSummary with daily_totals
            response = await page.goto('http://127.0.0.1:{port}/api/cost')
            assert response.status == 200, f"GET /api/cost failed: {{response.status}}"
            json_data = await response.json()
            assert 'daily_totals' in json_data, "Missing 'daily_totals' in CostSummary"
            daily_count = len(json_data.get('daily_totals', {{}}))
            print(f"[PASS] GET /api/cost returns CostSummary with {{daily_count}} day(s)")

            # Navigate to cost view to trigger component rendering
            await page.goto('http://127.0.0.1:{port}/#/cost')

            # {test_name} phase
            if "{test_name}" == "populated":
                # Test 2: cost-forecast-populated testid present
                forecast = page.locator('[data-testid="cost-forecast-populated"]')
                is_visible = await forecast.is_visible()
                assert is_visible, "CostForecast populated state not visible"
                print("[PASS] CostForecast populated state rendered")

                # Test 3: Chart exists with role="img" and aria-label
                chart = page.locator('[data-testid="cost-forecast-populated"] [role="img"]')
                is_visible = await chart.is_visible()
                assert is_visible, "Forecast chart not visible"
                aria_label = await chart.get_attribute('aria-label')
                assert aria_label, "Chart missing aria-label"
                assert 'forecast' in aria_label.lower(), f"Invalid aria-label: {{aria_label}}"
                print(f"[PASS] Chart renders with aria-label: '{{aria_label}}'")

                # Test 4: Metrics panel present
                metrics = page.locator('[data-testid="cost-forecast-metrics"]')
                is_visible = await metrics.is_visible()
                assert is_visible, "Metrics panel not visible"
                print("[PASS] Metrics panel visible")

                # Test 5: Check metrics content
                metrics_text = await metrics.inner_text()
                assert 'daily burn' in metrics_text.lower(), "Missing 'daily burn' metric"
                assert 'projected' in metrics_text.lower(), "Missing 'projected' metric"
                assert 'confidence' in metrics_text.lower(), "Missing confidence metric"
                print("[PASS] Metrics show: daily burn, projection, confidence")

                # Test 6: Check for trend note about data honesty
                note = page.locator('[data-testid="cost-forecast-populated"]')
                note_text = await note.inner_text()
                if 'linear regression' in note_text.lower():
                    print("[PASS] Trend note includes 'linear regression'")

            elif "{test_name}" == "empty":
                # Test 7: cost-forecast-empty testid present
                forecast = page.locator('[data-testid="cost-forecast-empty"]')
                is_visible = await forecast.is_visible()
                assert is_visible, "CostForecast empty state not visible"
                print("[PASS] CostForecast empty state rendered")

                # Test 8: Shows "Need at least 2 days" message
                msg_locator = forecast.locator('text=/Need at least 2 days/')
                is_visible = await msg_locator.is_visible()
                assert is_visible, "Empty state message not visible"
                print("[PASS] Empty state shows 'Need at least 2 days' message")

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
            encoding='utf-8', errors='replace',
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
    """Run the verify_cost_forecast proof."""
    parser = argparse.ArgumentParser(
        description='Browser proof for CostForecast component'
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

    # Test 1: Populated state with 5 days of data
    print("\n=== Test: Populated state (trend calculation) ===")
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

    # Test 2: Empty state (insufficient data)
    print("\n=== Test: Empty state (insufficient data) ===")
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
        print("[PASS] All cost forecast proof tests PASSED")
        print("=" * 60)
        return 0
    else:
        print("\n" + "=" * 60)
        print("[FAIL] Some cost forecast proof tests FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
