#!/usr/bin/env python3
"""
tools.watcher_linter -- Detect watcher/polling anti-patterns (Guardrail G3).

Mechanizes the "no watcher pattern in long runs" memory: long-running agents
must never detach into a stall-prone wait-loop that spawns work and then sits
idle expecting an external signal to arrive on its own. Three documented
incidents traced back to exactly this shape.

Detects two classes of problem:

1. AST anti-patterns in Python source (tools/, monitor/, driver/, daemons/ by
   default):
   - An unconditional loop (``while True:`` / ``while 1:``) whose body calls
     ``time.sleep`` (a synchronous poll-loop that can spin forever).
   - A function or method named ``watch_*`` / ``monitor_*`` / ``poll_*`` that
     contains an unconditional loop anywhere in its body (a dedicated
     stall-prone loop-forever).
   - A subprocess call (``subprocess.Popen`` / ``.run`` / ``.call`` /
     ``.check_call`` / ``.check_output``, or a bare ``Popen``/``run`` imported
     from ``subprocess``) issued from inside an unconditional loop (spawn,
     then loop-again-and-again instead of converging).

2. Dangerous phrasing in dispatch/prompt strings -- text that instructs an
   agent to sit idle until a monitor, watcher, signal, or notification
   arrives (the "wait-for-X" phrasing), the standalone poll_-family verb
   used as a standing instruction, or the "watch-for-changes" style
   phrasing -- rather than running synchronously to completion.

Suppression: append ``# watcher-ok`` on the offending line (the ``while``
line, the ``def watch_x(...):`` line, the subprocess-call line, or the
prompt-string line) to allow a reviewed, intentional exception.

Exit codes:
  0 = clean (no findings)
  1 = findings detected
  2 = usage/scan error

Usage:
  python tools/watcher_linter.py [--check] [--json] [--paths DIR [DIR ...]]

Options:
  --check         Run the scan (default action; present for symmetry with
                   other tools' --check/--fix CLIs). Always exit-code gated.
  --json          Emit machine-readable JSON instead of text.
  --paths DIR...  Override the default scan directories.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

# Default directories scanned when --paths is not given.
DEFAULT_SCAN_DIRS = ["tools", "monitor", "driver", "daemons"]

# This module's own source necessarily spells out the trigger words it
# looks for (pattern definitions, docstrings). Exclude it from directory
# scans rather than sprinkling suppression markers through the pattern
# table itself. Unit tests still exercise its functions directly against
# fixture source, so this does not weaken coverage of the detector logic.
SELF_EXCLUDE_NAMES = {"watcher_linter.py"}

SUPPRESSION_MARKER = "# watcher-ok"

# Function/method name prefixes that mark a routine as a dedicated
# "sit and watch" loop -- an infinite loop inside one of these is exactly the
# stall shape the incidents were made of.
WATCHER_NAME_PREFIXES = ("watch_", "monitor_", "poll_")

SUBPROCESS_ATTRS = {"Popen", "run", "call", "check_call", "check_output"}

# Prompt-string regexes (applied per source line, case-insensitive).
# Written to require the trigger words in the same line, which is how
# dispatch-prompt templates in this repo compose sentences.
PROMPT_PATTERNS = [
    (
        "prompt-wait-for-watcher",
        re.compile(
            r"\bwait(?:ing)?\s+for\b.*\b(monitor|watcher|signal|notification)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt-polling",
        re.compile(r"\bpoll(?:ing)?\b|\bpoll\s+for\b", re.IGNORECASE),
    ),
    (
        "prompt-watch-for-changes",
        re.compile(r"\bwatch(?:ing)?\s+for\s+changes\b", re.IGNORECASE),
    ),
]


def _is_infinite_test(test_node):
    """True if a `while` test node is the literal `True` or `1`."""
    if isinstance(test_node, ast.Constant):
        return test_node.value is True or test_node.value == 1
    return False


def _line_text(source_lines, lineno):
    """1-indexed safe line lookup; returns '' if out of range."""
    idx = lineno - 1
    if 0 <= idx < len(source_lines):
        return source_lines[idx]
    return ""


def _is_suppressed(source_lines, linenos):
    """True if any of the given 1-indexed line numbers carries the marker."""
    for lineno in linenos:
        if SUPPRESSION_MARKER in _line_text(source_lines, lineno):
            return True
    return False


_EXIT_CALL_NAMES = {"exit", "quit", "_exit"}


def _is_exit_call(call_node):
    """True for sys.exit(...)/os._exit(...)/exit(...)/quit(...)."""
    func = call_node.func
    if isinstance(func, ast.Attribute) and func.attr in _EXIT_CALL_NAMES:
        if isinstance(func.value, ast.Name) and func.value.id in ("sys", "os"):
            return True
    if isinstance(func, ast.Name) and func.id in _EXIT_CALL_NAMES:
        return True
    return False


def _loop_has_exit_path(while_node):
    """True if the loop body demonstrably converges: a `break` (belonging to
    THIS loop, not a nested one), or a `return`/`raise`/process-exit call
    anywhere inside it (those unwind past any nesting depth).

    This distinguishes a legitimate "poll-until-condition-or-timeout" loop
    (bounded, always terminates one way or another -- a normal, useful
    pattern used all over this codebase, e.g. CI-wait helpers) from the
    actual incident shape: a `while True:` with no way out at all, that
    just spawns work and sits sleeping forever.
    """
    found = False

    def visit(node, in_nested_loop):
        nonlocal found
        if found:
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return  # a nested def's own control flow doesn't affect this loop
        if isinstance(node, ast.Break):
            if not in_nested_loop:
                found = True
            return
        if isinstance(node, (ast.Return, ast.Raise)):
            found = True
            return
        if isinstance(node, ast.Call) and _is_exit_call(node):
            found = True
            return
        nested = in_nested_loop or (
            isinstance(node, (ast.While, ast.For)) and node is not while_node
        )
        for child in ast.iter_child_nodes(node):
            visit(child, nested)

    for child in ast.iter_child_nodes(while_node):
        visit(child, False)
    return found


def _iter_infinite_while_loops(tree):
    """Yield every ast.While node whose test is an unconditional truth."""
    for node in ast.walk(tree):
        if isinstance(node, ast.While) and _is_infinite_test(node.test):
            yield node


def _is_sleep_call(call_node):
    """True for `time.sleep(...)` or a bare `sleep(...)` (from time import sleep)."""
    func = call_node.func
    if isinstance(func, ast.Attribute) and func.attr == "sleep":
        if isinstance(func.value, ast.Name) and func.value.id == "time":
            return True
    if isinstance(func, ast.Name) and func.id == "sleep":
        return True
    return False


def _is_subprocess_call(call_node):
    """True for subprocess.Popen/run/call/check_call/check_output, or a bare
    import of one of those names from the subprocess module."""
    func = call_node.func
    if isinstance(func, ast.Attribute) and func.attr in SUBPROCESS_ATTRS:
        if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            return True
    if isinstance(func, ast.Name) and func.id in SUBPROCESS_ATTRS:
        return True
    return False


def find_while_true_sleep(tree, source_lines):
    """Find `while True:` / `while 1:` loops whose body calls time.sleep()
    with no exit path (see `_loop_has_exit_path`) -- a poll-until-timeout
    loop that DOES break/return/raise/exit is a normal, legitimate pattern
    and is not flagged."""
    findings = []
    for while_node in _iter_infinite_while_loops(tree):
        sleep_calls = [
            n
            for n in ast.walk(while_node)
            if isinstance(n, ast.Call) and _is_sleep_call(n)
        ]
        if not sleep_calls:
            continue
        if _loop_has_exit_path(while_node):
            continue
        anchor_lines = [while_node.lineno] + [c.lineno for c in sleep_calls]
        if _is_suppressed(source_lines, anchor_lines):
            continue
        findings.append(
            {
                "category": "while-true-sleep",
                "line": while_node.lineno,
                "message": (
                    "unconditional loop with time.sleep() and no break/"
                    "return/raise/exit -- polling loop that never converges"
                ),
                "snippet": _line_text(source_lines, while_node.lineno).strip(),
            }
        )
    return findings


def find_watcher_named_infinite_loops(tree, source_lines):
    """Find watch_*/monitor_*/poll_* functions containing an infinite loop."""
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name_lower = node.name.lower()
        if not name_lower.startswith(WATCHER_NAME_PREFIXES):
            continue
        inner_loops = list(_iter_infinite_while_loops(node))
        if not inner_loops:
            continue
        anchor_lines = [node.lineno] + [w.lineno for w in inner_loops]
        if _is_suppressed(source_lines, anchor_lines):
            continue
        findings.append(
            {
                "category": "watcher-named-infinite-loop",
                "line": node.lineno,
                "message": (
                    f"function '{node.name}' matches a watcher-name prefix "
                    "(watch_/monitor_/poll_) and contains an unconditional loop"
                ),
                "snippet": _line_text(source_lines, node.lineno).strip(),
            }
        )
    return findings


def find_subprocess_in_infinite_loop(tree, source_lines):
    """Find subprocess calls issued from inside a `while True:` loop with no
    exit path (spawn-and-detach-to-watch-forever pattern). A loop that
    breaks/returns/raises/exits is a normal bounded retry-with-subprocess
    pattern and is not flagged."""
    findings = []
    for while_node in _iter_infinite_while_loops(tree):
        sub_calls = [
            n
            for n in ast.walk(while_node)
            if isinstance(n, ast.Call) and _is_subprocess_call(n)
        ]
        if not sub_calls:
            continue
        if _loop_has_exit_path(while_node):
            continue
        anchor_lines = [while_node.lineno] + [c.lineno for c in sub_calls]
        if _is_suppressed(source_lines, anchor_lines):
            continue
        for call_node in sub_calls:
            findings.append(
                {
                    "category": "subprocess-in-infinite-loop",
                    "line": call_node.lineno,
                    "message": (
                        "subprocess call inside an unconditional loop -- "
                        "detach-and-watch pattern"
                    ),
                    "snippet": _line_text(source_lines, call_node.lineno).strip(),
                }
            )
    return findings


def _is_prompt_ish_name(name):
    return bool(name) and "prompt" in name.lower()


def _find_prompt_string_spans(tree):
    """Locate the AST nodes that hold actual dispatch-prompt content, as
    opposed to arbitrary program strings (log messages, comments, asserts).

    A node counts as prompt-ish when it is the value of:
      - an assignment/annotated-assignment to a name containing 'prompt'
        (e.g. a triple-quoted template assigned to `prompt` or `PROMPT`);
      - a call keyword argument named 'prompt' (e.g. Task(prompt=...));
      - a dict entry whose string key contains 'prompt'
        (e.g. a spawn payload dict with a 'prompt' key).

    Restricting to these contexts keeps the string-scan targeted at
    dispatch-template content -- exactly what the guardrail is for -- rather
    than flagging every mention of "poll"/"wait"/"watch" anywhere in a file's
    comments, print statements, or assertions.
    """
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(
                isinstance(t, ast.Name) and _is_prompt_ish_name(t.id)
                for t in node.targets
            ):
                spans.append(node.value)
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and _is_prompt_ish_name(node.target.id)
                and node.value is not None
            ):
                spans.append(node.value)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if _is_prompt_ish_name(kw.arg):
                    spans.append(kw.value)
        elif isinstance(node, ast.Dict):
            for key_node, val_node in zip(node.keys, node.values):
                if (
                    isinstance(key_node, ast.Constant)
                    and isinstance(key_node.value, str)
                    and _is_prompt_ish_name(key_node.value)
                ):
                    spans.append(val_node)
    return [n for n in spans if n is not None]


def find_prompt_patterns(tree, source_lines):
    """Scan dispatch-prompt string content (see `_find_prompt_string_spans`)
    for dangerous wait/poll/watch phrasing."""
    findings = []
    seen = set()  # (lineno, category) -- de-dupe overlapping spans
    for value_node in _find_prompt_string_spans(tree):
        start = getattr(value_node, "lineno", None)
        end = getattr(value_node, "end_lineno", start)
        if start is None:
            continue
        for lineno in range(start, (end or start) + 1):
            line = _line_text(source_lines, lineno)
            if SUPPRESSION_MARKER in line:
                continue
            for category, pattern in PROMPT_PATTERNS:
                if not pattern.search(line):
                    continue
                key = (lineno, category)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    {
                        "category": category,
                        "line": lineno,
                        "message": f"dispatch-prompt string matches '{category}'",
                        "snippet": line.strip(),
                    }
                )
    return findings


def lint_source(source_text, filename="<string>"):
    """Run every check against one file's source text.

    Returns a list of finding dicts (without the 'file' key filled in;
    caller adds it). Returns a single 'parse-error' finding if the source
    fails to parse -- never raises, so one bad file never aborts a scan.
    """
    source_lines = source_text.splitlines()
    try:
        tree = ast.parse(source_text, filename=filename)
    except SyntaxError as exc:
        return [
            {
                "category": "parse-error",
                "line": exc.lineno or 0,
                "message": f"could not parse file: {exc.msg}",
                "snippet": "",
            }
        ]

    findings = []
    findings.extend(find_while_true_sleep(tree, source_lines))
    findings.extend(find_watcher_named_infinite_loops(tree, source_lines))
    findings.extend(find_subprocess_in_infinite_loop(tree, source_lines))
    findings.extend(find_prompt_patterns(tree, source_lines))
    return findings


def lint_file(filepath):
    """Lint a single file on disk. Returns list of finding dicts with 'file'."""
    filepath = Path(filepath)
    try:
        # utf-8-sig transparently strips a leading BOM (common on
        # Windows-authored files) while behaving exactly like utf-8 when no
        # BOM is present -- avoids spurious parse-error findings on BOM'd files.
        source_text = filepath.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return [
            {
                "file": str(filepath),
                "category": "read-error",
                "line": 0,
                "message": f"could not read file: {exc}",
                "snippet": "",
            }
        ]

    findings = lint_source(source_text, filename=str(filepath))
    for finding in findings:
        finding["file"] = str(filepath)
    return findings


def collect_python_files(paths, repo_root):
    """Resolve --paths (or the defaults) to a sorted list of .py files."""
    repo_root = Path(repo_root)
    files = []
    for raw_path in paths:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        if not candidate.exists():
            continue
        if candidate.is_file():
            if candidate.suffix == ".py" and candidate.name not in SELF_EXCLUDE_NAMES:
                files.append(candidate)
            continue
        for py_file in sorted(candidate.rglob("*.py")):
            if py_file.name in SELF_EXCLUDE_NAMES:
                continue
            files.append(py_file)
    return sorted(set(files))


def lint_paths(paths, repo_root):
    """Scan every .py file under `paths` (relative to repo_root). Returns
    (findings, scanned_file_count)."""
    files = collect_python_files(paths, repo_root)
    all_findings = []
    for filepath in files:
        all_findings.extend(lint_file(filepath))
    return all_findings, len(files)


def _relativize(findings, repo_root):
    """Rewrite absolute 'file' entries to repo-relative posix paths for
    stable, portable output."""
    repo_root = Path(repo_root).resolve()
    for finding in findings:
        try:
            rel = Path(finding["file"]).resolve().relative_to(repo_root)
            finding["file"] = rel.as_posix()
        except (ValueError, OSError):
            pass
    return findings


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Watcher/polling anti-pattern linter (Guardrail G3)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run the scan (default action; exit 0 clean / 1 findings)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of text"
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=None,
        help="Override default scan directories (default: %s)"
        % ", ".join(DEFAULT_SCAN_DIRS),
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root the default/relative --paths are resolved against",
    )
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    repo_root = Path(args.root).resolve()
    scan_paths = args.paths if args.paths else list(DEFAULT_SCAN_DIRS)

    try:
        findings, scanned_count = lint_paths(scan_paths, repo_root)
    except Exception as exc:  # pragma: no cover - defensive, keep fail-closed
        print(f"watcher_linter error: {exc}", file=sys.stderr)
        return 2

    findings = _relativize(findings, repo_root)
    ok = len(findings) == 0

    if args.json:
        result = {
            "ok": ok,
            "scanned_files": scanned_count,
            "scanned_paths": scan_paths,
            "findings": findings,
        }
        print(json.dumps(result, indent=2))
    else:
        print(f"Watcher linter: scanned {scanned_count} file(s)")
        if findings:
            print(f"{len(findings)} finding(s):")
            for finding in findings:
                print(
                    f"  {finding['file']}:{finding['line']} "
                    f"[{finding['category']}] {finding['message']}"
                )
                if finding.get("snippet"):
                    print(f"    {finding['snippet']}")
            print(
                "\nFAIL: watcher/polling anti-pattern(s) detected "
                "(suppress a reviewed exception with '# watcher-ok')"
            )
        else:
            print("PASS: no watcher/polling anti-patterns found")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
