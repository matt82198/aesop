#!/usr/bin/env python3
"""
tools.spec_contract_validator -- Guardrail G4: spec-contract validator for agent dispatch calls.

AST-scans Python source for agent-dispatch call sites -- calls named `agent(...)` /
`Agent(...)` / `Task(...)`, or any call carrying a `subagent_type=`/`agentType=` keyword
-- and validates each dispatch's prompt text against the safety contracts that must hold
at spawn time:

  1. Env var allowlist -- if the prompt names an env-var-shaped token (FOO_KEY, FOO_TOKEN,
     FOO_SECRET, FOO_PASSWORD, FOO_CREDENTIAL[S]), it must be one of the vars this project
     actually provisions (KNOWN_ENV_VARS below). Open-ended credential-hunting phrasing
     ("find credentials", "search for keys", "hunt for api key", ...) is always a finding,
     regardless of allowlist membership -- this is the exact shape of a real incident where
     an agent hunted for API keys its transport never had configured.
  2. Forbidden flags -- --admin, --auto, --force, --no-verify must never appear in a
     dispatch prompt (these bypass required-checks / secret-scan / branch-protection gates).
  3. Isolation marker -- a prompt whose text implies file writes (Write(/Edit(/git commit/
     git push/etc.) must carry an explicit isolation instruction such as
     "[ISOLATION: sibling worktree]" (or the equivalent "sibling worktree" phrase).
  4. Role routing (advisory) -- a typed dispatch (subagent_type=/agentType= given) whose
     value isn't "general-purpose" and isn't in the known specialist catalog is flagged;
     the catalog here is a best-effort mirror of the harness's real specialist list, kept
     deliberately small and extended as new specialists are adopted.

Suppression: add `# contract-ok` anywhere on a line within the dispatch call's source span
(from its opening line to its closing line) to suppress ALL findings for that call site.

Exit codes: 0 = clean, 1 = findings present, 2 = error (bad args / unreadable path).

CLI:
  spec_contract_validator.py [--check] [--json] [--paths DIR_OR_FILE ...] [--root PATH]

  --check          Validate and report (default action; the only action today).
  --json           Emit machine-readable JSON instead of ASCII text.
  --paths ITEM...  Override default scan targets (directories are globbed non-recursively
                    for *.py; individual files are scanned directly).
  --root PATH      Repository root used to resolve relative --paths / defaults (default: cwd).

Default scan targets: driver/*.py, monitor/*.py, tools/*.py (dispatch-related files only --
in practice this is enforced by the AST pattern match itself: files with no dispatch call
sites simply produce zero findings).

ASCII-only output. Stdlib only, no external dependencies.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_SCAN_DIRS = ["driver", "monitor", "tools"]

DISPATCH_CALL_NAMES = {"agent", "Agent", "Task"}
DISPATCH_KEYWORD_NAMES = {"subagent_type", "agentType"}
PROMPT_KEYWORD_NAMES = {"prompt", "description"}

FORBIDDEN_FLAGS = ["--admin", "--auto", "--force", "--no-verify"]

# Env vars this project actually provisions for dispatched agents (2026-07-29: BENCH_API_KEY +
# OPENAI_API_KEY are the only user-scope API-key env vars; the rest are non-secret operational
# knobs). Extend this set in the same PR that wires a new env var into a dispatch prompt.
KNOWN_ENV_VARS = {
    "BENCH_API_KEY",
    "OPENAI_API_KEY",
    "AESOP_STATE_ROOT",
    "AESOP_CODEX_LIVE",
    "AESOP_PROOF_FIXTURES",
    "AESOP_TEST_CHILD_TIMEOUT_MS",
}

# Env-var-shaped token: FOO_KEY, FOO_API_TOKEN, FOO_SECRET, FOO_PASSWORD, FOO_CREDENTIAL(S).
ENV_VAR_TOKEN_RE = re.compile(
    r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?)\b"
)

# Open-ended credential-hunting phrasing -- forbidden regardless of allowlist membership.
CREDENTIAL_HUNTING_PATTERNS = [
    "find credentials",
    "find the credentials",
    "find an api key",
    "find api keys",
    "find any api key",
    "search for keys",
    "search for api key",
    "search for an api key",
    "search for credentials",
    "search for secrets",
    "look for api key",
    "look for credentials",
    "look for secrets",
    "hunt for",
    "locate credentials",
    "locate api key",
    "find secrets",
    "grep for key",
    "grep for credentials",
    "grep for secret",
]

ISOLATION_MARKERS = [
    "[isolation: sibling worktree]",
    "sibling worktree",
]

FILE_WRITE_INDICATORS = [
    "write(",
    "edit(",
    "git commit",
    "git push",
    "write the file",
    "write to the file",
    "create the file",
    "writes to disk",
    "commit the change",
    "commit and push",
]

# Advisory role-routing catalog: known specialist subagent_type values. "general-purpose" is
# always allowed as the documented default-dispatch fallback.
KNOWN_SPECIALIST_TYPES = {
    "general-purpose",
    "python-pro",
    "ui-ux-designer",
    "app-perf-frontend-developer",
    "frontend-developer",
    "security-auditor",
    "code-reviewer",
    "test-automator",
    "backend-architect",
    "devops-troubleshooter",
    "database-admin",
    "docs-architect",
}

SUPPRESS_MARKER = "# contract-ok"


def _call_name(node: ast.Call) -> Optional[str]:
    """Return the callee's simple name (Name.id or Attribute.attr), or None."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _string_value(node: ast.expr) -> Optional[str]:
    """Best-effort static string extraction from a Constant or f-string (JoinedStr)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return "".join(parts) if parts else None
    return None


def find_dispatch_calls(source: str, filename: str = "<string>") -> List[Dict[str, Any]]:
    """AST-scan source for agent-dispatch call sites.

    Returns a list of dicts: {lineno, end_lineno, prompt, subagent_type, suppressed}.
    Calls with unparseable source are skipped (returns []), never raise.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    lines = source.splitlines()
    calls: List[Dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        has_dispatch_kw = any(k in DISPATCH_KEYWORD_NAMES for k in keywords)
        name = _call_name(node)

        if name not in DISPATCH_CALL_NAMES and not has_dispatch_kw:
            continue

        text_fragments = []
        for kw_name in PROMPT_KEYWORD_NAMES:
            if kw_name in keywords:
                val = _string_value(keywords[kw_name])
                if val:
                    text_fragments.append(val)

        subagent_type = None
        for kw_name in DISPATCH_KEYWORD_NAMES:
            if kw_name in keywords:
                val = _string_value(keywords[kw_name])
                if val:
                    subagent_type = val
                    break

        start = node.lineno
        end = getattr(node, "end_lineno", node.lineno) or node.lineno

        suppressed = any(
            SUPPRESS_MARKER in lines[i]
            for i in range(max(0, start - 1), min(len(lines), end))
        )

        calls.append({
            "lineno": start,
            "end_lineno": end,
            "prompt": "\n".join(text_fragments),
            "subagent_type": subagent_type,
            "suppressed": suppressed,
        })

    return calls


def validate_call(call: Dict[str, Any]) -> List[Dict[str, str]]:
    """Validate a single dispatch call dict, returning a list of {rule, detail} findings."""
    findings: List[Dict[str, str]] = []
    text = call.get("prompt") or ""
    lower = text.lower()

    for flag in FORBIDDEN_FLAGS:
        if flag in text:
            findings.append({"rule": "forbidden_flag", "detail": flag})

    for phrase in CREDENTIAL_HUNTING_PATTERNS:
        if phrase in lower:
            findings.append({"rule": "credential_hunting", "detail": phrase})

    for m in ENV_VAR_TOKEN_RE.finditer(text):
        token = m.group(0)
        if token not in KNOWN_ENV_VARS:
            findings.append({"rule": "env_var_not_allowlisted", "detail": token})

    writes_files = any(ind in lower for ind in FILE_WRITE_INDICATORS)
    has_marker = any(marker in lower for marker in ISOLATION_MARKERS)
    if writes_files and not has_marker:
        findings.append({
            "rule": "missing_isolation_marker",
            "detail": "prompt implies file writes but has no isolation marker",
        })

    subagent_type = call.get("subagent_type")
    if subagent_type and subagent_type not in KNOWN_SPECIALIST_TYPES:
        findings.append({"rule": "unknown_specialist_type", "detail": subagent_type})

    return findings


def scan_file(path: Path) -> List[Dict[str, Any]]:
    """Scan one Python file for dispatch calls and return flat finding dicts."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    results: List[Dict[str, Any]] = []
    for call in find_dispatch_calls(source, filename=str(path)):
        if call["suppressed"]:
            continue
        for finding in validate_call(call):
            results.append({
                "file": str(path),
                "line": call["lineno"],
                "rule": finding["rule"],
                "detail": finding["detail"],
            })
    return results


def gather_targets(repo_root: Path, paths: Optional[List[str]]) -> List[Path]:
    """Resolve --paths (or the default scan dirs) into a sorted list of .py files."""
    items = paths if paths else DEFAULT_SCAN_DIRS
    files: List[Path] = []

    for item in items:
        p = Path(item)
        if not p.is_absolute():
            p = repo_root / item
        if p.is_dir():
            files.extend(sorted(p.glob("*.py")))
        elif p.is_file():
            files.append(p)
        # Nonexistent paths are silently skipped (a repo need not have a monitor/*.py yet).

    return files


def run(repo_root: Path, paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run the validator and return a result dict (ok/scanned_files/findings)."""
    targets = gather_targets(repo_root, paths)
    findings: List[Dict[str, Any]] = []
    for f in targets:
        findings.extend(scan_file(f))

    return {
        "ok": len(findings) == 0,
        "scanned_files": len(targets),
        "findings": findings,
    }


def format_ascii(result: Dict[str, Any]) -> str:
    lines = []
    if result["findings"]:
        lines.append(
            f"spec-contract-validator: {len(result['findings'])} finding(s) "
            f"in {result['scanned_files']} file(s) scanned"
        )
        for f in result["findings"]:
            lines.append(f"  FAIL: {f['file']}:{f['line']}: {f['rule']}: {f['detail']}")
    else:
        lines.append(
            f"spec-contract-validator: PASS ({result['scanned_files']} file(s) scanned, 0 findings)"
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Guardrail G4: spec-contract validator for agent dispatch calls"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Validate and report (default action; the only action today)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output")
    parser.add_argument(
        "--paths", nargs="+", default=None,
        help="Override default scan targets (directories globbed for *.py, or individual files)",
    )
    parser.add_argument(
        "--root", default=".", help="Repository root used to resolve relative --paths / defaults",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    try:
        repo_root = Path(args.root).resolve()
        result = run(repo_root, args.paths)
    except Exception as e:
        sys.stderr.write(f"ERROR: spec_contract_validator failed: {e}\n")
        return 2

    if args.json:
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    else:
        sys.stdout.write(format_ascii(result))

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
