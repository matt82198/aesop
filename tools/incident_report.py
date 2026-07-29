#!/usr/bin/env python3
"""
Incident log generator: mines committed record for operational failures.

Parses git history (commit messages, BUILDLOG, CHANGELOG, PR titles) and
extracts failure events classified by type (fake-green, ci-drift, test-pollution,
flake, conflict, stall, gate-activation, doc-invented).

Generates docs/INCIDENTS.md: a deterministic, idempotent failure taxonomy table
with columns: class, what_happened, resolution, source_ref.

Usage:
  python tools/incident_report.py [--repo PATH]
  python tools/incident_report.py --regenerate [--repo PATH] [--output docs/INCIDENTS.md]
  python tools/incident_report.py --check [--repo PATH]

Output modes:
  default  - Human-readable table
  --regenerate - Regenerate docs/INCIDENTS.md from live git state
  --check - Exit 0 if docs/INCIDENTS.md matches current state, 1 if drift

All output is deterministic: stable ordering, no generated timestamps, idempotent regeneration.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple


# Incident class taxonomy
INCIDENT_CLASSES = {
    "fake-green": "Tests reported green but never ran or skipped real validation",
    "ci-drift": "CI workflow state out of sync (missing deps, env setup, tools)",
    "test-pollution": "Test config leaked between shards, state not isolated, mock pollution",
    "flake": "Test timing/race condition, deflake required, logical time or retry",
    "conflict": "Merge/rebase conflict, module shadowing, unintended override",
    "stall": "Agent/process hung or deadlocked, watchdog detected, restart required",
    "gate-activation": "Pre-push secret/verification gate caught an escape or bypass",
    "doc-invented": "Documentation made unverifiable claims, hallucinated counts or proofs",
}


class IncidentClassifier:
    """Classify incidents from commit subjects and bodies."""

    def __init__(self):
        """Initialize classifier with patterns."""
        self.patterns = {
            "fake-green": [
                r"green-never-ran",
                r"never-ran",
                r"actually execute.*playwright",
                r"browser-proofs.*actually.*run",
            ],
            "ci-drift": [
                r"fix\(ci\):.*pytest\|chromium\|workflow",
                r"post-#\d+ drift",
                r"fix\(workflow\):",
                r"missing.*pytest.*workflow",
            ],
            "test-pollution": [
                r"leak.*sys\.modules",
                r"shard isolation",
                r"test.*pollut",
            ],
            "flake": [
                r"deflake.*test",
                r"fix.*timing.*test",
                r"boundary.*test.*deflake",
            ],
            "conflict": [
                r"module shadowing",
                r"resolve.*conflict",
                r"Resolve conflicts:",
                r"restore.*original.*spec",
            ],
            "stall": [
                r"stall.*check",
                r"hung.*process",
                r"deadlock",
            ],
            "gate-activation": [
                r"secret.*gate",
                r"\-\-no-verify",
                r"\-\-admin",
                r"gates.*fired",
                r"verification.*bypass",
            ],
            "doc-invented": [
                r"invented precision",
                r"unverifiable",
                r"hallucinated",
                r"remove.*precision",
            ],
        }

    def classify_commit_subject(self, subject: str) -> str:
        """Classify incident from commit subject.

        Returns the class name (e.g., 'fake-green') or 'unknown'.
        """
        subject_lower = subject.lower()

        # Check each class in order of specificity
        for class_name, patterns in self.patterns.items():
            for pattern in patterns:
                if re.search(pattern, subject_lower, re.IGNORECASE):
                    return class_name

        # Default to unknown if no pattern matches
        return "unknown"

    def classify_from_body(self, body: str) -> Optional[str]:
        """Classify incident from full commit body if subject didn't match."""
        body_lower = body.lower()

        for class_name, patterns in self.patterns.items():
            for pattern in patterns:
                if re.search(pattern, body_lower, re.IGNORECASE):
                    return class_name

        return None


class IncidentParser:
    """Parse incidents from git history."""

    def __init__(self, repo_root: str = "."):
        """Initialize with repo root."""
        self.repo_root = Path(repo_root)
        self.classifier = IncidentClassifier()

    def _run_git(self, *args, check=True) -> str:
        """Run git command, return stdout."""
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

    def _extract_source_ref(self, subject: str, hash_short: str) -> str:
        """Extract source reference (PR #NNN or commit hash) from subject."""
        # Look for PR number in subject
        match = re.search(r'#(\d+)', subject)
        if match:
            return f"PR #{match.group(1)}"

        # Fallback to commit hash
        return f"commit {hash_short}"

    def _extract_what_happened(self, subject: str, body: str) -> str:
        """Extract one-line summary of what happened."""
        # Truncate subject to 70 chars, remove prefix
        summary = subject
        # Remove common prefixes
        summary = re.sub(r'^(fix|feat|harden|ci|chore|docs?)\([^)]*\):\s*', '', summary)
        summary = re.sub(r'\s*\(#\d+\)\s*$', '', summary)  # Remove PR number
        summary = re.sub(r'\s*#\d+\s*$', '', summary)  # Remove trailing #NNN

        if len(summary) > 70:
            summary = summary[:67] + "..."

        return summary

    def _extract_resolution(self, body: str) -> str:
        """Extract resolution from commit body."""
        lines = body.split('\n')

        # Look for lines that describe the fix
        resolution_lines = []
        for line in lines:
            line = line.strip()
            if line and len(line) > 10:
                # Skip co-author lines
                if 'Co-Authored-By' in line or 'Authored-By' in line:
                    continue
                # Take first substantial line as resolution
                if len(resolution_lines) == 0 and line:
                    resolution_lines.append(line)

        if resolution_lines:
            resolution = resolution_lines[0]
            if len(resolution) > 70:
                resolution = resolution[:67] + "..."
            return resolution

        # Fallback: extract from subject
        return self._extract_what_happened(body[:100], "")

    def find_all_incidents(self, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """Find all incidents in git history.

        Returns list of dicts with keys: hash, class, what_happened, resolution, source_ref, date
        Ordered by date (newest first), then by hash for determinism.
        """
        # Get all commits with subject and first line of body
        try:
            output = self._run_git(
                "log",
                "--format=%H|%h|%s|%b|%cI",  # full hash | short hash | subject | body | date
                "--all",
                check=False,
            )
        except Exception:
            return []

        if not output:
            return []

        incidents = []
        seen_hashes = set()

        for line in output.split('\n'):
            if not line.strip():
                continue

            parts = line.split('|', 4)
            if len(parts) < 4:
                continue

            full_hash, short_hash, subject, body, date_iso = (
                parts[0], parts[1], parts[2], parts[3],
                parts[4] if len(parts) > 4 else ""
            )

            # Skip duplicates
            if full_hash in seen_hashes:
                continue
            seen_hashes.add(full_hash)

            # Classify
            incident_class = self.classifier.classify_commit_subject(subject)
            if incident_class == "unknown" or incident_class is None:
                incident_class = self.classifier.classify_from_body(body)

            # Skip if still unknown or None
            if incident_class == "unknown" or incident_class is None:
                continue

            # Extract metadata
            what_happened = self._extract_what_happened(subject, body)
            resolution = self._extract_resolution(body)
            source_ref = self._extract_source_ref(subject, short_hash)

            # Parse date for sorting
            try:
                date_obj = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                date_obj = datetime.now(timezone.utc)

            incidents.append({
                'hash': short_hash,
                'full_hash': full_hash,
                'class': incident_class,
                'what_happened': what_happened,
                'resolution': resolution,
                'source_ref': source_ref,
                'date': date_iso,
                'date_obj': date_obj,
            })

        # Sort by date (newest first), then by hash for determinism
        incidents.sort(
            key=lambda x: (-x['date_obj'].timestamp(), x['hash']),
            reverse=False
        )
        incidents.reverse()  # Newest first

        # Optionally limit
        if limit:
            incidents = incidents[:limit]

        # Remove internal date_obj field for output
        for incident in incidents:
            del incident['date_obj']

        return incidents


class IncidentMarkdown:
    """Generate Markdown output for incidents."""

    def generate_table(self, incidents: List[Dict[str, str]]) -> str:
        """Generate Markdown table from incidents."""
        lines = []
        lines.append("")
        lines.append("# Incidents")
        lines.append("")
        lines.append("Operational failures tracked by class: detection, resolution, and source reference.")
        lines.append("")

        if not incidents:
            lines.append("*(No incidents recorded)*")
            lines.append("")
            return "\n".join(lines)

        # Generate summary counts by class
        class_counts = {}
        for incident in incidents:
            class_name = incident.get('class') or 'unknown'
            if class_name == 'unknown' or class_name is None:
                continue
            class_counts[class_name] = class_counts.get(class_name, 0) + 1

        if class_counts:
            lines.append("**Summary**")
            lines.append("")
            for class_name in sorted(class_counts.keys()):
                count = class_counts[class_name]
                description = INCIDENT_CLASSES.get(class_name, "Unknown")
                lines.append(f"- **{class_name}** ({count}): {description}")
            lines.append("")

        # Generate table
        lines.append("| Class | What Happened | Resolution | Source |")
        lines.append("| --- | --- | --- | --- |")

        for incident in incidents:
            class_name = incident['class']
            what_happened = incident['what_happened']
            resolution = incident['resolution']
            source_ref = incident['source_ref']

            # Escape pipes in content
            what_happened = what_happened.replace('|', '\\|')
            resolution = resolution.replace('|', '\\|')

            lines.append(
                f"| {class_name} | {what_happened} | {resolution} | {source_ref} |"
            )

        lines.append("")

        # Add data-derived latest-entry timestamp
        if incidents:
            # Latest incident is first (newest first)
            latest_date = incidents[0].get('date', '')
            lines.append(f"<!-- Latest incident: {latest_date} -->")

        lines.append("")

        return "\n".join(lines)

    def generate_with_markers(self, incidents: List[Dict[str, str]]) -> str:
        """Generate Markdown with START/END markers for README insertion."""
        lines = []
        lines.append("<!-- INCIDENTS:START -->")
        lines.append("")
        lines.extend(self.generate_table(incidents).split('\n'))
        lines.append("<!-- INCIDENTS:END -->")
        lines.append("")

        return "\n".join(lines)


class IncidentValidator:
    """Validate incident data for secrets/safety."""

    @staticmethod
    def is_valid_git_hash(hash_str: str) -> bool:
        """Check if string is valid git short/long hash."""
        return bool(re.match(r'^[0-9a-f]{7,40}$', hash_str))

    @staticmethod
    def is_valid_source_ref(ref: str) -> bool:
        """Check if source ref is valid (PR #NNN or commit hash)."""
        if ref.startswith('PR #'):
            return bool(re.match(r'^PR #\d+$', ref))
        elif ref.startswith('commit '):
            hash_part = ref[7:]
            return IncidentValidator.is_valid_git_hash(hash_part)
        return False


class IncidentChecker:
    """Check if docs/INCIDENTS.md matches current git state."""

    def __init__(self, repo_root: str = "."):
        """Initialize checker."""
        self.repo_root = Path(repo_root)
        self.parser = IncidentParser(repo_root)
        self.markdown = IncidentMarkdown()

    def check_incidents_file(self, incidents_file: str = "docs/INCIDENTS.md") -> bool:
        """Check if INCIDENTS.md matches generated state.

        Returns True if they match, False if they don't.
        """
        incidents_path = self.repo_root / incidents_file
        if not incidents_path.exists():
            # File doesn't exist, need to regenerate
            return False

        # Read current file
        try:
            with open(incidents_path, 'r', encoding='utf-8', errors='replace') as f:
                current_content = f.read()
        except IOError:
            return False

        # Generate expected content
        incidents = self.parser.find_all_incidents()
        expected_content = self.markdown.generate_table(incidents)

        # Compare (normalize whitespace)
        current_normalized = '\n'.join(l.rstrip() for l in current_content.split('\n'))
        expected_normalized = '\n'.join(l.rstrip() for l in expected_content.split('\n'))

        return current_normalized.strip() == expected_normalized.strip()


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
        "--output",
        default="docs/INCIDENTS.md",
        help="Output file path (default: docs/INCIDENTS.md)"
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenerate docs/INCIDENTS.md from live git state"
    )
    mode_group.add_argument(
        "--check",
        action="store_true",
        help="Check if docs/INCIDENTS.md matches current state (exit 0=match, 1=drift)"
    )

    args = parser.parse_args()

    incident_parser = IncidentParser(repo_root=args.repo)
    markdown_gen = IncidentMarkdown()

    if args.regenerate:
        # Parse all incidents
        incidents = incident_parser.find_all_incidents()

        # Generate markdown
        markdown_output = markdown_gen.generate_table(incidents)

        # Write to file
        output_path = Path(args.repo) / args.output if not Path(args.output).is_absolute() else args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(markdown_output)

        print(f"Regenerated {output_path}")
        print(f"Total incidents: {len(incidents)}")
        print(f"Classes: {', '.join(sorted(set(i['class'] for i in incidents)))}")
        return 0

    if args.check:
        checker = IncidentChecker(repo_root=args.repo)
        if checker.check_incidents_file(args.output):
            print(f"OK: {args.output} matches current state")
            return 0
        else:
            print(f"DRIFT: {args.output} does not match current state")
            return 1

    # Default: print incidents
    incidents = incident_parser.find_all_incidents()
    markdown_output = markdown_gen.generate_table(incidents)
    print(markdown_output)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
