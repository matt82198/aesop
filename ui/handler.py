#!/usr/bin/env python3
"""Aesop UI — HTTP request handler (DashboardHandler) + server entry (wave-9 split)."""
import http.server
import json
import queue
import socketserver
import sys
import threading
import urllib.parse
from pathlib import Path

import config
import cost
import csrf
import demo
import quality_scorecard
import sse
import wave_prs
import wave_telemetry
import wave_failure
import wave_dispatch
import wave_gantt
import wave_audit_tail
import wave_reasoning_tail
import wave_context
import bench_panel
import tooling_panel
import api
import api.tracker
import api.submit
import state_query_panel
from render import render_dashboard
from csrf import validate_csrf_request
from collectors import (_snapshot_data, _snapshot_tracker,
                       _snapshot_orchestrator_status, drain_tracker_inbox,
                       get_alerts, get_heartbeat_status,
                       get_main_thread_messages, get_monitor_heartbeat_status,
                       get_recent_events, get_repos_status,
                       parse_audit_backlog)
from agents import (_AGENT_ID_FORBIDDEN, _transcripts_fingerprint,
                   extract_agent_dispatch_prompt, get_agent_detail,
                   get_fleet_agents)
from sse import (_latest_lock, _latest_snapshots, _maybe_emit,
                register_sse_client, unregister_sse_client)


# SSE section names, in emit order. /api/state returns the same sections so the
# frontend's first paint is one round trip (plan D3.1).
_STATE_SECTIONS = ("data", "backlog", "agents", "tracker", "status", "cost")


def _path_is_contained(child, root):
    """Check if child path is contained within root path (no traversal).

    Returns True if child is under root, False if it escapes (e.g., via ..).
    Uses Path.is_relative_to (Python 3.9+) with a fallback for older runtimes.

    Args:
        child: Path object (typically resolved)
        root: Path object (typically resolved)

    Returns:
        bool: True if child is contained within root, False otherwise
    """
    try:
        return child.is_relative_to(root.resolve())
    except AttributeError:
        # Path.is_relative_to requires Python 3.9+; fall back for older runtimes.
        try:
            child.relative_to(root.resolve())
            return True
        except ValueError:
            return False

# MIME types for the built dist's content-hashed assets (wave-14, plan D3.4).
_ASSET_MIME = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".map": "application/json; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _is_client_disconnect_error(exc):
    """True if exception indicates a normal client disconnect (not an error).

    When clients close connections mid-stream (tab closed, network drop, etc.),
    we get BrokenPipeError, ConnectionAbortedError, or ConnectionResetError.
    On Windows, rare plain OSError with winerror 10053/10054 may also indicate
    a disconnect. These are normal lifecycle events, not bugs, and should not
    be logged as errors.

    Args:
        exc: The exception to check

    Returns:
        bool: True if this is a normal disconnect, False if it's an unexpected error
    """
    if isinstance(exc, (BrokenPipeError, ConnectionAbortedError,
                       ConnectionResetError)):
        return True
    if isinstance(exc, OSError):
        # Windows disconnect errors: 10053 (WSAECONNABORTED), 10054 (WSAECONNRESET)
        winerror = getattr(exc, 'winerror', None)
        if winerror in (10053, 10054):
            return True
    return False


def _is_local_origin(origin):
    """True if an Origin header value is on the local allowlist.

    Keep in sync with csrf.validate_csrf_request(): same six local forms
    (http[s]://127.0.0.1:<port>, http[s]://localhost:<port>, http[s]://[::1]:<port>).
    Both http:// and https:// schemes are accepted for the same loopback hosts.
    """
    return bool(origin) and (
        origin.startswith("http://127.0.0.1:") or
        origin.startswith("https://127.0.0.1:") or
        origin.startswith("http://localhost:") or
        origin.startswith("https://localhost:") or
        origin.startswith("http://[::1]:") or
        origin.startswith("https://[::1]:")
    )


def _is_valid_host_header(host_header, expected_port):
    """True if Host header value is on the loopback allowlist.

    DNS-rebinding mitigation (wave-19): validates that the Host header
    matches one of the allowed loopback addresses with the correct port.

    Allowed forms (where port matches expected_port):
    - 127.0.0.1 (no port, implies http default)
    - 127.0.0.1:<port>
    - localhost (no port)
    - localhost:<port>
    - [::1] (IPv6 loopback, no port)
    - [::1]:<port>

    Args:
        host_header: The value of the Host header (may be None/empty)
        expected_port: the port the server is actually bound to (validate
            against the live socket, not config.PORT — a test or a
            dynamically-assigned ephemeral port can differ from config)

    Returns:
        bool: True if host is on the local allowlist, False otherwise
    """
    if not host_header:
        return False

    host_header = host_header.strip()

    # Extract host and port from the header value
    # Handle IPv6 format [::1]:port
    if host_header.startswith("["):
        # IPv6 format: [::1] or [::1]:port
        if "]" not in host_header:
            return False
        bracket_end = host_header.index("]")
        host_part = host_header[:bracket_end + 1]  # Include brackets
        remainder = host_header[bracket_end + 1:]
        if remainder:
            # Port must follow immediately with a colon
            if not remainder.startswith(":"):
                return False
            try:
                port_part = int(remainder[1:])
            except (ValueError, IndexError):
                return False
        else:
            port_part = None
    else:
        # IPv4 or hostname format: 127.0.0.1, 127.0.0.1:port, localhost, localhost:port
        if ":" in host_header:
            host_part, port_str = host_header.rsplit(":", 1)
            try:
                port_part = int(port_str)
            except ValueError:
                return False
        else:
            host_part = host_header
            port_part = None

    # Check if host is on allowlist (127.0.0.1, localhost, or [::1])
    # RFC 7230: hostnames are case-insensitive, so lowercase before comparing
    allowed_hosts = ("127.0.0.1", "localhost", "[::1]")
    if host_part.lower() not in allowed_hosts:
        return False

    # If a port was specified, it must match the server's actual bound port
    if port_part is not None:
        if port_part != expected_port:
            return False

    return True


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for dashboard."""

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def do_GET(self):
        """Handle GET requests."""
        # DNS-rebinding mitigation: validate Host header against allowlist
        host_header = self.headers.get("Host", "").strip()
        if not _is_valid_host_header(host_header, self.server.server_address[1]):
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"error": "Forbidden: invalid Host header"}
            ).encode('utf-8'))
            return

        if self.path == "/":
            self.serve_html()
        elif self.path == "/data":
            self.serve_data()
        elif self.path == "/api/state":
            self.serve_api_state()
        elif self.path == "/api/session":
            self.serve_api_session()
        elif self.path == "/api/cost":
            self.serve_api_cost()
        elif self.path == "/api/wave/prs":
            self.serve_api_wave_prs()
        elif self.path == "/api/wave/telemetry":
            self.serve_api_wave_telemetry()
        elif self.path == "/api/wave/dispatch":
            self.serve_api_wave_dispatch()
        elif self.path == "/api/wave/gantt":
            self.serve_api_wave_gantt()
        elif self.path == "/api/wave/audit-tail":
            self.serve_api_wave_audit_tail()
        elif self.path == "/api/wave/reasoning-tail":
            self.serve_api_wave_reasoning_tail()
        elif self.path == "/api/wave/quality-scorecards":
            self.serve_api_wave_quality_scorecards()
        elif self.path.startswith("/api/wave/failure"):
            self.serve_api_wave_failure()
        elif self.path == "/api/backlog":
            self.serve_backlog()
        elif self.path == "/api/agents":
            self.serve_agents()
        elif self.path.startswith("/api/agent?"):
            self.serve_api_agent()
        elif self.path.startswith("/api/quality/spec-sharpness"):
            self.serve_api_spec_sharpness()
        elif self.path.startswith("/api/context/files"):
            self.serve_api_file_scope()
        elif self.path == "/api/bench":
            self.serve_api_bench()
        elif self.path == "/api/bench/compare":
            self.serve_api_bench_compare()
        elif self.path.startswith("/api/tooling/summary"):
            self.serve_api_tooling_summary()
        elif self.path.startswith("/api/tracker"):
            self.serve_tracker()
        elif self.path.startswith("/api/state/events"):
            state_query_panel.serve_api_state_events(self)
        elif self.path == "/api/state/streams":
            state_query_panel.serve_api_state_streams(self)
        elif self.path.startswith("/assets/"):
            self.serve_asset()
        elif self.path.startswith("/agent?"):
            self.serve_agent()
        elif self.path == "/events":
            self.serve_events()
        elif self.path == "/favicon.ico":
            # Browsers auto-request this; answer 204 so it never 404s (keeps the
            # console clean — the dashboard ships no favicon asset).
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        """Handle POST requests."""
        # DNS-rebinding mitigation: validate Host header against allowlist
        host_header = self.headers.get("Host", "").strip()
        if not _is_valid_host_header(host_header, self.server.server_address[1]):
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"error": "Forbidden: invalid Host header"}
            ).encode('utf-8'))
            return

        if self.path == "/submit":
            self.handle_submit()
        elif self.path == "/api/tracker":
            self.handle_tracker_create()
        elif self.path.startswith("/api/tracker/"):
            self.handle_tracker_mutate()
        else:
            self.send_error(404)

    def serve_html(self):
        """Serve the dashboard HTML.

        Wave-14 (plan D3.4/D7 U9 cutover): the built frontend at
        config.WEB_DIST/index.html is always required and must be present;
        if missing, return a hard 500 with a clear error (never fall back to
        a legacy template). config.WEB_DIST is read at call time so
        config.reload() keeps working across fixtures.
        """
        dist_index = config.WEB_DIST / "index.html"
        if not dist_index.is_file():
            error_msg = (
                "built dashboard missing — run npm run build in ui/web"
            )
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(error_msg.encode('utf-8'))
            return

        html = render_dashboard(csrf.SESSION_TOKEN, template_path=dist_index)
        # Zero-key demo mode: inject a fixed "DEMO DATA" banner right after
        # <body> so the seeded snapshot can never be mistaken for live state
        # (honesty is this project's brand). No-op in default mode.
        if demo.enabled():
            lower = html.lower()
            idx = lower.find("<body")
            if idx != -1:
                insert_at = html.find(">", idx)
                if insert_at != -1:
                    insert_at += 1
                    html = html[:insert_at] + demo.BANNER_HTML + html[insert_at:]
            else:
                html = demo.BANNER_HTML + html
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_asset(self):
        """GET /assets/* — static files from the built dist (config.WEB_DIST/assets).

        Security: path-traversal containment via resolve() + is_relative_to,
        mirroring the pattern in agents.py — the resolved candidate must stay
        inside WEB_DIST/assets or the request is refused. URL-encoded (%2e%2e,
        %2f, %5c) and absolute-path inputs are decoded first so the containment
        check sees the real target.

        Caching: filenames are content-hashed by the Vite build, so responses
        are immutable (Cache-Control: public, max-age=31536000, immutable).
        """
        try:
            assets_root = config.WEB_DIST / "assets"
            if not assets_root.is_dir():
                # No built dist present (pre-cutover main) — nothing to serve.
                self.send_error(404)
                return

            raw_path = urllib.parse.urlparse(self.path).path
            rel = urllib.parse.unquote(raw_path[len("/assets/"):])
            if not rel or "\x00" in rel:
                self.send_error(404)
                return

            candidate = (assets_root / rel).resolve()

            # Containment check: the resolved target must stay inside
            # WEB_DIST/assets. Uses _path_is_contained helper for clarity.
            if not _path_is_contained(candidate, assets_root):
                self.send_error(403)
                return

            if not candidate.is_file():
                self.send_error(404)
                return

            content = candidate.read_bytes()
            mime = _ASSET_MIME.get(candidate.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass  # client went away mid-response — normal, not an error
        except OSError as e:
            if not _is_client_disconnect_error(e):
                print(f"[serve_asset] Uncaught OSError: {e}", file=sys.stderr)
                self.send_error(500)
            # else: silently pass for disconnect errors
        except Exception as e:
            if not _is_client_disconnect_error(e):
                print(f"[serve_asset] Uncaught exception: {e}", file=sys.stderr)
            self.send_error(500)

    def serve_api_state(self):
        """GET /api/state — consolidated snapshot of all six SSE sections.

        One round trip for the frontend's first paint (plan D3.1). Reuses the
        collectors' latest-snapshot mechanism: sections the background collector
        has already produced are returned as-is; anything not yet snapshotted
        (first-ever request) is computed inline, mirroring serve_events.
        """
        try:
            sse.start_collector_thread()
            with _latest_lock:
                latest = dict(_latest_snapshots)

            computers = {
                "data": _snapshot_data,
                "backlog": parse_audit_backlog,
                "agents": get_fleet_agents,
                "tracker": _snapshot_tracker,
                "status": _snapshot_orchestrator_status,
                "cost": cost.get_cost_summary,
            }
            state = {}
            for name in _STATE_SECTIONS:
                payload = latest.get(name)
                if payload is not None:
                    state[name] = json.loads(payload)
                else:
                    state[name] = computers[name]()

            # Zero-key demo mode self-identifies in the payload so the frontend
            # (and any API consumer) can tell seeded data from live state.
            if demo.enabled():
                state["demo"] = True

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(state, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_state] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_session(self):
        """GET /api/session — the CSRF token for same-origin JS (plan D3.3).

        Exists so the Vite dev server (which cannot do the sentinel
        substitution) still gets a working token; the built app keeps the
        sentinel-injection path as primary.

        SECURITY — Origin-checked FAIL-CLOSED: unlike the mutation endpoints
        (where a missing Origin is tolerated because the token itself gates the
        write), this endpoint HANDS OUT the token, so absence of an Origin
        header is a refusal. Only the same local allowlist csrf.py uses
        (127.0.0.1 / localhost / [::1]) is accepted.
        """
        origin = self.headers.get("Origin", "").strip()
        if not _is_local_origin(origin):
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"error": "Forbidden: /api/session requires a local Origin header"}
            ).encode('utf-8'))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(json.dumps({"token": csrf.SESSION_TOKEN}).encode('utf-8'))

    def serve_api_cost(self):
        """GET /api/cost — cost/scorecard summary from the outcomes ledger (plan D3.2)."""
        try:
            summary = cost.get_cost_summary()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(summary, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_cost] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_bench(self):
        """GET /api/bench — latest benchmark results from the journal."""
        try:
            payload = bench_panel.get_bench_results()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_bench] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_bench_compare(self):
        """GET /api/bench/compare — model comparison summary from benchmark results."""
        try:
            payload = bench_panel.get_bench_comparison()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_bench_compare] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_tooling_summary(self):
        """GET /api/tooling/summary — aggregated tooling scan results.

        Read-only; runs tool subprocesses (short timeout, cached 60s).
        Gracefully degrades to null for any metric whose tool is missing.
        Query param ?force=1 bypasses cache.
        """
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        force = params.get("force", ["0"])[0] == "1"
        tooling_panel.serve_api_tooling_summary(self, force=force)

    def serve_api_wave_prs(self):
        """GET /api/wave/prs — open PRs + PR-less feat/* branches for the PR board.

        Read-only; runs `gh pr list` / `git for-each-ref` (short timeout, cached
        a few seconds). Degrades to a well-formed {available:false, error:...}
        payload when gh is missing or un-authenticated — never a 500 for those.
        """
        try:
            payload = wave_prs.get_wave_prs()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_wave_prs] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_wave_telemetry(self):
        """GET /api/wave/telemetry — current wave phase, cost metrics, and top blocker.

        Read-only; reads state at call time (no caching). Returns wave phase info,
        current cost metrics, and top blocker from AUDIT-BACKLOG.md.
        """
        try:
            payload = wave_telemetry.get_wave_telemetry()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_wave_telemetry] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_wave_dispatch(self):
        """GET /api/wave/dispatch — live per-agent phase and activity visibility.

        Read-only; reads at call time (no caching). Returns per-agent phase,
        last-activity age, and token burn estimates. Degrades to {available:false}
        when no active workflow.
        """
        try:
            payload = wave_dispatch.get_wave_dispatch()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_wave_dispatch] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_wave_gantt(self):
        """GET /api/wave/gantt — Gantt timeline data for agents in current wave.

        Read-only; reads at call time (no caching). Returns per-agent rows with
        phase timing spans suitable for Gantt visualization. Degrades to
        {available:false} when no active workflow.
        """
        try:
            payload = wave_gantt.get_wave_gantt()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_wave_gantt] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_wave_audit_tail(self):
        """GET /api/wave/audit-tail — latest audit/verification outcomes.

        Read-only; reads at call time (no caching). Returns recent audit backlog
        items and ledger verdicts as a compact tail. Shows latest findings from
        adversarial reviews and verify verdicts.
        """
        try:
            payload = wave_audit_tail.get_wave_audit_tail()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_wave_audit_tail] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_wave_reasoning_tail(self):
        """GET /api/wave/reasoning-tail — per-agent live reasoning/transcript summary.

        Read-only; reads at call time (no caching). Returns latest transcript activity
        summary for each live agent (redacted). Shows thinking/tool-call sequences
        in a compact format suitable for Activity view transparency.
        """
        try:
            payload = wave_reasoning_tail.get_wave_reasoning_tail()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_wave_reasoning_tail] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_wave_quality_scorecards(self):
        """GET /api/wave/quality-scorecards — per-agent-specialty quality metrics.

        Read-only; reads at call time (no caching). Returns per-specialty quality:
        success rate (green/total) and retry/repair frequency from ledger.
        Includes top rankings by success and retry frequency.
        """
        try:
            payload = quality_scorecard.get_quality_scorecard()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_wave_quality_scorecards] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_api_wave_failure(self):
        """GET /api/wave/failure?pr=N — CI job logs and failure details for a PR.

        Read-only; shells `gh run view --json jobs` / `gh api .../logs` (short timeout,
        cached a few seconds). Degrades to {available:false, error:...} when gh is
        missing or un-authenticated — never a 500 for those.

        Query params:
          pr: PR number (required)
        """
        try:
            # Parse query string for pr parameter
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            pr_str = params.get('pr', [None])[0]

            if not pr_str or not pr_str.isdigit():
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "pr parameter required and must be a number"}).encode('utf-8'))
                return

            pr_number = int(pr_str)
            payload = wave_failure.get_wave_failure(pr_number)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(payload, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_api_wave_failure] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_data(self):
        """Serve dashboard data as JSON."""
        try:
            data = {
                "watchdog": get_heartbeat_status(),
                "monitor": get_monitor_heartbeat_status(),
                "agents": get_fleet_agents(),
                "repos": get_repos_status(),
                "events": get_recent_events(),
                "alerts": get_alerts(),
                "messages": get_main_thread_messages(),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_data] Uncaught exception: {e}", file=sys.stderr)
            try:
                # Best-effort 500 response
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                # Sending to a dead client must not cascade
                pass

    def serve_tracker(self):
        """Serve tracker items as JSON via GET /api/tracker."""
        try:
            # Parse query string for filters
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            status = params.get('status', [None])[0]
            priority = params.get('priority', [None])[0]

            status_code, body = api.tracker.list_items(status=status, priority=priority)
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(body, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_tracker] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def handle_tracker_create(self):
        """Handle POST /api/tracker (create item)."""
        try:
            is_valid, reason = validate_csrf_request(self.headers)
            if not is_valid:
                self.send_response(403)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "CSRF protection: " + reason}).encode('utf-8'))
                return

            content_length = int(self.headers.get('Content-Length', 0))
            # Read bounded amount; api.validate_mutation() validates Content-Length
            body_bytes = self.rfile.read(min(max(content_length, 0), api.MAX_BODY_BYTES))
            status_code, result = api.tracker.create(self.headers, body_bytes)
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(result, default=str).encode('utf-8'))
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass  # Client disconnected — normal lifecycle, not an error.
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[handle_tracker_create] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def handle_tracker_mutate(self):
        """Handle POST /api/tracker/<id> (update or delete)."""
        try:
            is_valid, reason = validate_csrf_request(self.headers)
            if not is_valid:
                self.send_response(403)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "CSRF protection: " + reason}).encode('utf-8'))
                return

            # Extract item_id from path
            path_parts = self.path.strip("/").split("/")
            if len(path_parts) < 3:
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Not found"}).encode('utf-8'))
                return

            item_id = path_parts[2]

            # Parse query for action (update or delete)
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            action = params.get('action', ['update'])[0]

            if action == "delete":
                status_code, result = api.tracker.delete(item_id)
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, default=str).encode('utf-8'))
            else:
                content_length = int(self.headers.get('Content-Length', 0))
                # Read bounded amount; api.validate_mutation() validates Content-Length
                body_bytes = self.rfile.read(min(max(content_length, 0), api.MAX_BODY_BYTES))
                status_code, result = api.tracker.update(item_id, body_bytes)
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, default=str).encode('utf-8'))
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass  # Client disconnected — normal lifecycle, not an error.
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[handle_tracker_mutate] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone


    def serve_backlog(self):
        """Serve audit backlog data as JSON via GET /api/backlog."""
        try:
            data = parse_audit_backlog()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_backlog] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_agents(self):
        """Serve rich agent list with metadata via GET /api/agents."""
        try:
            agents = get_fleet_agents()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(agents, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[serve_agents] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone

    def serve_agent(self):
        """Serve agent dispatch prompt and metadata via GET /agent?id=<agent_id>"""
        try:
            # Parse query string
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            agent_id = params.get('id', [None])[0]

            if not agent_id:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "missing id parameter"}).encode('utf-8'))
                return

            # Extract dispatch prompt and metadata
            data = extract_agent_dispatch_prompt(agent_id)

            if "error" in data:
                # Rejected input (path traversal, glob metacharacters, or a match
                # that resolved outside config.TRANSCRIPTS_ROOT) -> 400. A well-formed id
                # with no matching transcript -> 404. Never 200 on error.
                status = 400 if data.get("invalid") else 404
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": data["error"]}).encode('utf-8'))
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            if not _is_client_disconnect_error(e):
                print(f"[serve_agent] Uncaught exception: {e}", file=sys.stderr)
            self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))

    def serve_api_agent(self):
        """GET /api/agent?id=<agent_id> — agent detail for the Inspector drawer.

        Read-only. Returns the dispatch prompt/metadata PLUS a bounded, secret-
        redacted transcript tail (last ~40 NDJSON lines, seek-read so a huge
        transcript is never fully loaded). Same id-safety + status-code contract
        as GET /agent: rejected input -> 400, well-formed id with no transcript
        -> 404, unexpected failure -> 500. Never leaks a raw traceback.
        """
        try:
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            agent_id = params.get('id', [None])[0]

            if not agent_id:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "missing id parameter"}).encode('utf-8'))
                return

            data = get_agent_detail(agent_id)

            if "error" in data:
                status = 400 if data.get("invalid") else 404
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": data["error"]}).encode('utf-8'))
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            if not _is_client_disconnect_error(e):
                print(f"[serve_api_agent] Uncaught exception: {e}", file=sys.stderr)
            self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))

    def serve_api_spec_sharpness(self):
        """GET /api/quality/spec-sharpness?agent=<id> — spec sharpness indicator (C1).

        Read-only. Returns a spec sharpness score badge (Low/Med/High/Excellent)
        based on dispatch prompt signals (directive count, acceptance criteria,
        file specificity, structured content, emphasis markers).
        """
        try:
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            agent_id = params.get('agent', [None])[0]

            if not agent_id:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "missing agent parameter"}).encode('utf-8'))
                return

            data = wave_context.get_spec_sharpness(agent_id)
            if data is None:
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"agent {agent_id} not found"}).encode('utf-8'))
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return
            print(f"[serve_api_spec_sharpness] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass

    def serve_api_file_scope(self):
        """GET /api/context/files?agent=<id> — file-scope visualizer (C2).

        Read-only. Returns intended vs actual files for a dispatch, showing
        scope coverage and drift (files only in intended, files only in actual).
        """
        try:
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            agent_id = params.get('agent', [None])[0]

            if not agent_id:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "missing agent parameter"}).encode('utf-8'))
                return

            data = wave_context.get_file_scope(agent_id)
            if data is None:
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"agent {agent_id} not found"}).encode('utf-8'))
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(data, default=str).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return
            print(f"[serve_api_file_scope] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass

    def _write_sse_event(self, event_name, payload):
        """Write one SSE frame with timeout. Caller handles disconnect exceptions."""
        msg = f"event: {event_name}\ndata: {payload}\n\n"
        # Set socket timeout to prevent stalled writes from blocking the server
        old_timeout = None
        try:
            old_timeout = self.connection.gettimeout()
            self.connection.settimeout(config.SSE_WRITE_TIMEOUT)
        except (AttributeError, OSError):
            pass
        try:
            self.wfile.write(msg.encode("utf-8"))
            self.wfile.flush()
        finally:
            # Restore original timeout
            try:
                if old_timeout is not None:
                    self.connection.settimeout(old_timeout)
            except (AttributeError, OSError):
                pass

    def serve_events(self):
        """GET /events — Server-Sent Events stream.

        No CSRF token required: this is a read-only stream, not a mutation (POST
        /submit keeps its token requirement unchanged). Holds the connection open
        for the life of the client; requires ThreadingHTTPServer (see run_server)
        so one SSE client can't block every other request.

        Returns HTTP 503 if concurrent connection cap (config.SSE_MAX_CLIENTS) is exceeded.
        """
        sse.start_collector_thread()

        q = register_sse_client()
        if q is None:
            # Connection cap exceeded; return 503 Service Unavailable
            try:
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Retry-After", "30")
                self.end_headers()
                self.wfile.write(b"Service overloaded: too many concurrent clients\n")
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass
            except OSError as e:
                if not _is_client_disconnect_error(e):
                    print(f"[serve_events cap-exceeded] Uncaught OSError: {e}", file=sys.stderr)
                # else: silently pass for disconnect errors
            return

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            unregister_sse_client(q)
            return
        except OSError as e:
            if not _is_client_disconnect_error(e):
                print(f"[serve_events headers] Uncaught OSError: {e}", file=sys.stderr)
            unregister_sse_client(q)
            return
        try:
            # Send an immediate full snapshot so first paint isn't empty. If the
            # collector hasn't produced anything yet (first-ever request), compute
            # it inline once.
            with _latest_lock:
                initial = dict(_latest_snapshots)
            if all(v is None for v in initial.values()):
                initial["data"] = json.dumps(_snapshot_data(), default=str, sort_keys=True)
                initial["backlog"] = json.dumps(parse_audit_backlog(), default=str, sort_keys=True)
                initial["agents"] = json.dumps(get_fleet_agents(), default=str, sort_keys=True)
                initial["tracker"] = json.dumps(_snapshot_tracker(), default=str, sort_keys=True)
                initial["status"] = json.dumps(_snapshot_orchestrator_status(), default=str, sort_keys=True)
                initial["cost"] = json.dumps(cost.get_cost_summary(), default=str, sort_keys=True)
                with _latest_lock:
                    _latest_snapshots.update(initial)

            for name in _STATE_SECTIONS:
                payload = initial.get(name)
                if payload is not None:
                    self._write_sse_event(name, payload)

            while True:
                try:
                    event_name, payload = q.get(timeout=config.SSE_KEEPALIVE_SECONDS)
                    self._write_sse_event(event_name, payload)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Client disconnected (tab closed, network drop) — normal, not an error.
            pass
        except OSError as e:
            if not _is_client_disconnect_error(e):
                print(f"[serve_events stream] Uncaught OSError: {e}", file=sys.stderr)
            # else: silently pass for disconnect errors
        except Exception:
            pass
        finally:
            unregister_sse_client(q)

    def handle_submit(self):
        """Handle /submit POST with CSRF protection."""
        try:
            # CSRF validation: Check Origin/Referer + X-Aesop-Token
            is_valid, reason = validate_csrf_request(self.headers)
            if not is_valid:
                self.send_response(403)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": "CSRF protection: " + reason
                }).encode('utf-8'))
                return

            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0 or content_length > 10000:  # 10KB limit, must be positive
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": "Invalid Content-Length (must be 1-10000 bytes)"
                }).encode('utf-8'))
                return

            body_bytes = self.rfile.read(content_length)
            data = json.loads(body_bytes.decode('utf-8', errors='replace'))
            text = data.get("text", "").strip()

            if not text:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No text provided"}).encode('utf-8'))
                return

            ok, result = api.submit.append_to_inbox(text)
            if not ok:
                status_code, error = result
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(error).encode('utf-8'))
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))
        except Exception as e:
            if _is_client_disconnect_error(e):
                return  # client gone: nothing to send or log
            print(f"[handle_submit] Uncaught exception: {e}", file=sys.stderr)
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal server error"}).encode('utf-8'))
            except Exception:
                pass  # best-effort error response; client may be gone


class QuietThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer that suppresses expected socket disconnect exceptions.

    During normal operation and especially during shutdown, ThreadingHTTPServer may
    encounter ConnectionAbortedError (WinError 10053) or ConnectionResetError
    (WinError 10054) when clients disconnect abruptly. These are expected, not errors,
    and clutter stderr with tracebacks.

    This server overrides handle_error() to suppress only these two exception types
    while still reporting all other exceptions (real bugs, timeouts, etc.).
    """

    def handle_error(self, request, client_address):
        """Suppress client disconnect exceptions; report all others.

        Args:
            request: The socket request object
            client_address: The client address tuple
        """
        exc_type, exc_value, exc_tb = sys.exc_info()

        # Suppress only the two disconnect exception types that occur during
        # normal client aborts (especially on shutdown). All other exceptions
        # (real bugs, timeouts, etc.) still get logged via super().
        if exc_type in (ConnectionAbortedError, ConnectionResetError):
            # Expected client disconnect; silent is correct.
            return

        # All other exceptions get the default handler (logged to stderr)
        super().handle_error(request, client_address)


def run_server():
    """Start the HTTP server.

    Must be ThreadingHTTPServer, not HTTPServer: GET /events (SSE) holds its
    connection open for the life of the client, so a single-threaded server would
    wedge every other request (including the initial page load and /submit)
    behind that one held connection.

    Uses QuietThreadingHTTPServer to suppress expected socket disconnect exceptions.
    """
    addr = ("127.0.0.1", config.PORT)
    httpd = QuietThreadingHTTPServer(addr, DashboardHandler)
    httpd.daemon_threads = True
    sse.start_collector_thread()
    print(f"Dashboard: http://localhost:{config.PORT}")
    print(f"config.AESOP_ROOT: {config.AESOP_ROOT}")
    print(f"Transcripts: {config.TRANSCRIPTS_ROOT}")
    print(f"Press Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sse._collector_stop_event.set()
        print("\nShutdown complete.")
        sys.exit(0)
