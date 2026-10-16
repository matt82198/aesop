#!/usr/bin/env python3
"""Dispatch linter — enforces merge automation and security rules for agent prompts.
INDEX: Dispatch policy linter (merge automation + security + lane-contract rules); detects forbidden patterns (manual gh pr merge without --auto, bare --auto with no PR number, --admin/--no-verify/--force, git stash, credential hunting) and lane-side CI polling (`ci_merge_wait`, `gh run watch`, `merge_train.py`, sleep-wrapped `gh pr checks`) in dispatch prompts. **Lane terminal action it enforces**: a lane ends at push -> open PR -> `gh pr merge <n> --auto --squash` (arms GitHub's native auto-merge) -> exit, and NEVER waits on CI, because native auto-merge is the merge actor and does not depend on a live session (AesopMergeQueue is disabled; LANE-CONTRACT.md is the source of truth). `# dispatch-ok` suppression for genuine code sites; categorically exempts `tools/INDEX.md`, any `CLAUDE.md`, and `INDEX:` docstring summary lines as documentation (never relies on per-line markers for those, since a generated file's markers don't survive regeneration); CLI: `[--check] [--fix] [--json] [PATH]`; exit 0=clean/1=violations/2=error

Scans Python/JS/MD files for agent dispatch patterns and flags FORBIDDEN patterns:
  - `gh pr merge <n>` without `--auto` (manual/direct merge; lanes only ever ARM, never merge)
  - `gh pr merge --auto` with no explicit PR number/URL anywhere on the line (ambiguous target,
    matches the live no-orchestrator-merge-train PreToolUse hook's own denial semantics)
  - `--admin` flag (merge automation bypass; forbidden even alongside a legitimately-armed --auto)
  - `--no-verify` flag (pre-commit hook bypass)
  - `--force` in git context (dangerous history rewrite)
  - `git stash` (shared across worktrees, cross-contamination risk)
  - Credential/key hunting patterns (find.*key, grep.*token, env.*KEY)  # hygiene-ok
  - Lane-side CI polling (ci_merge_wait, `gh run watch`, merge_train.py,
    sleep-wrapped `gh pr checks`) -- a lane must never babysit CI

  `gh pr merge <n> --auto --squash` (PR number before OR after --auto) is the
  POLICY-SANCTIONED arming form per LANE-CONTRACT.md and is explicitly ALLOWED:
  this gate must never contradict the contract it exists to enforce. (Fixed
  2026-10-06, GAP: merge-actor session-independence -- this linter previously
  blocked the exact command LANE-CONTRACT.md requires every lane to run, a
  dead letter from the retired label+AesopMergeQueue regime.)

Lane terminal action (what the lane_ci_polling_* rules enforce):
  push -> open PR -> `gh pr merge <n> --auto --squash` -> exit.
  Everything after that is GitHub's own server-side native auto-merge, which
  completes the merge whether or not any session is live. A lane that polls CI
  burns an agent for the length of a CI run and re-couples merging to a live
  session -- the exact bottleneck arming at PR-open time removes.

Modes:
  dispatch_lint.py --check [PATH]          Exit 1 if violations found
  dispatch_lint.py --fix [PATH]            Show suggested fixes
  dispatch_lint.py --json [PATH]           Output violations as JSON
  dispatch_lint.py [PATH]                  Default: check mode on cwd

Suppression:
  Add '# dispatch-ok' (or '// dispatch-ok' / '<!-- dispatch-ok -->') on the
  line with a violation to suppress it in genuine code/prompt sites.
  Documentation is NEVER a dispatch template regardless of markers:
  tools/INDEX.md, any CLAUDE.md, and `INDEX:` docstring summary lines are
  categorically exempt (DOC_EXEMPT_FILENAMES / is_index_summary_line), since
  a one-line marker on a GENERATED file does not survive regeneration.

Exit: 0=clean, 1=violations found, 2=error
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


# Forbidden patterns and their suggested fixes
FORBIDDEN_PATTERNS = {
    "gh_pr_merge": {
        # Forbidden ONLY when the line has no --auto anywhere: a bare/manual
        # `gh pr merge <n>` merges directly, which lanes must never do. The
        # policy-sanctioned arming form `gh pr merge <n> --auto --squash`
        # (LANE-CONTRACT.md) is deliberately excluded via the negative lookahead.
        "pattern": r"\bgh\s+pr\s+merge\b(?!.*--auto\b)",
        "description": "Manual/direct 'gh pr merge' without --auto is forbidden; lanes arm native auto-merge, never merge directly",
        "fix": "Arm native auto-merge instead: gh pr merge <n> --auto --squash (board catch-up: python tools/auto_merge.py <n> [<n>...] or --all)",
    },
    "gh_pr_merge_auto_bare": {
        # `gh pr merge --auto` with NO digit anywhere on the line has no
        # explicit PR number/URL target -- ambiguous, forbidden. A PR number
        # on either side of --auto (before or after) satisfies this.
        "pattern": r"\bgh\s+pr\s+merge\s+--auto\b(?!.*\d)",
        "description": "'gh pr merge --auto' with no explicit PR number/URL is forbidden (ambiguous target)",
        "fix": "Specify the PR number: gh pr merge <n> --auto --squash",
    },
    "admin_flag": {
        "pattern": r"--admin\b",
        "description": "Merge automation bypass flag forbidden in dispatch prompts",
        "fix": "Remove --admin flag; use merge automation instead",
    },
    "no_verify_flag": {
        "pattern": r"--no-verify\b",
        "description": "Pre-commit hook bypass forbidden (security gate)",
        "fix": "Remove --no-verify flag; commit must pass security scanning",
    },
    "force_flag": {
        "pattern": r"\bgit\s+.*\s+--force\b|\bgit\s+.*\s+-f\b",
        "description": "Dangerous history rewrite flag forbidden",
        "fix": "Remove --force/-f flag; use safe git operations",
    },
    "git_stash": {
        "pattern": r"\bgit\s+stash\b",
        "description": "git stash is shared across worktrees; causes cross-contamination",
        "fix": "Use diff>patch + checkout + apply instead; see MEMORY.md",
    },
    "find_key_hunting": {
        "pattern": r"\bfind\s+.*\s+\(-\w*name\w*\s+.*['\"]?[^'\"]*(?:key|secret|token|password)[^'\"]*['\"]?",  # hygiene-ok
        "description": "Credential hunting pattern forbidden (missing key = SKIP, never search)",
        "fix": "Specify exact transport and allowed env vars instead; see no-credential-hunting memory",
    },
    "grep_token_hunting": {
        "pattern": r"\bgrep\s+.*(?:token|secret|password|api[_-]?key|auth)\b",  # hygiene-ok
        "description": "Credential hunting pattern forbidden",
        "fix": "Specify exact transport and allowed env vars instead",
    },
    "lane_ci_polling_merge_wait": {
        "pattern": r"\bci_merge_wait(?:\.py)?\b",
        "description": "Lane-side CI polling forbidden; native auto-merge owns the wait once armed",
        "fix": "End the lane at: gh pr merge <n> --auto --squash, then exit",
    },
    "lane_ci_polling_run_watch": {
        "pattern": r"\bgh\s+run\s+watch\b",
        "description": "Lane-side CI polling forbidden; native auto-merge owns the wait once armed",
        "fix": "End the lane at: gh pr merge <n> --auto --squash, then exit",
    },
    "lane_ci_polling_merge_train": {
        "pattern": r"\bmerge_train\.py\b",
        "description": "Lanes never run a merge train; arm native auto-merge and exit",
        "fix": "End the lane at: gh pr merge <n> --auto --squash, then exit",
    },
    "lane_ci_polling_sleep_checks": {
        "pattern": (r"\bsleep\b[^\n]{0,120}?\bgh\s+pr\s+checks\b"
                    r"|\bgh\s+pr\s+checks\b[^\n]{0,120}?\bsleep\b"),
        "description": "Sleep-wrapped `gh pr checks` is lane-side CI polling",
        "fix": "End the lane at: gh pr merge <n> --auto --squash, then exit",
    },
    "env_key_hunting": {
        "pattern": r"\benv\s+.*\b(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)\b",
        "description": "Credential hunting pattern forbidden",
        "fix": "Name exact env vars, never scan all env vars",
    },
    "auto_merge_bare_invocation": {
        # Matches: auto_merge.py NOT followed by (space+digit or space+--all)
        # This catches: bare invocation, or invocation with only flags (--json, --loop, etc.)
        "pattern": r"auto_merge\.py(?!(?:\s+\d|\s+--all\b))",
        "description": "auto_merge.py requires PR number(s) or --all flag; bare invocation blocked",
        "fix": "Use: auto_merge.py <n> [<n>...] or auto_merge.py --all (see guardrail 69e794d88fef)",
    },
}

# File patterns to scan (glob patterns, not regex)
SCANNABLE_GLOB_PATTERNS = ["*.py", "*.js", "*.mjs", "*.md", "*.sh"]

# Directory names (exact path components, relative to the scan root) never scanned.
SKIP_DIR_NAMES = {".git", "node_modules", ".pytest_cache", "state", "tests"}

# Categorical documentation exemptions. These are never dispatch templates --
# they are generated or hand-written PROSE that *describes* forbidden patterns
# (so a tool's job of preventing --admin/--auto/merge_train.py/etc necessarily
# mentions those tokens in its own one-line summary). Excluded by filename
# regardless of content drift from regeneration or merges, so a future
# `gen_tool_index.py --regenerate` or CLAUDE.md edit can never silently flip
# this gate red again (the #856 incident: a merge lane "fixed" this false
# positive by hand-appending `# dispatch-ok` markers onto a GENERATED file,
# which both fights regeneration and never covers a new tool's one-liner).
DOC_EXEMPT_FILENAMES = {"INDEX.md", "CLAUDE.md"}

# Patterns indicating dispatch context (must be in file to trigger full scan)
DISPATCH_INDICATORS = [
    r"\bAgent\s*\(",
    r"\bagent\s*\(",
    r"\bTaskCreate\s*\(",
    r"\bSendMessage\s*\(",
    r"agent\(\)",
    r"/\*\s*dispatch",
    r"dispatch\s*{",
]


def is_dispatch_file(content: str) -> bool:
    """Check if file contains dispatch-related code."""
    for indicator in DISPATCH_INDICATORS:
        if re.search(indicator, content):
            return True
    return False


def check_suppression(line: str) -> bool:
    """Check if line has dispatch-ok suppression (any comment style)."""
    return ("# dispatch-ok" in line or
            "// dispatch-ok" in line or
            "<!-- dispatch-ok -->" in line)


def is_index_summary_line(line: str) -> bool:
    """Check if a line is a module's `INDEX:` one-line tool summary.

    `INDEX:` lines are the docstring summary every tools/*.py module carries
    for `gen_tool_index.py` to aggregate into tools/INDEX.md. They are pure
    documentation ABOUT a tool's behavior (including, for security/lint
    tools, prose naming the exact forbidden tokens they detect or prevent),
    never an actual dispatch prompt -- so they are categorically exempt from
    forbidden-pattern scanning, independent of per-line `# dispatch-ok`
    markers (which do not survive `tools/INDEX.md` regeneration anyway).
    """
    return line.strip().startswith("INDEX:")


def _hash_comment_start(line: str) -> int:
    """Index of a '#' that starts a comment, or -1.

    A '#' starts a comment only when it is the first non-whitespace character
    of the line, or when it is preceded by whitespace AND followed by whitespace
    or end-of-line (`cmd  # note`). `PR #845` and `#845` are issue references,
    not comments, so the prose after them is still scanned (tracker 75cd331b1686).
    """
    first_code = len(line) - len(line.lstrip())
    for i, ch in enumerate(line):
        if ch != '#':
            continue
        if i == first_code:
            return i
        if i > 0 and line[i - 1].isspace() and (i + 1 == len(line) or line[i + 1].isspace()):
            return i
    return -1


def is_comment_only(line: str, pattern: str) -> bool:
    """Check if a pattern appears only in a comment, not in code.

    Returns True if the pattern is only found after # or // comment markers,
    indicating it's a comment-only reference that shouldn't trigger a violation.
    """
    # Find comment markers
    hash_pos = _hash_comment_start(line)
    slash_pos = line.find('//')

    # Determine the actual comment start position
    comment_start = None
    if hash_pos != -1 and slash_pos != -1:
        comment_start = min(hash_pos, slash_pos)
    elif hash_pos != -1:
        comment_start = hash_pos
    elif slash_pos != -1:
        comment_start = slash_pos

    # If no comment marker found, pattern is in code
    if comment_start is None:
        return False

    # Check if pattern appears before the comment marker (in code)
    code_part = line[:comment_start]
    if re.search(pattern, code_part, re.IGNORECASE):
        # Pattern found in code part, not just comment
        return False

    # Pattern only found in comment or not at all
    return True


# Prohibition markers. A forbidden flag or command that comes AFTER one of these
# in the same clause (same line, or earlier in its list item, with no ';' or '.'
# between marker and flag) is a MENTION (a rule about the command), not an
# instruction to run it. Case-insensitive, whole words/phrases.
PROHIBITION_MARKER_RE = re.compile(
    r"\b(?:never|forbidden|do\s+not|don['" + chr(0x2019) + r"]t|banned|prohibited|must\s+not|"
    r"disallowed|not\s+to|avoid|without)\b",
    re.IGNORECASE,
)
# A bare `no` or `not` immediately before a backticked flag: "no `--no-verify`".
BACKTICK_NEGATION_RE = re.compile(r"\b(?:no|not)\s+`\s*$", re.IGNORECASE)
# Clause boundaries: a marker only covers a flag that follows it in the same clause.
CLAUSE_BOUNDARY_RE = re.compile(r"[;.]")
# Prose describing a threat or a bypass (not an instruction). A line containing one
# of these words is a mention of the forbidden token it carries.
THREAT_CONTEXT_RE = re.compile(
    r"\b(?:threats?|bypass\w*|attacks?|adversar\w*|escap\w*|incidents?|would|could)\b",
    re.IGNORECASE,
)
# An angle-bracket placeholder argument (`auto_merge.py <n>`) is a documentation
# example, not an invocation. Only the sanctioned merge-automation commands use it.
PLACEHOLDER_ARG_RE = re.compile(r"\s+<[A-Za-z][\w .|-]*>")
PLACEHOLDER_EXEMPT_PATTERNS = {"auto_merge_bare_invocation", "gh_pr_merge_auto_bare"}
# A Markdown bullet or numbered item start. Indented lines after it belong to it.
LIST_ITEM_START_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# Leading decoration stripped before deciding whether a line is a command line.
LEADING_DECORATION_RE = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s+)?[`$>\s]*")
# A line whose first token is a shell command is an instruction, whatever it says.
COMMAND_LINE_RE = re.compile(r"(?:git|gh|python3?|cd|npm|node|bash|sh|pip|make)\b", re.IGNORECASE)
# Quoted spans on a line. A match inside one is code only when the quote is a
# string passed to an exec/spawn-style call, or assigned to a variable.
QUOTED_LITERAL_RE = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'|`[^`\n]*`")
CODE_CALL_BEFORE_QUOTE_RE = re.compile(
    r"(?:\bexec\w*|\bspawn\w*|\bPopen|\bsystem|\bcheck_output|\bcheck_call|\bsubprocess\.\w+)"
    r"\s*\((?:[^()]*,)?\s*\[?\s*$",
    re.IGNORECASE,
)
CODE_ASSIGN_BEFORE_QUOTE_RE = re.compile(r"\b[A-Za-z_$][\w$]*\s*=\s*\[?\s*$")


def list_item_owners(lines: List[str]) -> List[object]:
    """For each line, the index of the list item (bullet line) it belongs to, or None.

    A bullet starts an item; following lines indented under it are its
    continuation; a blank line or an unindented non-bullet line ends the item.
    """
    owners: List[object] = []
    owner = None
    for idx, raw in enumerate(lines):
        if LIST_ITEM_START_RE.match(raw):
            owner = idx
        elif not raw.strip():
            owner = None
        elif not (raw[:1] in (" ", "\t") and owner is not None):
            owner = None
        owners.append(owner)
    return owners


def _is_command_line(line: str) -> bool:
    """True when the line's first token is a shell command (an instruction)."""
    return bool(COMMAND_LINE_RE.match(LEADING_DECORATION_RE.sub("", line, count=1)))


def _in_code_literal(line: str, pos: int) -> bool:
    """True when position `pos` sits inside a quoted string that is code."""
    for quoted in QUOTED_LITERAL_RE.finditer(line):
        if quoted.start() < pos < quoted.end():
            before = line[:quoted.start()]
            return bool(CODE_CALL_BEFORE_QUOTE_RE.search(before)
                        or CODE_ASSIGN_BEFORE_QUOTE_RE.search(before))
    return False


def is_prohibition_mention(line: str, match_start: int, item_lines_before: List[str]) -> bool:
    """True when a forbidden match is a mention of a rule, not an instruction.

    Passes when a prohibition marker (or a bare `no`/`not` before a backtick) sits
    in the same clause as the match, on the same line or earlier in its list item,
    with no ';' or '.' between them. It also passes for a line that describes a
    threat or bypass. The match is never a mention when it is imperative: on a
    command line, or inside a code string passed to exec/spawn or assigned in code.
    """
    if _is_command_line(line) or _in_code_literal(line, match_start):
        return False
    # Threat/bypass prose is judged over the whole sentence-bearing item, so a
    # threat word on the item's first line covers a flag on its continuation line.
    if THREAT_CONTEXT_RE.search("\n".join(item_lines_before + [line])):
        return True
    preceding = "\n".join(item_lines_before + [line[:match_start]])
    clause = CLAUSE_BOUNDARY_RE.split(preceding)[-1]
    return bool(PROHIBITION_MARKER_RE.search(clause) or BACKTICK_NEGATION_RE.search(clause))


def find_violations(
    file_path: Path, content: str
) -> List[Dict]:
    """Find all dispatch policy violations in a file."""
    violations = []

    # Don't scan files that don't contain dispatch patterns
    if not is_dispatch_file(content):
        return violations

    lines = content.split("\n")
    owners = list_item_owners(lines)

    for line_num, line in enumerate(lines, 1):
        # Skip suppressed lines
        if check_suppression(line):
            continue

        # Skip INDEX: docstring summary lines -- documentation about a tool,
        # never a dispatch template (see is_index_summary_line docstring).
        if is_index_summary_line(line):
            continue

        owner = owners[line_num - 1]
        item_lines_before = lines[owner:line_num - 1] if owner is not None else []

        for pattern_key, pattern_info in FORBIDDEN_PATTERNS.items():
            for match in re.finditer(pattern_info["pattern"], line, re.IGNORECASE):
                # Skip if pattern only appears in comments. Inline backtick
                # spans and markdown fences are NOT exemptions: a command in
                # a dispatch prompt is an instruction wherever it is quoted.
                if is_comment_only(line, pattern_info["pattern"]):
                    continue

                # A documentation example with a placeholder argument (`<n>`) is not
                # an invocation of the command it names.
                if (pattern_key in PLACEHOLDER_EXEMPT_PATTERNS
                        and PLACEHOLDER_ARG_RE.match(line, match.end())):
                    continue

                # Skip a mention of the rule (prohibition marker before it).
                if is_prohibition_mention(line, match.start(), item_lines_before):
                    continue

                violations.append({
                    "file": str(file_path),
                    "line": line_num,
                    "pattern": pattern_key,
                    "description": pattern_info["description"],
                    "fix": pattern_info["fix"],
                    "code": line.strip(),
                })

    return violations


def scan_directory(
    start_path: Path, recursive: bool = True
) -> Tuple[Dict[str, List], List[str]]:
    """Scan directory for dispatch violations.

    Returns: (violations_by_file, errors)
    """
    violations_by_file = {}
    errors = []

    if not start_path.exists():
        errors.append(f"Path does not exist: {start_path}")
        return violations_by_file, errors

    if start_path.is_file():
        paths_to_scan = [start_path]
    else:
        # Find all scannable files
        paths_to_scan = []
        for pattern in SCANNABLE_GLOB_PATTERNS:
            if recursive:
                paths_to_scan.extend(start_path.glob(f"**/{pattern}"))
            else:
                paths_to_scan.extend(start_path.glob(pattern))

    scan_root = start_path if start_path.is_dir() else start_path.parent
    for file_path in sorted(set(paths_to_scan)):
        # Skip directories whose path component is exactly a skip name, relative
        # to the scan root. Substring matching on the absolute path wrongly
        # skipped state_store/ and every file under a checkout such as
        # C:/statewide/aesop (tracker 6a2736a273b8).
        rel_dirs = file_path.relative_to(scan_root).parts[:-1]
        if any(part in SKIP_DIR_NAMES for part in rel_dirs):
            continue
        # Skip test files specifically (test_*.py, etc.)
        if file_path.name.startswith("test_"):
            continue
        # Skip categorical documentation files (generated tool index, domain
        # CLAUDE.md docs) -- never dispatch templates; see DOC_EXEMPT_FILENAMES.
        if file_path.name in DOC_EXEMPT_FILENAMES:
            continue

        # Fail closed on read errors (reported as errors -> exit 2), but decode
        # with errors='replace' so a stray non-UTF-8 byte cannot hide the
        # forbidden command on the same file (tracker 4b3485fefc1e).
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            errors.append(f"Error reading {file_path}: {e}")
            continue

        violations = find_violations(file_path, content)
        if violations:
            violations_by_file[str(file_path)] = violations

    return violations_by_file, errors


def format_violations(violations_by_file: Dict, as_json: bool = False) -> str:
    """Format violations for output."""
    if as_json:
        result = {
            "violations": [],
            "total_violations": 0,
        }
        for file_path, violations in violations_by_file.items():
            for v in violations:
                result["violations"].append(v)
        result["total_violations"] = len(result["violations"])
        return json.dumps(result, indent=2)

    lines = []
    for file_path, violations in violations_by_file.items():
        lines.append(f"{file_path}:")
        for v in violations:
            lines.append(f"  Line {v['line']}: {v['pattern']}")
            lines.append(f"    {v['description']}")
            lines.append(f"    Code: {v['code']}")
            lines.append(f"    Fix: {v['fix']}")
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Dispatch linter - enforces merge automation and security rules"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="File or directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 if violations found (default behavior)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Show suggested fixes for violations",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output violations as JSON",
    )

    args = parser.parse_args()

    start_path = Path(args.path).resolve()
    violations_by_file, errors = scan_directory(start_path)

    # Print violations
    if violations_by_file:
        output = format_violations(violations_by_file, as_json=args.json)
        print(output)

    # Any path or file that could not be scanned is an error: fail closed (exit 2)
    # rather than reporting clean over files that were never read.
    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        return 2

    if violations_by_file:
        return 1

    # Clean
    if args.json:
        print(json.dumps({"violations": [], "total_violations": 0}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
