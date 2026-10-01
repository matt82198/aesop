#!/usr/bin/env python3
"""
test_humanize_lint.py — behavioral tests for humanize_lint.py

Tests exercise actual rule detection by running the lint functions
and asserting on their output (findings count, scores, etc).
Never inspect source text to "prove" a rule works — drive the code
and verify observable behavior.
"""

import sys
import unittest
from pathlib import Path

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))

from humanize_lint import (
    lint_text, score_text, split_sentences, split_paragraphs,
    get_content_words, find_ai_lexicon, find_tricolon, find_contrast_frame,
    find_dash_density, find_low_burstiness, find_bow_tie_closer,
    find_echo_source, find_opener_repeat, find_hedge_stack, Finding
)


class TestHumanizeLint(unittest.TestCase):
    """Test suite for humanize_lint module."""

    def test_split_sentences(self):
        """Test sentence splitting handles basic cases."""
        text = "Hello world. This is a test. Mr. Smith agrees."
        sentences = split_sentences(text)
        self.assertGreaterEqual(len(sentences), 3, f"Expected 3+ sentences, got {len(sentences)}")
        self.assertTrue(sentences[0].startswith("Hello"))
        self.assertTrue(sentences[-1].startswith("Mr."))

    def test_split_paragraphs(self):
        """Test paragraph splitting on blank lines."""
        text = "First paragraph.\n\nSecond paragraph.\n\nThird."
        paragraphs = split_paragraphs(text)
        self.assertEqual(len(paragraphs), 3)
        self.assertTrue(paragraphs[0].startswith("First"))
        self.assertTrue(paragraphs[2].startswith("Third"))

    def test_get_content_words(self):
        """Test content word extraction (alphabetic, length >= 4, not stopwords)."""
        text = "The quick brown fox jumps over the lazy dog"
        words = get_content_words(text)
        self.assertIn('quick', words)
        self.assertIn('brown', words)
        self.assertNotIn('the', words)  # stopword
        self.assertNotIn('fox', words)  # only 3 chars
        self.assertGreaterEqual(len(words), 2)

    def test_ai_lexicon_detection(self):
        """Test detection of AI lexicon words."""
        text = "This solution will leverage and unlock seamless synergy."
        findings = find_ai_lexicon(text)
        self.assertGreaterEqual(len(findings), 3, f"Expected 3+ findings, got {len(findings)}")
        # Verify each finding has required fields
        for f in findings:
            self.assertEqual(f.rule, 'ai-lexicon')
            self.assertEqual(f.severity, 1)
            self.assertLess(f.start, f.end)
            self.assertGreater(len(f.excerpt), 0)

    def test_ai_lexicon_phrases(self):
        """Test detection of AI phrase patterns."""
        text = "It's worth noting that in today's market we must navigate and leverage solutions."
        findings = find_ai_lexicon(text)
        # Should find "it's worth", "in today's", "navigate", and "leverage"
        self.assertGreaterEqual(len(findings), 3, f"Expected 3+ phrase findings, got {len(findings)}")

    def test_tricolon_detection(self):
        """Test detection of three-part parallel structures."""
        text = "We can look at it, say plainly what works, and prove the recommendation with data."
        findings = find_tricolon(text)
        self.assertGreater(len(findings), 0, "Expected to find tricolon structure")

    def test_contrast_frame_detection(self):
        """Test detection of 'not X but Y' and 'rather than' patterns."""
        text = "Not a rebuild but a reimagined solution, rather than a replatform."
        findings = find_contrast_frame(text)
        self.assertGreaterEqual(len(findings), 1, "Expected to find contrast frame")
        for f in findings:
            self.assertEqual(f.rule, 'contrast-frame')

    def test_dash_density_detection(self):
        """Test detection of high em-dash density."""
        text = "This is one thought — actually two thoughts — or maybe three — we'll see."
        findings = find_dash_density(text)
        self.assertGreater(len(findings), 0, "Expected to find high dash density")

    def test_low_burstiness_detection(self):
        """Test detection of uniform sentence length (low variance)."""
        # 3 sentences with similar length (5 words each) = low burstiness
        text = "The store runs well. There is no backend. I built this system."
        findings = find_low_burstiness(text)
        self.assertGreater(len(findings), 0, "Expected to find low burstiness in uniform sentences")

    def test_bow_tie_closer_detection(self):
        """Test detection of abstract paragraph-closing sentences."""
        text = """The team built the feature faster than expected. We added monitoring and feedback loops.

This embodied both strategy and design. Both were half strategy and half framework, reflecting our approach to modernization."""
        findings = find_bow_tie_closer(text)
        self.assertGreater(len(findings), 0, "Expected to find bow-tie closer sentence")

    def test_echo_source_detection(self):
        """Test paraphrase detection when reference is given."""
        source = "We look at technology landscape and understand what works and what does not and chart path forward."
        text = "The posting asks for someone who can look at technology landscape and understand what works and what does not and chart path forward to excellence."
        findings = find_echo_source(text, source)
        self.assertGreater(len(findings), 0, f"Expected to find echo-source match, got {len(findings)}")

    def test_echo_source_no_reference(self):
        """Test that echo-source is skipped when no reference given."""
        text = "Some text about a solution approach."
        findings = find_echo_source(text, None)
        self.assertEqual(len(findings), 0, "Should not detect echo-source without reference")

    def test_opener_repeat_sentences(self):
        """Test detection of repeated sentence openers."""
        text = "The system works well. The system also scales. Another sentence here."
        findings = find_opener_repeat(text)
        self.assertGreater(len(findings), 0, "Expected to find repeated sentence openers")

    def test_opener_repeat_paragraphs(self):
        """Test detection of repeated paragraph openers."""
        text = """Each system started small.

Each team built independently.

Each approach worked for its context."""
        findings = find_opener_repeat(text)
        self.assertGreater(len(findings), 0, "Expected to find three paragraphs starting with 'Each'")

    def test_hedge_stack_detection(self):
        """Test detection of multiple hedging phrases in one paragraph."""
        text = """
Ultimately, this approach works. Arguably, it's worth noting that overall, in many ways,
this design represents our best thinking on the trade-offs involved.
"""
        findings = find_hedge_stack(text)
        self.assertGreater(len(findings), 0, "Expected to find hedge stacking")

    def test_scoring_no_findings(self):
        """Test score is 100 with no findings."""
        score_info = score_text([])
        self.assertEqual(score_info['score'], 100)
        self.assertEqual(score_info['verdict'], 'human-like')

    def test_scoring_with_severity_1_findings(self):
        """Test score calculation with severity-1 findings."""
        findings = [
            Finding('test', 1, 0, 5, 'test', 'note'),
            Finding('test', 1, 10, 15, 'test', 'note'),
        ]
        # 100 - (1*4 + 1*4) = 100 - 8 = 92
        score_info = score_text(findings)
        self.assertEqual(score_info['score'], 92, f"Expected 92, got {score_info['score']}")
        self.assertEqual(score_info['verdict'], 'human-like')

    def test_scoring_with_severity_2_findings(self):
        """Test score with severity-2 findings."""
        findings = [
            Finding('test', 2, 0, 5, 'test', 'note'),
            Finding('test', 2, 10, 15, 'test', 'note'),
        ]
        # 100 - (2*4 + 2*4) = 100 - 16 = 84
        score_info = score_text(findings)
        self.assertEqual(score_info['score'], 84, f"Expected 84, got {score_info['score']}")

    def test_scoring_verdict_mixed(self):
        """Test verdict is 'mixed' for score in 60-79 range."""
        # Create enough findings to get into 60-79 range
        findings = [
            Finding('test', 1, 0, 5, 'test', 'note'),
            Finding('test', 1, 10, 15, 'test', 'note'),
            Finding('test', 1, 20, 25, 'test', 'note'),
            Finding('test', 1, 30, 35, 'test', 'note'),
            Finding('test', 1, 40, 45, 'test', 'note'),
            Finding('test', 1, 50, 55, 'test', 'note'),
            Finding('test', 1, 60, 65, 'test', 'note'),
        ]
        # 100 - (7 * 4) = 72
        score_info = score_text(findings)
        self.assertIn(score_info['score'], range(60, 80), f"Score {score_info['score']} not in mixed range")
        self.assertEqual(score_info['verdict'], 'mixed')

    def test_scoring_verdict_machine_like(self):
        """Test verdict is 'machine-like' for score < 60."""
        findings = [
            Finding('test', 2, 0, 5, 'test', 'note'),
            Finding('test', 2, 10, 15, 'test', 'note'),
            Finding('test', 2, 20, 25, 'test', 'note'),
            Finding('test', 2, 30, 35, 'test', 'note'),
            Finding('test', 2, 40, 45, 'test', 'note'),
            Finding('test', 2, 50, 55, 'test', 'note'),
        ]
        # 100 - (6 * 8) = 52
        score_info = score_text(findings)
        self.assertLess(score_info['score'], 60, f"Score {score_info['score']} not < 60")
        self.assertEqual(score_info['verdict'], 'machine-like')

    def test_lint_text_integration(self):
        """Test full lint_text function with mixed patterns."""
        text = """
The solution will leverage seamless synergy.

We can look at it, say plainly it works, and prove the recommendation with data.

Both were half design and half convincing executives that the trade-offs mattered.
"""
        findings = lint_text(text)
        score_info = score_text(findings)

        # Should find multiple issues
        self.assertGreater(len(findings), 0, "Expected to find some patterns")
        self.assertLess(score_info['score'], 100, "Score should be penalized")
        # Check we have diverse rule types
        rules_found = set(f.rule for f in findings)
        self.assertGreaterEqual(len(rules_found), 2, f"Expected 2+ different rules, got {rules_found}")

    def test_calibration_v1_vs_v2(self):
        """
        Calibration test: v1 (machine-written) should score lower than v2 (human-written).
        Uses SHORT synthetic versions that reproduce the known patterns.
        """
        # v1: Machine-like version with multiple antipatterns
        v1_text = """
The solution can look at existing systems, say plainly what works and what does not, and prove
the recommendation with a prototype before commitments are made.

Both were half design and half convincing executives that the trade-offs were worth the investment.

We will leverage seamless solutions and unlock transformative capabilities. This will navigate
the complex landscape and foster digital transformation.
"""

        # v2: Human-like version addressing same content
        v2_text = """
I read the posting twice because the second paragraph describes my week. Whiteboard sessions
in the morning, deep design in the afternoon, prototype before anyone commits.

When I took over the technology side, every pitch started with a rebuild. So I went the other way:
I treated page visits as events, built the product feed and listings on Google's infrastructure.
I had to sell it with numbers, not diagrams.

Two designs outlived me there. One started as our team's logic and became company-wide because
I built it so others could borrow it. The other moved logic out of code and into data. Half design,
half convincing executives that the trade-offs were worth it, which I came to like more than I expected.
"""

        findings_v1 = lint_text(v1_text)
        findings_v2 = lint_text(v2_text)

        score_v1 = score_text(findings_v1)
        score_v2 = score_text(findings_v2)

        # v2 must score higher (lower penalty) than v1
        self.assertGreater(score_v2['score'], score_v1['score'],
            f"Calibration failed: v1 score {score_v1['score']} should be lower than v2 {score_v2['score']}")


if __name__ == '__main__':
    unittest.main()
