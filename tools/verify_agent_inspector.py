"""Browser-level proof for the Agent Inspector drawer (ui/web/dist/ + /api/agent).

Drives the BUILT React app served by `python ui/serve.py` and exercises the
whole vertical slice of the inspector in a real Chromium via Playwright:
route + AgentsPanel mount, the real get_fleet_agents() → agents list (fed by a
stubbed dash-extra.mjs), and the real GET /api/agent?id= endpoint reading a
stubbed transcript. Everything is stubbed via a temp AESOP_ROOT so the proof is
deterministic and never touches the operator's real fleet.

One populated phase asserts:
  (a) console clean (no errors / no XSS side effects)
  (b) an agent row's "Inspect" button opens the drawer (role=dialog)
  (c) the transcript TAIL renders (distinctive stub line is present)
  (d) status is shown as TEXT ("running"), not color alone
  (e) an XSS payload in the transcript is rendered as escaped TEXT — no <script>
      /<img> element is injected and no onerror side effect fires
  (f) focus moves INTO the dialog on open (activeElement inside it)
  (g) Escape closes the drawer AND focus returns to the Inspect trigger

Run: python tools/verify_agent_inspector.py             (exit 0 = proven, 1 = failed)
     python tools/verify_agent_inspector.py --allow-skip (exit 0 = proven or skipped)

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
# playwright harness resolves regardless of cwd or how the file is loaded.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright_common import free_port, copy_dist, start_server, stop_server, filter_real_console_errors

REPO = Path(__file__).resolve().parent.parent
SERVE = REPO / "ui" / "serve.py"

# Generous, CI-safe waits. A cold ubuntu runner (cold chromium, cold `node`
# spawn for dash-extra.mjs, shared CPU) is much slower than a warm dev box, so
# every server/selector wait below is sized for the slow path, env-overridable.
SERVER_BOOT_TRIES = int(os.environ.get("AESOP_VERIFY_BOOT_TRIES", "150"))   # *0.2s = 30s
SERVER_BOOT_SLEEP = 0.2
SEL_TIMEOUT_MS = int(os.environ.get("AESOP_VERIFY_SEL_TIMEOUT_MS", "30000"))

# The stub agent id (also the transcript filename stem). Full id — the frontend
# fetches /api/agent?id=<this> and the backend prefix-globs agent-<id>*.jsonl.
AGENT_ID = "inspectme12345abc"

# A distinctive tail line the proof asserts on, plus an XSS payload that must be
# rendered as inert text (never injected as real DOM).
TAIL_MARKER = "DISTINCTIVE_TAIL_LINE_42"
XSS_PAYLOAD = "<img src=x onerror=\"window.__xss_fired=true\"><script>window.__xss_fired=true</script>"

# dash-extra.mjs stub: emits one agent so get_fleet_agents() lists a row.
DASH_EXTRA_STUB = (
    "console.log(JSON.stringify(["
    + json.dumps({
        "id": AGENT_ID,
        "project": "aesop",
        "status": "running",
        "age_s": 12,
        "hint": "wave-31 agent inspector drawer",
        "startedAt": "2026-07-17T14:02:11.000Z",
        "lastActivity": "2026-07-17T14:31:47.000Z",
        "runtimeSeconds": 1776,
        "tokensUsed": 48213,
        "taskLabel": "Build the Agent Inspector drawer for aesop.",
    })
    + "]));\n"
)




def build_root():
    """Fresh temp root with dist + a dash-extra stub + a stub transcript."""
    root = Path(tempfile.mkdtemp(prefix="aesop-verify-inspector-"))
    (root / "state").mkdir(exist_ok=True)
    transcripts = root / "transcripts"
    transcripts.mkdir(exist_ok=True)
    (root / "dash").mkdir(exist_ok=True)
    copy_dist(root, REPO)
    (root / "dash" / "dash-extra.mjs").write_text(DASH_EXTRA_STUB, encoding="utf-8")

    lines = [
        json.dumps({"type": "user", "parentUuid": None,
                    "message": {"content": "Build the Agent Inspector drawer for aesop."}}),
        json.dumps({"type": "assistant", "model": "claude-haiku-4-5",
                    "message": {"content": [{"type": "text", "text": "Reading the plan first."}]}}),
        json.dumps({"type": "assistant",
                    "message": {"content": f"{TAIL_MARKER} — progress update"}}),
        json.dumps({"type": "assistant",
                    "message": {"content": XSS_PAYLOAD}}),
    ]
    (transcripts / f"agent-{AGENT_ID}.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return root


def run_populated(pw, failures):
    root = build_root()
    port = free_port()
    server = start_server(root, port, REPO, SERVE, SERVER_BOOT_TRIES, SERVER_BOOT_SLEEP, "0.3")
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    console_errors, failed_urls = [], []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.on("response", lambda r: failed_urls.append(r.url) if r.status >= 400 else None)
    try:
        page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        try:
            page.wait_for_selector("[data-testid='health-header']", timeout=SEL_TIMEOUT_MS)
            page.evaluate("location.hash = '#/'")
            # Agents panel is on Overview; the Inspect trigger appears per row.
            page.wait_for_selector("[data-testid='agent-inspect-open']", timeout=SEL_TIMEOUT_MS)
        except Exception as e:
            failures.append(f"(b) agent row / inspect trigger never mounted: {e}")
            return

        # (b) open the drawer
        try:
            page.click("[data-testid='agent-inspect-open']")
            page.wait_for_selector("[data-testid='agent-inspector']", timeout=SEL_TIMEOUT_MS)
            dialog = page.locator("[data-testid='agent-inspector']")
            assert dialog.get_attribute("role") == "dialog", "drawer is not role=dialog"
            assert dialog.get_attribute("aria-modal") == "true", "drawer not aria-modal"
        except Exception as e:
            failures.append(f"(b) drawer did not open as a modal dialog: {e}")
            return

        # (c) transcript tail rendered (wait for the fetched detail)
        try:
            page.wait_for_selector("[data-testid='agent-inspector-transcript']", timeout=SEL_TIMEOUT_MS)
            tail_text = page.inner_text("[data-testid='agent-inspector-transcript']")
            assert TAIL_MARKER in tail_text, f"tail marker missing; got: {tail_text[:200]!r}"
        except Exception as e:
            failures.append(f"(c) transcript tail not rendered: {e}")

        # (d) status shown as TEXT (not color alone)
        try:
            status_text = page.inner_text("[data-testid='agent-inspector-status']")
            assert "running" in status_text.lower(), f"status text missing: {status_text!r}"
        except Exception as e:
            failures.append(f"(d) status not shown as text: {e}")

        # (e) XSS payload is inert: no injected element, no side effect, text present
        try:
            xss_fired = page.evaluate("window.__xss_fired === true")
            assert not xss_fired, "XSS onerror/script side effect fired"
            injected = page.evaluate(
                "document.querySelectorAll('[data-testid=\"agent-inspector-transcript\"] img,"
                " [data-testid=\"agent-inspector-transcript\"] script').length")
            assert injected == 0, f"{injected} raw HTML nodes injected from transcript"
            body_has_text = page.evaluate(
                "document.querySelector('[data-testid=\"agent-inspector-transcript\"]')"
                ".textContent.includes('onerror')")
            assert body_has_text, "XSS payload not rendered as visible text"
        except Exception as e:
            failures.append(f"(e) XSS not neutralised: {e}")

        # (f) focus moved INTO the dialog on open
        try:
            focus_inside = page.evaluate(
                "(() => { const d = document.querySelector('[data-testid=\"agent-inspector\"]');"
                " return !!d && d.contains(document.activeElement); })()")
            assert focus_inside, "focus did not move into the dialog"
        except Exception as e:
            failures.append(f"(f) focus not trapped into dialog: {e}")

        # (g) Escape closes AND focus returns to the Inspect trigger
        try:
            page.keyboard.press("Escape")
            page.wait_for_selector("[data-testid='agent-inspector']", state="detached", timeout=10000)
            focus_on_trigger = page.evaluate(
                "document.activeElement && document.activeElement.getAttribute('data-testid')"
                " === 'agent-inspect-open'")
            assert focus_on_trigger, "focus did not return to the Inspect trigger after close"
        except Exception as e:
            failures.append(f"(g) Escape-close / focus-restore failed: {e}")

        # (a) console clean
        time.sleep(0.4)
        real = filter_real_console_errors(console_errors, failed_urls)
        if real:
            failures.append(f"(a) console errors: {real[:3]}")
    finally:
        browser.close()
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Browser-level proof for the Agent Inspector drawer")
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
        run_populated(pw, failures)

    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1

    print("PROVEN: (a) console clean (b) Inspect opens a modal dialog "
          "(c) transcript tail renders (d) status shown as text "
          "(e) XSS payload rendered inert (no injected node/side effect) "
          "(f) focus trapped into dialog (g) Escape closes + focus restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
