#!/usr/bin/env python3
"""Cost economics metrics for aesop.
INDEX: Cost economics metrics (cost-per-LOC, per-merged-PR, per-wave/backlog-item) from stats.json + fleet ledger; shares ui/cost.py pricing; honesty caveats documented in output

Computes unit cost metrics derived from git stats, fleet ledger tokens, and optional pricing.

Metrics:
  - cost_per_loc: total_tokens / lines_of_code (tokens per line of code written)
  - cost_per_merged_pr: total_tokens / merged_prs (tokens per shipped feature)
  - cost_per_wave: total_tokens / wave_count (tokens per development cycle)
  - unit_economics: cost-per-backlog-item (proxy: cost per PR or commit)
  - cost_estimates: optional dollar costs based on aesop.config.json pricing

Honesty caveats:
  - Does NOT include Fable main-thread tokens (not in fleet ledger, only in MCP cost tracking)
  - Assumes ledger reflects actual execution; missing/stale ledger = underestimated costs
  - LOC from git ls-files; doesn't account for deleted branches or archived code
  - Wave count from commit messages; depends on "wave-N" naming convention
  - Pricing estimates require aesop.config.json with pricing map; fallback to token counts

Usage:
  python cost_econ.py [--repo PATH] [--config PATH] [--json]

Returns JSON dict with economics metrics, suitable for stats.json integration.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Any

# Try to import config (may not exist in all environments)
try:
    import config
except ImportError:
    config = None


def _run_git(repo_root: str, *args) -> str:
    """Run git command in repo, return stdout."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return (result.stdout or "").strip()
    except FileNotFoundError:
        return ""


def _get_lines_of_code(repo_root: str) -> int:
    """Count total lines in tracked files (reuse from self_stats)."""
    try:
        output = _run_git(repo_root, "ls-files")
        if not output:
            return 0

        files = [f.strip() for f in output.split("\n") if f.strip()]
        total_lines = 0

        for file_path in files:
            try:
                file_full_path = Path(repo_root) / file_path
                if file_full_path.is_file():
                    with open(file_full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        total_lines += sum(1 for _ in f)
            except Exception:
                continue

        return total_lines
    except Exception:
        return 0


def _get_merged_prs(repo_root: str) -> int:
    """Count distinct merged PRs from commit subjects (fallback definition).

    Uses the SAME definition as tools/self_stats.py git fallback: the union of
    merge-commit ("Merge pull request #N") and squash-merge ("... (#N)") PR
    numbers, deduplicated. Kept identical so economics and the git block never
    disagree when both fall back to git.
    """
    try:
        output = _run_git(repo_root, "log", "--format=%s")
        if not output:
            return 0

        pr_numbers = set()
        for match in re.finditer(r"^Merge pull request #(\d+)", output, re.MULTILINE):
            pr_numbers.add(int(match.group(1)))
        for match in re.finditer(r"\(#(\d+)\)\s*$", output, re.MULTILINE):
            pr_numbers.add(int(match.group(1)))
        return len(pr_numbers)
    except Exception:
        return 0


def _get_wave_count(repo_root: str) -> int:
    """Count distinct waves from commit messages."""
    try:
        output = _run_git(repo_root, "log", "--format=%B")
        if output:
            waves = set()
            for match in re.finditer(r"wave[_-]?(\d+)", output, re.IGNORECASE):
                waves.add(int(match.group(1)))
            if waves:
                return len(waves)

        # Fallback: count release tags (v*)
        tags = _run_git(repo_root, "tag", "-l", "v*")
        tag_count = len([t for t in tags.split("\n") if t.strip()])
        return tag_count
    except Exception:
        return 0


def _get_total_tokens(state_dir: Path) -> int:
    """Parse ledger and return total tokens (input + output)."""
    ledger_file = state_dir / "ledger" / "OUTCOMES-LEDGER.md"

    if not ledger_file.exists():
        return 0

    try:
        content = ledger_file.read_text(encoding='utf-8')
    except Exception:
        return 0

    total_tokens = 0
    lines = content.strip().split('\n')

    for line in lines:
        line = line.strip()

        if not line or all(c in '|- ' for c in line):
            continue

        if not line.startswith('|') or not line.endswith('|'):
            continue

        parts = [p.strip() for p in line.split('|')]

        if len(parts) < 9:
            continue

        try:
            tokens_in_str = parts[5]
            tokens_out_str = parts[6]

            if 'token' in tokens_in_str.lower():  # Skip header
                continue

            tokens_in = int(tokens_in_str)
            tokens_out = int(tokens_out_str)

            total_tokens += tokens_in + tokens_out
        except (ValueError, IndexError):
            continue

    return total_tokens


def _load_pricing_config(config_file: Optional[str] = None) -> Optional[Dict]:
    """Load pricing map from aesop.config.json.

    Returns:
        dict or None: pricing map {model: {input_per_mtok: float, output_per_mtok: float}}
    """
    if config_file:
        config_path = Path(config_file)
    elif config:
        config_path = config.CONFIG_FILE
    else:
        # Try to find aesop.config.json in current directory or parent
        config_path = Path("aesop.config.json")
        if not config_path.exists():
            config_path = Path.home() / "aesop" / "aesop.config.json"

    if not config_path.exists():
        return None

    try:
        with open(config_path, encoding='utf-8') as f:
            config_data = json.load(f)
    except Exception:
        return None

    return config_data.get("pricing", None)


def _get_total_tokens_by_model(state_dir: Path) -> Dict[str, Dict[str, int]]:
    """Parse ledger and return tokens per model.

    Returns:
        dict: {model: {tokens_in: int, tokens_out: int, count: int}}
    """
    ledger_file = state_dir / "ledger" / "OUTCOMES-LEDGER.md"

    if not ledger_file.exists():
        return {}

    try:
        content = ledger_file.read_text(encoding='utf-8')
    except Exception:
        return {}

    model_tokens = {}
    lines = content.strip().split('\n')

    for line in lines:
        line = line.strip()

        if not line or all(c in '|- ' for c in line):
            continue

        if not line.startswith('|') or not line.endswith('|'):
            continue

        parts = [p.strip() for p in line.split('|')]

        if len(parts) < 9:
            continue

        try:
            model = parts[3]
            tokens_in_str = parts[5]
            tokens_out_str = parts[6]

            if 'model' in model.lower() or 'token' in tokens_in_str.lower():  # Skip header
                continue

            tokens_in = int(tokens_in_str)
            tokens_out = int(tokens_out_str)

            if model not in model_tokens:
                model_tokens[model] = {"tokens_in": 0, "tokens_out": 0, "count": 0}

            model_tokens[model]["tokens_in"] += tokens_in
            model_tokens[model]["tokens_out"] += tokens_out
            model_tokens[model]["count"] += 1
        except (ValueError, IndexError):
            continue

    return model_tokens


def calculate_economics(
    repo_root: str = ".",
    state_dir: Optional[str] = None,
    config_file: Optional[str] = None,
    merged_prs: Optional[int] = None,
    merged_prs_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Calculate cost economics metrics for a repository.

    Args:
        repo_root: Path to git repository
        state_dir: Path to aesop state directory (defaults to repo_root/state)
        config_file: Path to aesop.config.json for pricing (optional)
        merged_prs: Single-sourced merged-PR count from the caller (self_stats).
            When provided, it is used verbatim and never recomputed here, so the
            economics block can never contradict the git block. When None, this
            module falls back to the SAME git-log definition self_stats uses.
        merged_prs_source: Provenance of merged_prs ('gh-api' | 'git-log').
            Defaults to 'git-log' when the count is computed here.

    Returns:
        dict: Economics metrics. The shape depends on token-ledger availability:
          - token_ledger_available == False: a slim, honest block carrying only
            the git-derived denominators (merged_prs, lines_of_code, wave_count)
            plus provenance. No token/cost ratio fields are emitted, because
            0.0 filler reads as a measured value.
          - token_ledger_available == True: the full block with cost_per_loc,
            cost_per_merged_pr, cost_per_wave, unit_economics and (if pricing is
            configured) cost_estimates.
    """
    repo_path = Path(repo_root)
    if state_dir:
        state_path = Path(state_dir)
    else:
        state_path = repo_path / "state"

    # Get git metrics
    loc = _get_lines_of_code(str(repo_path))
    # Single-source the merged-PR count: use the caller's value verbatim, else
    # fall back to the shared git-log definition.
    if merged_prs is None:
        merged_prs = _get_merged_prs(str(repo_path))
        source = merged_prs_source or "git-log"
    else:
        source = merged_prs_source or "git-log"
    wave_count = _get_wave_count(str(repo_path))

    # Get token metrics
    total_tokens = _get_total_tokens(state_path)
    model_tokens = _get_total_tokens_by_model(state_path)

    token_ledger_available = total_tokens > 0

    # No token ledger: emit an honest slim block. Omitting the ratio fields is
    # deliberate — a 0.0 tokens_per_pr is indistinguishable from a real measure.
    if not token_ledger_available:
        return {
            "token_ledger_available": False,
            "merged_prs": merged_prs,
            "merged_prs_source": source,
            "lines_of_code": loc,
            "wave_count": wave_count,
        }

    # Full block with measured token data.
    result = {
        "token_ledger_available": True,
        "merged_prs": merged_prs,
        "merged_prs_source": source,
        "cost_per_loc": {
            "lines_of_code": loc,
            "total_tokens": total_tokens,
            "tokens_per_loc": 0.0 if loc == 0 else total_tokens / loc,
        },
        "cost_per_merged_pr": {
            "merged_prs": merged_prs,
            "total_tokens": total_tokens,
            "tokens_per_pr": 0.0 if merged_prs == 0 else total_tokens / merged_prs,
        },
        "cost_per_wave": {
            "wave_count": wave_count,
            "total_tokens": total_tokens,
            "tokens_per_wave": 0.0 if wave_count == 0 else total_tokens / wave_count,
        },
        "unit_economics": {
            "cost_per_backlog_item": (
                total_tokens / merged_prs if merged_prs > 0
                else (total_tokens / wave_count if wave_count > 0 else 0.0)
            ),
            "cost_per_wave_item": 0.0 if wave_count == 0 else total_tokens / wave_count,
            "backlog_item_proxy": "merged_prs" if merged_prs > 0 else "waves",
            # items_count must equal the single-sourced PR count when PRs are the
            # proxy, so validate_stats_integrity never flags a contradiction.
            "items_count": merged_prs if merged_prs > 0 else (wave_count or 1),
        },
    }

    # Add pricing estimates if config available
    pricing_map = _load_pricing_config(config_file)
    if pricing_map and model_tokens:
        total_cost = 0.0
        model_costs = {}

        for model, tokens in model_tokens.items():
            if model in pricing_map:
                pricing = pricing_map[model]
                input_price = pricing.get("input_per_mtok", 0.0)
                output_price = pricing.get("output_per_mtok", 0.0)

                input_cost = (tokens["tokens_in"] * input_price) / 1_000_000
                output_cost = (tokens["tokens_out"] * output_price) / 1_000_000
                model_cost = input_cost + output_cost

                model_costs[model] = {
                    "input_cost": input_cost,
                    "output_cost": output_cost,
                    "total_cost": model_cost,
                }
                total_cost += model_cost

        result["cost_estimates"] = {
            "total_cost_dollars": total_cost,
            "cost_per_loc_dollars": 0.0 if loc == 0 else total_cost / loc,
            "cost_per_pr_dollars": 0.0 if merged_prs == 0 else total_cost / merged_prs,
            "by_model": model_costs,
        }

    return result


def get_metric_honesty_caveats() -> Dict[str, str]:
    """Return honesty caveats about what metrics do/don't capture.

    Returns:
        dict: Mapping of metric name to caveat text
    """
    return {
        "cost_per_loc": (
            "Cost-per-LOC metric (tokens / lines of code) captures token spend only from "
            "fleet ledger (parallel Haiku execution). It does NOT include:\n"
            "  - Fable orchestrator main-thread tokens (token tracking on MCP side only)\n"
            "  - Manual human review/decision time\n"
            "  - Deprecated code or deleted branches\n"
            "Limitations:\n"
            "  - LOC counts from git ls-files (current HEAD); doesn't reflect deleted branches\n"
            "  - Ledger entries are only recorded on successful/failed agent runs\n"
            "  - Missing/stale ledger will underestimate costs"
        ),
        "cost_per_merged_pr": (
            "Cost-per-PR metric captures spend only from shipped merges, using commit "
            "message pattern 'Merge pull request #N'. It does NOT include:\n"
            "  - Abandoned PRs or branches\n"
            "  - Main-thread Fable tokens\n"
            "  - Spike/spike work that never merged\n"
            "Limitations:\n"
            "  - Depends on GitHub merge message format; non-GitHub workflows may not count\n"
            "  - PRs with no ledger entries are still counted (incomplete cost attribution)"
        ),
        "cost_per_wave": (
            "Cost-per-wave metric infers wave count from commit messages containing 'wave-N'. "
            "It does NOT include:\n"
            "  - Implicit waves without naming\n"
            "  - Manual work outside the tracked waves\n"
            "Limitations:\n"
            "  - Requires strict 'wave-N' naming; ad-hoc commits may create false waves\n"
            "  - Fallback to release tags (v*) if no wave messages found\n"
            "  - Wave boundaries not validated against STATE.md"
        ),
        "unit_economics": (
            "Unit cost economics (cost per backlog item, cost per passing test) uses merged PRs "
            "as a proxy for backlog items. Limitations:\n"
            "  - Assumes every PR == one backlog item (may overcounts parallel work)\n"
            "  - Does NOT count test count metrics (passing tests require separate test ledger)\n"
            "  - Cost proxy uses total tokens / PR count; doesn't attribute costs per test\n"
            "Future: Requires tracker integration + test ledger for accurate cost-per-test"
        ),
        "cost_estimates": (
            "Dollar cost estimates require aesop.config.json pricing map in format:\n"
            "  {\"pricing\": {\"model-id\": {\"input_per_mtok\": 0.80, \"output_per_mtok\": 4.0}}}\n"
            "If pricing is missing or model not in map, no estimate provided.\n"
            "Limitations:\n"
            "  - Pricing based on model type; no dynamic rate adjustments (batch, volume)\n"
            "  - Doesn't account for regional pricing, tax, or discounts\n"
            "  - Tokens counted only from successful ledger entries"
        ),
    }


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
        "--state-dir",
        help="Path to aesop state directory (default: repo_root/state)"
    )
    parser.add_argument(
        "--config",
        help="Path to aesop.config.json for pricing (optional)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON"
    )

    args = parser.parse_args()

    metrics = calculate_economics(
        repo_root=args.repo,
        state_dir=args.state_dir,
        config_file=args.config
    )

    if args.json:
        print(json.dumps(metrics, indent=2))
    elif not metrics.get("token_ledger_available", False):
        # Honest slim output: no token ledger, so no per-unit token metrics.
        print("\nCost Economics Metrics")
        print("=" * 60)
        print("\nToken ledger: UNAVAILABLE (no OUTCOMES-LEDGER.md data)")
        print("Per-unit token/cost metrics omitted (not measured).")
        print(f"\n  Merged PRs (source {metrics.get('merged_prs_source')}): {metrics.get('merged_prs')}")
        print(f"  Lines of Code:        {metrics.get('lines_of_code'):,}")
        print(f"  Wave Count:           {metrics.get('wave_count')}")
        print("\n" + "=" * 60)
    else:
        # Human-readable output
        print("\nCost Economics Metrics")
        print("=" * 60)

        print("\nCost per LOC:")
        loc_data = metrics["cost_per_loc"]
        print(f"  Lines of Code:        {loc_data['lines_of_code']:,}")
        print(f"  Total Tokens:         {loc_data['total_tokens']:,}")
        print(f"  Tokens per LOC:       {loc_data['tokens_per_loc']:.2f}")

        print("\nCost per Merged PR:")
        pr_data = metrics["cost_per_merged_pr"]
        print(f"  Merged PRs:           {pr_data['merged_prs']}")
        print(f"  Total Tokens:         {pr_data['total_tokens']:,}")
        print(f"  Tokens per PR:        {pr_data['tokens_per_pr']:.2f}")

        print("\nCost per Wave:")
        wave_data = metrics["cost_per_wave"]
        print(f"  Wave Count:           {wave_data['wave_count']}")
        print(f"  Total Tokens:         {wave_data['total_tokens']:,}")
        print(f"  Tokens per Wave:      {wave_data['tokens_per_wave']:.2f}")

        print("\nUnit Economics:")
        unit_data = metrics["unit_economics"]
        print(f"  Cost per Backlog Item: {unit_data['cost_per_backlog_item']:.2f} tokens")
        print(f"  Cost per Wave Item:    {unit_data['cost_per_wave_item']:.2f} tokens")

        if "cost_estimates" in metrics:
            print("\nCost Estimates (USD):")
            est = metrics["cost_estimates"]
            print(f"  Total Cost:           ${est['total_cost_dollars']:.4f}")
            print(f"  Cost per LOC:         ${est['cost_per_loc_dollars']:.6f}")
            print(f"  Cost per PR:          ${est['cost_per_pr_dollars']:.4f}")

        print("\n" + "=" * 60)
        print("\nHonesty Caveats:")
        caveats = get_metric_honesty_caveats()
        for metric, caveat in caveats.items():
            print(f"\n{metric}:")
            for line in caveat.split("\n"):
                print(f"  {line}")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
