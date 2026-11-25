"""Frozen factory grading and receipt helpers used by native qualification."""
import difflib
import hashlib
import json
import subprocess

LIMITS = ["--user", "1001:1001", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
          "--pids-limit", "256", "--memory", "3g", "--cpus", "2"]
GRADE = """
import contextlib, io, json, sys
job = json.load(sys.stdin)
scope = {}
with contextlib.redirect_stdout(io.StringIO()):
    exec(compile(job['source'], 'candidate.py', 'exec'), scope)
    actual = []
    for args in job['inputs']:
        try:
            actual.append(scope['solve'](*args))
        except Exception as exc:
            actual.append({'error': type(exc).__name__})
print(json.dumps(actual, allow_nan=False))
"""


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def grade(ticket, candidate, command):
    data = encode({"source": candidate, "inputs": [args for args, _ in ticket.cases]})
    try:
        result = subprocess.run(command, input=data, capture_output=True, timeout=20, check=False)
        if command[0] == "docker" and result.returncode in (125, 126, 127):
            raise RuntimeError("offline grader failed to start")
        # Compare serialized values so JSON true cannot pass as the integer 1.
        actual = json.loads(result.stdout)
        if result.returncode or not isinstance(actual, list) or len(actual) != len(ticket.cases):
            return False
        return all(encode(a) == encode(b) for a, (_, b) in zip(actual, ticket.cases))
    except (ValueError, subprocess.TimeoutExpired):
        return False


def write(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def diff_stats(before, after):
    additions = deletions = 0
    changes = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    for path in changes:
        a = before.get(path, b"").splitlines(keepends=True)
        b = after.get(path, b"").splitlines(keepends=True)
        for op, i, j, k, l in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op != "equal":
                deletions += j - i
                additions += l - k
    return {"files": len(changes), "added": additions, "deleted": deletions}, changes
