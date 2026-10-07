#!/usr/bin/env python3
"""Build a static, self-contained snapshot of the Aesop dashboard with demo data.
INDEX: Build a static, self-contained snapshot of the dashboard with demo data for GitHub Pages; starts demo server, captures API state, produces _site/ with fetch/EventSource shim; CLI: `--output DIR`

Produces a deployable directory (_site/) suitable for GitHub Pages:
  - Starts the dashboard server in --demo mode on an ephemeral port
  - Captures /api/state and auxiliary API snapshots
  - Copies ui/web/dist/ to _site/
  - Inlines a shim that patches fetch/EventSource to serve embedded demo data
  - Fixes asset paths for project-page hosting (relative, not root-absolute)

Usage:
  python tools/build_static_dash.py [--output DIR]

No external dependencies (stdlib only).
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "ui" / "web" / "dist"
DEFAULT_OUTPUT = REPO_ROOT / "_site"

# Maximum seconds to wait for the demo server to start.
SERVER_START_TIMEOUT = 30
# How often to poll the server during startup.
SERVER_POLL_INTERVAL = 0.5

# API endpoints to capture for the static build.  Each tuple is
# (url_path, js_global_name).  The captured JSON is embedded in the HTML
# as window[js_global_name] so the fetch shim can serve it.
_CAPTURE_ENDPOINTS = [
    ("/api/state", "__AESOP_DEMO_STATE__"),
    ("/api/wave/prs", "__AESOP_DEMO_WAVE_PRS__"),
    ("/api/wave/telemetry", "__AESOP_DEMO_WAVE_TELEMETRY__"),
    ("/api/wave/dispatch", "__AESOP_DEMO_WAVE_DISPATCH__"),
    ("/api/wave/gantt", "__AESOP_DEMO_WAVE_GANTT__"),
    ("/api/wave/audit-tail", "__AESOP_DEMO_WAVE_AUDIT_TAIL__"),
    ("/api/wave/reasoning-tail", "__AESOP_DEMO_WAVE_REASONING_TAIL__"),
    ("/api/wave/quality-scorecards", "__AESOP_DEMO_WAVE_QUALITY_SCORECARDS__"),
    ("/api/cost", "__AESOP_DEMO_COST__"),
    ("/api/backlog", "__AESOP_DEMO_BACKLOG__"),
    ("/api/agents", "__AESOP_DEMO_AGENTS__"),
    ("/api/tracker", "__AESOP_DEMO_TRACKER__"),
]


def _find_free_port():
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port, timeout=SERVER_START_TIMEOUT):
    """Block until the demo server responds on the given port or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/favicon.ico")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            if resp.status in (200, 204):
                return True
        except (ConnectionRefusedError, OSError, http.client.HTTPException):
            pass
        time.sleep(SERVER_POLL_INTERVAL)
    return False


def _fetch_json(port, path):
    """Fetch a JSON endpoint from the demo server."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    if resp.status != 200:
        print(f"  WARNING: {path} returned {resp.status}", file=sys.stderr)
        return None
    return json.loads(body)


def _build_shim_script(captured):
    """Build the JS shim that patches fetch and EventSource for static hosting.

    Args:
        captured: dict mapping js_global_name -> captured JSON data

    Returns:
        str: JavaScript source for the shim <script> block.
    """
    # Embed each captured endpoint as a global.
    globals_js = "\n".join(
        f"  window[{json.dumps(name)}] = {json.dumps(data, separators=(',', ':'))};"
        for name, data in captured.items()
        if data is not None
    )

    # Build the route map for the fetch interceptor.
    route_entries = []
    for path, js_name in _CAPTURE_ENDPOINTS:
        route_entries.append(f"    {json.dumps(path)}: {json.dumps(js_name)}")
    route_map_js = ",\n".join(route_entries)

    return f"""(function() {{
  // --- Aesop Static Demo Shim ---
  // Embeds demo data and patches fetch/EventSource so the React app
  // renders a realistic snapshot without a live backend.

  // 1. Embed captured demo data as window globals.
{globals_js}

  // 2. Route map: URL path -> window global name.
  var ROUTES = {{
{route_map_js}
  }};

  // 3. Patch fetch to intercept known API routes.
  var _origFetch = window.fetch;
  window.fetch = function(input, init) {{
    var url = (typeof input === 'string') ? input : (input && input.url) || '';
    // Strip query string for route matching (except for parameterized routes).
    var basePath = url.split('?')[0];
    var globalName = ROUTES[basePath] || ROUTES[url];
    if (globalName && window[globalName] !== undefined) {{
      var body = JSON.stringify(window[globalName]);
      return Promise.resolve(new Response(body, {{
        status: 200,
        headers: {{ 'Content-Type': 'application/json' }}
      }}));
    }}
    // For unmatched API routes, return a graceful degradation response.
    if (url.indexOf('/api/') === 0 || url === '/data' || url === '/submit') {{
      var fallback = JSON.stringify({{ available: false, error: 'Static demo: endpoint not captured' }});
      return Promise.resolve(new Response(fallback, {{
        status: 200,
        headers: {{ 'Content-Type': 'application/json' }}
      }}));
    }}
    return _origFetch.apply(this, arguments);
  }};

  // 4. Patch EventSource to emit embedded state sections immediately,
  //    then sit quietly (no reconnect churn on a static page).
  window.EventSource = function FakeEventSource(url) {{
    this.readyState = 1; // OPEN
    this.url = url;
    this.close = function() {{ this.readyState = 2; }};
    this.addEventListener = function(type, fn) {{
      if (!this._listeners) this._listeners = {{}};
      if (!this._listeners[type]) this._listeners[type] = [];
      this._listeners[type].push(fn);
    }};
    this.removeEventListener = function(type, fn) {{
      if (!this._listeners || !this._listeners[type]) return;
      this._listeners[type] = this._listeners[type].filter(function(f) {{ return f !== fn; }});
    }};
    var self = this;
    // Emit all state sections after a microtask so listeners are attached.
    var state = window.__AESOP_DEMO_STATE__;
    if (state) {{
      setTimeout(function() {{
        var sections = ['data', 'backlog', 'agents', 'tracker', 'status', 'cost'];
        sections.forEach(function(section) {{
          if (state[section] !== undefined) {{
            var evt = new MessageEvent(section, {{
              data: JSON.stringify(state[section])
            }});
            if (self._listeners && self._listeners[section]) {{
              self._listeners[section].forEach(function(fn) {{ fn(evt); }});
            }}
          }}
        }});
      }}, 50);
    }}
  }};
  window.EventSource.CONNECTING = 0;
  window.EventSource.OPEN = 1;
  window.EventSource.CLOSED = 2;
}})();"""


def _build_banner_html():
    """The demo banner, same visual as demo.py BANNER_HTML but with a
    'hosted on GitHub Pages' note."""
    return (
        '<div id="aesop-demo-banner" role="note" style="position:fixed;top:0;'
        'left:0;right:0;z-index:2147483647;background:#b45309;color:#fff;'
        'font:600 12px/1.7 system-ui,-apple-system,sans-serif;text-align:center;'
        'padding:2px 10px;letter-spacing:0.06em;">'
        'DEMO DATA &#8212; static snapshot with fixture data '
        '(<a href="https://github.com/matt82198/aesop" '
        'style="color:#fff;text-decoration:underline;">source</a>)</div>'
    )


def build(output_dir=None):
    """Main build: start demo server, capture state, produce static site."""
    output = Path(output_dir) if output_dir else DEFAULT_OUTPUT

    # --- Validate prerequisites ---
    if not DIST_DIR.is_dir():
        print("ERROR: ui/web/dist/ not found. Run 'cd ui/web && npm run build' first.",
              file=sys.stderr)
        return 1

    dist_index = DIST_DIR / "index.html"
    if not dist_index.is_file():
        print("ERROR: ui/web/dist/index.html not found.", file=sys.stderr)
        return 1

    # --- Start demo server ---
    port = _find_free_port()
    print(f"Starting demo server on port {port}...")

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["AESOP_DEMO"] = "1"
    # Point AESOP_ROOT at the repo so WEB_DIST resolves correctly.
    env["AESOP_ROOT"] = str(REPO_ROOT)

    server_proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "ui" / "serve.py"), "--demo"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        if not _wait_for_server(port):
            print("ERROR: Demo server failed to start within timeout.",
                  file=sys.stderr)
            # Dump stderr for diagnostics.
            server_proc.terminate()
            _, stderr = server_proc.communicate(timeout=5)
            if stderr:
                print(stderr.decode("utf-8", errors="replace"), file=sys.stderr)
            return 1

        print("Demo server ready. Capturing API snapshots...")

        # --- Capture all endpoints ---
        captured = {}
        for path, js_name in _CAPTURE_ENDPOINTS:
            data = _fetch_json(port, path)
            if data is not None:
                captured[js_name] = data
                print(f"  captured {path}")
            else:
                print(f"  SKIP {path} (no data)")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait(timeout=5)

    if "__AESOP_DEMO_STATE__" not in captured:
        print("ERROR: Failed to capture /api/state -- cannot build static site.",
              file=sys.stderr)
        return 1

    # --- Produce output directory ---
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(DIST_DIR, output)
    print(f"Copied dist/ -> {output}")

    # --- Modify index.html ---
    index_path = output / "index.html"
    html = index_path.read_text(encoding="utf-8")

    # Fix asset paths: /assets/... -> ./assets/... for project-page hosting.
    html = html.replace('src="/assets/', 'src="./assets/')
    html = html.replace('href="/assets/', 'href="./assets/')

    # Replace CSRF sentinel with a static dummy token (mutations are disabled
    # in the static build anyway -- the fetch shim intercepts POST routes).
    html = html.replace("__AESOP_CSRF_SENTINEL__", json.dumps("static-demo-token"))

    # Inject the shim script BEFORE the app's module script.
    shim_js = _build_shim_script(captured)
    shim_tag = f"<script>{shim_js}</script>"
    banner = _build_banner_html()

    # Insert shim before the first <script type="module"> and banner after <body>.
    module_script_marker = '<script type="module"'
    if module_script_marker in html:
        html = html.replace(module_script_marker, shim_tag + "\n    " + module_script_marker, 1)

    body_idx = html.lower().find("<body")
    if body_idx != -1:
        insert_at = html.find(">", body_idx) + 1
        html = html[:insert_at] + banner + html[insert_at:]

    index_path.write_text(html, encoding="utf-8")
    print(f"Wrote {index_path} ({len(html):,} bytes)")

    # --- Write a .nojekyll file so GitHub Pages serves _-prefixed files. ---
    (output / ".nojekyll").write_text("", encoding="utf-8")

    print(f"\nStatic dashboard built at: {output}")
    print("Deploy with: GitHub Pages -> Actions -> pages.yml")
    return 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build static Aesop dashboard with demo data")
    parser.add_argument("--output", "-o", default=None,
                        help="Output directory (default: _site/)")
    args = parser.parse_args()
    sys.exit(build(args.output))


if __name__ == "__main__":
    main()
