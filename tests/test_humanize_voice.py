#!/usr/bin/env python3
"""
Test suite for humanize_voice.py.

Tests behavioural aspects: profile building, feature extraction,
profile comparison, and divergence ranking.
"""

import tempfile
import unittest
from pathlib import Path
import sys

# Add tools to path for import
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))

from humanize_voice import (
    split_sentences,
    split_paragraphs,
    tokenize,
    has_finite_verb,
    extract_features,
    build_profile,
    compare_profile,
)


class TestHumanizeVoice(unittest.TestCase):
    """Test suite for humanize_voice functionality."""

    def test_split_sentences(self):
        """Test sentence splitting."""
        text = "Hello world. This is a test! What about this? It's working."
        sentences = split_sentences(text)
        self.assertEqual(len(sentences), 4)
        self.assertIn("Hello world", sentences[0])
        self.assertIn("This is a test", sentences[1])

    def test_split_paragraphs(self):
        """Test paragraph splitting."""
        text = "First paragraph.\n\nSecond paragraph.\n\nThird."
        paragraphs = split_paragraphs(text)
        self.assertEqual(len(paragraphs), 3)
        self.assertIn("First paragraph", paragraphs[0])

    def test_tokenize(self):
        """Test word tokenization."""
        text = "Hello, world! How's it going?"
        tokens = tokenize(text)
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)
        self.assertIn("how", tokens)
        self.assertIn("s", tokens)  # contraction splits

    def test_has_finite_verb(self):
        """Test finite verb detection."""
        # Has finite verb
        self.assertTrue(has_finite_verb("The cat is sleeping."))
        self.assertTrue(has_finite_verb("I went to the store."))
        self.assertTrue(has_finite_verb("She doesn't like it."))

        # Likely fragments
        self.assertFalse(has_finite_verb("The store. Small."))
        self.assertFalse(has_finite_verb("Beautiful."))

    def test_extract_features_terse_style(self):
        """Test feature extraction on terse, informal style (contractions, fragments, first-person)."""
        terse_text = """I didn't like it. The meeting was pointless.

    So I left. What's the point? You're not listening. We'll figure it out.
    I'm done. Look, this isn't working. You can't just ignore me.

    It's frustrating. I've told you this before. Where're we going with this?
    The plan's broken. We need to start over."""

        features = extract_features(terse_text)

        # Should have high contraction rate
        self.assertGreater(features['contraction_rate'], 15)
        # Should have high first-person rate
        self.assertGreater(features['first_person_rate'], 8)
        # Should have some fragments
        self.assertGreater(features['fragment_rate'], 5)
        # Should have shorter sentences on average
        self.assertLess(features['sentence_length_mean'], 12)

    def test_extract_features_formal_style(self):
        """Test feature extraction on formal, complex style (no contractions, long sentences)."""
        formal_text = """The implementation requires careful consideration of all factors.

    One must acknowledge the complexity of the situation and the challenges that arise
    from the interdependence of multiple systems. This approach has been validated through
    extensive research and empirical observation.

    The proposed methodology offers significant advantages. Moreover, the integration of
    advanced technologies provides unprecedented opportunities for optimization and enhancement
    of overall system performance. These considerations demonstrate the importance of a
    comprehensive and well-structured approach to the problem at hand."""

        features = extract_features(formal_text)

        # Should have low contraction rate
        self.assertLess(features['contraction_rate'], 5)
        # Should have low first-person rate
        self.assertLess(features['first_person_rate'], 3)
        # Should have few fragments
        self.assertLess(features['fragment_rate'], 10)
        # Should have longer sentences
        self.assertGreater(features['sentence_length_mean'], 12)

    def test_extract_features_completeness(self):
        """Test that all required features are extracted."""
        text = "This is a test. It works well. We are learning. I love this!"
        features = extract_features(text)

        required = {
            'sentence_length_mean', 'sentence_length_stdev', 'sentence_length_p10',
            'sentence_length_p90', 'paragraph_length_mean', 'fragment_rate',
            'contraction_rate', 'first_person_rate', 'question_rate',
            'discourse_marker_rate', 'em_dash_density', 'semicolon_density',
            'colon_density', 'avg_word_length', 'type_token_ratio',
            'top_30_content_words', 'top_20_sentence_openers', 'number_density',
            'recurring_phrases'
        }

        self.assertTrue(all(k in features for k in required))

    def test_build_profile_from_corpus(self):
        """Test building a profile from multiple corpus files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create two corpus files with distinct styles
            terse_file = tmppath / 'terse.txt'
            terse_file.write_text("I didn't like it. It's broken. We're done. Let's go.\n" * 5)

            formal_file = tmppath / 'formal.txt'
            formal_file.write_text(
                "The implementation requires careful consideration. "
                "One must acknowledge the complexity of the situation. " * 5
            )

            profile = build_profile([str(terse_file), str(formal_file)], name='test_profile')

            self.assertEqual(profile['name'], 'test_profile')
            self.assertIn('features', profile)
            self.assertIn('contraction_rate', profile['features'])

    def test_compare_profile_shows_divergences(self):
        """Test that compare identifies and ranks divergences correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Build a formal profile
            formal_file = tmppath / 'formal.txt'
            formal_file.write_text(
                "The implementation requires careful consideration. "
                "One must acknowledge the complexity of the situation. " * 10
            )

            profile = build_profile([str(formal_file)], name='formal')

            # Compare against a very different (terse) draft
            terse_draft = tmppath / 'terse_draft.txt'
            terse_draft.write_text("I didn't like it. It's broken. Look, we're done. " * 10)

            comparison = compare_profile(profile, str(terse_draft))

            # Should have divergences
            self.assertGreater(len(comparison['top_5_divergences']), 0)
            self.assertIn('contraction_rate', {d['feature'] for d in comparison['top_5_divergences']})

    def test_compare_returns_sorted_divergences(self):
        """Test that divergences are sorted by distance (largest first)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            corpus_file = tmppath / 'corpus.txt'
            corpus_file.write_text("Sample text. More text. Testing testing. Data data data. " * 5)

            profile = build_profile([str(corpus_file)])

            draft_file = tmppath / 'draft.txt'
            draft_file.write_text("I think it works. Don't worry. Let's go now. It's fine. We're good. " * 5)

            comparison = compare_profile(profile, str(draft_file))

            # Divergences should be sorted by distance descending
            distances = [d['distance'] for d in comparison['top_5_divergences']]
            self.assertEqual(distances, sorted(distances, reverse=True))

    def test_recurring_phrases(self):
        """Test that recurring phrases (3-grams) are extracted."""
        text = "the quick brown fox jumps. the quick brown. and the quick brown fox"
        features = extract_features(text)

        # "the quick brown" appears 2+ times
        self.assertIn('the quick brown', features['recurring_phrases'])

    def test_top_words_and_openers(self):
        """Test extraction of top content words and sentence openers."""
        text = ("The store runs smoothly. The process works well. "
                "The result is clear. We succeeded. We learned. We grew.")

        features = extract_features(text)

        # Top openers should include 'the' and 'we'
        self.assertIn('the', features['top_20_sentence_openers'])
        self.assertIn('we', features['top_20_sentence_openers'])

        # Top words should include content words like 'store', 'process', etc.
        self.assertGreater(len(features['top_30_content_words']), 0)

    def test_suggestions_name_the_direction(self):
        """A draft with MORE semicolons than the profile must be told to use fewer, not more."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            terse = "I built it. It works. No drama. We shipped it on a Tuesday. It still runs today."
            semi = "I built it; it works; there was no drama; we shipped it on a Tuesday; it still runs today."
            c = tmppath / "c.txt"
            c.write_text(terse * 4, encoding="utf-8")
            d = tmppath / "d.txt"
            d.write_text(semi * 4, encoding="utf-8")
            prof = build_profile([str(c)], "t")
            comp = compare_profile(prof, str(d))
            semis = [x for x in comp["top_5_divergences"] if x["feature"] == "semicolon_density"]
            self.assertTrue(semis, comp["top_5_divergences"])
            self.assertIn("more semicolons", semis[0]["suggestion"])
            self.assertNotIn("fewer", semis[0]["suggestion"].split(":")[0])


if __name__ == '__main__':
    unittest.main()
