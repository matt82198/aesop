#!/usr/bin/env python3
"""Exhaustive gate inventory: every gate tool must have at least one invoker.

Complements tools/verify_gates_wired.py. That tool is MARKER-DRIVEN: it discovers
gates only from lines in tools/CLAUDE.md and tests/CLAUDE.md that carry the literal
"Guardrail G" marker (or the "verify_*.py are mandatory CI gates" section), so a gate
tool that nobody ever documented as a guardrail is invisible to it. This tool is
FILESYSTEM-DRIVEN: it enumerates every gate-shaped tool on disk and demands that each
resolve to a real invoker.

Two independent axes:

  Axis 1 -- orphan gates.
    Enumerate tools/{*_lint,*_check,*_gate,verify_*}.py via `git ls-files`. Each must
    resolve to >= 1 invoker among: a .github/workflows/*.yml run line, a git hook under
    hooks/, bin/cli.js, an npm script in package.json, another first-party module that
    invokes it (one-hop import/subprocess scan), or an allowlist entry carrying a
    non-empty REASON string. Anything unclassified is a finding.

  Axis 2 -- documented-but-unwired pre-push checks.
    verify_gates_wired.py explicitly EXCLUDES pre-push gates and never reads
    hooks/CLAUDE.md, so nothing asserts the symmetric property. Here every
    `check_*()` documented in hooks/CLAUDE.md must have a real CALL SITE in
    hooks/pre-push-policy.sh -- not merely a definition, and matched on word
    boundaries (this repo has had three prior substring-matching defects).

Exit codes: 0 = every gate classified and every documented check invoked;
1 = findings; 2 = error (missing/unreadable inputs -- fail-closed).

Usage:
    python tools/gate_inventory.py --check [--json] [--root DIR] [--allowlist PATH]
"""

import argparse
import json
import os
import re
import subprocess
import sys

# Gate-shaped tool filename patterns (relative to tools/).
GATE_GLOBS = [
    "tools/*_lint.py",
    "tools/*_check.py",
    "tools/*_gate.py",
    "tools/verify_*.py",
]

DEFAULT_ALLOWLIST = "tools/gate-inventory-allowlist.json"

# An allowlist reason must actually say something.
MIN_REASON_LEN = 12

# Directories scanned for one-hop tool-to-tool invocation. Deliberately excludes
# tests/ and docs/ -- a test importing a tool is not an invoker, and a doc that
# merely mentions a tool is not an invoker either. That distinction is the whole
# point of this gate.
ONE_HOP_DIRS = ["tools", "driver", "monitor", "daemons", "ui", "mcp", "bin", "scan"]
ONE_HOP_EXTS = (".py", ".js", ".mjs", ".sh")

WORKFLOW_DIR = os.path.join(".github", "workflows")
HOOKS_DIR = "hooks"
CLI_JS = os.path.join("bin", "cli.js")
PACKAGE_JSON = "package.json"

HOOKS_CLAUDEMD = os.path.join("hooks", "CLAUDE.md")
PREPUSH_SH = os.path.join("hooks", "pre-push-policy.sh")


class InventoryError(Exception):
    """Fail-closed error: an input this gate depends on is missing or unreadable."""


def read_text(path):
    """Read a UTF-8 text file, raising InventoryError on any failure (fail-closed)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        raise InventoryError("cannot read %s: %s" % (path, exc))


def git_ls_files(root, patterns):
    """Return sorted repo-relative paths matching the given git pathspecs."""
    cmd = [_git_bin(), "-C", root, "ls-files", "--"] + list(patterns)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InventoryError("git ls-files failed: %s" % exc)
    if proc.returncode != 0:
        raise InventoryError(
            "git ls-files exited %d: %s" % (proc.returncode, (proc.stderr or "").strip())
        )
    out = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line:
            out.append(line.replace("\\", "/"))
    return sorted(set(out))


def _git_bin():
    return os.environ.get("GIT_BINARY", "git")


def iter_files(root, rel_dir, exts):
    """Yield repo-relative paths of files under rel_dir with one of exts."""
    base = os.path.join(root, rel_dir)
    if not os.path.isdir(base):
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
        for name in sorted(filenames):
            if name.endswith(exts):
                full = os.path.join(dirpath, name)
                yield os.path.relpath(full, root).replace("\\", "/")


def build_reference_index(root):
    """Map each invoker-surface path to its text content.

    Only surfaces that can actually RUN a tool are indexed. Markdown, JSON
    baselines and tests/ are deliberately absent.
    """
    index = {}

    for rel in iter_files(root, WORKFLOW_DIR, (".yml", ".yaml")):
        index[rel] = read_text(os.path.join(root, rel))

    for rel in iter_files(root, HOOKS_DIR, (".sh", ".mjs", ".js")):
        index[rel] = read_text(os.path.join(root, rel))

    cli_path = os.path.join(root, CLI_JS)
    if os.path.isfile(cli_path):
        # Key on the POSIX form so the later bin/ one-hop walk does not add a
        # second, backslash-keyed copy of the same file on Windows.
        index[CLI_JS.replace("\\", "/")] = read_text(cli_path)

    pkg_path = os.path.join(root, PACKAGE_JSON)
    if os.path.isfile(pkg_path):
        index[PACKAGE_JSON] = read_text(pkg_path)

    for rel_dir in ONE_HOP_DIRS:
        for rel in iter_files(root, rel_dir, ONE_HOP_EXTS):
            if rel not in index:
                index[rel] = read_text(os.path.join(root, rel))

    return index


def classify_surface(rel_path):
    """Bucket an invoker surface path into a human-readable invoker kind."""
    norm = rel_path.replace("\\", "/")
    if norm.startswith(".github/workflows/"):
        return "ci-workflow"
    if norm.startswith("hooks/"):
        return "git-hook"
    if norm == CLI_JS.replace("\\", "/"):
        return "cli"
    if norm == PACKAGE_JSON:
        return "npm-script"
    return "tool-chain"


def references_tool(text, basename, stem):
    """True when text contains a plausible INVOCATION reference to the tool.

    Matches a `tools/<basename>` path, a bare `<basename>` filename literal, or a
    Python import of the module (`import <stem>`, `from <stem> import`,
    `from tools.<stem> import`). All matched on word boundaries.
    """
    patterns = [
        r"tools[/\\]" + re.escape(basename),
        r"(?<![\w.-])" + re.escape(basename),
        r"^\s*import\s+" + re.escape(stem) + r"\b",
        r"^\s*from\s+" + re.escape(stem) + r"\s+import\b",
        r"^\s*from\s+tools\." + re.escape(stem) + r"\s+import\b",
        r"\bimport_module\(\s*[\"']" + re.escape(stem) + r"[\"']\s*\)",
    ]
    for pat in patterns:
        if re.search(pat, text, re.MULTILINE):
            return True
    return False


def strip_comments_for(rel_path, text):
    """Strip comments from an invoker surface before scanning it for references.

    A tool NAMED IN A COMMENT is not an invoker. ci.yml, for example, carries
    `# PyYAML is required by tools/ci_workflow_lint.py`; counting that as wiring
    would let a genuinely orphaned gate pass on the strength of a stray remark --
    exactly the false-pass this gate exists to prevent.
    """
    if rel_path.endswith((".yml", ".yaml", ".sh", ".py")):
        return strip_shell_comments(text)
    if rel_path.endswith((".js", ".mjs")):
        return strip_slash_comments(text)
    return text


def strip_slash_comments(text):
    """Drop `//` line comments (JS/MJS). Block comments are left alone."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("//"):
            out.append("")
            continue
        idx = line.find("//")
        if idx >= 0 and "://" not in line[max(0, idx - 1):idx + 3]:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def strip_shell_comments(text):
    """Drop full-line and trailing shell comments so a mention in prose does not count."""
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            out.append("")
            continue
        # Trailing comment: naive but safe here (hook has no '#' inside string literals
        # that also carries a check_ name).
        idx = line.find(" #")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def load_allowlist(path):
    """Parse the allowlist. Returns {basename: reason}. Fail-closed on bad JSON."""
    if not os.path.isfile(path):
        return {}
    raw = read_text(path)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise InventoryError("allowlist %s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise InventoryError("allowlist %s must be a JSON object" % path)
    entries = data.get("entries", {})
    if not isinstance(entries, dict):
        raise InventoryError("allowlist %s: 'entries' must be an object" % path)
    out = {}
    for name, val in entries.items():
        if isinstance(val, dict):
            reason = val.get("reason", "")
        else:
            reason = val
        out[str(name)] = reason if isinstance(reason, str) else ""
    return out


def run_axis1(root, allowlist_path):
    """Axis 1: every gate tool resolves to at least one invoker."""
    gates = git_ls_files(root, GATE_GLOBS)
    if not gates:
        raise InventoryError("no gate tools discovered -- inventory cannot be empty")

    allowlist = load_allowlist(os.path.join(root, allowlist_path)
                              if not os.path.isabs(allowlist_path) else allowlist_path)
    index = build_reference_index(root)

    resolved = []
    findings = []

    for gate_rel in gates:
        basename = os.path.basename(gate_rel)
        stem = basename[:-3] if basename.endswith(".py") else basename

        invokers = []
        for surface, text in sorted(index.items()):
            if surface == gate_rel:
                continue  # a tool is not its own invoker
            if references_tool(strip_comments_for(surface, text), basename, stem):
                invokers.append((classify_surface(surface), surface))

        if invokers:
            kind, surface = invokers[0]
            resolved.append(
                {
                    "tool": gate_rel,
                    "status": "invoked",
                    "invoker_kind": kind,
                    "invokers": [s for _, s in invokers],
                }
            )
            continue

        if basename in allowlist:
            reason = (allowlist[basename] or "").strip()
            if len(reason) < MIN_REASON_LEN:
                findings.append(
                    {
                        "tool": gate_rel,
                        "status": "allowlist-no-reason",
                        "detail": (
                            "allowlist entry for %s has no usable reason "
                            "(need >= %d chars explaining why it has no invoker)"
                            % (basename, MIN_REASON_LEN)
                        ),
                    }
                )
            else:
                resolved.append(
                    {
                        "tool": gate_rel,
                        "status": "allowlisted",
                        "invoker_kind": "allowlist",
                        "reason": reason,
                    }
                )
            continue

        findings.append(
            {
                "tool": gate_rel,
                "status": "orphan",
                "detail": (
                    "no CI workflow, git hook, CLI entry, npm script or first-party "
                    "module invokes %s" % basename
                ),
            }
        )

    return {"total": len(gates), "resolved": resolved, "findings": findings}


DOC_CHECK_RE = re.compile(r"`(check_[a-z0-9_]+)\(\)`")
SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)


def extract_documented_checks(claudemd_text):
    """Pull `check_*()` names out of the pre-push-policy.sh section of hooks/CLAUDE.md."""
    # Isolate the '## pre-push-policy.sh' section so checks documented for other
    # hooks are not attributed to the pre-push hook.
    start = claudemd_text.find("## pre-push-policy.sh")
    if start < 0:
        raise InventoryError(
            "%s has no '## pre-push-policy.sh' section -- cannot verify axis 2"
            % HOOKS_CLAUDEMD
        )
    rest = claudemd_text[start + len("## pre-push-policy.sh"):]
    nxt = SECTION_RE.search(rest)
    section = rest[: nxt.start()] if nxt else rest

    names = []
    for match in DOC_CHECK_RE.finditer(section):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


def find_call_sites(hook_text, name):
    """Return line numbers where `name` is CALLED (word-boundary), not defined.

    A definition line looks like `name() {`. Everything else that mentions the bare
    identifier on a word boundary counts as a call site. Word boundaries matter:
    substring matching would let `check_test_coverage` be satisfied by a
    `check_test_coverage_helper` that nothing calls -- the exact defect class this
    repo has hit three times.
    """
    pattern = re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])")
    def_pattern = re.compile(r"^\s*" + re.escape(name) + r"\s*\(\s*\)\s*\{")

    sites = []
    for lineno, line in enumerate(strip_shell_comments(hook_text).splitlines(), 1):
        if def_pattern.search(line):
            continue
        if pattern.search(line):
            sites.append(lineno)
    return sites


def run_axis2(root):
    """Axis 2: every pre-push check documented in hooks/CLAUDE.md is actually invoked."""
    claudemd_path = os.path.join(root, HOOKS_CLAUDEMD)
    hook_path = os.path.join(root, PREPUSH_SH)
    if not os.path.isfile(claudemd_path):
        raise InventoryError("missing %s" % HOOKS_CLAUDEMD)
    if not os.path.isfile(hook_path):
        raise InventoryError("missing %s" % PREPUSH_SH)

    claudemd_text = read_text(claudemd_path)
    hook_text = read_text(hook_path)

    documented = extract_documented_checks(claudemd_text)
    if not documented:
        raise InventoryError(
            "%s documents no check_*() functions -- axis 2 would be vacuously green"
            % HOOKS_CLAUDEMD
        )

    resolved = []
    findings = []
    for name in documented:
        sites = find_call_sites(hook_text, name)
        if sites:
            resolved.append({"check": name, "status": "invoked", "lines": sites})
        else:
            findings.append(
                {
                    "check": name,
                    "status": "documented-not-invoked",
                    "detail": (
                        "%s is documented in %s but has no call site in %s"
                        % (name, HOOKS_CLAUDEMD, PREPUSH_SH)
                    ),
                }
            )

    return {"total": len(documented), "resolved": resolved, "findings": findings}


def render_text(report):
    lines = []
    a1 = report["axis1"]
    a2 = report["axis2"]

    lines.append("Axis 1 -- gate tool inventory (%d tools)" % a1["total"])
    by_kind = {}
    for item in a1["resolved"]:
        by_kind[item["invoker_kind"]] = by_kind.get(item["invoker_kind"], 0) + 1
    for kind in sorted(by_kind):
        lines.append("  %-14s %d" % (kind, by_kind[kind]))
    if a1["findings"]:
        lines.append("  FINDINGS: %d" % len(a1["findings"]))
        for f in a1["findings"]:
            lines.append("    [%s] %s" % (f["status"].upper(), f["tool"]))
            lines.append("        %s" % f["detail"])
    else:
        lines.append("  OK: every gate tool has an invoker")

    lines.append("")
    lines.append("Axis 2 -- documented pre-push checks (%d documented)" % a2["total"])
    if a2["findings"]:
        lines.append("  FINDINGS: %d" % len(a2["findings"]))
        for f in a2["findings"]:
            lines.append("    [%s] %s" % (f["status"].upper(), f["check"]))
            lines.append("        %s" % f["detail"])
    else:
        lines.append("  OK: every documented check has a call site in the hook")

    lines.append("")
    total = len(a1["findings"]) + len(a2["findings"])
    lines.append("RESULT: %s (%d finding(s))" % ("FAIL" if total else "PASS", total))
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gate_inventory.py",
        description=(
            "Exhaustive gate inventory: every tools/{*_lint,*_check,*_gate,verify_*}.py "
            "must have an invoker, and every pre-push check documented in "
            "hooks/CLAUDE.md must actually be called in hooks/pre-push-policy.sh."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="run both axes (default behaviour; read-only)",
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    parser.add_argument("--root", default=None, help="repo root (default: cwd)")
    parser.add_argument(
        "--allowlist",
        default=DEFAULT_ALLOWLIST,
        help="path to the reviewed allowlist (default: %s)" % DEFAULT_ALLOWLIST,
    )
    return parser


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on unknown flags, which is already our error code.
        return int(exc.code) if exc.code else 0

    root = os.path.abspath(args.root or os.getcwd())
    if not os.path.isdir(root):
        print("ERROR: root is not a directory: %s" % root, file=sys.stderr)
        return 2

    try:
        report = {
            "axis1": run_axis1(root, args.allowlist),
            "axis2": run_axis2(root),
        }
    except InventoryError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    findings = len(report["axis1"]["findings"]) + len(report["axis2"]["findings"])
    report["findings"] = findings
    report["ok"] = findings == 0

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
