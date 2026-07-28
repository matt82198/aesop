#!/usr/bin/env python3
"""
Aesop UI — context quality analysis helpers (wave-30 context-engineering lane C).

Provides read-only analysis of dispatch prompts and agent transcripts to surface
context quality indicators: spec sharpness (prompt quality signals), file-scope
visualization (intended vs actual), and first-try success rates.

All functions are read-only; no orchestration changes.
Computes REAL data from transcripts: actual files written (Write/Edit tool-use),
repair markers (re-dispatch/retry/fail-then-pass), honest empty states.
"""
import json
import re
import os
from pathlib import Path
from typing import Optional, Dict, List, Any

import config
import agents


class SpecSharpnessScore:
    """Spec sharpness score: Low/Med/High/Excellent based on prompt signals."""

    LEVELS = ["Low", "Med", "High", "Excellent"]

    @staticmethod
    def score_prompt(prompt: str) -> Dict[str, Any]:
        """Score a dispatch prompt for spec sharpness.

        Signals checked:
        - Directive count (control flow, explicit instructions)
        - Acceptance criteria section present
        - File pattern specificity (glob patterns, specific paths vs wildcards)
        - Structured data (code blocks, tables, lists)
        - Repetition/emphasis markers

        Returns:
            {
                "level": "Low" | "Med" | "High" | "Excellent",
                "score": 0-100,
                "signals": {
                    "directive_count": int,
                    "has_acceptance_criteria": bool,
                    "file_specificity": float (0-1),
                    "structured_content_ratio": float (0-1),
                    "emphasis_markers": int
                }
            }
        """
        if not prompt:
            return {
                "level": "Low",
                "score": 0,
                "signals": {
                    "directive_count": 0,
                    "has_acceptance_criteria": False,
                    "file_specificity": 0.0,
                    "structured_content_ratio": 0.0,
                    "emphasis_markers": 0
                }
            }

        signals = {}

        # 1. Count directives (must, should, must not, should not, require, ensure, etc.)
        directive_patterns = [
            r'\b(?:MUST|MUST NOT|SHOULD|SHOULD NOT|REQUIRE|ENSURE|NEVER|ALWAYS)\b',
            r'\b(?:implement|create|build|add|remove|delete|fix|refactor|optimize)\b',
            r'^[-*]\s+\w+', # list items
        ]
        directive_count = sum(
            len(re.findall(pat, prompt, re.IGNORECASE | re.MULTILINE))
            for pat in directive_patterns
        )
        signals["directive_count"] = directive_count

        # 2. Check for acceptance criteria section
        has_acceptance_criteria = bool(
            re.search(
                r'(?i)(?:acceptance\s+criteria|test\s+plan|expected\s+output|success\s+criteria)',
                prompt
            )
        )
        signals["has_acceptance_criteria"] = has_acceptance_criteria

        # 3. File specificity: ratio of specific paths to wildcards
        file_refs = re.findall(r'(?:ui/|src/|lib/|tests/|\.tsx?|\.py)\S*', prompt)
        wildcard_refs = re.findall(r'[*?]', prompt)
        file_specificity = 0.0
        if file_refs or wildcard_refs:
            total_refs = len(file_refs) + len(wildcard_refs)
            file_specificity = len(file_refs) / total_refs if total_refs > 0 else 0.0
        signals["file_specificity"] = file_specificity

        # 4. Structured content ratio (code blocks, tables, lists)
        lines = prompt.split('\n')
        structured_lines = sum(
            1 for line in lines
            if (line.strip().startswith(('```', '|', '-', '*', '+', '1.', '['))
                or re.match(r'^\s+[-*]\s', line))
        )
        total_lines = len([l for l in lines if l.strip()])
        structured_content_ratio = structured_lines / total_lines if total_lines > 0 else 0.0
        signals["structured_content_ratio"] = min(structured_content_ratio, 1.0)

        # 5. Emphasis markers (bold, code, numbered lists, etc.)
        emphasis_count = (
            len(re.findall(r'\*\*\w+\*\*', prompt)) +  # **bold**
            len(re.findall(r'`[^`]+`', prompt)) +  # `code`
            len(re.findall(r'#+\s', prompt))  # # headers
        )
        signals["emphasis_markers"] = emphasis_count

        # Compute score
        # Directives: 0-25 (max 10 directives = 25 points)
        directive_score = min(directive_count * 2.5, 25)

        # Acceptance criteria: 20 points if present
        criteria_score = 20 if has_acceptance_criteria else 0

        # File specificity: 20 points (0.5+ specificity = full points)
        specificity_score = file_specificity * 20

        # Structured content: 20 points (50%+ structured = full points)
        structure_score = min(structured_content_ratio * 40, 20)

        # Emphasis markers: 15 points (5+ = full points)
        emphasis_score = min(emphasis_count * 3, 15)

        total_score = directive_score + criteria_score + specificity_score + structure_score + emphasis_score
        total_score = min(total_score, 100)

        # Determine level
        if total_score >= 85:
            level = "Excellent"
        elif total_score >= 70:
            level = "High"
        elif total_score >= 50:
            level = "Med"
        else:
            level = "Low"

        return {
            "level": level,
            "score": int(total_score),
            "signals": signals
        }


class FileScopeAnalyzer:
    """Analyze file scope: declared vs actual touched files."""

    @staticmethod
    def extract_intended_scope(dispatch_prompt: str) -> List[str]:
        """Extract file patterns from dispatch prompt.

        Looks for patterns like:
        - ui/web/src/components/*.tsx
        - NEW ui/wave_context.py
        - ui/serve.py (small edits)
        - Modified: file.py

        Returns list of file paths/patterns mentioned in the prompt.
        """
        if not dispatch_prompt:
            return []

        patterns = [
            r'(?:ui|src|lib|tests)\/[^\s\n:]+',  # paths with slashes
            r'(?:NEW|MODIFIED|TOUCHED|CHANGED)?\s+(?:ui|src|lib|tests)\/\S+',  # explicit markers
            r'Files[:\s]+(?:.*?)(?:\n|$)',  # "Files: " section
        ]

        files = []
        for pattern in patterns:
            matches = re.findall(pattern, dispatch_prompt, re.IGNORECASE | re.MULTILINE)
            files.extend(matches)

        # Clean up and deduplicate
        files = [
            re.sub(r'^(?:NEW|MODIFIED|CHANGED|TOUCHED)?\s*', '', f.strip())
            for f in files
        ]
        return sorted(set(files))

    @staticmethod
    def _extract_actual_files_from_transcript(transcript_path: Path) -> List[str]:
        """Extract files actually written/edited from transcript NDJSON.

        Scans transcript for Write and Edit tool-use records; extracts file_path
        from each. Returns deduplicated sorted list of touched files.
        Reuses _redact_secrets for secret-safety (only extracts paths, not content).

        Returns:
            List of file paths actually touched (Write/Edit tool-use)
        """
        files = set()

        if not transcript_path.exists():
            return []

        try:
            with open(transcript_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue

                    if not isinstance(obj, dict):
                        continue

                    # Look for tool_use blocks in message.content
                    msg = obj.get("message")
                    if not isinstance(msg, dict):
                        continue

                    content = msg.get("content")
                    if not isinstance(content, list):
                        continue

                    for block in content:
                        if not isinstance(block, dict):
                            continue

                        btype = block.get("type")
                        if btype not in ("tool_use",):
                            continue

                        tool_name = block.get("name", "").lower()
                        if tool_name not in ("write", "edit"):
                            continue

                        # Extract file_path from tool input
                        tool_input = block.get("input")
                        if not isinstance(tool_input, dict):
                            continue

                        file_path = tool_input.get("file_path")
                        if isinstance(file_path, str) and file_path.strip():
                            files.add(file_path.strip())

        except (OSError, IOError):
            pass

        return sorted(files)

    @staticmethod
    def analyze_scope(dispatch_prompt: str, agent_id: str) -> Dict[str, Any]:
        """Analyze file scope for a dispatch.

        Computes REAL data: intended files from prompt, actual files from transcript
        Write/Edit tool-use records. Returns:
            {
                "intended_files": [path, ...],
                "actual_files": [path, ...],
                "coverage": float (0-1),
                "drift": {"only_intended": [...], "only_actual": [...]}
            }
        """
        intended = FileScopeAnalyzer.extract_intended_scope(dispatch_prompt)

        # Extract REAL actual files from transcript
        # If transcript doesn't exist or can't be read, actual_files will be empty
        actual = []
        try:
            transcript_path, err = agents._resolve_transcript_path(agent_id)
            if err is None:
                actual = FileScopeAnalyzer._extract_actual_files_from_transcript(transcript_path)
        except (TypeError, ValueError, AttributeError):
            # _resolve_transcript_path may fail if agent_id is invalid
            actual = []

        coverage = 0.0
        if intended:
            matched = sum(1 for i in intended if any(a in i or i in a for a in actual))
            coverage = matched / len(intended) if intended else 0.0

        drift = {
            "only_intended": [f for f in intended if not any(a in f for a in actual)],
            "only_actual": [f for f in actual if not any(i in f for i in intended)]
        }

        return {
            "intended_files": intended,
            "actual_files": actual,
            "coverage": coverage,
            "drift": drift
        }


def get_spec_sharpness(agent_id: str) -> Optional[Dict[str, Any]]:
    """Get spec sharpness indicator for a dispatch (C1).

    Args:
        agent_id: Agent ID to analyze

    Returns:
        Spec sharpness score dict, or None if agent not found
    """
    try:
        prompt = agents.extract_agent_dispatch_prompt(agent_id)
        if isinstance(prompt, dict) and "error" in prompt:
            return None

        if not prompt:
            return None

        return SpecSharpnessScore.score_prompt(prompt)
    except Exception:
        return None


def get_file_scope(agent_id: str) -> Optional[Dict[str, Any]]:
    """Get file-scope visualization data for a dispatch (C2).

    Args:
        agent_id: Agent ID to analyze

    Returns:
        File scope analysis dict, or None if agent not found
    """
    try:
        prompt = agents.extract_agent_dispatch_prompt(agent_id)
        if isinstance(prompt, dict) and "error" in prompt:
            return None

        if not prompt:
            return None

        # Even if prompt exists, return the analysis (may have empty actual_files)
        return FileScopeAnalyzer.analyze_scope(prompt, agent_id)
    except Exception as e:
        # Only return None on genuine errors (bad agent_id)
        # For missing transcripts, return with empty actual_files
        if isinstance(e, dict) and "error" in str(e):
            return None
        # Otherwise return default structure with intended files from prompt
        try:
            prompt = agents.extract_agent_dispatch_prompt(agent_id)
            if isinstance(prompt, dict) and "error" not in prompt and prompt:
                return FileScopeAnalyzer.analyze_scope(prompt, agent_id)
        except Exception:
            pass
        return None


def get_first_try_rate() -> Dict[str, Any]:
    """Get first-try success board data (C3).

    Analyzes all agent transcripts to compute % of dispatches needing no repair,
    broken down by domain and lane.

    SOUND STRUCTURED SIGNAL: A dispatch "needed repair" only if the TRANSCRIPT
    contains MULTIPLE DISTINCT DISPATCH PROMPTS (indicated by 2+ top-level user
    messages). This is a direct structural signal reflecting actual re-dispatch,
    not prose parsing (avoids false positives on "error" in file names, log lines,
    or prompt text like "never retry").

    Returns honest empty state if no transcripts found — never fabricated counts
    that would mislead about success rates.

    Returns:
        {
            "available": bool,
            "domains": {...},
            "lanes": {...},
            "overall": {"first_try": int, "needed_repair": int, "rate": float (0-1)}
        }
    """
    domains = {}
    lanes = {}
    overall_first_try = 0
    overall_repair = 0
    total_dispatches = 0

    # Scan all agent transcripts from TRANSCRIPTS_ROOT
    transcripts_root = config.TRANSCRIPTS_ROOT
    if not transcripts_root or not transcripts_root.exists():
        # Honest empty state: no transcripts found
        return {
            "available": False,
            "reason": "no transcripts found",
            "domains": {},
            "lanes": {},
            "overall": {
                "first_try": 0,
                "needed_repair": 0,
                "rate": 0.0
            }
        }

    try:
        # Find all agent-*.jsonl files
        transcript_files = list(transcripts_root.glob("**/agent-*.jsonl"))
        if not transcript_files:
            # Honest empty state: no transcripts found
            return {
                "available": False,
                "reason": "no transcripts found",
                "domains": {},
                "lanes": {},
                "overall": {
                    "first_try": 0,
                    "needed_repair": 0,
                    "rate": 0.0
                }
            }

        for transcript_file in transcript_files:
            # SOUND STRUCTURED SIGNAL: Count dispatch prompts in transcript
            # Multiple dispatches (2+ top-level user messages) = repair occurred
            dispatch_count = 0

            try:
                with open(transcript_file, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except (json.JSONDecodeError, ValueError):
                            continue

                        if not isinstance(obj, dict):
                            continue

                        # Count top-level dispatch prompts: user messages at root level
                        # Each new top-level user message indicates a new dispatch
                        msg = obj.get("message")
                        if isinstance(msg, dict) and msg.get("role") == "user":
                            # Check if this looks like a dispatch prompt (contains task description)
                            content = msg.get("content")
                            if isinstance(content, str) and len(content) > 50:  # Dispatch prompts are typically long
                                dispatch_count += 1

            except (OSError, IOError):
                pass

            # SOUND CLASSIFICATION:
            # - 1 dispatch = first_try (no repair needed)
            # - 2+ dispatches = needed_repair (orchestration re-dispatched the agent)
            needed_repair = dispatch_count >= 2
            total_dispatches += 1

            # Extract domain from transcript path
            domain = "unclassified"
            lane = "unclassified"

            try:
                filename = transcript_file.name
                # agent-wave14-driver-repair-abc123.jsonl -> "driver"
                parts = filename.split("-")
                if len(parts) > 2:
                    potential_domain = parts[2]
                    if potential_domain in ("ui", "driver", "tools", "bench", "state_store"):
                        domain = potential_domain

                # Try to detect lane from transcript content
                try:
                    with open(transcript_file, 'r', encoding='utf-8', errors='replace') as f:
                        for line in f:
                            if "ranked" in line.lower():
                                lane = "ranked"
                                break
                            elif "in-progress" in line.lower():
                                lane = "in-progress"
                                break
                except (OSError, IOError):
                    pass

            except (IndexError, AttributeError):
                pass

            # Update domain stats
            if domain not in domains:
                domains[domain] = {"first_try": 0, "needed_repair": 0}

            if needed_repair:
                domains[domain]["needed_repair"] += 1
                overall_repair += 1
            else:
                domains[domain]["first_try"] += 1
                overall_first_try += 1

            # Update lane stats
            if lane not in lanes:
                lanes[lane] = {"first_try": 0, "needed_repair": 0}

            if needed_repair:
                lanes[lane]["needed_repair"] += 1
            else:
                lanes[lane]["first_try"] += 1

    except (OSError, IOError):
        pass

    # Compute rates
    for domain in domains:
        total = domains[domain]["first_try"] + domains[domain]["needed_repair"]
        domains[domain]["rate"] = domains[domain]["first_try"] / total if total > 0 else 0.0

    for lane in lanes:
        total = lanes[lane]["first_try"] + lanes[lane]["needed_repair"]
        lanes[lane]["rate"] = lanes[lane]["first_try"] / total if total > 0 else 0.0

    overall_total = overall_first_try + overall_repair
    overall_rate = overall_first_try / overall_total if overall_total > 0 else 0.0

    return {
        "available": total_dispatches > 0,
        "domains": domains,
        "lanes": lanes,
        "overall": {
            "first_try": overall_first_try,
            "needed_repair": overall_repair,
            "rate": overall_rate
        }
    }
