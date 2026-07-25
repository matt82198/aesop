#!/usr/bin/env python3
"""OrchestratorDriver — the adjudication seam for orchestrator decision-making.

Mirrors the AgentDriver pattern: allows aesop's orchestrator logic to be
swapped across backends (Claude, OpenAI-compatible, Codex) without changing
the decision-making algorithm. The orchestrator is a set of judgment calls
(rank backlog, adjudicate findings, review diffs, synthesize briefs, repair
decisions, final-catch) — this seam isolates those decisions so the backend
can be replaced.

The orchestrator never calls backend-specific APIs or Workflow tools directly;
it dispatches through OrchestratorDriver.decide(decision_type, context_pack, schema).

Fail-safe semantics: after retries exhausted, return {'verdict': 'DECISION_FAILED', ...}
— NEVER fabricate a passing verdict. The cardinal rule (never green unless proven)
applies equally to the orchestrator seat.

stdlib-only, ASCII-only, Windows + Linux safe (concrete backends own their SDKs).
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Add driver/ to sys.path so we can import agent_driver (mirrors test pattern).
DRIVER_DIR = Path(__file__).resolve().parent
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from agent_driver import AgentDriver, CommandResult, DriverCapabilities
from context_pack import ContextPack
from orchestrator_backend import OrchestratorBackend


class DecisionFailed(Exception):
    """Raised when a decision cannot be made after retries exhausted."""

    pass


class OrchestratorDriver:
    """Backend-agnostic orchestrator decision-making seam.

    Wraps an OrchestratorBackend and uses it to make structured judgments about
    orchestration: ranking backlog items, adjudicating audit findings,
    reviewing diffs, and deciding merge eligibility.

    The backend is configured once at construction; all decisions route
    through the same backend (no swapping mid-wave). Decisions enforce
    structured output (JSON schema) with bounded retry on malformed output.

    Fail-safe: malformed output → retry (<=2 times) → DECISION_FAILED.
    Never fabricate a passing verdict; the orchestrator's judgment is
    advisory but not falsifiable.
    """

    def __init__(
        self,
        backend: OrchestratorBackend,
        schema_dir: Optional[str] = None,
        max_retries: int = 2,
    ):
        """Initialize an OrchestratorDriver.

        Args:
            backend: An OrchestratorBackend instance (openai-compatible, etc.).
            schema_dir: Optional path to a directory containing decision schemas
                       (decisions/<type>.schema.json). If provided, schemas are
                       loaded and used to validate decisions. Absent schemas are
                       treated as optional (minimal validation enforced).
            max_retries: Maximum retry attempts on malformed output (default 2).
                        Total attempts = 1 + max_retries.
        """
        self.backend = backend
        self.schema_dir = schema_dir
        self.max_retries = max_retries
        self._schemas = {}  # Cache loaded schemas.

    def decide(
        self,
        decision_type: str,
        context_pack: ContextPack,
        schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make a structured decision using the orchestrator backend.

        The orchestrator seat calls this for every judgment call:
          - rank_backlog (sort items by priority)
          - adjudicate_findings (decide severity and action)
          - review_diff (approve/request-changes on a code diff)
          - synthesize_brief (summarize wave status)
          - repair_decision (is a repair attempt likely to fix the bug?)
          - final_catch (is this safe to ship?)

        Behavior:
          1. Build a decision prompt framing the orchestrator's role + context.
          2. Call the backend (via resolve_model + transport).
          3. Parse and validate JSON against schema (if provided).
          4. On malformed output, retry (<=max_retries times).
          5. After retries exhausted, return DECISION_FAILED (never green).

        Args:
            decision_type: Name of the decision class
                          (e.g., 'rank_backlog', 'adjudicate_findings').
                          Used to locate schema (if schema_dir is set) and
                          frame the prompt.
            context_pack: ContextPack with the file-brain snapshot.
            schema: Optional JSON schema dict for output validation.
                   If None and schema_dir is set, attempts to load
                   decisions/<type>.schema.json. Absence of a schema
                   means minimal validation (must have 'verdict' and
                   'evidence' keys); the decision is still validated
                   structurally but not against a detailed schema.

        Returns:
            A dict with at least:
              {
                "verdict": "APPROVED" | "REJECTED" | "NEEDS_CHANGES" | "DECISION_FAILED",
                "evidence": "Reasoning (citations to context pack sources).",
                "decision_type": str,
                "retry_count": int,
                "schema_validated": bool,  # True if validated against schema
              }
            Additional fields depend on decision_type (set by schema or
            backend's reasoning).

        Raises:
            Nothing. decide() NEVER raises (P1 fail-safe): every failure path
            (backend error, malformed JSON, invalid structure, unexpected
            exception) returns a DECISION_FAILED dict after retries exhausted.
            The DecisionFailed exception class is retained for backward
            compatibility only; no code path raises it.
        """
        # Load schema if not provided and schema_dir is set.
        if schema is None and self.schema_dir:
            schema = self._load_schema(decision_type)

        # Build the decision prompt.
        prompt = _build_decision_prompt(decision_type, context_pack)

        # Dispatch and retry on malformed output.
        for attempt in range(1 + self.max_retries):
            try:
                # Call the backend with the built prompt and schema.
                # decide_call() returns raw text; we parse it.
                try:
                    response_text = self.backend.decide_call(prompt, schema=schema)
                except Exception as backend_error:
                    # Backend call failed (network, API error, etc.).
                    if attempt < self.max_retries:
                        continue
                    return {
                        "verdict": "DECISION_FAILED",
                        "evidence": f"Backend error after {attempt + 1} attempts: {backend_error}",
                        "decision_type": decision_type,
                        "retry_count": attempt,
                        "schema_validated": False,
                    }

                # Parse output as JSON.
                try:
                    result = json.loads(response_text)
                except json.JSONDecodeError as e:
                    if attempt < self.max_retries:
                        continue
                    return {
                        "verdict": "DECISION_FAILED",
                        "evidence": f"Malformed JSON after {attempt + 1} attempts: {e}",
                        "decision_type": decision_type,
                        "retry_count": attempt,
                        "schema_validated": False,
                    }

                # Validate structure (always required).
                if not self._validate_decision(result, schema):
                    if attempt < self.max_retries:
                        continue
                    return {
                        "verdict": "DECISION_FAILED",
                        "evidence": "Invalid decision structure (missing required keys)",
                        "decision_type": decision_type,
                        "retry_count": attempt,
                        "schema_validated": False,
                    }

                # Success: return the decision with metadata.
                result.setdefault("decision_type", decision_type)
                result.setdefault("retry_count", attempt)
                # schema_validated is True only if a schema was provided/loaded.
                result.setdefault("schema_validated", schema is not None)
                return result

            except Exception as e:
                # Unexpected exception (should not happen if logic above is correct).
                # P1 FIX: Return fail-safe dict, never raise.
                if attempt < self.max_retries:
                    continue
                return {
                    "verdict": "DECISION_FAILED",
                    "evidence": f"Unexpected error after {attempt + 1} attempts: {e}",
                    "decision_type": decision_type,
                    "retry_count": attempt,
                    "schema_validated": False,
                }

        # Exhausted all retries without success.
        return {
            "verdict": "DECISION_FAILED",
            "evidence": "Exhausted all retry attempts",
            "decision_type": decision_type,
            "retry_count": self.max_retries,
            "schema_validated": False,
        }

    def _load_schema(self, decision_type: str) -> Optional[Dict[str, Any]]:
        """Load a decision schema from the schema directory.

        Schemas are optional; absence is not an error. Stored in
        decisions/<type>.schema.json under the schema_dir.

        Args:
            decision_type: The decision type (e.g., 'rank_backlog').

        Returns:
            The parsed schema dict, or None if not found.
        """
        if not self.schema_dir:
            return None

        if decision_type in self._schemas:
            return self._schemas[decision_type]

        schema_path = (
            Path(self.schema_dir)
            / "decisions"
            / f"{decision_type}.schema.json"
        )
        if not schema_path.exists():
            self._schemas[decision_type] = None
            return None

        try:
            with open(schema_path, encoding="utf-8") as f:
                schema = json.load(f)
            self._schemas[decision_type] = schema
            return schema
        except (OSError, json.JSONDecodeError):
            # Schema load failure is logged but not fatal.
            self._schemas[decision_type] = None
            return None

    def _validate_decision(
        self,
        result: Any,
        schema: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Validate a decision result against schema (if present) or minimally.

        Minimal validation (always enforced):
          - result must be a dict.
          - must have 'verdict' key (string, not "DECISION_FAILED").
          - must have 'evidence' key (array of >=1 non-empty strings).

        With schema: also validates verdict enum and required fields.

        Args:
            result: Parsed decision result.
            schema: Optional JSON schema dict.

        Returns:
            True if valid; False otherwise.
        """
        if not isinstance(result, dict):
            return False

        # Verdict must be a string.
        if not isinstance(result.get("verdict"), str):
            return False

        # P2 FIX: Reject "DECISION_FAILED" as a model-provided verdict
        # (reserved for orchestrator's own fail-safe).
        if result.get("verdict") == "DECISION_FAILED":
            return False

        # Evidence must be an array of non-empty strings with minItems >= 1.
        evidence = result.get("evidence")
        if not isinstance(evidence, list):
            return False
        if len(evidence) < 1:
            return False
        if not all(isinstance(item, str) and len(item) > 0 for item in evidence):
            return False

        # If schema is provided, validate verdict enum and required fields.
        if schema:
            required = schema.get("required", [])
            # Check all required fields are present.
            for field in required:
                if field not in result:
                    return False

            # Validate verdict enum if schema defines it.
            verdict_schema = schema.get("properties", {}).get("verdict", {})
            allowed_verdicts = verdict_schema.get("enum", [])
            # P3 FIX: Fail-closed on empty enum (no verdict is valid).
            if allowed_verdicts is not None:
                # If enum exists (even if empty), verdict must be in it.
                if result.get("verdict") not in allowed_verdicts:
                    return False

            # P2 FIX: Validate confidence against schema bounds if defined.
            if "confidence" in result:
                confidence = result.get("confidence")
                if isinstance(confidence, (int, float)):
                    confidence_schema = schema.get("properties", {}).get("confidence", {})
                    minimum = confidence_schema.get("minimum")
                    maximum = confidence_schema.get("maximum")
                    if minimum is not None and confidence < minimum:
                        return False
                    if maximum is not None and confidence > maximum:
                        return False

        return True


def _sanitize_label_name(name: str) -> str:
    """Sanitize label names to prevent prompt injection via newlines/control chars.

    Strips newlines, carriage returns, and control characters that could break
    the label syntax and inject fake instructions. The label channel is
    non-authoritative (seam for future localization); legitimate content values
    are unchanged.

    Args:
        name: The label/source name from context pack.

    Returns:
        Sanitized name with control chars and newlines removed.
    """
    # Strip newlines and control chars; keep legitimate alphanumerics, spaces, hyphens, underscores.
    return "".join(
        c for c in name
        if c.isprintable() and c not in ("\n", "\r", "\t", "[", "]")
    )


def _build_decision_prompt(decision_type: str, context_pack: ContextPack) -> str:
    """Build the system + user prompt for a decision.

    Frames the orchestrator's role and context, citing the file brain.

    Args:
        decision_type: The decision type (e.g., 'rank_backlog').
        context_pack: The context pack with file-brain snapshot.

    Returns:
        The complete prompt (system framing + context + decision request).
    """
    # System framing: you are the orchestrator adjudication seat.
    system = f"""You are the orchestrator adjudication seat for aesop, an autonomous
development harness. Your role is to make structured decisions that require human
judgment: ranking work items, adjudicating audit findings, reviewing code changes,
and deciding merge eligibility.

Decision type: {decision_type}

CARDINAL RULE: Verdicts require evidence citations from the context. Never invent
findings or assume facts not in the file brain. Your output is JSON with:
  {{"verdict": "<enum-value>", "evidence": ["citation 1", "citation 2", ...], "confidence": 0.0-1.0, ...}}

Required structure:
  - verdict: string enum value specific to this decision type
  - evidence: array of >=1 non-empty citation strings (mandatory)
  - confidence: optional float 0.0-1.0 indicating confidence in the verdict"""

    # User context: the file brain snapshot. Do NOT re-truncate here — the pack was
    # already size-bounded at build time; clipping to 500 again would silently
    # starve the model of context it was given.
    # P2/P3 FIX: Sanitize source names to prevent prompt injection.
    context_text = "\n\n".join(
        f"[{_sanitize_label_name(source)}]:\n{text}"
        for source, text in context_pack.content.items()
    )

    # Evidence channel: the finding under adjudication + cited code/repro. SEPARATE
    # from content and MUST be rendered — it carries the actual thing to decide on.
    # Rendering only content (the prior bug) left the model with no finding to judge,
    # producing spurious 'undetermined' verdicts.
    evidence = getattr(context_pack, "evidence", None) or {}
    if evidence:
        # P2/P3 FIX: Sanitize evidence label names too.
        evidence_text = "\n\n".join(
            f"[{_sanitize_label_name(name)}]:\n{text}" for name, text in evidence.items()
        )
        evidence_block = (
            "Evidence (the finding to adjudicate + supporting citations):\n"
            f"{evidence_text}\n\n---\n\n"
        )
    else:
        evidence_block = ""

    user = f"""File brain (orchestrator context):
{context_text}

---

{evidence_block}Manifest (what was included/truncated):
{json.dumps(context_pack.manifest, indent=2)}

---

Make your decision as JSON (response must include verdict, evidence array, and optional confidence):
"""

    return f"{system}\n\n{user}"
