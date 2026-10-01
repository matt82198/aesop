#!/usr/bin/env python3
"""
humanize_lint.py — flags machine-writing patterns in text deterministically.

Detects patterns like echoing source text, rigid tricolons, low sentence-length
variance, abstract paragraph closers, and common AI lexicon. Returns findings
with character offsets and a human-likeness score (0-100).

INDEX: Flags machine-writing patterns (ai-lexicon, tricolon, low-burstiness, bow-tie-closer, contrast-frame, dash-density, echo-source, opener-repeat, hedge-stack); scores 0-100; importable split_sentences() and lint_text().

Usage:
  python tools/humanize_lint.py --text FILE [--reference FILE] [--json] [--min-score N]

Rules live in RULES dict (id, category, severity 1-3, description, finder function).
Findings: {rule, severity, start, end, excerpt, note}
Score: 100 - sum(severity * 4), floored at 0. Verdict: >=80 human-like, 60-79 mixed, <60 machine-like.

Exit codes: 0=OK or score >= --min-score; 1=findings; 2=error.
"""

import re
import sys
import json
import argparse
from pathlib import Path
from typing import Callable, Optional, Any
from dataclasses import dataclass, asdict

try:
    from tools.cli import CLIBuilder, OutputFormatter
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from cli import CLIBuilder, OutputFormatter


STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
    'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'could', 'should', 'may', 'might', 'must', 'can', 'this', 'that',
    'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they',
    'what', 'which', 'who', 'when', 'where', 'why', 'how', 'as', 'if',
    'because', 'while', 'so', 'not', 'no', 'yes', 'more', 'most', 'some',
    'all', 'each', 'every', 'both', 'either', 'neither', 'me', 'him', 'her',
    'us', 'them', 'my', 'your', 'his', 'their', 'its', 'our', 'or', 'than',
}

AI_LEXICON = {
    'delve', 'leverage', 'robust', 'seamless', 'tapestry', 'testament',
    'landscape', 'navigate', 'foster', 'unlock', 'synergy', 'holistic',
    'paradigm', 'innovative', 'transformative', 'cutting-edge', 'best-in-class',
    'mission-critical', 'enterprise-grade', 'strategic', 'empower', 'drive',
    'catalyst', 'ecosystem', 'scalable', 'dynamic', 'agile', 'circle back',
}

AI_PHRASES = [
    "it's worth", "in today's", "it goes without saying", "needless to say",
    'on a final note', 'in conclusion', 'to summarize', 'all in all',
]


@dataclass
class Finding:
    rule: str
    severity: int
    start: int
    end: int
    excerpt: str
    note: str


def split_paragraphs(text: str) -> list[str]:
    """Split text by blank lines into paragraphs."""
    return [p.strip() for p in text.split('\n\n') if p.strip()]


def split_sentences(text: str) -> list[str]:
    """
    Split text into sentences. Keeps abbreviations simple.
    Looks for .!? followed by space and capital letter or quote.
    """
    # Replace common abbreviations to protect them
    protected = text.replace('Mr.', 'MR').replace('Mrs.', 'MRS').replace('Dr.', 'DR')
    protected = protected.replace('Prof.', 'PROF').replace('vs.', 'VS')

    # Split on [.!?] followed by space and capital or quote
    pattern = r'([.!?])\s+(?=[A-Z"])'
    sentences = re.split(pattern, protected)

    # Reconstruct: every odd index is the punctuation, pair it with the next chunk
    result = []
    i = 0
    while i < len(sentences):
        if i + 1 < len(sentences):
            sent = sentences[i] + sentences[i + 1]
            i += 2
        else:
            sent = sentences[i]
            i += 1
        # Restore abbreviations
        sent = sent.replace('MR', 'Mr.').replace('MRS', 'Mrs.').replace('DR', 'Dr.')
        sent = sent.replace('PROF', 'Prof.').replace('VS', 'vs.')
        result.append(sent.strip())

    return [s for s in result if s]


def get_content_words(text: str) -> list[str]:
    """
    Extract content words (alphabetic, length >= 4, not in stopwords).
    """
    tokens = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    return [t for t in tokens if len(t) >= 4 and t not in STOPWORDS]


def find_ai_lexicon(text: str, _ref: Optional[str] = None) -> list[Finding]:
    """Detect common AI-sounding words and phrases (case-insensitive)."""
    findings = []
    lower_text = text.lower()

    # Word boundaries: \b for whole-word matches
    for word in AI_LEXICON:
        for match in re.finditer(rf'\b{re.escape(word)}\b', lower_text):
            start, end = match.span()
            findings.append(Finding(
                rule='ai-lexicon',
                severity=1,
                start=start,
                end=end,
                excerpt=text[start:end],
                note=f"'{word}' is a common AI-lexicon word"
            ))

    # Phrases (also whole-word on the main parts)
    for phrase in AI_PHRASES:
        # Remove spaces for matching to handle spacing variations
        phrase_no_spaces = phrase.replace(' ', '')
        pattern = phrase.replace(' ', r'\s+')
        for match in re.finditer(pattern, lower_text, re.IGNORECASE):
            start, end = match.span()
            findings.append(Finding(
                rule='ai-lexicon',
                severity=1,
                start=start,
                end=end,
                excerpt=text[start:end],
                note=f"'{phrase}' is a common AI phrase"
            ))

    return findings


def find_tricolon(text: str, _ref: Optional[str] = None) -> list[Finding]:
    """
    Detect tricolon: three parallel items joined by commas + and/or.
    Pattern: item, item, and/or item (with some flexibility in punctuation).
    """
    findings = []

    # Look for patterns like "X, Y, and Z" or "X, Y, or Z"
    # Simplified: look for comma-separated lists with 3+ items ending in "and" or "or"
    pattern = r'([^,;.!?]+),\s*([^,;.!?]+),\s*(?:and|or)\s*([^,;.!?]+)'

    for match in re.finditer(pattern, text):
        item1, item2, item3 = match.groups()
        # Check if items are roughly parallel (similar length and structure)
        lengths = [len(i.strip().split()) for i in [item1, item2, item3]]
        if max(lengths) / (min(lengths) + 1) < 2:  # Items within 2x length of each other
            findings.append(Finding(
                rule='tricolon',
                severity=1,
                start=match.start(),
                end=match.end(),
                excerpt=match.group(),
                note="Rigid three-part parallel structure"
            ))

    return findings


def find_contrast_frame(text: str, _ref: Optional[str] = None) -> list[Finding]:
    """
    Detect contrast frames: "not X but Y" or "X rather than Y" patterns.
    """
    findings = []

    # Match "not X but Y" and "X rather than Y"
    patterns = [
        r'not\s+([^,;.!?]+?)\s+but\s+([^,;.!?]+?)(?=[,;.!?\s])',
        r'([^,;.!?]+?)\s+rather\s+than\s+([^,;.!?]+?)(?=[,;.!?\s])',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            findings.append(Finding(
                rule='contrast-frame',
                severity=1,
                start=match.start(),
                end=match.end(),
                excerpt=match.group(),
                note="Contrast frame (not X but Y / rather than)"
            ))

    return findings


def find_dash_density(text: str, _ref: Optional[str] = None) -> list[Finding]:
    """
    Detect high em-dash density (threshold: >1.5 dashes per 100 words for paragraph).
    """
    findings = []

    for para in split_paragraphs(text):
        words = para.split()
        dash_count = para.count('—') + para.count('--')
        if len(words) > 0:
            density = (dash_count / len(words)) * 100
            if density > 1.5:
                # Find the dashes
                for match in re.finditer(r'—|--', para):
                    # Try to extract surrounding context
                    start_context = max(0, match.start() - 20)
                    end_context = min(len(para), match.end() + 20)
                    # Calculate offset within original text
                    offset = text.find(para)
                    findings.append(Finding(
                        rule='dash-density',
                        severity=1,
                        start=offset + match.start(),
                        end=offset + match.end(),
                        excerpt=para[start_context:end_context].replace('\n', ' '),
                        note=f"High em-dash density in paragraph ({density:.1f} per 100 words)"
                    ))
                break  # Only report once per paragraph

    return findings


def find_low_burstiness(text: str, _ref: Optional[str] = None) -> list[Finding]:
    """
    Detect low sentence-length variance (std-dev / mean < 0.35).
    Indicates mechanical, uniform sentence structure.
    """
    findings = []

    for para in split_paragraphs(text):
        sentences = split_sentences(para)
        if len(sentences) >= 3:
            lengths = [len(s.split()) for s in sentences]
            mean = sum(lengths) / len(lengths)
            if mean > 0:
                variance = sum((x - mean) ** 2 for x in lengths) / len(lengths)
                std_dev = variance ** 0.5
                burstiness = std_dev / mean
                if burstiness < 0.35:
                    offset = text.find(para)
                    findings.append(Finding(
                        rule='low-burstiness',
                        severity=2,
                        start=offset,
                        end=offset + len(para),
                        excerpt=para[:100].replace('\n', ' '),
                        note=f"Low sentence-length variance ({burstiness:.2f}); sentences are too uniform"
                    ))

    return findings


def find_bow_tie_closer(text: str, _ref: Optional[str] = None) -> list[Finding]:
    """
    Detect paragraph-closing sentences that summarize in abstract nouns
    without introducing new concrete details.
    Pattern: final sentence contains abstract nouns (design, strategy, impact, etc)
    but no concrete nouns/numbers/proper nouns.
    """
    findings = []
    abstract_nouns = {'design', 'strategy', 'value', 'impact', 'trade-off', 'tradeoff',
                     'approach', 'solution', 'architecture', 'framework', 'principle',
                     'concept', 'methodology', 'paradigm', 'transition', 'evolution'}

    for para in split_paragraphs(text):
        sentences = split_sentences(para)
        if len(sentences) >= 2:
            last_sent = sentences[-1]
            last_words = set(re.findall(r'\b\w+\b', last_sent.lower()))

            # Check if has abstract nouns
            has_abstract = bool(last_words & abstract_nouns)

            # Check for concrete tokens (numbers, capitalized words that might be entities)
            has_concrete = bool(re.search(r'\d+|[A-Z][a-z]+\s+[A-Z]', last_sent))

            if has_abstract and not has_concrete:
                offset = text.find(para)
                sent_offset = offset + para.find(last_sent)
                findings.append(Finding(
                    rule='bow-tie-closer',
                    severity=1,
                    start=sent_offset,
                    end=sent_offset + len(last_sent),
                    excerpt=last_sent,
                    note="Paragraph-closing sentence summarizes abstractly without concrete details"
                ))

    return findings


def find_echo_source(text: str, ref: Optional[str] = None) -> list[Finding]:
    """
    Detect when sentences echo/paraphrase source text (>=60% content-word overlap).
    Only active when --reference is provided.
    """
    findings = []
    if not ref:
        return findings

    ref_sentences = split_sentences(ref)
    text_sentences = split_sentences(text)

    for sent in text_sentences:
        sent_content = set(get_content_words(sent))
        if not sent_content:
            continue

        # Check overlap with each reference sentence
        for ref_sent in ref_sentences:
            ref_content = set(get_content_words(ref_sent))
            if not ref_content:
                continue

            overlap = sent_content & ref_content
            overlap_ratio = len(overlap) / len(sent_content) if sent_content else 0

            if overlap_ratio >= 0.60:
                offset = text.find(sent)
                findings.append(Finding(
                    rule='echo-source',
                    severity=2,
                    start=offset,
                    end=offset + len(sent),
                    excerpt=sent,
                    note=f"Sentence echoes source text ({overlap_ratio*100:.0f}% content-word overlap)"
                ))
                break  # Only report once per sentence

    return findings


def find_opener_repeat(text: str, _ref: Optional[str] = None) -> list[Finding]:
    """
    Detect repeated sentence/paragraph openers:
    - Two consecutive sentences starting with same 2 words
    - Three paragraphs in a row starting with same word
    """
    findings = []

    # Check sentence-level repeats
    all_sentences = split_sentences(text)
    for i in range(len(all_sentences) - 1):
        sent1_words = all_sentences[i].split()[:2]
        sent2_words = all_sentences[i + 1].split()[:2]
        if len(sent1_words) >= 2 and sent1_words == sent2_words:
            offset = text.find(all_sentences[i])
            next_offset = text.find(all_sentences[i + 1], offset + 1)
            findings.append(Finding(
                rule='opener-repeat',
                severity=1,
                start=offset,
                end=next_offset + len(all_sentences[i + 1]),
                excerpt=all_sentences[i][:50] + ' / ' + all_sentences[i + 1][:50],
                note="Two consecutive sentences start with same words"
            ))

    # Check paragraph-level repeats
    paragraphs = split_paragraphs(text)
    for i in range(len(paragraphs) - 2):
        word1 = paragraphs[i].split()[0].lower() if paragraphs[i].split() else ''
        word2 = paragraphs[i + 1].split()[0].lower() if paragraphs[i + 1].split() else ''
        word3 = paragraphs[i + 2].split()[0].lower() if paragraphs[i + 2].split() else ''
        if word1 and word1 == word2 == word3:
            offset = text.find(paragraphs[i])
            end_offset = text.find(paragraphs[i + 2], offset) + len(paragraphs[i + 2])
            findings.append(Finding(
                rule='opener-repeat',
                severity=1,
                start=offset,
                end=end_offset,
                excerpt=f"Para 1: {paragraphs[i][:30]}... / Para 2: {paragraphs[i+1][:30]}... / Para 3: {paragraphs[i+2][:30]}...",
                note=f"Three consecutive paragraphs start with '{word1}'"
            ))

    return findings


def find_hedge_stack(text: str, _ref: Optional[str] = None) -> list[Finding]:
    """
    Detect hedge-stacking: 2+ hedging phrases in one paragraph.
    Hedges: "it is important to note", "it's worth", "arguably", "in many ways",
    "ultimately", "overall"
    """
    findings = []
    hedges = [
        'it is important to note',
        "it's worth",
        'arguably',
        'in many ways',
        'ultimately',
        'overall',
    ]

    for para in split_paragraphs(text):
        hedge_matches = []
        for hedge in hedges:
            pattern = hedge.replace(' ', r'\s+')
            for match in re.finditer(pattern, para, re.IGNORECASE):
                hedge_matches.append((match.start(), match.end(), hedge))

        if len(hedge_matches) >= 2:
            offset = text.find(para)
            first_match = hedge_matches[0]
            last_match = hedge_matches[-1]
            findings.append(Finding(
                rule='hedge-stack',
                severity=1,
                start=offset,
                end=offset + len(para),
                excerpt=para[:100].replace('\n', ' '),
                note=f"Paragraph contains {len(hedge_matches)} hedging phrases"
            ))

    return findings


# Define all rules
RULES = {
    'ai-lexicon': {
        'category': 'vocabulary',
        'severity': 1,
        'description': 'Common AI-sounding words and phrases',
        'finder': find_ai_lexicon,
    },
    'tricolon': {
        'category': 'structure',
        'severity': 1,
        'description': 'Rigid three-part parallel structure',
        'finder': find_tricolon,
    },
    'contrast-frame': {
        'category': 'structure',
        'severity': 1,
        'description': 'Formulaic contrast frames (not X but Y / rather than)',
        'finder': find_contrast_frame,
    },
    'dash-density': {
        'category': 'punctuation',
        'severity': 1,
        'description': 'Excessive em-dash usage',
        'finder': find_dash_density,
    },
    'low-burstiness': {
        'category': 'pacing',
        'severity': 2,
        'description': 'Uniform sentence length (low variance)',
        'finder': find_low_burstiness,
    },
    'bow-tie-closer': {
        'category': 'structure',
        'severity': 1,
        'description': 'Paragraph-closing summary sentence without concrete details',
        'finder': find_bow_tie_closer,
    },
    'echo-source': {
        'category': 'derivation',
        'severity': 2,
        'description': 'Sentence echoes reference text (paraphrase detection)',
        'finder': find_echo_source,
    },
    'opener-repeat': {
        'category': 'pacing',
        'severity': 1,
        'description': 'Repeated sentence/paragraph openers',
        'finder': find_opener_repeat,
    },
    'hedge-stack': {
        'category': 'certainty',
        'severity': 1,
        'description': 'Multiple hedging phrases in one paragraph',
        'finder': find_hedge_stack,
    },
}


def lint_text(text: str, reference: Optional[str] = None) -> list[Finding]:
    """Run all rules against text. Returns findings sorted by position."""
    findings = []

    for rule_id, rule_info in RULES.items():
        finder = rule_info['finder']
        rule_findings = finder(text, reference)
        for finding in rule_findings:
            # Ensure rule id is set
            if not finding.rule:
                finding.rule = rule_id
            findings.append(finding)

    # Sort by position
    findings.sort(key=lambda f: (f.start, f.end))
    return findings


def score_text(findings: list[Finding]) -> dict[str, Any]:
    """
    Calculate score: 100 - sum(severity * 4), floored at 0.
    Verdict: >=80 human-like, 60-79 mixed, <60 machine-like.
    """
    penalty = sum(f.severity * 4 for f in findings)
    score = max(0, 100 - penalty)

    if score >= 80:
        verdict = 'human-like'
    elif score >= 60:
        verdict = 'mixed'
    else:
        verdict = 'machine-like'

    return {'score': score, 'verdict': verdict, 'penalty': penalty}


def main():
    parser = argparse.ArgumentParser(description='Flag machine-writing patterns in text')
    parser.add_argument('--text', required=True, type=str, help='Text file to analyze')
    parser.add_argument('--reference', type=str, default=None, help='Reference text for echo-source rule')
    parser.add_argument('--json', action='store_true', help='Output JSON')
    parser.add_argument('--min-score', type=int, default=0, help='Minimum acceptable score (exit 1 if below)')

    args = parser.parse_args()

    try:
        text_path = Path(args.text)
        if not text_path.exists():
            print(f"Error: text file not found: {args.text}", file=sys.stderr)
            return 2

        text = text_path.read_text(encoding='utf-8')
        reference = None
        if args.reference:
            ref_path = Path(args.reference)
            if not ref_path.exists():
                print(f"Error: reference file not found: {args.reference}", file=sys.stderr)
                return 2
            reference = ref_path.read_text(encoding='utf-8')

        findings = lint_text(text, reference)
        score_info = score_text(findings)

        if args.json:
            output = {
                'score': score_info['score'],
                'verdict': score_info['verdict'],
                'penalty': score_info['penalty'],
                'findings_count': len(findings),
                'findings': [asdict(f) for f in findings],
            }
            print(json.dumps(output, indent=2))
        else:
            print(f"Score: {score_info['score']} ({score_info['verdict']})")
            print(f"Findings: {len(findings)}")
            for f in findings:
                print(f"  [{f.rule}] ({f.severity}) {f.start}:{f.end} – {f.note}")

        # Exit 1 if below min-score
        if score_info['score'] < args.min_score:
            return 1

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
