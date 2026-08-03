#!/usr/bin/env python3
"""
tools.status_bucket_lint -- Detect status/conclusion bucketing that fails OPEN.

Mechanizes a repeating defect class: a helper translates a CI status or
conclusion string into a pass/fail bucket with an if/elif chain over a
hardcoded list of known constants, and anything the chain does not recognize
falls through to a NON-FAILURE default. The unknown outcome then either
disappears from denominators or is reported as healthy -- a green that was
never earned.

Reference incident (GAP5, tools/crossos_drift.py): ``job_conclusion()``
bucketed every COMPLETED job whose conclusion was outside its two hardcoded
tuples as "PENDING", and PENDING is excluded from the pass-rate denominators.
GitHub's real ``startup_failure`` conclusion therefore vanished from cross-OS
drift measurement rather than counting against it.

Detection (AST, stdlib only):
  A function is a CANDIDATE when its name or any of its parameter names
  mentions ``status``, ``conclusion`` or ``state``.

  Inside a candidate, an if/elif chain is a BUCKETING CHAIN when its tests
  compare against at least two distinct known outcome constants (SUCCESS,
  FAILURE, CANCELLED, TIMED_OUT, SKIPPED, ...), via ``==``/``!=``/``in``/
  ``not in`` against string literals or literal tuples/lists/sets.

  A bucketing chain is a FINDING when the value produced on the path where no
  branch matched is classified as pass/pending rather than failure:
    no-terminal-else     -- no ``else``; the statement following the chain
                            yields a green/pending token.
    green-default        -- a terminal ``else`` yields a green/pending token.
    implicit-none-default-- no ``else`` and nothing follows: the function
                            returns ``None`` for every unrecognized outcome.

Precision is deliberate. A fall-through that raises, calls ``sys.exit`` with a
non-zero code, or yields a value that cannot be statically classified (a call,
a variable) is NOT reported. Green tokens come from a configurable list
(``--green-tokens``) so a project can teach the gate its own vocabulary.

Suppression: append ``# bucket-lint: ok <reason>`` to the ``def`` line, the
chain's first ``if`` line, or the fall-through line. Suppressions are counted
and printed (text and JSON) so an accumulating pile stays visible.

Exit codes:
  0 = clean (no findings)
  1 = findings detected
  2 = usage/scan error (missing path, unparseable source)

Usage:
  python tools/status_bucket_lint.py [--check] [--json] [--root DIR]
                                     [--paths DIR|FILE ...]
                                     [--green-tokens TOK,TOK]

Options:
  --check          Run the scan (default action; present for symmetry with the
                   repo's other gates). Always exit-code gated.
  --json           Emit machine-readable JSON instead of text.
  --root DIR       Repository root findings are reported relative to.
  --paths P ...    Override the default scan paths (default: tools).
  --green-tokens   Extra comma-separated tokens to treat as non-failure
                   defaults, in addition to the built-in list.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

# Default scan paths (repo-root-relative).
DEFAULT_SCAN_PATHS = ["tools"]

# This module necessarily spells out the very tokens it hunts for; scanning it
# would only produce noise about its own configuration tables.
SELF_EXCLUDE_NAMES = {"status_bucket_lint.py"}

SUPPRESSION_MARKER = "# bucket-lint: ok"

# A function is a candidate when its name or a parameter name mentions one of
# these -- that is what makes it a status translator rather than generic logic.
CANDIDATE_NAME_RE = re.compile(r"status|conclusion|state", re.IGNORECASE)

# Outcome constants that mark an if/elif chain as bucketing CI/job/run results.
# Two distinct hits are required, so a lone `== "SUCCESS"` guard is not a chain.
KNOWN_STATUS_TOKENS = frozenset({
    "SUCCESS", "FAILURE", "FAILED", "CANCELLED", "CANCELED", "TIMED_OUT",
    "TIMEDOUT", "SKIPPED", "NEUTRAL", "ACTION_REQUIRED", "STALE",
    "STARTUP_FAILURE", "COMPLETED", "IN_PROGRESS", "QUEUED", "WAITING",
    "PENDING", "REQUESTED", "EXPECTED", "ERROR", "PASSED", "PASS", "FAIL",
    "MERGED", "OPEN", "CLOSED", "ABORTED", "BLOCKED", "UNSTABLE", "NOT_RUN",
})

# Fall-through values that mean "this was not a failure". Landing here for an
# UNRECOGNIZED outcome is the bug: the unknown is blessed or dropped.
DEFAULT_GREEN_TOKENS = frozenset({
    "PASS", "PASSED", "SUCCESS", "SUCCEEDED", "OK", "GREEN", "CLEAN", "HEALTHY",
    "PENDING", "QUEUED", "IN_PROGRESS", "RUNNING", "WAITING", "SKIPPED",
    "NEUTRAL", "UNKNOWN", "NONE", "TRUE", "NOOP", "IGNORED",
})

# Fall-through values that correctly fail closed.
FAILURE_TOKENS = frozenset({
    "FAIL", "FAILED", "FAILURE", "ERROR", "RED", "BROKEN", "TIMED_OUT",
    "CANCELLED", "CANCELED", "ABORTED", "FALSE", "BLOCKED", "ACTION_REQUIRED",
    "STALE", "STARTUP_FAILURE", "UNSTABLE",
})

# Classification of a fall-through value.
GREEN = "green"
FAILURE = "failure"
UNKNOWN = "unknown"

CATEGORY_NO_ELSE = "no-terminal-else"
CATEGORY_GREEN_ELSE = "green-default"
CATEGORY_IMPLICIT_NONE = "implicit-none-default"


def normalize_token(text):
    """Fold a literal into a comparable token: upper, non-alphanumerics to '_'."""
    return re.sub(r"[^A-Z0-9]+", "_", str(text).upper()).strip("_")


def _line_text(source_lines, lineno):
    """1-indexed safe line lookup; '' when out of range."""
    idx = lineno - 1
    if 0 <= idx < len(source_lines):
        return source_lines[idx]
    return ""


def _is_suppressed(source_lines, linenos):
    """True when any of the given 1-indexed lines carries the suppression marker."""
    for lineno in linenos:
        if lineno and SUPPRESSION_MARKER in _line_text(source_lines, lineno):
            return True
    return False


def _iter_literal_strings(node):
    """Yield string constants reachable from a comparator node (scalar or literal container)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                yield elt.value


_BUCKETING_OPS = (ast.Eq, ast.NotEq, ast.In, ast.NotIn)


def _tokens_in_test(test_node):
    """Collect normalized string tokens compared against inside an if-test."""
    tokens = set()
    for node in ast.walk(test_node):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, _BUCKETING_OPS) for op in node.ops):
            continue
        for comparator in node.comparators:
            for text in _iter_literal_strings(comparator):
                tokens.add(normalize_token(text))
        for text in _iter_literal_strings(node.left):
            tokens.add(normalize_token(text))
    return tokens


def _chain_links(if_node):
    """Return the ordered list of If nodes forming an if/elif chain."""
    links = [if_node]
    current = if_node
    while len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
        current = current.orelse[0]
        links.append(current)
    return links


def _terminal_else(links):
    """Return the terminal `else` body of a chain, or None when there is none."""
    last = links[-1]
    if last.orelse and not (len(last.orelse) == 1 and isinstance(last.orelse[0], ast.If)):
        return last.orelse
    return None


def _is_exit_call(node):
    """True for sys.exit(...) / os._exit(...) / exit(...) expression statements."""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    if isinstance(func, ast.Attribute) and func.attr in ("exit", "_exit"):
        return isinstance(func.value, ast.Name) and func.value.id in ("sys", "os")
    return isinstance(func, ast.Name) and func.id in ("exit", "quit")


def _classify_constant(value, green_tokens):
    """Classify a literal fall-through value as GREEN / FAILURE / UNKNOWN."""
    if value is None:
        return GREEN, "None"
    if isinstance(value, bool):
        return (GREEN, "True") if value else (FAILURE, "False")
    if isinstance(value, int):
        return (GREEN, str(value)) if value == 0 else (FAILURE, str(value))
    if isinstance(value, str):
        token = normalize_token(value)
        if token in green_tokens:
            return GREEN, value
        if token in FAILURE_TOKENS:
            return FAILURE, value
        return UNKNOWN, value
    return UNKNOWN, repr(value)


def _classify_statement(stmt, green_tokens):
    """Classify the outcome a fall-through statement produces.

    Returns (classification, rendered_value, lineno).
    """
    if isinstance(stmt, ast.Raise):
        return FAILURE, "raise", stmt.lineno
    if _is_exit_call(stmt):
        args = stmt.value.args
        if args and isinstance(args[0], ast.Constant):
            kind, rendered = _classify_constant(args[0].value, green_tokens)
            return kind, "exit(%s)" % rendered, stmt.lineno
        return UNKNOWN, "exit(...)", stmt.lineno
    if isinstance(stmt, ast.Return):
        if stmt.value is None:
            return GREEN, "None", stmt.lineno
        if isinstance(stmt.value, ast.Constant):
            kind, rendered = _classify_constant(stmt.value.value, green_tokens)
            return kind, rendered, stmt.lineno
        return UNKNOWN, "<expression>", stmt.lineno
    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
        if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            kind, rendered = _classify_constant(stmt.value.value, green_tokens)
            return kind, rendered, stmt.lineno
    return UNKNOWN, "<statement>", stmt.lineno


def _is_candidate_function(func_node):
    """True when the function's name or any parameter mentions status/conclusion/state."""
    if CANDIDATE_NAME_RE.search(func_node.name):
        return True
    spec = func_node.args
    all_args = list(spec.posonlyargs) + list(spec.args) + list(spec.kwonlyargs)
    if spec.vararg:
        all_args.append(spec.vararg)
    if spec.kwarg:
        all_args.append(spec.kwarg)
    return any(CANDIDATE_NAME_RE.search(arg.arg) for arg in all_args)


class _FunctionScanner:
    """Scans one candidate function's body for fail-open bucketing chains."""

    def __init__(self, func_node, source_lines, filename, green_tokens):
        self.func = func_node
        self.source_lines = source_lines
        self.filename = filename
        self.green_tokens = green_tokens
        self.findings = []
        self.suppressed = 0

    def run(self):
        self._scan_body(self.func.body, at_function_top_level=True)
        return self.findings, self.suppressed

    def _scan_body(self, stmts, at_function_top_level):
        index = 0
        while index < len(stmts):
            stmt = stmts[index]
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index += 1  # nested defs are scanned as their own candidates
                continue
            if isinstance(stmt, ast.If):
                links = _chain_links(stmt)
                self._check_chain(links, stmts[index + 1:], at_function_top_level)
                for link in links:
                    self._scan_body(link.body, at_function_top_level=False)
                terminal = _terminal_else(links)
                if terminal:
                    self._scan_body(terminal, at_function_top_level=False)
                index += 1
                continue
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, list) and nested and isinstance(nested[0], ast.stmt):
                    self._scan_body(nested, at_function_top_level=False)
            for handler in getattr(stmt, "handlers", []) or []:
                self._scan_body(handler.body, at_function_top_level=False)
            index += 1

    def _check_chain(self, links, continuation, at_function_top_level):
        tokens = set()
        for link in links:
            tokens |= _tokens_in_test(link.test)
        known = tokens & KNOWN_STATUS_TOKENS
        if len(known) < 2:
            return  # not a bucketing chain over known outcome constants

        terminal = _terminal_else(links)
        if terminal:
            category = CATEGORY_GREEN_ELSE
            kind, rendered, lineno = _classify_statement(terminal[0], self.green_tokens)
            detail = "terminal else yields %s" % rendered
        elif continuation:
            category = CATEGORY_NO_ELSE
            kind, rendered, lineno = _classify_statement(continuation[0], self.green_tokens)
            detail = "no terminal else; falls through to %s" % rendered
        elif at_function_top_level:
            category = CATEGORY_IMPLICIT_NONE
            kind, rendered, lineno = GREEN, "None", links[-1].lineno
            detail = "no terminal else and nothing follows; returns None"
        else:
            return  # fall-through leaves an inner block; cannot classify safely

        if kind != GREEN:
            return

        chain_line = links[0].lineno
        if _is_suppressed(self.source_lines, [self.func.lineno, chain_line, lineno]):
            self.suppressed += 1
            return

        self.findings.append({
            "file": self.filename,
            "line": chain_line,
            "category": category,
            "message": (
                "%s() buckets %s but %s -- an unrecognized value is treated as "
                "non-failure (fail-open)"
                % (self.func.name, "/".join(sorted(known)), detail)
            ),
            "function": self.func.name,
            "fallthrough_line": lineno,
        })


def lint_source(source, filename="<source>", green_tokens=None):
    """Lint one Python source string.

    Returns (findings, suppressed_count). Raises SyntaxError on unparseable source.
    """
    green = frozenset(DEFAULT_GREEN_TOKENS | set(green_tokens or ()))
    tree = ast.parse(source, filename=filename)
    source_lines = source.splitlines()

    findings = []
    suppressed = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_candidate_function(node):
            continue
        scanner = _FunctionScanner(node, source_lines, filename, green)
        func_findings, func_suppressed = scanner.run()
        findings.extend(func_findings)
        suppressed += func_suppressed

    findings.sort(key=lambda f: (f["file"], f["line"], f["category"]))
    return findings, suppressed


def _discover(paths, repo_root):
    """Resolve scan paths to a sorted list of .py files. Raises on a missing path."""
    files = []
    for raw in paths:
        target = Path(raw)
        if not target.is_absolute():
            target = repo_root / target
        if not target.exists():
            raise FileNotFoundError("scan path not found: %s" % raw)
        if target.is_file():
            files.append(target)
            continue
        for candidate in sorted(target.rglob("*.py")):
            if candidate.name in SELF_EXCLUDE_NAMES:
                continue
            if "__pycache__" in candidate.parts or ".git" in candidate.parts:
                continue
            files.append(candidate)
    return files


def lint_paths(paths, repo_root, green_tokens=None):
    """Lint every .py file under the given paths. Returns (findings, scanned, suppressed)."""
    files = _discover(paths, repo_root)
    findings = []
    suppressed = 0
    for path in files:
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            rel = path.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            rel = path.as_posix()
        try:
            file_findings, file_suppressed = lint_source(source, rel, green_tokens)
        except SyntaxError as exc:
            raise RuntimeError("could not parse %s: %s" % (rel, exc))
        findings.extend(file_findings)
        suppressed += file_suppressed
    findings.sort(key=lambda f: (f["file"], f["line"], f["category"]))
    return findings, len(files), suppressed


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="status_bucket_lint",
        description=(
            "Detect status/conclusion bucketing whose unrecognized outcomes fall "
            "through to a non-failure default (fail-open)."
        ),
    )
    parser.add_argument("--check", action="store_true",
                        help="Run the scan (default action).")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON.")
    parser.add_argument("--root", default=".",
                        help="Repository root for relative reporting (default: .).")
    parser.add_argument("--paths", nargs="+", default=None,
                        help="Files or directories to scan (default: tools).")
    parser.add_argument("--green-tokens", default="",
                        help="Extra comma-separated non-failure tokens.")
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    repo_root = Path(args.root).resolve()
    scan_paths = args.paths if args.paths else list(DEFAULT_SCAN_PATHS)
    extra_green = {
        normalize_token(tok) for tok in args.green_tokens.split(",") if tok.strip()
    }

    try:
        findings, scanned, suppressed = lint_paths(scan_paths, repo_root, extra_green)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        print("status_bucket_lint error: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - defensive, keep fail-closed
        print("status_bucket_lint error: %s" % exc, file=sys.stderr)
        return 2

    ok = len(findings) == 0

    if args.json:
        print(json.dumps({
            "ok": ok,
            "scanned_files": scanned,
            "scanned_paths": scan_paths,
            "suppressed": suppressed,
            "findings": findings,
        }, indent=2))
    else:
        print("Status bucket lint: scanned %d file(s), %d suppression(s)"
              % (scanned, suppressed))
        if findings:
            print("%d finding(s):" % len(findings))
            for finding in findings:
                print("  %s:%d [%s] %s"
                      % (finding["file"], finding["line"],
                         finding["category"], finding["message"]))
            print("\nFAIL: status/conclusion bucketing falls open "
                  "(suppress a reviewed exception with '%s <reason>')"
                  % SUPPRESSION_MARKER)
        else:
            print("PASS: no fail-open status bucketing found")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
