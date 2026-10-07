#!/usr/bin/env python3
"""tools.multibox_config -- the multibox config block and its hard preflight gate.
INDEX: the config-consuming seam (Inc 7): parses the `multibox` block with precedence env (`AESOP_MULTIBOX_<KEY>`) > aesop.config.json > `MULTIBOX_DEFAULTS`, rejecting unknown keys, bad types, bools-as-numbers and self-contradictory combinations (shared-fs without shared_dir, shared_dir under transport=local, heartbeat >= lease_ttl) fail-closed; `assert_preflight()` is the HARD startup gate -- enabling multibox refuses unless the DB is on local storage AND (shared-fs only) measured p99 visibility delay < settle_seconds AND measured skew < max_skew_seconds, with any probe exception itself a refusal and every refusal reproducing MOUNT_REMEDIES (NFS nfsvers=4.1,actimeo=1,lookupcache=none / SMB cache=none); `build_backend()` then returns LocalLeaseBackend or FsClaimLog. INERTNESS IS THE CONTRACT: at the shipped default (enabled:false) it returns None before importing state_store.claim_backend, state_store.fs_claim_log or multibox_preflight -- every backend/probe import is deferred into the function body precisely so a fresh interpreter can prove zero multibox code paths were reached.

This is the seam where the multibox increments (0-6) become reachable. It owns
three things and nothing else:

1. **Parsing** the ``multibox`` block of ``aesop.config.json`` with the repo's
   standing precedence rule -- ``environment > config file > built-in default``
   -- rejecting malformed values fail-closed instead of silently defaulting.
2. **The hard preflight gate.** The whole shared-filesystem design rests on one
   measurable assumption: a written+fsynced record becomes visible in a peer's
   directory listing within a bounded time D, and D is below the configured
   settle window. ``tools/multibox_preflight.py`` (increment 0) measures it.
   Here that measurement stops being advisory: enabling multibox REFUSES to
   proceed unless the probe proves (i) the event-store SQLite DB is on local
   storage, (ii) the measured p99 visibility delay is under ``settle_seconds``,
   and (iii) the measured clock skew is under ``max_skew_seconds``. Any probe
   failure is itself a refusal -- an unmeasurable share is an unsafe share.
3. **Backend selection**, once the gate has passed: ``LocalLeaseBackend`` for
   ``transport: "local"``, ``FsClaimLog`` for ``transport: "shared-fs"``.

Inertness is a load-bearing property, not a nicety. At the shipped default
(``enabled: false``) ``build_backend`` returns ``None`` before importing a
single coordination module -- neither ``state_store.claim_backend`` nor
``state_store.fs_claim_log`` nor ``tools.multibox_preflight`` is pulled in.
That is why every backend/probe import in this module is deferred into the
function body: it makes "zero multibox code paths reached" observable from
outside the process (``tests/test_multibox_config.py``).

Stdlib only, ASCII output.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Optional

# Built-in defaults. This dict IS the contract: aesop.config.example.json ships
# these exact values, and a test asserts the two never drift apart.
MULTIBOX_DEFAULTS = {
    "enabled": False,
    "transport": "local",
    "shared_dir": None,
    "settle_seconds": 5.0,
    "max_skew_seconds": 2.0,
    "lease_ttl_seconds": 300,
    "heartbeat_seconds": 30,
    "case_policy": "insensitive",
    "instance_id": None,
}

#: Environment overrides are ``AESOP_MULTIBOX_<UPPER_KEY>``.
ENV_PREFIX = "AESOP_MULTIBOX_"

VALID_TRANSPORTS = ("local", "shared-fs")
VALID_CASE_POLICIES = ("platform", "insensitive", "sensitive")

#: Subdirectory of ``shared_dir`` holding the append-only claim records.
CLAIMS_DIRNAME = "claims"

_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})

#: Reproduced verbatim in every refusal. A refusal that only says "no" makes the
#: operator guess; the plan documents exactly which mount options make a share
#: measurable, so the gate hands them over at the moment they are needed.
MOUNT_REMEDIES = (
    "Remedies (documented mount options -- disable client-side caching so a\n"
    "fsynced record is promptly visible in a peer's directory listing):\n"
    "  NFS : mount -o nfsvers=4.1,actimeo=1,lookupcache=none\n"
    "  SMB : mount -o cache=none   (Windows client: directoryCacheLifetime=0)\n"
    "Also check, in order:\n"
    "  - Keep the SQLite event store on LOCAL storage. SQLite WAL needs a\n"
    "    shared-memory index that is only coherent on one host; only the claim\n"
    "    log belongs on the share.\n"
    "  - Raise multibox.settle_seconds above the measured p99 visibility delay.\n"
    "  - Bring the boxes' clocks together (NTP) or raise multibox.max_skew_seconds;\n"
    "    skew only ever lengthens a lease, so an over-estimate is safe.\n"
    "  - Re-run the probe directly: python tools/multibox_preflight.py --check\n"
    "    --shared-dir DIR --db PATH"
)


class MultiboxConfigError(ValueError):
    """A multibox config value is missing, malformed, or self-contradictory."""


class MultiboxPreflightRefused(RuntimeError):
    """The startup preflight refused to enable multibox.

    Carries the raw preflight report (when one was produced) so callers that
    want structured findings do not have to parse the message.
    """

    def __init__(self, message: str, report: Optional[dict] = None):
        """Record the rendered refusal message and the originating report."""
        super().__init__(message)
        self.report = report


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _coerce_bool(key: str, raw: Any) -> bool:
    """Coerce a config or environment value to bool, fail-closed."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        word = raw.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    raise MultiboxConfigError(
        "multibox.%s: expected a boolean, got %r" % (key, raw))


def _coerce_number(key: str, raw: Any, as_int: bool, allow_zero: bool):
    """Coerce to float/int with an explicit bool rejection, fail-closed.

    ``True`` equals ``1`` in Python; accepting it in a numeric slot would turn a
    config typo into a silently plausible timeout.
    """
    if isinstance(raw, bool):
        raise MultiboxConfigError(
            "multibox.%s: expected a number, got boolean %r" % (key, raw))
    try:
        value = int(raw) if as_int else float(raw)
    except (TypeError, ValueError):
        raise MultiboxConfigError(
            "multibox.%s: expected a number, got %r" % (key, raw)) from None
    if value < 0 or (value == 0 and not allow_zero):
        raise MultiboxConfigError(
            "multibox.%s: expected %s, got %r"
            % (key, "a non-negative number" if allow_zero
               else "a positive number", raw))
    return value


def _coerce_optional_str(key: str, raw: Any) -> Optional[str]:
    """Coerce to a non-empty string or None (empty string clears the field)."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise MultiboxConfigError(
            "multibox.%s: expected a string or null, got %r" % (key, raw))
    text = raw.strip()
    return text or None


def _coerce_choice(key: str, raw: Any, choices) -> str:
    """Coerce to one of ``choices``, fail-closed on anything else."""
    if not isinstance(raw, str) or raw not in choices:
        raise MultiboxConfigError(
            "multibox.%s: expected one of %s, got %r"
            % (key, ", ".join(choices), raw))
    return raw


_COERCERS: dict = {
    "enabled": lambda raw: _coerce_bool("enabled", raw),
    "transport": lambda raw: _coerce_choice("transport", raw, VALID_TRANSPORTS),
    "shared_dir": lambda raw: _coerce_optional_str("shared_dir", raw),
    "settle_seconds": lambda raw: _coerce_number(
        "settle_seconds", raw, as_int=False, allow_zero=False),
    "max_skew_seconds": lambda raw: _coerce_number(
        "max_skew_seconds", raw, as_int=False, allow_zero=True),
    "lease_ttl_seconds": lambda raw: _coerce_number(
        "lease_ttl_seconds", raw, as_int=True, allow_zero=False),
    "heartbeat_seconds": lambda raw: _coerce_number(
        "heartbeat_seconds", raw, as_int=True, allow_zero=False),
    "case_policy": lambda raw: _coerce_choice(
        "case_policy", raw, VALID_CASE_POLICIES),
    "instance_id": lambda raw: _coerce_optional_str("instance_id", raw),
}


def _validate(settings: dict) -> dict:
    """Reject self-contradictory combinations that parse field-by-field."""
    if settings["transport"] == "shared-fs" and settings["shared_dir"] is None:
        raise MultiboxConfigError(
            "multibox.transport='shared-fs' requires multibox.shared_dir "
            "(the directory holding the cross-instance claim log)")
    if settings["transport"] == "local" and settings["shared_dir"] is not None:
        raise MultiboxConfigError(
            "multibox.shared_dir is set but multibox.transport='local'; a "
            "shared directory would be silently ignored. Set "
            "transport='shared-fs' or clear shared_dir")
    if settings["heartbeat_seconds"] >= settings["lease_ttl_seconds"]:
        raise MultiboxConfigError(
            "multibox.heartbeat_seconds (%r) must be below "
            "multibox.lease_ttl_seconds (%r), or a live instance's lease "
            "expires between heartbeats"
            % (settings["heartbeat_seconds"], settings["lease_ttl_seconds"]))
    return settings


def load_multibox_config(config: Optional[Mapping] = None,
                         env: Optional[Mapping] = None) -> dict:
    """Resolve the multibox settings block.

    Precedence is ``env > config file > built-in default``, matching every other
    aesop config surface. Keys beginning with ``_`` in the config block are
    documentation comments (the convention used throughout
    ``aesop.config.example.json``) and are ignored; any other unrecognised key
    is an error, so a typo cannot quietly leave a safety setting at its default.

    Args:
        config: parsed ``aesop.config.json`` contents, or None.
        env: environment mapping (defaults to ``os.environ``).

    Returns:
        dict with exactly the keys of ``MULTIBOX_DEFAULTS``, coerced and validated.

    Raises:
        MultiboxConfigError: on an unknown key, a malformed value, or a
            self-contradictory combination.
    """
    env = os.environ if env is None else env
    settings = dict(MULTIBOX_DEFAULTS)

    block: Any = {}
    if config is not None and "multibox" in config:
        block = config["multibox"]
        if block is None:
            block = {}
    if not isinstance(block, Mapping):
        raise MultiboxConfigError(
            "multibox: expected a JSON object, got %r" % (block,))

    for key, raw in block.items():
        if key.startswith("_"):
            continue
        if key not in MULTIBOX_DEFAULTS:
            raise MultiboxConfigError(
                "multibox.%s: unknown config key (known keys: %s)"
                % (key, ", ".join(sorted(MULTIBOX_DEFAULTS))))
        settings[key] = _COERCERS[key](raw)

    for key in MULTIBOX_DEFAULTS:
        env_name = ENV_PREFIX + key.upper()
        if env_name not in env:
            continue
        raw = env[env_name]
        if raw == "" and MULTIBOX_DEFAULTS[key] is None:
            settings[key] = None
            continue
        settings[key] = _COERCERS[key](raw)

    return _validate(settings)


# ---------------------------------------------------------------------------
# The hard preflight gate
# ---------------------------------------------------------------------------

def _render_refusal(settings: Mapping, db_path, detail: str,
                    findings=None) -> str:
    """Build the operator-facing refusal message, remedies included."""
    lines = [
        "REFUSED: multibox.enabled is true but the startup preflight did not "
        "pass.",
        "Multibox stays OFF (fail-closed) -- coordination state is only safe "
        "once the share is measured.",
        "  transport   : %s" % settings["transport"],
        "  shared_dir  : %s" % settings["shared_dir"],
        "  db          : %s" % db_path,
        "  settle      : %.3fs   max_skew: %.3fs"
        % (float(settings["settle_seconds"]),
           float(settings["max_skew_seconds"])),
    ]
    if findings:
        lines.append("Findings:")
        for finding in findings:
            lines.append("  - [%s] %s" % (finding.get("id", "UNKNOWN"),
                                          finding.get("detail", "")))
    else:
        lines.append("Findings:")
        lines.append("  - %s" % detail)
    lines.append(MOUNT_REMEDIES)
    return "\n".join(lines)


def assert_preflight(settings: Mapping, db_path,
                     runner: Optional[Callable] = None,
                     fs_kind_fn: Optional[Callable] = None,
                     samples: Optional[int] = None) -> Optional[dict]:
    """Hard startup gate: refuse to enable multibox on an unproven environment.

    A no-op when ``settings['enabled']`` is false -- the probe is never even
    imported. When enabled:

    * ``transport='shared-fs'``: runs the full increment-0 preflight against
      ``shared_dir`` and requires all three conditions -- DB on local storage,
      p99 visibility delay under ``settle_seconds``, clock skew under
      ``max_skew_seconds``.
    * ``transport='local'``: there is no share, so the visibility and skew
      conditions are vacuous by construction (they are properties OF a share).
      Condition (i) still binds -- a WAL SQLite event store on a network mount
      is unsafe whether or not anything is shared -- and is enforced here.

    Args:
        settings: a mapping from :func:`load_multibox_config`.
        db_path: path to the event-store SQLite database.
        runner: override for ``multibox_preflight.run_preflight`` (tests).
        fs_kind_fn: override for ``multibox_preflight.detect_fs_kind`` (tests).
        samples: probe sample count; None uses the preflight default.

    Returns:
        The preflight report dict, or None when multibox is disabled.

    Raises:
        MultiboxPreflightRefused: on any finding, or on any probe failure.
    """
    if not settings.get("enabled"):
        return None

    from tools import multibox_preflight as preflight

    shared_dir = settings.get("shared_dir")
    if shared_dir is None:
        kind_of = fs_kind_fn or preflight.detect_fs_kind
        try:
            kind = kind_of(str(db_path))
        except Exception as exc:
            raise MultiboxPreflightRefused(_render_refusal(
                settings, db_path,
                "DB-FS-PROBE-FAILED: could not classify %s: %s"
                % (db_path, exc))) from exc
        if preflight.is_network_kind(kind):
            raise MultiboxPreflightRefused(_render_refusal(
                settings, db_path,
                "DB-ON-NETWORK-FS: event-store db %s resolves to fs kind '%s'; "
                "SQLite WAL is only coherent on local storage"
                % (db_path, kind)))
        return {"v": 1, "ok": True, "findings": [],
                "db": {"path": str(db_path), "fs_kind": kind},
                "shared_dir": None,
                "checks": ["db-locality"]}

    run = runner or preflight.run_preflight
    kwargs = {
        "settle_seconds": float(settings["settle_seconds"]),
        "max_skew_seconds": float(settings["max_skew_seconds"]),
    }
    if samples is not None:
        kwargs["samples"] = samples
    try:
        report = run(shared_dir, db_path, **kwargs)
    except Exception as exc:
        raise MultiboxPreflightRefused(_render_refusal(
            settings, db_path,
            "PREFLIGHT-PROBE-FAILED: %s" % (exc,))) from exc

    if not isinstance(report, Mapping) or not report.get("ok", False):
        findings = None
        if isinstance(report, Mapping):
            findings = report.get("findings")
        raise MultiboxPreflightRefused(
            _render_refusal(settings, db_path,
                            "PREFLIGHT-REPORT-NOT-OK: %r" % (report,),
                            findings=findings),
            report if isinstance(report, Mapping) else None)
    return dict(report)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def claims_dir_for(settings: Mapping) -> Optional[str]:
    """Return the claim-log directory under ``shared_dir``, or None."""
    shared_dir = settings.get("shared_dir")
    if shared_dir is None:
        return None
    return os.path.join(shared_dir, CLAIMS_DIRNAME)


def build_backend(db_path, config: Optional[Mapping] = None,
                  env: Optional[Mapping] = None,
                  repo_root: Optional[str] = None,
                  epoch: int = 1,
                  samples: Optional[int] = None,
                  _gate: Optional[Callable] = None,
                  _local_factory: Optional[Callable] = None,
                  _shared_factory: Optional[Callable] = None):
    """Resolve config, run the hard gate, and return the active ClaimBackend.

    Returns None when multibox is disabled -- the shipped default -- WITHOUT
    importing any coordination module, running any probe, or touching the
    filesystem. That inertness is asserted from a fresh interpreter in
    ``tests/test_multibox_config.py``.

    Args:
        db_path: path to the event-store SQLite database.
        config: parsed ``aesop.config.json`` contents, or None.
        env: environment mapping (defaults to ``os.environ``).
        repo_root: repo root for repo-relative claim-path canonicalisation.
        epoch: this instance's fencing epoch (increment 3 identity).
        samples: probe sample count passed to the preflight.
        _gate: preflight-gate override (tests only).
        _local_factory: LocalLeaseBackend factory override (tests only).
        _shared_factory: FsClaimLog factory override (tests only).

    Returns:
        A ClaimBackend when multibox is enabled, else None.

    Raises:
        MultiboxConfigError: on malformed config.
        MultiboxPreflightRefused: when the environment fails the hard gate.
    """
    settings = load_multibox_config(config, env=env)
    if not settings["enabled"]:
        return None

    gate = _gate or assert_preflight
    gate(settings, db_path, samples=samples)

    if settings["transport"] == "shared-fs":
        factory = _shared_factory
        if factory is None:
            from state_store.fs_claim_log import FsClaimLog

            factory = FsClaimLog
        return factory(
            claims_dir=claims_dir_for(settings),
            settle_seconds=float(settings["settle_seconds"]),
            max_skew_seconds=float(settings["max_skew_seconds"]),
            case_policy=settings["case_policy"],
            repo_root=repo_root,
            epoch=epoch,
            default_ttl_seconds=float(settings["lease_ttl_seconds"]),
        )

    factory = _local_factory
    if factory is None:
        from state_store.claim_backend import LocalLeaseBackend

        factory = LocalLeaseBackend
    return factory(str(db_path))
