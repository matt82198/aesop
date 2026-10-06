#!/usr/bin/env python3
"""Node-suite HOME-isolation wiring check: every Node test-runner invocation site
must load tests/helpers/isolated-env.mjs and run under the isolation tripwire.
INDEX: Node-suite HOME-isolation wiring check (G8 cross-site drift guard): enumerates every site that invokes the Node test runner against `tests/*.test.mjs` -- raw `node ... --test ...` lines in `.github/workflows/*.yml` and `package.json`'s `test`/`test:node` scripts, resolving `npm run test:node`/`npm test` workflow lines through to package.json's own script text -- and fails if any resolved command is missing `--import ./tests/helpers/isolated-env.mjs` or the `tools/test_isolation_tripwire.py --` wrapper prefix. CLI: `--check [--json] [--root DIR]`; exit 0=clean/1=findings/2=error (fail-closed: no invocation sites found at all, or an unreadable input, is itself a finding -- an empty inventory must never read as a pass).

Why this exists (GAP, 2026-10-06): PR #864 ("wire isolated-env.mjs into every Node
test invocation") wired exactly two sites -- ci.yml's ubuntu "Run Node.js tests" step
(via npm run test:node) and ci.yml's windows-shard raw `node --import ... --test ...`
line -- but missed a THIRD site, .github/workflows/main-full.yml's own raw
`node --test ...` invocation, which predates #864 and was never touched by it. That
line ran the Node suite against the real, unisolated HOME/USERPROFILE on every
main-full run. It stayed accidentally green on windows-latest (USERPROFILE is an
ambient OS env var there regardless of wiring) and went RED on ubuntu-latest (no
such ambient var) on every completed main-full run from 2026-10-06 onward --
tests/isolated-home-tripwire.test.mjs's own tripwire assertion
("USERPROFILE must be set in the test process environment") caught it, but nothing
caught the SHAPE of the gap: wiring landing at some invocation sites and not others.
This tool is the fix for that shape, not just this one instance of it -- a fourth
invocation site added anywhere (a new workflow, a new npm script) without the same
wiring now fails closed instead of silently drifting.

Usage:
    python tools/node_harness_wiring_check.py --check [--json] [--root DIR]
"""

import argparse
import json
import re
import sys
from pathlib import Path

IMPORT_TOKEN = "--import ./tests/helpers/isolated-env.mjs"
IMPORT_TOKEN_ALT = "--import tests/helpers/isolated-env.mjs"
TRIPWIRE_TOKEN = "tools/test_isolation_tripwire.py --"
SUITE_GLOB = "tests/*.test.mjs"

# Matches a `run:` scalar (single-line `run: <cmd>` or the first line of a `run: |`
# block scalar; multi-line block scalars are handled by scanning subsequent indented
# lines until dedent, same approach ci_needs_skip_guard.py's siblings use for
# raw-text workflow scanning).
_RUN_LINE_RE = re.compile(r'^\s*run:\s*(.*)$')


class CheckError(Exception):
    """Raised for conditions that must fail closed (exit 2)."""


def _iter_run_commands(yaml_text, path):
    """Yield (line_no, command_text) for every `run:` step body in a workflow file.

    Text-based, not a YAML parser: `run:` block scalars (`run: |`) continue on
    following more-indented lines until dedent; a single-line `run: cmd` is just
    that line. Good enough for substring/regex matching on shell commands, which is
    all this gate needs.
    """
    lines = yaml_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _RUN_LINE_RE.match(line)
        if not m:
            i += 1
            continue
        run_indent = len(line) - len(line.lstrip(" "))
        rest = m.group(1).strip()
        start_line = i + 1  # 1-indexed
        if rest in ("|", ">", "|-", ">-", "|+", ">+"):
            body_lines = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip() == "":
                    body_lines.append(nxt)
                    j += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt_indent <= run_indent:
                    break
                body_lines.append(nxt)
                j += 1
            yield start_line, "\n".join(body_lines)
            i = j
        else:
            yield start_line, rest
            i += 1


def _is_node_suite_invocation(cmd):
    """True if `cmd` directly invokes the Node test runner against the real suite
    glob (as opposed to e.g. `npm run test:node`, which resolves separately)."""
    return SUITE_GLOB in cmd and re.search(r'\bnode\b', cmd) and "--test" in cmd


def _is_npm_test_node_invocation(cmd):
    stripped = cmd.strip()
    return stripped in ("npm run test:node", "npm test") or stripped.endswith(
        ("&& npm run test:node", "&& npm test")
    ) or re.search(r'(^|[;&|]\s*)npm (run test:node|test)\s*$', stripped) is not None


def _wiring_findings(cmd, site):
    findings = []
    has_import = IMPORT_TOKEN in cmd or IMPORT_TOKEN_ALT in cmd
    has_tripwire = TRIPWIRE_TOKEN in cmd
    if not has_import:
        findings.append(f"{site}: missing `{IMPORT_TOKEN}` -- Node suite runs against the REAL HOME/USERPROFILE")
    if not has_tripwire:
        findings.append(f"{site}: missing `{TRIPWIRE_TOKEN}` wrapper -- no independent proof the profile stayed untouched")
    return findings


def check(root):
    root = Path(root)
    workflows_dir = root / ".github" / "workflows"
    package_json_path = root / "package.json"

    findings = []
    sites_checked = 0

    # --- package.json's own test/test:node scripts ---
    npm_script_text = {}
    if package_json_path.is_file():
        try:
            pkg = json.loads(package_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckError(f"cannot parse {package_json_path}: {exc}")
        scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
        for key in ("test", "test:node"):
            script = scripts.get(key)
            if isinstance(script, str) and _is_node_suite_invocation(script):
                npm_script_text[key] = script
                sites_checked += 1
                findings.extend(_wiring_findings(script, f"package.json scripts.{key}"))
    else:
        raise CheckError(f"package.json not found at {package_json_path}")

    # --- every .github/workflows/*.yml run: step ---
    if not workflows_dir.is_dir():
        raise CheckError(f"workflows directory not found: {workflows_dir}")

    for wf_path in sorted(workflows_dir.glob("*.yml")):
        try:
            text = wf_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CheckError(f"cannot read {wf_path}: {exc}")

        for line_no, cmd in _iter_run_commands(text, wf_path):
            site = f"{wf_path.relative_to(root)}:{line_no}"
            if _is_node_suite_invocation(cmd):
                sites_checked += 1
                findings.extend(_wiring_findings(cmd, site))
            elif _is_npm_test_node_invocation(cmd):
                # Resolves through to package.json -- already checked above. Still
                # counts as a site so an npm-only workflow doesn't read as "zero
                # sites found" (fail-closed check below).
                sites_checked += 1
                resolved_key = "test:node" if "test:node" in cmd else "test"
                if resolved_key not in npm_script_text:
                    findings.append(
                        f"{site}: invokes `npm run {resolved_key}` but package.json "
                        f"scripts.{resolved_key} does not itself invoke the Node suite "
                        f"against {SUITE_GLOB} -- cannot verify wiring"
                    )

    if sites_checked == 0:
        # An empty inventory must never read as a pass -- this gate exists precisely
        # because a real invocation site silently fell outside coverage once already.
        raise CheckError(
            "found ZERO Node test-runner invocation sites across package.json and "
            ".github/workflows/*.yml -- either the suite was removed (update this "
            "gate deliberately) or this gate's detection regexes no longer match "
            "the real invocation shape (fail closed, never silently pass)"
        )

    return findings, sites_checked


def main():
    parser = argparse.ArgumentParser(description="Node-suite HOME-isolation wiring check")
    parser.add_argument("--check", action="store_true", help="run the check (required)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args()

    if not args.check:
        parser.print_help()
        return 2

    try:
        findings, sites_checked = check(args.root)
    except CheckError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"sites_checked": sites_checked, "findings": findings}))
    else:
        print(f"Node harness wiring check: {sites_checked} invocation site(s) checked")
        for finding in findings:
            print(f"FAIL: {finding}", file=sys.stderr)
        if not findings:
            print("OK: every site wires tests/helpers/isolated-env.mjs + the isolation tripwire")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
