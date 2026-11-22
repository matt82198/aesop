#!/usr/bin/env python3
"""Python emitter for the one shared aesop activity feed.

Writes the same JSONL schema that `chat/activity_feed.mjs` writes, so `aesop chat`
shows every local-model call made from this driver (Codex evals, agy bench, the
TUI turn engine) next to `aesop ask` jobs.

Path rule (shared with chat/activity_feed.mjs `feedPath`; keep the two in step):
  1. AESOP_ACTIVITY_FEED, when set, wins.
  2. Otherwise one per-user file for every checkout:
     <HOME || USERPROFILE || home>/.aesop/state/chat/activity.jsonl
     (the same home lookup auth/store.mjs uses; AESOP_STATE_ROOT is not consulted).

Contract:
  - append-only, one `write` call per event (`ab` mode, one full line), so
    concurrent appenders never interleave inside a line;
  - never raises: a feed failure must never fail a model call. The first
    failure prints one warning line to stderr and the emitter goes quiet;
  - no secrets: events carry prompts and reply text only, never headers or keys.
    Known secret env values are scrubbed from any error text before writing;
  - `from` comes from AESOP_FROM (default "driver").

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Optional

FEED_ENV = "AESOP_ACTIVITY_FEED"
FROM_ENV = "AESOP_FROM"
DEFAULT_FROM = "driver"
ROTATE_BYTES = 5 * 1024 * 1024
PROMPT_CAP = 1024
PREVIEW_CAP = 2048
CHUNK_SPLIT = 600
ERROR_CAP = 1024
# Env vars whose values must never reach an event (scrubbed from error text).
SECRET_ENV_NAMES = ("AESOP_TURN_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")

_STATUS = {0: "ok", 5: "timeout", 6: "refused"}


def feed_path(env=None) -> Path:
    """Feed file path. Same rule as chat/activity_feed.mjs feedPath()."""
    env = os.environ if env is None else env
    override = env.get(FEED_ENV)
    if override:
        return Path(override)
    home = env.get("HOME") or env.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / ".aesop" / "state" / "chat" / "activity.jsonl"


def status_for(exit_code: int) -> str:
    """Feed status for an aesop ask exit code (same mapping as the JS writer)."""
    return _STATUS.get(exit_code, "error")


def _cap(text: str, nbytes: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= nbytes:
        return text
    return raw[:nbytes].decode("utf-8", errors="ignore")


def _scrub(text: str, env) -> str:
    out = str(text)
    for name in SECRET_ENV_NAMES:
        value = env.get(name)
        if value:
            out = out.replace(value, "[redacted]")
    return out


class ActivityFeed:
    """Writer for one job. Every method swallows failures (one stderr warning at most)."""

    def __init__(self, env=None, job: Optional[str] = None, stderr=None):
        self._env = os.environ if env is None else env
        self._stderr = sys.stderr if stderr is None else stderr
        try:
            self.file = feed_path(self._env)
        except Exception:  # no resolvable home: the feed goes quiet, the call goes on
            self.file = Path(os.devnull)
        self.job = job or (
            f"{int(time.time() * 1000):x}-{os.getpid():x}-{secrets.token_hex(2)}"
        )
        self._from = self._env.get(FROM_ENV) or DEFAULT_FROM
        self._warned = False
        self._seq = 0

    def _put(self, obj: dict) -> None:
        try:
            line = json.dumps({"job": self.job, **obj}, ensure_ascii=False, separators=(",", ":")) + "\n"
            self.file.parent.mkdir(parents=True, exist_ok=True)
            if self.file.exists() and self.file.stat().st_size >= ROTATE_BYTES:
                rolled = self.file.with_name(f"{self.file.stem}.1.jsonl")
                os.replace(self.file, rolled)
            with open(self.file, "ab") as handle:
                handle.write(line.encode("utf-8"))
        except Exception as exc:  # never raise into a model call
            if not self._warned:
                self._warned = True
                try:
                    self._stderr.write(
                        f"aesop: activity feed disabled ({type(exc).__name__})\n"
                    )
                except Exception:
                    pass

    def start(self, *, model: str, prompt: str, stdin: str = "") -> None:
        """Opening event: caller label, model, prompt and stdin preview with byte counts."""
        try:
            prompt_text = str(prompt)
            stdin_text = str(stdin)
            self._put({
                "type": "start",
                "ts": int(time.time() * 1000),
                "from": self._from,
                "model": str(model),
                "prompt": _cap(prompt_text, PROMPT_CAP),
                "prompt_bytes": len(prompt_text.encode("utf-8")),
                "stdin_preview": _cap(stdin_text, PREVIEW_CAP),
                "stdin_bytes": len(stdin_text.encode("utf-8")),
            })
        except Exception:
            pass

    def chunk(self, text: str) -> None:
        """Reply text, split into lines of at most CHUNK_SPLIT characters."""
        try:
            s = str(text)
            for i in range(0, len(s), CHUNK_SPLIT):
                self._seq += 1
                self._put({"type": "chunk", "seq": self._seq, "text": s[i:i + CHUNK_SPLIT]})
        except Exception:
            pass

    def end(self, *, status: str, exit_code: int, elapsed_ms: int, error: Optional[str] = None) -> None:
        """Closing event: status, exit code, elapsed time, optional scrubbed error."""
        try:
            obj = {"type": "end", "status": status, "exit": int(exit_code), "elapsed_ms": int(elapsed_ms)}
            if error:
                obj["error"] = _cap(_scrub(error, self._env), ERROR_CAP)
            self._put(obj)
        except Exception:
            pass


def prompt_and_context(messages) -> tuple:
    """(prompt, context) from chat messages: the last user message is the prompt; the rest is context."""
    try:
        items = [m for m in (messages or []) if isinstance(m, dict)]
        last = None
        for index in range(len(items) - 1, -1, -1):
            if items[index].get("role") == "user":
                last = index
                break
        prompt = str(items[last].get("content") or "") if last is not None else ""
        context = "\n".join(
            str(m.get("content") or "") for i, m in enumerate(items) if i != last
        )
        return prompt, context
    except Exception:
        return "", ""


def _reply_text(reply) -> Optional[str]:
    try:
        content = reply["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else None
    except Exception:
        return None


def observe_transport(transport):
    """Wrap a payload -> dict transport so every call emits start, chunk(s), then end.

    Used at the OpenAI-compatible layer (OpenAICompatibleDriver). The wrapper never
    changes the reply or the raised exception; a failed call ends with status error, exit 4.
    """
    if transport is None:
        return None

    def observed(payload):
        feed = ActivityFeed()
        prompt, context = prompt_and_context(payload.get("messages") if isinstance(payload, dict) else None)
        model = payload.get("model", "") if isinstance(payload, dict) else ""
        feed.start(model=model, prompt=prompt, stdin=context)
        started = time.monotonic()
        try:
            reply = transport(payload)
        except Exception as exc:
            feed.end(status="error", exit_code=4, elapsed_ms=int((time.monotonic() - started) * 1000),
                     error=f"{type(exc).__name__}: {exc}")
            raise
        text = _reply_text(reply)
        if text:
            feed.chunk(text)
        feed.end(status="ok", exit_code=0, elapsed_ms=int((time.monotonic() - started) * 1000))
        return reply

    return observed
