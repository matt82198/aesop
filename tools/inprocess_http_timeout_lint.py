#!/usr/bin/env python3
"""In-process HTTP test timeout lint (escape main-full-windows-shard2-ui-acceptance-timeout).
INDEX: Lints tests/**/*.py for a constant client timeout under 30 s (HTTPConnection/HTTPSConnection/urlopen) in any module that starts an in-process server (serve_forever / QuietThreadingHTTPServer); fail-closed on hits, a `# timing-ok: <reason>` escape is counted and ratcheted against the trunk baseline (.inprocess-timeout-baseline.json: escape total + per-file hit ceilings), exit 2 on unreadable input

A test that starts an HTTP server on a thread of its own process (serve_forever
or QuietThreadingHTTPServer) shares the GIL and background threads with its
client. On a loaded runner the server may not answer inside a short client
timeout, and the test dies with socket.timeout. This lint keeps such tests off
short timeouts.

Rule, for every tests/**/*.py module that references serve_forever or
QuietThreadingHTTPServer: a client call (HTTPConnection, HTTPSConnection or
urlopen) whose constant timeout (keyword, or third positional argument) is
below MIN_TIMEOUT is a hit. A hit is excused when any line of the call carries
'# timing-ok: <reason>' with a non-empty reason; excused hits are escapes.

Ratchet: the baseline file (--baseline, default DEFAULT_BASELINE at the root
when present) is flat: {"escapes": N, "<tests/path.py>": K, ...}. An escape
total above N fails, and a file with more unescaped hits than its K fails
(fail-closed); a count below either ceiling passes with a burn-down note.
Files absent from the baseline allow zero. With no baseline file every hit fails.

--json prints {"hits", "escapes", "stale"} for baseline_liveness_check.py, whose
stale entries are "key (baseline N, current M)" lines.

Exit codes: 0 clean, 1 hits or ratchet exceeded, 2 usage or unreadable input
(the pre-push hook treats every non-zero exit as a block).
Stdlib-only, ASCII-only output.
"""
import argparse
import ast
import json
import sys
from pathlib import Path

MIN_TIMEOUT = 30.0
DEFAULT_BASELINE = ".inprocess-timeout-baseline.json"
ESCAPE_MARK = "# timing-ok:"
CLIENT_NAMES = frozenset({"HTTPConnection", "HTTPSConnection", "urlopen"})
SERVER_NAMES = frozenset({"serve_forever", "QuietThreadingHTTPServer"})
ESCAPES_KEY = "escapes"


def _called_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _references_server(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in SERVER_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in SERVER_NAMES:
            return True
    return False


def _number(node):
    if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)):
        return float(node.value)
    return None


def _constant_timeout(call):
    for kw in call.keywords:
        if kw.arg == "timeout":
            return _number(kw.value)
    if len(call.args) >= 3:
        return _number(call.args[2])
    return None


def _escape_on_line(line):
    """Return True when the line carries a timing-ok escape with a reason."""
    idx = line.find(ESCAPE_MARK)
    if idx < 0:
        return False
    return bool(line[idx + len(ESCAPE_MARK):].strip())


def scan_source(source, rel):
    """Scan one module. Returns (hits, escapes); hits are (rel, line, message)."""
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return [(rel, exc.lineno or 0, "unparseable test module: %s" % exc.msg)], 0
    if not _references_server(tree):
        return [], 0
    lines = source.splitlines()
    hits, escapes = [], 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _called_name(node.func) not in CLIENT_NAMES:
            continue
        timeout = _constant_timeout(node)
        if timeout is None or timeout >= MIN_TIMEOUT:
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        if any(_escape_on_line(lines[ln - 1]) for ln in range(node.lineno, end + 1)):
            escapes += 1
            continue
        hits.append((rel, node.lineno,
                     "client timeout %g < %g in a module that starts an in-process "
                     "server; raise it or add '%s <reason>' on the call line"
                     % (timeout, MIN_TIMEOUT, ESCAPE_MARK.rstrip())))
    return hits, escapes


def scan_tree(root):
    """Scan tests/**/*.py under root. Returns (hits, escapes, file_count)."""
    tests = root / "tests"
    if not tests.is_dir():
        raise OSError("no tests/ directory under %s" % root)
    hits, escapes, count = [], 0, 0
    for path in sorted(tests.rglob("*.py")):
        count += 1
        rel = path.relative_to(root).as_posix()
        file_hits, file_escapes = scan_source(path.read_text(encoding="utf-8"), rel)
        hits.extend(file_hits)
        escapes += file_escapes
    hits.sort(key=lambda h: (h[0], h[1]))
    return hits, escapes, count


def load_baseline(path):
    """Return (escape_ceiling, {rel: hit_ceiling}); raises ValueError when malformed."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError('baseline must be a JSON object {"escapes": N, "<path>": K}')
    ceiling = data.get(ESCAPES_KEY)
    if not _non_negative_int(ceiling):
        raise ValueError('baseline "escapes" must be a non-negative int')
    hit_ceilings = {}
    for key, value in data.items():
        if key == ESCAPES_KEY:
            continue
        if not _non_negative_int(value):
            raise ValueError('baseline entry %s must map to a non-negative int' % key)
        hit_ceilings[key] = value
    return ceiling, hit_ceilings


def _non_negative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _stale_entries(per_file, escapes, ceiling, hit_ceilings):
    stale = []
    for rel, allowed in sorted(hit_ceilings.items()):
        current = per_file.get(rel, 0)
        if current < allowed:
            stale.append("%s (baseline %d, current %d)" % (rel, allowed, current))
    if escapes < ceiling:
        stale.append("%s (baseline %d, current %d)" % (ESCAPES_KEY, ceiling, escapes))
    return stale


def main(argv=None):
    default_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Lint in-process HTTP test servers for short client timeouts.")
    parser.add_argument("--root", default=str(default_root),
                        help="repository root holding tests/ (default: this checkout)")
    parser.add_argument("--baseline", default=None,
                        help="ratchet file (default: %s at the root when present)"
                        % DEFAULT_BASELINE)
    parser.add_argument("--json", action="store_true",
                        help="print {hits, escapes, stale} as JSON (baseline_liveness_check)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    baseline = args.baseline
    if baseline is None and (root / DEFAULT_BASELINE).is_file():
        baseline = str(root / DEFAULT_BASELINE)
    try:
        hits, escapes, count = scan_tree(root)
        if baseline is not None:
            ceiling, hit_ceilings = load_baseline(baseline)
        else:
            ceiling, hit_ceilings = None, {}
    except (OSError, ValueError) as exc:
        print("inprocess-timeout lint error: %s" % exc, file=sys.stderr)
        return 2

    per_file = {}
    for rel, _line, _message in hits:
        per_file[rel] = per_file.get(rel, 0) + 1
    over = {}
    for rel, line, message in hits:
        if baseline is None or per_file[rel] > hit_ceilings.get(rel, 0):
            over.setdefault(rel, []).append((line, message))
    ratchet_fail = bool(over)
    if baseline is not None and escapes > ceiling:
        ratchet_fail = True
    stale = (_stale_entries(per_file, escapes, ceiling, hit_ceilings)
             if baseline is not None else [])

    if args.json:
        print(json.dumps({
            "hits": ["%s:%d: %s" % (rel, line, message) for rel, line, message in hits],
            "escapes": escapes,
            "stale": stale,
        }, indent=2))
        return 0

    for rel in sorted(over):
        for line, message in over[rel]:
            print("%s:%d: %s" % (rel, line, message))
    if baseline is not None and escapes > ceiling:
        print("inprocess-timeout escapes %d exceed baseline %d; the ratchet only "
              "shrinks" % (escapes, ceiling))
    for entry in stale:
        print("inprocess-timeout baseline burned down: %s" % entry)
    print("inprocess-timeout lint: %d file(s), %d hit(s), %d escape(s)"
          % (count, len(hits), escapes))
    return 1 if ratchet_fail else 0


if __name__ == "__main__":
    sys.exit(main())
