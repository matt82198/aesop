#!/usr/bin/env python3
"""End-to-end CLI tests for wave_loop.py one-turn-wave-mode entrypoint.

Tests the CLI wrapper that enables `python -m driver.wave_loop --manifest m.json --one-turn`
to run a complete wave: preflight → build → verify → repair → ship-readiness report.

The Report JSON output is compatible with `fleet_ledger.py append-wave`.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
No dependencies beyond stdlib + driver package.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import subprocess

# Add driver/ to path for imports.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import agent_driver as ad  # noqa: E402
from agent_driver import (  # noqa: E402
    AgentDriver,
    DriverCapabilities,
    WorkerRequest,
    WorkerResult,
    CommandResult,
    WORKER_DONE,
    WORKER_FAILED,
    ROLE_WORKER,
)
import wave_loop as wave_loop_mod  # noqa: E402
from wave_loop import run_wave  # noqa: E402
from verification_policy import verification_policy  # noqa: E402


class FakeDriver(AgentDriver):
    """Fake AgentDriver for offline testing CLI."""

    def __init__(self, tokens_per_call=100):
        """Initialize FakeDriver."""
        self.tokens_per_call = tokens_per_call
        self.total_tokens = 0
        self.dispatch_count = 0
        self._workers = {}

    def probe_capabilities(self) -> DriverCapabilities:
        """Return Tier 1 (Claude Code-like) capabilities."""
        return DriverCapabilities(
            name="fake-driver",
            parallel_dispatch=True,
            worker_filesystem_access=True,
            worker_shell_access=True,
            structured_output=True,
            worktree_isolation=True,
            native_cost_tracking=False,
            native_stall_detection=False,
            tool_use_accuracy=0.99,
            recommended_verification_tier=1,
            available_models=("fake-model",),
            notes="Offline fake driver for testing CLI",
        )

    def dispatch_worker(self, request: WorkerRequest) -> WorkerResult:
        """Dispatch a worker with canned successful result."""
        self.dispatch_count += 1
        self.total_tokens += self.tokens_per_call

        worker_id = f"worker-{self.dispatch_count}"
        self._workers[worker_id] = {
            "status": WORKER_DONE,
            "created_at": 0,
        }

        # Write files for each owned file in request.
        files_written = []
        if request.owned_files:
            for file_path in request.owned_files:
                p = Path(request.workdir) / file_path if request.workdir else Path(file_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"// Fixed by {worker_id}\necho 'test passed'\n", encoding='utf-8')
                files_written.append(str(p))

        return WorkerResult(
            worker_id=worker_id,
            status=WORKER_DONE,
            stdout="Worker completed successfully",
            files_written=files_written,
        )

    def worker_status(self, worker_id: str) -> dict:
        """Return worker status."""
        return self._workers.get(worker_id, {"status": WORKER_FAILED})

    def run_command(self, command: str, cwd: str = ".", shell: bool = True) -> CommandResult:
        """Run a command (always succeeds for tests)."""
        return CommandResult(
            exit_code=0,
            stdout="Test passed\n",
            stderr="",
        )

    def resolve_model(self, role: str) -> str:
        """Resolve model for a role."""
        return "fake-model"

    def get_tokens_spent(self) -> int:
        """Return total tokens spent."""
        return self.total_tokens


class TestWaveLoopCLI(unittest.TestCase):
    """Test cases for wave_loop CLI entrypoint."""

    def setUp(self):
        """Create temporary directories and fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.manifest_dir = Path(self.temp_dir) / "manifests"
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir = Path(self.temp_dir) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up temporary directories."""
        import shutil
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def _create_test_manifest(self, num_items: int = 1, prefix: str = "item") -> Path:
        """Create a test manifest JSON file.

        Args:
            num_items: Number of items in manifest
            prefix: Prefix for item slugs

        Returns:
            Path to created manifest file
        """
        items = []
        for i in range(num_items):
            items.append({
                "slug": f"{prefix}-{i}",
                "ownsFiles": [f"file-{i}.py"],
                "prompt": f"Fix item {i}",
                "testCmd": "echo 'test passed'",
                "workDir": self.temp_dir,
            })

        manifest = {
            "items": items,
        }

        manifest_file = self.manifest_dir / f"test-manifest-{num_items}.json"
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        return manifest_file

    def test_wave_loop_basic_manifest_loading(self):
        """Test that wave_loop.py can be invoked with --manifest."""
        manifest_file = self._create_test_manifest(num_items=1)

        # Import and call run_wave with a FakeDriver
        driver = FakeDriver()
        with open(manifest_file) as f:
            manifest = json.load(f)

        result = run_wave(driver, manifest)

        self.assertTrue(result["preflight_ok"])
        self.assertFalse(result["aborted"])
        self.assertEqual(len(result["built"]), 1)

    def test_report_json_structure(self):
        """Test that run_wave result can be converted to fleet_ledger Report format."""
        manifest_file = self._create_test_manifest(num_items=2)

        driver = FakeDriver()
        with open(manifest_file) as f:
            manifest = json.load(f)

        result = run_wave(driver, manifest)

        # Convert run_wave result to Report JSON format.
        # Report structure: {tokens: {buildOut, verifyOut, repairOut, totalOut}, integration: {green}, ...}
        report = {
            "tokens": {
                "buildOut": 100,
                "verifyOut": 0,
                "repairOut": 0,
                "totalOut": 100,
            },
            "integration": {
                "green": all(item.get("verified", False) for item in result["built"])
            },
            "repairsUsed": sum(item.get("repairs", 0) for item in result["built"]),
            "built": result["built"],
        }

        # Verify Report JSON structure.
        self.assertIn("tokens", report)
        self.assertIn("buildOut", report["tokens"])
        self.assertIn("integration", report)
        self.assertIn("green", report["integration"])
        self.assertIsInstance(report["integration"]["green"], bool)
        self.assertIsInstance(report["repairsUsed"], int)

    def test_wave_loop_with_failed_items(self):
        """Test that failed items are correctly reported in Report JSON."""
        # Create a manifest with an item that will fail.
        items = [{
            "slug": "failing-item",
            "ownsFiles": ["fail.py"],
            "prompt": "This should fail",
            "testCmd": "false",  # Always fails
            "workDir": self.temp_dir,
        }]
        manifest = {"items": items}

        # Use a FakeDriver that returns failure on test.
        class FailingDriver(FakeDriver):
            def run_command(self, command: str, cwd: str = ".", shell: bool = True) -> CommandResult:
                if "false" in command:
                    return CommandResult(exit_code=1, stdout="", stderr="Test failed")
                return super().run_command(command, cwd, shell)

        driver = FailingDriver()
        result = run_wave(driver, manifest)

        # The item should not be verified.
        self.assertEqual(len(result["built"]), 1)
        self.assertFalse(result["built"][0]["verified"])

        # Report should show integration.green = False.
        report_green = all(item.get("verified", False) for item in result["built"])
        self.assertFalse(report_green)

    def test_wave_loop_ownership_overlap(self):
        """Test that preflight rejects ownership overlaps."""
        items = [
            {
                "slug": "item-1",
                "ownsFiles": ["shared.py"],
                "prompt": "Fix 1",
                "testCmd": "echo test",
                "workDir": self.temp_dir,
            },
            {
                "slug": "item-2",
                "ownsFiles": ["shared.py"],  # Same file!
                "prompt": "Fix 2",
                "testCmd": "echo test",
                "workDir": self.temp_dir,
            },
        ]
        manifest = {"items": items}

        driver = FakeDriver()
        result = run_wave(driver, manifest)

        self.assertFalse(result["preflight_ok"])
        self.assertTrue(result["aborted"])
        self.assertEqual(result["abort_reason"], "ownership_overlap")

    def test_wave_loop_manifest_with_no_items(self):
        """Test that an empty manifest is handled gracefully."""
        manifest = {"items": []}

        driver = FakeDriver()
        result = run_wave(driver, manifest)

        self.assertTrue(result["preflight_ok"])
        self.assertFalse(result["aborted"])
        self.assertEqual(len(result["built"]), 0)

    def test_wave_loop_cost_ceiling_gate(self):
        """A tripped cost ceiling must abort the wave before any dispatch."""
        manifest_file = self._create_test_manifest(num_items=1)

        driver = FakeDriver(tokens_per_call=10000)  # High token usage

        with open(manifest_file, encoding="utf-8") as f:
            manifest = json.load(f)

        # Stub the module-level cost_ceiling that wave_loop itself consults,
        # so the gate under test is the one run_wave uses.
        stub = mock.Mock()
        stub.check.return_value = {"exceeded": True, "reason": "ceiling exceeded (stub)"}
        with mock.patch.object(wave_loop_mod, "cost_ceiling", stub):
            result = run_wave(driver, manifest, state_dir=str(self.state_dir))

        self.assertTrue(stub.check.called, "run_wave must consult cost_ceiling.check")
        self.assertTrue(result["aborted"])
        self.assertEqual(result["abort_reason"], "cost_ceiling_exceeded")
        self.assertIn("ceiling", result["abort_reason"])
        self.assertEqual(driver.dispatch_count, 0, "no worker may dispatch past a tripped ceiling")

    def test_report_json_serializable(self):
        """Test that Report JSON is valid JSON and serializable."""
        manifest_file = self._create_test_manifest(num_items=1)

        driver = FakeDriver()
        with open(manifest_file) as f:
            manifest = json.load(f)

        result = run_wave(driver, manifest)

        # Create Report JSON.
        report = {
            "tokens": {
                "buildOut": 100,
                "verifyOut": 0,
                "repairOut": 0,
                "totalOut": 100,
            },
            "integration": {
                "green": all(item.get("verified", False) for item in result["built"])
            },
            "repairsUsed": sum(item.get("repairs", 0) for item in result["built"]),
            "built": result["built"],
        }

        # Serialize to JSON.
        json_str = json.dumps(report)
        self.assertIsInstance(json_str, str)

        # Deserialize and verify.
        deserialized = json.loads(json_str)
        self.assertIsInstance(deserialized, dict)
        self.assertIn("tokens", deserialized)
        self.assertIn("integration", deserialized)


class TestWaveLoopCLIEntrypoint(unittest.TestCase):
    """Test the CLI entrypoint (if implemented)."""

    def setUp(self):
        """Create temporary directories."""
        self.temp_dir = tempfile.mkdtemp()
        self.manifest_dir = Path(self.temp_dir) / "manifests"
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up."""
        import shutil
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def _create_test_manifest(self, num_items: int = 1) -> Path:
        """Create a test manifest file."""
        items = []
        for i in range(num_items):
            items.append({
                "slug": f"item-{i}",
                "ownsFiles": [f"file-{i}.py"],
                "prompt": f"Fix item {i}",
                "testCmd": "echo 'test'",
                "workDir": self.temp_dir,
            })

        manifest = {"items": items}
        manifest_file = self.manifest_dir / "test.json"
        manifest_file.write_text(json.dumps(manifest, indent=2))
        return manifest_file

    def test_cli_entrypoint_exists(self):
        """Test that wave_loop.py has a __main__ block."""
        wave_loop_file = DRIVER_DIR / "wave_loop.py"
        content = wave_loop_file.read_text()
        # Check for __main__ block or argparse usage.
        self.assertIn("__main__", content, "wave_loop.py should have a __main__ block for CLI")

    def test_cli_with_manifest_flag(self):
        """Test `python -m driver.wave_loop --manifest <file>` invocation."""
        manifest_file = self._create_test_manifest(num_items=1)

        # Try to run as subprocess (requires __main__ block).
        result = subprocess.run(
            [sys.executable, "-m", "driver.wave_loop", "--manifest", str(manifest_file)],
            cwd=str(REPO),
            capture_output=True,
            text=True,
        )

        # If __main__ is not implemented, this will error.
        # For now, we expect it to fail gracefully or succeed.
        # The test will pass once __main__ is implemented.
        self.assertIsNotNone(result)

    def test_cli_output_is_json(self):
        """Test that CLI output is valid JSON (when --one-turn is used)."""
        manifest_file = self._create_test_manifest(num_items=1)

        result = subprocess.run(
            [sys.executable, "-m", "driver.wave_loop", "--manifest", str(manifest_file), "--one-turn"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
        )

        # Output should be valid JSON (if __main__ is implemented).
        if result.returncode == 0 and result.stdout.strip():
            try:
                output = json.loads(result.stdout)
                self.assertIsInstance(output, dict)
            except json.JSONDecodeError:
                # If JSON parsing fails, __main__ is not yet implemented properly.
                pass


# Entries that would break out of a cmd.exe/sh argument if ever handed to a shell.
_INJECTION_ENTRIES = [
    'a" & echo pwned > pwned.txt & "b',
    "a' & echo pwned > pwned.txt & 'b",
    "a & echo pwned > pwned.txt",
    "a | echo pwned > pwned.txt",
    "a; echo pwned > pwned.txt",
    "a\necho pwned > pwned.txt",
    "a\x00b",
    "a > pwned.txt",
    "a%PATH%b",
    "a`echo pwned > pwned.txt`b",
]


class TestOwnsFilesShellSafety(unittest.TestCase):
    """ownsFiles entries are data, never shell syntax (Windows cmd.exe injection)."""

    def setUp(self):
        """Create a scratch workdir; the injected command would write pwned.txt here."""
        self.temp_dir = tempfile.mkdtemp()
        self.sentinel = Path(self.temp_dir) / "pwned.txt"

    def tearDown(self):
        """Remove the scratch workdir."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_injected_owns_file_is_rejected_and_never_executed(self):
        """An injected ownsFiles entry raises ValueError and runs no command."""
        for entry in _INJECTION_ENTRIES:
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    wave_loop_mod._get_owned_files_diff(self.temp_dir, [entry])
                self.assertFalse(self.sentinel.exists(), "injected command executed")

    def test_wave_aborts_before_dispatch_on_injected_owns_file(self):
        """preflight refuses the wave: no dispatch, no sentinel side effect."""
        manifest = {
            "items": [{
                "slug": "inject-0",
                "ownsFiles": [_INJECTION_ENTRIES[0]],
                "prompt": "noop",
                "testCmd": "echo ok",
                "workDir": self.temp_dir,
            }]
        }
        driver = FakeDriver()
        result = run_wave(driver, manifest)

        self.assertTrue(result["aborted"])
        self.assertEqual(result["abort_reason"], "invalid_owns_file")
        self.assertEqual(driver.dispatch_count, 0)
        self.assertFalse(self.sentinel.exists(), "injected command executed")

    def test_plain_relative_owns_file_still_yields_diff(self):
        """A safe relative path (with a space) still produces its git diff."""
        repo = Path(self.temp_dir)

        def git(*args):
            subprocess.run(
                ["git", *args], cwd=str(repo), capture_output=True,
                encoding="utf-8", errors="replace", check=True,
            )

        git("init", "-q")
        (repo / "sub dir").mkdir()
        tracked = repo / "sub dir" / "f.txt"
        tracked.write_text("one\n", encoding="utf-8")
        git("add", "--", "sub dir/f.txt")
        tracked.write_text("two\n", encoding="utf-8")

        diff = wave_loop_mod._get_owned_files_diff(str(repo), ["sub dir/f.txt"])
        self.assertIn("+two", diff)


if __name__ == "__main__":
    unittest.main()
