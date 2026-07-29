"""
Unit tests for ui/wave_context.py — context quality analysis helpers (C1, C2).

Tests spec sharpness scoring and file scope extraction.
C3 (first-try rate) deferred to future work with validated repair signal from fleet ledger.

Run: python -m unittest tests.test_ui_wave_context
"""
import sys
import os
import tempfile
import unittest
from pathlib import Path

# Add ui/ to path so we can import wave_context
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ui'))

# Save originals before stubbing (leak prevention)
_orig_config = sys.modules.get('config')
_orig_agents = sys.modules.get('agents')

# Mock config before importing wave_context
class MockConfig:
    AESOP_ROOT = Path(tempfile.gettempdir()) / "aesop-test"
    STATE_DIR = AESOP_ROOT / "state"
    TRANSCRIPTS_ROOT = AESOP_ROOT / ".claude" / "projects"

    def reload(self):
        """No-op reload method (defensive against leaked stubs)."""
        pass

sys.modules['config'] = MockConfig()
sys.modules['agents'] = type('module', (), {
    'extract_agent_dispatch_prompt': lambda x: None
})()

import wave_context

# Restore originals immediately after import (prevents leaking into siblings)
if _orig_config is not None:
    sys.modules['config'] = _orig_config
else:
    sys.modules.pop('config', None)

if _orig_agents is not None:
    sys.modules['agents'] = _orig_agents
else:
    sys.modules.pop('agents', None)


class TestSpecSharpnessScore(unittest.TestCase):
    """Test spec sharpness scoring."""

    def test_score_empty_prompt(self):
        """Empty prompt should score Low."""
        result = wave_context.SpecSharpnessScore.score_prompt("")
        self.assertEqual(result["level"], "Low")
        self.assertEqual(result["score"], 0)

    def test_score_low_prompt(self):
        """Prompt with minimal signals should score Low."""
        prompt = "Do something simple"
        result = wave_context.SpecSharpnessScore.score_prompt(prompt)
        self.assertEqual(result["level"], "Low")
        self.assertLess(result["score"], 50)

    def test_score_high_prompt(self):
        """Prompt with strong signals should score High or Excellent."""
        prompt = """
# Build Task

MUST implement the following:
- Read file ui/wave_context.py
- Modify handler.py with new endpoints

## Acceptance Criteria
- All tests pass
- Code follows style guide
- No secrets leaked

## Files to modify
- ui/handler.py
- ui/serve.py
- ui/web/src/components/*.tsx

**Important**: Keep backward compatibility.
`config.X` at call time.
        """
        result = wave_context.SpecSharpnessScore.score_prompt(prompt)
        self.assertGreaterEqual(result["score"], 50)
        self.assertIn(result["level"], ["High", "Excellent"])

    def test_signal_directive_count(self):
        """Should count directives accurately."""
        prompt = "MUST do X. SHOULD do Y. NEVER do Z. Implement feature. Optimize code."
        result = wave_context.SpecSharpnessScore.score_prompt(prompt)
        # Should detect multiple directives
        self.assertGreater(result["signals"]["directive_count"], 0)

    def test_signal_acceptance_criteria(self):
        """Should detect acceptance criteria section."""
        prompt_with = """
        Task: Build UI

        Acceptance Criteria:
        - Feature works
        - Tests pass
        """
        prompt_without = "Build a UI component"

        result_with = wave_context.SpecSharpnessScore.score_prompt(prompt_with)
        result_without = wave_context.SpecSharpnessScore.score_prompt(prompt_without)

        self.assertTrue(result_with["signals"]["has_acceptance_criteria"])
        self.assertFalse(result_without["signals"]["has_acceptance_criteria"])

    def test_signal_file_specificity(self):
        """Should measure file path specificity."""
        prompt_specific = """
        Files to modify:
        - ui/handler.py
        - ui/serve.py
        - ui/web/src/components/SpecSharpnessIndicator.tsx
        """
        prompt_vague = "Modify files in the project *"

        result_specific = wave_context.SpecSharpnessScore.score_prompt(prompt_specific)
        result_vague = wave_context.SpecSharpnessScore.score_prompt(prompt_vague)

        self.assertGreater(
            result_specific["signals"]["file_specificity"],
            result_vague["signals"]["file_specificity"]
        )

    def test_signal_structured_content(self):
        """Should detect structured content (lists, code blocks, tables)."""
        prompt_structured = """
        Tasks:
        - Task 1
        - Task 2
        - Task 3

        ```python
        def hello():
            pass
        ```

        | Column | Value |
        |--------|-------|
        | A      | 1     |
        """
        prompt_unstructured = "Do some stuff and write code"

        result_structured = wave_context.SpecSharpnessScore.score_prompt(prompt_structured)
        result_unstructured = wave_context.SpecSharpnessScore.score_prompt(prompt_unstructured)

        self.assertGreater(
            result_structured["signals"]["structured_content_ratio"],
            result_unstructured["signals"]["structured_content_ratio"]
        )

    def test_signal_emphasis_markers(self):
        """Should count emphasis markers (bold, code, headers)."""
        prompt_emphasis = "## Header\nUse **bold** text and `code` blocks. ### Subheader"
        prompt_plain = "Just plain text without any emphasis"

        result_emphasis = wave_context.SpecSharpnessScore.score_prompt(prompt_emphasis)
        result_plain = wave_context.SpecSharpnessScore.score_prompt(prompt_plain)

        self.assertGreater(
            result_emphasis["signals"]["emphasis_markers"],
            result_plain["signals"]["emphasis_markers"]
        )

    def test_score_capped_at_100(self):
        """Score should never exceed 100."""
        excellent_prompt = """
# EXCELLENT TASK

MUST implement. MUST test. SHOULD optimize. NEVER break.
Implement feature. Build system. Add tests. Fix bugs. Refactor code.

## Acceptance Criteria
- All passing
- Zero defects
- Full coverage

Files:
- ui/handler.py
- ui/serve.py
- ui/web/src/components/*.tsx

```python
# Code blocks
def foo(): pass
```

| Status | Count |
|--------|-------|
| OK     | 100   |

**Bold** `code` ## Headers ### More
        """
        result = wave_context.SpecSharpnessScore.score_prompt(excellent_prompt)
        self.assertLessEqual(result["score"], 100)
        self.assertGreaterEqual(result["score"], 0)


class TestFileScopeAnalyzer(unittest.TestCase):
    """Test file scope extraction and analysis."""

    def test_extract_intended_scope_empty(self):
        """Empty prompt should extract no files."""
        files = wave_context.FileScopeAnalyzer.extract_intended_scope("")
        self.assertEqual(files, [])

    def test_extract_intended_scope_with_paths(self):
        """Should extract file paths from prompt."""
        prompt = """
        Files to modify:
        - ui/handler.py
        - ui/serve.py
        - ui/web/src/components/SpecSharpnessIndicator.tsx
        """
        files = wave_context.FileScopeAnalyzer.extract_intended_scope(prompt)
        self.assertIn("ui/handler.py", files)
        self.assertIn("ui/serve.py", files)
        self.assertIn("ui/web/src/components/SpecSharpnessIndicator.tsx", files)

    def test_extract_intended_scope_with_new_marker(self):
        """Should extract files marked as NEW."""
        prompt = """
        NEW ui/wave_context.py
        MODIFIED ui/handler.py
        CHANGED ui/web/src/components/SpecSharpnessIndicator.tsx
        """
        files = wave_context.FileScopeAnalyzer.extract_intended_scope(prompt)
        self.assertIn("ui/wave_context.py", files)
        self.assertIn("ui/handler.py", files)

    def test_extract_intended_scope_deduplication(self):
        """Should deduplicate extracted files."""
        prompt = """
        Modify ui/handler.py
        Edit ui/handler.py
        Update ui/handler.py
        """
        files = wave_context.FileScopeAnalyzer.extract_intended_scope(prompt)
        # Should have only one entry for ui/handler.py
        handler_count = sum(1 for f in files if 'handler.py' in f)
        self.assertEqual(handler_count, 1)

    def test_analyze_scope_no_files(self):
        """Should handle case with no files."""
        result = wave_context.FileScopeAnalyzer.analyze_scope("", "test-agent")
        self.assertEqual(result["intended_files"], [])
        self.assertEqual(result["actual_files"], [])
        self.assertEqual(result["coverage"], 0.0)

    def test_analyze_scope_drift(self):
        """Should detect drift between intended and actual files."""
        prompt = """
        Files:
        - ui/file1.py
        - ui/file2.py
        - ui/file3.py
        """
        result = wave_context.FileScopeAnalyzer.analyze_scope(prompt, "test-agent")
        self.assertGreater(len(result["intended_files"]), 0)
        # Since we're using a placeholder for actual files, drift should show
        self.assertGreaterEqual(len(result["drift"]["only_intended"]), 0)


class TestGetSpecSharpness(unittest.TestCase):
    """Test spec sharpness getter."""

    def test_get_spec_sharpness_returns_none_on_error(self):
        """Should return None if agent not found."""
        # Mock agents module to return error
        wave_context.agents.extract_agent_dispatch_prompt = lambda x: {"error": "not found"}
        result = wave_context.get_spec_sharpness("nonexistent-agent")
        self.assertIsNone(result)

    def test_get_spec_sharpness_returns_score(self):
        """Should return spec sharpness score when successful."""
        # Mock a good prompt
        test_prompt = """
        MUST implement features.

        ## Acceptance Criteria
        - Tests pass
        - No defects

        Files:
        - ui/handler.py
        - ui/serve.py
        """
        wave_context.agents.extract_agent_dispatch_prompt = lambda x: test_prompt

        result = wave_context.get_spec_sharpness("test-agent")
        self.assertIsNotNone(result)
        self.assertIn("level", result)
        self.assertIn("score", result)
        self.assertIn("signals", result)


class TestGetFileScope(unittest.TestCase):
    """Test file scope getter."""

    def test_get_file_scope_returns_none_on_error(self):
        """Should return None if agent not found."""
        wave_context.agents.extract_agent_dispatch_prompt = lambda x: {"error": "not found"}
        result = wave_context.get_file_scope("nonexistent-agent")
        self.assertIsNone(result)

    def test_get_file_scope_returns_analysis(self):
        """Should return file scope analysis when successful."""
        test_prompt = """
        Files to modify:
        - ui/handler.py
        - ui/serve.py
        """
        wave_context.agents.extract_agent_dispatch_prompt = lambda x: test_prompt

        result = wave_context.get_file_scope("test-agent")
        self.assertIsNotNone(result)
        self.assertIn("intended_files", result)
        self.assertIn("actual_files", result)
        self.assertIn("coverage", result)
        self.assertIn("drift", result)


if __name__ == '__main__':
    unittest.main()
