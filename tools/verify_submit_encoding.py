"""Browser-level proof that /submit's inbox-file bootstrap writes valid UTF-8
(ui/serve.py, handle_submit / INBOX_FILE header write).

Bug: when state/ui-inbox.md doesn't exist yet, the header used to be written
with INBOX_FILE.write_text(...) with no encoding=. On Windows that falls back
to the locale-preferred encoding (cp1252), so the em-dash U+2014 in the header
is encoded as the single byte 0x97. Every subsequent append opens the file
with encoding='utf-8'. The result is a file that is not valid UTF-8 as a
whole: any reader that does ui-inbox.md.read_text(encoding="utf-8") (the
normal, correct thing to do — matching the encoding the append path already
declares) raises UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97.
That breaks the project's "orchestrator reads state/ui-inbox.md each turn /
on /power" pipeline on Windows dev machines.

This drives the REAL /submit flow end-to-end in a real headless Chromium:
type into #inbox-input, click #inbox-button, with the real CSRF token the
page injects — against a FRESH fixture (no pre-existing ui-inbox.md, so the
header-bootstrap path is exercised) — then asserts:
  (a) state/ui-inbox.md decodes cleanly via .read_text(encoding="utf-8")
      with NO exception
  (b) it contains the submitted marker text

Run: python tools/verify_submit_encoding.py     (exit 0 = proven, 1 = failed)
     python tools/verify_submit_encoding.py --allow-skip (exit 0 = proven or skipped, 1 = failed)

Fails with exit 1 if playwright/chromium is unavailable (unless --allow-skip is passed).
Use --allow-skip for environments that legitimately can't run a browser.
"""
import argparse
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
from playwright_common import free_port, copy_dist

REPO = Path(__file__).resolve().parent.parent
SERVE = REPO / "ui" / "serve.py"

MARKER = "SUBMIT-ENCODING-FIXTURE-MARKER: rebuild the flux capacitor"


def build_fixture(root: Path):
    """Fresh fixture root — deliberately does NOT create state/ui-inbox.md,
    so /submit exercises the header-bootstrap (write_text) path, not just
    the append path."""
    (root / "state").mkdir(exist_ok=True)
    (root / "transcripts").mkdir(exist_ok=True)
    (root / "dash").mkdir(exist_ok=True)

    # Copy ui/web/dist from the real repo so the server can serve the built React app
    copy_dist(root, REPO)

    (root / "AUDIT-BACKLOG.md").write_text(
        "# Audit backlog — verify_submit_encoding fixture\n\n## Landing log\n- fixture\n",
        encoding="utf-8",
    )
    # Minimal fake detector so the collector thread has nothing real to shell out to.
    (root / "dash" / "dash-extra.mjs").write_text(
        "console.log(JSON.stringify([]));\n", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Browser-level proof for /submit UTF-8 encoding"
    )
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help="Allow skipping if playwright/chromium is unavailable (exit 0 instead of 1)"
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        msg = "playwright missing — run `python -m playwright install chromium`, or pass --allow-skip"
        if args.allow_skip:
            print(f"SKIP: {msg}")
            return 0
        else:
            print(f"FAIL: {msg}")
            return 1

    root = Path(tempfile.mkdtemp(prefix="aesop-verify-submit-"))
    state_root = root / "state"

    # HARD GUARD: refuse to run if state_root looks like the real repo state dir
    # (e.g., ~/aesop/state or an absolute path ending with /aesop/state)
    real_state = Path.home() / "aesop" / "state"
    if state_root.resolve() == real_state.resolve():
        print("FAIL: state dir resolved to real repo state (~aesop/state), refusing to run")
        return 1

    inbox_file = state_root / "ui-inbox.md"
    port = free_port()
    env = dict(
        os.environ,
        AESOP_ROOT=str(root),
        AESOP_STATE_ROOT=str(state_root),
        AESOP_TRANSCRIPTS_ROOT=str(root / "transcripts"),
        AESOP_WEB_DIST=str(REPO / "ui" / "web" / "dist"),
        AESOP_PROOF_FIXTURES="1",
        AESOP_UI_COLLECT_INTERVAL="0.3",
        PORT=str(port),
    )
    build_fixture(root)
    assert not inbox_file.exists(), "fixture must start WITHOUT ui-inbox.md"

    server = subprocess.Popen(
        [sys.executable, str(SERVE)], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    failures = []
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        else:
            print("FAIL: server never came up")
            return 1

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as e:
                msg = f"chromium unavailable ({e}); run: python -m playwright install chromium"
                if args.allow_skip:
                    print(f"SKIP: {msg}")
                    return 0
                else:
                    print(f"FAIL: {msg}")
                    return 1
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")

            # Drive the real /submit flow: inbox-input -> inbox-submit, real
            # CSRF token (window.__AESOP_CSRF_TOKEN__ injected server-side).
            page.wait_for_selector("[data-testid='inbox-input']", timeout=8000)
            page.fill("[data-testid='inbox-input']", MARKER)
            page.click("[data-testid='inbox-submit']")

            # Wait for the inbox file to land on disk (async POST).
            for _ in range(50):
                if inbox_file.exists():
                    break
                time.sleep(0.2)
            else:
                failures.append("state/ui-inbox.md was never created by /submit")

            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    if not failures:
        raw = inbox_file.read_bytes()
        try:
            text = inbox_file.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            bad_byte = raw[e.start:e.end].hex()
            failures.append(
                f"(a) state/ui-inbox.md is not valid UTF-8: {e} "
                f"(offending byte(s): 0x{bad_byte}) — raw bytes: {raw!r}"
            )
        else:
            if MARKER not in text:
                failures.append(f"(b) submitted marker text not found in inbox file: {text!r}")

    shutil.rmtree(root, ignore_errors=True)

    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("PROVEN: (a) state/ui-inbox.md decodes cleanly as UTF-8 "
          "(b) submitted marker text present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
