#!/usr/bin/env python3
"""In-process HTTP test timeout lint (escape main-full-windows-shard2-ui-acceptance-timeout).
INDEX: Lints tests/**/*.py for client timeouts under 30 s (HTTPConnection/HTTPSConnection/urlopen/requests.*) aimed at loopback or at an in-process server; non-literal timeouts are banned; fail-closed on hits, a `# timing-ok: <reason>` escape is counted and ratcheted against the trunk baseline (.inprocess-timeout-baseline.json: dated, escape total + per-file hit ceilings), --emit-baseline measures the baseline, exit 2 on unreadable input

A test that starts an HTTP server on a thread of its own process (serve_forever
or QuietThreadingHTTPServer) shares the GIL and background threads with its
client. On a loaded runner the server may not answer inside a short client
timeout, and the test dies with socket.timeout. The server may live in this
module or in a helper module, so the rule keys on the CLIENT call instead.

Rule, for every tests/**/*.py module:
  - A client call (HTTPConnection, HTTPSConnection, urlopen, requests.<method>)
    is IN SCOPE when its host is a loopback literal (127.x, localhost, ::1,
    0.0.0.0), when its host is not a literal (a variable, f-string or missing
    argument: cannot be proven remote), or when the module references
    serve_forever / QuietThreadingHTTPServer. A call to a remote string literal
    host from a module with no in-process server is out of scope.
  - The timeout (keyword, or third positional argument of HTTPConnection and
    urlopen) must be a numeric literal, or a module-level name bound only to
    numeric literals (the smallest such value is used), or None (no timeout).
    Any other expression (an import, attribute, parameter, computed value) is
    a non-literal timeout and is a hit: non-literal client timeouts are banned.
  - A resolved timeout below MIN_TIMEOUT is a hit. A hit is excused when any
    line of the call carries '# timing-ok: <reason>' with a non-empty reason;
    excused hits are escapes.

Ratchet: the baseline file (--baseline, default DEFAULT_BASELINE at the root
when present) is flat: {"_generated": "YYYY-MM-DD", "escapes": N,
"<tests/path.py>": K, ...}. An escape total above N fails, and a file with more
unescaped hits than its K fails (fail-closed); a count below either ceiling
passes with a burn-down note. Files absent from the baseline allow zero. With
no baseline file every hit fails. Keys starting with "_" other than
"_generated" are ignored. The baseline expires: when "_generated" is more than
--max-age-days (default 30) before --today (default: the current date) the
ratchet fails. Re-measure with --emit-baseline (review-only; prints the JSON to
stdout, dated today) and commit the output as the new baseline.

--json prints {"hits", "escapes", "stale"} for baseline_liveness_check.py, whose
stale entries are "key (baseline N, current M)" lines.

Exit codes: 0 clean, 1 hits, ratchet exceeded or baseline expired, 2 usage or
unreadable input (the pre-push hook treats every non-zero exit as a block).
Stdlib-only, ASCII-only output.
"""
import argparse
import ast
import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

MIN_TIMEOUT = 30.0
DEFAULT_BASELINE = ".inprocess-timeout-baseline.json"
DEFAULT_MAX_AGE_DAYS = 30
ESCAPE_MARK = "# timing-ok:"
HTTP_CLIENTS = frozenset({"HTTPConnection", "HTTPSConnection"})
URL_CLIENT = "urlopen"
REQUESTS_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head",
                              "options", "request"})
SERVER_NAMES = frozenset({"serve_forever", "QuietThreadingHTTPServer"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})
ESCAPES_KEY = "escapes"
GENERATED_KEY = "_generated"


def _ascii(text):
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _client_kind(call):
    """Return 'http', 'url', 'requests' or None for a call node."""
    func = call.func
    if isinstance(func, ast.Name):
        name, owner = func.id, None
    elif isinstance(func, ast.Attribute):
        name, owner = func.attr, func.value
    else:
        return None
    if name in HTTP_CLIENTS:
        return "http"
    if name == URL_CLIENT:
        return "url"
    if isinstance(owner, ast.Name) and owner.id == "requests" and name in REQUESTS_METHODS:
        return "requests"
    return None


def _host_node(call):
    """First positional (host or URL), else the host/url keyword, else None."""
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg in ("host", "url"):
            return kw.value
    return None


def _timeout_node(call, kind):
    for kw in call.keywords:
        if kw.arg == "timeout":
            return kw.value
    if kind in ("http", "url") and len(call.args) >= 3:
        return call.args[2]
    return None


def _is_remote_literal(node):
    """True only for a string literal naming a non-loopback host."""
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    text = node.value
    target = text if "://" in text else "//" + text
    try:
        host = urlsplit(target).hostname
    except ValueError:
        return False
    if not host:
        return False
    return host not in LOOPBACK_HOSTS and not host.startswith("127.")


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


def _module_constants(tree):
    """Map each module-level name to its numeric values, or None when unresolvable.

    Every module-level binding contributes: a numeric plain assignment adds its
    value; anything else (a non-numeric value, tuple unpacking, an import, a
    def or class, augmented assignment) adds None, which makes the name
    unresolvable. A name is resolved only when every binding is numeric.
    """
    bindings = {}

    def bind(name, value):
        bindings.setdefault(name, []).append(_number(value) if value is not None else None)

    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            single = (len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name))
            for target in stmt.targets:
                for node in ast.walk(target):
                    if isinstance(node, ast.Name):
                        bind(node.id, stmt.value if single else None)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            bind(stmt.target.id, stmt.value)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                bindings.setdefault((alias.asname or alias.name).split(".")[0], []).append(None)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.setdefault(stmt.name, []).append(None)
        else:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    bindings.setdefault(node.id, []).append(None)
    resolved = {}
    for name, values in bindings.items():
        resolved[name] = None if any(v is None for v in values) else min(values)
    return resolved


def _resolve_timeout(node, constants):
    """Return ('none', None), ('literal', float), or ('unresolved', None)."""
    if isinstance(node, ast.Constant) and node.value is None:
        return "none", None
    value = _number(node)
    if value is not None:
        return "literal", value
    if isinstance(node, ast.Name):
        value = constants.get(node.id)
        if value is not None:
            return "literal", value
    return "unresolved", None


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
    server_ref = _references_server(tree)
    constants = _module_constants(tree)
    lines = source.splitlines()
    hits, escapes = [], 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _client_kind(node)
        if kind is None:
            continue
        host = _host_node(node)
        if not server_ref and _is_remote_literal(host):
            continue
        timeout = _timeout_node(node, kind)
        if timeout is None:
            continue
        status, value = _resolve_timeout(timeout, constants)
        if status == "none" or (status == "literal" and value >= MIN_TIMEOUT):
            continue
        if status == "unresolved":
            message = ("non-literal client timeout %s is banned; use a numeric literal "
                       ">= %g" % (_ascii(ast.unparse(timeout)), MIN_TIMEOUT))
        else:
            message = ("client timeout %g < %g in a module that starts or calls an "
                       "in-process server; raise it or add '%s <reason>' on the call line"
                       % (value, MIN_TIMEOUT, ESCAPE_MARK.rstrip()))
        end = getattr(node, "end_lineno", None) or node.lineno
        if any(_escape_on_line(lines[ln - 1]) for ln in range(node.lineno, end + 1)):
            escapes += 1
            continue
        hits.append((rel, node.lineno, message))
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
    """Return (escape_ceiling, {rel: hit_ceiling}, generated_date); ValueError when malformed."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError('baseline must be a JSON object {"_generated": "YYYY-MM-DD", '
                         '"escapes": N, "<path>": K}')
    generated = data.get(GENERATED_KEY)
    try:
        if not isinstance(generated, str):
            raise ValueError
        generated_date = date.fromisoformat(generated)
    except ValueError:
        raise ValueError('baseline "_generated" must be an ISO date string (YYYY-MM-DD)')
    ceiling = data.get(ESCAPES_KEY)
    if not _non_negative_int(ceiling):
        raise ValueError('baseline "escapes" must be a non-negative int')
    hit_ceilings = {}
    for key, value in data.items():
        if key == ESCAPES_KEY or key.startswith("_"):
            continue
        if not _non_negative_int(value):
            raise ValueError('baseline entry %s must map to a non-negative int' % key)
        hit_ceilings[key] = value
    return ceiling, hit_ceilings, generated_date


def _non_negative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def emit_baseline(per_file, escapes, today):
    """Return the baseline payload measured from the current scan, dated today."""
    payload = {GENERATED_KEY: today.isoformat(), ESCAPES_KEY: escapes}
    for rel in sorted(per_file):
        payload[rel] = per_file[rel]
    return payload


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
        description="Lint in-process HTTP test clients for short or non-literal timeouts.")
    parser.add_argument("--root", default=str(default_root),
                        help="repository root holding tests/ (default: this checkout)")
    parser.add_argument("--baseline", default=None,
                        help="ratchet file (default: %s at the root when present)"
                        % DEFAULT_BASELINE)
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                        help="baseline expires this many days after its _generated date "
                        "(default: %d)" % DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--today", default=None,
                        help="ISO date used as today for expiry and --emit-baseline "
                        "(default: the current date)")
    parser.add_argument("--emit-baseline", action="store_true",
                        help="print a baseline measured from the current scan, dated --today")
    parser.add_argument("--json", action="store_true",
                        help="print {hits, escapes, stale} as JSON (baseline_liveness_check)")
    args = parser.parse_args(argv)
    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError:
        parser.error("--today must be an ISO date (YYYY-MM-DD)")
    root = Path(args.root).resolve()
    baseline = args.baseline
    if baseline is None and (root / DEFAULT_BASELINE).is_file():
        baseline = str(root / DEFAULT_BASELINE)
    try:
        hits, escapes, count = scan_tree(root)
        if args.emit_baseline:
            per_file = {}
            for rel, _line, _message in hits:
                per_file[rel] = per_file.get(rel, 0) + 1
            print(json.dumps(emit_baseline(per_file, escapes, today), indent=2))
            return 0
        if baseline is not None:
            ceiling, hit_ceilings, generated = load_baseline(baseline)
        else:
            ceiling, hit_ceilings, generated = None, {}, None
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
    expired_days = None
    if baseline is not None:
        age = (today - generated).days
        if age > args.max_age_days:
            ratchet_fail = True
            expired_days = age
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
    if expired_days is not None:
        print("inprocess-timeout baseline expired: generated %s, %d day(s) old (max %d); "
              "burn down or re-measure with --emit-baseline"
              % (generated.isoformat(), expired_days, args.max_age_days))
    for entry in stale:
        print("inprocess-timeout baseline burned down: %s" % entry)
    print("inprocess-timeout lint: %d file(s), %d hit(s), %d escape(s)"
          % (count, len(hits), escapes))
    return 1 if ratchet_fail else 0


if __name__ == "__main__":
    sys.exit(main())
