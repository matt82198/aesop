#!/usr/bin/env python3
"""Cost/scorecard collector — parse ledger and aggregate costs.

This module provides get_cost_summary() which parses the outcomes ledger
markdown table and returns per-model, per-day, and overall cost/token aggregations
with optional pricing estimates.

Ledger format (markdown table, supports both 7-column and 9-column):
  Legacy 7-column:
    | ISO timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict |
    | 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |

  Extended 9-column (phase/wave optional):
    | ISO timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict | phase | wave |
    | 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK | build | 7 |

CostSummary JSON shape (returned by get_cost_summary()):
  {
    "models": {
      "model-id": {
        "runs": int,
        "tokens_in": int,
        "tokens_out": int,
        "verdicts": {"OK": int, "FAILED": int, "EMPTY": int, "HUNG": int}
      },
      ...
    },
    "daily_totals": {
      "YYYY-MM-DD": {"tokens_in": int, "tokens_out": int},
      ...
    },
    "overall_scorecard": {
      "total_runs": int,
      "ok_count": int,
      "failed_count": int,
      "empty_count": int,
      "hung_count": int,
      "ok_rate": float (0.0-1.0),
      "failed_rate": float,
      "empty_rate": float,
      "hung_rate": float
    },
    "skipped_lines": int,
    "has_pricing": bool,
    "estimates_by_model": {
      "model-id": {
        "input_cost": float (dollars),
        "output_cost": float (dollars),
        "total_cost": float (dollars)
      },
      ...
    }
  }

Key behavior:
  - Missing ledger file: returns empty summary with documented shape.
  - Malformed lines: skipped and counted in skipped_lines field.
  - Config read at CALL time (not import time) for test isolation.
  - UTF-8 explicit encoding for all file operations.
  - No external dependencies (pure stdlib).
  - Pricing estimates ONLY if aesop.config.json has a "pricing" map.
"""
import json
from pathlib import Path

# Note: import config at module level, but read config.X at CALL time
# (not at import time) to ensure test-fixture isolation works.
import config


def _validate_ledger_format(lines):
    """Validate that ledger has the expected column structure.

    Checks the first non-empty, non-separator, non-header line to ensure it looks like a data row
    (has ISO timestamp, agent type, model, numeric fields, verdict). Returns (is_valid, error_message).

    Accepts two formats:
      - Legacy 7-column: | timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict |
      - Extended 9-column: | timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict | phase | wave |

    Optional trailing columns (phase, wave) are ignored during validation.
    """
    for line in lines:
        line = line.strip()

        # Skip empty lines
        if not line:
            continue

        # Skip separator lines (all dashes, pipes, and spaces)
        if all(c in '|- ' for c in line):
            continue

        # This is the first real line; validate it
        if not line.startswith('|') or not line.endswith('|'):
            return False, "First data line does not start/end with pipe"

        parts = [p.strip() for p in line.split('|')]

        # Accept both 7-column (9 parts) and 9-column (11 parts) formats
        # 7 columns: ['', col1, col2, col3, col4, col5, col6, col7, '']
        # 9 columns: ['', col1, col2, col3, col4, col5, col6, col7, col8, col9, '']
        if len(parts) < 9:
            return False, f"Too few columns, got {len(parts) - 2} (expected at least 7)"

        # Extract core columns (first 7 required columns)
        try:
            timestamp = parts[1]
            agent_type = parts[2]
            model = parts[3]
            duration_str = parts[4]
            tokens_in_str = parts[5]
            tokens_out_str = parts[6]
            verdict = parts[7]

            # Skip header line if timestamp column contains "timestamp" or "ISO"
            if 'timestamp' in timestamp.lower() or 'iso' in timestamp.lower():
                continue

            # Check if timestamp looks like ISO format (contains 'T' and '-')
            if 'T' not in timestamp or '-' not in timestamp:
                return False, f"First data line does not have ISO timestamp in column 1: {timestamp}"

            # Check if tokens are numeric
            try:
                int(tokens_in_str)
                int(tokens_out_str)
            except ValueError:
                return False, f"Token columns must be numeric, got tokens_in={tokens_in_str}, tokens_out={tokens_out_str}"

            # Check if verdict is valid
            if verdict not in ("OK", "FAILED", "EMPTY", "HUNG"):
                return False, f"First data line has invalid verdict: {verdict}"

            return True, ""
        except IndexError:
            return False, "First data line has missing columns"

    # No data lines found
    return True, ""  # Empty ledger is ok


def get_cost_summary():
    """Parse the outcomes ledger and return cost/token/verdict aggregations.

    Reads from the ledger path exposed by config (config.STATE_DIR/ledger/OUTCOMES-LEDGER.md).
    Returns an empty summary with documented shape if ledger is missing or empty.
    Malformed lines are skipped and counted in skipped_lines.

    Validates ledger format on first data line. If format is invalid, returns
    a summary containing {"error": "ledger format invalid"} and logs to stderr.

    All config paths are read at call time (not import time) to ensure
    test-fixture isolation via config.reload().

    Extended fields (additively):
      - per_week_costs: dict of "YYYY-Www" -> week cost/token totals and model mix
      - per_wave_costs: dict of "wave-N" -> wave cost/token totals and model mix
      - per_agent_costs: dict of agent_type -> agent cost/token totals and model mix
      - verdict_weighted_cost: cost-per-outcome metrics (cost per OK, weighted by verdict distribution)
      - model_mix_trend: per-day model usage distribution (%)

    Returns:
        dict: CostSummary with models, daily_totals, overall_scorecard,
              skipped_lines, has_pricing, estimates_by_model, per_week_costs,
              per_wave_costs, per_agent_costs, verdict_weighted_cost, model_mix_trend
              (or error field if invalid).
    """
    import sys
    from datetime import datetime, timedelta

    # Read ledger path at call time
    ledger_file = config.STATE_DIR / "ledger" / "OUTCOMES-LEDGER.md"

    # Initialize result structure
    result = {
        "models": {},
        "daily_totals": {},
        "overall_scorecard": {
            "total_runs": 0,
            "ok_count": 0,
            "failed_count": 0,
            "empty_count": 0,
            "hung_count": 0,
            "ok_rate": 0.0,
            "failed_rate": 0.0,
            "empty_rate": 0.0,
            "hung_rate": 0.0,
        },
        "skipped_lines": 0,
        "has_pricing": False,
        "estimates_by_model": {},
        "per_week_costs": {},
        "per_wave_costs": {},
        "per_agent_costs": {},
        "verdict_weighted_cost": {
            "cost_per_ok": 0.0,
            "cost_per_failed": 0.0,
            "cost_per_empty": 0.0,
            "cost_per_hung": 0.0,
        },
        "model_mix_trend": {},
    }

    # If ledger file doesn't exist, return empty summary
    if not ledger_file.exists():
        return result

    # Read and parse ledger with explicit UTF-8 encoding
    try:
        content = ledger_file.read_text(encoding='utf-8')
    except Exception:
        # Graceful: if read fails, return empty summary
        return result

    lines = content.strip().split('\n')

    # Validate ledger format
    is_valid, error_msg = _validate_ledger_format(lines)
    if not is_valid:
        print(f"[cost] Ledger format invalid: {error_msg}", file=sys.stderr, flush=True)
        result["error"] = "ledger format invalid"
        return result

    # Track ledger entries for per-week-per-model calculation
    ledger_entries = []

    # Parse each line
    for line in lines:
        line = line.strip()

        # Skip empty lines
        if not line:
            continue

        # Skip header separator lines (all dashes, pipes, and spaces) — silently, don't count as skipped
        if all(c in '|- ' for c in line):
            continue

        # Parse pipe-delimited row
        if not line.startswith('|') or not line.endswith('|'):
            print(f"[cost] Skipping malformed line (no pipe delimiters): {line[:50]}", file=sys.stderr, flush=True)
            result["skipped_lines"] += 1
            continue

        # Skip separator lines (all dashes, pipes, and spaces) — silently, don't count as skipped
        if all(c in '|- ' for c in line):
            continue

        # Split by pipe and strip whitespace
        parts = [p.strip() for p in line.split('|')]

        # Markdown tables have empty strings at start and end after split
        # Format: | col1 | col2 | col3 | col4 | col5 | col6 | col7 |
        # After split: ['', 'col1', 'col2', 'col3', 'col4', 'col5', 'col6', 'col7', '']
        if len(parts) < 9:  # Need at least 9 parts (empty + 7 columns + empty)
            print(f"[cost] Skipping line with too few columns ({len(parts) - 2}): {line[:50]}", file=sys.stderr, flush=True)
            result["skipped_lines"] += 1
            continue

        # Extract columns (skip leading/trailing empty)
        try:
            timestamp = parts[1]  # ISO timestamp
            agent_type = parts[2]  # "Agent"
            model = parts[3]
            duration_str = parts[4]
            tokens_in_str = parts[5]
            tokens_out_str = parts[6]
            verdict = parts[7]
        except IndexError:
            print(f"[cost] Skipping line with missing columns: {line[:50]}", file=sys.stderr, flush=True)
            result["skipped_lines"] += 1
            continue

        # Skip header line if timestamp column contains "timestamp" or "ISO" — silently, don't count as skipped
        if 'timestamp' in timestamp.lower() or 'iso' in timestamp.lower():
            continue

        # Parse numeric fields
        try:
            tokens_in = int(tokens_in_str)
            tokens_out = int(tokens_out_str)
        except ValueError:
            print(f"[cost] Skipping line with non-numeric tokens (in={tokens_in_str}, out={tokens_out_str}): {line[:50]}", file=sys.stderr, flush=True)
            result["skipped_lines"] += 1
            continue

        # Validate verdict
        if verdict not in ("OK", "FAILED", "EMPTY", "HUNG"):
            print(f"[cost] Skipping line with invalid verdict ({verdict}): {line[:50]}", file=sys.stderr, flush=True)
            result["skipped_lines"] += 1
            continue

        # Extract date from ISO timestamp (YYYY-MM-DD)
        try:
            date_str = timestamp.split('T')[0]
        except IndexError:
            print(f"[cost] Skipping line with invalid timestamp format: {line[:50]}", file=sys.stderr, flush=True)
            result["skipped_lines"] += 1
            continue

        # Extract optional wave and phase fields (if present)
        wave = None
        phase = None
        try:
            if len(parts) > 8:
                phase = parts[8].strip() if len(parts) > 8 else None
            if len(parts) > 9:
                wave = parts[9].strip() if len(parts) > 9 else None
        except (IndexError, ValueError):
            pass

        # Store ledger entry for per-week/per-wave/per-agent calculation
        ledger_entries.append({
            "timestamp": timestamp,
            "date_str": date_str,
            "model": model,
            "agent_type": agent_type,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "verdict": verdict,
            "wave": wave,
            "phase": phase
        })

        # Aggregate by model
        if model not in result["models"]:
            result["models"][model] = {
                "runs": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "verdicts": {"OK": 0, "FAILED": 0, "EMPTY": 0, "HUNG": 0},
            }

        result["models"][model]["runs"] += 1
        result["models"][model]["tokens_in"] += tokens_in
        result["models"][model]["tokens_out"] += tokens_out
        result["models"][model]["verdicts"][verdict] += 1

        # Aggregate by date
        if date_str not in result["daily_totals"]:
            result["daily_totals"][date_str] = {"tokens_in": 0, "tokens_out": 0}

        result["daily_totals"][date_str]["tokens_in"] += tokens_in
        result["daily_totals"][date_str]["tokens_out"] += tokens_out

        # Count for overall scorecard
        result["overall_scorecard"]["total_runs"] += 1
        if verdict == "OK":
            result["overall_scorecard"]["ok_count"] += 1
        elif verdict == "FAILED":
            result["overall_scorecard"]["failed_count"] += 1
        elif verdict == "EMPTY":
            result["overall_scorecard"]["empty_count"] += 1
        elif verdict == "HUNG":
            result["overall_scorecard"]["hung_count"] += 1

    # Calculate rates
    total = result["overall_scorecard"]["total_runs"]
    if total > 0:
        result["overall_scorecard"]["ok_rate"] = result["overall_scorecard"]["ok_count"] / total
        result["overall_scorecard"]["failed_rate"] = result["overall_scorecard"]["failed_count"] / total
        result["overall_scorecard"]["empty_rate"] = result["overall_scorecard"]["empty_count"] / total
        result["overall_scorecard"]["hung_rate"] = result["overall_scorecard"]["hung_count"] / total

    # Load pricing config at call time and compute estimates
    pricing_map = _load_pricing_config()
    if pricing_map:
        result["has_pricing"] = True
        for model, stats in result["models"].items():
            if model in pricing_map:
                pricing = pricing_map[model]
                input_price = pricing.get("input_per_mtok", 0.0)
                output_price = pricing.get("output_per_mtok", 0.0)

                # Calculate costs: (tokens / 1_000_000) * price_per_mtok
                input_cost = (stats["tokens_in"] * input_price) / 1_000_000
                output_cost = (stats["tokens_out"] * output_price) / 1_000_000
                total_cost = input_cost + output_cost

                result["estimates_by_model"][model] = {
                    "input_cost": input_cost,
                    "output_cost": output_cost,
                    "total_cost": total_cost,
                }

    # Calculate per-week costs and model mix trend
    _calculate_weekly_costs(result, pricing_map, ledger_entries)
    _calculate_per_wave_costs(result, pricing_map, ledger_entries)
    _calculate_per_agent_costs(result, pricing_map, ledger_entries)
    _calculate_verdict_weighted_cost(result, pricing_map)
    _calculate_model_mix_trend(result)

    return result


def _calculate_weekly_costs(result, pricing_map, ledger_entries):
    """Calculate per-week cost rollup from ledger entries.

    Groups ledger entries by ISO week (YYYY-Www format) and aggregates
    per-model tokens for each week. If pricing is available, includes cost estimates.

    BUG FIX: This function now uses EACH WEEK'S OWN per-model token counts
    from the ledger, not the global model distribution. This prevents inflating
    each week's cost by applying global model mix that may not be accurate
    for that particular week.

    PERF FIX: datetime.strptime is hoisted outside the inner loops. Each timestamp
    is parsed ONCE during the initial pass, avoiding O(weeks*models*entries) re-parsing.

    Modifies result["per_week_costs"] in-place with structure:
    {
        "YYYY-Www": {
            "tokens_in": int,
            "tokens_out": int,
            "model_tokens": {"model": int, ...},
            "cost": float (if pricing available)
        }
    }
    """
    from datetime import datetime

    if not ledger_entries:
        return

    # OPTIMIZATION: Pre-parse all timestamps once (avoid O(weeks*models*entries) re-parsing)
    # Map each entry index to its ISO week key, computed upfront
    entry_weeks = {}
    for idx, entry in enumerate(ledger_entries):
        try:
            # Parse YYYY-MM-DD to ISO week
            dt = datetime.strptime(entry["date_str"], "%Y-%m-%d")
            iso_year, iso_week, _ = dt.isocalendar()
            week_key = f"{iso_year}-W{iso_week:02d}"
            entry_weeks[idx] = week_key
        except (ValueError, KeyError):
            entry_weeks[idx] = None

    # Group ledger entries by ISO week and aggregate per-model within each week
    weeks = {}
    for idx, entry in enumerate(ledger_entries):
        week_key = entry_weeks[idx]
        if week_key is None:
            # Skip entries with invalid dates or missing fields
            continue

        if week_key not in weeks:
            weeks[week_key] = {
                "tokens_in": 0,
                "tokens_out": 0,
                "model_tokens": {},
                "cost": 0.0
            }

        # Aggregate this entry's tokens into the week
        weeks[week_key]["tokens_in"] += entry["tokens_in"]
        weeks[week_key]["tokens_out"] += entry["tokens_out"]

        # Track per-model tokens within this week
        model = entry["model"]
        if model not in weeks[week_key]["model_tokens"]:
            weeks[week_key]["model_tokens"][model] = 0
        weeks[week_key]["model_tokens"][model] += entry["tokens_in"] + entry["tokens_out"]

    # If pricing available, calculate cost per week based on THAT WEEK'S model mix
    if pricing_map:
        for week_key, week_data in weeks.items():
            total_cost = 0.0
            for model, total_tokens in week_data["model_tokens"].items():
                if model in pricing_map:
                    pricing = pricing_map[model]
                    input_price = pricing.get("input_per_mtok", 0.0)
                    output_price = pricing.get("output_per_mtok", 0.0)
                    # Use the pre-parsed week info to find entries for this model in this week
                    model_entries_in_week = [
                        ledger_entries[idx]
                        for idx, w_key in entry_weeks.items()
                        if w_key == week_key and ledger_entries[idx]["model"] == model
                    ]
                    if model_entries_in_week:
                        week_model_tokens_in = sum(e["tokens_in"] for e in model_entries_in_week)
                        week_model_tokens_out = sum(e["tokens_out"] for e in model_entries_in_week)
                        model_cost = (week_model_tokens_in * input_price + week_model_tokens_out * output_price) / 1_000_000
                    else:
                        # Fallback: if we can't find the entries, use 1:2 ratio estimate
                        tokens_in_estimate = total_tokens * 0.333
                        tokens_out_estimate = total_tokens * 0.667
                        model_cost = (tokens_in_estimate * input_price + tokens_out_estimate * output_price) / 1_000_000
                    total_cost += model_cost
            week_data["cost"] = total_cost

    result["per_week_costs"] = weeks


def _calculate_verdict_weighted_cost(result, pricing_map):
    """Calculate cost-per-outcome metrics weighted by verdict distribution.

    Computes cost per successful outcome (cost / ok_count) and cost per other outcomes.
    If pricing is available, uses estimated costs; otherwise uses token counts as proxy.

    Modifies result["verdict_weighted_cost"] in-place with structure:
    {
        "cost_per_ok": float,
        "cost_per_failed": float,
        "cost_per_empty": float,
        "cost_per_hung": float,
    }
    """
    scorecard = result["overall_scorecard"]

    # Calculate total cost (if pricing available)
    total_cost = 0.0
    if pricing_map and result["has_pricing"]:
        for estimate in result["estimates_by_model"].values():
            total_cost += estimate.get("total_cost", 0.0)
    else:
        # Use token count as cost proxy (tokens_in + tokens_out)
        for daily in result["daily_totals"].values():
            total_cost += daily["tokens_in"] + daily["tokens_out"]

    # Calculate cost per outcome type
    result["verdict_weighted_cost"] = {
        "cost_per_ok": total_cost / scorecard["ok_count"] if scorecard["ok_count"] > 0 else 0.0,
        "cost_per_failed": total_cost / scorecard["failed_count"] if scorecard["failed_count"] > 0 else 0.0,
        "cost_per_empty": total_cost / scorecard["empty_count"] if scorecard["empty_count"] > 0 else 0.0,
        "cost_per_hung": total_cost / scorecard["hung_count"] if scorecard["hung_count"] > 0 else 0.0,
    }


def _calculate_model_mix_trend(result):
    """Calculate per-day model usage distribution as percentages.

    Breaks down the token usage by model for each day in daily_totals.
    Modifies result["model_mix_trend"] in-place with structure:
    {
        "YYYY-MM-DD": {
            "model": percentage (0.0-100.0),
            ...
        }
    }
    """
    model_mix_trend = {}

    # For each day, calculate model distribution
    # Since daily_totals doesn't track per-model breakdown, we need to estimate
    # based on the overall model distribution across all runs in that day
    if not result["models"]:
        result["model_mix_trend"] = model_mix_trend
        return

    # Calculate overall model token distribution
    total_model_tokens = {}
    grand_total_tokens = 0
    for model, stats in result["models"].items():
        tokens = stats["tokens_in"] + stats["tokens_out"]
        total_model_tokens[model] = tokens
        grand_total_tokens += tokens

    # Apply model distribution to each day (as a simplified proxy)
    for date_str in result["daily_totals"].keys():
        daily_dist = {}
        daily_total = result["daily_totals"][date_str]["tokens_in"] + result["daily_totals"][date_str]["tokens_out"]

        if grand_total_tokens > 0 and daily_total > 0:
            for model, total_tokens in total_model_tokens.items():
                # Distribute daily tokens proportionally to model usage
                ratio = total_tokens / grand_total_tokens
                daily_dist[model] = round(ratio * 100.0, 2)

        model_mix_trend[date_str] = daily_dist

    result["model_mix_trend"] = model_mix_trend


def _calculate_per_wave_costs(result, pricing_map, ledger_entries):
    """Calculate per-wave cost rollup from ledger entries.

    Groups ledger entries by wave number and aggregates per-model tokens for each wave.
    If pricing is available, includes cost estimates.

    Modifies result["per_wave_costs"] in-place with structure:
    {
        "wave-N": {
            "tokens_in": int,
            "tokens_out": int,
            "model_tokens": {"model": int, ...},
            "cost": float (if pricing available)
        }
    }
    """
    if not ledger_entries:
        return

    # Group ledger entries by wave and aggregate per-model within each wave
    waves = {}
    for entry in ledger_entries:
        if not entry.get("wave"):
            # Skip entries without wave info
            continue

        wave_key = f"wave-{entry['wave']}"

        if wave_key not in waves:
            waves[wave_key] = {
                "tokens_in": 0,
                "tokens_out": 0,
                "model_tokens": {},
                "cost": 0.0
            }

        # Aggregate this entry's tokens into the wave
        waves[wave_key]["tokens_in"] += entry["tokens_in"]
        waves[wave_key]["tokens_out"] += entry["tokens_out"]

        # Track per-model tokens within this wave
        model = entry["model"]
        if model not in waves[wave_key]["model_tokens"]:
            waves[wave_key]["model_tokens"][model] = 0
        waves[wave_key]["model_tokens"][model] += entry["tokens_in"] + entry["tokens_out"]

    # If pricing available, calculate cost per wave based on that wave's model mix
    if pricing_map:
        for wave_key, wave_data in waves.items():
            total_cost = 0.0
            for model, total_tokens in wave_data["model_tokens"].items():
                if model in pricing_map:
                    pricing = pricing_map[model]
                    input_price = pricing.get("input_per_mtok", 0.0)
                    output_price = pricing.get("output_per_mtok", 0.0)
                    # Find entries for this model in this wave
                    model_entries_in_wave = [
                        e for e in ledger_entries
                        if e.get("wave") == wave_key.replace("wave-", "") and e["model"] == model
                    ]
                    if model_entries_in_wave:
                        wave_model_tokens_in = sum(e["tokens_in"] for e in model_entries_in_wave)
                        wave_model_tokens_out = sum(e["tokens_out"] for e in model_entries_in_wave)
                        model_cost = (wave_model_tokens_in * input_price + wave_model_tokens_out * output_price) / 1_000_000
                    else:
                        # Fallback: if we can't find the entries, use 1:2 ratio estimate
                        tokens_in_estimate = total_tokens * 0.333
                        tokens_out_estimate = total_tokens * 0.667
                        model_cost = (tokens_in_estimate * input_price + tokens_out_estimate * output_price) / 1_000_000
                    total_cost += model_cost
            wave_data["cost"] = total_cost

    result["per_wave_costs"] = waves


def _calculate_per_agent_costs(result, pricing_map, ledger_entries):
    """Calculate per-agent-type cost rollup from ledger entries.

    Groups ledger entries by agent_type and aggregates per-model tokens for each agent type.
    If pricing is available, includes cost estimates.

    Modifies result["per_agent_costs"] in-place with structure:
    {
        "agent_type": {
            "tokens_in": int,
            "tokens_out": int,
            "model_tokens": {"model": int, ...},
            "runs": int,
            "verdicts": {"OK": int, "FAILED": int, ...},
            "cost": float (if pricing available)
        }
    }
    """
    if not ledger_entries:
        return

    # Group ledger entries by agent_type and aggregate per-model within each agent type
    agents = {}
    for entry in ledger_entries:
        agent_type = entry.get("agent_type", "unknown")

        if agent_type not in agents:
            agents[agent_type] = {
                "tokens_in": 0,
                "tokens_out": 0,
                "model_tokens": {},
                "runs": 0,
                "verdicts": {"OK": 0, "FAILED": 0, "EMPTY": 0, "HUNG": 0},
                "cost": 0.0
            }

        # Aggregate this entry's tokens into the agent type
        agents[agent_type]["tokens_in"] += entry["tokens_in"]
        agents[agent_type]["tokens_out"] += entry["tokens_out"]
        agents[agent_type]["runs"] += 1
        agents[agent_type]["verdicts"][entry["verdict"]] += 1

        # Track per-model tokens within this agent type
        model = entry["model"]
        if model not in agents[agent_type]["model_tokens"]:
            agents[agent_type]["model_tokens"][model] = 0
        agents[agent_type]["model_tokens"][model] += entry["tokens_in"] + entry["tokens_out"]

    # If pricing available, calculate cost per agent type based on that type's model mix
    if pricing_map:
        for agent_type, agent_data in agents.items():
            total_cost = 0.0
            for model, total_tokens in agent_data["model_tokens"].items():
                if model in pricing_map:
                    pricing = pricing_map[model]
                    input_price = pricing.get("input_per_mtok", 0.0)
                    output_price = pricing.get("output_per_mtok", 0.0)
                    # Find entries for this model from this agent type
                    model_entries_for_agent = [
                        e for e in ledger_entries
                        if e.get("agent_type") == agent_type and e["model"] == model
                    ]
                    if model_entries_for_agent:
                        agent_model_tokens_in = sum(e["tokens_in"] for e in model_entries_for_agent)
                        agent_model_tokens_out = sum(e["tokens_out"] for e in model_entries_for_agent)
                        model_cost = (agent_model_tokens_in * input_price + agent_model_tokens_out * output_price) / 1_000_000
                    else:
                        # Fallback: if we can't find the entries, use 1:2 ratio estimate
                        tokens_in_estimate = total_tokens * 0.333
                        tokens_out_estimate = total_tokens * 0.667
                        model_cost = (tokens_in_estimate * input_price + tokens_out_estimate * output_price) / 1_000_000
                    total_cost += model_cost
            agent_data["cost"] = total_cost

    result["per_agent_costs"] = agents


def _load_pricing_config():
    """Load pricing map from aesop.config.json at call time.

    Returns:
        dict or None: pricing map {model: {input_per_mtok: float, output_per_mtok: float}}
                      or None if no pricing config found.
    """
    # Read config file at call time (not import time)
    config_file = config.CONFIG_FILE

    if not config_file.exists():
        return None

    try:
        with open(config_file, encoding='utf-8') as f:
            config_data = json.load(f)
    except Exception:
        # Graceful: if config read fails, no pricing
        return None

    return config_data.get("pricing", None)
