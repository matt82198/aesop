#!/usr/bin/env python3
"""
Aesop UI — context quality analysis helpers (wave-30 context-engineering lane C).

Provides read-only analysis of dispatch prompts and agent transcripts to surface
context quality indicators: spec sharpness (prompt quality signals), file-scope
visualization (intended vs actual), and first-try success rates.

All functions are read-only; no orchestration changes.
"""
import json
import re
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
    def analyze_scope(dispatch_prompt: str, agent_id: str) -> Dict[str, Any]:
        """Analyze file scope for a dispatch.

        Returns:
            {
                "intended_files": [path, ...],
                "actual_files": [path, ...],
                "coverage": float (0-1),
                "drift": [files_only_in_intended, files_only_in_actual]
            }
        """
        intended = FileScopeAnalyzer.extract_intended_scope(dispatch_prompt)

        # For now, actual files would come from transcript analysis
        # This is a placeholder; in production, we'd parse the transcript
        # to see which files were actually opened/modified
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

        return FileScopeAnalyzer.analyze_scope(prompt, agent_id)
    except Exception:
        return None


def get_first_try_rate() -> Dict[str, Any]:
    """Get first-try success board data (C3).

    Analyzes all agent transcripts to compute % of dispatches needing no repair,
    broken down by domain and lane.

    Returns:
        {
            "domains": {
                "domain_name": {
                    "first_try": int,
                    "needed_repair": int,
                    "rate": float (0-1)
                },
                ...
            },
            "lanes": {
                "lane_name": {
                    "first_try": int,
                    "needed_repair": int,
                    "rate": float (0-1)
                },
                ...
            },
            "overall": {
                "first_try": int,
                "needed_repair": int,
                "rate": float (0-1)
            }
        }
    """
    # Placeholder implementation; in production, this would:
    # 1. Scan all agent transcripts
    # 2. Detect repair markers (re-run, retry, fail-then-pass)
    # 3. Extract domain/lane from dispatch prompt
    # 4. Compute rates per domain/lane

    return {
        "domains": {},
        "lanes": {},
        "overall": {
            "first_try": 0,
            "needed_repair": 0,
            "rate": 0.0
        }
    }
