#!/usr/bin/env python3
"""Tests for tools.multibox_config -- multibox config block + hard preflight gate.

Three properties are under test, in the order the plan demands them:

1. **Parse**: the ``multibox`` config block resolves with precedence
   ``env > config file > built-in default``, and every malformed value is
   rejected fail-closed rather than silently defaulted.
2. **Hard gate**: enabling multibox refuses to proceed unless the Inc 0 preflight
   proves (i) the event-store DB is on local storage, (ii) the measured p99
   cross-box visibility delay is below ``settle_seconds``, and (iii) the measured
   clock skew is below ``max_skew_seconds``. Each condition is fixtured
   independently and each refusal must carry the documented mount remedies.
3. **Inert at default**: with the shipped default config (``enabled: false``) not
   one multibox code path is reached. Proven by sentinel, and independently by a
   subprocess that asserts the coordination modules were never even imported.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.multibox_config import (  # noqa: E402
    ENV_PREFIX,
    MOUNT_REMEDIES,
    MULTIBOX_DEFAULTS,
    MultiboxConfigError,
    MultiboxPreflightRefused,
    assert_preflight,
    build_backend,
    load_multibox_config,
)


def _clean_env():
    """An environment mapping with every multibox override removed."""
    return {k: v for k, v in os.environ.items() if not k.startswith(ENV_PREFIX)}


def _ok_report(**over):
    report = {
        "v": 1,
        "ok": True,
        "findings": [],
        "db": {"path": "db", "fs_kind": "local"},
        "shared_dir": {"path": "share", "fs_kind": "network"},
        "visibility": {"p99_seconds": 0.5},
        "clock_skew": {"max_abs_seconds": 0.1},
    }
    report.update(over)
    return report


def _refused_report(finding_id, detail):
    return {
        "v": 1,
        "ok": False,
        "findings": [{"id": finding_id, "severity": "error", "detail": detail}],
        "db": {"path": "db", "fs_kind": "local"},
        "shared_dir": {"path": "share", "fs_kind": "network"},
        "visibility": None,
        "clock_skew": None,
    }


class _Recorder:
    """Callable sentinel that records every invocation."""

    def __init__(self, result=None, raises=None):
        self.calls = []
        self._result = result
        self._raises = raises

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._raises is not None:
            raise self._raises
        return self._result

    @property
    def called(self):
        return bool(self.calls)


# ---------------------------------------------------------------------------
# 1. Config parse: defaults and precedence
# ---------------------------------------------------------------------------

class TestMultiboxConfigDefaults(unittest.TestCase):
    def test_defaults_when_no_config_at_all(self):
        settings = load_multibox_config(None, env=_clean_env())
        self.assertEqual(settings, dict(MULTIBOX_DEFAULTS))

    def test_defaults_when_config_has_no_multibox_block(self):
        settings = load_multibox_config({"state_root": "./state"}, env=_clean_env())
        self.assertEqual(settings, dict(MULTIBOX_DEFAULTS))

    def test_shipped_defaults_are_the_documented_values(self):
        self.assertEqual(MULTIBOX_DEFAULTS["enabled"], False)
        self.assertEqual(MULTIBOX_DEFAULTS["transport"], "local")
        self.assertIsNone(MULTIBOX_DEFAULTS["shared_dir"])
        self.assertEqual(MULTIBOX_DEFAULTS["settle_seconds"], 5.0)
        self.assertEqual(MULTIBOX_DEFAULTS["max_skew_seconds"], 2.0)
        self.assertEqual(MULTIBOX_DEFAULTS["lease_ttl_seconds"], 300)
        self.assertEqual(MULTIBOX_DEFAULTS["heartbeat_seconds"], 30)
        self.assertEqual(MULTIBOX_DEFAULTS["case_policy"], "insensitive")
        self.assertIsNone(MULTIBOX_DEFAULTS["instance_id"])

    def test_defaults_are_not_mutated_by_a_parse(self):
        before = dict(MULTIBOX_DEFAULTS)
        load_multibox_config(
            {"multibox": {"enabled": True, "settle_seconds": 9.0}}, env=_clean_env()
        )
        self.assertEqual(dict(MULTIBOX_DEFAULTS), before)

    def test_config_file_overrides_defaults(self):
        settings = load_multibox_config(
            {
                "multibox": {
                    "enabled": True,
                    "transport": "shared-fs",
                    "shared_dir": "/mnt/share",
                    "settle_seconds": 7.5,
                    "max_skew_seconds": 1.25,
                    "lease_ttl_seconds": 120,
                    "heartbeat_seconds": 15,
                    "case_policy": "sensitive",
                    "instance_id": "box-a",
                }
            },
            env=_clean_env(),
        )
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["transport"], "shared-fs")
        self.assertEqual(settings["shared_dir"], "/mnt/share")
        self.assertEqual(settings["settle_seconds"], 7.5)
        self.assertEqual(settings["max_skew_seconds"], 1.25)
        self.assertEqual(settings["lease_ttl_seconds"], 120)
        self.assertEqual(settings["heartbeat_seconds"], 15)
        self.assertEqual(settings["case_policy"], "sensitive")
        self.assertEqual(settings["instance_id"], "box-a")

    def test_underscore_prefixed_keys_are_comments_and_ignored(self):
        settings = load_multibox_config(
            {"multibox": {"_comment": "docs", "settle_seconds": 6.0}},
            env=_clean_env(),
        )
        self.assertEqual(settings["settle_seconds"], 6.0)
        self.assertNotIn("_comment", settings)


class TestMultiboxConfigPrecedence(unittest.TestCase):
    def test_env_beats_config_file_for_every_field(self):
        config = {
            "multibox": {
                "enabled": False,
                "transport": "local",
                "shared_dir": "/from-config",
                "settle_seconds": 1.0,
                "max_skew_seconds": 1.0,
                "lease_ttl_seconds": 1,
                "heartbeat_seconds": 1,
                "case_policy": "sensitive",
                "instance_id": "from-config",
            }
        }
        env = _clean_env()
        env.update({
            ENV_PREFIX + "ENABLED": "true",
            ENV_PREFIX + "TRANSPORT": "shared-fs",
            ENV_PREFIX + "SHARED_DIR": "/from-env",
            ENV_PREFIX + "SETTLE_SECONDS": "8.5",
            ENV_PREFIX + "MAX_SKEW_SECONDS": "3.5",
            ENV_PREFIX + "LEASE_TTL_SECONDS": "600",
            ENV_PREFIX + "HEARTBEAT_SECONDS": "45",
            ENV_PREFIX + "CASE_POLICY": "insensitive",
            ENV_PREFIX + "INSTANCE_ID": "from-env",
        })
        settings = load_multibox_config(config, env=env)
        self.assertEqual(settings, {
            "enabled": True,
            "transport": "shared-fs",
            "shared_dir": "/from-env",
            "settle_seconds": 8.5,
            "max_skew_seconds": 3.5,
            "lease_ttl_seconds": 600,
            "heartbeat_seconds": 45,
            "case_policy": "insensitive",
            "instance_id": "from-env",
        })

    def test_env_beats_default_when_config_is_silent(self):
        env = _clean_env()
        env[ENV_PREFIX + "SETTLE_SECONDS"] = "11.0"
        settings = load_multibox_config(None, env=env)
        self.assertEqual(settings["settle_seconds"], 11.0)
        self.assertFalse(settings["enabled"])

    def test_config_beats_default_when_env_is_silent(self):
        settings = load_multibox_config(
            {"multibox": {"settle_seconds": 2.5}}, env=_clean_env()
        )
        self.assertEqual(settings["settle_seconds"], 2.5)

    def test_empty_env_string_clears_a_nullable_field(self):
        env = _clean_env()
        env[ENV_PREFIX + "SHARED_DIR"] = ""
        settings = load_multibox_config(
            {"multibox": {"shared_dir": "/from-config"}}, env=env
        )
        self.assertIsNone(settings["shared_dir"])

    def test_env_boolean_truthy_and_falsy_spellings(self):
        for raw in ("1", "true", "TRUE", "True", "yes", "on"):
            env = _clean_env()
            env[ENV_PREFIX + "ENABLED"] = raw
            self.assertTrue(
                load_multibox_config(None, env=env)["enabled"], raw
            )
        for raw in ("0", "false", "FALSE", "no", "off"):
            env = _clean_env()
            env[ENV_PREFIX + "ENABLED"] = raw
            self.assertFalse(
                load_multibox_config(None, env=env)["enabled"], raw
            )

    def test_reads_os_environ_when_env_not_supplied(self):
        # Default env source is the real process environment.
        os.environ[ENV_PREFIX + "SETTLE_SECONDS"] = "13.5"
        try:
            self.assertEqual(load_multibox_config(None)["settle_seconds"], 13.5)
        finally:
            del os.environ[ENV_PREFIX + "SETTLE_SECONDS"]


class TestMultiboxConfigFailClosed(unittest.TestCase):
    def test_unknown_key_rejected(self):
        with self.assertRaises(MultiboxConfigError) as ctx:
            load_multibox_config({"multibox": {"enabld": True}}, env=_clean_env())
        self.assertIn("enabld", str(ctx.exception))

    def test_multibox_block_must_be_an_object(self):
        with self.assertRaises(MultiboxConfigError):
            load_multibox_config({"multibox": []}, env=_clean_env())

    def test_invalid_transport_rejected(self):
        with self.assertRaises(MultiboxConfigError) as ctx:
            load_multibox_config(
                {"multibox": {"transport": "postgres"}}, env=_clean_env()
            )
        self.assertIn("transport", str(ctx.exception))

    def test_invalid_case_policy_rejected(self):
        with self.assertRaises(MultiboxConfigError):
            load_multibox_config(
                {"multibox": {"case_policy": "whatever"}}, env=_clean_env()
            )

    def test_non_positive_numbers_rejected(self):
        for key in ("settle_seconds", "lease_ttl_seconds", "heartbeat_seconds"):
            with self.assertRaises(MultiboxConfigError, msg=key):
                load_multibox_config({"multibox": {key: 0}}, env=_clean_env())

    def test_negative_max_skew_rejected(self):
        with self.assertRaises(MultiboxConfigError):
            load_multibox_config(
                {"multibox": {"max_skew_seconds": -1}}, env=_clean_env()
            )

    def test_non_numeric_env_value_rejected(self):
        env = _clean_env()
        env[ENV_PREFIX + "SETTLE_SECONDS"] = "soon"
        with self.assertRaises(MultiboxConfigError):
            load_multibox_config(None, env=env)

    def test_non_boolean_env_value_rejected(self):
        env = _clean_env()
        env[ENV_PREFIX + "ENABLED"] = "maybe"
        with self.assertRaises(MultiboxConfigError):
            load_multibox_config(None, env=env)

    def test_boolean_is_not_accepted_as_a_number(self):
        # True == 1 in Python; a bool in a numeric slot is a config mistake.
        with self.assertRaises(MultiboxConfigError):
            load_multibox_config(
                {"multibox": {"settle_seconds": True}}, env=_clean_env()
            )

    def test_shared_fs_transport_requires_shared_dir(self):
        with self.assertRaises(MultiboxConfigError) as ctx:
            load_multibox_config(
                {"multibox": {"enabled": True, "transport": "shared-fs"}},
                env=_clean_env(),
            )
        self.assertIn("shared_dir", str(ctx.exception))

    def test_local_transport_rejects_a_shared_dir(self):
        with self.assertRaises(MultiboxConfigError):
            load_multibox_config(
                {"multibox": {"transport": "local", "shared_dir": "/mnt/share"}},
                env=_clean_env(),
            )


# ---------------------------------------------------------------------------
# 2. Hard preflight gate: one refusal test per documented condition
# ---------------------------------------------------------------------------

class TestPreflightGateRefusal(unittest.TestCase):
    """Each of the three gate conditions refuses independently, fail-closed."""

    def _shared_fs_settings(self, **over):
        settings = load_multibox_config(
            {
                "multibox": {
                    "enabled": True,
                    "transport": "shared-fs",
                    "shared_dir": "/mnt/share",
                }
            },
            env=_clean_env(),
        )
        settings.update(over)
        return settings

    def test_condition_i_db_on_network_fs_refuses(self):
        runner = _Recorder(result=_refused_report(
            "DB-ON-NETWORK-FS",
            "event-store db /mnt/share/state.db resolves to fs kind 'cifs'",
        ))
        with self.assertRaises(MultiboxPreflightRefused) as ctx:
            assert_preflight(self._shared_fs_settings(), "/mnt/share/state.db",
                             runner=runner)
        message = str(ctx.exception)
        self.assertIn("DB-ON-NETWORK-FS", message)
        self.assertIn("multibox.enabled", message)

    def test_condition_ii_visibility_delay_exceeds_settle_refuses(self):
        runner = _Recorder(result=_refused_report(
            "VISIBILITY-DELAY-EXCEEDS-SETTLE",
            "measured p99 visibility delay 9.100s exceeds settle window 5.000s",
        ))
        with self.assertRaises(MultiboxPreflightRefused) as ctx:
            assert_preflight(self._shared_fs_settings(), "/local/state.db",
                             runner=runner)
        self.assertIn("VISIBILITY-DELAY-EXCEEDS-SETTLE", str(ctx.exception))

    def test_condition_iii_clock_skew_exceeds_bound_refuses(self):
        runner = _Recorder(result=_refused_report(
            "CLOCK-SKEW-EXCEEDS-BOUND",
            "measured clock skew 4.400s exceeds max_skew 2.000s",
        ))
        with self.assertRaises(MultiboxPreflightRefused) as ctx:
            assert_preflight(self._shared_fs_settings(), "/local/state.db",
                             runner=runner)
        self.assertIn("CLOCK-SKEW-EXCEEDS-BOUND", str(ctx.exception))

    def test_every_refusal_carries_the_documented_mount_remedies(self):
        for finding_id in ("DB-ON-NETWORK-FS",
                           "VISIBILITY-DELAY-EXCEEDS-SETTLE",
                           "CLOCK-SKEW-EXCEEDS-BOUND"):
            runner = _Recorder(result=_refused_report(finding_id, "detail"))
            with self.assertRaises(MultiboxPreflightRefused) as ctx:
                assert_preflight(self._shared_fs_settings(), "/local/state.db",
                                 runner=runner)
            message = str(ctx.exception)
            self.assertIn("nfsvers=4.1", message, finding_id)
            self.assertIn("actimeo=1", message, finding_id)
            self.assertIn("lookupcache=none", message, finding_id)
            self.assertIn("cache=none", message, finding_id)

    def test_mount_remedies_constant_names_both_filesystems(self):
        self.assertIn("NFS", MOUNT_REMEDIES)
        self.assertIn("SMB", MOUNT_REMEDIES)

    def test_settle_and_skew_bounds_are_threaded_into_the_probe(self):
        runner = _Recorder(result=_ok_report())
        settings = self._shared_fs_settings(settle_seconds=7.5,
                                            max_skew_seconds=1.5)
        assert_preflight(settings, "/local/state.db", runner=runner)
        _, kwargs = runner.calls[0]
        self.assertEqual(kwargs["settle_seconds"], 7.5)
        self.assertEqual(kwargs["max_skew_seconds"], 1.5)

    def test_probe_exception_is_a_refusal_not_a_crash(self):
        runner = _Recorder(raises=OSError("share unreachable"))
        with self.assertRaises(MultiboxPreflightRefused) as ctx:
            assert_preflight(self._shared_fs_settings(), "/local/state.db",
                             runner=runner)
        self.assertIn("share unreachable", str(ctx.exception))

    def test_report_without_an_ok_key_is_treated_as_refusal(self):
        runner = _Recorder(result={"findings": []})
        with self.assertRaises(MultiboxPreflightRefused):
            assert_preflight(self._shared_fs_settings(), "/local/state.db",
                             runner=runner)

    def test_clean_report_passes_and_is_returned(self):
        runner = _Recorder(result=_ok_report())
        report = assert_preflight(self._shared_fs_settings(), "/local/state.db",
                                  runner=runner)
        self.assertTrue(report["ok"])

    def test_disabled_config_never_runs_the_probe(self):
        runner = _Recorder(result=_ok_report())
        settings = load_multibox_config(None, env=_clean_env())
        self.assertIsNone(assert_preflight(settings, "/local/state.db",
                                          runner=runner))
        self.assertFalse(runner.called)


class TestPreflightGateLocalTransport(unittest.TestCase):
    """transport=local has no share, so only condition (i) can bind."""

    def _local_settings(self):
        return load_multibox_config(
            {"multibox": {"enabled": True, "transport": "local"}},
            env=_clean_env(),
        )

    def test_local_db_passes_without_probing_a_share(self):
        runner = _Recorder(result=_ok_report())
        fs_kind = _Recorder(result="local")
        report = assert_preflight(self._local_settings(), "/local/state.db",
                                  runner=runner, fs_kind_fn=fs_kind)
        self.assertTrue(report["ok"])
        self.assertFalse(runner.called)
        self.assertTrue(fs_kind.called)

    def test_network_db_refuses_under_local_transport_too(self):
        fs_kind = _Recorder(result="cifs")
        with self.assertRaises(MultiboxPreflightRefused) as ctx:
            assert_preflight(self._local_settings(), "//server/share/state.db",
                             fs_kind_fn=fs_kind)
        self.assertIn("DB-ON-NETWORK-FS", str(ctx.exception))
        self.assertIn("nfsvers=4.1", str(ctx.exception))

    def test_unknown_fs_kind_is_treated_as_network(self):
        fs_kind = _Recorder(result="unknown")
        with self.assertRaises(MultiboxPreflightRefused):
            assert_preflight(self._local_settings(), "/somewhere/state.db",
                             fs_kind_fn=fs_kind)


# ---------------------------------------------------------------------------
# 3. Inert at default
# ---------------------------------------------------------------------------

class TestInertAtDefault(unittest.TestCase):
    def test_build_backend_returns_none_on_default_config(self):
        self.assertIsNone(build_backend("/tmp/state.db", None, env=_clean_env()))

    def test_build_backend_reaches_no_multibox_code_path_on_default_config(self):
        gate = _Recorder(result=None)
        local = _Recorder(result="LOCAL-SENTINEL")
        shared = _Recorder(result="FS-SENTINEL")
        result = build_backend(
            "/tmp/state.db", {"multibox": {"enabled": False}}, env=_clean_env(),
            _gate=gate, _local_factory=local, _shared_factory=shared,
        )
        self.assertIsNone(result)
        self.assertFalse(gate.called, "preflight gate must not run when disabled")
        self.assertFalse(local.called, "no local backend when disabled")
        self.assertFalse(shared.called, "no shared-fs backend when disabled")

    def test_coordination_modules_are_not_even_imported_at_default(self):
        """Strongest inertness sentinel: a fresh interpreter, default config."""
        script = (
            "import json, sys\n"
            "sys.path.insert(0, %r)\n"
            "from tools.multibox_config import build_backend\n"
            "backend = build_backend('state.db', {}, env={})\n"
            "print(json.dumps({\n"
            "  'backend': backend,\n"
            "  'fs_claim_log': 'state_store.fs_claim_log' in sys.modules,\n"
            "  'claim_backend': 'state_store.claim_backend' in sys.modules,\n"
            "  'lease_claims': 'state_store.lease_claims' in sys.modules,\n"
            "  'preflight': 'tools.multibox_preflight' in sys.modules,\n"
            "}))\n" % (str(ROOT),)
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        observed = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertIsNone(observed["backend"])
        self.assertFalse(observed["fs_claim_log"], "fs_claim_log imported")
        self.assertFalse(observed["claim_backend"], "claim_backend imported")
        self.assertFalse(observed["lease_claims"], "lease_claims imported")
        self.assertFalse(observed["preflight"], "preflight imported")

    def test_example_config_ships_multibox_inert(self):
        with open(ROOT / "aesop.config.example.json", encoding="utf-8") as handle:
            example = json.load(handle)
        self.assertIn("multibox", example)
        settings = load_multibox_config(example, env=_clean_env())
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["transport"], "local")
        self.assertIsNone(build_backend("/tmp/state.db", example,
                                        env=_clean_env()))

    def test_example_config_block_matches_the_documented_defaults(self):
        with open(ROOT / "aesop.config.example.json", encoding="utf-8") as handle:
            example = json.load(handle)
        block = {k: v for k, v in example["multibox"].items()
                 if not k.startswith("_")}
        self.assertEqual(block, dict(MULTIBOX_DEFAULTS))


class TestBuildBackendSelection(unittest.TestCase):
    def test_enabled_local_transport_gates_then_builds_local_backend(self):
        gate = _Recorder(result={"ok": True})
        local = _Recorder(result="LOCAL-SENTINEL")
        shared = _Recorder(result="FS-SENTINEL")
        result = build_backend(
            "/tmp/state.db", {"multibox": {"enabled": True}}, env=_clean_env(),
            _gate=gate, _local_factory=local, _shared_factory=shared,
        )
        self.assertEqual(result, "LOCAL-SENTINEL")
        self.assertTrue(gate.called)
        self.assertFalse(shared.called)

    def test_enabled_shared_fs_threads_every_setting_into_the_claim_log(self):
        gate = _Recorder(result={"ok": True})
        shared = _Recorder(result="FS-SENTINEL")
        config = {"multibox": {
            "enabled": True, "transport": "shared-fs",
            "shared_dir": "/mnt/share", "settle_seconds": 6.0,
            "max_skew_seconds": 1.5, "lease_ttl_seconds": 111,
            "case_policy": "insensitive",
        }}
        result = build_backend("/tmp/state.db", config, env=_clean_env(),
                               repo_root="/repo", epoch=4,
                               _gate=gate, _shared_factory=shared)
        self.assertEqual(result, "FS-SENTINEL")
        _, kwargs = shared.calls[0]
        self.assertEqual(kwargs["settle_seconds"], 6.0)
        self.assertEqual(kwargs["max_skew_seconds"], 1.5)
        self.assertEqual(kwargs["default_ttl_seconds"], 111)
        self.assertEqual(kwargs["case_policy"], "insensitive")
        self.assertEqual(kwargs["repo_root"], "/repo")
        self.assertEqual(kwargs["epoch"], 4)

    def test_gate_refusal_prevents_any_backend_from_being_constructed(self):
        gate = _Recorder(raises=MultiboxPreflightRefused("refused"))
        local = _Recorder(result="LOCAL-SENTINEL")
        shared = _Recorder(result="FS-SENTINEL")
        with self.assertRaises(MultiboxPreflightRefused):
            build_backend("/tmp/state.db", {"multibox": {"enabled": True}},
                          env=_clean_env(), _gate=gate,
                          _local_factory=local, _shared_factory=shared)
        self.assertFalse(local.called, "fail-closed: no backend after refusal")
        self.assertFalse(shared.called, "fail-closed: no backend after refusal")

    def test_real_local_backend_is_constructed_end_to_end(self):
        """No factory injection: the real LocalLeaseBackend must come back."""
        from state_store.claim_backend import LocalLeaseBackend

        gate = _Recorder(result={"ok": True})
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.db")
            backend = build_backend(db_path, {"multibox": {"enabled": True}},
                                    env=_clean_env(), _gate=gate)
            try:
                self.assertIsInstance(backend, LocalLeaseBackend)
            finally:
                backend.close()

    def test_real_shared_fs_backend_is_constructed_end_to_end(self):
        from state_store.fs_claim_log import FsClaimLog

        gate = _Recorder(result={"ok": True})
        with tempfile.TemporaryDirectory() as tmp:
            config = {"multibox": {
                "enabled": True, "transport": "shared-fs",
                "shared_dir": tmp, "settle_seconds": 0.01,
            }}
            backend = build_backend(os.path.join(tmp, "state.db"), config,
                                    env=_clean_env(), _gate=gate)
            self.assertIsInstance(backend, FsClaimLog)
            self.assertEqual(backend.settle_seconds, 0.01)
            self.assertEqual(backend.case_policy, "insensitive")


if __name__ == "__main__":
    unittest.main()
