#!/usr/bin/env python3
"""CLAUDE.md linter — dogfoods the scope-min invariant.

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

Exit: 0=clean, 1=findings. Supports --json flag.
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

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
    for claudemd in repo_root.rglob("CLAUDE.md"):
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


def lint_claudemd(
    claudemd_path: Path,
    repo_root: Path,
    max_lines: int = 150,
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

    # Per-file oversize allowance: ui/CLAUDE.md is the documented dense-domain
    # exception (lossless-verified, probe-passed at ~197 lines). Mirrors the same
    # allowance in ~/scripts/compliance_check.py so the two gates agree.
    ALLOWED_OVERSIZE = {"ui/CLAUDE.md": 215}  # grew with bench_panel + BenchmarkPanel additions
    rel = str(claudemd_path.relative_to(repo_root)).replace("\\", "/")
    effective_max = ALLOWED_OVERSIZE.get(rel, max_lines)

    # Check line count
    if len(lines) > effective_max:
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
        default=150,
        help="Maximum lines per CLAUDE.md (default: 150)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()
    repo_root = args.root.resolve()

    if not repo_root.exists():
        print(f"Error: repo root {repo_root} does not exist", file=sys.stderr)
        sys.exit(1)

    # Find all CLAUDE.md files (recursive, with exclusions for common junk dirs)
    # Exclude: node_modules, .git, dist, worktrees (sibling dirs), .pytest_cache, __pycache__
    claudemd_files = []

    # Use rglob to find all CLAUDE.md files at any depth
    for claudemd_path in repo_root.rglob("CLAUDE.md"):
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
