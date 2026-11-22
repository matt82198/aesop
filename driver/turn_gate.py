#!/usr/bin/env python3
"""The provider boundary gate for the turn engine (TUI driver plan, phase P4).

PURPOSE: this artifact exists to make the aesop chat TUI, aesop ask and
codex/gemini/agy run every turn through the aesop driver (one turn engine;
the pipe is the product); status: real

Non-Anthropic only. `assert_provider_allowed(provider, model)` is the single
gate: the CLI calls it before stdin is read, and every request calls it again
before a socket is opened, so a config changed after load is still refused.
It raises ProviderRefused (exit 6) for:

- a provider type that is command-style or Anthropic (`cli`, `exec`,
  `claude`, `anthropic`);
- a provider id that names Claude or Anthropic;
- a model id in which `claude` or `anthropic` is a whole token (bounded by
  start, end, whitespace or one of / : . _ -), unless the type is
  `orchestrator`. Gateway routes (`openrouter/anthropic/...`,
  `us.anthropic.claude-3`) are refused; `claudette-7b` is not;
- a base_url that is not a URL, whose host is anthropic.com, claude.ai or a
  subdomain of either (any case, trailing dot, userinfo tricks,
  percent-encoding, compatibility characters), whose scheme is not http or
  https, or that is plain http to a non-loopback host without
  `allow_insecure: true`.

It is a port of assertProviderAllowed in chat/harness.mjs, with the same
reason texts. Both run tests/fixtures/provider-config-cases.json. The gate
does no I/O at all: no DNS lookup, no socket.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import re
import unicodedata
from typing import Optional
from urllib.parse import unquote, urlsplit

REFUSED_TYPES = frozenset(("cli", "exec", "claude", "anthropic"))
LOOPBACK_HOSTS = frozenset(("127.0.0.1", "localhost", "::1"))
_CLAUDE_TOKEN = re.compile(r"(?:^|[\s/:._-])(?:claude|anthropic)(?=$|[\s/:._-])", re.IGNORECASE)
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
# Characters a URL parser treats as a dot in a host name (ideographic and fullwidth full stops).
_DOT_LIKE = (chr(0x3002), chr(0xFF0E), chr(0xFF61))


class ProviderRefused(Exception):
    """A provider, its base_url or a model id is not allowed. The message names the provider."""

    def __init__(self, provider: str, reason: str):
        super().__init__(f"{provider}: refused: {reason}")
        self.provider = provider
        self.reason = reason


def normalize_host(host: str) -> str:
    """Lower-case a host, decode percent-escapes, fold compatibility characters, drop trailing dots and brackets."""
    text = unicodedata.normalize("NFKC", unquote(str(host)))
    for dot in _DOT_LIKE:
        text = text.replace(dot, ".")
    text = text.lower().rstrip(".")
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return text


def is_anthropic_host(host: str) -> bool:
    """True for anthropic.com, claude.ai and their subdomains."""
    name = normalize_host(host)
    return (
        name == "anthropic.com" or name.endswith(".anthropic.com")
        or name == "claude.ai" or name.endswith(".claude.ai")
    )


def names_claude(text) -> bool:
    """True when `claude` or `anthropic` is a whole token anywhere in the id."""
    return bool(_CLAUDE_TOKEN.search(str(text).strip()))


def parse_url(base_url):
    """Return (scheme, host) or None when the text is not a URL."""
    # Browsers and Node treat a backslash as a slash in http(s) URLs; do the same before splitting.
    text = str(base_url or "").strip().replace("\\", "/")
    match = _SCHEME.match(text)
    if not match:
        return None
    scheme = match.group(1).lower()
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return None
    if scheme in ("http", "https") and host == "":
        return None
    return scheme, host


def assert_provider_allowed(provider: dict, model=None) -> None:
    """Raise ProviderRefused when the provider (or the model id, when given) is not allowed.

    `provider` is the raw config entry plus its `id`.
    """
    provider_id = str(provider.get("id") if provider.get("id") is not None else "?")
    ptype = str(provider.get("type") if provider.get("type") is not None else "")
    if ptype in REFUSED_TYPES:
        raise ProviderRefused(provider_id, f'provider type "{ptype}" is command-style or Anthropic')
    if ptype != "orchestrator" and names_claude(provider_id):
        raise ProviderRefused(provider_id, f'provider id "{provider_id}" names Claude or Anthropic')
    if model is not None and ptype != "orchestrator" and names_claude(model):
        raise ProviderRefused(provider_id, f'model "{model}" resolves to Claude')
    parsed = parse_url(provider.get("base_url"))
    if parsed is None:
        raise ProviderRefused(provider_id, "base_url is not a valid URL")
    scheme, host = parsed
    if is_anthropic_host(host):
        raise ProviderRefused(provider_id, "base_url host is an Anthropic host")
    if scheme not in ("http", "https"):
        raise ProviderRefused(provider_id, "base_url must be http or https")
    if scheme == "http" and normalize_host(host) not in LOOPBACK_HOSTS and provider.get("allow_insecure") is not True:
        raise ProviderRefused(provider_id, "non-loopback http requires allow_insecure")


def refusal_for(entry: dict, model=None) -> Optional[str]:
    """The refusal reason for a registry entry `{"id", "raw"}` and a model, or None."""
    try:
        assert_provider_allowed({**entry["raw"], "id": entry["id"]}, model)
    except ProviderRefused as refused:
        return refused.reason
    return None


def check_provider(config) -> tuple:
    """Pure provider-config check for the shared fixture: `(ok, reason)`.

    Runs the gate on the base_url, the type and every listed model, like
    validateProviderConfig in chat/harness.mjs.
    """
    raw = config if isinstance(config, dict) else {}
    models = raw.get("models")
    try:
        assert_provider_allowed({**raw, "id": "config"})
        for model in models if isinstance(models, list) else []:
            assert_provider_allowed({**raw, "id": "config"}, str(model))
    except ProviderRefused as refused:
        return False, refused.reason
    return True, None
