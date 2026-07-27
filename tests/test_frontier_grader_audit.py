#!/usr/bin/env python3
"""Grader-error audit gate (Amendment 3, permanent).

The v3 pattern audit found six tasks that were impossible or wrong to grade:
exact-match grading colliding with explanation-demanding prompts, an anchored
regex that any preamble broke, and two factually wrong expected answers whose
bare-word exemplars sailed through the old gate. This gate closes the grading
half of that class mechanically.

For every task whose prompt pins a closed token set ("First line of your
response: exactly A or B ..."), this suite:
1. parses the token set from the prompt;
2. identifies the correct token as the unique token the expected_regex accepts;
3. synthesizes realistic response SHAPES for every token (bare, markdown-bold,
   "Answer:" prefixed, lowercase, and token-plus-verbose-explanation) and
   asserts every correct-token shape MATCHES while every wrong-token shape is
   REJECTED — exactly as score_response will evaluate them
   (re.search with IGNORECASE | DOTALL);
4. requires the exemplar to be a realistic verbose response (multi-line, not a
   bare grading token) so unrealistic exemplars can never bless a pattern again.

Ground-truth FACTUAL correctness cannot be mechanized here; it is covered by
the authoring rule (executable verification in the rationale) and review.
"""

import json
import re
import unittest

TOKEN_LINE = re.compile(r"First line(?: of your response)?:\s*exactly\s+(.+)", re.IGNORECASE)
SCORE_FLAGS = re.IGNORECASE | re.DOTALL  # keep identical to score_response


def load_jsonl(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                out[obj["id"]] = obj
    return out


def parse_token_set(prompt):
    """Extract the pinned closed token set from a prompt, or None."""
    m = TOKEN_LINE.search(prompt)
    if not m:
        return None
    spec = m.group(1).split("\n")[0].strip().rstrip(".")
    # Accept "A or B", "A, B, or C", "A or B or C", "A / B / C"
    parts = re.split(r"\s*,\s*|\s+or\s+|\s*/\s*", spec)
    parts = [re.sub(r"^or\s+", "", p.strip()) for p in parts]
    tokens = [p.strip().strip("`'\"") for p in parts if p.strip()]
    # Tokens are uppercase-with-underscores by authoring rule; drop empties/stragglers
    tokens = [t for t in tokens if re.fullmatch(r"[A-Z0-9_]+", t)]
    return tokens if len(tokens) >= 2 else None


def shapes(token):
    """Realistic response shapes score_response must handle for a token."""
    return [
        token,
        f"**{token}**",
        f"Answer: {token}",
        token.lower(),
        f"{token}\nExplanation: this follows from tracing the semantics of the "
        "scenario step by step, which yields exactly this outcome.",
        f"**{token}**\n\nReasoning: the key rule determines the result; applying "
        "it to the given inputs produces the outcome stated on the first line.",
    ]


class TestGraderAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = load_jsonl("bench/tasks_frontier.jsonl")
        cls.gt = load_jsonl("bench/ground_truth_frontier.jsonl")
        cls.pinned = {
            tid: parse_token_set(t["prompt"])
            for tid, t in cls.tasks.items()
            if parse_token_set(t["prompt"])
        }

    def test_pinned_tasks_are_regex_graded(self):
        """Exact-match grading is banned for pinned-token tasks (the ft09 class)."""
        offenders = [
            tid for tid in self.pinned if self.tasks[tid].get("match") != "regex"
        ]
        self.assertEqual(
            offenders, [],
            f"pinned-token tasks must use regex grading: {offenders}",
        )

    def test_exactly_one_correct_token(self):
        """The expected_regex must accept exactly one token from the pinned set."""
        for tid, tokens in self.pinned.items():
            pattern = self.gt[tid]["expected_regex"]
            accepted = [t for t in tokens if re.search(pattern, t, SCORE_FLAGS)]
            self.assertEqual(
                len(accepted), 1,
                f"{tid}: expected_regex accepts {accepted} from {tokens}; "
                "must accept exactly one (token collision or dead pattern)",
            )

    def test_correct_token_shapes_all_match(self):
        failures = []
        for tid, tokens in self.pinned.items():
            pattern = self.gt[tid]["expected_regex"]
            correct = [t for t in tokens if re.search(pattern, t, SCORE_FLAGS)]
            if len(correct) != 1:
                continue  # reported by test_exactly_one_correct_token
            for shape in shapes(correct[0]):
                if not re.search(pattern, shape, SCORE_FLAGS):
                    failures.append(f"{tid}: correct shape rejected: {shape[:60]!r}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_wrong_token_shapes_all_reject(self):
        failures = []
        for tid, tokens in self.pinned.items():
            pattern = self.gt[tid]["expected_regex"]
            correct = set(t for t in tokens if re.search(pattern, t, SCORE_FLAGS))
            for wrong in tokens:
                if wrong in correct:
                    continue
                for shape in shapes(wrong):
                    if re.search(pattern, shape, SCORE_FLAGS):
                        failures.append(
                            f"{tid}: wrong-token shape matched: {shape[:60]!r}"
                        )
        self.assertEqual(failures, [], "\n".join(failures))

    def test_exemplars_are_realistic_verbose_responses(self):
        """Bare-word exemplars blessed the six defects; require realism."""
        failures = []
        for tid in self.pinned:
            ex = self.gt[tid]["exemplar"]
            has_newline = "\n" in ex
            if not has_newline or len(ex) < 80:
                failures.append(
                    f"{tid}: exemplar must be a multi-line realistic response "
                    f"(got {len(ex)} chars, newline={has_newline})"
                )
        self.assertEqual(failures, [], "\n".join(failures))

    def test_pinned_coverage_includes_all_new_tasks(self):
        """Every ft101+ task (and the six repaired ones) must carry a pinned set."""
        must_pin = {"ft09", "ft37", "ft88", "ft95", "ft99", "ft100"}
        for tid in self.tasks:
            m = re.match(r"ft(\d+)", tid)
            if m and int(m.group(1)) >= 101:
                must_pin.add(tid.split("_")[0])
        missing = []
        for tid in self.tasks:
            prefix = tid.split("_")[0]
            if prefix in must_pin and tid not in self.pinned:
                missing.append(tid)
        self.assertEqual(
            missing, [],
            f"tasks missing a parseable pinned token set: {missing}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
