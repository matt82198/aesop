#!/usr/bin/env python3
"""Fail-closed continuous sync controller for the private macOS fork.

INDEX: Fail-closed sync controller for maintaining a private macOS fork against upstream origin/main (--status, --plan via git merge-tree, --sync with post-merge Mac invariant audit and rollback, --push); stdlib-only, ASCII-only.

Modes:
  --status  Fetch upstream and report ahead/behind/dirty/branch/commits.
  --plan    Fetch upstream and preview merge via `git merge-tree --write-tree`
            without touching the working tree; flags Mac-owned path conflicts.
  --sync    Verify clean non-detached working tree and non-protected target,
            merge upstream, run audit_mac_invariants(repo), and roll back with
            `git reset --hard <pre_merge_sha>` on any violation. With --push,
            push the verified result to --fork-remote / --fork-branch.

Exit codes:
  0  Success (status clean, plan clean, or sync + audit + optional push passed)
  1  Fail-closed refusal (plan has conflicts, merge conflict, or Mac audit failed)
  2  Precondition/operational error (dirty tree, detached HEAD, protected
     origin/main|master target, invalid args, git/fetch/push failure)
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_TIMEOUT = 60
MAC_OWNED_PREFIXES = (".github/workflows/", "hooks/", "daemons/")
PROTECTED_UPSTREAM_BRANCHES = frozenset({"main", "master"})
SCRUB_GIT_ENV_KEYS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
)
BINARY_EXTENSIONS = frozenset({
    ".bin", ".exe", ".dll", ".so", ".dylib",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".rar",
    ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".wav", ".mov",
})
CODE_AUDIT_PREFIXES = (
    ".github/workflows/",
    "hooks/",
    "daemons/",
    "tools/",
    "bin/",
    "scripts/",
)
_WINDOWS_DRIVE_PREFIX = "C:" + "(?:/|\\\\{1,2})"
_WINDOWS_USERS_SEG = "Users" + "(?:/|\\\\{1,2})"
WINDOWS_USER_PATH_RE = re.compile(
    _WINDOWS_DRIVE_PREFIX + _WINDOWS_USERS_SEG + r"[a-zA-Z0-9_\-\.]+",
    re.IGNORECASE,
)
_MERGE_STAGE_RE = re.compile(r"^[0-7]+\s+[0-9a-fA-F]+\s+[123]\t(.+)$")
_CONFLICT_LINE_RE = re.compile(r"^CONFLICT\s+\([^)]+\):\s+.*?\bin\s+(.+?)\s*$")
_JOB_KEY_RE = re.compile(r"^(\s+)([A-Za-z0-9_-]+):\s*(?:#.*)?$")
_RUNS_ON_RE = re.compile(r"^\s+runs-on:\s*(.*?)\s*(?:#.*)?$")


def _git_env():
    """Return environment copy with GIT_DIR/GIT_WORK_TREE scrubbed."""
    env = dict(os.environ)
    for key in SCRUB_GIT_ENV_KEYS:
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(repo, args, timeout=DEFAULT_TIMEOUT):
    """Run git inside repo with scrubbed env, UTF-8 decoding, and timeout."""
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            env=_git_env(),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        return res.returncode, res.stdout, res.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"git {' '.join(args)} timed out after {timeout}s"
    except OSError as exc:
        return 127, "", str(exc)


def _run_git_bytes(repo, args, timeout=DEFAULT_TIMEOUT):
    """Run git inside repo returning raw stdout bytes for NUL-delimited commands."""
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            env=_git_env(),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        err = res.stderr.decode("utf-8", errors="replace") if res.stderr else ""
        return res.returncode, res.stdout, err
    except subprocess.TimeoutExpired:
        return 124, b"", f"git {' '.join(args)} timed out after {timeout}s"
    except OSError as exc:
        return 127, b"", str(exc)


def _normalize_branch(branch):
    """Strip refs/heads/ prefix when comparing branch names."""
    if not branch:
        return ""
    text = str(branch).strip()
    if text.startswith("refs/heads/"):
        return text[len("refs/heads/"):]
    return text


LINUX_EXEMPT_WORKFLOWS = frozenset({"codeql.yml", "scorecard.yml"})


def validate_fork_target(
    fork_remote,
    fork_branch,
    current_branch=None,
    check_remote=True,
    allow_origin_branch=False,
):
    """Refuse syncing/pushing onto protected branches (main/master) or origin."""
    remote = (fork_remote or "").strip()
    target = _normalize_branch(fork_branch) or _normalize_branch(current_branch)
    if target in PROTECTED_UPSTREAM_BRANCHES:
        return (
            f"Refused: target branch '{target}' is a protected trunk branch"
        )
    if check_remote and remote == "origin" and not allow_origin_branch:
        return (
            f"Refused: fork remote '{remote}' is the upstream remote "
            "(pass --allow-origin-branch to push an isolated port/* branch)"
        )
    return None


def _current_branch(repo, timeout=DEFAULT_TIMEOUT):
    """Return (branch_name_or_none, is_detached)."""
    rc, out, _ = _run_git(
        repo, ["symbolic-ref", "--quiet", "--short", "HEAD"], timeout=timeout
    )
    if rc == 0 and out.strip():
        return out.strip(), False
    return None, True


def _is_dirty(repo, timeout=DEFAULT_TIMEOUT):
    """Return True if working tree has staged, unstaged, or untracked changes."""
    rc, out, err = _run_git(repo, ["status", "--porcelain"], timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"git status --porcelain failed: {err.strip()}")
    return bool(out.strip())


def _rev_parse(repo, ref, timeout=DEFAULT_TIMEOUT):
    """Resolve ref to full SHA, or raise RuntimeError."""
    rc, out, err = _run_git(repo, ["rev-parse", "--verify", ref], timeout=timeout)
    if rc != 0 or not out.strip():
        raise RuntimeError(f"cannot resolve ref {ref}: {err.strip()}")
    return out.strip()


def _fetch_upstream(repo, upstream_remote, timeout=DEFAULT_TIMEOUT):
    """Fetch upstream_remote; raise RuntimeError on failure."""
    rc, _, err = _run_git(
        repo, ["fetch", "--quiet", upstream_remote], timeout=timeout
    )
    if rc != 0:
        raise RuntimeError(
            f"git fetch {upstream_remote} failed (rc={rc}): {err.strip()}"
        )


def _resolve_upstream_ref(repo, upstream_remote, upstream_branch, timeout=DEFAULT_TIMEOUT):
    """Return verified remote-tracking ref '<remote>/<branch>'."""
    ref = f"{upstream_remote}/{upstream_branch}"
    rc, _, err = _run_git(
        repo,
        ["rev-parse", "--verify", "--quiet", f"refs/remotes/{ref}"],
        timeout=timeout,
    )
    if rc != 0:
        raise RuntimeError(
            f"upstream ref refs/remotes/{ref} not found: {err.strip()}"
        )
    return ref


def _ahead_behind(repo, head_ref, upstream_ref, timeout=DEFAULT_TIMEOUT):
    """Return (ahead, behind) commit counts between head_ref and upstream_ref."""
    rc, out, err = _run_git(
        repo,
        ["rev-list", "--left-right", "--count", f"{head_ref}...{upstream_ref}"],
        timeout=timeout,
    )
    if rc != 0:
        raise RuntimeError(f"git rev-list failed: {err.strip()}")
    parts = out.strip().split()
    if len(parts) != 2:
        raise RuntimeError(f"unexpected rev-list --count output: {out!r}")
    return int(parts[0]), int(parts[1])


def _upstream_commits(repo, head_ref, upstream_ref, timeout=DEFAULT_TIMEOUT):
    """Return list of new upstream commits reachable from upstream_ref not head_ref."""
    rc, out, err = _run_git(
        repo,
        ["log", "--reverse", "--format=%H\t%s", f"{head_ref}..{upstream_ref}"],
        timeout=timeout,
    )
    if rc != 0:
        raise RuntimeError(f"git log failed: {err.strip()}")
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        commits.append({"sha": sha.strip(), "subject": subject})
    return commits


def _parse_merge_tree_output(stdout):
    """Parse `git merge-tree --write-tree` output into (tree_sha, conflicts)."""
    lines = stdout.splitlines()
    tree_sha = lines[0].strip() if lines else ""
    conflicts = set()
    in_header = True
    for line in lines[1:]:
        if in_header:
            if line == "":
                in_header = False
                continue
            m = _MERGE_STAGE_RE.match(line)
            if m:
                conflicts.add(m.group(1))
        else:
            m2 = _CONFLICT_LINE_RE.match(line)
            if m2 and not conflicts:
                conflicts.add(m2.group(1))
    return tree_sha, sorted(conflicts)


def _is_exception_line(line):
    """Match portability_check.py exception markers (example/default/e.g.)."""
    low = line.lower()
    return any(marker in low for marker in ("example", "default", "e.g."))


def _is_text_file(path_obj):
    """Return False for known binary extensions or NUL-containing files."""
    if path_obj.suffix.lower() in BINARY_EXTENSIONS:
        return False
    try:
        chunk = path_obj.read_bytes()[:4096]
    except OSError:
        return True
    return b"\x00" not in chunk


def _check_single_workflow(wf_path, rel_posix):
    """Check one workflow file for disallowed ubuntu-latest runner references."""
    try:
        text = wf_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{
            "rule": "ci_workflow_unreadable",
            "file": rel_posix,
            "line": 0,
            "message": f"cannot read {rel_posix}: {exc}",
        }]

    violations = []
    in_jobs = False
    job_indent = None
    current_job = None
    in_runs_on_list = False
    runs_on_indent = None

    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))

        if not in_jobs:
            if indent == 0 and re.match(r"^jobs:\s*(?:#.*)?$", raw):
                in_jobs = True
            continue

        if indent == 0:
            break

        if job_indent is None:
            job_indent = indent

        if indent == job_indent:
            in_runs_on_list = False
            m_job = _JOB_KEY_RE.match(raw)
            current_job = m_job.group(2) if m_job else None
            continue

        if not current_job:
            continue

        if in_runs_on_list:
            if indent > runs_on_indent and stripped.startswith("-"):
                item = stripped[1:].split("#", 1)[0].strip().strip("'\"")
                if item == "ubuntu-latest":
                    violations.append({
                        "rule": "ci_ubuntu_runner",
                        "file": rel_posix,
                        "line": lineno,
                        "message": f"jobs.{current_job} reintroduces ubuntu-latest",
                    })
                continue
            in_runs_on_list = False

        if "ubuntu-latest" in stripped and (
            stripped.startswith("os:") or stripped.startswith("- ubuntu-latest")
        ):
            violations.append({
                "rule": "ci_ubuntu_runner",
                "file": rel_posix,
                "line": lineno,
                "message": f"jobs.{current_job} matrix reintroduces ubuntu-latest",
            })
            continue

        m_run = _RUNS_ON_RE.match(raw)
        if m_run:
            val = m_run.group(1).strip().strip("'\"")
            if not val:
                in_runs_on_list = True
                runs_on_indent = indent
            elif val == "ubuntu-latest" or (
                val.startswith("[") and "ubuntu-latest" in val
            ):
                violations.append({
                    "rule": "ci_ubuntu_runner",
                    "file": rel_posix,
                    "line": lineno,
                    "message": f"jobs.{current_job} reintroduces runs-on: ubuntu-latest",
                })

    return violations


def _check_ci_workflow(repo_path):
    """Check all non-exempt .github/workflows/*.yml files against ubuntu-latest."""
    wf_dir = repo_path / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    violations = []
    for wf_path in sorted(wf_dir.glob("*.yml")):
        if wf_path.name in LINUX_EXEMPT_WORKFLOWS:
            continue
        rel_posix = f".github/workflows/{wf_path.name}"
        violations.extend(_check_single_workflow(wf_path, rel_posix))
    return violations


def _load_portability_baseline(repo_path):
    """Load .portability-baseline.json violations map; fail closed if malformed."""
    baseline_path = repo_path / ".portability-baseline.json"
    if not baseline_path.exists():
        return {}
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    violations = data.get("violations", {})
    if not isinstance(violations, dict):
        raise ValueError("malformed .portability-baseline.json: violations must be dict")
    return {str(k).replace("\\", "/"): int(v) for k, v in violations.items()}


def _list_tracked_files(repo_path, timeout=DEFAULT_TIMEOUT):
    """Return sorted list of git-tracked relative POSIX paths."""
    rc, out_bytes, err = _run_git_bytes(
        repo_path, ["ls-files", "-z"], timeout=timeout
    )
    if rc != 0:
        raise RuntimeError(f"git ls-files -z failed: {err.strip()}")
    text = out_bytes.decode("utf-8", errors="replace")
    return sorted(p.replace("\\", "/") for p in text.split("\0") if p)


def _select_audit_files(repo_path, tracked_rel_paths):
    """Select tracked files subject to the Windows-user-path Mac invariant."""
    pkg_path = repo_path / "package.json"
    if pkg_path.is_file():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot parse package.json: {exc}") from exc
        patterns = pkg.get("files") if isinstance(pkg, dict) else None
        if isinstance(patterns, list) and patterns:
            selected = set()
            for pattern in patterns:
                norm = str(pattern).replace("\\", "/")
                full_pattern = os.path.join(str(repo_path), norm).replace("\\", "/")
                for match in glob.glob(full_pattern, recursive=True):
                    p = Path(match)
                    if p.is_file():
                        try:
                            rel = p.relative_to(repo_path).as_posix()
                            selected.add(rel)
                        except ValueError:
                            pass
            for rel in tracked_rel_paths:
                if rel.startswith(CODE_AUDIT_PREFIXES):
                    selected.add(rel)
            return sorted(selected)

    return [
        rel
        for rel in tracked_rel_paths
        if not rel.startswith("tests/") and rel != ".portability-baseline.json"
    ]


def _check_windows_user_paths(repo_path, timeout=DEFAULT_TIMEOUT):
    """Detect un-baselined Windows user-profile literal paths."""
    baseline = _load_portability_baseline(repo_path)
    tracked = _list_tracked_files(repo_path, timeout=timeout)
    targets = _select_audit_files(repo_path, tracked)

    violations = []
    for rel in targets:
        file_path = repo_path / rel
        if not file_path.is_file() or not _is_text_file(file_path):
            continue
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            violations.append({
                "rule": "file_unreadable",
                "file": rel,
                "line": 0,
                "message": f"cannot read tracked file: {exc}",
            })
            continue

        matches = []
        for lineno, line in enumerate(lines, 1):
            if _is_exception_line(line):
                continue
            for m in WINDOWS_USER_PATH_RE.finditer(line):
                matches.append((lineno, m.group(0)))

        allowed = baseline.get(f"{rel}@windows_user_path", 0)
        if len(matches) > allowed:
            for lineno, found in matches[allowed:]:
                violations.append({
                    "rule": "unbaselined_windows_user_path",
                    "file": rel,
                    "line": lineno,
                    "message": (
                        f"un-baselined Windows user path '{found}' "
                        f"(count={len(matches)}, baseline={allowed})"
                    ),
                })

    return violations


def audit_mac_invariants(repo, timeout=DEFAULT_TIMEOUT):
    """Verify post-merge Mac invariants; fail closed on any violation or error."""
    repo_path = Path(repo).resolve()
    try:
        violations = []
        violations.extend(_check_ci_workflow(repo_path))
        violations.extend(_check_windows_user_paths(repo_path, timeout=timeout))
        return {"ok": len(violations) == 0, "violations": violations}
    except Exception as exc:
        return {
            "ok": False,
            "violations": [{
                "rule": "audit_error",
                "file": "",
                "line": 0,
                "message": str(exc),
            }],
        }


def run_status(
    repo,
    upstream_remote="origin",
    upstream_branch="main",
    fetch=True,
    timeout=DEFAULT_TIMEOUT,
):
    """Collect branch, dirty, ahead/behind, and new upstream commit list."""
    repo_path = Path(repo).resolve()
    try:
        if fetch:
            _fetch_upstream(repo_path, upstream_remote, timeout=timeout)
        upstream_ref = _resolve_upstream_ref(
            repo_path, upstream_remote, upstream_branch, timeout=timeout
        )
        branch, detached = _current_branch(repo_path, timeout=timeout)
        dirty = _is_dirty(repo_path, timeout=timeout)
        head_sha = _rev_parse(repo_path, "HEAD", timeout=timeout)
        upstream_sha = _rev_parse(
            repo_path, f"refs/remotes/{upstream_ref}", timeout=timeout
        )
        ahead, behind = _ahead_behind(
            repo_path, "HEAD", f"refs/remotes/{upstream_ref}", timeout=timeout
        )
        commits = _upstream_commits(
            repo_path, "HEAD", f"refs/remotes/{upstream_ref}", timeout=timeout
        )
        return 0, {
            "ok": True,
            "mode": "status",
            "branch": branch,
            "detached": detached,
            "dirty": dirty,
            "head_sha": head_sha,
            "upstream_ref": upstream_ref,
            "upstream_sha": upstream_sha,
            "ahead": ahead,
            "behind": behind,
            "upstream_commits": commits,
        }
    except Exception as exc:
        return 2, {
            "ok": False,
            "mode": "status",
            "error": str(exc),
        }


def run_plan(
    repo,
    upstream_remote="origin",
    upstream_branch="main",
    fetch=True,
    timeout=DEFAULT_TIMEOUT,
):
    """Preview upstream merge via `git merge-tree --write-tree` without tree writes."""
    repo_path = Path(repo).resolve()
    try:
        if fetch:
            _fetch_upstream(repo_path, upstream_remote, timeout=timeout)
        upstream_ref = _resolve_upstream_ref(
            repo_path, upstream_remote, upstream_branch, timeout=timeout
        )
        branch, detached = _current_branch(repo_path, timeout=timeout)
        dirty = _is_dirty(repo_path, timeout=timeout)
        ahead, behind = _ahead_behind(
            repo_path, "HEAD", f"refs/remotes/{upstream_ref}", timeout=timeout
        )
        commits = _upstream_commits(
            repo_path, "HEAD", f"refs/remotes/{upstream_ref}", timeout=timeout
        )
        rc, out, err = _run_git(
            repo_path,
            ["merge-tree", "--write-tree", "HEAD", f"refs/remotes/{upstream_ref}"],
            timeout=timeout,
        )
        if rc not in (0, 1):
            raise RuntimeError(
                f"git merge-tree --write-tree failed (rc={rc}): {err.strip()}"
            )
        tree_sha, conflicts = _parse_merge_tree_output(out)
        mac_owned = [
            p for p in conflicts if p.startswith(MAC_OWNED_PREFIXES)
        ]
        clean = (rc == 0) and not conflicts
        exit_code = 0 if clean else 1
        return exit_code, {
            "ok": clean,
            "mode": "plan",
            "clean": clean,
            "branch": branch,
            "detached": detached,
            "dirty": dirty,
            "upstream_ref": upstream_ref,
            "ahead": ahead,
            "behind": behind,
            "upstream_commits": commits,
            "tree_sha": tree_sha,
            "conflicts": conflicts,
            "mac_owned_conflicts": mac_owned,
            "has_mac_owned_conflicts": bool(mac_owned),
        }
    except Exception as exc:
        return 2, {
            "ok": False,
            "mode": "plan",
            "clean": False,
            "error": str(exc),
        }


def run_sync(
    repo,
    upstream_remote="origin",
    upstream_branch="main",
    fork_remote="mac-fork",
    fork_branch=None,
    push=False,
    allow_origin_branch=False,
    fetch=True,
    timeout=DEFAULT_TIMEOUT,
):
    """Fail-closed sync: precondition checks -> merge -> Mac audit -> rollback/push."""
    repo_path = Path(repo).resolve()
    try:
        branch, detached = _current_branch(repo_path, timeout=timeout)
        target_error = validate_fork_target(
            fork_remote,
            fork_branch,
            branch,
            check_remote=bool(push),
            allow_origin_branch=allow_origin_branch,
        )
        if target_error:
            return 2, {
                "ok": False,
                "mode": "sync",
                "status": "REFUSED_PROTECTED_TARGET",
                "error": target_error,
            }

        dirty = _is_dirty(repo_path, timeout=timeout)
        if dirty or detached:
            reason = (
                "working tree is dirty"
                if dirty
                else "HEAD is detached"
            )
            return 2, {
                "ok": False,
                "mode": "sync",
                "status": "REFUSED_PRECONDITION",
                "dirty": dirty,
                "detached": detached,
                "error": f"Refused: {reason}",
            }

        target_branch = _normalize_branch(fork_branch) or branch
        pre_merge_sha = _rev_parse(repo_path, "HEAD", timeout=timeout)

        if fetch:
            _fetch_upstream(repo_path, upstream_remote, timeout=timeout)
        upstream_ref = _resolve_upstream_ref(
            repo_path, upstream_remote, upstream_branch, timeout=timeout
        )
        _, behind = _ahead_behind(
            repo_path, "HEAD", f"refs/remotes/{upstream_ref}", timeout=timeout
        )

        merged = False
        if behind > 0:
            rc, out, err = _run_git(
                repo_path,
                ["merge", "--no-edit", f"refs/remotes/{upstream_ref}"],
                timeout=timeout,
            )
            if rc != 0:
                _run_git(repo_path, ["merge", "--abort"], timeout=timeout)
                _run_git(repo_path, ["reset", "--hard", pre_merge_sha], timeout=timeout)
                return 1, {
                    "ok": False,
                    "mode": "sync",
                    "status": "MERGE_CONFLICT",
                    "branch": branch,
                    "upstream_ref": upstream_ref,
                    "pre_merge_sha": pre_merge_sha,
                    "rolled_back": True,
                    "pushed": False,
                    "error": (err or out).strip() or "merge conflict",
                }
            merged = True

        audit = audit_mac_invariants(repo_path, timeout=timeout)
        if not audit["ok"]:
            rc_reset, _, err_reset = _run_git(
                repo_path, ["reset", "--hard", pre_merge_sha], timeout=timeout
            )
            if rc_reset != 0:
                return 2, {
                    "ok": False,
                    "mode": "sync",
                    "status": "ROLLBACK_FAILED",
                    "pre_merge_sha": pre_merge_sha,
                    "rolled_back": False,
                    "violations": audit["violations"],
                    "error": f"git reset --hard failed: {err_reset.strip()}",
                }
            return 1, {
                "ok": False,
                "mode": "sync",
                "status": "AUDIT_FAILED",
                "branch": branch,
                "upstream_ref": upstream_ref,
                "pre_merge_sha": pre_merge_sha,
                "rolled_back": True,
                "pushed": False,
                "violations": audit["violations"],
                "error": "Post-merge Mac invariant audit failed; rolled back",
            }

        head_sha = _rev_parse(repo_path, "HEAD", timeout=timeout)
        pushed = False
        if push:
            target_error = validate_fork_target(
                fork_remote,
                target_branch,
                branch,
                check_remote=True,
                allow_origin_branch=allow_origin_branch,
            )
            if target_error:
                return 2, {
                    "ok": False,
                    "mode": "sync",
                    "status": "REFUSED_PROTECTED_TARGET",
                    "error": target_error,
                }
            rc_push, _, err_push = _run_git(
                repo_path,
                ["push", fork_remote, f"HEAD:refs/heads/{target_branch}"],
                timeout=timeout,
            )
            if rc_push != 0:
                return 2, {
                    "ok": False,
                    "mode": "sync",
                    "status": "PUSH_FAILED",
                    "branch": branch,
                    "fork_remote": fork_remote,
                    "fork_branch": target_branch,
                    "head_sha": head_sha,
                    "pushed": False,
                    "error": f"git push failed: {err_push.strip()}",
                }
            pushed = True

        return 0, {
            "ok": True,
            "mode": "sync",
            "status": "SYNCED",
            "branch": branch,
            "upstream_ref": upstream_ref,
            "fork_remote": fork_remote,
            "fork_branch": target_branch,
            "pre_merge_sha": pre_merge_sha,
            "head_sha": head_sha,
            "merged": merged,
            "behind_merged": behind,
            "rolled_back": False,
            "pushed": pushed,
            "violations": [],
        }
    except Exception as exc:
        return 2, {
            "ok": False,
            "mode": "sync",
            "status": "ERROR",
            "error": str(exc),
        }


def _emit(payload, as_json):
    """Print structured JSON or concise human-readable summary."""
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    mode = payload.get("mode", "status")
    if not payload.get("ok") and payload.get("error"):
        print(f"mac_fork_sync [{mode}]: {payload['error']}", file=sys.stderr)
    elif mode == "status":
        print(
            f"branch={payload.get('branch')} dirty={payload.get('dirty')} "
            f"ahead={payload.get('ahead')} behind={payload.get('behind')} "
            f"upstream={payload.get('upstream_ref')}"
        )
    elif mode == "plan":
        print(
            f"plan clean={payload.get('clean')} conflicts={len(payload.get('conflicts', []))} "
            f"mac_owned={len(payload.get('mac_owned_conflicts', []))}"
        )
    elif mode == "sync":
        print(
            f"sync status={payload.get('status')} merged={payload.get('merged')} "
            f"pushed={payload.get('pushed')} head={payload.get('head_sha')}"
        )


def main(argv=None):
    """CLI entry point for mac_fork_sync."""
    parser = argparse.ArgumentParser(
        description="Fail-closed continuous sync controller for the private macOS fork."
    )
    parser.add_argument("--repo", default=".", help="Path to git repository (default: .)")
    parser.add_argument("--upstream-remote", default="origin", help="Upstream remote (default: origin)")
    parser.add_argument("--upstream-branch", default="main", help="Upstream branch (default: main)")
    parser.add_argument("--fork-remote", default="mac-fork", help="Fork remote (default: mac-fork)")
    parser.add_argument("--fork-branch", default=None, help="Target fork branch (default: current branch)")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--status", action="store_true", help="Report sync status (default)")
    mode_group.add_argument("--plan", action="store_true", help="Preview merge via git merge-tree")
    mode_group.add_argument("--sync", action="store_true", help="Merge upstream, audit invariants, optionally push")
    parser.add_argument("--push", action="store_true", help="Push synced branch to --fork-remote (requires --sync)")
    parser.add_argument(
        "--allow-origin-branch",
        action="store_true",
        help="Allow --push to a non-protected branch on origin (main/master remain hard-refused)",
    )
    parser.add_argument("--no-fetch", action="store_true", help="Skip git fetch before inspecting upstream ref")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Per-git-command timeout seconds")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    if args.push and not args.sync:
        payload = {
            "ok": False,
            "mode": "args",
            "error": "--push requires --sync",
        }
        _emit(payload, args.json)
        return 2

    fetch = not args.no_fetch
    if args.plan:
        rc, payload = run_plan(
            args.repo,
            upstream_remote=args.upstream_remote,
            upstream_branch=args.upstream_branch,
            fetch=fetch,
            timeout=args.timeout,
        )
    elif args.sync:
        rc, payload = run_sync(
            args.repo,
            upstream_remote=args.upstream_remote,
            upstream_branch=args.upstream_branch,
            fork_remote=args.fork_remote,
            fork_branch=args.fork_branch,
            push=args.push,
            allow_origin_branch=args.allow_origin_branch,
            fetch=fetch,
            timeout=args.timeout,
        )
    else:
        rc, payload = run_status(
            args.repo,
            upstream_remote=args.upstream_remote,
            upstream_branch=args.upstream_branch,
            fetch=fetch,
            timeout=args.timeout,
        )

    _emit(payload, args.json)
    return rc


if __name__ == "__main__":
    sys.exit(main())
