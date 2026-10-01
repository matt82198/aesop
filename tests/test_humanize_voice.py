#!/usr/bin/env python3
"""
Test suite for humanize_voice.py.

Tests behavioural aspects: profile building, feature extraction,
profile comparison, and divergence ranking.
"""

import json
import tempfile
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


def test_split_sentences():
    """Test sentence splitting."""
    text = "Hello world. This is a test! What about this? It's working."
    sentences = split_sentences(text)
    assert len(sentences) == 4
    assert "Hello world" in sentences[0]
    assert "This is a test" in sentences[1]


def test_split_paragraphs():
    """Test paragraph splitting."""
    text = "First paragraph.\n\nSecond paragraph.\n\nThird."
    paragraphs = split_paragraphs(text)
    assert len(paragraphs) == 3
    assert "First paragraph" in paragraphs[0]


def test_tokenize():
    """Test word tokenization."""
    text = "Hello, world! How's it going?"
    tokens = tokenize(text)
    assert "hello" in tokens
    assert "world" in tokens
    assert "how" in tokens
    assert "s" in tokens  # contraction splits


def test_has_finite_verb():
    """Test finite verb detection."""
    # Has finite verb
    assert has_finite_verb("The cat is sleeping.")
    assert has_finite_verb("I went to the store.")
    assert has_finite_verb("She doesn't like it.")

    # Likely fragments
    assert not has_finite_verb("The store. Small.")
    assert not has_finite_verb("Beautiful.")


def test_extract_features_terse_style():
    """Test feature extraction on terse, informal style (contractions, fragments, first-person)."""
    terse_text = """I didn't like it. The meeting was pointless.

    So I left. What's the point? You're not listening. We'll figure it out.
    I'm done. Look, this isn't working. You can't just ignore me.

    It's frustrating. I've told you this before. Where're we going with this?
    The plan's broken. We need to start over."""

    features = extract_features(terse_text)

    # Should have high contraction rate
    assert features['contraction_rate'] > 15
    # Should have high first-person rate
    assert features['first_person_rate'] > 8
    # Should have some fragments
    assert features['fragment_rate'] > 5
    # Should have shorter sentences on average
    assert features['sentence_length_mean'] < 12


def test_extract_features_formal_style():
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
    assert features['contraction_rate'] < 5
    # Should have low first-person rate
    assert features['first_person_rate'] < 3
    # Should have few fragments
    assert features['fragment_rate'] < 10
    # Should have longer sentences
    assert features['sentence_length_mean'] > 12


def test_extract_features_completeness():
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

    assert all(k in features for k in required)


def test_build_profile_from_corpus():
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

        assert profile['name'] == 'test_profile'
        assert 'features' in profile
        assert 'contraction_rate' in profile['features']


def test_compare_profile_shows_divergences():
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
        assert len(comparison['top_5_divergences']) > 0
        assert 'contraction_rate' in {d['feature'] for d in comparison['top_5_divergences']}


def test_compare_returns_sorted_divergences():
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
        assert distances == sorted(distances, reverse=True)


def test_recurring_phrases():
    """Test that recurring phrases (3-grams) are extracted."""
    text = "the quick brown fox jumps. the quick brown. and the quick brown fox"
    features = extract_features(text)

    # "the quick brown" appears 2+ times
    assert 'the quick brown' in features['recurring_phrases']


def test_top_words_and_openers():
    """Test extraction of top content words and sentence openers."""
    text = ("The store runs smoothly. The process works well. "
            "The result is clear. We succeeded. We learned. We grew.")

    features = extract_features(text)

    # Top openers should include 'the' and 'we'
    assert 'the' in features['top_20_sentence_openers']
    assert 'we' in features['top_20_sentence_openers']

    # Top words should include content words like 'store', 'process', etc.
    assert len(features['top_30_content_words']) > 0


def run_all_tests():
    """Run all tests and return a suite declaration."""
    tests = [
        test_split_sentences,
        test_split_paragraphs,
        test_tokenize,
        test_has_finite_verb,
        test_extract_features_terse_style,
        test_extract_features_formal_style,
        test_extract_features_completeness,
        test_build_profile_from_corpus,
        test_compare_profile_shows_divergences,
        test_compare_returns_sorted_divergences,
        test_recurring_phrases,
        test_top_words_and_openers,
    ]

    failed = []
    for test in tests:
        try:
            test()
            print(f'✓ {test.__name__}')
        except AssertionError as e:
            print(f'✗ {test.__name__}: {e}')
            failed.append(test.__name__)
        except Exception as e:
            print(f'✗ {test.__name__}: {type(e).__name__}: {e}')
            failed.append(test.__name__)

    print()
    print(f'{len(tests) - len(failed)}/{len(tests)} tests passed')

    if failed:
        print(f'Failed: {", ".join(failed)}')
        return None, None

    return f'{len(tests)} passed', tests  # Return suite declaration


if __name__ == '__main__':
    suite_status, suite_decl = run_all_tests()
    sys.exit(0 if suite_decl else 1)


def test_suggestions_name_the_direction(tmp_path):
    """A draft with MORE semicolons than the profile must be told to use fewer, not more."""
    terse = "I built it. It works. No drama. We shipped it on a Tuesday. It still runs today."
    semi = "I built it; it works; there was no drama; we shipped it on a Tuesday; it still runs today."
    c = tmp_path / "c.txt"; c.write_text(terse * 4, encoding="utf-8")
    d = tmp_path / "d.txt"; d.write_text(semi * 4, encoding="utf-8")
    prof = build_profile([str(c)], "t")
    comp = compare_profile(prof, str(d))
    semis = [x for x in comp["top_5_divergences"] if x["feature"] == "semicolon_density"]
    assert semis, comp["top_5_divergences"]
    assert "more semicolons" in semis[0]["suggestion"] and "fewer" not in semis[0]["suggestion"].split(":")[0]
