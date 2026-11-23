#!/usr/bin/env python3
"""HeadlessCliDriver -- RED STAGE STUB (ledger f-2e9a2cfb, S1).

This file exists only so tests/test_headless_cli_driver.py can import the
class. It has NO behavior: every method raises NotImplementedError. The
contract the tests pin down is documented in the test module, not here.

Planned profiles: "codex-exec" (codex exec CLI) and "agy-headless"
(agy -p ... --output-format json --print-timeout ...).
"""

from agent_driver import AgentDriver

PROFILES = ("codex-exec", "agy-headless")


class HeadlessCliDriver(AgentDriver):
    """Generic headless-CLI AgentDriver (S1 stub, no behavior)."""

    name = "headless-cli"

    def __init__(self, profile, model=None, provider=None):
        raise NotImplementedError("HeadlessCliDriver is a S1 red stub")

    def probe_capabilities(self):
        raise NotImplementedError("HeadlessCliDriver is a S1 red stub")

    def dispatch_worker(self, request):
        raise NotImplementedError("HeadlessCliDriver is a S1 red stub")

    def worker_status(self, worker_id):
        raise NotImplementedError("HeadlessCliDriver is a S1 red stub")

    def run_command(self, command, cwd=None, shell=None):
        raise NotImplementedError("HeadlessCliDriver is a S1 red stub")

    def resolve_model(self, role):
        raise NotImplementedError("HeadlessCliDriver is a S1 red stub")
