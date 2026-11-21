"""Python side of the chat provider-config conformance fixture (spec H-R26).

Reads tests/fixtures/provider-config-cases.json and checks every case against
driver/backend_config.py validate_provider_entry. The Node side is
tests/chat-harness-spec.test.mjs (H-R26), which reads the same file. DNS is
stubbed so no real network call is made.
"""

import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "driver"))

import backend_config  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "provider-config-cases.json")


class ProviderConfigConformanceTests(unittest.TestCase):
    def setUp(self):
        # Offline: unresolvable names pass, so no DNS lookup leaves the test.
        patcher = mock.patch.object(backend_config, "_getaddrinfo_bounded", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_fixture_case_matches_python_verdict(self):
        with open(FIXTURE, "r", encoding="utf-8") as fh:
            cases = json.load(fh)
        self.assertTrue(isinstance(cases, list) and cases)
        for case in cases:
            ok, reason = backend_config.validate_provider_entry(case["config"])
            expect_ok = case["expect"] == "accept"
            self.assertEqual(ok, expect_ok, f"{case['reason']} (python reason: {reason})")

    def test_chat_shaped_entry_loads_as_driver_seat_unchanged(self):
        entry = {"type": "openai-compatible", "base_url": "http://127.0.0.1:11434/v1",
                 "models": ["qwen2.5:7b"], "is_local": True}
        self.assertEqual(backend_config.load_provider_seat(entry), entry)

    def test_refused_entry_raises_for_seat_loader(self):
        entry = {"type": "openai-compatible", "base_url": "https://api.anthropic.com/v1", "models": ["m"]}
        with self.assertRaises(ValueError):
            backend_config.load_provider_seat(entry)


if __name__ == "__main__":
    unittest.main()
