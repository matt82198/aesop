#!/usr/bin/env python3
"""Deterministic gate that verifies a worker lane hand-back before orchestrator intake.

INDEX: Deterministic lane hand-back verifier gate (`verify_handback()` + CLI): rejects a worker hand-back before orchestrator intake unless (1) signed receipt verifies via `verify_receipt.verify()`, (2) git worktree is clean including untracked files, (3) all committed diff paths in `<base_ref>...<head>` match non-empty declared scope globs, and (4) recorded `test_run` command exited 0; emits typed verdict JSON (`ACCEPT`/`REJECT`/`ERROR`, exit 0/1/2, fail-closed); Python 3.10+ stdlib only.
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

try:
    from tools import receipt_common
except ImportError:
    import receipt_common

try:
    from tools import verify_receipt
except ImportError:
    import verify_receipt

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

EXIT_CODES = {
    "ACCEPT": 0,
    "REJECT": 1,
    "ERROR": 2,
}


class HandbackError(Exception):
    """Fail-closed evaluation error corresponding to verdict ERROR and exit code 2."""


class _MissingReceiptFile:
    """Internal marker for a declared receipt path that does not exist on disk."""

    def __init__(self, path_str: str):
        self.path_str = path_str


def exit_code_for_verdict(result: dict) -> int:
    """Map a verdict dictionary to its process exit code (fail-closed to 2)."""
    if not isinstance(result, dict):
        return 2
    return EXIT_CODES.get(result.get("verdict"), 2)


def normalize_posix_path(raw: str) -> str:
    """Normalize a relative path or glob string to forward-slash POSIX form."""
    text = raw.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    while "//" in text:
        text = text.replace("//", "/")
    if text.startswith("/"):
        text = text.lstrip("/")
    return text


def glob_match(path: str, pattern: str) -> bool:
    """Deterministic cross-platform POSIX glob match supporting `*`, `?`, `[]`, and `**`.

    Single `*` matches within one path segment and never crosses `/`.
    A `**` segment matches zero or more path segments (at least one when trailing
    after a slash, so `tools/**` matches files beneath `tools/` only).
    Parent-traversal segments (`..`) are always rejected.
    """
    norm_path = normalize_posix_path(path)
    norm_pat = normalize_posix_path(pattern)
    if not norm_path or not norm_pat or norm_pat.endswith("/"):
        return False

    path_parts = norm_path.split("/")
    pat_parts = norm_pat.split("/")
    if ".." in path_parts or ".." in pat_parts:
        return False

    memo = {}

    def _match_at(pi: int, gi: int) -> bool:
        key = (pi, gi)
        if key in memo:
            return memo[key]
        if gi == len(pat_parts):
            ans = pi == len(path_parts)
        elif pat_parts[gi] == "**":
            if gi == len(pat_parts) - 1:
                ans = pi < len(path_parts)
            else:
                ans = _match_at(pi, gi + 1) or (
                    pi < len(path_parts) and _match_at(pi + 1, gi)
                )
        elif pi < len(path_parts) and fnmatch.fnmatchcase(path_parts[pi], pat_parts[gi]):
            ans = _match_at(pi + 1, gi + 1)
        else:
            ans = False
        memo[key] = ans
        return ans

    return _match_at(0, 0)


def _run_git(repo_path: Path, *args: str):
    """Run git in `repo_path` with explicit UTF-8 replacement decoding and timeout."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path)] + list(args),
            cwd=str(repo_path),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HandbackError(f"git {' '.join(args)} failed to execute: {exc}") from exc
    return proc.returncode, proc.stdout or "", (proc.stderr or "").strip()


def _default_checks(status: str = "ERROR") -> dict:
    """Construct the initial four-check dictionary."""
    return {
        "receipt": {
            "ok": False,
            "status": status,
            "exit_code": None,
            "reasons": [],
        },
        "worktree_clean": {
            "ok": False,
            "status": status,
            "dirty_paths": [],
            "reasons": [],
        },
        "scope": {
            "ok": False,
            "status": status,
            "declared_scope": [],
            "diff_paths": [],
            "out_of_scope": [],
            "reasons": [],
        },
        "tests": {
            "ok": False,
            "status": status,
            "command": None,
            "exit_code": None,
            "reasons": [],
        },
    }


def _make_verdict(verdict: str, lane, head_sha, checks: dict, reasons: list) -> dict:
    """Assemble the canonical v1 verdict payload."""
    return {
        "v": 1,
        "ok": verdict == "ACCEPT",
        "verdict": verdict,
        "lane": lane,
        "head_sha": head_sha,
        "checks": checks,
        "reasons": list(reasons),
    }


def _read_json_file(path: Path, label: str):
    """Read and parse a UTF-8 JSON file, raising HandbackError on failure."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return json.loads(handle.read())
    except (OSError, ValueError) as exc:
        raise HandbackError(f"cannot read {label} JSON from {path}: {exc}") from exc


def _resolve_receipt_input(handback: dict, receipt_envelope, repo_path: Path):
    """Resolve receipt envelope object or missing-file marker from args or handback."""
    if receipt_envelope is not None:
        return receipt_envelope

    if "receipt_envelope" in handback and handback["receipt_envelope"] is not None:
        env_val = handback["receipt_envelope"]
        if not isinstance(env_val, dict):
            raise HandbackError("handback.receipt_envelope must be a JSON object")
        return env_val

    raw = handback.get("receipt")
    if raw is None and "receipt_path" in handback:
        raw = handback.get("receipt_path")

    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, Path)):
        raw_str = str(raw).strip()
        if not raw_str:
            return None
        candidate = Path(raw_str)
        if not candidate.is_absolute():
            in_repo = repo_path / candidate
            if in_repo.exists():
                candidate = in_repo
        if not candidate.exists():
            return _MissingReceiptFile(raw_str)
        if not candidate.is_file():
            raise HandbackError(f"receipt path is not a regular file: {raw_str}")
        loaded = _read_json_file(candidate, "receipt")
        if not isinstance(loaded, dict):
            raise HandbackError("receipt JSON must be an object")
        return loaded

    raise HandbackError("handback.receipt must be an envelope object or file path string")


def _normalize_required(required) -> list:
    """Normalize `required` parameter into a list of part names."""
    if required is None:
        return list(receipt_common.DEFAULT_REQUIRED_PARTS)
    if isinstance(required, str):
        return [item.strip() for item in required.split(",") if item.strip()]
    if isinstance(required, (list, tuple)) and all(isinstance(x, str) for x in required):
        return [item.strip() for item in required if item.strip()]
    raise HandbackError("required must be a comma-separated string or sequence of strings")


def _check_receipt(
    handback: dict,
    *,
    repo_path: Path,
    head_sha: str,
    receipt_envelope,
    main_ref: str,
    max_behind: int,
    required_list: list,
    pubkey_path,
    hmac_secret,
    keyring,
    allow_no_required: bool,
):
    """Run check 1 (receipt verification). Returns (check_dict, is_error)."""
    hb_head = handback.get("head_sha")
    if hb_head is not None and not isinstance(hb_head, str):
        msg = "receipt: handback.head_sha must be a string when present"
        return {"ok": False, "status": "ERROR", "exit_code": 2, "reasons": [msg]}, True

    try:
        resolved_env = _resolve_receipt_input(handback, receipt_envelope, repo_path)
    except HandbackError as exc:
        msg = f"receipt: {exc}"
        return {"ok": False, "status": "ERROR", "exit_code": 2, "reasons": [msg]}, True

    if resolved_env is None:
        msg = "receipt: missing signed receipt"
        return {"ok": False, "status": "FAIL", "exit_code": 1, "reasons": [msg]}, False

    if isinstance(resolved_env, _MissingReceiptFile):
        msg = f"receipt: receipt file not found: {resolved_env.path_str}"
        return {"ok": False, "status": "FAIL", "exit_code": 1, "reasons": [msg]}, False

    resolved_pubkey = (
        Path(pubkey_path)
        if pubkey_path is not None
        else repo_path / receipt_common.DEFAULT_PUBKEY_REL
    )
    resolved_keyring = Path(keyring) if keyring is not None else None

    v_code, v_reasons = verify_receipt.verify(
        resolved_env,
        repo=repo_path,
        head=head_sha,
        main_ref=main_ref,
        max_behind=max_behind,
        required=required_list,
        pubkey_path=resolved_pubkey,
        hmac_secret=hmac_secret,
        keyring=resolved_keyring,
        allow_no_required=allow_no_required,
    )

    prefixed = [f"receipt: {r}" for r in v_reasons]
    if v_code == 2:
        if not prefixed:
            prefixed = ["receipt: cannot evaluate receipt"]
        return {"ok": False, "status": "ERROR", "exit_code": 2, "reasons": prefixed}, True

    if isinstance(hb_head, str) and hb_head != head_sha:
        prefixed.append(
            f"receipt: handback head_sha {hb_head[:12]} != checkout {head_sha[:12]}"
        )

    if v_code != 0 or prefixed:
        if not prefixed:
            prefixed = ["receipt: verification failed"]
        return {"ok": False, "status": "FAIL", "exit_code": 1, "reasons": prefixed}, False

    return {"ok": True, "status": "PASS", "exit_code": 0, "reasons": []}, False


def _extract_porcelain_path(line: str) -> str:
    """Extract and POSIX-normalize the path portion of a porcelain v1 status line."""
    raw_path = line[3:] if len(line) > 3 else line
    if len(line) >= 2 and line[0] in ("R", "C") and " -> " in raw_path:
        raw_path = raw_path.split(" -> ", 1)[1]
    return normalize_posix_path(raw_path)


def _check_worktree_clean(repo_path: Path):
    """Run check 2 (worktree cleanliness via git status --porcelain --untracked-files=all)."""
    try:
        code, stdout, stderr = _run_git(
            repo_path,
            "-c",
            "core.quotePath=false",
            "status",
            "--porcelain",
            "--untracked-files=all",
        )
    except HandbackError as exc:
        msg = f"worktree_clean: {exc}"
        return {"ok": False, "status": "ERROR", "dirty_paths": [], "reasons": [msg]}, True

    if code != 0:
        msg = f"worktree_clean: git status failed (exit {code}): {stderr}"
        return {"ok": False, "status": "ERROR", "dirty_paths": [], "reasons": [msg]}, True

    dirty_lines = [ln for ln in stdout.splitlines() if ln]
    if dirty_lines:
        dirty_paths = [_extract_porcelain_path(ln) for ln in dirty_lines]
        preview = ", ".join(dirty_paths[:10])
        msg = f"worktree_clean: worktree has {len(dirty_paths)} uncommitted entry(s): {preview}"
        return {
            "ok": False,
            "status": "FAIL",
            "dirty_paths": dirty_paths,
            "reasons": [msg],
        }, False

    return {"ok": True, "status": "PASS", "dirty_paths": [], "reasons": []}, False


def _check_scope(repo_path: Path, base_ref: str, head_sha: str, raw_scope):
    """Run check 3 (all committed diff paths match non-empty declared scope globs)."""
    if raw_scope is not None and not isinstance(raw_scope, (list, tuple)):
        msg = "scope: declared scope must be a list of glob strings"
        return {
            "ok": False,
            "status": "ERROR",
            "declared_scope": [],
            "diff_paths": [],
            "out_of_scope": [],
            "reasons": [msg],
        }, True

    if raw_scope is not None and any(not isinstance(g, str) for g in raw_scope):
        msg = "scope: every declared scope entry must be a string"
        return {
            "ok": False,
            "status": "ERROR",
            "declared_scope": [],
            "diff_paths": [],
            "out_of_scope": [],
            "reasons": [msg],
        }, True

    try:
        code, stdout, stderr = _run_git(
            repo_path,
            "-c",
            "core.quotePath=false",
            "diff",
            "--no-renames",
            "--name-only",
            f"{base_ref}...{head_sha}",
        )
    except HandbackError as exc:
        msg = f"scope: {exc}"
        return {
            "ok": False,
            "status": "ERROR",
            "declared_scope": [],
            "diff_paths": [],
            "out_of_scope": [],
            "reasons": [msg],
        }, True

    if code != 0:
        msg = f"scope: git diff {base_ref}...{head_sha[:12]} failed (exit {code}): {stderr}"
        return {
            "ok": False,
            "status": "ERROR",
            "declared_scope": [],
            "diff_paths": [],
            "out_of_scope": [],
            "reasons": [msg],
        }, True

    diff_paths = [normalize_posix_path(line) for line in stdout.splitlines() if line.strip()]

    if raw_scope is None or len(raw_scope) == 0:
        msg = "scope: declared scope is empty (fail-closed)"
        return {
            "ok": False,
            "status": "FAIL",
            "declared_scope": [],
            "diff_paths": diff_paths,
            "out_of_scope": list(diff_paths),
            "reasons": [msg],
        }, False

    norm_globs = [normalize_posix_path(g) for g in raw_scope]
    invalid_globs = [
        g for g in norm_globs if not g or g.endswith("/") or ".." in g.split("/")
    ]
    if invalid_globs:
        msg = "scope: declared scope contains empty or invalid glob pattern"
        return {
            "ok": False,
            "status": "FAIL",
            "declared_scope": norm_globs,
            "diff_paths": diff_paths,
            "out_of_scope": list(diff_paths),
            "reasons": [msg],
        }, False

    out_of_scope = [
        path for path in diff_paths if not any(glob_match(path, glob) for glob in norm_globs)
    ]
    if out_of_scope:
        msg = "scope: diff paths outside declared scope: " + ", ".join(out_of_scope)
        return {
            "ok": False,
            "status": "FAIL",
            "declared_scope": norm_globs,
            "diff_paths": diff_paths,
            "out_of_scope": out_of_scope,
            "reasons": [msg],
        }, False

    return {
        "ok": True,
        "status": "PASS",
        "declared_scope": norm_globs,
        "diff_paths": diff_paths,
        "out_of_scope": [],
        "reasons": [],
    }, False


def _check_recorded_tests(handback: dict):
    """Run check 4 (recorded test_run has non-empty str command and strict int exit_code == 0)."""
    if "test_run" not in handback or handback["test_run"] is None:
        msg = "tests: missing test_run in handback"
        return {
            "ok": False,
            "status": "FAIL",
            "command": None,
            "exit_code": None,
            "reasons": [msg],
        }, False

    test_run = handback["test_run"]
    if not isinstance(test_run, dict):
        msg = "tests: test_run must be an object with command and exit_code"
        return {
            "ok": False,
            "status": "FAIL",
            "command": None,
            "exit_code": None,
            "reasons": [msg],
        }, False

    command = test_run.get("command")
    has_exit_code = "exit_code" in test_run
    exit_code = test_run.get("exit_code")

    reasons = []
    if not isinstance(command, str) or not command.strip():
        reasons.append("tests: test_run.command must be a non-empty string")

    if not has_exit_code or exit_code is None:
        reasons.append("tests: test_run.exit_code is missing")
    elif isinstance(exit_code, bool) or not isinstance(exit_code, int):
        reasons.append(
            f"tests: test_run.exit_code must be int (bool rejected), got {exit_code!r}"
        )
    elif exit_code != 0:
        reasons.append(f"tests: recorded test command exited {exit_code} (expected 0)")

    clean_cmd = command if isinstance(command, str) else None
    clean_code = (
        exit_code
        if (not isinstance(exit_code, bool) and isinstance(exit_code, int))
        else None
    )

    if reasons:
        return {
            "ok": False,
            "status": "FAIL",
            "command": clean_cmd,
            "exit_code": clean_code,
            "reasons": reasons,
        }, False

    return {
        "ok": True,
        "status": "PASS",
        "command": clean_cmd,
        "exit_code": clean_code,
        "reasons": [],
    }, False


def verify_handback(
    handback,
    *,
    repo=None,
    scope=None,
    receipt_envelope=None,
    base_ref="origin/main",
    main_ref="origin/main",
    max_behind=50,
    required=None,
    pubkey_path=None,
    hmac_secret=None,
    keyring=None,
    allow_no_required=False,
) -> dict:
    """Verify a lane hand-back payload against repository state and signed receipt.

    Returns a typed verdict dictionary with schema version 1:
    `{'v': 1, 'ok': bool, 'verdict': 'ACCEPT'|'REJECT'|'ERROR', 'lane': str|None,
    'head_sha': str|None, 'checks': {...}, 'reasons': [...]}`.
    """
    checks = _default_checks()
    if not isinstance(handback, dict):
        return _make_verdict(
            "ERROR",
            lane=None,
            head_sha=None,
            checks=checks,
            reasons=["malformed handback: expected a JSON object"],
        )

    raw_lane = handback.get("lane")
    if raw_lane is not None and not isinstance(raw_lane, str):
        return _make_verdict(
            "ERROR",
            lane=None,
            head_sha=None,
            checks=checks,
            reasons=["malformed handback.lane: expected string or null"],
        )
    lane = raw_lane

    if not isinstance(base_ref, str) or not base_ref.strip():
        return _make_verdict(
            "ERROR",
            lane=lane,
            head_sha=None,
            checks=checks,
            reasons=["malformed base_ref: expected non-empty ref string"],
        )
    if not isinstance(main_ref, str) or not main_ref.strip():
        return _make_verdict(
            "ERROR",
            lane=lane,
            head_sha=None,
            checks=checks,
            reasons=["malformed main_ref: expected non-empty ref string"],
        )
    if isinstance(max_behind, bool) or not isinstance(max_behind, int) or max_behind < 0:
        return _make_verdict(
            "ERROR",
            lane=lane,
            head_sha=None,
            checks=checks,
            reasons=["malformed max_behind: expected non-negative integer"],
        )
    if not isinstance(allow_no_required, bool):
        return _make_verdict(
            "ERROR",
            lane=lane,
            head_sha=None,
            checks=checks,
            reasons=["malformed allow_no_required: expected bool"],
        )

    try:
        required_list = _normalize_required(required)
    except HandbackError as exc:
        return _make_verdict(
            "ERROR",
            lane=lane,
            head_sha=None,
            checks=checks,
            reasons=[str(exc)],
        )

    raw_repo = repo if repo is not None else handback.get("repo", ".")
    if raw_repo is None:
        raw_repo = "."
    if not isinstance(raw_repo, (str, Path)) or not str(raw_repo).strip():
        return _make_verdict(
            "ERROR",
            lane=lane,
            head_sha=None,
            checks=checks,
            reasons=["malformed repo: expected directory path"],
        )

    try:
        repo_path = Path(raw_repo).resolve()
    except OSError as exc:
        return _make_verdict(
            "ERROR",
            lane=lane,
            head_sha=None,
            checks=checks,
            reasons=[f"cannot resolve repo path {raw_repo}: {exc}"],
        )

    if not repo_path.is_dir():
        return _make_verdict(
            "ERROR",
            lane=lane,
            head_sha=None,
            checks=checks,
            reasons=[f"repo directory does not exist: {repo_path}"],
        )

    try:
        rc_head, out_head, err_head = _run_git(
            repo_path, "rev-parse", "--verify", "HEAD^{commit}"
        )
    except HandbackError as exc:
        return _make_verdict(
            "ERROR",
            lane=lane,
            head_sha=None,
            checks=checks,
            reasons=[str(exc)],
        )

    head_candidate = out_head.strip()
    if rc_head != 0 or not _SHA_RE.match(head_candidate):
        detail = err_head or head_candidate or "unresolvable HEAD"
        return _make_verdict(
            "ERROR",
            lane=lane,
            head_sha=None,
            checks=checks,
            reasons=[f"cannot resolve HEAD commit in {repo_path}: {detail}"],
        )
    head_sha = head_candidate

    reasons = []
    has_error = False

    receipt_res, receipt_err = _check_receipt(
        handback,
        repo_path=repo_path,
        head_sha=head_sha,
        receipt_envelope=receipt_envelope,
        main_ref=main_ref,
        max_behind=max_behind,
        required_list=required_list,
        pubkey_path=pubkey_path,
        hmac_secret=hmac_secret,
        keyring=keyring,
        allow_no_required=allow_no_required,
    )
    checks["receipt"] = receipt_res
    has_error = has_error or receipt_err
    reasons.extend(receipt_res["reasons"])

    clean_res, clean_err = _check_worktree_clean(repo_path)
    checks["worktree_clean"] = clean_res
    has_error = has_error or clean_err
    reasons.extend(clean_res["reasons"])

    effective_scope = scope if scope is not None else handback.get("scope")
    scope_res, scope_err = _check_scope(repo_path, base_ref, head_sha, effective_scope)
    checks["scope"] = scope_res
    has_error = has_error or scope_err
    reasons.extend(scope_res["reasons"])

    tests_res, tests_err = _check_recorded_tests(handback)
    checks["tests"] = tests_res
    has_error = has_error or tests_err
    reasons.extend(tests_res["reasons"])

    if has_error:
        verdict = "ERROR"
    elif all(item["ok"] is True for item in checks.values()):
        verdict = "ACCEPT"
    else:
        verdict = "REJECT"

    return _make_verdict(
        verdict,
        lane=lane,
        head_sha=head_sha,
        checks=checks,
        reasons=reasons,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser for verify_handback."""
    parser = argparse.ArgumentParser(
        prog="verify_handback.py",
        description="Verify a worker lane hand-back before orchestrator intake.",
    )
    parser.add_argument(
        "--handback",
        default=None,
        metavar="FILE|-",
        help="Hand-back JSON file path, or - to read from stdin",
    )
    parser.add_argument(
        "--repo",
        default=None,
        metavar="DIR",
        help="Repository or worktree directory (default: handback.repo or cwd)",
    )
    parser.add_argument(
        "--scope",
        action="append",
        default=None,
        metavar="GLOB",
        help="Declared scope glob (repeatable or comma-separated; overrides handback.scope)",
    )
    parser.add_argument(
        "--receipt",
        default=None,
        metavar="FILE",
        help="Signed receipt envelope JSON file, or - for stdin",
    )
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        metavar="REF",
        help="Base git ref for diff scope check (default: origin/main)",
    )
    parser.add_argument(
        "--main-ref",
        default="origin/main",
        metavar="REF",
        help="Trunk git ref for receipt freshness check (default: origin/main)",
    )
    parser.add_argument(
        "--max-behind",
        type=int,
        default=50,
        metavar="N",
        help="Maximum commits receipt base_sha may lag main-ref (default: 50)",
    )
    parser.add_argument(
        "--required",
        default=",".join(receipt_common.DEFAULT_REQUIRED_PARTS),
        metavar="PARTS",
        help="Comma-separated required receipt part names",
    )
    parser.add_argument(
        "--pubkey",
        default=None,
        metavar="PATH",
        help="Ed25519 public key path (default: <repo>/tools/receipt_pubkey.pub)",
    )
    parser.add_argument(
        "--keyring",
        default=None,
        metavar="DIR",
        help="Directory of extra registered Ed25519 public keys",
    )
    parser.add_argument(
        "--allow-no-required-parts",
        action="store_true",
        help="Accept an empty --required parts list",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit compact single-line JSON verdict",
    )
    return parser


def _read_stdin_text() -> str:
    """Read UTF-8 text from sys.stdin safely across locales."""
    buf = getattr(sys.stdin, "buffer", None)
    if buf is not None:
        return buf.read().decode("utf-8", errors="replace")
    return sys.stdin.read()


def _load_cli_json(path_or_dash: str, label: str):
    """Load JSON payload from a path or `-` (stdin)."""
    if path_or_dash == "-":
        try:
            return json.loads(_read_stdin_text())
        except ValueError as exc:
            raise HandbackError(f"cannot parse {label} JSON from stdin: {exc}") from exc
    path = Path(path_or_dash)
    if not path.exists():
        raise HandbackError(f"{label} file does not exist: {path_or_dash}")
    if not path.is_file():
        raise HandbackError(f"{label} path is not a regular file: {path_or_dash}")
    return _read_json_file(path, label)


def _flatten_cli_scope(raw_scopes) -> list | None:
    """Flatten repeatable/comma-separated `--scope` values; preserve explicit empty override."""
    if raw_scopes is None:
        return None
    flattened = []
    for entry in raw_scopes:
        for piece in entry.split(","):
            stripped = piece.strip()
            if stripped:
                flattened.append(stripped)
    return flattened


def _emit_verdict_stdout(result: dict, compact: bool) -> None:
    """Write ASCII-safe JSON verdict to stdout."""
    if compact:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=True))


def main(argv=None, environ=None) -> int:
    """CLI entry point returning 0 (ACCEPT), 1 (REJECT), or 2 (ERROR)."""
    environ = os.environ if environ is None else environ
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = int(exc.code) if exc.code is not None else 0
        if code == 0:
            return 0
        err_verdict = _make_verdict(
            "ERROR",
            lane=None,
            head_sha=None,
            checks=_default_checks(),
            reasons=["invalid CLI arguments"],
        )
        _emit_verdict_stdout(err_verdict, compact=True)
        return 2

    if not args.handback:
        err_verdict = _make_verdict(
            "ERROR",
            lane=None,
            head_sha=None,
            checks=_default_checks(),
            reasons=["--handback is required"],
        )
        _emit_verdict_stdout(err_verdict, compact=args.json)
        return 2

    if args.handback == "-" and args.receipt == "-":
        err_verdict = _make_verdict(
            "ERROR",
            lane=None,
            head_sha=None,
            checks=_default_checks(),
            reasons=["cannot read both --handback and --receipt from stdin"],
        )
        _emit_verdict_stdout(err_verdict, compact=args.json)
        return 2

    try:
        handback_payload = _load_cli_json(args.handback, "handback")
    except HandbackError as exc:
        err_verdict = _make_verdict(
            "ERROR",
            lane=None,
            head_sha=None,
            checks=_default_checks(),
            reasons=[str(exc)],
        )
        _emit_verdict_stdout(err_verdict, compact=args.json)
        return 2

    receipt_obj = None
    if args.receipt is not None:
        if args.receipt != "-" and not Path(args.receipt).exists():
            receipt_obj = _MissingReceiptFile(args.receipt)
        else:
            try:
                receipt_obj = _load_cli_json(args.receipt, "receipt")
            except HandbackError as exc:
                lane_hint = (
                    handback_payload.get("lane")
                    if isinstance(handback_payload, dict)
                    and isinstance(handback_payload.get("lane"), str)
                    else None
                )
                err_verdict = _make_verdict(
                    "ERROR",
                    lane=lane_hint,
                    head_sha=None,
                    checks=_default_checks(),
                    reasons=[str(exc)],
                )
                _emit_verdict_stdout(err_verdict, compact=args.json)
                return 2

    cli_scope = _flatten_cli_scope(args.scope)
    required_parts = [p.strip() for p in args.required.split(",") if p.strip()]

    result = verify_handback(
        handback_payload,
        repo=args.repo,
        scope=cli_scope,
        receipt_envelope=receipt_obj,
        base_ref=args.base_ref,
        main_ref=args.main_ref,
        max_behind=args.max_behind,
        required=required_parts,
        pubkey_path=args.pubkey,
        hmac_secret=environ.get(receipt_common.HMAC_ENV),
        keyring=args.keyring,
        allow_no_required=args.allow_no_required_parts,
    )
    _emit_verdict_stdout(result, compact=args.json)
    return exit_code_for_verdict(result)


if __name__ == "__main__":
    sys.exit(main())
