"""Shared Playwright harness boilerplate for verify_*.py tools.
INDEX: Shared Playwright harness boilerplate: `free_port()`, `copy_dist()`, `start_server()`, `stop_server()`, `filter_real_console_errors()` extracted from verify_*.py to reduce duplication (module for import, not CLI); browser proofs import from this module instead of reimplementing

Extracted common functions used by verify_dash.py and verify_dispatch_panel.py:
  - free_port(): Find an available local port
  - copy_dist(): Copy built React dist to temp root
  - start_server(): Start ui/serve.py with fixture environment
  - stop_server(): Terminate server process cleanly

Each verify tool defines REPO and SERVE constants:
  REPO = Path(__file__).resolve().parent.parent
  SERVE = REPO / "ui" / "serve.py"

Then calls these helpers to manage test lifecycle. Reduces boilerplate duplication
and ensures consistent timeout/retry behavior across all verify tools.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


def free_port():
    """Find an available local port by binding to 127.0.0.1:0.

    Returns:
        int: An available port number.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def copy_dist(root: Path, repo: Path):
    """Copy built React dist from repo/ui/web/dist to root/ui/web/dist.

    Args:
        root: Destination root directory (temp dir for test).
        repo: Repository root containing ui/web/dist.
    """
    real_dist = repo / "ui" / "web" / "dist"
    if real_dist.is_dir():
        shutil.copytree(real_dist, root / "ui" / "web" / "dist")


def start_server(root: Path, port: int, repo: Path, serve_script: Path,
                 boot_tries: int = 50, boot_sleep: float = 0.2,
                 collect_interval: str = "0.3"):
    """Start ui/serve.py with fixture environment and wait for readiness.

    Args:
        root: Temp root directory with fixture state.
        port: Port to serve on.
        repo: Repository root path.
        serve_script: Path to ui/serve.py.
        boot_tries: Number of connection attempts before giving up.
        boot_sleep: Sleep duration (seconds) between attempts.
        collect_interval: AESOP_UI_COLLECT_INTERVAL value (default 0.3).

    Returns:
        subprocess.Popen: Running server process (caller must call stop_server).

    Raises:
        RuntimeError: If state dir resolves to real repo state, or server fails to start.
    """
    state_root = root / "state"
    real_state = Path.home() / "aesop" / "state"
    if state_root.resolve() == real_state.resolve():
        raise RuntimeError("state dir resolved to real repo state (~aesop/state)")

    env = dict(os.environ,
               AESOP_ROOT=str(root),
               AESOP_STATE_ROOT=str(state_root),
               AESOP_TRANSCRIPTS_ROOT=str(root / "transcripts"),
               AESOP_WEB_DIST=str(repo / "ui" / "web" / "dist"),
               AESOP_PROOF_FIXTURES="1",
               AESOP_UI_COLLECT_INTERVAL=collect_interval,
               PORT=str(port))
    server = subprocess.Popen([sys.executable, str(serve_script)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(boot_tries):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return server
        except OSError:
            time.sleep(boot_sleep)
    server.kill()
    raise RuntimeError("server never came up")


def stop_server(server):
    """Terminate server process cleanly (SIGTERM, then SIGKILL if needed).

    Args:
        server: subprocess.Popen object returned by start_server.
    """
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()


def filter_real_console_errors(console_errors, failed_urls):
    """Drop favicon/urlless-resource noise; surface real broken assets.

    Filters console error messages to remove common benign messages (favicon,
    generic "Failed to load resource" when only favicon failed) so proofs can
    focus on real errors. Returns a deduplicated list of genuinely broken assets.

    Args:
        console_errors: List of console error messages captured by Playwright.
        failed_urls: List of URLs with HTTP status >= 400.

    Returns:
        List of filtered error messages, with real failed URLs appended.
    """
    non_favicon = [u for u in failed_urls if "favicon" not in u.lower()]
    real = []
    for e in console_errors:
        low = e.lower()
        if "favicon" in low:
            continue
        if "failed to load resource" in low and not non_favicon:
            continue
        real.append(e)
    real.extend(f"failed resource: {u}" for u in non_favicon)
    return real
