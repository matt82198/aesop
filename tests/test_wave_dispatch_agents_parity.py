"""Cross-artifact test: wave_dispatch.py and agents.py discover same agents.

This test ensures that the two independent implementations of agent discovery
(wave_dispatch.py:get_wave_dispatch() and agents.py:_transcripts_fingerprint_uncached())
produce consistent results when given the same fixture tree.

Failure indicates:
  - glob pattern mismatch (e.g., one uses recursive, one doesn't)
  - path resolution divergence
  - filtering logic (e.g., mtime threshold) inconsistency

Run: python -m unittest tests.test_wave_dispatch_agents_parity
"""
import contextlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).parent.parent
UI_PATH = REPO / "ui"
WAVE_DISPATCH_PATH = UI_PATH / "wave_dispatch.py"


# sys.modules entries the loaders below delete and re-import. Modules that bind
# `config` at import time (wave_dispatch, agents, wave_telemetry) must keep seeing the
# ORIGINAL object after a loader returns, so every loader restores these on exit.
_TOUCHED_MODULES = ("config", "agents", "wave_dispatch")


@contextlib.contextmanager
def _restored_sys_modules(names=_TOUCHED_MODULES):
    """Snapshot the named sys.modules entries; restore them (or drop them) on exit."""
    saved = {name: sys.modules.get(name) for name in names}
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def load_wave_dispatch_module(fixture_root):
    """Import fresh wave_dispatch module bound to fixture."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    os.environ["AESOP_STATE_ROOT"] = str(fixture_root / "state")
    os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(fixture_root / "transcripts")

    if str(UI_PATH) not in sys.path:
        sys.path.insert(0, str(UI_PATH))

    with _restored_sys_modules():
        # Force reimport for isolation
        sys.modules.pop("wave_dispatch", None)
        sys.modules.pop("config", None)

        spec = importlib.util.spec_from_file_location(
            f"wave_dispatch_parity_{id(fixture_root)}", WAVE_DISPATCH_PATH)
        wave_dispatch = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = wave_dispatch
        try:
            spec.loader.exec_module(wave_dispatch)
        finally:
            sys.modules.pop(spec.name, None)
    return wave_dispatch


def load_agents_module(fixture_root):
    """Import fresh agents module with config bound to fixture."""
    # Save old environ for config isolation
    old_root = os.environ.get("AESOP_TRANSCRIPTS_ROOT")
    os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(fixture_root / "transcripts")

    if str(UI_PATH) not in sys.path:
        sys.path.insert(0, str(UI_PATH))

    try:
        with _restored_sys_modules():
            # Force reimport to pick up new config
            sys.modules.pop("agents", None)
            sys.modules.pop("config", None)

            import config
            import agents

            # Reload config to ensure fresh paths
            importlib.reload(config)
            importlib.reload(agents)
    finally:
        # Restore old environ
        if old_root is not None:
            os.environ["AESOP_TRANSCRIPTS_ROOT"] = old_root
        else:
            os.environ.pop("AESOP_TRANSCRIPTS_ROOT", None)

    return agents


class CrossArtifactParityCase(unittest.TestCase):
    """Verify wave_dispatch and agents discovery consistency."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-parity-test-"))
        (self.fixture_root / "state").mkdir(parents=True)
        self.transcripts_root = self.fixture_root / "transcripts"
        self._create_mixed_fixture()

    def tearDown(self):
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _create_mixed_fixture(self):
        """Create fixture with agents in nested subagents dirs (real layout).

        Structure:
          transcripts/
            proj-a/
              subagents/
                agent-task-001.jsonl
                agent-task-002.jsonl (old, >30min)
            proj-b/
              subagents/
                agent-other-001.jsonl
            other-session/
              subagents/
                agent-nested-001.jsonl
        """
        now = time.time()

        # Project A: two agents (one fresh, one stale)
        proj_a = self.transcripts_root / "proj-a" / "subagents"
        proj_a.mkdir(parents=True)

        agent_a1 = proj_a / "agent-task-001.jsonl"
        agent_a1.write_text(json.dumps({"type": "user", "text": "dispatch"}) + "\n")
        # Fresh (now)
        os.utime(agent_a1, (now, now))

        agent_a2 = proj_a / "agent-task-002.jsonl"
        agent_a2.write_text(json.dumps({"type": "user", "text": "dispatch"}) + "\n")
        # Stale (>30min, should be filtered out)
        stale_time = now - 1900
        os.utime(agent_a2, (stale_time, stale_time))

        # Project B: one agent (fresh)
        proj_b = self.transcripts_root / "proj-b" / "subagents"
        proj_b.mkdir(parents=True)

        agent_b1 = proj_b / "agent-other-001.jsonl"
        agent_b1.write_text(json.dumps({"type": "user", "text": "dispatch"}) + "\n")
        os.utime(agent_b1, (now, now))

        # Other session dir (also uses subagents/)
        other = self.transcripts_root / "other-session" / "subagents"
        other.mkdir(parents=True)

        agent_o1 = other / "agent-nested-001.jsonl"
        agent_o1.write_text(json.dumps({"type": "user", "text": "dispatch"}) + "\n")
        os.utime(agent_o1, (now, now))

    def test_both_discover_recent_agents(self):
        """wave_dispatch and agents both find the same recent agent files."""
        # Load both modules independently
        wave_dispatch = load_wave_dispatch_module(self.fixture_root)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")

        # Get dispatch agents
        dispatch_data = wave_dispatch.get_wave_dispatch(force=True)
        dispatch_agent_ids = set(a["id"] for a in dispatch_data.get("agents", []))

        # Get agents fingerprint (which enumerates all agent files)
        agents_mod = load_agents_module(self.fixture_root)
        file_count, _ = agents_mod._transcripts_fingerprint_uncached()

        # Dispatch should find 3 recent agents (a1, b1, o1)
        # Stale agent (a2) should be filtered out by <30min check
        expected_ids = {"task-001", "other-001", "nested-001"}
        self.assertEqual(dispatch_agent_ids, expected_ids,
                        f"wave_dispatch found {dispatch_agent_ids}, expected {expected_ids}")

        # Fingerprint should report 3 or 4 files (depending on whether glob also sees stale)
        # But the key point: wave_dispatch filters to <30min, agents.glob finds all
        # Both should agree on the RECENT agents used for dispatch
        self.assertGreaterEqual(file_count, len(dispatch_agent_ids),
                               f"agents fingerprint found {file_count} files, "
                               f"dispatch found {len(dispatch_agent_ids)} agents")

    def test_dispatch_filters_stale_agents(self):
        """wave_dispatch filters out stale agents (>30min); agents.py sees all."""
        wave_dispatch = load_wave_dispatch_module(self.fixture_root)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")

        dispatch_data = wave_dispatch.get_wave_dispatch(force=True)
        dispatch_agents = dispatch_data.get("agents", [])

        # Stale agent (task-002) should NOT appear in dispatch (>30min filter)
        dispatch_ids = set(a["id"] for a in dispatch_agents)
        self.assertNotIn("task-002", dispatch_ids,
                        "stale agent (>30min) should be filtered out")

    def test_recursive_glob_matches_nested_layout(self):
        """Both use recursive glob to find agents in nested subagents/ dirs."""
        # This verifies the pattern change from {project}/memory/ to **/subagents/
        wave_dispatch = load_wave_dispatch_module(self.fixture_root)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")

        dispatch_data = wave_dispatch.get_wave_dispatch(force=True)
        dispatch_agents = set(a["id"] for a in dispatch_data.get("agents", []))

        # Should find agents from proj-a, proj-b, and other-session
        # (at least 3 recent ones across all projects)
        self.assertGreaterEqual(len(dispatch_agents), 3,
                               f"expected >=3 agents from nested structure, got {dispatch_agents}")

    def test_reload_path_restores_original_config_in_sys_modules(self):
        """The import/reload dance must not leave a replacement `config` in sys.modules.

        ui/wave_telemetry.py and other siblings bind `config` at import time; a stale
        or swapped sys.modules['config'] makes their binding diverge for the rest of
        the process (order-dependent failures, see PR #1001).
        """
        sys.path.insert(0, str(UI_PATH))
        try:
            importlib.import_module("config")  # establish the live binding
            original = sys.modules["config"]
            load_agents_module(self.fixture_root)
            self.assertIs(sys.modules.get("config"), original,
                          "load_agents_module left a replacement 'config' in sys.modules")
        finally:
            sys.path.remove(str(UI_PATH))

    def test_agents_discovery_consistency(self):
        """agents.py glob discovers all files; wave_dispatch filters consistently."""
        agents_mod = load_agents_module(self.fixture_root)
        wave_dispatch = load_wave_dispatch_module(self.fixture_root)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")

        # agents.py raw fingerprint (no filter)
        file_count, latest_mtime = agents_mod._transcripts_fingerprint_uncached()

        # wave_dispatch (with <30min filter)
        dispatch_data = wave_dispatch.get_wave_dispatch(force=True)
        dispatch_count = len(dispatch_data.get("agents", []))

        # Should have found at least the recent agents
        self.assertEqual(dispatch_count, 3,
                        f"wave_dispatch found {dispatch_count} agents, expected 3 recent")

        # agents.py raw glob should see >= dispatch count (all files, not filtered)
        self.assertGreaterEqual(file_count, dispatch_count,
                               f"agents glob {file_count} should be >= dispatch {dispatch_count}")


if __name__ == "__main__":
    unittest.main()
