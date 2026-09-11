#!/usr/bin/env python3
"""CLAUDE.md semantic drift detector.
INDEX: Semantic drift detector: CLAUDE.md claims vs disk reality (missing refs, unmapped dirs, dead map entries, absent CLI flags); exit 1 on drift; --json

Detects semantic drift between documentation claims and disk reality:
1. Files/commands referenced in domain CLAUDE.md that don't exist
2. Domain dirs on disk missing from root map
3. Root-map entries whose dirs are gone
4. Documented CLI flags absent from --help

Deterministic checks only (no LLM). Per-domain report.
Exit: 0=clean, 1=drift found. Supports --json.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional


def extract_domain_map(root_claude_content: str) -> Dict[str, Optional[str]]:
    """Extract domain entries from root CLAUDE.md.

    Returns dict of domain_name -> description (or None).
    Only entries ending with '/' are recognized as domains.
    """
    domains = {}
    # Match lines like: - **domain/** — description
    pattern = r"-\s+\*\*([a-zA-Z0-9_\-]+)/\*\*\s+—\s+([^\n]+)"
    for match in re.finditer(pattern, root_claude_content):
        domain_name = match.group(1)
        description = match.group(2).strip()
        domains[domain_name] = description
    return domains


def extract_cli_specs(tool_name: str, tool_line: str) -> Set[str]:
    """Extract CLI flag specifications from a tool index line.

    Parses lines like:
        `tool.py` — description; CLI: `--flag1 --flag2 <arg>` | `--flag3`

    Returns set of flag names (e.g., {"--flag1", "--flag2", "--flag3"}).
    """
    flags = set()

    # Look for CLI: ... section (may span to end of line or be cut by another section)
    cli_match = re.search(r"CLI:\s*(.+?)(?:$)", tool_line)
    if not cli_match:
        return flags

    cli_text = cli_match.group(1)

    # Extract all --flag patterns (including those with arguments like <arg>)
    # Matches: --flag-name, --flag, --flag <arg>, --flag <path>, etc.
    # Handle pipes and backticks as delimiters
    flag_pattern = r"--[a-zA-Z0-9\-]+"
    for match in re.finditer(flag_pattern, cli_text):
        flag = match.group(0)
        flags.add(flag)

    return flags


def check_domain_dirs_exist(
    domains: Dict[str, Optional[str]], repo_root: Path
) -> List[Dict[str, str]]:
    """Check that all mapped domains exist as directories.

    Returns findings for domains that don't exist.

    Skips special runtime-only domains that are intentionally missing.
    """
    findings = []

    # Domains that are documented but intentionally runtime-only (git-ignored)
    runtime_only_domains = {"state"}

    for domain_name in domains:
        # Skip runtime-only domains
        if domain_name in runtime_only_domains:
            continue

        domain_path = repo_root / domain_name
        if not domain_path.is_dir():
            findings.append({
                "type": "missing-domain-dir",
                "domain": domain_name,
                "message": f"Domain '{domain_name}/' in root map but not on disk",
            })
    return findings


def check_root_map_complete(
    mapped_domains: Dict[str, Optional[str]], repo_root: Path
) -> List[Dict[str, str]]:
    """Check for domains on disk that are missing from root map.

    Ignores hidden dirs, special dirs (assets, state, .git, etc.).
    """
    findings = []

    # Dirs to ignore (not domains)
    ignore_dirs = {
        "assets", "state", ".git", "node_modules", ".github",
        ".pytest_cache", "__pycache__", ".venv", "venv",
        "dist", "build", ".vscode", ".idea", "conductor3"
    }

    for item in repo_root.iterdir():
        # Skip non-dirs
        if not item.is_dir():
            continue

        # Skip ignored dirs
        if item.name in ignore_dirs or item.name.startswith("."):
            continue

        # Check if this dir is in the mapped domains
        if item.name not in mapped_domains:
            findings.append({
                "type": "unmapped-domain-dir",
                "domain": item.name,
                "message": f"Domain directory '{item.name}/' on disk but not in root map",
            })

    return findings


def check_file_references(
    domain_name: str, domain_dir: Path, repo_root: Path
) -> List[Dict[str, str]]:
    """Check that files referenced in a domain CLAUDE.md exist.

    Ignores runtime artifacts (state/*, BRIEF.md, etc.).
    """
    findings = []

    # Runtime artifacts allowlist (matches claudemd_lint.py)
    runtime_patterns = [
        r"^\.\.?/state/",
        r"heartbeat",
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
    ]

    def is_runtime_artifact(ref: str) -> bool:
        for pattern in runtime_patterns:
            if re.search(pattern, ref, re.IGNORECASE):
                return True
        return False

    # Extract file references from CLAUDE.md
    claudemd_path = domain_dir / "CLAUDE.md"
    if not claudemd_path.exists():
        # No CLAUDE.md in this domain, nothing to check
        return findings

    try:
        content = claudemd_path.read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        # Can't read the file
        return findings

    # Extract path references (same regex as claudemd_lint.py)
    pattern = r"(?:[`'\"])?([a-zA-Z0-9_.][a-zA-Z0-9_./\-]*\.(?:md|py|sh|mjs))(?:[`'\"])?"
    for match in re.finditer(pattern, content):
        ref = match.group(1)

        # Filter false positives (same filters as claudemd_lint.py)
        if len(ref) <= 2 or ref.startswith("*"):
            continue
        if ref.startswith("/") or "~/" in ref:
            continue
        if ref.startswith(".") and "/" in ref and not ref.startswith("./"):
            continue
        if "/path/to/" in ref or ref.startswith("path/to/"):
            continue
        if re.match(r"^[A-Z_]+/", ref):
            continue
        if ref.count(".") > 2 or "/" not in ref:
            continue

        # Skip compound references like "CLAUDE.md/STATE.md" (documentation patterns, not file refs)
        # These are patterns listing multiple files separated by "/" (e.g., "FILE1.md/FILE2.md")
        parts = ref.split("/")
        if len(parts) > 1 and all(p.endswith((".md", ".py", ".sh", ".mjs")) for p in parts):
            # This looks like a documentation pattern listing multiple files, not a path
            continue

        # Skip runtime artifacts
        if is_runtime_artifact(ref):
            continue

        # Try to resolve the reference
        target = domain_dir / ref
        if not target.exists():
            target = repo_root / ref
            if not target.exists():
                findings.append({
                    "type": "phantom-file-reference",
                    "domain": domain_name,
                    "message": f"{domain_name}/CLAUDE.md references non-existent '{ref}'",
                })

    return findings


def check_cli_flags(cli_specs: Dict[str, Set[str]], repo_root: Path) -> List[Dict[str, str]]:
    """Check that documented CLI flags exist in tool --help.

    Calls each tool with --help and verifies documented flags appear.
    """
    findings = []

    for tool_name, flags in cli_specs.items():
        if not flags:
            continue

        # Find the tool
        tool_path = repo_root / "tools" / tool_name
        if not tool_path.exists():
            # Tool doesn't exist, skip CLI check (file check will catch it)
            continue

        # Try to run tool --help
        try:
            result = subprocess.run(
                [str(tool_path), "--help"],
                capture_output=True,
                text=True,
                encoding='utf-8', errors='replace',
                timeout=5,
            )
            help_text = result.stdout + result.stderr
        except (subprocess.TimeoutExpired, OSError):
            # Can't run the tool, skip
            continue

        # Check if each documented flag appears in help
        for flag in flags:
            if flag not in help_text:
                findings.append({
                    "type": "missing-cli-flag",
                    "domain": "tools",
                    "message": f"{tool_name}: documented flag '{flag}' not in --help output",
                })

    return findings


def extract_tool_index(tools_claudemd: str) -> Dict[str, Set[str]]:
    """Extract tool names and their CLI specs from tools/CLAUDE.md.

    Returns dict mapping tool_name -> set of flags.
    """
    cli_specs = {}

    # Find the "Tool index" section
    index_match = re.search(r"## Tool index.*?\n(.*?)(?=\n## |\Z)", tools_claudemd, re.DOTALL)
    if not index_match:
        return cli_specs

    index_text = index_match.group(1)

    # Match tool lines: `tool.py` — description with optional CLI:
    # We're looking for lines like: - `name.py` — description; CLI: `...`
    tool_pattern = r"-\s+`([a-zA-Z0-9_\-]+\.(py|sh|mjs))`\s+—\s+([^\n]+)"

    for match in re.finditer(tool_pattern, index_text):
        tool_name = match.group(1)
        description = match.group(3)

        flags = extract_cli_specs(tool_name, description)
        if flags:
            cli_specs[tool_name] = flags

    return cli_specs


def run_drift_check(repo_root: Path) -> List[Dict[str, str]]:
    """Run full drift check.

    Returns list of finding dicts with 'type', 'domain', 'message'.
    Exit: 0=clean, 1=drift found.
    """
    findings = []

    # Read root CLAUDE.md
    root_claude_path = repo_root / "CLAUDE.md"
    if not root_claude_path.exists():
        findings.append({
            "type": "missing-root-claudemd",
            "domain": "root",
            "message": "Root CLAUDE.md not found",
        })
        return findings

    try:
        root_claude_content = root_claude_path.read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError) as e:
        findings.append({
            "type": "root-claudemd-read-error",
            "domain": "root",
            "message": f"Failed to read root CLAUDE.md: {e}",
        })
        return findings

    # Extract domain map
    domains = extract_domain_map(root_claude_content)

    # Check 1: domain dirs exist
    findings.extend(check_domain_dirs_exist(domains, repo_root))

    # Check 2: root map is complete
    findings.extend(check_root_map_complete(domains, repo_root))

    # Check 3: file references in each domain CLAUDE.md
    for domain_name in domains:
        domain_dir = repo_root / domain_name
        if domain_dir.is_dir():
            findings.extend(check_file_references(domain_name, domain_dir, repo_root))

    # Check 4: CLI flags (tools only, for now)
    tools_claudemd_path = repo_root / "tools" / "CLAUDE.md"
    if tools_claudemd_path.exists():
        try:
            tools_claudemd_content = tools_claudemd_path.read_text(encoding="utf-8")
            cli_specs = extract_tool_index(tools_claudemd_content)
            findings.extend(check_cli_flags(cli_specs, repo_root))
        except (IOError, UnicodeDecodeError):
            pass

    return findings


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="CLAUDE.md semantic drift detector — detect drift between docs and disk"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: cwd)",
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
        sys.exit(2)

    findings = run_drift_check(repo_root)

    if args.json:
        output = {
            "findings": findings,
            "count": len(findings),
            "repo_root": str(repo_root),
        }
        print(json.dumps(output, indent=2))
    else:
        if findings:
            for i, finding in enumerate(findings, 1):
                print(
                    f"{i}. [{finding['type']}] {finding['domain']}: {finding['message']}"
                )
        else:
            print("[OK] No semantic drift detected")

    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
