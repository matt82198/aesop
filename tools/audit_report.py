#!/usr/bin/env python3
"""
Deterministic audit report generator — aggregates machine-generated audit outputs.
INDEX: Deterministic markdown audit report aggregator (defect_escape, mutation results, lint/drift findings, ledger verdict rates); --out/--strict/--json inputs from machine outputs only

Assembles findings from multiple tools into a single dated markdown audit report:
  - defect_escape.py telemetry (code quality metrics)
  - mutation_test.py results (test quality gaps)
  - claudemd_lint.py findings (documentation integrity)
  - OUTCOMES-LEDGER.md verdict rates (fleet reliability)

All sources are optional by default (graceful degradation). Use --strict to require all.
Output is deterministic (no LLM calls, pure aggregation).

CLI:
  python tools/audit_report.py [--defect-escape=<json>] [--mutation-test=<json>]
                               [--claudemd-lint=<json>] [--ledger=<path>]
                               [--out=<path>] [--strict]

Exit: 0=success, 1=error (missing sources in --strict mode, read errors, etc.)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def load_json_file(path: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Load a JSON file.

    Args:
        path: Path to JSON file or None

    Returns:
        Tuple of (data, error_msg). If path is None, returns (None, None).
        On error, returns (None, error_msg).
    """
    if path is None:
        return None, None

    try:
        # Use utf-8-sig to handle BOM on Windows
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data, None
    except FileNotFoundError:
        return None, f"File not found: {path}"
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON in {path}: {e}"
    except IOError as e:
        return None, f"Failed to read {path}: {e}"


def load_ledger_file(path: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Load a ledger markdown file.

    Args:
        path: Path to ledger file or None

    Returns:
        Tuple of (content, error_msg). If path is None, returns (None, None).
    """
    if path is None:
        return None, None

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return content, None
    except FileNotFoundError:
        return None, f"File not found: {path}"
    except IOError as e:
        return None, f"Failed to read {path}: {e}"


def parse_ledger_verdicts(ledger_content: str) -> Dict[str, int]:
    """Parse OUTCOMES-LEDGER.md and count verdicts.

    Args:
        ledger_content: Markdown table content

    Returns:
        Dict mapping verdict string (OK, FAILED, EMPTY, HUNG) to count.
    """
    verdicts = {"OK": 0, "FAILED": 0, "EMPTY": 0, "HUNG": 0}

    # Skip header lines (first 2)
    lines = ledger_content.strip().split("\n")[2:]

    for line in lines:
        # Parse markdown table row: | col1 | col2 | ... |
        if not line.startswith("|"):
            continue

        cells = [cell.strip() for cell in line.split("|")]
        # Skip empty cells at start/end (from split)
        cells = [c for c in cells if c]

        # Verdict is typically at position 6 (0-indexed): ISO, agent_type, model, duration, tokens_in, tokens_out, verdict
        if len(cells) >= 7:
            verdict = cells[6]
            if verdict in verdicts:
                verdicts[verdict] += 1

    return verdicts


def format_defect_escape_section(data: Optional[Dict[str, Any]]) -> str:
    """Format defect escape telemetry section."""
    if data is None:
        return "## Defect Escape Telemetry\n\n*Not available*\n\n"

    feature = data.get("feature_commits", 0)
    fixforward = data.get("fixforward_commits", 0)
    rate = data.get("fixforward_rate", 0)
    first_try = data.get("first_try_estimate")
    window = data.get("window", {})
    since = window.get("since", "unknown")

    section = f"""## Defect Escape Telemetry

**Window:** {since}

| Metric | Value |
|--------|-------|
| Feature commits | {feature} |
| Fix-forward commits | {fixforward} |
| Fix-forward rate | {rate:.2%} |
| First-try estimate | {first_try if first_try is not None else "N/A"}{f" ({first_try:.2%})" if first_try is not None else ""} |

"""
    return section


def format_mutation_test_section(data: Optional[Dict[str, Any]]) -> str:
    """Format mutation testing section."""
    if data is None:
        return "## Mutation Testing\n\n*Not available*\n\n"

    killed = data.get("killed", 0)
    survived = data.get("survived", 0)
    total = killed + survived
    survival_rate = (survived / total * 100) if total > 0 else 0

    section = f"""## Mutation Testing

| Metric | Value |
|--------|-------|
| Mutations killed | {killed} |
| Mutations survived | {survived} |
| Total mutations | {total} |
| Survival rate (test gaps) | {survival_rate:.1f}% |

"""

    # List survived mutations if present
    mutations = data.get("mutations", [])
    if mutations:
        section += "### Survived Mutations (Test Gaps)\n\n"
        for mut in mutations:
            line = mut.get("line", "?")
            orig = mut.get("original", "?")
            mutated = mut.get("mutated", "?")
            section += f"- {line}: `{orig}` -> `{mutated}`\n"
        section += "\n"

    return section


def format_claudemd_lint_section(data: Optional[Dict[str, Any]]) -> str:
    """Format CLAUDE.md lint findings section."""
    if data is None:
        return "## CLAUDE.md Lint\n\n*Not available*\n\n"

    count = data.get("count", 0)
    section = f"""## CLAUDE.md Lint

**Findings:** {count}

"""

    findings = data.get("findings", [])
    if findings:
        for finding in findings:
            ftype = finding.get("type", "unknown")
            msg = finding.get("message", "")
            line = finding.get("line", "?")
            section += f"- [{ftype}] {msg} (line {line})\n"
        section += "\n"
    else:
        section += "*No issues found*\n\n"

    return section


def format_ledger_section(ledger_content: Optional[str]) -> str:
    """Format fleet ledger summary section."""
    if ledger_content is None:
        return "## Fleet Ledger Summary\n\n*Not available*\n\n"

    verdicts = parse_ledger_verdicts(ledger_content)
    section = """## Fleet Ledger Summary

| Verdict | Count |
|---------|-------|
"""

    total = sum(verdicts.values())
    for verdict in ["OK", "FAILED", "EMPTY", "HUNG"]:
        count = verdicts.get(verdict, 0)
        section += f"| {verdict} | {count} |\n"

    if total > 0:
        ok_rate = verdicts["OK"] / total * 100
        section += f"\n**Success rate:** {ok_rate:.1f}%\n\n"
    else:
        section += "\n*No ledger entries*\n\n"

    return section


def generate_report(
    defect_escape_data: Optional[Dict[str, Any]],
    mutation_test_data: Optional[Dict[str, Any]],
    claudemd_lint_data: Optional[Dict[str, Any]],
    ledger_content: Optional[str],
    strict: bool = False,
    timestamp: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Generate audit report from aggregated data.

    Args:
        defect_escape_data: defect_escape.py JSON
        mutation_test_data: mutation_test.py JSON
        claudemd_lint_data: claudemd_lint.py JSON
        ledger_content: OUTCOMES-LEDGER.md content
        strict: If True, fail if any source is missing
        timestamp: ISO timestamp for report (default: current UTC time)

    Returns:
        Tuple of (report_markdown, error_msg). On success, error_msg is None.
    """
    # In strict mode, all sources must be present
    if strict:
        missing = []
        if defect_escape_data is None:
            missing.append("defect-escape")
        if mutation_test_data is None:
            missing.append("mutation-test")
        if claudemd_lint_data is None:
            missing.append("claudemd-lint")
        if ledger_content is None:
            missing.append("ledger")

        if missing:
            return "", f"Strict mode: missing sources: {', '.join(missing)}"

    # Build report
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report = f"""# Audit Report

**Generated:** {timestamp}

"""

    # Add each section
    report += format_defect_escape_section(defect_escape_data)
    report += format_mutation_test_section(mutation_test_data)
    report += format_claudemd_lint_section(claudemd_lint_data)
    report += format_ledger_section(ledger_content)

    # Footer
    report += f"""---

*Report generated by audit_report.py (deterministic, no external LLM calls)*
"""

    return report, None


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate deterministic audit report from machine outputs"
    )
    parser.add_argument(
        "--defect-escape",
        type=str,
        default=None,
        help="Path to defect_escape.py JSON output",
    )
    parser.add_argument(
        "--mutation-test",
        type=str,
        default=None,
        help="Path to mutation_test.py JSON output",
    )
    parser.add_argument(
        "--claudemd-lint",
        type=str,
        default=None,
        help="Path to claudemd_lint.py JSON output",
    )
    parser.add_argument(
        "--ledger",
        type=str,
        default=None,
        help="Path to OUTCOMES-LEDGER.md file",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Write report to file (default: stdout)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if any source is missing",
    )
    parser.add_argument(
        "--timestamp",
        type=str,
        default=None,
        help="ISO timestamp for report (default: current UTC time)",
    )

    args = parser.parse_args()

    # Load all sources
    defect_escape_data, defect_err = load_json_file(args.defect_escape)
    mutation_test_data, mutation_err = load_json_file(args.mutation_test)
    claudemd_lint_data, claudemd_err = load_json_file(args.claudemd_lint)
    ledger_content, ledger_err = load_ledger_file(args.ledger)

    # Check for load errors
    errors = []
    if defect_err:
        errors.append(f"Defect escape: {defect_err}")
    if mutation_err:
        errors.append(f"Mutation test: {mutation_err}")
    if claudemd_err:
        errors.append(f"CLAUDE.md lint: {claudemd_err}")
    if ledger_err:
        errors.append(f"Ledger: {ledger_err}")

    # In non-strict mode, print warnings but continue
    if errors and not args.strict:
        for error in errors:
            print(f"Warning: {error}", file=sys.stderr)
    elif errors and args.strict:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    # Generate report
    report, err = generate_report(
        defect_escape_data,
        mutation_test_data,
        claudemd_lint_data,
        ledger_content,
        strict=args.strict,
        timestamp=args.timestamp,
    )

    if err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    # Output report
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(report)
        except IOError as e:
            print(f"Error writing to {args.out}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
