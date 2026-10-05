#!/usr/bin/env python3
"""
humanize_voice.py — Build and compare against personal voice profiles.

Extracts style features (sentence length, contractions, discourse markers, etc.)
from a person's writing and measures how far a draft diverges from their style.

INDEX: Voice profiler extracting style features from corpus text to measure draft divergence from personal writing patterns.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, stdev


def split_sentences(text: str) -> list[str]:
    """Split text into sentences. Regex on [.!?] followed by space + capital/quote."""
    # Simple regex: sentence ends with .!? followed by space and uppercase/quote
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'])', text)
    return [s.strip() for s in sentences if s.strip()]


def split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs (separated by blank lines)."""
    paragraphs = re.split(r'\n\s*\n', text.strip())
    return [p.strip() for p in paragraphs if p.strip()]


def tokenize(text: str) -> list[str]:
    """Tokenize text into words (alphabetic sequences)."""
    return re.findall(r'\b[a-z]+\b', text.lower())


def has_finite_verb(sentence: str) -> bool:
    """
    Approximate check for finite verb in a sentence.
    Returns False (likely a fragment) if:
    - Sentence has no common auxiliary/main verbs AND
    - Sentence has < 6 words
    """
    common_verbs = {
        'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did',
        'can', 'could', 'will', 'would', 'shall', 'should',
        'may', 'might', 'must', 'ought',
        'go', 'goes', 'went', 'gone', 'going',
        'make', 'makes', 'made', 'making',
        'get', 'gets', 'got', 'getting', 'gotten',
        'say', 'says', 'said', 'saying',
        'think', 'thinks', 'thought', 'thinking',
        'know', 'knows', 'knew', 'knowing', 'known',
        'like', 'likes', 'liked', 'liking',
        'want', 'wants', 'wanted', 'wanting',
        'use', 'uses', 'used', 'using',
        'take', 'takes', 'took', 'taking', 'taken',
        'see', 'sees', 'saw', 'seeing', 'seen',
        'come', 'comes', 'came', 'coming',
        'work', 'works', 'worked', 'working',
        'find', 'finds', 'found', 'finding',
        'tell', 'tells', 'told', 'telling',
        'ask', 'asks', 'asked', 'asking',
        'give', 'gives', 'gave', 'giving', 'given',
        'need', 'needs', 'needed', 'needing',
        'feel', 'feels', 'felt', 'feeling',
        'leave', 'leaves', 'left', 'leaving',
        'put', 'puts', 'putting',
        'mean', 'means', 'meant', 'meaning',
        'keep', 'keeps', 'kept', 'keeping',
        'let', 'lets', 'letting',
        'begin', 'begins', 'began', 'beginning', 'begun',
        'seem', 'seems', 'seemed', 'seeming',
        'help', 'helps', 'helped', 'helping',
        'talk', 'talks', 'talked', 'talking',
        'try', 'tries', 'tried', 'trying',
        'run', 'runs', 'ran', 'running',
        'allow', 'allows', 'allowed', 'allowing',
        'read', 'reads', 'read', 'reading',
        'result', 'results', 'resulted', 'resulting',
        'grow', 'grows', 'grew', 'growing', 'grown',
        'lose', 'loses', 'lost', 'losing',
        'fall', 'falls', 'fell', 'falling', 'fallen',
        'prove', 'proves', 'proved', 'proving', 'proven',
        'hold', 'holds', 'held', 'holding',
        'build', 'builds', 'built', 'building',
        'turn', 'turns', 'turned', 'turning',
    }
    words = tokenize(sentence)
    verb_found = any(w in common_verbs for w in words)
    if verb_found:
        return True
    # Fragment if no verb and sentence too short
    return len(words) >= 6


def extract_features(text: str) -> dict:
    """Extract all voice profile features from text."""
    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)
    tokens = tokenize(text)

    # Sentence length metrics
    sentence_lengths = [len(tokenize(s)) for s in sentences]
    if len(sentence_lengths) >= 2:
        sent_mean = mean(sentence_lengths)
        sent_stdev = stdev(sentence_lengths) if len(sentence_lengths) > 1 else 0
    else:
        sent_mean = sentence_lengths[0] if sentence_lengths else 0
        sent_stdev = 0

    # Percentiles for sentence length
    sorted_lens = sorted(sentence_lengths)
    n = len(sorted_lens)
    sent_p10 = sorted_lens[max(0, n // 10)] if n > 0 else 0
    sent_p90 = sorted_lens[min(n - 1, (9 * n) // 10)] if n > 0 else 0

    # Paragraph length (in sentences)
    para_lengths = [len(split_sentences(p)) for p in paragraphs]
    para_mean = mean(para_lengths) if para_lengths else 0

    # Fragment rate
    fragments = sum(1 for s in sentences if not has_finite_verb(s))
    fragment_rate = (fragments / len(sentences) * 100) if sentences else 0

    # Contraction rate (n't, 's, 're, 've, 'll, 'd, 'm per 100 words)
    contraction_pattern = r"n't|'s|'re|'ve|'ll|'d|'m"
    contractions = len(re.findall(contraction_pattern, text.lower(), re.IGNORECASE))
    contraction_rate = (contractions / len(tokens) * 100) if tokens else 0

    # First-person rate
    first_person = len(re.findall(r'\b(i|me|my|mine|we|us|our|ours)\b', text.lower()))
    first_person_rate = (first_person / len(tokens) * 100) if tokens else 0

    # Question rate
    questions = text.count('?')
    question_rate = (questions / len(sentences) * 100) if sentences else 0

    # Discourse marker rate
    discourse_markers = {
        'honestly', 'so', 'anyway', 'look', 'turns', 'which', 'thing',
        'basically', 'actually', 'really', 'like', 'you', 'know'
    }
    discourse_count = sum(tokens.count(m) for m in discourse_markers if m in tokens)
    discourse_rate = (discourse_count / len(tokens) * 100) if tokens else 0

    # Punctuation density (per 100 words)
    em_dash_count = text.count('—') + text.count('--')
    semicolon_count = text.count(';')
    colon_count = text.count(':')
    em_dash_density = (em_dash_count / len(tokens) * 100) if tokens else 0
    semicolon_density = (semicolon_count / len(tokens) * 100) if tokens else 0
    colon_density = (colon_count / len(tokens) * 100) if tokens else 0

    # Average word length
    avg_word_length = mean(len(w) for w in tokens) if tokens else 0

    # Type-token ratio on first 500 tokens
    first_500 = tokens[:500]
    ttr = len(set(first_500)) / len(first_500) if first_500 else 0

    # Top 30 content words (exclude common stopwords)
    stopwords = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'is', 'are', 'am', 'was', 'were', 'be', 'been', 'have', 'has',
        'had', 'do', 'does', 'did', 'can', 'could', 'will', 'would', 'should',
        'may', 'might', 'must', 'it', 'its', 'this', 'that', 'these', 'those',
        'i', 'you', 'he', 'she', 'we', 'they', 'me', 'him', 'her', 'us', 'them',
        'my', 'your', 'his', 'our', 'their', 'mine', 'yours', 'ours', 'theirs',
        'as', 'from', 'with', 'by', 'about', 'into', 'through', 'during',
        'before', 'after', 'above', 'below', 'up', 'down', 'out', 'off',
        'over', 'under', 'again', 'further', 'then', 'once', 'not', 'no',
        'nor', 'such', 'so', 'than', 'too', 'very', 'just', 'how', 'what',
        'which', 'who', 'when', 'where', 'why', 'all', 'each', 'every',
        'both', 'few', 'more', 'most', 'other', 'some', 'such', 'only'
    }
    content_tokens = [t for t in tokens if t not in stopwords and len(t) > 2]
    top_30_words = [w for w, _ in Counter(content_tokens).most_common(30)]

    # Top 20 sentence openers
    openers = [tokenize(s)[0] for s in sentences if tokenize(s)]
    top_20_openers = [w for w, _ in Counter(openers).most_common(20)]

    # Number density (digits per 100 words)
    digit_count = sum(1 for c in text if c.isdigit())
    number_density = (digit_count / len(tokens) * 100) if tokens else 0

    # Recurring phrases (3-grams appearing >= 2 times)
    trigrams = []
    for i in range(len(tokens) - 2):
        trigram = ' '.join(tokens[i:i+3])
        trigrams.append(trigram)
    trigram_counts = Counter(trigrams)
    recurring_phrases = [phrase for phrase, count in trigram_counts.items() if count >= 2]

    return {
        'sentence_length_mean': sent_mean,
        'sentence_length_stdev': sent_stdev,
        'sentence_length_p10': sent_p10,
        'sentence_length_p90': sent_p90,
        'paragraph_length_mean': para_mean,
        'fragment_rate': fragment_rate,
        'contraction_rate': contraction_rate,
        'first_person_rate': first_person_rate,
        'question_rate': question_rate,
        'discourse_marker_rate': discourse_rate,
        'em_dash_density': em_dash_density,
        'semicolon_density': semicolon_density,
        'colon_density': colon_density,
        'avg_word_length': avg_word_length,
        'type_token_ratio': ttr,
        'top_30_content_words': top_30_words,
        'top_20_sentence_openers': top_20_openers,
        'number_density': number_density,
        'recurring_phrases': recurring_phrases,
    }


def build_profile(corpus_files: list[str], name: str = None) -> dict:
    """Build a voice profile from multiple corpus files."""
    all_text = []
    for fpath in corpus_files:
        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
            all_text.append(f.read())

    combined_text = '\n\n'.join(all_text)
    features = extract_features(combined_text)

    profile = {
        'name': name or 'unnamed',
        'source_files': corpus_files,
        'features': features,
    }
    return profile


def compare_profile(profile: dict, draft_file: str) -> dict:
    """Compare a draft text against a profile."""
    with open(draft_file, 'r', encoding='utf-8', errors='replace') as f:
        draft_text = f.read()

    draft_features = extract_features(draft_text)
    profile_features = profile['features']

    # Calculate divergences
    divergences = []

    numeric_features = {
        'sentence_length_mean', 'sentence_length_stdev', 'sentence_length_p10',
        'sentence_length_p90', 'paragraph_length_mean', 'fragment_rate',
        'contraction_rate', 'first_person_rate', 'question_rate',
        'discourse_marker_rate', 'em_dash_density', 'semicolon_density',
        'colon_density', 'avg_word_length', 'type_token_ratio', 'number_density'
    }

    for feature in numeric_features:
        profile_val = profile_features[feature]
        draft_val = draft_features.get(feature, 0)

        # Use stdev if available, else use 1 as small threshold
        stdev_val = profile_features.get('sentence_length_stdev', 1)
        if feature == 'sentence_length_stdev':
            stdev_val = max(1, profile_val)  # Avoid division by zero
        elif feature not in {'sentence_length_stdev', 'type_token_ratio'}:
            stdev_val = max(0.1, abs(profile_val) * 0.2)  # Use 20% as variation threshold

        distance = abs(draft_val - profile_val) / max(stdev_val, 0.01)

        divergences.append({
            'feature': feature,
            'profile_value': profile_val,
            'draft_value': draft_val,
            'distance': distance,
        })

    # Sort by distance and get top 5
    divergences.sort(key=lambda x: x['distance'], reverse=True)
    top_divergences = divergences[:5]

    # Direction-aware suggestions: say "more" or "fewer" based on which side the draft is on.
    nouns = {
        'sentence_length_mean': ('words per sentence', 'split the long ones', 'let some sentences run longer'),
        'sentence_length_p10': ('words in the shortest sentences', 'add a few short ones', 'lengthen the shortest sentences'),
        'sentence_length_p90': ('words in the longest sentences', 'trim the longest sentences', 'allow a longer sentence now and then'),
        'fragment_rate': ('fragments', 'cut some fragments', 'let a fragment stand'),
        'contraction_rate': ('contractions', 'spell a few out', "use contractions (it's, don't)"),
        'first_person_rate': ('first-person pronouns', 'take yourself out of a few sentences', 'say I'),
        'question_rate': ('questions', 'cut a question', 'ask one'),
        'discourse_marker_rate': ('discourse markers (honestly, so, anyway)', 'drop a marker', 'add one where you would say it aloud'),
        'em_dash_density': ('em dashes', 'swap dashes for commas or full stops', 'a dash is fine here and there'),
        'semicolon_density': ('semicolons', 'swap semicolons for full stops', 'a semicolon is fine'),
        'colon_density': ('colons', 'cut a colon', 'a colon is fine'),
        'avg_word_length': ('letters per word', 'use plainer words', 'longer words are fine here'),
        'number_density': ('numbers', 'fewer figures', 'put a real number in'),
        'paragraph_length_mean': ('sentences per paragraph', 'break a paragraph', 'join two short paragraphs'),
        'type_token_ratio': ('distinct words', 'repeat a word rather than reach for a synonym', 'vary the wording'),
    }

    def suggest(d):
        noun, too_high, too_low = nouns.get(d['feature'], (d['feature'], 'bring it down', 'bring it up'))
        pv, dv = d['profile_value'], d['draft_value']
        if dv > pv:
            return f"Draft has more {noun} than you do ({dv:g} vs {pv:g}): {too_high}."
        return f"Draft has fewer {noun} than you do ({dv:g} vs {pv:g}): {too_low}."

    comparison = {
        'profile_name': profile['name'],
        'draft_file': draft_file,
        'all_divergences': divergences,
        'top_5_divergences': [
            {
                'feature': d['feature'],
                'profile_value': d['profile_value'],
                'draft_value': d['draft_value'],
                'distance': d['distance'],
                'suggestion': suggest(d)
            }
            for d in top_divergences
        ],
    }
    return comparison


def main():
    parser = argparse.ArgumentParser(
        description='Build and compare voice profiles.',
    )
    subparsers = parser.add_subparsers(dest='command', help='build or compare')

    # Build subcommand
    build_parser = subparsers.add_parser('build', help='Build a voice profile from corpus files.')
    build_parser.add_argument('--corpus', nargs='+', required=True, help='Corpus files to build profile from.')
    build_parser.add_argument('--out', required=True, help='Output profile JSON file.')
    build_parser.add_argument('--name', help='Profile name.')

    # Compare subcommand
    compare_parser = subparsers.add_parser('compare', help='Compare a draft against a profile.')
    compare_parser.add_argument('--profile', required=True, help='Profile JSON file.')
    compare_parser.add_argument('--text', required=True, help='Draft text file to compare.')
    compare_parser.add_argument('--json', action='store_true', help='Output as JSON.')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(2)

    try:
        if args.command == 'build':
            profile = build_profile(args.corpus, args.name)
            with open(args.out, 'w', encoding='utf-8') as f:
                json.dump(profile, f, indent=2)
            print(f'Profile written to {args.out}', file=sys.stderr)
            sys.exit(0)

        elif args.command == 'compare':
            with open(args.profile, 'r', encoding='utf-8') as f:
                profile = json.load(f)
            comparison = compare_profile(profile, args.text)

            if args.json:
                print(json.dumps(comparison, indent=2))
            else:
                # Human-readable output
                print(f'Profile: {comparison["profile_name"]}')
                print(f'Draft: {comparison["draft_file"]}')
                print()
                print('Top 5 divergences:')
                for d in comparison['top_5_divergences']:
                    print(f'  {d["feature"]}:')
                    print(f'    Profile: {d["profile_value"]:.2f}')
                    print(f'    Draft: {d["draft_value"]:.2f}')
                    print(f'    Distance: {d["distance"]:.2f}')
                    print(f'    Suggestion: {d["suggestion"]}')
                    print()
            sys.exit(0)

    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
