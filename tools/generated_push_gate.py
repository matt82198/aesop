#!/usr/bin/env python3
"""Pre-push byte-identity gate for registered generated artifacts on COMMITTED content.
INDEX: Pre-push byte-identity gate for registered generated artifacts on COMMITTED content: for each `--range BASE..TIP` whose diff touches a `generated_paths.REGISTRY` path that has a regenerator (or any path named in the per-worktree `.needs-regen` stamp left by `generated_merge.py`), checks TIP out in a throwaway detached worktree, runs the registered generator there, and fails (exit 1) with exactly `run: python tools/gen_tool_index.py --regenerate && git add tools/INDEX.md` when the committed bytes differ -- the exact CI gate on the pushed commit, not the working tree (the gap that let #784/#856/#739 push union-stale indexes past `check_gen_tool_index()`); stamp consumed on pass; wired as `hooks/pre-push-policy.sh check_generated_regen()`; no escape hatch (a stale artifact is never a legitimate write); exit 0=verified or nothing to verify, 1=stale or generator failed, 2=usage/git error

The existing check_gen_tool_index() runs `gen_tool_index.py --check` against the
WORKING TREE. Three ways that is not the pushed content: the lane regenerated
but did not commit; the merge commit carries a `union`-stale index while the
tree was fixed afterwards; a server-side update-branch merge was fetched and
pushed on. CI runs the gate on the commit, so the hook must too. This tool
materialises the tip in a detached worktree (`git worktree add --detach`), runs
the registered generator from THAT tree (the tip's own copy of the generator,
exactly what CI runs), and compares bytes. Cleanup is unconditional.

Triggers: a registered regenerable path changed in the range, or named in the
driver's stamp. An empty range with no stamp is a no-op, so an ordinary push
pays nothing.

CLI:
    generated_push_gate.py --range BASE..TIP [--range ...] [--root DIR] [--json]
Exit codes: 0 = every triggered artifact matches its generator (or nothing to
verify); 1 = at least one is stale, or its generator failed (cannot verify =>
deny); 2 = usage or git error.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # sibling imports below resolve when loaded by file path

import generated_paths  # noqa: E402

USAGE = "usage: generated_push_gate.py --range BASE..TIP [--range ...] [--root DIR] [--json]\n"
STAMP_NAME = "aesop-needs-regen"
RANGE_RE = re.compile(r"^([^.\s]+)\.\.([^.\s]+)$")


def _git(root: Path, *args: str, timeout: int = 120) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git"] + list(args), cwd=str(root), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def resolve_root(explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        return Path(explicit).resolve()
    rc, out, _ = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    if rc != 0 or not out.strip():
        return None
    return Path(out.strip()).resolve()


def stamp_file(root: Path) -> Optional[Path]:
    rc, out, _ = _git(root, "rev-parse", "--git-path", STAMP_NAME)
    if rc != 0 or not out.strip():
        return None
    path = Path(out.strip())
    return path if path.is_absolute() else root / path


def read_stamp(root: Path) -> List[str]:
    stamp = stamp_file(root)
    if stamp is None or not stamp.exists():
        return []
    try:
        return [generated_paths.normalize(l) for l in
                stamp.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def consume_stamp(root: Path, verified: List[str]) -> None:
    stamp = stamp_file(root)
    if stamp is None or not stamp.exists():
        return
    remaining = [p for p in read_stamp(root) if p not in verified]
    try:
        if remaining:
            stamp.write_text("\n".join(remaining) + "\n", encoding="utf-8", newline="\n")
        else:
            stamp.unlink()
    except OSError:
        pass


def parse_range(text: str) -> Optional[Tuple[str, str]]:
    m = RANGE_RE.match(text.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def changed_paths(root: Path, base: str, tip: str) -> Optional[List[str]]:
    rc, out, _ = _git(root, "diff", "--name-only", base, tip)
    if rc != 0:
        return None
    return [generated_paths.normalize(l) for l in out.splitlines() if l.strip()]


def instruction_for(path: str) -> str:
    argv = generated_paths.regenerator_for(path) or []
    return "run: python %s && git add %s" % (" ".join(argv), path)


def _remove_worktree(root: Path, wt: Path, tmp: Path) -> None:
    _git(root, "worktree", "remove", "--force", str(wt))
    shutil.rmtree(tmp, ignore_errors=True)
    _git(root, "worktree", "prune")


def verify_tip(root: Path, tip: str, paths: List[str]) -> Tuple[List[Dict[str, str]], List[str]]:
    """Regenerate each path from TIP's own sources in a detached worktree.

    Returns (stale_records, verified_paths). A generator that fails to run is
    reported as stale: a gate that cannot verify must not report success.
    Comparison is done through git diff, which applies EOL normalization
    (core.autocrlf, .gitattributes) so the gate works correctly on all platforms.
    """
    stale: List[Dict[str, str]] = []
    verified: List[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="aesop-regen-"))
    wt = tmp / "wt"
    rc, _, err = _git(root, "worktree", "add", "--detach", "-q", str(wt), tip, timeout=300)
    if rc != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        stale.append({"path": ",".join(paths), "tip": tip,
                      "reason": "cannot materialise tip: %s" % err.strip()})
        return stale, verified
    try:
        for path in paths:
            argv = generated_paths.regenerator_for(path)
            if not argv:
                continue
            script = wt / argv[0]
            if not script.exists():
                sys.stdout.write("generated_push_gate: %s has no %s at %s; nothing to verify\n"
                                 % (path, argv[0], tip[:7]))
                continue
            # Capture the state of the file at tip (committed state).
            # We do NOT use raw bytes; instead we'll use git diff which applies
            # EOL normalization and .gitattributes rules.
            try:
                proc = subprocess.run(
                    [sys.executable, str(script)] + list(argv[1:]), cwd=str(wt),
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=300)
            except (OSError, subprocess.SubprocessError) as exc:
                stale.append({"path": path, "tip": tip, "reason": "generator failed: %s" % exc})
                continue
            if proc.returncode != 0:
                stale.append({"path": path, "tip": tip,
                              "reason": "generator exited %d: %s"
                              % (proc.returncode, (proc.stderr or proc.stdout).strip())})
                continue
            # Use git diff to compare the committed version (with EOL normalization applied)
            # against the regenerated working tree file. This is platform-agnostic and
            # handles core.autocrlf and .gitattributes line-ending rules.
            diff_rc, _, _ = _git(wt, "diff", "--quiet", "--exit-code", "--", path)
            if diff_rc != 0:
                # Diff exit code 0 = no changes, 1 = changes exist, 2+ = error.
                # Only 0 means the regenerated file matches the committed version.
                stale.append({"path": path, "tip": tip,
                              "reason": "committed bytes differ from the generator's output"})
            else:
                verified.append(path)
    finally:
        _remove_worktree(root, wt, tmp)
    return stale, verified


def main(argv: List[str]) -> int:
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(__doc__ + "\n" + USAGE)
        return 0
    as_json = "--json" in argv
    ranges: List[str] = []
    root_arg: Optional[str] = None
    it = iter(argv)
    for arg in it:
        if arg == "--json":
            continue
        if arg == "--range":
            value = next(it, None)
            if value is None:
                sys.stderr.write("generated_push_gate: --range needs BASE..TIP\n" + USAGE)
                return 2
            ranges.append(value)
            continue
        if arg == "--root":
            root_arg = next(it, None)
            if root_arg is None:
                sys.stderr.write("generated_push_gate: --root needs a value\n" + USAGE)
                return 2
            continue
        sys.stderr.write("generated_push_gate: unexpected argument %r\n%s" % (arg, USAGE))
        return 2
    if not ranges:
        sys.stderr.write("generated_push_gate: at least one --range is required\n" + USAGE)
        return 2

    root = resolve_root(root_arg)
    if root is None:
        sys.stderr.write("generated_push_gate: not inside a git repository\n")
        return 2

    stamped = [p for p in read_stamp(root) if generated_paths.regenerator_for(p)]
    per_tip: Dict[str, List[str]] = {}
    for text in ranges:
        parsed = parse_range(text)
        if parsed is None:
            sys.stderr.write("generated_push_gate: bad range %r (want BASE..TIP)\n" % text)
            return 2
        base, tip = parsed
        for rev in (base, tip):
            rc, _, _ = _git(root, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
            if rc != 0:
                sys.stderr.write("generated_push_gate: unknown revision %r in %r\n" % (rev, text))
                return 2
        changed = changed_paths(root, base, tip)
        if changed is None:
            sys.stderr.write("generated_push_gate: git diff failed for %r\n" % text)
            return 2
        # Collect all regenerable paths: those changed in the range, plus stamped, plus all
        # regenerable paths that exist at the tip (to catch generator output drift even when
        # the artifact itself wasn't touched in this push range, e.g., a tool docstring changed
        # but INDEX.md wasn't regenerated). Only check regenerable paths if there are commits
        # in the range (non-empty diff); an empty range with no stamp is a no-op.
        targets = [p for p in changed if generated_paths.regenerator_for(p)]
        for p in stamped:
            if p not in targets:
                targets.append(p)
        # Only add regenerable paths if there's a non-empty diff in this range
        if changed:
            for p in generated_paths.regenerable_paths():
                if p not in targets:
                    # Check if this path exists at the tip
                    rc, _, _ = _git(root, "rev-parse", "--verify", "--quiet", tip + ":" + p)
                    if rc == 0:
                        # Path exists at tip; add it to targets for verification
                        targets.append(p)
        if targets:
            bucket = per_tip.setdefault(tip, [])
            for p in targets:
                if p not in bucket:
                    bucket.append(p)

    stale_all: List[Dict[str, str]] = []
    verified_all: List[str] = []
    for tip, paths in per_tip.items():
        stale, verified = verify_tip(root, tip, paths)
        stale_all.extend(stale)
        verified_all.extend(verified)

    if as_json:
        sys.stdout.write(json.dumps({"stale": stale_all, "verified": verified_all,
                                     "checked_tips": sorted(per_tip)}, indent=2) + "\n")
    if stale_all:
        for rec in stale_all:
            sys.stderr.write("ERROR: %s committed at %s is stale (%s)\n"
                             % (rec["path"], rec["tip"][:7], rec["reason"]))
            sys.stderr.write(instruction_for(rec["path"]) + "\n")
        return 1
    if verified_all:
        consume_stamp(root, verified_all)
        if not as_json:
            sys.stdout.write("[OK] generated artifacts verified at the pushed tip: %s\n"
                             % ", ".join(sorted(set(verified_all))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
