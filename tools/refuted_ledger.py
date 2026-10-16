#!/usr/bin/env python3
"""
INDEX: Refuted ledger validator, add, stats and redact for docs/refuted.jsonl (fail-closed: exit 1 on refused input, 2 on I/O error).

Refuted ledger: the committed record of what aesop measured and closed.

Git records what shipped. This ledger records the negative space: ideas measured
and killed, claims bounced by gates, lanes that came back REFUTED with no PR.
One JSON object per line in docs/refuted.jsonl; schema and verdicts in docs/REFUTED.md.

Usage:
  python tools/refuted_ledger.py validate <path>
  python tools/refuted_ledger.py add <path> --proposed-by ... --proposal ... \
      --measured-by ... --verdict ... --evidence ... [--id R-NNNN] [--date YYYY-MM-DD] [--note ...]
  python tools/refuted_ledger.py stats <path> [--json]
  python tools/refuted_ledger.py redact <in> <out>

Exit codes (fail-closed):
  0  success
  1  validation refused (schema, personal path, UUID, duplicate id, bad enum)
  2  usage or I/O error (missing file, unreadable input, bad arguments)

Stdlib only. Validation never echoes an offending value, only its line number and class.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROPOSED_BY = ("user", "orchestrator", "lane", "hook")
MEASURED_BY = ("test", "repro", "ab", "review", "gate", "precedent")
VERDICTS = ("REFUTED", "DEFERRED", "DONE_BY_DESIGN", "FALSE_POSITIVE")
REQUIRED_KEYS = ("id", "date", "proposed_by", "proposal", "measured_by", "verdict", "evidence")
OPTIONAL_KEYS = ("note",)

ID_RE = re.compile(r"^R-\d{4}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Windows user profile path, single or JSON-escaped slashes, name optional.
WINDOWS_USER_PATH_RE = re.compile(
    r"[A-Za-z]:(?:\\\\|[/\\])Users(?:\\\\|[/\\])(?:[^/\\\s\"]+)?", re.IGNORECASE
)
# $HOME-style and POSIX home paths.
HOME_PATH_RE = re.compile(
    r"\$HOME\b|\$\{HOME\}|%USERPROFILE%|\$env:USERPROFILE|/home/[^/\s\"]+|/Users/[^/\s\"]+"
)
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# Evidence must be a public pointer: an absolute, home-relative or drive path is local.
EVIDENCE_LOCAL_RE = re.compile(r"^(?:[/\\~]|[A-Za-z]:|<home>)")

FORBIDDEN_CLASSES: Tuple[Tuple[re.Pattern, str], ...] = (
    (WINDOWS_USER_PATH_RE, "Windows user path"),
    (HOME_PATH_RE, "home-style path"),
    (UUID_RE, "UUID"),
)


def _string_values(obj):
    """Yield every string value in a decoded JSON object, recursively."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _string_values(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _string_values(value)


def check_entry(obj) -> List[str]:
    """Return schema error messages for one decoded entry (no line numbers)."""
    if not isinstance(obj, dict):
        return ["entry is not a JSON object"]
    errors = []
    for key in REQUIRED_KEYS:
        if key not in obj:
            errors.append(f"missing required key '{key}'")
    allowed = set(REQUIRED_KEYS) | set(OPTIONAL_KEYS)
    for key in obj:
        if key not in allowed:
            errors.append(f"unknown key '{key}'")

    rid = obj.get("id")
    if "id" in obj and not (isinstance(rid, str) and ID_RE.match(rid)):
        errors.append("id must match R-NNNN")

    day = obj.get("date")
    if "date" in obj:
        valid = False
        if isinstance(day, str) and DATE_RE.match(day):
            try:
                date.fromisoformat(day)
                valid = True
            except ValueError:
                valid = False
        if not valid:
            errors.append("date must be a real calendar date YYYY-MM-DD")

    for key, allowed_values in (("proposed_by", PROPOSED_BY), ("measured_by", MEASURED_BY), ("verdict", VERDICTS)):
        if key in obj and obj[key] not in allowed_values:
            errors.append(f"{key} must be one of {', '.join(allowed_values)}")

    for key in ("proposal", "evidence"):
        if key in obj:
            value = obj[key]
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{key} must be a non-empty string")

    evidence = obj.get("evidence")
    if isinstance(evidence, str) and evidence.strip() and EVIDENCE_LOCAL_RE.match(evidence.strip()):
        errors.append("evidence must be a public pointer, not a local path")

    if "note" in obj and not isinstance(obj["note"], str):
        errors.append("note must be a string")
    return errors


def validate_text(text: str) -> List[str]:
    """Validate ledger text. Returns 'line N: message' strings; empty list means valid."""
    if not text.strip():
        return []  # an empty ledger is valid: zero entries
    errors: List[str] = []
    seen_ids: Dict[str, int] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            errors.append(f"line {lineno}: blank line")
            continue
        messages = []
        for pattern, label in FORBIDDEN_CLASSES:
            if pattern.search(raw):
                messages.append(f"contains {label}")
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            messages.append(f"invalid JSON ({exc.msg})")
            obj = None
        if obj is not None:
            messages.extend(check_entry(obj))
            if isinstance(obj, dict):
                for value in _string_values(obj):
                    for pattern, label in FORBIDDEN_CLASSES:
                        if pattern.search(value):
                            messages.append(f"contains {label}")
                rid = obj.get("id")
                if isinstance(rid, str):
                    if rid in seen_ids:
                        messages.append(f"duplicate id {rid} (first at line {seen_ids[rid]})")
                    else:
                        seen_ids[rid] = lineno
        for message in dict.fromkeys(messages):
            errors.append(f"line {lineno}: {message}")
    return errors


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _entries(text: str) -> List[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def compute_stats(path: Path) -> dict:
    """Return counts and refute rate for a ledger file. Raises ValueError if invalid."""
    text = _read(path)
    errors = validate_text(text)
    if errors:
        raise ValueError("; ".join(errors))
    entries = _entries(text)
    total = len(entries)
    by_verdict = {v: 0 for v in VERDICTS}
    by_proposed = {p: 0 for p in PROPOSED_BY}
    by_measured = {m: 0 for m in MEASURED_BY}
    for entry in entries:
        by_verdict[entry["verdict"]] += 1
        by_proposed[entry["proposed_by"]] += 1
        by_measured[entry["measured_by"]] += 1
    refuted = by_verdict["REFUTED"] + by_verdict["FALSE_POSITIVE"]
    refute_rate = round(refuted / total, 4) if total else None
    return {
        "total": total,
        "by_verdict": by_verdict,
        "by_proposed_by": by_proposed,
        "by_measured_by": by_measured,
        "refute_rate": refute_rate,
    }


def redact_text(text: str) -> str:
    """Replace Windows user paths and home-style paths with <home>, UUIDs with <session>."""
    text = WINDOWS_USER_PATH_RE.sub("<home>", text)
    text = HOME_PATH_RE.sub("<home>", text)
    text = UUID_RE.sub("<session>", text)
    return text


def _next_id(entries: List[dict]) -> str:
    numbers = [int(e["id"][2:]) for e in entries if isinstance(e.get("id"), str) and ID_RE.match(e["id"])]
    return f"R-{(max(numbers) + 1 if numbers else 1):04d}"


def add_entry(path: Path, fields: dict) -> List[str]:
    """Append one entry after validating the full resulting file. Returns errors (empty on success)."""
    existing = _read(path) if path.exists() else ""
    if existing and validate_text(existing):
        return ["existing ledger is invalid; refusing to append"] + validate_text(existing)
    obj = {key: fields[key] for key in ("id", "date", "proposed_by", "proposal", "measured_by", "verdict", "evidence")}
    if fields.get("note") is not None:
        obj["note"] = fields["note"]
    if obj["id"] is None:
        obj["id"] = _next_id(_entries(existing))
    line = json.dumps(obj, ensure_ascii=True)
    candidate = existing + ("" if not existing or existing.endswith("\n") else "\n") + line + "\n"
    errors = validate_text(candidate)
    if errors:
        return errors
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(line + "\n")
    return []


def _report(errors: List[str]) -> int:
    for error in errors:
        print(error, file=sys.stderr)
    return 1


def _cmd_validate(args) -> int:
    path = Path(args.path)
    try:
        text = _read(path)
    except OSError as exc:
        print(f"cannot read {path}: {exc.strerror}", file=sys.stderr)
        return 2
    errors = validate_text(text)
    if errors:
        return _report(errors)
    print(f"OK {len(_entries(text))} entries in {path}")
    return 0


def _cmd_stats(args) -> int:
    path = Path(args.path)
    try:
        result = compute_stats(path)
    except OSError as exc:
        print(f"cannot read {path}: {exc.strerror}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"refusing stats on invalid ledger: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_add(args) -> int:
    fields = {
        "id": args.id,
        "date": args.date or date.today().isoformat(),
        "proposed_by": args.proposed_by,
        "proposal": args.proposal,
        "measured_by": args.measured_by,
        "verdict": args.verdict,
        "evidence": args.evidence,
        "note": args.note,
    }
    try:
        errors = add_entry(Path(args.path), fields)
    except OSError as exc:
        print(f"cannot write {args.path}: {exc.strerror}", file=sys.stderr)
        return 2
    if errors:
        return _report(errors)
    print(f"added to {args.path}")
    return 0


def _cmd_redact(args) -> int:
    try:
        text = Path(args.input).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {args.input}: {exc.strerror}", file=sys.stderr)
        return 2
    try:
        Path(args.output).write_text(redact_text(text), encoding="utf-8", newline="\n")
    except OSError as exc:
        print(f"cannot write {args.output}: {exc.strerror}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate, extend, summarise and redact the refuted ledger.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a ledger file")
    p_validate.add_argument("path")
    p_validate.set_defaults(func=_cmd_validate)

    p_add = sub.add_parser("add", help="append one validated entry")
    p_add.add_argument("path")
    p_add.add_argument("--id", default=None, help="R-NNNN; auto-assigned when omitted")
    p_add.add_argument("--date", default=None, help="YYYY-MM-DD; today when omitted")
    p_add.add_argument("--proposed-by", required=True, dest="proposed_by")
    p_add.add_argument("--proposal", required=True)
    p_add.add_argument("--measured-by", required=True, dest="measured_by")
    p_add.add_argument("--verdict", required=True)
    p_add.add_argument("--evidence", required=True)
    p_add.add_argument("--note", default=None)
    p_add.set_defaults(func=_cmd_add)

    p_stats = sub.add_parser("stats", help="counts by verdict/proposer/measurer and refute rate")
    p_stats.add_argument("path")
    p_stats.add_argument("--json", action="store_true", help="accepted for clarity; output is always JSON")
    p_stats.set_defaults(func=_cmd_stats)

    p_redact = sub.add_parser("redact", help="turn a raw log into a committable one")
    p_redact.add_argument("input")
    p_redact.add_argument("output")
    p_redact.set_defaults(func=_cmd_redact)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
