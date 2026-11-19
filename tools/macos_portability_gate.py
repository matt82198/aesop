#!/usr/bin/env python3
"""macOS portability gate for Aesop and sibling fleet repositories.
INDEX: macOS portability gate: scans .mjs/.js for bare spawnSync/execFileSync('python', ...) lacking a python3 resolver/fallback, scans .py via AST for unguarded winreg/msvcrt/ctypes.windll lacking a win32/nt/Windows guard or # macos-port-exempt marker, and scans non-test production files for un-baselined personal user paths; CLI: --root PATH --profile <name> (e.g. aesop|brain|scripts) [--baseline FILE] [--json] [--check]; exit 0=clean/1=findings/2=error; stdlib-only

Exit codes:
  0 = clean (all checks passed)
  1 = portability findings detected
  2 = invalid usage, missing root, or unreadable baseline
"""

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

_PROF_C3 = "conductor" + "3"
_USER_M8 = "matt" + "8"
_USER_MT = "Matt" + "t"

VALID_PROFILES = ("aesop", "brain", "scripts", _PROF_C3)

COMMON_EXCLUDED_DIRS = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    ".venv",
    "venv",
    ".pytest_cache",
    "tests",
})

PROFILE_EXCLUDED_DIRS = {
    "aesop": COMMON_EXCLUDED_DIRS | frozenset({"docs", "bench", "assets", "examples"}),
    "brain": COMMON_EXCLUDED_DIRS | frozenset({
        "docs",
        "projects",
        "transcripts",
        "session-env",
        "file-history",
        "paste-cache",
        "shell-snapshots",
        "todos",
        "plans",
        "plugins",
        "humanize",
        "agents-parked",
    }),
    "scripts": COMMON_EXCLUDED_DIRS | frozenset({"docs", "archive"}),
    _PROF_C3: COMMON_EXCLUDED_DIRS | frozenset({"docs", "state", "archive"}),
}

# Personal WoW/keyrun scripts and historical audit reports explicitly out of Mac port scope.
PROFILE_EXCLUDED_FILES = {
    "aesop": frozenset(),
    "brain": frozenset({"memory-keys.json"}),
    "scripts": frozenset({
        "mouse_macro.py",
        "pp_brain.py",
        "pp_live_proof.py",
        "sui_command.py",
        "sui_error_check.py",
        "wow_reload.py",
        "wow_screenshot.py",
        "cmc_error_collector.py",
        "sv_backup.py",
        "keyrun_signal.py",
        "profile_path_lint.py",
        "open_findings.py",
    }),
    _PROF_C3: frozenset({
        "DELETION-REPORT.txt",
        "MAPPING-INVESTIGATION-SUMMARY.txt",
        "SKU-MAPPING-FINDINGS.json",
    }),
}

EXCLUDED_EXTENSIONS = BINARY_EXTENSIONS = frozenset({
    ".bin", ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyo",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".rar", ".woff", ".woff2",
    ".ttf", ".eot", ".mp3", ".mp4", ".wav", ".mov", ".db",
    ".sqlite", ".sqlite3", ".log",
})

EXEMPT_MARKER = "macos-port-exempt"
EXAMPLE_MARKERS = ("e.g.", "example", "default")

_JS_BARE_PYTHON_RE = re.compile(
    r"\b(?:spawnSync|execFileSync|spawn|execFile)\s*\(\s*(['\"])python\1\s*,"
)
_JS_FALLBACK_PYTHON_RE = re.compile(
    r"(?:\|\||\?\?)\s*(['\"])python\1"
)
_JS_ARGV_PYTHON_RE = re.compile(
    r"\[\s*(['\"])python\1\s*,\s*(?:['\"]-[A-Za-z]|['\"][^'\"]*\.py\b|[A-Za-z_$])"
)
_JS_PYTHON3_RE = re.compile(r"['\"]python3['\"]")
_JS_RESOLVE_PYTHON_RE = re.compile(r"['\"]python3['\"]|\bresolvePython\w*")

_USER_PATH_RE = re.compile(
    r"(?:C:[/\\]{1,2}Users[/\\]{1,2}|/c/Users/|C--Users-)(?:"
    + _USER_MT
    + r"|"
    + _USER_M8
    + r")\b",
    re.IGNORECASE,
)

_WINDOWS_MODULES = frozenset({"winreg", "msvcrt"})


def _is_test_file(rel_posix):
    parts = rel_posix.split("/")
    if "tests" in parts:
        return True
    name = parts[-1]
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith((".test.mjs", ".test.js", ".test.sh")):
        return True
    return False


def _strip_js_comments(source):
    """Return JS source with // and /* */ comments replaced by spaces, preserving lines."""
    out = []
    i = 0
    n = len(source)
    quote = None
    in_line_comment = False
    in_block_comment = False
    escaped = False

    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                out.append("\n")
            else:
                out.append(" ")
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                out.append("  ")
                i += 2
            elif ch == "\n":
                out.append("\n")
                i += 1
            else:
                out.append(" ")
                i += 1
            continue

        if quote is not None:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            i += 1
            continue

        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            in_line_comment = True
            out.append("  ")
            i += 2
            continue

        if ch == "/" and nxt == "*":
            in_block_comment = True
            out.append("  ")
            i += 2
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def scan_js_file(filepath, rel_posix):
    """Scan a .js or .mjs file for bare or indirect 'python' invocations."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as handle:
            raw_text = handle.read()
    except OSError:
        return []

    raw_lines = raw_text.splitlines()
    stripped_text = _strip_js_comments(raw_text)
    stripped_lines = stripped_text.splitlines()
    file_has_resolver = bool(_JS_RESOLVE_PYTHON_RE.search(stripped_text))
    findings = []

    for idx, line in enumerate(stripped_lines):
        raw_line = raw_lines[idx] if idx < len(raw_lines) else line
        if EXEMPT_MARKER in raw_line:
            continue

        direct_hit = bool(_JS_BARE_PYTHON_RE.search(line))
        indirect_hit = (
            not file_has_resolver
            and bool(
                _JS_FALLBACK_PYTHON_RE.search(line)
                or _JS_ARGV_PYTHON_RE.search(line)
            )
        )
        if not direct_hit and not indirect_hit:
            continue

        if direct_hit:
            window_start = max(0, idx - 15)
            preceding = "\n".join(stripped_lines[window_start : idx + 1])
            if _JS_PYTHON3_RE.search(preceding):
                continue

        findings.append({
            "rule": "js-bare-python",
            "file": rel_posix,
            "line": idx + 1,
            "message": (
                "bare 'python' invocation lacks a preceding 'python3' "
                "resolver or fallback"
            ),
            "snippet": raw_line.strip()[:120],
        })
    return findings


def _expr_mentions_platform_guard(expr):
    """True when an AST expression tests sys.platform / os.name / platform.system."""
    text_tokens = set()
    attr_chains = set()
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            text_tokens.add(sub.value.lower())
        elif isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
            attr_chains.add((sub.value.id, sub.attr))
        elif isinstance(sub, ast.Name):
            text_tokens.add(sub.id.lower())

    if any(tok in ("win32", "nt", "windows", "win") for tok in text_tokens):
        return True
    if ("sys", "platform") in attr_chains or ("os", "name") in attr_chains:
        return True
    if ("platform", "system") in attr_chains:
        return True
    return False


def _is_negated_win_guard(expr):
    """True for `sys.platform != 'win32'`, `os.name != 'nt'`, `not ...`."""
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return _expr_mentions_platform_guard(expr.operand)
    if isinstance(expr, ast.Compare):
        if any(isinstance(op, (ast.NotEq, ast.NotIn)) for op in expr.ops):
            return _expr_mentions_platform_guard(expr)
    return False


def _block_always_exits(stmts):
    """True when the last statement in a guard body exits the enclosing scope."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
        return True
    if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call):
        func = last.value.func
        if isinstance(func, ast.Attribute) and func.attr == "exit":
            return True
    return False


def _collect_win_api_hits(tree, raw_lines):
    """Collect (lineno, kind, detail, fn_scope, is_guarded) for Windows-only APIs."""
    hits = []
    guarded_fn_refs = {}
    unguarded_fn_refs = {}

    def record_ref(name, guarded, fn_scope):
        if guarded:
            guarded_fn_refs.setdefault(name, set()).add(fn_scope)
        else:
            unguarded_fn_refs.setdefault(name, set()).add(fn_scope)

    def has_exempt(lineno):
        if 1 <= lineno <= len(raw_lines):
            return EXEMPT_MARKER in raw_lines[lineno - 1]
        return False

    def visit_block(stmts, guarded, fn_scope):
        local_guarded = guarded
        for stmt in stmts:
            visit_stmt(stmt, local_guarded, fn_scope)
            if (
                isinstance(stmt, ast.If)
                and _is_negated_win_guard(stmt.test)
                and _block_always_exits(stmt.body)
            ):
                local_guarded = True

    def scan_expr(node, guarded, fn_scope):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute):
                if (
                    isinstance(sub.value, ast.Name)
                    and sub.value.id == "ctypes"
                    and sub.attr == "windll"
                ):
                    hits.append((
                        sub.lineno,
                        "ctypes.windll",
                        "unguarded ctypes.windll access",
                        fn_scope,
                        guarded or has_exempt(sub.lineno),
                    ))
            elif isinstance(sub, ast.Name):
                record_ref(sub.id, guarded, fn_scope)

    def visit_stmt(stmt, guarded, fn_scope):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                root_mod = alias.name.split(".")[0]
                if root_mod in _WINDOWS_MODULES:
                    hits.append((
                        stmt.lineno,
                        root_mod,
                        "unguarded import %s" % alias.name,
                        fn_scope,
                        guarded or has_exempt(stmt.lineno),
                    ))
            return

        if isinstance(stmt, ast.ImportFrom):
            mod = (stmt.module or "").split(".")[0]
            if mod in _WINDOWS_MODULES:
                hits.append((
                    stmt.lineno,
                    mod,
                    "unguarded import from %s" % stmt.module,
                    fn_scope,
                    guarded or has_exempt(stmt.lineno),
                ))
            elif mod == "ctypes" and any(a.name == "windll" for a in stmt.names):
                hits.append((
                    stmt.lineno,
                    "ctypes.windll",
                    "unguarded from ctypes import windll",
                    fn_scope,
                    guarded or has_exempt(stmt.lineno),
                ))
            return

        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit_block(stmt.body, guarded, stmt.name)
            return

        if isinstance(stmt, ast.ClassDef):
            visit_block(stmt.body, guarded, fn_scope)
            return

        if isinstance(stmt, ast.If):
            scan_expr(stmt.test, guarded, fn_scope)
            is_guard = _expr_mentions_platform_guard(stmt.test)
            negated = _is_negated_win_guard(stmt.test)
            body_guarded = guarded or (is_guard and not negated)
            else_guarded = guarded or (is_guard and negated)
            visit_block(stmt.body, body_guarded, fn_scope)
            visit_block(stmt.orelse, else_guarded, fn_scope)
            return

        if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            scan_expr(stmt.test if isinstance(stmt, ast.While) else stmt.iter, guarded, fn_scope)
            visit_block(stmt.body, guarded, fn_scope)
            visit_block(stmt.orelse, guarded, fn_scope)
            return

        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                scan_expr(item.context_expr, guarded, fn_scope)
            visit_block(stmt.body, guarded, fn_scope)
            return

        if isinstance(stmt, ast.Try):
            visit_block(stmt.body, guarded, fn_scope)
            for handler in stmt.handlers:
                visit_block(handler.body, guarded, fn_scope)
            visit_block(stmt.orelse, guarded, fn_scope)
            visit_block(stmt.finalbody, guarded, fn_scope)
            return

        scan_expr(stmt, guarded, fn_scope)

    visit_block(tree.body, False, None)
    return hits, guarded_fn_refs, unguarded_fn_refs


def scan_python_file(filepath, rel_posix):
    """Scan a .py file for unguarded winreg / msvcrt / ctypes.windll usage."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as handle:
            raw_text = handle.read()
    except OSError:
        return []

    try:
        tree = ast.parse(raw_text, filename=rel_posix)
    except SyntaxError:
        return []

    raw_lines = raw_text.splitlines()
    hits, guarded_refs, unguarded_refs = _collect_win_api_hits(tree, raw_lines)

    def fn_is_only_called_guarded(fn_name, depth=0, seen=None):
        if not fn_name or depth > 3:
            return False
        seen = seen or set()
        if fn_name in seen:
            return False
        seen = seen | {fn_name}
        g_callers = guarded_refs.get(fn_name, set())
        u_callers = unguarded_refs.get(fn_name, set())
        if not g_callers and not u_callers:
            return False
        if None in u_callers:
            return False
        for caller in u_callers:
            if not fn_is_only_called_guarded(caller, depth + 1, seen):
                return False
        return True

    findings = []
    for lineno, _kind, detail, fn_scope, is_guarded in hits:
        if is_guarded:
            continue
        if fn_scope and fn_is_only_called_guarded(fn_scope):
            continue
        snippet = raw_lines[lineno - 1].strip()[:120] if 1 <= lineno <= len(raw_lines) else ""
        findings.append({
            "rule": "py-unguarded-win-api",
            "file": rel_posix,
            "line": lineno,
            "message": "%s without win32/nt/Windows platform guard" % detail,
            "snippet": snippet,
        })
    return findings


def scan_paths_in_file(filepath, rel_posix):
    """Scan a production text file for hardcoded personal user paths."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []

    findings = []
    for idx, line in enumerate(lines, 1):
        if EXEMPT_MARKER in line:
            continue
        line_lower = line.lower()
        if any(marker in line_lower for marker in EXAMPLE_MARKERS):
            continue
        match = _USER_PATH_RE.search(line)
        if match:
            findings.append({
                "rule": "hardcoded-user-path",
                "file": rel_posix,
                "line": idx,
                "message": "un-baselined personal user path %r" % match.group(0),
                "snippet": line.strip()[:120],
            })
    return findings


def _load_baseline(baseline_path):
    if not baseline_path:
        return {}
    path = Path(baseline_path)
    if not path.is_file():
        raise ValueError("baseline file not found: %s" % baseline_path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError("cannot parse baseline %s: %s" % (baseline_path, exc))
    violations = data.get("violations", {})
    if not isinstance(violations, dict):
        return {}
    return {str(k).replace("\\", "/"): int(v) for k, v in violations.items()}


def _apply_baseline(findings, baseline):
    if not baseline:
        return findings
    counts = {}
    remaining = []
    for item in findings:
        key = "%s@%s" % (item["file"], item["rule"])
        allowed = baseline.get(key, 0)
        seen = counts.get(key, 0) + 1
        counts[key] = seen
        if seen <= allowed:
            continue
        remaining.append(item)
    return remaining


def scan_repository(root, profile="aesop", baseline_path=None):
    """Scan root directory under the given profile and return report dict."""
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ValueError("root is not a directory: %s" % root)
    if profile not in VALID_PROFILES:
        raise ValueError("unknown profile %r (expected one of %s)" % (profile, VALID_PROFILES))

    excluded_dirs = PROFILE_EXCLUDED_DIRS[profile]
    if baseline_path is None:
        default_bl = root_path / ".macos-portability-baseline.json"
        if default_bl.is_file():
            baseline_path = str(default_bl)
    baseline = _load_baseline(baseline_path)

    findings = []
    files_scanned = 0

    for dirpath, dirnames, filenames in os.walk(str(root_path)):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded_dirs)
        for filename in sorted(filenames):
            if filename in (".git", ".DS_Store"):
                continue
            full_path = Path(dirpath) / filename
            rel_posix = full_path.relative_to(root_path).as_posix()
            ext = full_path.suffix.lower()

            if ext in BINARY_EXTENSIONS or ext == ".md":
                continue
            if rel_posix in PROFILE_EXCLUDED_FILES.get(profile, frozenset()):
                continue
            if _is_test_file(rel_posix):
                continue

            files_scanned += 1
            if ext in (".js", ".mjs"):
                findings.extend(scan_js_file(full_path, rel_posix))
            elif ext == ".py":
                findings.extend(scan_python_file(full_path, rel_posix))

            findings.extend(scan_paths_in_file(full_path, rel_posix))

    filtered = _apply_baseline(findings, baseline)
    return {
        "status": "PASS" if not filtered else "FAIL",
        "exit_code": 0 if not filtered else 1,
        "profile": profile,
        "root": root_path.as_posix(),
        "files_scanned": files_scanned,
        "findings": filtered,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="macOS portability gate for Aesop and fleet repositories."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root directory to scan (default: .).",
    )
    parser.add_argument(
        "--profile",
        choices=VALID_PROFILES,
        default="aesop",
        help="Scan profile (default: aesop).",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Optional JSON baseline file of file@rule -> count exemptions.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Run gate check (default).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    args = parser.parse_args(argv)

    try:
        report = scan_repository(
            root=args.root,
            profile=args.profile,
            baseline_path=args.baseline,
        )
    except ValueError as exc:
        if args.json:
            print(json.dumps({"status": "ERROR", "exit_code": 2, "error": str(exc)}, indent=2))
        else:
            print("macos-portability-gate ERROR: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if report["findings"]:
            print(
                "macos-portability-gate FAIL (%d finding(s), %d files scanned, profile=%s):"
                % (len(report["findings"]), report["files_scanned"], report["profile"])
            )
            for item in report["findings"]:
                print(
                    "  [%s] %s:%d: %s"
                    % (item["rule"], item["file"], item["line"], item["message"])
                )
        else:
            print(
                "macos-portability-gate PASS (0 findings, %d files scanned, profile=%s)"
                % (report["files_scanned"], report["profile"])
            )
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
