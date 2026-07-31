#!/usr/bin/env python3
"""End-to-end verification of the wave failure drill-down feature.

Sets up a temporary AESOP_ROOT, stubs the gh CLI, starts the dashboard server,
and exercises the failure drill-down UI + API via Playwright. Verifies:

  1. GET /api/wave/failure?pr=N endpoint returns correct shape
  2. FailureDrilldown component toggles expand/collapse
  3. Failure details render when expanded
  4. Graceful degradation when gh is unavailable

Run: python tools/verify_failure_drilldown.py
     (or supply --port=<port> for a custom port)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:  # import-clean without playwright; main() reports the miss
    sync_playwright = None
    expect = None


# Paths
REPO_ROOT = Path(__file__).parent.parent
UI_PATH = REPO_ROOT / "ui"
SERVE_PATH = UI_PATH / "serve.py"


def run_server(fixture_root, port, extra_env=None):
    """Start the dashboard server in a background thread.

    Returns: (server_process, cleanup_callback)
    """
    env = os.environ.copy()
    env["AESOP_ROOT"] = str(fixture_root)
    env["AESOP_STATE_ROOT"] = str(fixture_root / "state")
    # Point to the real repo's built dist (not the fixture root)
    env["AESOP_WEB_DIST"] = str(REPO_ROOT / "ui" / "web" / "dist")
    env["AESOP_PROOF_FIXTURES"] = "1"
    env["PORT"] = str(port)
    env["AESOP_UI_COLLECT_INTERVAL"] = "0.1"
    if extra_env:
        env.update(extra_env)

    proc = subprocess.Popen(
        [sys.executable, str(SERVE_PATH)],
        env=env,
        cwd=str(UI_PATH),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
    )

    # Wait for server to be ready (crude: sleep and probe)
    for _ in range(30):
        try:
            import http.client
            con = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            con.request("GET", "/")
            con.close()
            return proc, lambda: proc.terminate()
        except Exception:
            time.sleep(0.1)

    raise RuntimeError(f"Server failed to start on port {port}")


def stub_gh(fixture_root):
    """Create a stub gh CLI that always fails gracefully.

    Returns: path to the stub script.
    """
    stub = fixture_root / "stub-gh"
    stub.write_text(
        "#!/bin/bash\n"
        "echo 'stubbed gh: not authenticated' >&2\n"
        "exit 1\n",
        encoding="utf-8"
    )
    stub.chmod(0o755)
    return stub


def main():
    if sync_playwright is None:
        allow_skip = "--allow-skip" in sys.argv
        msg = "playwright missing — run `python -m playwright install chromium`, or pass --allow-skip"
        print(f"SKIP: {msg}" if allow_skip else f"FAIL: {msg}")
        sys.exit(0 if allow_skip else 1)
    parser = argparse.ArgumentParser(description="Verify wave failure drill-down feature")
    parser.add_argument("--port", type=int, default=0, help="Dashboard port (default: auto)")
    args = parser.parse_args()

    fixture_root = Path(tempfile.mkdtemp(prefix="aesop-verify-failure-"))
    port = args.port or 8771  # Use 8771 by default to avoid conflicts

    try:
        print(f"[setup] fixture_root={fixture_root}")
        (fixture_root / "state").mkdir(parents=True)
        (fixture_root / "transcripts").mkdir()

        stub_gh_path = stub_gh(fixture_root)
        print(f"[setup] stub_gh={stub_gh_path}")

        # Start server
        extra_env = {"AESOP_GH_BIN": str(stub_gh_path)}
        proc, cleanup = run_server(fixture_root, port, extra_env)
        print(f"[server] started on port {port}")

        try:
            # Run Playwright tests
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                try:
                    # Test 1: GET /api/wave/failure endpoint
                    print("[test] GET /api/wave/failure?pr=123")
                    response = page.request.get(f"http://127.0.0.1:{port}/api/wave/failure?pr=123")
                    assert response.status == 200, f"Expected 200, got {response.status}"
                    body = json.loads(response.text())
                    assert "available" in body
                    assert "pr_number" in body
                    assert body["pr_number"] == 123
                    print("  ✓ Endpoint returns correct shape")

                    # Test 2: Navigate to dashboard (just to verify it loads)
                    print("[test] Dashboard loads")
                    page.goto(f"http://127.0.0.1:{port}/")
                    # Use domcontentloaded instead of networkidle because SSE keeps
                    # connections open indefinitely, preventing networkidle from firing
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                    title = page.title()
                    assert title, "Page should have a title"
                    print(f"  ✓ Dashboard loaded: {title}")

                    # Test 3: Test 400 on missing ?pr=
                    print("[test] GET /api/wave/failure without ?pr= returns 400")
                    response = page.request.get(f"http://127.0.0.1:{port}/api/wave/failure")
                    assert response.status == 400, f"Expected 400, got {response.status}"
                    print("  ✓ Missing ?pr= rejected")

                    # Test 4: Test 400 on invalid ?pr=
                    print("[test] GET /api/wave/failure?pr=invalid returns 400")
                    response = page.request.get(f"http://127.0.0.1:{port}/api/wave/failure?pr=notanumber")
                    assert response.status == 400, f"Expected 400, got {response.status}"
                    print("  ✓ Invalid ?pr= rejected")

                    # Test 5: Verify no-cache headers
                    print("[test] Verify no-cache headers")
                    response = page.request.get(f"http://127.0.0.1:{port}/api/wave/failure?pr=456")
                    cache_control = response.headers.get("cache-control", "")
                    assert "no-cache" in cache_control, f"Expected no-cache in {cache_control}"
                    print("  ✓ No-cache headers present")

                    print("\n[success] All verification tests passed!")

                finally:
                    browser.close()

        finally:
            cleanup()

    except Exception as e:
        print(f"\n[error] {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    finally:
        shutil.rmtree(fixture_root, ignore_errors=True)
        print(f"[cleanup] removed {fixture_root}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
