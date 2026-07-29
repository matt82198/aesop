#!/usr/bin/env python3
"""AnthropicDriver -- AgentDriver for the Anthropic API messages endpoint.

Minimal backend for bench experiments: Claude models via HTTP API (not Claude Code).
Reuses the Phase 2 CodexDriver execution contract but uses FORCED TOOL CALLS
instead of prose JSON to avoid Fable/Opus API refusals on structured requests.

TRANSPORT SEAM & TOOL-CALL CHANNEL
----------------------------------
- Dispatches with forced tool_choice: submit_work
- Parses tool_use blocks (not prose JSON)
- Refusals (stop_reason=refusal) handled gracefully -> error status
- Multi-turn repair loop intact: failure output appended to user message,
  assistant's tool_use block is parsed, next attempt includes the prior failure

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


def _build_submit_work_tool() -> dict:
    """Build the submit_work tool definition for forced tool calls.

    Matches the WORKER_PATCH_SCHEMA structure so the model can return
    structured work output via tool_use instead of prose JSON.
    """
    return {
        "name": "submit_work",
        "description": "Submit the completed work with modified files and status",
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "description": "Array of files to modify (full replacement)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path relative to repo root",
                            },
                            "contents": {
                                "type": "string",
                                "description": "Full new contents of the file",
                            },
                        },
                        "required": ["path", "contents"],
                    },
                },
                "summary": {
                    "type": "string",
                    "description": "Brief summary of changes made",
                },
                "done": {
                    "type": "boolean",
                    "description": "Whether the task is complete",
                },
            },
            "required": ["files", "summary", "done"],
        },
    }


class AnthropicDriver(AgentDriver):
    """AgentDriver for Anthropic messages API with forced tool-call dispatch.

    Tier 1: reports high tool-use accuracy (same as Claude Code).
    Uses forced tool calls (submit_work) for structured work output.
    Refusal-safe: gracefully handles stop_reason=refusal from the API.
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

        # Build submit_work tool definition (reused across all calls).
        self._submit_work_tool = _build_submit_work_tool()

    def probe_capabilities(self) -> DriverCapabilities:
        """Report Tier 1: high accuracy, structured output, no native fs/shell."""
        return DriverCapabilities(
            name="anthropic-api",
            parallel_dispatch=False,
            worker_filesystem_access=False,
            worker_shell_access=False,
            structured_output=True,
            worktree_isolation=False,
            recommended_verification_tier=1,
            tool_use_accuracy=0.95,
        )

    def resolve_model(self, role: str) -> str:
        """Map a role to a concrete model id."""
        return self.model_map.get(role, self.model_map.get(ROLE_WORKER, "claude-haiku-4-5-20251001"))

    def dispatch_worker(self, request: WorkerRequest) -> WorkerResult:
        """Dispatch via Anthropic messages API with forced tool calls.

        Injects file contents, forces submit_work tool call, parses tool_use block.
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
{file_context}"""

            # Build the messages array (may contain prior turns for repair loop).
            # If this is a fresh dispatch, start with a user message.
            # If this is a repair attempt, the request.prompt already contains the failure output.
            messages = [
                {
                    "role": "user",
                    "content": full_prompt,
                }
            ]

            # Prepare the Anthropic messages API request with forced tool call.
            payload = {
                "model": self.resolve_model(ROLE_WORKER),
                "max_tokens": 8192,  # Generous for structured output
                "messages": messages,
                "tools": [self._submit_work_tool],
                "tool_choice": {"type": "tool", "name": "submit_work"},
            }

            # Dispatch and get response.
            response = self.transport(payload)

            # Track tokens.
            if "usage" in response:
                tokens = (
                    response["usage"].get("input_tokens", 0)
                    + response["usage"].get("output_tokens", 0)
                )
                self._tokens_spent += tokens

            # Check for refusal (stop_reason=refusal, no content or no tool_use).
            stop_reason = response.get("stop_reason")
            if stop_reason == "refusal":
                return WorkerResult(
                    worker_id=worker_id,
                    ok=False,
                    status=WORKER_FAILED,
                    files_written=(),
                    structured={},
                    error="Model refused to process request (API refusal)",
                )

            # Extract tool_use block from response content.
            content = response.get("content", [])
            if not content:
                return WorkerResult(
                    worker_id=worker_id,
                    ok=False,
                    status=WORKER_FAILED,
                    files_written=(),
                    structured={},
                    error="No content in response",
                )

            # Look for tool_use block.
            tool_use_block = None
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    tool_use_block = item
                    break

            if not tool_use_block:
                return WorkerResult(
                    worker_id=worker_id,
                    ok=False,
                    status=WORKER_FAILED,
                    files_written=(),
                    structured={},
                    error="No tool_use block in response",
                )

            # Extract the patch from the tool_use input.
            try:
                patch_dict = tool_use_block.get("input", {})
                if not isinstance(patch_dict, dict):
                    patch_dict = json.loads(patch_dict) if isinstance(patch_dict, str) else {}

                # Validate the schema.
                _validate_patch_schema(patch_dict)
            except (json.JSONDecodeError, ValueError) as exc:
                return WorkerResult(
                    worker_id=worker_id,
                    ok=False,
                    status=WORKER_FAILED,
                    files_written=(),
                    structured={},
                    error=f"Tool input validation failed: {exc}",
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
        # proc_util.run_shell_bounded(command, cwd, timeout_s) has no `shell`
        # parameter (it always runs via a shell) and returns a CommandResult
        # object, not a tuple. The `shell` arg here is kept for interface parity
        # with the other drivers but is not forwarded.
        result = run_shell_bounded(
            command,
            cwd=cwd,
            timeout_s=self.timeout_s,
        )
        return CommandResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def get_tokens_spent(self) -> Optional[int]:
        """Return cumulative tokens spent."""
        return self._tokens_spent if self._tokens_spent > 0 else None
