"""Browser-level proof for the Wave PR Board view (ui/web/dist/ + /api/wave/prs).

Drives the BUILT React app served by `python ui/serve.py` and exercises the
full stack of the PR board — route wiring, component mount, the real
GET /api/wave/prs endpoint (which runs `gh`), and rendering — in a real
Chromium via Playwright. `gh` is stubbed through the AESOP_GH_BIN seam so the
proof is deterministic regardless of the box's real GitHub state.

Three phases, each a fresh server boot with its own fake gh:

  Populated: fake gh emits fixture PRs (passing/failing/pending/draft) →
    (a) console clean
    (b) #/prs route renders the view + table with one row per PR
    (c) CI status is color-INDEPENDENT: the words Passing/Failing/Pending are
        present as text (not conveyed by color alone)
    (d) top-blocker text ("CI failing") is shown
    (e) a PR title is a real, keyboard-focusable <a> to the PR url
    (f) no javascript: href leaks into the DOM

  Empty (gh ok, zero PRs): fake gh emits [] →
    (g) the "No open PRs" empty state renders, no table, clean console

  Unavailable (gh un-authenticated): fake gh exits non-zero with an auth error →
    (h) the "GitHub CLI unavailable" callout renders with the backend reason,
        clean console

Run: python tools/verify_prboard.py            (exit 0 = proven, 1 = failed)
     python tools/verify_prboard.py --allow-skip (exit 0 = proven or skipped)

Fails with exit 1 if playwright/chromium is unavailable (unless --allow-skip).
"""
import argparse
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

# Generous, CI-safe waits. A cold ubuntu runner (cold chromium, cold python
# import, shared CPU) is much slower than a warm dev box, so every server/
# selector wait below is sized for the slow path, overridable via env.
SERVER_BOOT_TRIES = int(os.environ.get("AESOP_VERIFY_BOOT_TRIES", "150"))   # *0.2s = 30s
SERVER_BOOT_SLEEP = 0.2
SEL_TIMEOUT_MS = int(os.environ.get("AESOP_VERIFY_SEL_TIMEOUT_MS", "30000"))

# Fixture PRs the fake `gh pr list` emits in the populated phase. Covers every
# CI rollup state + a draft so the board's whole status vocabulary is exercised.
FIXTURE_PR_JSON = """import json
print(json.dumps([
  {"number": 501, "title": "feat: PR board passing case", "headRefName": "feat/wave30-a",
   "mergeable": "MERGEABLE", "isDraft": False, "url": "https://github.com/example/aesop/pull/501",
   "createdAt": "2026-07-17T08:00:00Z", "reviewDecision": "REVIEW_REQUIRED",
   "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "SUCCESS"}]},
  {"number": 502, "title": "fix: PR board failing case", "headRefName": "feat/wave30-b",
   "mergeable": "CONFLICTING", "isDraft": False, "url": "https://github.com/example/aesop/pull/502",
   "createdAt": "2026-07-16T18:00:00Z", "reviewDecision": "",
   "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]},
  {"number": 503, "title": "wip: PR board pending draft", "headRefName": "feat/wave30-c",
   "mergeable": "MERGEABLE", "isDraft": True, "url": "https://github.com/example/aesop/pull/503",
   "createdAt": "2026-07-17T07:30:00Z", "reviewDecision": "",
   "statusCheckRollup": [{"status": "IN_PROGRESS"}]}
]))
"""

FIXTURE_EMPTY_JSON = "print('[]')\n"

FIXTURE_AUTH_FAIL = """import sys
sys.stderr.write("gh: To get started with GitHub CLI, please run: gh auth login\\n")
sys.exit(1)
"""




def make_fake_gh(bin_dir: Path, script_body: str) -> Path:
    """Write a fake `gh` executable and return its path — Linux-CI robust.

    wave_prs.py invokes AESOP_GH_BIN directly as ``argv[0]`` via subprocess, so
    the stub must be a file the *current* OS can exec on its own:

      * Windows: a full-path ``gh.cmd`` batch that shells out to a python stub
        (a bare extension-less script is not runnable by subprocess on Windows).
      * POSIX (ubuntu CI): a python script whose first line is a ``#!`` shebang
        pointing at this very interpreter, marked executable (chmod +x). The
        previous ``gh.cmd`` was NOT executable on Linux — the kernel refused it
        with EACCES, ``gh pr list`` failed, /api/wave/prs degraded to empty, and
        the PR-table selector timed out. That was the CI failure this fixes.

    Either way the stub ignores its args and emits the fixture, which is all
    ``gh pr list`` needs for the proof.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        stub = bin_dir / "gh_stub.py"
        stub.write_text(script_body, encoding="utf-8")
        gh_cmd = bin_dir / "gh.cmd"
        gh_cmd.write_text('@python "%~dp0gh_stub.py" %*\n', encoding="utf-8")
        return gh_cmd
    # POSIX: shebang + executable bit so the ubuntu runner can exec it directly.
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{script_body}", encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return gh


def build_root(gh_script: str):
    """Fresh temp root with dist + a fake gh; returns (root, gh_path)."""
    root = Path(tempfile.mkdtemp(prefix="aesop-verify-prboard-"))
    (root / "state").mkdir(exist_ok=True)
    (root / "transcripts").mkdir(exist_ok=True)
    (root / "dash").mkdir(exist_ok=True)
    copy_dist(root, REPO)
    (root / "dash" / "dash-extra.mjs").write_text(
        "console.log(JSON.stringify([]));\n", encoding="utf-8")
    gh_path = make_fake_gh(root / "fakebin", gh_script)
    return root, gh_path


def start_server(root: Path, port: int, gh_path: Path):
    state_root = root / "state"
    real_state = Path.home() / "aesop" / "state"
    if state_root.resolve() == real_state.resolve():
        raise RuntimeError("state dir resolved to real repo state (~aesop/state)")
    env = dict(os.environ,
               AESOP_ROOT=str(root),
               AESOP_STATE_ROOT=str(state_root),
               AESOP_TRANSCRIPTS_ROOT=str(root / "transcripts"),
               AESOP_WEB_DIST=str(REPO / "ui" / "web" / "dist"),
               AESOP_PROOF_FIXTURES="1",
               AESOP_UI_COLLECT_INTERVAL="0.3",
               AESOP_GH_BIN=str(gh_path),
               PORT=str(port))
    server = subprocess.Popen([sys.executable, str(SERVE)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(SERVER_BOOT_TRIES):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return server
        except OSError:
            time.sleep(SERVER_BOOT_SLEEP)
    server.kill()
    raise RuntimeError("server never came up")


def _boot(pw, gh_script):
    """Boot a fresh server+page for one phase. Returns (server, root, browser,
    page, console_errors, failed_urls). Caller must goto + assert + teardown."""
    root, gh_path = build_root(gh_script)
    port = free_port()
    server = start_server(root, port, gh_path)
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    console_errors, failed_urls = [], []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.on("response", lambda r: failed_urls.append(r.url) if r.status >= 400 else None)
    page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
    return server, root, browser, page, console_errors, failed_urls, port


def run_populated(pw, failures):
    server, root, browser, page, console_errors, failed_urls, _ = _boot(pw, FIXTURE_PR_JSON)
    try:
        try:
            page.wait_for_selector("[data-testid='health-header']", timeout=SEL_TIMEOUT_MS)
            page.evaluate("location.hash = '#/prs'")
            page.wait_for_selector("[data-testid='view-prboard']", timeout=SEL_TIMEOUT_MS)
        except Exception as e:
            failures.append(f"(b) PR board view never mounted: {e}")
            return
        # (b) table + one row per PR
        try:
            page.wait_for_selector("[data-testid='prboard-table']", timeout=SEL_TIMEOUT_MS)
            rows = page.locator("[data-testid='prboard-row']").count()
            assert rows == 3, f"expected 3 rows, got {rows}"
        except Exception as e:
            failures.append(f"(b) PR board table/rows wrong: {e}")
        # (c) color-independent CI text labels present
        try:
            body = page.inner_text("[data-testid='prboard-table']")
            for word in ("Passing", "Failing", "Pending"):
                assert word in body, f"CI label '{word}' not shown as text"
        except Exception as e:
            failures.append(f"(c) CI status not color-independent: {e}")
        # (d) top blocker text
        try:
            body = page.inner_text("[data-testid='prboard-table']")
            assert "CI failing" in body, "top blocker 'CI failing' not shown"
        except Exception as e:
            failures.append(f"(d) blocker not shown: {e}")
        # (e) PR title is a real, keyboard-focusable link to the url
        try:
            link = page.get_by_role("link", name="feat: PR board passing case")
            assert link.get_attribute("href") == "https://github.com/example/aesop/pull/501"
            link.focus()
            focused_tag = page.evaluate("document.activeElement && document.activeElement.tagName")
            assert focused_tag == "A", f"PR link not keyboard-focusable (active={focused_tag})"
        except Exception as e:
            failures.append(f"(e) PR title link/focus failed: {e}")
        # (f) no javascript: href leaked
        try:
            bad = page.evaluate(
                "Array.from(document.querySelectorAll('a[href]'))"
                ".filter(a => a.href.toLowerCase().startsWith('javascript:')).length")
            assert bad == 0, f"{bad} javascript: anchors present"
        except Exception as e:
            failures.append(f"(f) javascript: href leaked: {e}")
        # (a) console clean
        time.sleep(0.4)
        real = filter_real_console_errors(console_errors, failed_urls)
        if real:
            failures.append(f"(a) populated console errors: {real[:3]}")
    finally:
        browser.close()
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def run_empty(pw, failures):
    server, root, browser, page, console_errors, failed_urls, _ = _boot(pw, FIXTURE_EMPTY_JSON)
    try:
        try:
            page.wait_for_selector("[data-testid='health-header']", timeout=SEL_TIMEOUT_MS)
            page.evaluate("location.hash = '#/prs'")
            page.wait_for_selector("[data-testid='prboard-empty']", timeout=SEL_TIMEOUT_MS)
            text = page.inner_text("[data-testid='prboard-empty']")
            assert "No open PRs" in text or "feature branch" in text, \
                f"empty state text unexpected: {text!r}"
            assert page.locator("[data-testid='prboard-table']").count() == 0, \
                "table should not render in the empty state"
        except Exception as e:
            failures.append(f"(g) empty state failed: {e}")
        time.sleep(0.4)
        real = filter_real_console_errors(console_errors, failed_urls)
        if real:
            failures.append(f"(g) empty console errors: {real[:3]}")
    finally:
        browser.close()
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def run_unavailable(pw, failures):
    server, root, browser, page, console_errors, failed_urls, _ = _boot(pw, FIXTURE_AUTH_FAIL)
    try:
        try:
            page.wait_for_selector("[data-testid='health-header']", timeout=SEL_TIMEOUT_MS)
            page.evaluate("location.hash = '#/prs'")
            page.wait_for_selector("[data-testid='prboard-empty']", timeout=SEL_TIMEOUT_MS)
            text = page.inner_text("[data-testid='prboard-empty']")
            assert "GitHub CLI" in text, f"gh-unavailable callout missing: {text!r}"
            assert "authenticated" in text.lower(), f"backend reason not surfaced: {text!r}"
        except Exception as e:
            failures.append(f"(h) gh-unavailable state failed: {e}")
        time.sleep(0.4)
        real = filter_real_console_errors(console_errors, failed_urls)
        if real:
            failures.append(f"(h) unavailable console errors: {real[:3]}")
    finally:
        browser.close()
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Browser-level proof for the Wave PR Board")
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
        run_empty(pw, failures)
        run_unavailable(pw, failures)

    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1

    print("PROVEN: (a) console clean (b) #/prs table renders row-per-PR "
          "(c) CI status color-independent text (d) top blocker shown "
          "(e) PR title keyboard-focusable link (f) no javascript: href "
          "(g) empty state (no PRs) (h) gh-unavailable callout with reason")
    return 0


if __name__ == "__main__":
    sys.exit(main())
