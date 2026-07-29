#!/usr/bin/env python3
"""
Wave backlog risk analyzer — assess risk for backlog items pre-wave.

Analyzes tracker backlog items and correlates with git history to compute
per-item risk assessments. Uses deterministic heuristics only:
  - Past failure counts (fix-forward commits by domain)
  - Repair frequency for similar-file patterns
  - Item size proxies

Output: JSON with {slug, risk_level, estimated_retries, justification}
  - risk_level: high/medium/low/unknown
  - estimated_retries: integer or null
  - justification: human-readable explanation

Exit: always 0 (warn-level only; never blocks preflight)

Usage:
  python tools/wave_backlog_analyzer.py [--root REPO_ROOT] [--state-root STATE_ROOT] [--json]

Arguments:
  --root REPO_ROOT: repository root directory (default: cwd)
  --state-root STATE_ROOT: state directory (default: REPO_ROOT/state or ./state)
  --json: output in JSON format (default: text)

Environment:
  AESOP_STATE_ROOT: state dir (takes precedence over --state-root argument)
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir


def load_config(root_dir=None):
    """Load aesop.config.json from root, return dict (or {} if absent/bad)."""
    if root_dir is None:
        root_dir = Path.cwd()
    else:
        root_dir = Path(root_dir)

    config_file = root_dir / "aesop.config.json"
    if not config_file.exists():
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_state_dir(root_dir=None, config=None):
    """Resolve state dir: AESOP_STATE_ROOT env > config state_root > ./state."""
    if os.environ.get("AESOP_STATE_ROOT"):
        return Path(os.environ["AESOP_STATE_ROOT"])

    if root_dir is None:
        root_dir = Path.cwd()
    else:
        root_dir = Path(root_dir)

    if config is None:
        config = load_config(root_dir)

    state_root = config.get("state_root") if isinstance(config, dict) else None
    if state_root:
        p = Path(state_root).expanduser()
        if not p.is_absolute():
            p = root_dir / p
        return p

    return root_dir / "state"


def load_tracker_json(state_dir):
    """Load state/tracker.json.

    Returns:
        list of items, or empty list if not found or invalid
    """
    tracker_path = Path(state_dir) / "tracker.json"
    if not tracker_path.exists():
        return []

    try:
        with open(tracker_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "items" in data:
                items = data["items"]
                if isinstance(items, list):
                    return items
    except Exception:
        pass

    return []


def extract_domain_from_item(item):
    """Extract domain hint from item (title, description, slug).

    Returns:
        str: domain name or None
    """
    if not isinstance(item, dict):
        return None

    # Try description or title first (more reliable)
    for field in ["description", "title"]:
        text = item.get(field, "")
        if isinstance(text, str) and text:
            # Look for path patterns: tools/, state_store/, ui/, etc.
            match = re.search(r"\b([a-z_]+)/", text)
            if match:
                return match.group(1)
            # Look for domain keywords (with flexible hyphen/underscore matching)
            for domain in ["tools", "state_store", "state-store", "ui", "daemons", "monitor", "mcp", "hooks", "driver", "bin"]:
                if domain in text.lower():
                    return domain.replace("-", "_")

    # Try to extract from slug: tools-001 -> tools, state-store-fix -> state_store
    # Known domain patterns (avoid false positives)
    known_domains = ["tools", "state_store", "ui", "daemons", "monitor", "mcp", "hooks", "driver", "bin", "state"]
    slug = item.get("slug", "")
    if slug:
        slug_lower = slug.lower()
        for domain in known_domains:
            if slug_lower.startswith(domain):
                return domain
        # Fallback: extract prefix before any hyphen and normalize
        match = re.match(r"^([a-z_]+)", slug)
        if match:
            return match.group(1)

    return None


def get_commits_since(repo_path, days=30):
    """Get all commits in the last N days.

    Args:
        repo_path: path to git repo
        days: number of days to look back

    Returns:
        list of (commit_hash, subject) tuples
    """
    try:
        since_date = (datetime.now() - timedelta(days=days)).isoformat()
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", "--all", f"--since={since_date}", "--format=%H%n%s%n--END--"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        commits = []
        lines = result.stdout.split("\n")
        i = 0
        while i < len(lines):
            if i + 1 < len(lines) and lines[i + 1] != "--END--":
                commit_hash = lines[i]
                subject = lines[i + 1]
                commits.append((commit_hash, subject))
                i += 3  # hash, subject, --END--
            else:
                i += 1
        return commits
    except Exception:
        return []


def get_files_changed_in_commit(repo_path, commit_hash):
    """Get list of files changed in a commit.

    Returns:
        list of file paths
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "show", "--name-only", "--format=", commit_hash],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.split("\n") if f.strip()]
    except Exception:
        pass
    return []


def is_fixforward_commit(subject):
    """Check if commit subject matches fix-forward pattern."""
    patterns = [
        r"fix-forward",
        r"hotfix",
        r"fix\s*\(\s*ci\s*\)",
        r"repair",
    ]
    combined = "|".join(f"({p})" for p in patterns)
    return bool(re.search(combined, subject, re.IGNORECASE))


def analyze_domain_history(repo_path, domain, commits, lookback_days=30):
    """Analyze commit history for a domain.

    Returns:
        dict: {
            feature_commits: int,
            fixforward_commits: int,
            repair_frequency: float (0.0-1.0),
            related_files: set of related file paths
        }
    """
    domain_pattern = re.compile(rf"\b{re.escape(domain)}\b")

    feature_commits = 0
    fixforward_commits = 0
    related_files = set()

    for commit_hash, subject in commits:
        if domain_pattern.search(subject) or domain_pattern.search(commit_hash):
            if is_fixforward_commit(subject):
                fixforward_commits += 1
            else:
                feature_commits += 1

            # Track files changed in this commit
            files = get_files_changed_in_commit(repo_path, commit_hash)
            for f in files:
                if f.startswith(domain + "/") or domain in f:
                    related_files.add(f)

    total_domain_commits = feature_commits + fixforward_commits
    repair_frequency = (
        fixforward_commits / feature_commits
        if feature_commits > 0
        else (0.5 if fixforward_commits > 0 else 0.0)
    )

    return {
        "feature_commits": feature_commits,
        "fixforward_commits": fixforward_commits,
        "repair_frequency": repair_frequency,
        "related_files": related_files,
        "total_commits": total_domain_commits,
    }


def compute_risk_level(domain, history, item):
    """Compute risk level based on history.

    Returns:
        str: high/medium/low/unknown
    """
    if history["total_commits"] == 0:
        return "unknown"

    # Heuristics:
    # - repair_frequency > 0.5 = high risk (more fixes than features)
    # - 0.3-0.5 = medium risk
    # - < 0.3 = low risk
    # - feature_commits >= 5 increases risk slightly

    repair_freq = history["repair_frequency"]
    feature_count = history["feature_commits"]

    if repair_freq > 0.5:
        return "high"
    elif repair_freq > 0.3 or feature_count > 5:
        return "medium"
    else:
        return "low"


def compute_estimated_retries(history):
    """Compute estimated retries based on repair frequency.

    Returns:
        int or None
    """
    if history["total_commits"] == 0:
        return None

    # Heuristics:
    # - No history: None
    # - repair_frequency * max(feature_commits / 2, 1) = estimated retries
    freq = history["repair_frequency"]
    features = history["feature_commits"]

    if features == 0:
        return None if freq == 0 else 1

    estimated = round(freq * max(features / 2, 1))
    return max(0, estimated)


def build_justification(domain, item, history):
    """Build a human-readable justification for the risk assessment.

    Returns:
        str
    """
    if history["total_commits"] == 0:
        return "No history found for this domain in the last 30 days."

    parts = []

    # Mention domain
    parts.append(f"Domain: {domain}")

    # Mention commit counts
    features = history["feature_commits"]
    fixes = history["fixforward_commits"]
    parts.append(f"Recent commits: {features} features, {fixes} fixes")

    # Mention repair frequency
    freq = history["repair_frequency"]
    if freq > 0.5:
        parts.append(f"High repair frequency ({freq:.1%})")
    elif freq > 0.3:
        parts.append(f"Moderate repair frequency ({freq:.1%})")
    else:
        parts.append(f"Low repair frequency ({freq:.1%})")

    # Mention related files
    if history["related_files"]:
        files_sample = list(history["related_files"])[:2]
        parts.append(f"Related files: {', '.join(files_sample)}")

    return "; ".join(parts)


def analyze_item(item, domain, repo_path, commits):
    """Analyze a single tracker item.

    Returns:
        dict: {slug, risk_level, estimated_retries, justification}
    """
    if not isinstance(item, dict):
        return None

    slug = item.get("slug")
    if not slug:
        return None

    # Analyze domain history
    if domain:
        history = analyze_domain_history(repo_path, domain, commits)
    else:
        history = {
            "feature_commits": 0,
            "fixforward_commits": 0,
            "repair_frequency": 0.0,
            "related_files": set(),
            "total_commits": 0,
        }

    # Compute risk and retries
    risk_level = compute_risk_level(domain or "unknown", history, item)
    estimated_retries = compute_estimated_retries(history)

    # Build justification
    justification = build_justification(domain or "unknown", item, history)

    return {
        "slug": slug,
        "risk_level": risk_level,
        "estimated_retries": estimated_retries,
        "justification": justification,
    }


def run_analysis(root_dir=None, state_dir=None, config=None):
    """Run full backlog analysis.

    Returns:
        dict: {items: [analysis results]}
    """
    if root_dir is None:
        root_dir = Path.cwd()
    else:
        root_dir = Path(root_dir)

    if config is None:
        config = load_config(root_dir)

    if state_dir is None:
        state_dir = resolve_state_dir(root_dir, config)
    else:
        state_dir = Path(state_dir)

    # Load tracker items
    items = load_tracker_json(state_dir)

    # Get recent commits
    commits = get_commits_since(root_dir, days=30)

    # Analyze each item
    results = []
    for item in items:
        # Extract domain hint
        domain = extract_domain_from_item(item)

        # Analyze item
        analysis = analyze_item(item, domain, root_dir, commits)
        if analysis:
            results.append(analysis)

    return {"items": results}


def main(argv=None):
    """CLI entry point."""
    argv = sys.argv[1:] if argv is None else argv

    root_dir = None
    state_dir = None
    output_format = "text"

    # Parse arguments
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print("Usage: wave_backlog_analyzer.py [--root PATH] [--state-root PATH] [--json]")
            return 0
        elif arg == "--root":
            i += 1
            if i < len(argv):
                root_dir = argv[i]
            i += 1
        elif arg.startswith("--root="):
            root_dir = arg[len("--root="):]
            i += 1
        elif arg == "--state-root":
            i += 1
            if i < len(argv):
                state_dir = argv[i]
            i += 1
        elif arg.startswith("--state-root="):
            state_dir = arg[len("--state-root="):]
            i += 1
        elif arg == "--json":
            output_format = "json"
            i += 1
        else:
            print(f"Unknown argument: {arg}", file=sys.stderr)
            return 2  # Unrecognized flag: fail closed (not the item-level warn path)

    if root_dir is None:
        root_dir = Path.cwd()
    else:
        root_dir = Path(root_dir)

    config = load_config(root_dir)
    if state_dir is None:
        state_dir = resolve_state_dir(root_dir, config)
    else:
        state_dir = Path(state_dir)

    result = run_analysis(root_dir, state_dir, config)

    if output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        # Text format
        print("Backlog Risk Analysis:")
        if not result["items"]:
            print("  (no items to analyze)")
        else:
            for item in result["items"]:
                print(f"\n  {item['slug']}: {item['risk_level'].upper()}")
                if item["estimated_retries"] is not None:
                    print(f"    Estimated retries: {item['estimated_retries']}")
                print(f"    {item['justification']}")

    return 0  # Always exit 0 (warn-level)


if __name__ == "__main__":
    sys.exit(main())
