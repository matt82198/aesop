#!/usr/bin/env python3
"""
INDEX: G11 lint-evasion detector: flags compile-time string construction that hides another gate's trigger token -- adjacent-literal `+` chains, all-constant `str.join`, all-constant f-strings (Python via `ast`; `.js/.mjs/.cjs` via regex). Fires only when the RECONSTRUCTED value matches a gate token (word-boundary anchored) AND no single fragment contains that whole token, so for a protected control file the split form is evasion while a single-fragment spelling is not (the owning gate still sees the latter). Reports file:line + reconstructed value + matched token; CLI `[--root DIR] [--paths P ...] [--json] [--check]`, exit 0=clean/1=findings/2=error, stdlib-only. Tokens are DERIVED by AST-parsing the `*_TO_PROTECT` tables in `stateapi_lint.py` -- never imported and never re-spelled as literals here, since spelling them would make the detector itself a violation of the gate it protects -- plus built-in ratchet-baseline filenames. Sanctioned exemptions: runtime-assembled dummy credentials (splitting those is a REQUIRED invariant), `tests/**/fixtures/` trees, and `# lint-evasion-ok` / `// lint-evasion-ok` on any line of the construction. NOT yet wired into ci.yml: the first real-tree run found a live escape, so the tool exits 1 on the tree and `tests/test_verify_no_lint_evasion.py` pins the known-escape set as a bidirectional ratchet; wire it in only once that set is empty
tools.verify_no_lint_evasion -- G11: lint-evasion detector (staged, not yet
wired into CI -- see tools/CLAUDE.md for why and for the wiring precondition).

Mechanizes the rule "never defeat a gate by hiding its trigger token".

BACKGROUND (the escape this exists to prevent)
  A gate scans source for quoted filenames of protected control files. An agent
  wanted to write one of those files without adding a new gate violation, so it
  split the filename across three adjacent string literals joined with `+`. The
  reconstructed value was identical; the gate's regex -- which requires the whole
  name inside one pair of quotes -- no longer matched. The commit message stated
  the intent outright. The gate stayed green while the behaviour it forbids
  shipped.

DETECTION MODEL
  Flag a compile-time string construction (adjacent-literal `+` chains, an
  all-constant `str.join`, or an all-constant f-string) when BOTH hold:

    1. The RECONSTRUCTED value contains a gate token (word-boundary anchored).
    2. NO SINGLE fragment of the construction contains that whole token.

  Condition 2 keeps this precise rather than noisy: if one fragment already
  carries the entire token, the owning gate's literal scan still sees it and
  nothing was evaded; only a split that straddles the token is obfuscation-
  shaped. For a protected `alpha.json`, `prefix + 'alpha' + '.json'` is caught
  while `prefix + 'alpha.json'` is not. (Stand-in name deliberate: spelling a
  real protected filename here would violate the very gate this protects.)

GATE TOKEN SOURCES
  * Derived: the protected-file tables, read out of tools/stateapi_lint.py by
    parsing its module AST -- no import, no sys.path mutation, no literals.
  * Built-in: the ratchet baseline files (gate state, not control state).

SANCTIONED EXEMPTIONS (documented, deliberate)
  * Secret-scan trigger names are NOT gate tokens here. Runtime-assembled dummy
    credentials are a REQUIRED invariant -- the scanner's own fixtures must not
    contain contiguous credential text or the push gate blocks the commit -- so
    credential-placeholder-shaped values are skipped outright (SECRET_SHAPE_RE).
  * Files under a `tests/.../fixtures/` directory are skipped: fixture trees
    exist to hold deliberately malformed and deliberately split sample source.
  * `# lint-evasion-ok` (or `// lint-evasion-ok`) on any line of the
    construction suppresses that finding, matching the suppression convention
    used by the other AST guardrails in this directory.

WIRING STATUS AND LABELLING
  Staged, not wired into CI. tools/verify_gates_wired.py reads a "Guardrail G<n>"
  label in tools/CLAUDE.md as the assertion "this gate runs in CI", so this
  tool's CLAUDE.md entry deliberately withholds that label: applying it while
  unwired would be a false claim the enforcer would rightly fail. Add the label
  in the SAME PR that adds the CI step -- not sooner, and never by rewording
  around the enforcer. Precondition for wiring: the real tree scans clean (see
  the known-escape ratchet in tests/test_verify_no_lint_evasion.py).

Exit codes: 0 = clean, 1 = findings, 2 = error (fail-closed).

Usage:
  python tools/verify_no_lint_evasion.py [--root DIR] [--paths P ...] [--json]
"""
import ast
import json
import re
import sys
import warnings
from pathlib import Path


def parse_source(text):
    """ast.parse without leaking a scanned file's SyntaxWarnings to stderr.

    Scanning is read-only inspection; warnings about invalid escape sequences in
    somebody else's module are noise here, and they corrupt --json output.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.parse(text)

# Baseline/ratchet state files. These are gate bookkeeping rather than
# orchestration control files, so the scanned module does not list them.
BUILTIN_TOKENS = [
    ".stateapi-baseline.json",
    ".encoding-baseline.json",
]

# Credential-placeholder shapes. A construction whose reconstructed value looks
# like one of these is a sanctioned runtime-assembled dummy secret (see the
# module docstring) and is never reported. These are prefix shapes only -- they
# carry no entropy tail and are not themselves credentials.
SECRET_SHAPE_RE = re.compile(
    r"(?:gh[pousr]_|github_pat_|AKIA|ASIA|AIza|xox[baprs]-|sk-|"
    r"BEGIN [A-Z ]*PRIVATE KEY|-----BEGIN)"
)

SUPPRESS_MARKER = "lint-evasion-ok"

PY_SUFFIXES = {".py"}
JS_SUFFIXES = {".js", ".mjs", ".cjs"}

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv",
    ".pytest_cache", "coverage", ".mypy_cache", "state", "_site",
}

# Adjacent JS string literals joined by `+` (single line, no template holes).
JS_CONCAT_RE = re.compile(
    r"""(?P<first>'[^'\n\\]*'|"[^"\n\\]*"|`[^`\n\\${]*`)"""
    r"""(?P<rest>(?:\s*\+\s*(?:'[^'\n\\]*'|"[^"\n\\]*"|`[^`\n\\${]*`))+)"""
)
JS_LITERAL_RE = re.compile(r"""'[^'\n\\]*'|"[^"\n\\]*"|`[^`\n\\${]*`""")

MAX_REPEAT = 4096


# --- Gate token derivation ---

def derive_gate_tokens(repo_root):
    """Collect gate tokens by AST-parsing the protected-file tables.

    Reads tools/stateapi_lint.py as a module AST and pulls every string element
    out of its top-level list assignments whose names end in `_TO_PROTECT`.
    Parsing (rather than importing) keeps this file free of literal control
    filenames and avoids any sys.path mutation.

    Args:
        repo_root: Path to repository root.

    Returns:
        tuple: (sorted token list, source note string)
    """
    tokens = set(BUILTIN_TOKENS)
    source_file = Path(repo_root) / "tools" / "stateapi_lint.py"
    if not source_file.is_file():
        return sorted(tokens), "builtin-only (gate module not found)"

    try:
        tree = parse_source(source_file.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return sorted(tokens), "builtin-only (gate module unparseable)"

    derived = 0
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not any(n.endswith("_TO_PROTECT") for n in names):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                if elt.value.strip():
                    tokens.add(elt.value)
                    derived += 1

    note = "derived from gate module (%d) + builtin (%d)" % (
        derived, len(BUILTIN_TOKENS)
    )
    return sorted(tokens), note


def compile_token_matchers(tokens):
    """Build word-boundary-anchored matchers for each gate token.

    A token must not match when embedded in a longer identifier or filename
    (a protected `alpha.json` must not match inside `my-alpha.json`).

    Args:
        tokens: iterable of token strings.

    Returns:
        list: (token, compiled regex) pairs.
    """
    matchers = []
    for token in tokens:
        pattern = (
            r"(?<![A-Za-z0-9_.\-])" + re.escape(token) + r"(?![A-Za-z0-9_])"
        )
        matchers.append((token, re.compile(pattern)))
    return matchers


# --- Python AST extraction ---

def eval_const_str(node):
    """Evaluate a node to a compile-time string, or return None.

    Handles plain string constants and `'x' * N` repeats (the shape used by the
    sanctioned dummy-secret fixtures).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left, right = node.left, node.right
        for text_node, count_node in ((left, right), (right, left)):
            if (
                isinstance(text_node, ast.Constant)
                and isinstance(text_node.value, str)
                and isinstance(count_node, ast.Constant)
                and isinstance(count_node.value, int)
                and not isinstance(count_node.value, bool)
            ):
                count = max(0, min(count_node.value, MAX_REPEAT))
                return text_node.value * count
    return None


def flatten_add(node, out):
    """Flatten a left-associative `+` chain into left-to-right operand order."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        flatten_add(node.left, out)
        flatten_add(node.right, out)
    else:
        out.append(node)


def _runs_of_constants(operands):
    """Split operands into maximal runs of adjacent compile-time strings."""
    runs = []
    current = []
    for operand in operands:
        text = eval_const_str(operand)
        if text is None:
            if len(current) >= 2:
                runs.append(current)
            current = []
        else:
            current.append(text)
    if len(current) >= 2:
        runs.append(current)
    return runs


def extract_python_constructions(tree):
    """Yield (lineno, end_lineno, fragments, value) for constant constructions.

    Covers three shapes: adjacent-literal `+` chains, all-constant `str.join`
    over a list/tuple, and f-strings whose every piece is compile-time constant.
    """
    found = []
    seen_add_children = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                if isinstance(side, ast.BinOp) and isinstance(side.op, ast.Add):
                    seen_add_children.add(id(side))

    for node in ast.walk(tree):
        end = getattr(node, "end_lineno", None) or getattr(node, "lineno", 0)

        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            if id(node) in seen_add_children:
                continue
            operands = []
            flatten_add(node, operands)
            for run in _runs_of_constants(operands):
                found.append((node.lineno, end, run, "".join(run)))

        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "join"
                and isinstance(func.value, ast.Constant)
                and isinstance(func.value.value, str)
                and len(node.args) == 1
                and isinstance(node.args[0], (ast.List, ast.Tuple))
            ):
                pieces = [eval_const_str(e) for e in node.args[0].elts]
                if len(pieces) >= 2 and all(p is not None for p in pieces):
                    found.append(
                        (node.lineno, end, pieces, func.value.value.join(pieces))
                    )

        elif isinstance(node, ast.JoinedStr):
            pieces = []
            constant = True
            for piece in node.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    pieces.append(piece.value)
                    continue
                if isinstance(piece, ast.FormattedValue):
                    inner = piece.value
                    text = eval_const_str(inner)
                    if text is None and isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Add):
                        operands = []
                        flatten_add(inner, operands)
                        parts = [eval_const_str(o) for o in operands]
                        if all(p is not None for p in parts):
                            text = "".join(parts)
                    if text is None or piece.format_spec is not None or piece.conversion not in (-1, None):
                        constant = False
                        break
                    pieces.append(text)
                    continue
                constant = False
                break
            if constant and len(pieces) >= 2:
                found.append((node.lineno, end, pieces, "".join(pieces)))

    return found


def extract_js_constructions(text):
    """Yield (lineno, end_lineno, fragments, value) for JS literal `+` chains."""
    found = []
    for lineno, line in enumerate(text.split("\n"), 1):
        for match in JS_CONCAT_RE.finditer(line):
            literals = JS_LITERAL_RE.findall(match.group(0))
            if len(literals) < 2:
                continue
            fragments = [lit[1:-1] for lit in literals]
            found.append((lineno, lineno, fragments, "".join(fragments)))
    return found


# --- Scanning ---

def is_fixture_path(rel_path):
    """True for `tests/.../fixtures/...` paths (sanctioned split-source trees)."""
    parts = [p.lower() for p in rel_path.parts]
    if "fixtures" not in parts:
        return False
    return "tests" in parts[: parts.index("fixtures")]


def is_suppressed(lines, start, end):
    """True if any line of the construction carries the suppression marker."""
    for index in range(max(0, start - 1), min(len(lines), max(start, end))):
        if SUPPRESS_MARKER in lines[index]:
            return True
    return False


def iter_source_files(root, paths):
    """Yield source files to scan, honouring SKIP_DIRS."""
    roots = [Path(p) for p in paths] if paths else [Path(root)]
    for base in roots:
        if base.is_file():
            if base.suffix in PY_SUFFIXES or base.suffix in JS_SUFFIXES:
                yield base
            continue
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix not in PY_SUFFIXES and path.suffix not in JS_SUFFIXES:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            yield path


def scan_file(path, rel_path, matchers):
    """Scan one file, returning a list of finding dicts."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    lines = text.split("\n")

    if path.suffix in PY_SUFFIXES:
        try:
            tree = parse_source(text)
        except SyntaxError:
            return []
        constructions = extract_python_constructions(tree)
    else:
        constructions = extract_js_constructions(text)

    findings = []
    seen = set()
    for lineno, end_lineno, fragments, value in constructions:
        if not value or SECRET_SHAPE_RE.search(value):
            continue
        for token, matcher in matchers:
            if not matcher.search(value):
                continue
            # Obfuscation signal: the token survives reconstruction but is
            # absent from every individual fragment, so the owning gate's
            # literal scan can no longer see it.
            if any(token in fragment for fragment in fragments):
                continue
            if is_suppressed(lines, lineno, end_lineno):
                continue
            key = (lineno, token, value)
            if key in seen:
                continue
            seen.add(key)
            findings.append({
                "file": rel_path.as_posix(),
                "line": lineno,
                "token": token,
                "value": value,
                "fragments": fragments,
            })
    return findings


def scan(root, paths=None):
    """Scan a tree for lint-evasion-shaped string construction.

    Args:
        root: repository root (used for gate-token derivation and relative paths).
        paths: optional explicit files/dirs to scan instead of the whole root.

    Returns:
        tuple: (findings list, token-source note)
    """
    root = Path(root).resolve()
    tokens, note = derive_gate_tokens(root)
    matchers = compile_token_matchers(tokens)

    findings = []
    for path in iter_source_files(root, paths):
        try:
            rel_path = path.resolve().relative_to(root)
        except ValueError:
            rel_path = path
        if is_fixture_path(rel_path):
            continue
        findings.extend(scan_file(path, rel_path, matchers))

    findings.sort(key=lambda f: (f["file"], f["line"], f["token"]))
    return findings, note


def main(argv=None):
    """CLI entry point."""
    argv = list(sys.argv[1:] if argv is None else argv)

    root = None
    paths = []
    as_json = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        if arg == "--check":
            i += 1
        elif arg == "--json":
            as_json = True
            i += 1
        elif arg == "--root":
            i += 1
            if i >= len(argv):
                print("--root requires a value", file=sys.stderr)
                return 2
            root = argv[i]
            i += 1
        elif arg.startswith("--root="):
            root = arg[len("--root="):]
            i += 1
        elif arg == "--paths":
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                paths.append(argv[i])
                i += 1
        else:
            print("Unknown argument: %s" % arg, file=sys.stderr)
            return 2

    try:
        findings, note = scan(root or Path.cwd(), paths or None)
    except Exception as exc:  # fail-closed
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    if as_json:
        print(json.dumps(
            {"ok": not findings, "token_source": note, "findings": findings},
            indent=2,
        ))
    else:
        print("Lint-evasion scan: %d finding(s) [%s]" % (len(findings), note))
        for finding in findings:
            print("  %s:%d obfuscated gate token %r" % (
                finding["file"], finding["line"], finding["token"]
            ))
            print("      reconstructed: %r" % finding["value"])
            print("      fragments:     %r" % (finding["fragments"],))
        if findings:
            print("\nFAIL: %d concat-obfuscated gate token(s)." % len(findings))
            print("Use the real literal (and fix the underlying violation), or")
            print("annotate the line with '%s' if genuinely benign."
                  % SUPPRESS_MARKER)
        else:
            print("\nPASS: no concat-obfuscated gate tokens")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
