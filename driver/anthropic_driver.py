#!/usr/bin/env python3
"""AnthropicDriver -- AgentDriver for the Anthropic API messages endpoint.

Minimal backend for bench experiments: Claude models via HTTP API (not Claude Code).
Reuses the Phase 2 CodexDriver execution contract (file injection, JSON schema,
validation, full-file replacement, ownership enforcement).

TRANSPORT SEAM
--------------
AnthropicDriver.__init__ takes an optional `transport` callable (default =
default_anthropic_transport). This injectable seam keeps tests offline: tests
pass a FakeTransport; production uses the real urllib transport reading
ANTHROPIC_API_KEY from env.

VERIFICATION TIER
-----------------
Tier 1 (same as Claude Code): tool_use_accuracy ~0.95, no native JSON schema
validation but close-to-perfect reliability. Honest reporting.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, Optional

from proc_util import run_shell_bounded

from agent_driver import (
    AgentDriver,
    CommandResult,
    DriverCapabilities,
    ROLE_SETUP,
    ROLE_VERIFY,
    ROLE_WORKER,
    WorkerRequest,
    WorkerResult,
    WorkerStatus,
    WORKER_DONE,
    WORKER_FAILED,
    WORKER_RUNNING,
    WORKER_UNKNOWN,
)

# Import the transport layer.
try:
    from anthropic_transport import default_anthropic_transport
except ImportError:
    default_anthropic_transport = None

# Reuse Codex schema and validation.
try:
    from codex_driver import WORKER_PATCH_SCHEMA, _validate_patch_schema
except ImportError:
    # Fallback schema if codex_driver unavailable.
    WORKER_PATCH_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string"},
                        "contents": {"type": "string"},
                    },
                    "required": ["path", "contents"],
                },
            },
            "summary": {"type": "string"},
            "done": {"type": "boolean"},
        },
        "required": ["files", "summary", "done"],
    }

    def _validate_patch_schema(obj: dict, schema: dict = None) -> bool:
        """Lightweight schema validator (copied from codex_driver)."""
        if schema is None:
            schema = WORKER_PATCH_SCHEMA

        if not isinstance(obj, dict):
            raise ValueError("expected object, got " + type(obj).__name__)

        required = schema.get("required", [])
        for key in required:
            if key not in obj:
                raise ValueError(f"missing required field: {key}")

        additional = schema.get("additionalProperties", True)
        if not additional:
            schema_keys = set(schema.get("properties", {}).keys())
            obj_keys = set(obj.keys())
            extra = obj_keys - schema_keys
            if extra:
                raise ValueError(f"unexpected fields: {extra}")

        if "files" in obj:
            files = obj["files"]
            if not isinstance(files, list):
                raise ValueError("'files' must be array")
            for i, file_entry in enumerate(files):
                if not isinstance(file_entry, dict):
                    raise ValueError(f"files[{i}] must be object")
                if "path" not in file_entry or "contents" not in file_entry:
                    raise ValueError(
                        f"files[{i}] must have 'path' and 'contents' keys"
                    )

        return True


# Abstract-role -> Anthropic model mapping. Direct Claude API.
_DEFAULT_MODEL_MAP = {
    ROLE_WORKER: "claude-haiku-4-5-20251001",
    ROLE_SETUP: "claude-opus-4-1-20250805",
    ROLE_VERIFY: "claude-opus-4-1-20250805",
}

# Known Claude models (for validation; bench will override).
CLAUDE_MODELS = {
    "claude-haiku-4-5-20251001",
    "claude-opus-4-1-20250805",
    "claude-opus-4-1-20250527",
    "claude-3-5-sonnet-20241022",
    "claude-3-opus-20240229",
}


class AnthropicDriver(AgentDriver):
    """AgentDriver for Anthropic messages API.

    Tier 1: reports high tool-use accuracy (same as Claude Code).
    Uses the CodexDriver file-injection model: full-file replacement via JSON.
    """

    def __init__(
        self,
        model_map: Optional[Dict[str, str]] = None,
        max_owned_bytes: int = 200000,
        max_retries: int = 2,
        timeout_s: float = 120.0,
        transport=None,
    ):
        """Initialize an Anthropic driver.

        Args:
            model_map: optional {role -> model_id}; defaults to _DEFAULT_MODEL_MAP.
            max_owned_bytes: max file size per owned file.
            max_retries: max JSON validation retry attempts.
            timeout_s: HTTP timeout.
            transport: optional injectable transport callable (for testing).
        """
        self.model_map = model_map or _DEFAULT_MODEL_MAP
        self.max_owned_bytes = max_owned_bytes
        self.max_retries = max_retries
        self.timeout_s = timeout_s

        # Transport defaults to the real Anthropic transport; tests inject FakeTransport.
        if transport is None:
            if default_anthropic_transport is None:
                raise RuntimeError(
                    "Could not import anthropic_transport; cannot create real transport. "
                    "Pass transport= or ensure anthropic_transport.py is available."
                )
            self.transport = default_anthropic_transport()
        else:
            self.transport = transport

        # In-memory worker registry (same pattern as CodexDriver).
        self._workers = {}
        self._next_worker_id = 0
        self._tokens_spent = 0

    def probe_capabilities(self) -> DriverCapabilities:
        """Report Tier 1: high accuracy, structured output, no native fs/shell."""
        return DriverCapabilities(
            parallel_dispatch=False,
            worker_filesystem_access=False,
            worker_shell_access=False,
            structured_output=True,
            worktree_isolation=False,
            recommended_verification_tier=1,
            tool_use_accuracy=0.95,
            estimated_tokens_per_work_unit=8000,
        )

    def resolve_model(self, role: str) -> str:
        """Map a role to a concrete model id."""
        return self.model_map.get(role, self.model_map.get(ROLE_WORKER, "claude-haiku-4-5-20251001"))

    def dispatch_worker(self, request: WorkerRequest) -> WorkerResult:
        """Dispatch via Anthropic messages API.

        Injects file contents, requests JSON patch, validates, writes files.
        """
        worker_id = f"anthropic-{self._next_worker_id}"
        self._next_worker_id += 1

        try:
            # Load owned files with size check.
            owned_path_to_content = {}
            for fpath in request.owned_files:
                full_path = Path(request.workdir) / fpath
                if full_path.exists():
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    if len(content.encode("utf-8")) > self.max_owned_bytes:
                        return WorkerResult(
                            worker_id=worker_id,
                            ok=False,
                            status=WORKER_FAILED,
                            files_written=(),
                            structured={},
                            error=f"File {fpath} exceeds max_owned_bytes={self.max_owned_bytes}",
                        )
                    owned_path_to_content[fpath] = content
                else:
                    owned_path_to_content[fpath] = ""

            # Build the prompt with file contents.
            file_context = "\n".join(
                f"=== {fpath} ===\n{owned_path_to_content[fpath]}"
                for fpath in request.owned_files
            )
            full_prompt = f"""{request.prompt}

Owned files you can modify:
{file_context}

Respond with a JSON object containing:
- "files": array of {{"path": "...", "contents": "..."}} for modified files
- "summary": brief description of changes
- "done": boolean indicating if the task is complete"""

            # Prepare the Anthropic messages API request.
            payload = {
                "model": self.resolve_model(ROLE_WORKER),
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": full_prompt,
                    }
                ],
            }

            # Attempt to get a valid response (with retries).
            patch_dict = None
            last_error = None
            for attempt in range(self.max_retries + 1):
                try:
                    response = self.transport(payload)
                    # Extract response content.
                    if "content" not in response or not response["content"]:
                        raise ValueError("No content in response")
                    content = response["content"][0].get("text", "")
                    if not content:
                        raise ValueError("No text in response content")

                    # Try to parse JSON.
                    patch_dict = json.loads(content)
                    _validate_patch_schema(patch_dict)

                    # Track tokens.
                    if "usage" in response:
                        tokens = (
                            response["usage"].get("input_tokens", 0)
                            + response["usage"].get("output_tokens", 0)
                        )
                        self._tokens_spent += tokens

                    break  # Success!
                except (json.JSONDecodeError, ValueError) as exc:
                    last_error = str(exc)
                    if attempt < self.max_retries:
                        continue
                    # Final attempt failed.
                    return WorkerResult(
                        worker_id=worker_id,
                        ok=False,
                        status=WORKER_FAILED,
                        files_written=(),
                        structured={},
                        error=f"JSON validation failed after {self.max_retries + 1} attempts: {last_error}",
                    )

            if patch_dict is None:
                return WorkerResult(
                    worker_id=worker_id,
                    ok=False,
                    status=WORKER_FAILED,
                    files_written=(),
                    structured={},
                    error="No valid patch produced",
                )

            # Write files.
            files_written = ()
            files = patch_dict.get("files", [])
            for file_spec in files:
                fpath = file_spec.get("path", "")
                contents = file_spec.get("contents", "")

                # Ownership check.
                if fpath not in request.owned_files:
                    return WorkerResult(
                        worker_id=worker_id,
                        ok=False,
                        status=WORKER_FAILED,
                        files_written=files_written,
                        structured={},
                        error=f"Path {fpath} not in owned_files",
                    )

                # Write the file.
                full_path = Path(request.workdir) / fpath
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(contents, encoding="utf-8")
                files_written = files_written + (fpath,)

            # Store worker state and return success.
            self._workers[worker_id] = {
                "status": WORKER_DONE,
                "patch": patch_dict,
                "timestamp": time.time(),
            }

            return WorkerResult(
                worker_id=worker_id,
                ok=True,
                status=WORKER_DONE,
                files_written=files_written,
                structured=patch_dict,
                tokens_spent=self._tokens_spent,
            )

        except Exception as exc:
            return WorkerResult(
                worker_id=worker_id,
                ok=False,
                status=WORKER_FAILED,
                files_written=(),
                structured={},
                error=str(exc),
            )

    def worker_status(self, worker_id: str) -> WorkerStatus:
        """Check worker status from in-memory registry."""
        if worker_id not in self._workers:
            return WorkerStatus(state=WORKER_UNKNOWN)
        state = self._workers[worker_id]["status"]
        return WorkerStatus(state=state)

    def run_command(
        self,
        command: str,
        cwd: str = ".",
        shell: bool = False,
    ) -> CommandResult:
        """Run a command (orchestrator-side only)."""
        stdout, stderr, exit_code = run_shell_bounded(
            command,
            cwd=cwd,
            timeout_s=self.timeout_s,
            shell=shell,
        )
        return CommandResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    def get_tokens_spent(self) -> Optional[int]:
        """Return cumulative tokens spent."""
        return self._tokens_spent if self._tokens_spent > 0 else None
