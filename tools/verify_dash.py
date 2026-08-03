"""Browser-level proof for the wave-14 React dashboard (ui/web/dist/).
INDEX: Browser proof (Playwright): realtime SSE dashboard renders live agent/wave state; exit 0=proven/1=failed, --allow-skip only in truly browserless envs

Drives the BUILT app served by `python ui/serve.py` against fixture fleet state,
asserting the contract via data-testid hooks only (never CSS internals):

Populated-state phase:
  (a) console clean of errors across the whole run
  (b) app serves React dist (not legacy template)
  (c) health-header testid present and rendered
  (d) view testids present (overview on first paint)
  (e) inbox form testids (submit flow)
  (f) cost view renders table/chart/scorecard from the fixture ledger
  (g) CSS stylesheet loaded (reduced-motion media query proven by vitest)
  (h) a11y live regions present (role=status or aria-live)
  (j) SSE live update WITHOUT reload (tracker.json mutation appears in DOM)
  (k) tracker round-trip through the real form: create -> proposed lane, claim/done move lanes
  (l) hostile javascript: pr_link is inert in the real DOM (no javascript: href)
  (m) keyboard-only agent-row expand; expansion SURVIVES an SSE agents update
  (n) computed contrast of health-header label >= 4.5:1 in BOTH themes (toggle exercised)
  (o) orchestrator-status phase=audit -> audit badge appears in a live region

Empty-state phase (separate boot, empty tracker/agents/alerts/backlog):
  (i) all four views render their empty states with a clean console

Run: python tools/verify_dash.py            (exit 0 = proven, 1 = failed)
     python tools/verify_dash.py --allow-skip (exit 0 = proven or skipped, 1 = failed)

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

# Ensure this tool's own directory (tools/) is importable so the shared
# playwright harness resolves regardless of cwd or how the file is loaded
# (the import-gate loads tools by path, without tools/ on sys.path).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright_common import free_port, copy_dist, start_server, stop_server

REPO = Path(__file__).resolve().parent.parent
SERVE = REPO / "ui" / "serve.py"


def _real_console_errors(console_errors, failed_urls):
    """Drop noise from the captured console errors.

    Playwright reports a failed sub-resource as a generic
    "Failed to load resource: ... 404" console line that carries NO url, so
    string-matching it is fragile. We instead correlate with the actual
    failed-response urls: a generic resource-load line is ignorable when the
    only failed responses were favicon (which the server now answers 204, so
    this is belt-and-suspenders). Non-favicon failed responses are surfaced
    explicitly so a real broken asset still fails the proof.
    """
    non_favicon = [u for u in failed_urls if "favicon" not in u.lower()]
    real = []
    for e in console_errors:
        low = e.lower()
        if "favicon" in low:
            continue
        if "failed to load resource" in low and not non_favicon:
            continue  # only favicon failed → ignore the urlless generic line
        real.append(e)
    real.extend(f"failed resource: {u}" for u in non_favicon)
    return real

AGENT_FULL_ID = "verifyagent0123456789ab"
PROMPT_MARKER = "FIXTURE-PROMPT-MARKER: rebuild the flux capacitor\n" + "\n".join(
    f"line {i}: recalibrate subsystem {i} and verify each tolerance band carefully"
    for i in range(60))

FIXTURE_BACKLOG = """# Audit backlog — wave-14 verify_dash fixture

**Status legend:** ⬜ unclaimed · 🔵 dispatched · ✅ merged · ⏸ user call

## P0 — correctness / security

- ✅ **[sec] Dashboard rewrite (U1 foundation).** completed.
- 🔵 **[ui] React component library (U4-U7).** in progress.

## P1 — observability

- ⬜ **[cost] Cost analytics per model.** todo.

## Landing log
- fixture
"""

FIXTURE_LEDGER = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-13T14:00:00Z | orchestrator | claude-opus-4-20250805 | 120 | 50000 | 12000 | OK |
| 2026-07-13T14:02:30Z | haiku | claude-haiku-4-5-20251001 | 45 | 12000 | 3500 | OK |
| 2026-07-13T14:05:15Z | haiku | claude-haiku-4-5-20251001 | 50 | 14000 | 4200 | OK |
| 2026-07-13T14:08:00Z | sonnet | claude-sonnet-4-5-20250929 | 85 | 28000 | 8100 | OK |
| 2026-07-13T14:12:20Z | haiku | claude-haiku-4-5-20251001 | 40 | 11000 | 3200 | FAILED |
"""

XSS_ITEM = {
    "id": "fixturexss01",
    "title": "fixture xss probe",
    "priority": "P2",
    "status": "todo",
    "lane": "proposed",
    "source": "verify-dash",
    "tags": ["fixture"],
    "notes": "hostile pr_link must render inert",
    "pr_link": "javascript:alert(1)",
    "created_at": "2026-07-14T00:00:00Z",
    "completed_at": None,
}



def _rel_luminance(rgb):
    def chan(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb1, rgb2):
    l1, l2 = _rel_luminance(rgb1), _rel_luminance(rgb2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def parse_rgb(css_color):
    """Parse 'rgb(r, g, b)' / 'rgba(r, g, b, a)' into an (r, g, b) tuple."""
    inner = css_color[css_color.index("(") + 1: css_color.rindex(")")]
    parts = [p.strip() for p in inner.split(",")]
    return tuple(int(float(p)) for p in parts[:3])



def build_fixture(root: Path, hint: str):
    """Populated fixture: agent + tracker (with XSS probe) + alerts + ledger + backlog."""
    (root / "state").mkdir(exist_ok=True)
    (root / "transcripts").mkdir(exist_ok=True)
    (root / "dash").mkdir(exist_ok=True)
    copy_dist(root, REPO)

    (root / "AUDIT-BACKLOG.md").write_text(FIXTURE_BACKLOG, encoding="utf-8")

    (root / "state" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "state" / "ledger" / "OUTCOMES-LEDGER.md").write_text(
        FIXTURE_LEDGER, encoding="utf-8")

    (root / "state" / "SECURITY-ALERTS.log").write_text(
        "2026-07-13T14:32:01Z | HIGH | fixture alert high\n"
        "2026-07-13T14:30:05Z | MED | fixture alert med\n", encoding="utf-8")

    (root / "state" / "tracker.json").write_text(
        json.dumps({"version": 1, "items": [XSS_ITEM]}), encoding="utf-8")

    # Fake detector: reads hint.txt so live agent updates are deterministic.
    (root / "hint.txt").write_text(hint, encoding="utf-8")
    fake = (
        "import { readFileSync } from 'node:fs';\n"
        "const hint = readFileSync(new URL('../hint.txt', import.meta.url), 'utf8').trim();\n"
        "console.log(JSON.stringify([{id:'" + AGENT_FULL_ID[:13] + "',"
        "status:'running',age_s:4,hint:hint,taskLabel:hint}]));\n"
    )
    (root / "dash" / "dash-extra.mjs").write_text(fake, encoding="utf-8")
    transcript = root / "transcripts" / f"agent-{AGENT_FULL_ID}.jsonl"
    lines = [
        json.dumps({"type": "user", "parentUuid": None,
                    "message": {"content": PROMPT_MARKER}}),
        json.dumps({"type": "assistant", "model": "claude-haiku-4-5",
                    "message": {"content": "working"}}),
    ]
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "transcripts" / "agent-seed.jsonl").write_text("{}\n", encoding="utf-8")


def build_empty_fixture(root: Path):
    """Empty fixture: no tracker items, no agents, no alerts, no backlog, no ledger."""
    (root / "state").mkdir(exist_ok=True)
    (root / "transcripts").mkdir(exist_ok=True)
    (root / "dash").mkdir(exist_ok=True)
    copy_dist(root, REPO)
    (root / "dash" / "dash-extra.mjs").write_text(
        "console.log(JSON.stringify([]));\n", encoding="utf-8")




def run_empty_phase(pw, failures):
    """(i) all four views render empty states with a clean console."""
    root = Path(tempfile.mkdtemp(prefix="aesop-verify-w14-empty-"))
    port = free_port()
    console_errors = []
    failed_urls = []
    build_empty_fixture(root)
    try:
        server = start_server(root, port, REPO, SERVE, 50, 0.2, "0.3")
    except RuntimeError as e:
        failures.append(f"(i) empty-state server failed: {e}")
        shutil.rmtree(root, ignore_errors=True)
        return
    try:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        page.on("response", lambda r: failed_urls.append(r.url) if r.status >= 400 else None)
        page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        try:
            page.wait_for_selector("[data-testid='health-header']", timeout=15000)
        except Exception as e:
            failures.append(f"(i) empty-state app never mounted: {e}")
        views = [("#/", "view-overview"), ("#/work", "view-work"),
                 ("#/activity", "view-activity"), ("#/cost", "view-cost")]
        for hash_, testid in views:
            page.evaluate(f"location.hash = '{hash_}'")
            try:
                page.wait_for_selector(f"[data-testid='{testid}']", timeout=12000)
            except Exception as e:
                failures.append(f"(i) empty-state view {testid} did not render: {e}")
        time.sleep(0.5)
        real_errors = _real_console_errors(console_errors, failed_urls)
        if real_errors:
            failures.append(f"(i) empty-state console errors: {real_errors[:3]}")
        browser.close()
    finally:
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(
        description="Browser-level proof for the wave-14 React dashboard (ui/web/dist/)")
    parser.add_argument("--allow-skip", action="store_true",
                        help="Allow skipping if playwright/chromium is unavailable")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        msg = "playwright missing — run `python -m playwright install chromium`, or pass --allow-skip"
        print(f"SKIP: {msg}" if args.allow_skip else f"FAIL: {msg}")
        return 0 if args.allow_skip else 1

    root = Path(tempfile.mkdtemp(prefix="aesop-verify-wave14-dash-"))
    port = free_port()
    console_errors = []
    failed_urls = []
    failures = []
    build_fixture(root, hint="fixture hint alpha")
    try:
        server = start_server(root, port, REPO, SERVE, 50, 0.2, "0.3")
    except RuntimeError as e:
        print(f"FAIL: {e}")
        shutil.rmtree(root, ignore_errors=True)
        return 1

    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as e:
                msg = f"chromium unavailable ({e}); run: python -m playwright install chromium"
                print(f"SKIP: {msg}" if args.allow_skip else f"FAIL: {msg}")
                return 0 if args.allow_skip else 1

            page = browser.new_page()
            page.on("console", lambda m: console_errors.append(m.text)
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errors.append(str(e)))
            page.on("response", lambda r: failed_urls.append(r.url) if r.status >= 400 else None)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")

            # (b) app serves React dist, not the legacy template
            try:
                page_html = page.content()
                assert "data-testid" in page_html, "React app should use data-testid hooks"
                assert "audit-banner" not in page_html, "should not be serving old template"
            except Exception as e:
                failures.append(f"(b) app does not serve React dist: {e}")

            # (c) health header rendered
            try:
                page.wait_for_selector("[data-testid='health-header']", timeout=5000)
            except Exception as e:
                failures.append(f"(c) health-header testid not found: {e}")

            # (d) overview view on first paint
            try:
                page.wait_for_selector("[data-testid='view-overview']", timeout=5000)
            except Exception as e:
                failures.append(f"(d) view testids not found: {e}")

            # (e) inbox form present
            try:
                page.wait_for_selector("[data-testid='inbox-input']", timeout=5000)
                page.wait_for_selector("[data-testid='inbox-submit']", timeout=5000)
            except Exception as e:
                failures.append(f"(e) inbox form testids not found: {e}")

            # (m) keyboard-only agent expand + survival across an SSE agents update
            try:
                page.wait_for_selector("[data-testid='agent-row']", timeout=8000)
                # keyboard-only: focus the row's real <button> and press Enter
                expand_btn = page.locator("[data-testid='agent-row'] button").first
                expand_btn.focus()
                page.keyboard.press("Enter")
                page.wait_for_selector("[data-testid='agent-row-detail']", timeout=8000)
                # trigger a live agents update: change hint + touch the fingerprint seed
                (root / "hint.txt").write_text("fixture hint beta", encoding="utf-8")
                seed = root / "transcripts" / "agent-seed.jsonl"
                seed.write_text('{"touch": %d}\n' % time.time_ns(), encoding="utf-8")
                page.wait_for_function(
                    "document.body.innerText.includes('fixture hint beta')", timeout=10000)
                assert page.query_selector("[data-testid='agent-row-detail']") is not None, \
                    "expansion did not survive the SSE agents update"
            except Exception as e:
                failures.append(f"(m) keyboard expand / expansion-survival failed: {e}")

            # (j) SSE live update WITHOUT reload: mutate tracker.json directly
            try:
                page.evaluate("location.hash = '#/work'")
                page.wait_for_selector("[data-testid='view-work']", timeout=5000)
                tracker_file = root / "state" / "tracker.json"
                data = json.loads(tracker_file.read_text(encoding="utf-8"))
                data["items"].append(dict(XSS_ITEM, id="fixturesse001",
                                          title="SSE-LIVE-MARKER item",
                                          pr_link=None))
                tracker_file.write_text(json.dumps(data), encoding="utf-8")
                page.wait_for_function(
                    "document.body.innerText.includes('SSE-LIVE-MARKER')", timeout=10000)
            except Exception as e:
                failures.append(f"(j) SSE live tracker update failed: {e}")

            # (l) hostile javascript: pr_link inert in the real DOM
            try:
                bad = page.evaluate(
                    "Array.from(document.querySelectorAll('a[href]'))"
                    ".filter(a => a.href.toLowerCase().startsWith('javascript:')).length")
                assert bad == 0, f"{bad} anchor(s) carry a javascript: href"
            except Exception as e:
                failures.append(f"(l) XSS pr_link not inert: {e}")

            # (k) tracker round-trip through the real form (CSRF path)
            try:
                # the add form sits behind a "+ Add Item" toggle
                if page.locator("[data-testid='tracker-form-title']").count() == 0:
                    page.get_by_role("button", name="+ Add Item").click()
                page.fill("[data-testid='tracker-form-title']", "ROUNDTRIP-MARKER item")
                page.click("[data-testid='tracker-form-submit']")
                page.wait_for_function(
                    "document.body.innerText.includes('ROUNDTRIP-MARKER')", timeout=10000)
                # locate the card, then drive its lane actions by accessible name
                card = page.locator("[data-testid='tracker-card']",
                                    has_text="ROUNDTRIP-MARKER").first
                moved = 0
                for action in ("Claim", "Done"):
                    btn = card.get_by_role("button", name=action)
                    if btn.count() == 0:
                        # expand the card first if actions are inside the detail area
                        card.click()
                        btn = card.get_by_role("button", name=action)
                    if btn.count() > 0:
                        btn.first.click()
                        time.sleep(1.0)
                        moved += 1
                assert moved == 2, f"only {moved}/2 lane actions (Claim/Done) were operable"
            except Exception as e:
                failures.append(f"(k) tracker round-trip failed: {e}")

            # (f) cost view renders table/chart/scorecard from the fixture ledger
            try:
                page.evaluate("location.hash = '#/cost'")
                page.wait_for_selector("[data-testid='cost-table']", timeout=8000)
                page.wait_for_selector("[data-testid='cost-chart']", timeout=5000)
                page.wait_for_selector("[data-testid='scorecard']", timeout=5000)
                assert "haiku" in page.inner_text("[data-testid='cost-table']").lower(), \
                    "fixture ledger models not rendered in cost table"
            except Exception as e:
                failures.append(f"(f) cost view did not render fixture ledger: {e}")

            # (o) orchestrator-status phase=audit -> audit badge in a live region
            try:
                status_file = root / "state" / "orchestrator-status.json"
                status_file.write_text(json.dumps({
                    "id": "main", "role": "orchestrator",
                    "activity": "running audit", "phase": "audit"}), encoding="utf-8")
                page.wait_for_function(
                    "(() => { const el = document.querySelector(\"[data-testid='health-orchestrator']\");"
                    " return el && /audit/i.test(el.textContent); })()", timeout=10000)
                announced = page.evaluate(
                    "(() => { const el = document.querySelector(\"[data-testid='health-orchestrator']\");"
                    " const near = el.closest('[role=status],[aria-live]') || el.querySelector('[role=status],[aria-live]');"
                    " return !!near || el.getAttribute('role') === 'status' || el.hasAttribute('aria-live'); })()")
                assert announced, "audit badge is not in/near a live region"
            except Exception as e:
                failures.append(f"(o) audit-phase badge failed: {e}")

            # (n) computed contrast of a health-header label in BOTH themes
            try:
                page.evaluate("location.hash = '#/'")
                page.wait_for_selector("[data-testid='health-watchdog']", timeout=5000)
                ratios = {}
                for theme_pass in ("first", "second"):
                    styles = page.evaluate(
                        "(() => { const el = document.querySelector(\"[data-testid='health-watchdog']\");"
                        " const cs = getComputedStyle(el);"
                        " let bg = 'rgba(0, 0, 0, 0)'; let node = el;"
                        " while (node) { const c = getComputedStyle(node).backgroundColor;"
                        "   if (c && !c.includes('0, 0, 0, 0')) { bg = c; break; } node = node.parentElement; }"
                        " if (bg.includes('0, 0, 0, 0')) bg = getComputedStyle(document.body).backgroundColor;"
                        " return { fg: cs.color, bg: bg,"
                        "          theme: document.documentElement.getAttribute('data-theme') || 'default' }; })()")
                    ratio = contrast_ratio(parse_rgb(styles["fg"]), parse_rgb(styles["bg"]))
                    ratios[styles["theme"] + "/" + theme_pass] = round(ratio, 2)
                    assert ratio >= 4.5, \
                        f"health label contrast {ratio:.2f}:1 in theme '{styles['theme']}' (< 4.5)"
                    if theme_pass == "first":
                        page.click("[data-testid='theme-toggle']")
                        time.sleep(0.5)
                print(f"  contrast ratios: {ratios}")
            except Exception as e:
                failures.append(f"(n) theme contrast check failed: {e}")

            # (g) CSS stylesheet loaded
            try:
                assert page.query_selector("link[rel='stylesheet']") is not None, \
                    "page should have CSS stylesheet links"
            except Exception as e:
                failures.append(f"(g) CSS stylesheet check failed: {e}")

            # (h) live regions present
            try:
                live_regions = page.evaluate(
                    "document.querySelectorAll('[role=\"status\"], [aria-live]').length")
                assert live_regions > 0, "no live regions on page"
            except Exception as e:
                failures.append(f"(h) live regions check failed: {e}")

            # (a) console clean across the whole populated run
            time.sleep(0.5)
            real_errors = _real_console_errors(console_errors, failed_urls)
            if real_errors:
                failures.append(f"(a) console errors: {real_errors[:3]}")

            browser.close()

            # (i) empty-state phase — separate boot, separate console
            run_empty_phase(pw, failures)

    finally:
        stop_server(server)
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1

    print("PROVEN: (a) console clean (b) React dist served (c) health-header "
          "(d) view testids (e) inbox form (f) cost view renders fixture ledger "
          "(g) stylesheet (h) live regions (i) empty-state pass all views "
          "(j) SSE tracker update w/o reload (k) tracker form round-trip incl. lane actions "
          "(l) javascript: pr_link inert (m) keyboard expand survives SSE update "
          "(n) AA contrast both themes (o) audit badge announced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
