#!/usr/bin/env python3
"""Live-spend guard for tools/bench_api_runner.py.

make_api_runner is a second paid live path (alongside bench/frontier_slice.py).
These tests pin two rules without any network or key:

- AESOP_BENCH_OFFLINE=1 is checked before any anthropic client is constructed,
  even when the caller passes confirm_spend=True.
- Without the explicit confirm-spend gate, no client is constructed and main()
  exits 2 before any paid call.

Driven in-process with a stub `anthropic` module patched onto the runner module.
"""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

tools_dir = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(tools_dir))

import bench_api_runner  # noqa: E402

_SEAM_ENV = "AESOP_BENCH_OFFLINE"


class _ClientConstructed(Exception):
    """Raised by the stub anthropic.Anthropic when the live path builds a client."""


def _stub_anthropic():
    stub = types.ModuleType("anthropic")

    def _construct(*args, **kwargs):
        raise _ClientConstructed("anthropic.Anthropic() was constructed")

    stub.Anthropic = _construct
    return stub


class TestApiRunnerLiveGuard(unittest.TestCase):

    def _env(self, seam_value):
        env = {"BENCH_API_KEY": "dummy-key-for-test", "ANTHROPIC_API_KEY": "dummy-key-for-test"}
        if seam_value is not None:
            env[_SEAM_ENV] = seam_value
        return env

    def test_seam_blocks_client_construction(self):
        """Seam set: make_api_runner raises before any client is constructed."""
        with mock.patch.dict(os.environ, self._env("1"), clear=False), \
                mock.patch.object(bench_api_runner, "anthropic", _stub_anthropic()):
            os.environ[_SEAM_ENV] = "1"
            with self.assertRaises(RuntimeError) as ctx:
                bench_api_runner.make_api_runner("m", "k")
        self.assertIn("AESOP_BENCH_OFFLINE", str(ctx.exception))

    def test_seam_blocks_client_construction_even_with_confirm_spend(self):
        """Seam set + confirm_spend=True: still no client constructed."""
        with mock.patch.dict(os.environ, self._env("1"), clear=False), \
                mock.patch.object(bench_api_runner, "anthropic", _stub_anthropic()):
            os.environ[_SEAM_ENV] = "1"
            with self.assertRaises(RuntimeError):
                bench_api_runner.make_api_runner("m", "k", confirm_spend=True)

    def test_no_confirm_spend_blocks_client_construction(self):
        """Seam unset, no confirm_spend: the paid path refuses before constructing a client."""
        with mock.patch.dict(os.environ, self._env(None), clear=False), \
                mock.patch.object(bench_api_runner, "anthropic", _stub_anthropic()):
            os.environ.pop(_SEAM_ENV, None)
            with self.assertRaises(RuntimeError) as ctx:
                bench_api_runner.make_api_runner("m", "k")
        self.assertIn("confirm", str(ctx.exception).lower())

    def test_main_without_confirm_spend_exits_2_and_never_constructs(self):
        """main() without --confirm-spend returns 2; no client constructed."""
        with mock.patch.dict(os.environ, self._env(None), clear=False), \
                mock.patch.object(bench_api_runner, "anthropic", _stub_anthropic()):
            os.environ.pop(_SEAM_ENV, None)
            rc = bench_api_runner.main(["--variants", "v2", "--models", "haiku"])
        self.assertEqual(rc, 2)

    def test_seam_unset_with_confirm_spend_reaches_client_construction(self):
        """Control: seam unset + confirm_spend=True does reach the client (stub raises)."""
        with mock.patch.dict(os.environ, self._env(None), clear=False), \
                mock.patch.object(bench_api_runner, "anthropic", _stub_anthropic()):
            os.environ.pop(_SEAM_ENV, None)
            with self.assertRaises(_ClientConstructed):
                bench_api_runner.make_api_runner("m", "k", confirm_spend=True)


if __name__ == "__main__":
    unittest.main()
