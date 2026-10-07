#!/usr/bin/env python3
"""
INDEX: Derive the fail-closed gate-script stub list from hooks/pre-push-policy.sh
(instead of a hand-maintained list that forgets new gates).

Why this exists: tests/test_pre_push_policy.sh's "main() TTY guard (option B)"
fixture must stub every `check_*` gate script that resolves via
`gate_tool_status()` (the fail-CLOSED-on-missing-script path), or main() blocks
the whole fixture on a `<gate>_tool_missing` the moment a new gate is added and
not stubbed. Each of PR #872's five red CI rounds was a different hand-maintained
list a new gate forgot to join; this tool removes that one by deriving it from
the real source instead of re-typing it.

Algorithm (stdlib-only, regex over the shell source -- no bash execution):
  1. Walk hooks/pre-push-policy.sh top to bottom, splitting it into
     `check_<name>() { ... }` function bodies. Bodies are delimited by a
     `check_*() {` opening line and the next line that is exactly `}` at
     column 0 (the file's consistent style -- verified against all current
     check_* functions).
  2. Within a body, find `local <var>="$aesop_root/tools/<script>.py"`
     assignments (the uniform script-path pattern every gate uses).
  3. Within the same body, find `gate_tool_status "$aesop_root" "$<var>"`
     calls -- this is the fail-closed resolution path (see hooks/CLAUDE.md
     "Gate tool resolution (fail-closed)"). Only script vars actually passed
     to gate_tool_status matter: some checks (check_claudemd_headroom,
     check_generated_paths) fail OPEN on a missing script by design and must
     NOT be stubbed as fail-closed gates.
  4. Emit each matched `<script>` basename (without .py), sorted, deduped.

CLI: `python tools/gate_stub_list.py [HOOK_SCRIPT]` (default:
hooks/pre-push-policy.sh relative to repo root). Prints one script name per
line to stdout. Exit 0 on success, 2 if the hook script cannot be read.
"""
import re
import sys
from pathlib import Path

CHECK_FUNC_RE = re.compile(r"^check_[a-z_]+\(\)\s*\{\s*$")
VAR_ASSIGN_RE = re.compile(
    r'local\s+(\w+)\s*=\s*"\$aesop_root/tools/([A-Za-z0-9_]+)\.py"'
)
GATE_TOOL_STATUS_RE = re.compile(
    r'gate_tool_status\s+"\$aesop_root"\s+"\$(\w+)"'
)


def iter_check_function_bodies(text):
    """Yield (name, body_lines) for each check_*() { ... } function."""
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        m = CHECK_FUNC_RE.match(lines[i])
        if not m:
            i += 1
            continue
        func_line = lines[i]
        name = func_line.split("(")[0].strip()
        body = []
        j = i + 1
        while j < n and lines[j].rstrip() != "}":
            body.append(lines[j])
            j += 1
        yield name, body
        i = j + 1


def derive_stub_scripts(text):
    """Return a sorted, deduped list of tools/<script>.py basenames (no .py)
    that are resolved via the fail-closed gate_tool_status() path inside any
    check_* function in the given hook-script source text."""
    scripts = set()
    for _name, body in iter_check_function_bodies(text):
        body_text = "\n".join(body)
        var_to_script = {}
        for vm in VAR_ASSIGN_RE.finditer(body_text):
            var_to_script[vm.group(1)] = vm.group(2)
        for gm in GATE_TOOL_STATUS_RE.finditer(body_text):
            var = gm.group(1)
            if var in var_to_script:
                scripts.add(var_to_script[var])
    return sorted(scripts)


def main(argv):
    if len(argv) > 1:
        hook_path = Path(argv[1])
    else:
        repo_root = Path(__file__).resolve().parent.parent
        hook_path = repo_root / "hooks" / "pre-push-policy.sh"

    try:
        text = hook_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot read {hook_path}: {exc}", file=sys.stderr)
        return 2

    for script in derive_stub_scripts(text):
        print(script)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
