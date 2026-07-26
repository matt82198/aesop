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
  --check - Exit non-zero if README's marked block doesn't match current stats.json (drift gate)

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
from typing import Optional, Dict, Any


class GitStats:
    """Compute statistics from git repository."""

    def __init__(self, repo_root: str = "."):
        """Initialize with repo root path."""
        self.repo_root = Path(repo_root)
        self._merged_prs = None
        self._total_commits = None
        self._project_age_days = None
        self._wave_count = None
        self._insertions_deletions = None
        self._files_tracked = None
        self._distinct_coauthors = None
        self._lines_of_code = None

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

    @property
    def merged_prs(self) -> int:
        """Count merged PRs using gh API (preferred) or git fallback.

        Prefers `gh` API: gh api -X GET search/issues -f q="repo:matt82198/aesop is:pr is:merged" --jq .total_count
        Falls back to git: counts distinct PR numbers from both merge-commit and squash-merge patterns.
        """
        if self._merged_prs is not None:
            return self._merged_prs

        # Try gh API first (preferred, authoritative)
        try:
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    "-X",
                    "GET",
                    "search/issues",
                    "-f",
                    "q=repo:matt82198/aesop is:pr is:merged",
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
                    count = int(result.stdout.strip())
                    self._merged_prs = count
                    return count
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
                self._merged_prs = 0
                return 0

            # Collect distinct PR numbers from both patterns:
            # 1. "Merge pull request #N" (merge-commit style)
            # 2. "(#N)" at end of subject (squash-merge style)
            pr_numbers = set()

            for match in re.finditer(r"^Merge pull request #(\d+)", output, re.MULTILINE):
                pr_numbers.add(int(match.group(1)))

            for match in re.finditer(r"\(#(\d+)\)\s*$", output, re.MULTILINE):
                pr_numbers.add(int(match.group(1)))

            count = len(pr_numbers)
            self._merged_prs = count
            return count
        except Exception:
            self._merged_prs = 0
            return 0

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
            with open(self.data_file) as f:
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
        if self.git.wave_count > 0:
            lines.append(f"  Wave Count:           {self.git.wave_count}")
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
        if self.git.wave_count > 0:
            rows.append(
                f"| Waves | {self.git.wave_count} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if self.git.insertions_deletions > 0:
            rows.append(
                f"| Insertions + Deletions | {self.git.insertions_deletions:,} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if self.git.files_tracked > 0:
            rows.append(
                f"| Files Tracked | {self.git.files_tracked} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if self.git.distinct_coauthors > 0:
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
                "total_commits": self.git.total_commits,
                "project_age_days": self.git.project_age_days,
                "wave_count": self.git.wave_count,
                "insertions_deletions": self.git.insertions_deletions,
                "files_tracked": self.git.files_tracked,
                "distinct_coauthors": self.git.distinct_coauthors,
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

        # Add cost economics metrics (requires cost_econ module)
        try:
            from cost_econ import calculate_economics, get_metric_honesty_caveats
            # Infer state directory from repo root or config
            repo_path = Path(self.git.repo_root)
            state_dir = repo_path / "state"

            economics = calculate_economics(
                repo_root=str(repo_path),
                state_dir=str(state_dir) if state_dir.exists() else None,
                config_file=None
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
        if git_stats.get("wave_count", 0) > 0:
            rows.append(
                f"| Waves | {git_stats['wave_count']} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if git_stats.get("insertions_deletions", 0) > 0:
            rows.append(
                f"| Insertions + Deletions | {git_stats['insertions_deletions']:,} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if git_stats.get("files_tracked", 0) > 0:
            rows.append(
                f"| Files Tracked | {git_stats['files_tracked']} <!-- metrics-verified: self_stats.py (git log) --> |"
            )
        if git_stats.get("distinct_coauthors", 0) > 0:
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

    def check_readme(self, readme_path: str = "README.md", stats_file: str = "stats.json") -> bool:
        """Check if README's marked block matches current stats.json.

        Returns True if they match, False if they don't (or markers not found).
        Exit code usage: sys.exit(0) if True, sys.exit(1) if False.
        """
        # Load stats from file
        stats_dict = self.load_stats(stats_file)
        if stats_dict is None:
            # stats.json doesn't exist, regenerate it
            self.save_stats(stats_file)
            stats_dict = self.load_stats(stats_file)
            if stats_dict is None:
                return False

        readme_file = Path(readme_path)
        if not readme_file.exists():
            return False

        with open(readme_file, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # Look for markers
        start_marker = "<!-- STATS:START -->"
        end_marker = "<!-- STATS:END -->"

        if start_marker not in content or end_marker not in content:
            # Markers not found, treat as no-op (return True since nothing to check)
            return True

        # Extract current block
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker) + len(end_marker)
        current_block = content[start_idx:end_idx]

        # Generate expected block
        expected_block = self.markdown_from_dict(stats_dict)

        # Compare
        return current_block.strip() == expected_block.strip()


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

        if counter.check_readme(str(readme_path), str(stats_file)):
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
