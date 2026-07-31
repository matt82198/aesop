#!/usr/bin/env python3
"""
Self-building stats counter for aesop README.

Computes git-derived metrics (verifiable by anyone who clones) and reads session telemetry
from docs/self-stats-data.json. All hard metrics in output carry verification markers.

Usage:
  python self_stats.py [--repo PATH] [--data-file PATH] [--markdown|--json]
  python self_stats.py --regenerate [--repo PATH] [--data-file PATH] [--stats-file PATH]
  python self_stats.py --update-readme [--repo PATH] [--stats-file PATH] [--readme PATH]
  python self_stats.py --check [--repo PATH] [--stats-file PATH] [--readme PATH]

Output modes:
  default  - Human-readable table
  --markdown - README block with <!-- SELF-STATS:START/END --> markers (markdown verification comments)
  --json   - Machine-readable JSON object

Special modes:
  --regenerate - Regenerate stats.json from live git state
  --update-readme - Update README.md between <!-- STATS:START/END --> markers with stats from stats.json
  --check - Exit non-zero on: README drift vs stats.json, internal contradiction
            (two disagreeing PR counts), zero-filler economics, missing merged-PR
            source, or stale receipts (generated_at age / commit lag past thresholds)

All hard metrics (percentages, multipliers, dollar amounts) in markdown output include
<!-- metrics-verified: <source> --> markers for the metrics_gate.py CI gate.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, Set, List


# Author classification rules
# Note: Primary author email is resolved from git config rather than hardcoded,
# so classification works for any repo without baking in personal info.
#
# This lookup MUST stay call-time, not import-time. Evaluating it at import
# froze the result to whatever git config the interpreter happened to start in,
# so classification depended on the caller's cwd instead of the repo actually
# being analyzed (a fresh clone or CI checkout then counted every human commit
# as "junk" and published authors_human=0).
def _get_default_author_email(repo_root: Optional[str] = None) -> Optional[str]:
    """Get the configured author email for a repo (call-time, repo-scoped)."""
    cmd = ["git"]
    if repo_root:
        cmd += ["-C", str(repo_root)]
    cmd += ["config", "user.email"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None  # No email classification if not configured


def human_emails(repo_root: Optional[str] = None) -> List[str]:
    """Resolve the set of emails treated as human authors, at call time.

    Precedence:
      1. AESOP_HUMAN_EMAILS env var (comma-separated) — for repos with several
         human contributors, or to make CI deterministic.
      2. The analyzed repo's configured `user.email`.
    """
    override = os.environ.get("AESOP_HUMAN_EMAILS", "").strip()
    if override:
        return [e.strip() for e in override.split(",") if e.strip()]
    email = _get_default_author_email(repo_root)
    return [email] if email else []


AUTHOR_CLASSIFICATION = {
    "human": {
        # Resolved at call time via human_emails(); see note above. Kept empty
        # here so nothing reads a stale import-time snapshot.
        "emails": [],
        "description": "Real human developers (deduplicated by email)"
    },
    "model": {
        "email_patterns": [
            r"^noreply@anthropic\.com$",
            r"^noreply@aesop$"
        ],
        "description": "Claude AI model tiers (deduplicated by normalized tier name)"
    },
    "bot": {
        "name_patterns": [r"\[bot\]$"],
        "description": "Automated bots (e.g., dependabot)"
    },
    "junk": {
        "emails": ["test@example.com", "aesop@open-source"],
        "names": ["aesop", "Aesop Contributors"],
        "description": "Test fixtures and generic identities (excluded from counts)"
    }
}


def extract_model_tier(model_name: str) -> str:
    """Extract and normalize model tier from name.

    Examples:
      "Claude Opus 4.8" -> "Opus 4.8"
      "Claude Opus 4.8 (1M context)" -> "Opus 4.8"
      "Claude Fable 5" -> "Fable 5"
      "Claude Haiku 4.5" -> "Haiku 4.5"
      "Claude Opus 5.0" -> "Opus 5"  # normalize variant to canonical name

    Returns the normalized tier name, or the original if unrecognized.
    """
    # Remove "Claude " prefix if present
    name = model_name.replace("Claude ", "").strip()

    # Remove anything in parentheses (context hints, etc.)
    name = re.sub(r'\s*\([^)]*\)\s*', '', name).strip()

    # Normalize variants to canonical names: "Opus 5.0" -> "Opus 5", "Haiku 4" -> "Haiku 4.5"
    name = re.sub(r'^Opus 5\.0$', 'Opus 5', name)
    name = re.sub(r'^Haiku 4$', 'Haiku 4.5', name)

    return name


def classify_author(
    name: str, email: str, repo_root: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """Classify an author identity.

    Args:
        name: Author display name
        email: Author email address
        repo_root: Repo whose git config supplies the human email (defaults to cwd)

    Returns:
        Tuple of (classification, metadata):
        - classification: "human", "model", "bot", "junk"
        - metadata: For models, the normalized tier name; otherwise None

    Classification priority:
    1. Junk (test fixtures, generic names)
    2. Bot (matches [bot] pattern)
    3. Model (specific email patterns)
    4. Human (specific emails)
    5. Default: junk (unknown identities)
    """

    # Check junk first (highest priority)
    if email in AUTHOR_CLASSIFICATION["junk"]["emails"]:
        return ("junk", None)
    if name in AUTHOR_CLASSIFICATION["junk"]["names"]:
        return ("junk", None)

    # Check bot
    for pattern in AUTHOR_CLASSIFICATION["bot"]["name_patterns"]:
        if re.search(pattern, name):
            return ("bot", None)

    # Check model
    for pattern in AUTHOR_CLASSIFICATION["model"]["email_patterns"]:
        if re.match(pattern, email):
            # Extract tier name from the display name
            tier = extract_model_tier(name)
            return ("model", tier)

    # Check human (resolved at call time against the analyzed repo)
    if email in human_emails(repo_root):
        return ("human", None)

    # Default: treat unknown as junk (conservative)
    return ("junk", None)


# Freshness gate defaults — practical, not per-commit. The daily stats-refresh
# workflow keeps generated_at well under the age window; the commit-lag window is
# generous so ordinary feature PRs (which don't regenerate stats) stay green.
DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_MAX_COMMITS_BEHIND = 200

# Economics token/cost ratio fields that are meaningless as 0.0 filler when no
# token ledger is available. They must be OMITTED (not zero) in that case.
_ECON_RATIO_FIELDS = (
    ("cost_per_loc", "tokens_per_loc"),
    ("cost_per_merged_pr", "tokens_per_pr"),
    ("cost_per_wave", "tokens_per_wave"),
    ("unit_economics", "cost_per_backlog_item"),
)


def _collect_merged_pr_counts(stats_dict: Dict[str, Any]) -> Dict[str, int]:
    """Collect every merged-PR count asserted anywhere in stats.json.

    Returns a mapping of location -> count so contradictions can be reported
    with their source. A single-sourced file yields exactly one distinct value.
    """
    counts: Dict[str, int] = {}
    git = stats_dict.get("git", {})
    if isinstance(git, dict) and isinstance(git.get("merged_prs"), int):
        counts["git.merged_prs"] = git["merged_prs"]

    econ = stats_dict.get("economics", {})
    if isinstance(econ, dict):
        if isinstance(econ.get("merged_prs"), int):
            counts["economics.merged_prs"] = econ["merged_prs"]
        cpp = econ.get("cost_per_merged_pr")
        if isinstance(cpp, dict) and isinstance(cpp.get("merged_prs"), int):
            counts["economics.cost_per_merged_pr.merged_prs"] = cpp["merged_prs"]
        unit = econ.get("unit_economics")
        if (isinstance(unit, dict)
                and unit.get("backlog_item_proxy") == "merged_prs"
                and isinstance(unit.get("items_count"), int)):
            counts["economics.unit_economics.items_count"] = unit["items_count"]
    return counts


def validate_stats_integrity(
    stats_dict: Dict[str, Any],
    repo_root: str = ".",
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_commits_behind: int = DEFAULT_MAX_COMMITS_BEHIND,
) -> List[str]:
    """Validate stats.json for internal consistency and freshness.

    Returns a list of human-readable error strings (empty == valid). Checks:
      1. Merged-PR count contradiction: any two PR counts in the file disagreeing.
      2. Missing/invalid provenance: git.merged_prs present without a valid
         merged_prs_source ('gh-api' | 'git-log').
      3. Zero-filler economics: token/cost ratio fields present while no token
         ledger is available (0.0-but-present filler that reads as measured).
      4. Age staleness: generated_at older than max_age_days.
      5. Commit-lag staleness: recorded total_commits more than
         max_commits_behind behind the current HEAD (skipped on negative delta,
         e.g. shallow clones where HEAD sees fewer commits than recorded).
    """
    errors: List[str] = []

    # 1. Merged-PR count contradiction
    counts = _collect_merged_pr_counts(stats_dict)
    distinct = set(counts.values())
    if len(distinct) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        errors.append(
            f"Merged-PR count contradiction: {detail}. "
            "The file must report ONE merged-PR count with ONE definition."
        )

    # 2. Provenance / source field
    git = stats_dict.get("git", {})
    if isinstance(git, dict) and "merged_prs" in git:
        source = git.get("merged_prs_source")
        if source not in ("gh-api", "git-log"):
            errors.append(
                "Missing/invalid git.merged_prs_source: expected 'gh-api' or 'git-log', "
                f"got {source!r}. Regenerate via scripts/verify-stats.sh --regenerate."
            )

    # 3. Zero-filler economics
    econ = stats_dict.get("economics")
    if isinstance(econ, dict):
        ledger_available = econ.get("token_ledger_available")
        ratio_present = False
        for block_key, ratio_key in _ECON_RATIO_FIELDS:
            block = econ.get(block_key)
            if isinstance(block, dict) and ratio_key in block:
                ratio_present = True
                break
        if ratio_present and ledger_available is not True:
            errors.append(
                "Economics zero-filler: token/cost ratio fields are present without an "
                "available token ledger (token_ledger_available is not true). Omit these "
                "fields when unmeasured instead of emitting 0.0."
            )

    # 4. Age staleness
    generated_at = stats_dict.get("generated_at")
    if generated_at:
        try:
            gen_dt = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
            if gen_dt.tzinfo is None:
                gen_dt = gen_dt.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - gen_dt).days
            if age_days > max_age_days:
                errors.append(
                    f"Stale receipts: stats.json generated_at is {age_days} days old "
                    f"(limit {max_age_days}). Run scripts/verify-stats.sh --regenerate."
                )
        except (ValueError, TypeError):
            errors.append(
                f"Invalid generated_at timestamp {generated_at!r}. "
                "Run scripts/verify-stats.sh --regenerate."
            )

    # 5. Commit-lag staleness
    recorded_commits = git.get("total_commits") if isinstance(git, dict) else None
    if isinstance(recorded_commits, int) and repo_root:
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=30,
            )
            current = int((result.stdout or "").strip()) if result.returncode == 0 else None
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, Exception):
            current = None
        if current is not None:
            delta = current - recorded_commits
            if delta > max_commits_behind:
                errors.append(
                    f"Stale receipts: stats.json is {delta} commits behind HEAD "
                    f"(limit {max_commits_behind}). Run scripts/verify-stats.sh --regenerate."
                )

    return errors


class GitStats:
    """Compute statistics from git repository."""

    def __init__(self, repo_root: str = "."):
        """Initialize with repo root path."""
        self.repo_root = Path(repo_root)
        self._merged_prs = None
        self._merged_prs_source = None
        self._total_commits = None
        self._project_age_days = None
        self._wave_count = None
        self._insertions_deletions = None
        self._files_tracked = None
        self._distinct_coauthors = None
        self._lines_of_code = None
        self._authors_human = None
        self._model_tiers = None
        self._model_tier_names = None

    def _run_git(self, *args, check=True) -> str:
        """Run git command in repo, return stdout."""
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=check,
            )
            return (result.stdout or "").strip()
        except FileNotFoundError:
            return ""

    def _origin_slug(self) -> Optional[str]:
        """Parse 'owner/repo' from the origin remote URL (GitHub remotes only).

        Returns None when there is no origin remote or it is not a GitHub URL,
        in which case the gh API path is skipped entirely (offline-reproducible).
        """
        url = self._run_git("remote", "get-url", "origin", check=False)
        if not url:
            return None
        match = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url.strip())
        if not match:
            return None
        return f"{match.group(1)}/{match.group(2)}"

    def _compute_merged_prs(self) -> Tuple[int, str]:
        """Compute the single merged-PR count and record its source.

        ONE count, ONE definition, with provenance:
          - source "gh-api": total merged PRs per the GitHub search API
            (authoritative; requires a GitHub origin remote and a working `gh`).
          - source "git-log": distinct PR numbers found in commit subjects,
            union of merge-commit ("Merge pull request #N") and squash-merge
            ("... (#N)") patterns, deduplicated.

        Whichever path succeeds populates the SAME field; consumers (economics,
        README) must never recompute their own count.
        """
        # Try gh API first (preferred, authoritative) — only when origin is a GitHub repo
        slug = self._origin_slug()
        if slug:
            try:
                result = subprocess.run(
                    [
                        "gh",
                        "api",
                        "-X",
                        "GET",
                        "search/issues",
                        "-f",
                        f"q=repo:{slug} is:pr is:merged",
                        "--jq",
                        ".total_count"
                    ],
                    cwd=str(self.repo_root),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=10
                )
                if result.returncode == 0 and result.stdout:
                    try:
                        return (int(result.stdout.strip()), "gh-api")
                    except (ValueError, TypeError):
                        # gh returned non-numeric output, fall through to git
                        pass
            except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
                # gh not found, timeout, or other error — fall through to git
                pass

        # Git fallback: count distinct PR numbers from commit subjects
        try:
            output = self._run_git("log", "--format=%s", check=False)
            if not output:
                return (0, "git-log")

            # Collect distinct PR numbers from both patterns:
            # 1. "Merge pull request #N" (merge-commit style)
            # 2. "(#N)" at end of subject (squash-merge style)
            pr_numbers = set()

            for match in re.finditer(r"^Merge pull request #(\d+)", output, re.MULTILINE):
                pr_numbers.add(int(match.group(1)))

            for match in re.finditer(r"\(#(\d+)\)\s*$", output, re.MULTILINE):
                pr_numbers.add(int(match.group(1)))

            return (len(pr_numbers), "git-log")
        except Exception:
            return (0, "git-log")

    def _merged_prs_resolved(self) -> Tuple[int, str]:
        """Cached (count, source) pair — both always resolved together."""
        if self._merged_prs is None:
            self._merged_prs, self._merged_prs_source = self._compute_merged_prs()
        return (self._merged_prs, self._merged_prs_source)

    @property
    def merged_prs(self) -> int:
        """The single merged-PR count (see _compute_merged_prs for the definition)."""
        return self._merged_prs_resolved()[0]

    @property
    def merged_prs_source(self) -> str:
        """Provenance of merged_prs: 'gh-api' or 'git-log'."""
        return self._merged_prs_resolved()[1]

    @property
    def total_commits(self) -> int:
        """Total commit count."""
        if self._total_commits is not None:
            return self._total_commits

        try:
            output = self._run_git("rev-list", "--count", "HEAD", check=False)
            count = int(output) if output else 0
            self._total_commits = count
            return count
        except (ValueError, Exception):
            self._total_commits = 0
            return 0

    @property
    def project_age_days(self) -> Optional[int]:
        """Project age in days (first commit to now)."""
        if self._project_age_days is not None:
            return self._project_age_days

        try:
            # Get timestamp of the earliest commit (root) — --reverse lists
            # oldest first, so the first line is the project's birth.
            output = self._run_git(
                "log", "--reverse", "--format=%cI", check=False
            )
            if not output:
                self._project_age_days = None
                return None

            first_commit_iso = output.split("\n", 1)[0].strip()
            if not first_commit_iso:
                self._project_age_days = None
                return None

            # Parse ISO format timestamp
            first_commit_dt = datetime.fromisoformat(first_commit_iso.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)
            age_days = (now_dt - first_commit_dt).days

            self._project_age_days = age_days
            return age_days
        except Exception:
            self._project_age_days = None
            return None

    @property
    def wave_count(self) -> int:
        """Count of distinct waves (parse wave labels or release tags)."""
        if self._wave_count is not None:
            return self._wave_count

        try:
            # First try parsing wave labels from merge commit messages
            output = self._run_git("log", "--format=%B", check=False)
            if output:
                # Count lines like "wave-N" (case insensitive)
                waves = set()
                for match in re.finditer(r"wave[_-]?(\d+)", output, re.IGNORECASE):
                    waves.add(int(match.group(1)))
                if waves:
                    self._wave_count = len(waves)
                    return len(waves)

            # Fallback: count release tags (v*)
            tags = self._run_git("tag", "-l", "v*", check=False)
            tag_count = len([t for t in tags.split("\n") if t.strip()])
            self._wave_count = tag_count
            return tag_count
        except Exception:
            self._wave_count = 0
            return 0

    @property
    def insertions_deletions(self) -> int:
        """Total insertions + deletions across all commits."""
        if self._insertions_deletions is not None:
            return self._insertions_deletions

        try:
            output = self._run_git(
                "log", "--numstat", "--format=%H", check=False
            )
            if not output:
                self._insertions_deletions = 0
                return 0

            total = 0
            for line in output.split("\n"):
                parts = line.split("\t")
                if len(parts) >= 2:
                    try:
                        # Skip lines that are commit hashes or other non-numstat data
                        insertions = int(parts[0])
                        deletions = int(parts[1])
                        total += insertions + deletions
                    except ValueError:
                        continue

            self._insertions_deletions = total
            return total
        except Exception:
            self._insertions_deletions = 0
            return 0

    @property
    def files_tracked(self) -> int:
        """Count of tracked files."""
        if self._files_tracked is not None:
            return self._files_tracked

        try:
            output = self._run_git("ls-files", check=False)
            count = len([f for f in output.split("\n") if f.strip()])
            self._files_tracked = count
            return count
        except Exception:
            self._files_tracked = 0
            return 0

    @property
    def distinct_coauthors(self) -> int:
        """Count of distinct authors including co-authors.

        Filters out fixture identities (Test User <test@example.com>) that leaked
        into commits due to test config pollution (fix/git-identity-guard).
        """
        if self._distinct_coauthors is not None:
            return self._distinct_coauthors

        try:
            # Get all authors
            output = self._run_git("log", "--format=%an", check=False)
            authors = set()
            if output:
                for author in output.split("\n"):
                    if author.strip():
                        authors.add(author.strip())

            # Get all co-authors from commit messages
            commit_msg = self._run_git("log", "--format=%B", check=False)
            if commit_msg:
                for match in re.finditer(r"Co-Authored-By:\s*(.+?)(?:\n|$)", commit_msg):
                    coauthor = match.group(1).strip()
                    if coauthor:
                        # Exclude fixture identities (test pollution)
                        if coauthor == "Test User <test@example.com>":
                            continue
                        authors.add(coauthor)

            count = len(authors)
            self._distinct_coauthors = count
            return count
        except Exception:
            self._distinct_coauthors = 0
            return 0

    @property
    def authors_human(self) -> int:
        """Count of distinct human authors (deduplicated by email).

        Counts distinct human emails from all commit authors and co-authors.
        """
        if self._authors_human is not None:
            return self._authors_human

        try:
            human_emails: Set[str] = set()

            # Get all authors
            output = self._run_git("log", "--format=%an|%ae", check=False)
            if output:
                for line in output.split("\n"):
                    if not line.strip():
                        continue
                    parts = line.split("|", 1)
                    if len(parts) == 2:
                        name, email = parts
                        classification, _ = classify_author(name.strip(), email.strip(), self.repo_root)
                        if classification == "human":
                            human_emails.add(email.strip())

            # Get all co-authors from commit messages
            commit_msg = self._run_git("log", "--format=%B", check=False)
            if commit_msg:
                for match in re.finditer(r"Co-Authored-By:\s*(.+?)\s*<(.+?)>", commit_msg):
                    name, email = match.group(1).strip(), match.group(2).strip()
                    classification, _ = classify_author(name, email, self.repo_root)
                    if classification == "human":
                        human_emails.add(email)

            count = len(human_emails)
            self._authors_human = count
            return count
        except Exception:
            self._authors_human = 0
            return 0

    @property
    def model_tiers(self) -> int:
        """Count of distinct Claude model tiers used.

        Counts unique model tiers (e.g., Haiku 4.5, Opus 4.8, Fable 5) from Co-Authored-By trailers.
        Variants like "Claude Opus 4.8" and "Claude Opus 4.8 (1M context)" are merged to one tier.
        """
        if self._model_tiers is not None:
            return self._model_tiers

        try:
            tiers: Set[str] = set()

            # Get all co-authors from commit messages
            commit_msg = self._run_git("log", "--format=%B", check=False)
            if commit_msg:
                for match in re.finditer(r"Co-Authored-By:\s*(.+?)\s*<(.+?)>", commit_msg):
                    name, email = match.group(1).strip(), match.group(2).strip()
                    classification, tier = classify_author(name, email, self.repo_root)
                    if classification == "model" and tier:
                        tiers.add(tier)

            count = len(tiers)
            self._model_tiers = count
            return count
        except Exception:
            self._model_tiers = 0
            return 0

    @property
    def model_tier_names(self) -> List[str]:
        """List of distinct Claude model tier names.

        Returns sorted list of unique model tiers found in Co-Authored-By trailers.
        """
        if self._model_tier_names is not None:
            return self._model_tier_names

        try:
            tiers: Set[str] = set()

            # Get all co-authors from commit messages
            commit_msg = self._run_git("log", "--format=%B", check=False)
            if commit_msg:
                for match in re.finditer(r"Co-Authored-By:\s*(.+?)\s*<(.+?)>", commit_msg):
                    name, email = match.group(1).strip(), match.group(2).strip()
                    classification, tier = classify_author(name, email, self.repo_root)
                    if classification == "model" and tier:
                        tiers.add(tier)

            result = sorted(list(tiers))
            self._model_tier_names = result
            return result
        except Exception:
            self._model_tier_names = []
            return []

    @property
    def lines_of_code(self) -> int:
        """Count total lines in tracked files."""
        if self._lines_of_code is not None:
            return self._lines_of_code

        try:
            # Get list of tracked files
            output = self._run_git("ls-files", check=False)
            if not output:
                self._lines_of_code = 0
                return 0

            files = [f.strip() for f in output.split("\n") if f.strip()]
            total_lines = 0

            for file_path in files:
                try:
                    file_full_path = self.repo_root / file_path
                    if file_full_path.is_file():
                        with open(file_full_path, 'r', encoding='utf-8', errors='ignore') as f:
                            total_lines += sum(1 for _ in f)
                except Exception:
                    # Skip files we can't read
                    continue

            self._lines_of_code = total_lines
            return total_lines
        except Exception:
            self._lines_of_code = 0
            return 0


class SessionTelemetry:
    """Load session telemetry from JSON file."""

    def __init__(self, data_file: str = "docs/self-stats-data.json"):
        """Initialize with data file path."""
        self.data_file = Path(data_file)
        self._data = None
        self._load()

    def _load(self):
        """Load JSON data, silently ignore missing/invalid files."""
        if not self.data_file.exists():
            self._data = {}
            return

        try:
            with open(self.data_file, encoding="utf-8") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, IOError):
            self._data = {}

    def _get(self, key: str) -> Optional[Any]:
        """Get field, return None if missing or null."""
        if not self._data:
            return None
        value = self._data.get(key)
        return value if value is not None else None

    @property
    def total_sessions(self) -> Optional[int]:
        return self._get("total_sessions")

    @property
    def total_turns(self) -> Optional[int]:
        return self._get("total_turns")

    @property
    def total_user_prompts(self) -> Optional[int]:
        return self._get("total_user_prompts")

    @property
    def max_tokens_single_turn(self) -> Optional[int]:
        return self._get("max_tokens_single_turn")

    @property
    def cumulative_agent_runs(self) -> Optional[int]:
        return self._get("cumulative_agent_runs")

    @property
    def cumulative_tokens(self) -> Optional[int]:
        return self._get("cumulative_tokens")

    @property
    def total_coding_hours(self) -> Optional[float]:
        return self._get("total_coding_hours")


class StatsCounter:
    """Combine git and telemetry stats, format for output."""

    def __init__(self, repo_root: str = ".", data_file: str = None):
        """Initialize with repo root and optional data file."""
        self.git = GitStats(repo_root=repo_root)
        if data_file is None:
            # Infer from repo root
            data_file = str(Path(repo_root) / "docs" / "self-stats-data.json")
        self.telemetry = SessionTelemetry(data_file=data_file)

    def table(self) -> str:
        """Human-readable table format."""
        lines = []
        lines.append("")
        lines.append("=" * 50)
        lines.append("Aesop Self-Building Stats")
        lines.append("=" * 50)
        lines.append("")

        # Git-derived stats
        lines.append("Repository Metrics:")
        if self.git.merged_prs > 0:
            lines.append(f"  Merged PRs:           {self.git.merged_prs}")
        if self.git.total_commits > 0:
            lines.append(f"  Total Commits:        {self.git.total_commits}")
        if self.git.project_age_days is not None and self.git.project_age_days >= 0:
            lines.append(f"  Project Age (days):   {self.git.project_age_days}")
        if self.git.insertions_deletions > 0:
            lines.append(f"  Insertions+Deletions: {self.git.insertions_deletions}")
        if self.git.files_tracked > 0:
            lines.append(f"  Files Tracked:        {self.git.files_tracked}")
        if self.git.distinct_coauthors > 0:
            lines.append(f"  Distinct Co-authors:  {self.git.distinct_coauthors}")
        if self.git.lines_of_code > 0:
            lines.append(f"  Lines of Code:        {self.git.lines_of_code}")

        # Session telemetry (only if present)
        if any([
            self.telemetry.total_sessions,
            self.telemetry.total_turns,
            self.telemetry.cumulative_tokens,
        ]):
            lines.append("")
            lines.append("Session Telemetry:")
            if self.telemetry.total_sessions is not None:
                lines.append(f"  Total Sessions:       {self.telemetry.total_sessions}")
            if self.telemetry.total_turns is not None:
                lines.append(f"  Total Turns:          {self.telemetry.total_turns}")
            if self.telemetry.total_user_prompts is not None:
                lines.append(f"  User Prompts:         {self.telemetry.total_user_prompts}")
            if self.telemetry.cumulative_agent_runs is not None:
                lines.append(f"  Agent Runs:           {self.telemetry.cumulative_agent_runs}")
            if self.telemetry.cumulative_tokens is not None:
                lines.append(f"  Total Tokens:         {self.telemetry.cumulative_tokens}")
            if self.telemetry.max_tokens_single_turn is not None:
                lines.append(f"  Max Tokens/Turn:      {self.telemetry.max_tokens_single_turn}")
            if self.telemetry.total_coding_hours is not None:
                lines.append(f"  Coding Hours:         {self.telemetry.total_coding_hours}")

        lines.append("")
        lines.append("=" * 50)
        lines.append("")

        return "\n".join(lines)

    def markdown(self) -> str:
        """Markdown output with verification markers for hard metrics."""
        lines = []
        lines.append("<!-- SELF-STATS:START -->")
        lines.append("")
        lines.append("## Aesop builds itself")
        lines.append("")
        lines.append(
            "Aesop is built entirely by its own `/buildsystem` wave cycle—running parallel Haiku fleets "
            "across ranked backlog items, verifying merges, auditing orchestration health. "
            "These stats are the receipts: all numbers computed LIVE from git, verified by anyone who clones."
        )
        lines.append("")

        # Build stat rows
        rows = []

        if self.git.merged_prs > 0:
            rows.append(
                f"| Merged PRs | {self.git.merged_prs} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if self.git.total_commits > 0:
            rows.append(
                f"| Total Commits | {self.git.total_commits} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if self.git.project_age_days is not None and self.git.project_age_days >= 0:
            rows.append(
                f"| Project Age | {self.git.project_age_days} days <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if self.git.insertions_deletions > 0:
            rows.append(
                f"| Insertions + Deletions | {self.git.insertions_deletions:,} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if self.git.files_tracked > 0:
            rows.append(
                f"| Files Tracked | {self.git.files_tracked} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        # Render classified author stats
        if self.git.authors_human > 0 or self.git.model_tiers > 0:
            author_parts = []
            if self.git.authors_human > 0:
                author_parts.append(f"{self.git.authors_human} human")
            if self.git.model_tiers > 0:
                author_parts.append(f"{self.git.model_tiers} Claude model tier{'s' if self.git.model_tiers != 1 else ''}")
            authors_text = " + ".join(author_parts)
            rows.append(
                f"| Authors | {authors_text} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        elif self.git.distinct_coauthors > 0:
            rows.append(
                f"| Distinct Co-authors | {self.git.distinct_coauthors} <!-- metrics-verified: self_stats.py (git log) --> |"
            )

        # Session telemetry
        if self.telemetry.total_sessions is not None:
            rows.append(
                f"| Sessions | {self.telemetry.total_sessions} <!-- metrics-verified: docs/self-stats-data.json --> |"
            )
        if self.telemetry.total_turns is not None:
            rows.append(
                f"| Total Turns | {self.telemetry.total_turns} <!-- metrics-verified: docs/self-stats-data.json --> |"
            )
        if self.telemetry.cumulative_tokens is not None:
            rows.append(
                f"| Cumulative Tokens | {self.telemetry.cumulative_tokens:,} <!-- metrics-verified: docs/self-stats-data.json --> |"
            )
        if self.telemetry.total_coding_hours is not None:
            rows.append(
                f"| Coding Hours | {self.telemetry.total_coding_hours} <!-- metrics-verified: docs/self-stats-data.json --> |"
            )

        # Only add table if we have rows
        if rows:
            lines.append("| Metric | Value |")
            lines.append("| --- | --- |")
            lines.extend(rows)
            lines.append("")

        lines.append("<!-- SELF-STATS:END -->")
        lines.append("")

        return "\n".join(lines)

    def json(self) -> str:
        """Machine-readable JSON output."""
        data = {
            "git": {
                "merged_prs": self.git.merged_prs,
                "merged_prs_source": self.git.merged_prs_source,
                "total_commits": self.git.total_commits,
                "project_age_days": self.git.project_age_days,
                "wave_count": self.git.wave_count,
                "insertions_deletions": self.git.insertions_deletions,
                "files_tracked": self.git.files_tracked,
                "distinct_coauthors": self.git.distinct_coauthors,
                "authors_human": self.git.authors_human,
                "model_tiers": self.git.model_tiers,
                "model_tier_names": self.git.model_tier_names,
            },
            "telemetry": {
                "total_sessions": self.telemetry.total_sessions,
                "total_turns": self.telemetry.total_turns,
                "total_user_prompts": self.telemetry.total_user_prompts,
                "max_tokens_single_turn": self.telemetry.max_tokens_single_turn,
                "cumulative_agent_runs": self.telemetry.cumulative_agent_runs,
                "cumulative_tokens": self.telemetry.cumulative_tokens,
                "total_coding_hours": self.telemetry.total_coding_hours,
            },
        }
        return json.dumps(data, indent=2)

    def to_dict_with_metadata(self) -> Dict[str, Any]:
        """Export stats as dict with metadata (for stats.json)."""
        data = json.loads(self.json())
        data["generated_at"] = datetime.now(timezone.utc).isoformat()
        data["loc"] = self.git.lines_of_code

        # Record HEAD sha for provenance / freshness checks (empty repos yield "")
        head_sha = self.git._run_git("rev-parse", "HEAD", check=False)
        if head_sha:
            data["head_sha"] = head_sha

        # Add cost economics metrics (requires cost_econ module)
        try:
            from cost_econ import calculate_economics, get_metric_honesty_caveats
            # Infer state directory from repo root or config
            repo_path = Path(self.git.repo_root)
            state_dir = repo_path / "state"

            # Single-source the merged-PR count: economics consumes the SAME
            # count and provenance that the git block reports, never recomputing.
            economics = calculate_economics(
                repo_root=str(repo_path),
                state_dir=str(state_dir) if state_dir.exists() else None,
                config_file=None,
                merged_prs=self.git.merged_prs,
                merged_prs_source=self.git.merged_prs_source,
            )

            data["economics"] = economics
            data["economics_caveats"] = get_metric_honesty_caveats()
        except Exception:
            # Graceful fallback: if cost_econ unavailable, skip economics metrics
            pass

        return data

    def save_stats(self, output_file: str = "stats.json") -> None:
        """Regenerate stats.json from live git state."""
        output_path = Path(output_file)
        data = self.to_dict_with_metadata()

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def load_stats(self, stats_file: str = "stats.json") -> Optional[Dict[str, Any]]:
        """Load previously saved stats from stats.json."""
        stats_path = Path(stats_file)
        if not stats_path.exists():
            return None

        try:
            with open(stats_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def get_stats_load_error(self, stats_file: str = "stats.json") -> Optional[str]:
        """Check if stats.json exists and is readable. Return error type or None.

        Returns:
            None if file exists and is readable
            "MISSING" if file doesn't exist
            "UNREADABLE" if file exists but is corrupted/unreadable
        """
        stats_path = Path(stats_file)
        if not stats_path.exists():
            return "MISSING"

        try:
            with open(stats_path, 'r', encoding='utf-8') as f:
                json.load(f)
            return None  # File is readable
        except (json.JSONDecodeError, IOError):
            return "UNREADABLE"

    def markdown_from_dict(self, stats_dict: Dict[str, Any]) -> str:
        """Generate markdown block from a stats dictionary (e.g., from stats.json)."""
        lines = []
        lines.append("<!-- STATS:START -->")
        lines.append("")
        lines.append("## Aesop builds itself")
        lines.append("")
        lines.append(
            "Aesop is built entirely by its own `/buildsystem` wave cycle—running parallel Haiku fleets "
            "across ranked backlog items, verifying merges, auditing orchestration health. "
            "These stats are the receipts: all numbers computed LIVE from git, verified by anyone who clones."
        )
        lines.append("")

        # Extract git stats from dict
        git_stats = stats_dict.get("git", {})
        rows = []

        if git_stats.get("merged_prs", 0) > 0:
            rows.append(
                f"| Merged PRs | {git_stats['merged_prs']} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if git_stats.get("total_commits", 0) > 0:
            rows.append(
                f"| Total Commits | {git_stats['total_commits']} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if git_stats.get("project_age_days") is not None and git_stats.get("project_age_days", 0) >= 0:
            rows.append(
                f"| Project Age | {git_stats['project_age_days']} days <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if git_stats.get("insertions_deletions", 0) > 0:
            rows.append(
                f"| Insertions + Deletions | {git_stats['insertions_deletions']:,} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if git_stats.get("files_tracked", 0) > 0:
            rows.append(
                f"| Files Tracked | {git_stats['files_tracked']} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        # Render classified author stats (prefer new fields over legacy distinct_coauthors)
        authors_human = git_stats.get("authors_human", 0)
        model_tiers = git_stats.get("model_tiers", 0)
        model_tier_names = git_stats.get("model_tier_names", [])
        if authors_human > 0 or model_tiers > 0:
            # Build the authors row using new classified fields
            author_parts = []
            if authors_human > 0:
                author_parts.append(f"{authors_human} human")
            if model_tiers > 0:
                author_parts.append(f"{model_tiers} Claude model tier{'s' if model_tiers != 1 else ''}")
            authors_text = " + ".join(author_parts)
            rows.append(
                f"| Authors | {authors_text} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        elif git_stats.get("distinct_coauthors", 0) > 0:
            # Fallback to legacy field for backward compatibility
            rows.append(
                f"| Distinct Co-authors | {git_stats['distinct_coauthors']} <!-- metrics-verified: self_stats.py (git log) --> |"
            )

        # Session telemetry
        telemetry = stats_dict.get("telemetry", {})
        if telemetry.get("total_sessions") is not None:
            rows.append(
                f"| Sessions | {telemetry['total_sessions']} <!-- metrics-verified: docs/self-stats-data.json --> |"
            )
        if telemetry.get("total_turns") is not None:
            rows.append(
                f"| Total Turns | {telemetry['total_turns']} <!-- metrics-verified: docs/self-stats-data.json --> |"
            )
        if telemetry.get("cumulative_tokens") is not None:
            rows.append(
                f"| Cumulative Tokens | {telemetry['cumulative_tokens']:,} <!-- metrics-verified: docs/self-stats-data.json --> |"
            )
        if telemetry.get("total_coding_hours") is not None:
            rows.append(
                f"| Coding Hours | {telemetry['total_coding_hours']} <!-- metrics-verified: docs/self-stats-data.json --> |"
            )

        # Only add table if we have rows
        if rows:
            lines.append("| Metric | Value |")
            lines.append("| --- | --- |")
            lines.extend(rows)
            lines.append("")

        lines.append("<!-- STATS:END -->")
        lines.append("")

        return "\n".join(lines)

    def update_readme(self, readme_path: str = "README.md", stats_file: str = "stats.json") -> bool:
        """Update README.md between <!-- STATS:START --> and <!-- STATS:END --> markers.

        Returns True if updated, False if markers not found (gracefully no-op).
        """
        # Load stats from file
        stats_dict = self.load_stats(stats_file)
        if stats_dict is None:
            # stats.json doesn't exist, regenerate it first
            self.save_stats(stats_file)
            stats_dict = self.load_stats(stats_file)

        readme_file = Path(readme_path)
        if not readme_file.exists():
            return False

        with open(readme_file, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # Look for markers
        start_marker = "<!-- STATS:START -->"
        end_marker = "<!-- STATS:END -->"

        if start_marker not in content or end_marker not in content:
            # Markers not found, gracefully no-op
            return False

        # Generate new markdown block
        new_block = self.markdown_from_dict(stats_dict)

        # Replace the block
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker) + len(end_marker)

        new_content = content[:start_idx] + new_block + content[end_idx:]

        with open(readme_file, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return True

    def check_readme(self, readme_path: str = "README.md", stats_file: str = "stats.json") -> Tuple[bool, Optional[str]]:
        """Check if README's marked block matches current stats.json.

        Returns:
            (bool, error_msg_tuple): bool is True if match, False if mismatch/no-markers
                                    error_msg_tuple is None for success/drift, or (error_type, message)
                                    for MISSING/UNREADABLE (fail-closed semantics)

        Fail-closed: if stats.json is MISSING or UNREADABLE, returns False with error info.
        Never writes stats.json (--regenerate is the only mode that writes).
        """
        # Check if stats.json is readable (fail-closed if not)
        load_error = self.get_stats_load_error(stats_file)
        if load_error is not None:
            # Return False with error info (will be handled by CLI to exit 1 with stderr)
            error_message = f"ERROR: stats.json {load_error.lower()}"
            if load_error == "MISSING":
                error_message = f"MISSING: stats.json not found — run --regenerate"
            elif load_error == "UNREADABLE":
                error_message = f"UNREADABLE: stats.json is corrupted or unreadable"
            return (False, (load_error, error_message))

        # Load stats from file (should succeed since we just checked it)
        stats_dict = self.load_stats(stats_file)
        if stats_dict is None:
            # Shouldn't reach here (we just verified it loads), but fail-closed
            return (False, ("UNREADABLE", "UNREADABLE: stats.json could not be loaded"))

        readme_file = Path(readme_path)
        if not readme_file.exists():
            return (False, None)

        with open(readme_file, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # Look for markers
        start_marker = "<!-- STATS:START -->"
        end_marker = "<!-- STATS:END -->"

        if start_marker not in content or end_marker not in content:
            # Markers not found, treat as no-op (return True since nothing to check)
            return (True, None)

        # Extract current block
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker) + len(end_marker)
        current_block = content[start_idx:end_idx]

        # Generate expected block
        expected_block = self.markdown_from_dict(stats_dict)

        # Compare: return (True, None) if match, (False, None) if drift
        matches = current_block.strip() == expected_block.strip()
        return (matches, None)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root (default: current directory)"
    )
    parser.add_argument(
        "--data-file",
        help="Path to docs/self-stats-data.json (auto-detected if not specified)"
    )
    parser.add_argument(
        "--stats-file",
        default="stats.json",
        help="Path to stats.json (default: stats.json in repo root)"
    )
    parser.add_argument(
        "--readme",
        default="README.md",
        help="Path to README.md (default: README.md in repo root)"
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--markdown",
        action="store_true",
        help="Output markdown block with START/END markers"
    )
    mode_group.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON"
    )
    mode_group.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenerate stats.json from live git state"
    )
    mode_group.add_argument(
        "--update-readme",
        action="store_true",
        help="Update README.md between <!-- STATS:START/END --> markers with stats from stats.json"
    )
    mode_group.add_argument(
        "--check",
        action="store_true",
        help="Check if README's marked block matches current stats.json (exit 0=match, 1=mismatch)"
    )

    args = parser.parse_args()

    counter = StatsCounter(repo_root=args.repo, data_file=args.data_file)

    # Use UTF-8 for output to handle emojis
    import io
    if hasattr(sys.stdout, 'buffer'):
        out = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    else:
        out = sys.stdout

    if args.regenerate:
        # Resolve paths relative to repo root
        stats_file = Path(args.repo) / args.stats_file if not Path(args.stats_file).is_absolute() else args.stats_file
        counter.save_stats(str(stats_file))
        out.write(f"Regenerated {stats_file}\n")
        out.flush()
        return 0

    if args.update_readme:
        # Resolve paths relative to repo root
        readme_path = Path(args.repo) / args.readme if not Path(args.readme).is_absolute() else args.readme
        stats_file = Path(args.repo) / args.stats_file if not Path(args.stats_file).is_absolute() else args.stats_file

        if counter.update_readme(str(readme_path), str(stats_file)):
            out.write(f"Updated {readme_path}\n")
        else:
            out.write(f"No markers found in {readme_path} or markers not recognized (gracefully skipped)\n")
        out.flush()
        return 0

    if args.check:
        # Resolve paths relative to repo root
        readme_path = Path(args.repo) / args.readme if not Path(args.readme).is_absolute() else args.readme
        stats_file = Path(args.repo) / args.stats_file if not Path(args.stats_file).is_absolute() else args.stats_file

        matches, error_info = counter.check_readme(str(readme_path), str(stats_file))

        # Handle fail-closed errors (MISSING or UNREADABLE)
        if error_info is not None:
            error_type, error_message = error_info
            sys.stderr.write(f"{error_message}\n")
            sys.stderr.flush()
            sys.exit(1)

        # Internal-consistency + freshness validation (fail before README compare).
        stats_dict = counter.load_stats(str(stats_file))
        if stats_dict is not None:
            integrity_errors = validate_stats_integrity(stats_dict, repo_root=args.repo)
            if integrity_errors:
                for err in integrity_errors:
                    sys.stderr.write(f"INTEGRITY: {err}\n")
                sys.stderr.flush()
                sys.exit(1)

        # Handle match/drift
        if matches:
            out.write(f"OK: {readme_path} matches {stats_file}\n")
            out.flush()
            sys.exit(0)
        else:
            out.write(f"DRIFT: {readme_path} does not match {stats_file}\n")
            out.flush()
            sys.exit(1)

    if args.markdown:
        out.write(counter.markdown())
    elif args.json:
        out.write(counter.json())
    else:
        out.write(counter.table())
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
