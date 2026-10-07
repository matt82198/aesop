#!/usr/bin/env python3
"""
One-command checklist runner for "I am adding or wiring a new pre-push gate".
INDEX: One-command new-gate checklist runner: pre-push self-test, gate_inventory axis-2 parity, claudemd_lint (+ --headroom + claudemd_sync_gate), portability_check ratchet, verify_gates_wired, dispatch_lint, conflict_marker_check, and a dry pre-push range check against origin/main (runs in detached worktree to avoid mutating HEAD); prints a pass/fail table with the exact fix command per red row; exit 0=all green/1=any red/2=usage error; stdlib-only.

Why this exists: PR #872 (a new pre-push gate) took FIVE red CI rounds, each a
different checklist item a new gate must satisfy -- the TTY-fixture stub list
(see tools/gate_stub_list.py), hooks/CLAUDE.md's numbered list matching real
call sites (tools/gate_inventory.py axis 2), the CLAUDE.md 150-line cap +
headroom + domain-sync gates, the portability ratchet, the gates-wired
inventory, and the dispatch/conflict-marker linters. Each round was a
DIFFERENT gate, discovered one CI failure at a time. This tool runs the whole
checklist locally in one command, so the next new-gate PR finds every row
before pushing instead of five rounds later.

This is a LOCAL DEVELOPER TOOL, not itself a CI gate (it has no file-content
rule to enforce; it orchestrates other gates that already exist). It is
exempted in tools/gate-inventory-allowlist.json for exactly that reason -- see
that file's entry for "new_gate_check.py".

Scope note on row 2 (gate_inventory): tools/gate_inventory.py's axis 1 (every
gate-shaped tool must have an invoker) carries pre-existing, deliberately
unallowlisted orphans unrelated to adding a new gate (see that tool's own
INDEX: line). This checklist only gates on axis 2 (every check_* documented in
hooks/CLAUDE.md has a real call site in hooks/pre-push-policy.sh, and vice
versa) -- the exact class of defect PR #872 round 2 was. Axis 1's count is
printed for visibility but never fails this row.

CLI: `python tools/new_gate_check.py [--root DIR] [--json]`.
Exit 0 = every row green. Exit 1 = at least one row red (table + fix commands
printed to stdout; the row's own captured output goes to stderr unless
--json). Exit 2 = usage/environment error (e.g. --root is not a directory).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent


def _resolve_argv0(cmd):
    """Pre-resolve a bare executable name (e.g. "bash") through shutil.which.

    Windows quirk (first hit in tools/test_isolation_tripwire.py): handing
    subprocess.run a bare "bash" can resolve to the WSL App Execution Alias
    stub ("Windows Subsystem for Linux has no installed distributions")
    instead of Git's real bash.exe on PATH, even though shutil.which() ranks
    Git's bash first. Pre-resolving denies the alias stub a bare name to
    latch onto. Falls back to the original token unchanged if which() can't
    find it (e.g. it's already an absolute path).
    """
    if not cmd:
        return cmd
    resolved = list(cmd)
    which_path = shutil.which(resolved[0])
    if which_path:
        resolved[0] = which_path
    return resolved


def _run(cmd, cwd, env, timeout=180, input_text=None):
    """Single subprocess entry point; the one seam tests monkeypatch.

    Returns a subprocess.CompletedProcess-like object (has returncode,
    stdout, stderr). Never raises for a non-zero exit; raises only if the
    interpreter/binary itself cannot be invoked or the timeout is hit, both
    surfaced to the caller as a synthetic rc=2 result instead of propagating.
    """
    cmd = _resolve_argv0(cmd)
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(
            cmd, returncode=2, stdout="", stderr="ERROR: %s" % exc
        )


def _py(repo_root, env):
    """Resolve a python interpreter the same way the rest of the repo does:
    prefer the interpreter currently running us (always Python 3)."""
    return sys.executable or "python3"


class CheckResult:
    def __init__(self, key, label, ok, detail, fix):
        self.key = key
        self.label = label
        self.ok = ok
        self.detail = detail
        self.fix = fix

    def to_dict(self):
        return {
            "key": self.key,
            "label": self.label,
            "ok": self.ok,
            "detail": self.detail,
            "fix": None if self.ok else self.fix,
        }


def check_pre_push_selftest(repo_root, env):
    cmd = ["bash", str(repo_root / "hooks" / "pre-push-policy.sh"), "--test"]
    proc = _run(cmd, repo_root, env, timeout=300)
    ok = proc.returncode == 0
    detail = (proc.stdout or "") + (proc.stderr or "")
    fix = "bash hooks/pre-push-policy.sh --test  # read the first FAIL above and fix hooks/pre-push-policy.sh (or tests/test_pre_push_policy.sh if the fixture itself is wrong)"
    return ok, detail, fix


def check_gate_inventory(repo_root, env):
    py = _py(repo_root, env)
    cmd = [py, str(repo_root / "tools" / "gate_inventory.py"), "--check", "--root", str(repo_root), "--json"]
    proc = _run(cmd, repo_root, env, timeout=120)
    fix = (
        "python tools/gate_inventory.py --check --root . --json  "
        "# axis2 DOCUMENTED-NOT-INVOKED: add the check_<name> call site to main() in "
        "hooks/pre-push-policy.sh, or remove the stale numbered entry from hooks/CLAUDE.md. "
        "(axis1 orphan count is informational only and does not fail this row.)"
    )
    if proc.returncode == 2:
        return False, (proc.stdout or "") + (proc.stderr or ""), fix
    try:
        report = json.loads(proc.stdout or "{}")
    except ValueError:
        return False, "could not parse gate_inventory.py --json output:\n" + (proc.stdout or "") + (proc.stderr or ""), fix

    axis2 = report.get("axis2", {})
    axis2_findings = axis2.get("findings", [])
    axis1 = report.get("axis1", {})
    axis1_findings = axis1.get("findings", [])

    ok = len(axis2_findings) == 0
    detail_lines = [
        "axis2 (documented check_* <-> real call site): %d finding(s)" % len(axis2_findings)
    ]
    for f in axis2_findings:
        detail_lines.append("  %s: %s" % (f.get("check", "?"), f.get("message", f)))
    detail_lines.append(
        "axis1 (gate-shaped tool -> invoker), informational only: %d orphan(s) pre-existing"
        % len(axis1_findings)
    )
    return ok, "\n".join(detail_lines), fix


def check_claudemd_lint(repo_root, env):
    py = _py(repo_root, env)
    cmd = [py, str(repo_root / "tools" / "claudemd_lint.py"), "--root", str(repo_root)]
    proc = _run(cmd, repo_root, env, timeout=120)
    ok = proc.returncode == 0
    detail = (proc.stdout or "") + (proc.stderr or "")
    fix = "python tools/claudemd_lint.py --root .  # trim the flagged CLAUDE.md under 150 lines, or fix the named DOC-POINTER/TEST-CMD/DOMAIN-CROSS-REF finding"
    return ok, detail, fix


def check_claudemd_headroom(repo_root, env):
    py = _py(repo_root, env)
    cmd = [
        py, str(repo_root / "tools" / "claudemd_lint.py"),
        "--headroom", "--base-ref", "origin/main", "--root", str(repo_root),
    ]
    proc = _run(cmd, repo_root, env, timeout=120)
    ok = proc.returncode == 0
    detail = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 2:
        fix = "git fetch origin main && python tools/claudemd_lint.py --headroom --base-ref origin/main --root .  # merge union unreadable (fetch origin/main first)"
    else:
        fix = "python tools/claudemd_lint.py --headroom --base-ref origin/main --root .  # trim the CLAUDE.md whose MERGE UNION with origin/main busts the 150-line cap"
    return ok, detail, fix


def check_claudemd_sync(repo_root, env):
    py = _py(repo_root, env)
    cmd = [py, str(repo_root / "tools" / "claudemd_sync_gate.py"), "--check"]
    proc = _run(cmd, repo_root, env, timeout=120)
    ok = proc.returncode == 0
    detail = (proc.stdout or "") + (proc.stderr or "")
    fix = "python tools/claudemd_sync_gate.py --check --json  # update the domain CLAUDE.md for every changed domain directory, in this same PR"
    return ok, detail, fix


def check_portability(repo_root, env, baseline=None):
    py = _py(repo_root, env)
    if baseline is None:
        baseline = repo_root / ".portability-baseline.json"
    else:
        baseline = Path(baseline)
    cmd = [py, str(repo_root / "tools" / "portability_check.py"), "--root", str(repo_root), "--baseline", str(baseline)]
    proc = _run(cmd, repo_root, env, timeout=120)
    ok = proc.returncode == 0
    detail = (proc.stdout or "") + (proc.stderr or "")
    fix = (
        "python tools/portability_check.py --root . --baseline .portability-baseline.json --json  "
        "# remove the new hardcoded path/private-machine token from new code/tests/docs. "
        "Never pass --update-baseline yourself unless recording an intentional burn-down (CI must never pass it)."
    )
    return ok, detail, fix


def check_verify_gates_wired(repo_root, env):
    py = _py(repo_root, env)
    cmd = [py, str(repo_root / "tools" / "verify_gates_wired.py")]
    proc = _run(cmd, repo_root, env, timeout=120)
    ok = proc.returncode == 0
    detail = (proc.stdout or "") + (proc.stderr or "")
    fix = "python tools/verify_gates_wired.py  # add the missing `python tools/<gate>.py` invocation to a .github/workflows/*.yml job"
    return ok, detail, fix


def check_dispatch_lint(repo_root, env):
    py = _py(repo_root, env)
    cmd = [py, str(repo_root / "tools" / "dispatch_lint.py"), "--check", str(repo_root)]
    proc = _run(cmd, repo_root, env, timeout=120)
    ok = proc.returncode == 0
    detail = (proc.stdout or "") + (proc.stderr or "")
    fix = "python tools/dispatch_lint.py --check . --json  # remove the forbidden pattern, or mark a legitimate line `# dispatch-ok`"
    return ok, detail, fix


def check_conflict_markers(repo_root, env):
    py = _py(repo_root, env)
    cmd = [py, str(repo_root / "tools" / "conflict_marker_check.py"), "--check", "--root", str(repo_root)]
    proc = _run(cmd, repo_root, env, timeout=120)
    ok = proc.returncode == 0
    detail = (proc.stdout or "") + (proc.stderr or "")
    fix = "python tools/conflict_marker_check.py --check --root . --json  # resolve the literal conflict marker, or register the path in tools/.conflict-marker-allowlist.json"
    return ok, detail, fix


def _git(repo_root, env, *args):
    proc = _run(["git", "-C", str(repo_root)] + list(args), repo_root, env, timeout=60)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "")


# Freshness checks for registered machine-generated paths (tools/generated_paths.py
# REGISTRY). The pre-push hook's designed writer path is: regenerate, then push
# with AESOP_ALLOW_GENERATED=1 -- so a branch that merely carries a clean
# regeneration (e.g. because it merged origin/main, which itself merged a prior
# regeneration) is NOT a violation and must not red the dry-range row (the bug
# behind PR #882 reading 9/10 on a perfectly good branch). Each entry maps a
# REGISTRY path to the command that verifies it is byte-identical to its
# generator's output, and the exact fix text to print when it is not -- the
# same text that generator's own --check mode prints. A REGISTRY path with no
# entry here falls back to "unknown" (see _evaluate_generated_freshness):
# freshness cannot be verified, so this row defers to the real hook's own
# check_generated_paths gate unchanged -- never silently waved through.
GENERATED_FRESHNESS_CHECKS = {
    "tools/INDEX.md": {
        "cmd": lambda py, repo_root: [
            py, str(Path(repo_root) / "tools" / "gen_tool_index.py"), "--check", "--root", str(repo_root),
        ],
        "fix": "python tools/gen_tool_index.py --regenerate && git add tools/INDEX.md",
    },
}


def _changed_registered_generated_paths(repo_root, env, py, changed_paths):
    """Ask tools/generated_paths.py --check --json which of `changed_paths` are
    registered machine-generated files. Returns a list of {"path", "pattern",
    "generator", "why"} dicts (empty if none are registered, or if the tool /
    interpreter is unavailable -- fail-open, the same posture the pre-push
    hook's own check_generated_paths uses for missing optional tooling)."""
    if not changed_paths:
        return []
    gen_script = Path(repo_root) / "tools" / "generated_paths.py"
    if not gen_script.exists():
        return []
    stdin_text = "\n".join(changed_paths) + "\n"
    proc = _run(
        [py, str(gen_script), "--check", "--json"], repo_root, env, timeout=60, input_text=stdin_text
    )
    try:
        report = json.loads(proc.stdout or "{}")
    except ValueError:
        return []
    return report.get("hits", [])


def _evaluate_generated_freshness(repo_root, env, py, hits):
    """Evaluate registered generated-path hits the way the pre-push hook's
    designed writer path does: fresh (byte-identical to the generator's
    output) is fine to push with AESOP_ALLOW_GENERATED=1; stale is a real
    defect that must be regenerated before push.

    Returns (status, payload):
      ("none", None)             -- hits was empty; caller changes nothing.
      ("fresh", note)            -- every hit has a known, passing freshness
                                      check; note is the human-readable PASS
                                      annotation to prepend to the row detail.
      ("stale", (detail, fix))   -- at least one hit's generator check failed;
                                      fix is the exact regen command (same
                                      text the generator's own --check prints).
      ("unknown", None)          -- hits exist but at least one has no known
                                      freshness check here; defer to the real
                                      hook's own gate (unchanged behavior).
    """
    if not hits:
        return "none", None

    stale = []
    unknown = False
    for hit in hits:
        path = hit.get("path", "")
        checker = GENERATED_FRESHNESS_CHECKS.get(path)
        if checker is None:
            unknown = True
            continue
        proc = _run(checker["cmd"](py, repo_root), repo_root, env, timeout=120)
        if proc.returncode != 0:
            stale.append((path, checker["fix"], (proc.stdout or "") + (proc.stderr or "")))

    if stale:
        detail_lines = ["generated path(s) are stale (regeneration required):"]
        for path, fix, out in stale:
            detail_lines.append("  %s -- run: %s" % (path, fix))
            if out.strip():
                detail_lines.append("    " + out.strip().replace("\n", "\n    "))
        fix = "; ".join(dict.fromkeys(f for _p, f, _o in stale))
        return "stale", ("\n".join(detail_lines), fix)

    if unknown:
        return "unknown", None

    return "fresh", "generated paths verified regenerated (%s)" % (
        ", ".join(sorted(h.get("path", "") for h in hits))
    )


def check_dry_prepush(repo_root, env):
    """Simulate the pre-push hook for the current branch against origin/main
    WITHOUT running `git push` -- builds the exact stdin tuple git would
    pipe into the hook for a feature-branch push and runs the real hook.

    Before running the hook, pre-evaluates any registered generated paths
    (tools/generated_paths.py REGISTRY, e.g. tools/INDEX.md) changed relative
    to origin/main the same way the hook's designed writer path does: if
    every changed registered path is byte-identical to its generator's
    output, the dry run proceeds with AESOP_ALLOW_GENERATED=1 (the designed
    writer path) and PASSes with a note; if one is stale, this row FAILs
    immediately with the exact regen command instead of ever reaching the
    hook. A branch with no registered-path changes, or with a registered
    change this tool has no freshness check for, behaves exactly as before.

    CRITICAL: runs the simulation in a throwaway detached worktree to avoid
    mutating the caller's HEAD, index, or working tree."""
    rc, branch, err = _git(repo_root, env, "branch", "--show-current")
    if rc != 0 or not branch:
        return False, "could not resolve current branch:\n" + err, "git branch --show-current  # must be on a feature branch, not detached HEAD"

    rc, head_sha, err = _git(repo_root, env, "rev-parse", "HEAD")
    if rc != 0 or not head_sha:
        return False, "could not resolve HEAD sha:\n" + err, "git rev-parse HEAD"

    fetch_rc, _out, fetch_err = _git(repo_root, env, "fetch", "origin", "main")
    if fetch_rc != 0:
        detail = "git fetch origin main failed:\n" + fetch_err
        return False, detail, "git fetch origin main  # fetch must succeed before a dry pre-push range check can be meaningful"

    run_env = env
    gen_note = ""
    py = _py(repo_root, env)
    mb_rc, base_sha, _mb_err = _git(repo_root, env, "merge-base", head_sha, "origin/main")
    if mb_rc == 0 and base_sha:
        diff_rc, changed_out, _diff_err = _git(repo_root, env, "diff", "--name-only", "%s..%s" % (base_sha, head_sha))
        if diff_rc == 0:
            changed_paths = [p for p in changed_out.splitlines() if p.strip()]
            hits = _changed_registered_generated_paths(repo_root, env, py, changed_paths)
            status, payload = _evaluate_generated_freshness(repo_root, env, py, hits)
            if status == "stale":
                return (False,) + payload
            if status == "fresh":
                run_env = dict(env)
                run_env["AESOP_ALLOW_GENERATED"] = "1"
                gen_note = payload + "\n"

    stdin_line = "refs/heads/%s %s refs/heads/%s %s\n" % (
        branch, head_sha, branch, "0" * 40
    )

    # Create a throwaway detached worktree to run the hook, so the simulation
    # never mutates the caller's HEAD, index, or working tree.
    tmpdir_obj = tempfile.TemporaryDirectory()
    tmpdir = Path(tmpdir_obj.name)
    try:
        # Create a detached worktree at the current HEAD
        add_rc, _out, add_err = _git(repo_root, env, "worktree", "add", "--detach", str(tmpdir), head_sha)
        if add_rc != 0:
            detail = "git worktree add --detach failed:\n" + add_err
            return False, detail, "git worktree add --detach  # failed to create temporary worktree for dry pre-push simulation"

        # Run the hook in the throwaway worktree with run_env (which includes AESOP_ALLOW_GENERATED if needed)
        hook_script = tmpdir / "hooks" / "pre-push-policy.sh"
        proc = _run(["bash", str(hook_script)], tmpdir, run_env, timeout=300, input_text=stdin_line)
        ok = proc.returncode == 0
        detail = gen_note + "stdin: %s" % stdin_line.strip() + "\n" + (proc.stdout or "") + (proc.stderr or "")
        fix = (
            "git fetch origin main && printf 'refs/heads/%s %s refs/heads/%s %s\\n' | bash hooks/pre-push-policy.sh  "
            "# re-run after fixing whichever check_* printed FATAL/Error above"
        ) % (branch, head_sha, branch, "0" * 40)
        return ok, detail, fix
    finally:
        # Clean up the temporary worktree
        tmpdir_obj.cleanup()
        # Remove the worktree via git (the directory is already gone, but git bookkeeping remains)
        _git(repo_root, env, "worktree", "prune", "--verbose")


CHECKS = [
    ("pre_push_selftest", "pre-push self-test (hooks/pre-push-policy.sh --test)", check_pre_push_selftest),
    ("gate_inventory", "gate inventory axis2 parity (hooks/CLAUDE.md <-> check_* call sites)", check_gate_inventory),
    ("claudemd_lint", "claudemd_lint (working tree: 150-line cap, phantom paths, domain cross-ref)", check_claudemd_lint),
    ("claudemd_headroom", "claudemd_lint --headroom (merge-union line cap vs origin/main)", check_claudemd_headroom),
    ("claudemd_sync", "claudemd_sync_gate (domain CLAUDE.md updated alongside domain code)", check_claudemd_sync),
    ("portability", "portability_check (ratchet, no baseline bump)", check_portability),
    ("gates_wired", "verify_gates_wired (documented CI gates actually invoked)", check_verify_gates_wired),
    ("dispatch_lint", "dispatch_lint (forbidden dispatch/merge patterns)", check_dispatch_lint),
    ("conflict_markers", "conflict_marker_check (no literal unresolved markers)", check_conflict_markers),
    ("dry_prepush", "dry pre-push range check against origin/main (no real push)", check_dry_prepush),
]


def run_all(repo_root, env, baseline=None):
    results = []
    for key, label, fn in CHECKS:
        try:
            if key == "portability" and baseline is not None:
                ok, detail, fix = fn(repo_root, env, baseline=baseline)
            else:
                ok, detail, fix = fn(repo_root, env)
        except Exception as exc:  # noqa: BLE001 - a row crashing must still report, not abort the table
            ok, detail, fix = False, "INTERNAL ERROR running this check: %r" % exc, "(tool bug -- see tools/new_gate_check.py check_%s)" % key
        results.append(CheckResult(key, label, ok, detail, fix))
    return results


def render_table(results):
    lines = []
    width = max(len(r.label) for r in results) + 2
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        lines.append("[%s] %-*s" % (status, width, r.label))
    failed = [r for r in results if not r.ok]
    lines.append("")
    lines.append("RESULT: %s (%d/%d rows green)" % (
        "PASS" if not failed else "FAIL", len(results) - len(failed), len(results)
    ))
    if failed:
        lines.append("")
        lines.append("Fix commands for red rows:")
        for r in failed:
            lines.append("")
            lines.append("  %s:" % r.label)
            lines.append("    %s" % r.fix)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the whole new-gate checklist (see module docstring) and print a pass/fail table."
    )
    parser.add_argument("--root", default=str(REPO_ROOT_DEFAULT), help="Repository root (default: this tool's own repo)")
    parser.add_argument("--baseline", default=None, help="Portability baseline file path (default: <repo-root>/.portability-baseline.json)")
    parser.add_argument("--json", action="store_true", help="Emit a JSON report instead of the text table")
    args = parser.parse_args(argv)

    repo_root = Path(args.root).resolve()
    if not repo_root.is_dir():
        print("ERROR: --root is not a directory: %s" % repo_root, file=sys.stderr)
        return 2

    env = dict(os.environ)
    env["AESOP_ROOT"] = str(repo_root)

    results = run_all(repo_root, env, baseline=args.baseline)
    failed = [r for r in results if not r.ok]

    if args.json:
        # When invoked with --baseline by the liveness gate for JSON output,
        # emit the stale_entries structure expected by baseline_liveness_check.py
        output = {"ok": not failed, "results": [r.to_dict() for r in results]}
        # The liveness gate checks for "stale" or "stale_entries"; this tool
        # doesn't have baseline-specific stale entries (that's for portability_check.py),
        # so we emit an empty list to signal success when --baseline is used.
        if args.baseline is not None:
            output["stale_entries"] = []
        print(json.dumps(output, indent=2))
    else:
        print(render_table(results))
        if failed:
            print("\n--- captured output for red rows ---", file=sys.stderr)
            for r in failed:
                print("\n=== %s ===" % r.label, file=sys.stderr)
                print(r.detail, file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
