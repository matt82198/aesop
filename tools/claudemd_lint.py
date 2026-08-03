#!/usr/bin/env python3
"""CLAUDE.md linter — dogfoods the scope-min invariant.
INDEX: Lint the domain CLAUDE.md layer: doc-pointers resolve, cited npm scripts exist, runtime/state artifacts not flagged, domain cross-refs prohibited; 4 checks: DOC-POINTER, TEST-CMD, DOMAIN-CROSS-REF (domain CLAUDE.md must not reference other domain CLAUDE.md with directives; parent-child refs allowed), line-count; --json; root CLAUDE.md exempt from cross-ref check. `--headroom [--base-ref origin/main] [--head-ref HEAD]` is a separate mode linting the MERGE UNION instead of the working tree: it previews the merge (`git merge-tree --write-tree`, falling back to a per-file three-way `git merge-file` on pre-2.38 git) and applies the same cap + per-file oversize allowance to the union's line count, so a branch sitting just under the cap while the base independently grew fails BEFORE the merge busts the cap on main; exit 0=clean / 1=a union busts its cap / 2=union unreadable (not a git repo, unresolvable ref, undecodable blob); wired into hooks/pre-push-policy.sh as `check_claudemd_headroom` (exit 2 fails open, exit 1 blocks the push). Also enforces the GENERATED-FILE contract landed with the tools/INDEX.md extraction: an authored CLAUDE.md carrying a `<!-- GENERATED-BY: <generator> -->` sentinel is a `sentinel-on-authored` finding, a sentinel-bearing file is exempt from the line-count cap, and every generated file must stay byte-identical to its generator's output (`generated-drift`, so a hand-edited tools/INDEX.md is rejected).

For each */CLAUDE.md in a repo:
1. DOC-POINTER check — every referenced path ending .md/.py/.sh/.mjs that looks like a
   REPO file (relative, not a runtime artifact) must exist. Distinguishes real repo-doc
   pointers from legitimate references to runtime artifacts (state/**, *heartbeat*,
   BRIEF.md, PROPOSALS.md, BUILDLOG.md, MEMORY.md, STATE.md, OUTCOMES-LEDGER.md, tracker.json).
2. TEST-CMD check — any `npm run <script>` cited must exist in package.json scripts.
   Flags `pytest` if the repo uses unittest (grep package.json test:py).
3. DOMAIN-CROSS-REF check — domain CLAUDE.md files must not reference other domain
   CLAUDE.md files (violates one-file-per-domain-dispatch rule). Root CLAUDE.md is exempt
   (it's the navigation map).
4. Optional — flags files over --max-lines (default 150).

`--headroom` is a separate mode: instead of linting the working tree, it lints the
MERGE UNION of each tracked CLAUDE.md against a base ref (default origin/main). A
branch can sit at 149/150 on its own and still bust the cap once merged, because the
base moved too; three such cascades landed in one day. The union preview is the only
thing that predicts the post-merge file, so the cap is checked there.

Exit: 0=clean, 1=findings, 2=unreadable (headroom mode only: no git, missing base ref,
un-previewable merge, undecodable blob). Supports --json flag.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_MAX_LINES = 150

# Per-file oversize allowance: ui/CLAUDE.md is the documented dense-domain
# exception (lossless-verified, probe-passed at ~197 lines). Mirrors the same
# allowance in ~/scripts/compliance_check.py so the two gates agree.
ALLOWED_OVERSIZE = {"ui/CLAUDE.md": 215}  # grew with bench_panel + BenchmarkPanel additions

# Generated-file sentinel: files carrying this marker are produced by a generator
# (captured in group 1) and must be byte-identical to `python <generator> --check`.
# Authored CLAUDE.md files must NOT carry it; generated files are cap-exempt.
GENERATED_SENTINEL_RE = re.compile(r"<!--\s*GENERATED-BY:\s*(\S+)\s*-->")

# Runtime artifact allowlist — these are correctly absent from the tree
RUNTIME_ARTIFACTS = {
    # State/control files
    "state",  # state/ directory
    "BRIEF.md",
    "PROPOSALS.md",
    "BUILDLOG.md",
    "MEMORY.md",
    "STATE.md",
    "OUTCOMES-LEDGER.md",
    "tracker.json",
    "ACTIONS.log",
    ".monitor-heartbeat",
    ".signal-state.json",
    ".HALT",
    ".git",
    "node_modules",
}

# Patterns that indicate a runtime artifact
RUNTIME_PATTERNS = [
    r"^\.\.?/state/",  # state/ directory
    r"heartbeat",  # *heartbeat*, .monitor-heartbeat, etc.
    r"^BRIEF\.md$",
    r"^PROPOSALS\.md$",
    r"^BUILDLOG\.md$",
    r"^MEMORY\.md$",
    r"^STATE\.md$",
    r"^CLAUDE\.md$",
    r"^SKILL\.md$",
    r"^OUTCOMES-LEDGER\.md$",
    r"^tracker\.json$",
    r"^ACTIONS\.log$",
    r"^\./state/",
    r"^state/",
    # Allow these control files in compound refs like "CLAUDE.md/STATE.md"
    r"CLAUDE\.md(?:/|$)",
    r"STATE\.md(?:/|$)",
    r"SKILL\.md(?:/|$)",
]



def _tracked_claudemds(repo_root):
    """Yield CLAUDE.md paths that are TRACKED IN GIT.

    rglob() walks the filesystem, so untracked scaffolder output (tests create
    ./aesop-fleet/ in the repo root) was being linted as if it were source -- the
    gate demanded a 150-line cap and cross-ref purity for a directory that is not
    part of the repo. A documentation gate must reason about tracked source only.

    Falls back to rglob if git is unavailable, so the tool still works outside a
    checkout; that is a degraded mode, not the normal path.
    """
    import subprocess
    # Only consult git when repo_root IS a real checkout. Tests build fixture trees in
    # temp dirs; invoking git there makes it walk up to an unrelated repo (or block),
    # which turned each fast unit test into a 30s timeout.
    if not (repo_root / ".git").exists():
        return list(repo_root.rglob("CLAUDE.md"))
    try:
        out = subprocess.run(
            ["git", "ls-files", "*CLAUDE.md"],
            cwd=str(repo_root), capture_output=True, text=True,
            encoding="utf-8", timeout=10,
        )
        if out.returncode == 0:
            names = [n.strip() for n in out.stdout.splitlines() if n.strip()]
            if names:
                return [repo_root / n for n in names]
    except (OSError, subprocess.SubprocessError):
        pass
    return list(_tracked_claudemds(repo_root))


def is_runtime_artifact(ref: str) -> bool:
    """Check if a reference is a legitimate runtime artifact."""
    for pattern in RUNTIME_PATTERNS:
        if re.search(pattern, ref, re.IGNORECASE):
            return True
    return False


def extract_path_references(text: str) -> List[str]:
    """Extract all references to paths ending in .md/.py/.sh/.mjs.

    Filters out:
    - Example paths starting with /path/to/
    - Environment variable references (VAR_NAME/...)
    - Absolute paths starting with /
    - Home directory references (~/.../...)
    - Non-relative paths
    - Glob patterns (*.something)
    - File type descriptions like ".py/.mjs"
    - Hidden directory references like .claude/ (not repo structure)
    """
    # Match paths: relative or starting with ./, alphanumeric, /, -, _
    # Also match inline code references like `path/file.md`
    # Note: intentionally NOT matching patterns like "*.test.mjs" (glob)
    # The leading `~/` must be part of the capture, otherwise a home-dir
    # reference like `~/scripts/foo.py` is captured as `scripts/foo.py` and the
    # home-directory filter below never fires — reporting a phantom repo path
    # for a file that correctly lives outside the repo.
    pattern = r"(?:[`'\"])?((?:~/)?[a-zA-Z0-9_.][a-zA-Z0-9_./\-]*\.(?:md|py|sh|mjs))(?:[`'\"])?"
    matches = re.finditer(pattern, text)
    refs = set()
    for match in matches:
        ref = match.group(1)

        # Filter out false positives
        if len(ref) <= 2:
            continue

        # Skip glob patterns (starting with *)
        if ref.startswith("*"):
            continue

        # Skip absolute paths
        if ref.startswith("/"):
            continue

        # Skip home directory references (~/...)
        if "~/" in ref:
            continue

        # Skip hidden directories that don't look like repo structure (./.something/...)
        # These are typically home dir refs like .claude/, .config/, etc.
        if ref.startswith(".") and "/" in ref:
            # Allow only ./ for current dir refs
            if not ref.startswith("./"):
                continue

        # Skip example paths
        if "/path/to/" in ref or ref.startswith("path/to/"):
            continue

        # Skip env var references (ALLCAPS_NAME/...)
        if re.match(r"^[A-Z_]+/", ref):
            continue

        # Skip file type descriptions like ".py/.mjs" (multiple dots in non-path context)
        if ref.count(".") > 2:
            continue

        # Skip references that don't have / (not a path)
        if "/" not in ref:
            continue

        refs.add(ref)

    return sorted(refs)


def extract_domain_claude_references(text: str) -> List[str]:
    r"""Extract DIRECTIVE references to domain CLAUDE.md files.

    Returns list of domain paths like ['tools', 'daemons', 'monitor'] for
    directive references like "read tools/CLAUDE.md" or "see daemons/CLAUDE.md".

    Matches references that appear in sentences containing directive keywords
    (read, see, refer to, check, review) to distinguish directives from
    incidental mentions like "used in tests/CLAUDE.md".

    Excludes:
    - Root CLAUDE.md (matches r'^/?CLAUDE\.md$')
    - Nested paths like "docs/" or "./" relative references
    """
    # Strategy: split text into sentences, then for each sentence containing
    # a directive keyword, find domain/CLAUDE.md references

    # Split into sentences by period/semicolon/newline, but not in the middle of filenames
    # Split on: . followed by space/newline, or ; or newline
    sentences = re.split(r'(?<=[.;])\s+|\n', text)

    refs = set()
    directive_keywords = r'(?:read|see|refer to|check|review)'

    for sentence in sentences:
        # Check if this sentence contains a directive keyword
        if re.search(directive_keywords, sentence, re.IGNORECASE):
            # Find all domain/CLAUDE.md in this sentence
            domain_pattern = r"([a-z_][a-z0-9_\-]*(?:/[a-z_][a-z0-9_\-]*)*)/CLAUDE\.md"
            for match in re.finditer(domain_pattern, sentence, re.IGNORECASE):
                refs.add(match.group(1))

    return sorted(refs)


def extract_npm_scripts(text: str) -> List[str]:
    """Extract all `npm run <script>` references."""
    pattern = r"npm\s+run\s+([a-zA-Z0-9:_\-]+)"
    matches = re.finditer(pattern, text)
    scripts = set()
    for match in matches:
        scripts.add(match.group(1))
    return sorted(scripts)


def get_package_scripts(repo_root: Path) -> Dict[str, str]:
    """Load scripts from every package.json in the repo (root + nested, e.g. ui/web).

    A multi-package repo (aesop has ui/web/package.json for the frontend) means a
    domain doc may legitimately cite a script that lives in a nested package — union
    them so the linter doesn't false-positive on ui/CLAUDE.md's `npm run build`/`dev`.
    """
    scripts: Dict[str, str] = {}
    for pkg_path in repo_root.rglob("package.json"):
        # skip dependencies' package.json
        if "node_modules" in pkg_path.parts:
            continue
        try:
            with open(pkg_path, encoding="utf-8") as f:
                pkg = json.load(f)
            scripts.update(pkg.get("scripts", {}))
        except (json.JSONDecodeError, IOError):
            continue
    return scripts


def check_test_cmd_match(repo_root: Path) -> Tuple[bool, str]:
    """Check if repo uses unittest (test:py in package.json uses 'unittest').

    Returns: (is_using_unittest, test_cmd_value)
    """
    scripts = get_package_scripts(repo_root)
    test_py = scripts.get("test:py", "")
    is_unittest = "unittest" in test_py
    return is_unittest, test_py


def get_sibling_domains(repo_root: Path, current_claudemd_path: Path) -> set:
    """Get all sibling domain CLAUDE.md paths (domains other than the current one).

    Returns a set of domain paths like {'tools', 'daemons', 'monitor'}.
    Excludes the root CLAUDE.md and the current file's domain.
    """
    domains = set()

    # Find all CLAUDE.md files in the repo
    for claudemd in _tracked_claudemds(repo_root):
        parts = claudemd.parts

        # Skip node_modules, .git, etc.
        if any(part in {"node_modules", ".git", "dist", ".pytest_cache", "__pycache__"} for part in parts):
            continue

        # If it's the root CLAUDE.md, skip it
        if claudemd.parent == repo_root:
            continue

        # Get the domain (directory relative to repo_root)
        try:
            rel_path = claudemd.relative_to(repo_root).parent
            domain = str(rel_path).replace("\\", "/")

            # Exclude the current domain
            current_domain = str(current_claudemd_path.relative_to(repo_root).parent).replace("\\", "/")
            if domain != current_domain:
                domains.add(domain)
        except ValueError:
            continue

    return domains


def effective_max_lines(rel_path: str, max_lines: int = DEFAULT_MAX_LINES) -> int:
    """Cap for one CLAUDE.md, honouring the documented per-file oversize allowance."""
    return ALLOWED_OVERSIZE.get(rel_path.replace("\\", "/"), max_lines)


class HeadroomError(Exception):
    """The merge union could not be read (exit 2, NOT a policy violation)."""


def _git(repo_root: Path, args: List[str], binary: bool = False):
    """Run a git plumbing command in repo_root. Returns CompletedProcess."""
    kwargs = {"cwd": str(repo_root), "capture_output": True}
    if not binary:
        kwargs["encoding"] = "utf-8"
        kwargs["errors"] = "replace"
    try:
        return subprocess.run(["git"] + args, **kwargs)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as exc:
        raise HeadroomError(f"git {' '.join(args)} failed to launch: {exc}") from exc


def _count_lines(blob: bytes, where: str) -> int:
    """Line count using the same convention as lint_claudemd (content.split('\\n'))."""
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HeadroomError(f"{where}: not valid UTF-8 ({exc})")
    return len(text.split("\n"))


def _tracked_claudemd_paths(repo_root: Path, ref: str) -> List[str]:
    """CLAUDE.md paths tracked at `ref` (repo-relative, forward slashes)."""
    # -z: NUL-delimited and NOT C-quoted, so a path with a space/non-ASCII byte
    # still round-trips instead of arriving as `"weird\303\251/CLAUDE.md"`.
    res = _git(repo_root, ["ls-tree", "-r", "-z", "--name-only", ref])
    if res.returncode != 0:
        raise HeadroomError(f"cannot list tree at {ref}: {res.stderr.strip()}")
    return [
        entry
        for entry in res.stdout.split("\0")
        if entry and entry.split("/")[-1] == "CLAUDE.md"
    ]


def _blob_at(repo_root: Path, ref: str, path: str) -> Optional[bytes]:
    """Raw blob bytes for `path` at `ref`, or None when the path is absent there."""
    res = _git(repo_root, ["cat-file", "blob", f"{ref}:{path}"], binary=True)
    if res.returncode != 0:
        return None
    return res.stdout


def _union_via_merge_tree(repo_root: Path, base_ref: str, head_ref: str) -> Optional[Dict[str, int]]:
    """Merge preview via `git merge-tree --write-tree` (git >= 2.38).

    Returns {path: union_line_count} or None when this git predates --write-tree
    (so the caller can fall back to a three-way read of the blobs).
    """
    res = _git(repo_root, ["merge-tree", "--write-tree", base_ref, head_ref])
    if res.returncode not in (0, 1):
        # rc >= 2 is a real error; an old git reports "unknown option" (rc 128/129)
        stderr = (res.stderr or "").lower()
        if "write-tree" in stderr and ("unknown" in stderr or "usage" in stderr):
            return None
        raise HeadroomError(
            f"merge preview {base_ref}..{head_ref} failed: {(res.stderr or res.stdout).strip()}"
        )
    tree = res.stdout.splitlines()[0].strip() if res.stdout.strip() else ""
    if not tree:
        raise HeadroomError(f"merge preview {base_ref}..{head_ref} produced no tree")

    counts: Dict[str, int] = {}
    for path in _tracked_claudemd_paths(repo_root, tree):
        blob = _blob_at(repo_root, tree, path)
        if blob is None:
            raise HeadroomError(f"cannot read merged blob for {path}")
        counts[path] = _count_lines(blob, f"{path} (merge union)")
    return counts


def _union_via_three_way(repo_root: Path, base_ref: str, head_ref: str) -> Dict[str, int]:
    """Fallback merge preview: per-file three-way `git merge-file` over the blobs."""
    mb = _git(repo_root, ["merge-base", base_ref, head_ref])
    if mb.returncode != 0 or not mb.stdout.strip():
        raise HeadroomError(f"no merge base between {base_ref} and {head_ref}")
    ancestor = mb.stdout.strip()

    paths = sorted(
        set(_tracked_claudemd_paths(repo_root, head_ref))
        | set(_tracked_claudemd_paths(repo_root, base_ref))
    )
    counts: Dict[str, int] = {}
    for path in paths:
        ours = _blob_at(repo_root, head_ref, path)
        theirs = _blob_at(repo_root, base_ref, path)
        if ours is None:
            counts[path] = _count_lines(theirs or b"", f"{path} (base only)")
            continue
        if theirs is None:
            counts[path] = _count_lines(ours, f"{path} (branch only)")
            continue
        common = _blob_at(repo_root, ancestor, path) or b""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            names = {"ours": ours, "base": common, "theirs": theirs}
            for name, data in names.items():
                (tmpdir / name).write_bytes(data)
            res = _git(
                repo_root,
                [
                    "merge-file", "-p",
                    str(tmpdir / "ours"), str(tmpdir / "base"), str(tmpdir / "theirs"),
                ],
                binary=True,
            )
            # git merge-file exits with the conflict count (capped at 127); an
            # actual error is signalled as -1, which surfaces as 255 unsigned.
            # Checking `< 0` alone would silently accept every failure.
            if res.returncode > 127:
                raise HeadroomError(f"three-way merge of {path} failed (rc={res.returncode})")
            counts[path] = _count_lines(res.stdout, f"{path} (merge union)")
    return counts


def compute_union_line_counts(
    repo_root: Path,
    base_ref: str = "origin/main",
    head_ref: str = "HEAD",
) -> Dict[str, int]:
    """Line count of every tracked CLAUDE.md as it will look AFTER merging into base_ref.

    Raises HeadroomError (caller maps to exit 2) when the preview is unreadable.
    """
    top = _git(repo_root, ["rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        raise HeadroomError(f"{repo_root} is not a git repository")
    for ref in (base_ref, head_ref):
        probe = _git(repo_root, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
        if probe.returncode != 0 or not probe.stdout.strip():
            raise HeadroomError(f"ref '{ref}' does not resolve to a commit")

    counts = _union_via_merge_tree(repo_root, base_ref, head_ref)
    if counts is None:
        counts = _union_via_three_way(repo_root, base_ref, head_ref)
    return counts


def check_headroom(
    repo_root: Path,
    base_ref: str = "origin/main",
    head_ref: str = "HEAD",
    max_lines: int = DEFAULT_MAX_LINES,
) -> List[Dict[str, str]]:
    """Lint the merge union's line count for every tracked CLAUDE.md.

    Fails a file whose UNION busts its cap even when the branch alone is under it --
    the cascade class this gate exists for. Raises HeadroomError on unreadable input.
    """
    union_counts = compute_union_line_counts(repo_root, base_ref, head_ref)
    branch_counts: Dict[str, int] = {}
    for path in _tracked_claudemd_paths(repo_root, head_ref):
        blob = _blob_at(repo_root, head_ref, path)
        if blob is not None:
            branch_counts[path] = _count_lines(blob, f"{path} ({head_ref})")

    findings: List[Dict[str, str]] = []
    for path in sorted(union_counts):
        union = union_counts[path]
        cap = effective_max_lines(path, max_lines)
        if union <= cap:
            continue
        branch = branch_counts.get(path)
        branch_note = (
            f"branch alone: {branch}" if branch is not None else "not tracked on branch"
        )
        findings.append({
            "type": "headroom-line-count",
            "line": str(union),
            "message": (
                f"{path}: merge union with {base_ref} is {union} lines, "
                f"exceeds max {cap} ({branch_note}) -- trim before merging"
            ),
        })
    return findings


def lint_claudemd(
    claudemd_path: Path,
    repo_root: Path,
    max_lines: int = DEFAULT_MAX_LINES,
) -> List[Dict[str, str]]:
    """Lint a single CLAUDE.md file.

    Returns list of findings, each a dict with 'type', 'line', 'message'.
    """
    findings = []

    try:
        content = claudemd_path.read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError) as e:
        return [{
            "type": "file-read-error",
            "line": "0",
            "message": f"Failed to read {claudemd_path.relative_to(repo_root)}: {e}",
        }]

    lines = content.split("\n")
    rel = str(claudemd_path.relative_to(repo_root)).replace("\\", "/")

    # Generated-file sentinel: a CLAUDE.md is an AUTHORED file, so it must NEVER
    # carry the generated-file sentinel (that marks a machine-generated file whose
    # bytes must equal its generator's output — an authored doc claiming to be
    # generated is a finding). Sentinel-bearing files are also exempt from the
    # line-count cap (they can be arbitrarily long, e.g. tools/INDEX.md).
    has_sentinel = bool(GENERATED_SENTINEL_RE.search(content))
    if has_sentinel:
        findings.append({
            "type": "sentinel-on-authored",
            "line": "?",
            "message": f"{rel}: authored CLAUDE.md carries a "
                       f"'<!-- GENERATED-BY: -->' sentinel; only generated files may.",
        })

    # The per-file oversize allowance (ALLOWED_OVERSIZE) lives at module scope so
    # that --headroom applies the SAME cap to the merge union as this working-tree
    # lint does; effective_max_lines() is the single reader of it.
    effective_max = effective_max_lines(rel, max_lines)

    # Check line count (sentinel-bearing generated files are exempt from the cap)
    if not has_sentinel and len(lines) > effective_max:
        findings.append({
            "type": "line-count",
            "line": str(len(lines)),
            "message": f"{claudemd_path.relative_to(repo_root)}: "
                       f"{len(lines)} lines exceeds max {effective_max}",
        })

    # Check if content endorses pytest but repo uses unittest
    # Exclude false positives where pytest is mentioned in passing or explicitly excluded
    is_unittest, _ = check_test_cmd_match(repo_root)
    if is_unittest:
        content_lower = content.lower()
        # Check for pytest endorsement (not just mention)
        pytest_mentioned = "pytest" in content_lower
        # Check for exclusion phrases that indicate pytest is NOT used
        pytest_excluded = any(phrase in content_lower for phrase in [
            "not pytest",
            "not use pytest",
            "don't use pytest",
            "do not use pytest",
            "uses unittest",
            "use unittest",
            "unittest, not pytest",
            "-m unittest",
        ])
        # Flag only if pytest is mentioned AND not explicitly excluded
        if pytest_mentioned and not pytest_excluded:
            findings.append({
                "type": "pytest-vs-unittest",
                "line": "?",
                "message": f"{claudemd_path.relative_to(repo_root)}: "
                           f"mentions 'pytest' but repo uses unittest (test:py)",
            })

    # DOC-POINTER check: find file references
    path_refs = extract_path_references(content)

    # Get the directory of the CLAUDE.md file for relative resolution
    claudemd_dir = claudemd_path.parent

    for ref in path_refs:
        # Skip runtime artifacts
        if is_runtime_artifact(ref):
            continue

        # Try to resolve relative to the CLAUDE.md file's directory first
        target = claudemd_dir / ref
        if not target.exists():
            # Fall back to repo root resolution
            target = repo_root / ref
            if not target.exists():
                findings.append({
                    "type": "phantom-path",
                    "line": "?",
                    "message": f"{claudemd_path.relative_to(repo_root)}: "
                               f"references non-existent '{ref}'",
                })

    # TEST-CMD check: npm run scripts
    npm_scripts = extract_npm_scripts(content)
    available_scripts = get_package_scripts(repo_root)

    for script in npm_scripts:
        if script not in available_scripts:
            findings.append({
                "type": "missing-npm-script",
                "line": "?",
                "message": f"{claudemd_path.relative_to(repo_root)}: "
                           f"npm run '{script}' not in package.json scripts",
            })

    # DOMAIN-CROSS-REF check: domain CLAUDE.md files must not reference other domains
    # Exception: root CLAUDE.md is exempt (it's the navigation map)
    is_root = claudemd_path.parent == repo_root
    if not is_root:
        domain_refs = extract_domain_claude_references(content)
        sibling_domains = get_sibling_domains(repo_root, claudemd_path)

        current_domain = str(claudemd_path.relative_to(repo_root).parent).replace("\\", "/")
        for domain_ref in domain_refs:
            # Allow parent→child references (e.g., driver → driver/orchestrator-swap)
            if domain_ref.startswith(current_domain + "/"):
                continue
            if domain_ref in sibling_domains:
                findings.append({
                    "type": "domain-cross-ref",
                    "line": "?",
                    "message": f"{claudemd_path.relative_to(repo_root)}: "
                               f"domain CLAUDE.md must not reference other domain CLAUDE.md files; "
                               f"found reference to '{domain_ref}/CLAUDE.md' "
                               f"(violation of one-file-per-domain-dispatch rule)",
                })

    return findings


def check_generated_files(repo_root: Path) -> List[Dict[str, str]]:
    """Verify every generated file is byte-identical to its generator's output.

    Discovers markdown files carrying the `<!-- GENERATED-BY: <gen> -->` sentinel
    and runs `python <gen> --check`; a nonzero-exit drift (return code 1) is a
    finding. Return code 2 (generator could not evaluate, e.g. not a git tree) is
    treated as skip so this never false-fails in a non-repo checkout. CLAUDE.md
    files are handled by the authored-sentinel check in lint_claudemd().
    """
    import subprocess

    findings: List[Dict[str, str]] = []
    skip_dirs = {"node_modules", ".git", "dist", ".pytest_cache", "__pycache__"}
    for md_path in repo_root.rglob("*.md"):
        if any(part in skip_dirs for part in md_path.parts):
            continue
        if md_path.name == "CLAUDE.md":
            continue
        try:
            content = md_path.read_text(encoding="utf-8")
        except (IOError, UnicodeDecodeError):
            continue
        m = GENERATED_SENTINEL_RE.search(content)
        if not m:
            continue
        rel = str(md_path.relative_to(repo_root)).replace("\\", "/")
        gen_rel = m.group(1)
        gen_path = repo_root / gen_rel
        if not gen_path.exists():
            findings.append({
                "type": "generated-missing-generator",
                "line": "?",
                "message": f"{rel}: sentinel names generator '{gen_rel}' which does not exist",
            })
            continue
        try:
            res = subprocess.run(
                [sys.executable, str(gen_path), "--check"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            findings.append({
                "type": "generated-check-error",
                "line": "?",
                "message": f"{rel}: could not run generator '{gen_rel}' --check: {e}",
            })
            continue
        if res.returncode == 1:
            findings.append({
                "type": "generated-drift",
                "line": "?",
                "message": f"{rel}: not byte-identical to '{gen_rel}' output "
                           f"(hand-edit?); run: python {gen_rel} --regenerate",
            })
        # return code 2 = generator could not evaluate -> skip (avoid false fail)
    return findings


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Lint CLAUDE.md files for integrity"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: cwd)",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=DEFAULT_MAX_LINES,
        help=f"Maximum lines per CLAUDE.md (default: {DEFAULT_MAX_LINES})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--headroom",
        action="store_true",
        help="Lint the MERGE UNION with --base-ref instead of the working tree "
             "(catches a branch that passes at 149/150 but merges to 151)",
    )
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="Base ref for --headroom merge preview (default: origin/main)",
    )
    parser.add_argument(
        "--head-ref",
        default="HEAD",
        help="Head ref for --headroom merge preview (default: HEAD)",
    )

    args = parser.parse_args()
    repo_root = args.root.resolve()

    if not repo_root.exists():
        print(f"Error: repo root {repo_root} does not exist", file=sys.stderr)
        sys.exit(1)

    if args.headroom:
        try:
            all_findings = check_headroom(
                repo_root, args.base_ref, args.head_ref, args.max_lines
            )
        except HeadroomError as exc:
            if args.json:
                print(json.dumps({"error": str(exc), "findings": [], "count": 0}, indent=2))
            else:
                print(f"Error: merge union unreadable: {exc}", file=sys.stderr)
            sys.exit(2)
        if args.json:
            print(json.dumps(
                {"findings": all_findings, "count": len(all_findings),
                 "repo_root": str(repo_root), "base_ref": args.base_ref},
                indent=2,
            ))
        elif all_findings:
            for i, finding in enumerate(all_findings, 1):
                print(f"{i}. [{finding['type']}] {finding['message']}")
        else:
            print(f"[OK] No CLAUDE.md busts its cap in the merge union with {args.base_ref}")
        sys.exit(1 if all_findings else 0)

    # Find all CLAUDE.md files (recursive, with exclusions for common junk dirs)
    # Exclude: node_modules, .git, dist, worktrees (sibling dirs), .pytest_cache, __pycache__
    claudemd_files = []

    # Use rglob to find all CLAUDE.md files at any depth
    for claudemd_path in _tracked_claudemds(repo_root):
        # Exclude paths in problematic directories
        parts = claudemd_path.parts
        if any(part in {"node_modules", ".git", "dist", ".pytest_cache", "__pycache__"} for part in parts):
            continue
        # Exclude worktree paths (parent directory sibling paths like ../aesop-wt-*)
        # This is already handled by only searching within repo_root
        claudemd_files.append(claudemd_path)

    claudemd_files = sorted(set(claudemd_files))

    all_findings = []
    for claudemd_path in claudemd_files:
        findings = lint_claudemd(claudemd_path, repo_root, args.max_lines)
        all_findings.extend(findings)

    # Verify generated files (e.g. tools/INDEX.md) are byte-identical to output.
    all_findings.extend(check_generated_files(repo_root))

    if args.json:
        output = {
            "findings": all_findings,
            "count": len(all_findings),
            "repo_root": str(repo_root),
        }
        print(json.dumps(output, indent=2))
    else:
        if all_findings:
            for i, finding in enumerate(all_findings, 1):
                print(
                    f"{i}. [{finding['type']}] {finding['message']} "
                    f"(line {finding['line']})"
                )
        else:
            print("[OK] No issues found")

    sys.exit(1 if all_findings else 0)


if __name__ == "__main__":
    main()
