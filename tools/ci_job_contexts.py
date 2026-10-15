#!/usr/bin/env python3
"""Required-check context resolution for CI workflows (shared by the CI gate linters).
INDEX: Required-check context resolver: maps a branch-protection context such as `ci (2)` to the workflow job whose RENDERED check name equals it (job id with the matrix suffix, or a `name:` override with matrix substitution). Fails closed: a context is resolved only when some matrix combination of some job renders exactly that name, so a `name:` override that renders something else, a suffix that is not a literal matrix value, and an unverifiable matrix (expression, include/exclude, non-literal values) are all rejected. Used by tools/ci_gate_runability.py and tools/ci_needs_skip_guard.py so both agree on what a required context means.

Job input shape (both callers build it): {job_id: {"name": str|None, "matrix": None|str|dict}} where matrix None means no strategy.matrix, a str is an unverifiable expression, and a dict maps dimension -> list of literal values (or any other value, which is rejected). Keys `include` / `exclude` in the dict are unverifiable and rejected.
"""

import re

_SUFFIX_RE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
_EXPR_RE = re.compile(r"\$\{\{.*?\}\}", re.S)
_MATRIX_REF_RE = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}")
_UNVERIFIABLE_KEYS = ("include", "exclude")


def split_context(context):
    """('ci (2)') -> ('ci', '2'); ('windows') -> ('windows', None)."""
    text = context.strip()
    match = _SUFFIX_RE.match(text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return text, None


def _literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        text = str(value)
        return None if _EXPR_RE.search(text) else text
    return None


def _combinations(matrix):
    """Matrix value combinations as dicts, or (None, reason) when they cannot be enumerated."""
    if matrix is None:
        return [{}], None
    if isinstance(matrix, str):
        return None, "its matrix is an expression (%s), so its check-name suffixes cannot be verified" % matrix.strip()
    if not isinstance(matrix, dict):
        return None, "its matrix is not a mapping"
    for key in _UNVERIFIABLE_KEYS:
        if key in matrix:
            return None, "its matrix uses %s, so its check-name suffixes cannot be verified" % key
    if not matrix:
        return None, "its matrix has no dimensions"
    combos = [{}]
    for dim, values in matrix.items():
        if not isinstance(values, list) or not values:
            return None, "matrix dimension %r is not a non-empty literal list" % dim
        literals = [_literal(v) for v in values]
        if any(v is None for v in literals):
            return None, "matrix dimension %r has a non-literal value" % dim
        combos = [{**combo, dim: value} for combo in combos for value in literals]
    return combos, None


def rendered_names(job_id, body):
    """Check names GitHub reports for one job, or (None, reason) when they cannot be computed."""
    name = body.get("name") if isinstance(body, dict) else None
    matrix = body.get("matrix") if isinstance(body, dict) else None
    combos, reason = _combinations(matrix)
    if combos is None:
        return None, reason
    names = set()
    for combo in combos:
        if name is None:
            if matrix is None:
                names.add(job_id)
            else:
                names.add("%s (%s)" % (job_id, ", ".join(combo.values())))
            continue
        text = str(name).strip()
        text = _MATRIX_REF_RE.sub(lambda m: combo.get(m.group(1), m.group(0)), text)
        if _EXPR_RE.search(text):
            return None, "its name: %r uses an expression this linter cannot render" % name
        names.add(text)
    return names, None


def resolve_required_context(context, jobs):
    """Resolve a required context to a job id, or (None, reason).

    A context resolves only when some job renders exactly that check name for some matrix
    combination. Otherwise the reason names the job the context points at (by id) and what
    that job actually renders, so the finding is actionable.
    """
    base, _suffix = split_context(context)
    ctx = context.strip()
    reason = None
    for job_id, body in jobs.items():
        names, why = rendered_names(job_id, body)
        if names is not None and ctx in names:
            return job_id, None
        if job_id == base:
            if names is None:
                reason = why
            else:
                reason = ("job %r renders check name(s) %s, none of which is %r"
                          % (job_id, ", ".join(repr(n) for n in sorted(names)), ctx))
    if reason is None:
        reason = "no job renders check name %r" % ctx
    return None, reason
