"""Contract tests for docs/DETERMINISTIC-HARNESS.md and its index links.

Verifies that:
1. docs/DETERMINISTIC-HARNESS.md exists as a concise architecture page covering
   the three layers (thin model orchestrator, deterministic state-machine
   enforcement layer, and stateless isolated workers across Linux, macOS, and
   Windows) and separating deterministic code from model cognition.
2. The ASCII architecture diagram uses box borders and single '->' arrows with
   zero double hyphens and zero em dashes.
3. Exact v1 interfaces from the deterministic-harness contract are documented
   with parseable JSON examples and all required existing and sibling-lane
   component references, and every markdown link in the page resolves on disk.
4. README.md ('## Learn more' -> Concepts) and docs/README.md ('## Understand it')
   link to docs/DETERMINISTIC-HARNESS.md.
5. Prose outside inline and fenced code contains zero em dashes and zero double
   hyphens, and helper scanners catch synthetic violations (non-vacuous).
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DOC = REPO_ROOT / "docs" / "DETERMINISTIC-HARNESS.md"
DOCS_INDEX = REPO_ROOT / "docs" / "README.md"
ROOT_README = REPO_ROOT / "README.md"

EM_DASH = "\u2014"
EN_DASH = "\u2013"
FENCE_RE = re.compile(r"^```([A-Za-z0-9_-]*)\s*$")
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def extract_fenced_blocks(md_text: str) -> list[tuple[str, str]]:
    """Return list of (info_string, body) for all fenced code blocks."""
    blocks: list[tuple[str, str]] = []
    lines = md_text.splitlines()
    in_fence = False
    lang = ""
    buf: list[str] = []
    for line in lines:
        m = FENCE_RE.match(line.strip())
        if not in_fence and m is not None:
            in_fence = True
            lang = m.group(1).lower()
            buf = []
        elif in_fence and line.strip() == "```":
            blocks.append((lang, "\n".join(buf)))
            in_fence = False
            lang = ""
            buf = []
        elif in_fence:
            buf.append(line)
    if in_fence:
        raise ValueError("Unterminated fenced code block in markdown")
    return blocks


def strip_code_spans_and_fences(md_text: str) -> str:
    """Return markdown prose with fenced code blocks and inline code removed."""
    out_lines: list[str] = []
    in_fence = False
    for line in md_text.splitlines():
        if FENCE_RE.match(line.strip()) is not None and not in_fence:
            in_fence = True
            continue
        if in_fence:
            if line.strip() == "```":
                in_fence = False
            continue
        out_lines.append(INLINE_CODE_RE.sub("", line))
    if in_fence:
        raise ValueError("Unterminated fenced code block in markdown")
    return "\n".join(out_lines)


def extract_section(md_text: str, heading: str) -> str:
    """Return body under an exact level-2 heading up to the next level-2 heading."""
    lines = md_text.splitlines()
    capture = False
    body: list[str] = []
    for line in lines:
        if line.strip() == heading:
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture:
            body.append(line)
    return "\n".join(body)


class TestProseHelperScanner(unittest.TestCase):
    """Ensure the markdown prose/fence extractors themselves catch violations."""

    def test_strip_preserves_prose_violations_and_strips_code(self):
        sample = (
            "# Title\n\n"
            "Clean prose with `--flag` inside inline code.\n\n"
            "```text\n"
            "-- inside fence\n"
            "```\n\n"
            "Bad prose -- right here and em" + EM_DASH + "dash.\n"
        )
        prose = strip_code_spans_and_fences(sample)
        self.assertNotIn("--flag", prose)
        self.assertNotIn("inside fence", prose)
        self.assertIn("Bad prose -- right here", prose)
        self.assertIn(EM_DASH, prose)


class TestDeterministicHarnessDoc(unittest.TestCase):
    """Contract checks for docs/DETERMINISTIC-HARNESS.md and README pointers."""

    def test_doc_exists_and_is_concise(self):
        self.assertTrue(
            HARNESS_DOC.is_file(),
            f"Missing architecture doc: {HARNESS_DOC}",
        )
        text = HARNESS_DOC.read_text(encoding="utf-8")
        line_count = len(text.splitlines())
        self.assertGreaterEqual(line_count, 60, "Doc is unexpectedly tiny")
        self.assertLessEqual(
            line_count,
            200,
            f"Doc should remain a concise one-page reference (got {line_count} lines)",
        )

    def test_zero_em_dashes_and_zero_double_hyphens_in_prose(self):
        text = HARNESS_DOC.read_text(encoding="utf-8")
        self.assertNotIn(EM_DASH, text, "Em dash forbidden anywhere in doc")
        self.assertNotIn(EN_DASH, text, "En dash forbidden anywhere in doc")
        prose = strip_code_spans_and_fences(text)
        self.assertNotIn(
            "--",
            prose,
            "Double hyphens forbidden in prose/tables outside inline and fenced code",
        )

    def test_three_layers_and_os_portability_documented(self):
        text = HARNESS_DOC.read_text(encoding="utf-8")
        lower = text.lower()
        for term in (
            "decompose",
            "route",
            "escalat",
            "deterministic",
            "state machine",
            "stateless",
            "worktree",
        ):
            self.assertIn(term, lower, f"Missing core architecture concept: {term}")
        for os_name in ("Linux", "macOS", "Windows"):
            self.assertIn(os_name, text, f"Missing worker OS coverage: {os_name}")

    def test_ascii_architecture_diagram_uses_single_arrows_and_zero_double_hyphens(self):
        text = HARNESS_DOC.read_text(encoding="utf-8")
        text_blocks = [
            body for lang, body in extract_fenced_blocks(text) if lang in ("text", "ascii", "")
        ]
        self.assertGreaterEqual(
            len(text_blocks),
            1,
            "Expected an ASCII architecture diagram in a fenced text block",
        )
        diagram = text_blocks[0]
        self.assertIn("->", diagram, "Diagram must use single '->' arrows")
        self.assertNotIn("--", diagram, "Diagram must contain zero double hyphens")
        self.assertNotIn(EM_DASH, diagram, "Diagram must contain zero em dashes")
        self.assertIn("+", diagram, "Diagram must draw ASCII boxes")
        self.assertIn("|", diagram, "Diagram must draw ASCII box walls")
        for layer_marker in ("Orchestrator", "Enforcement", "Workers"):
            self.assertIn(
                layer_marker,
                diagram,
                f"Diagram missing layer box label: {layer_marker}",
            )

    def test_code_vs_model_and_existing_pieces_linked(self):
        text = HARNESS_DOC.read_text(encoding="utf-8")
        for ref in (
            "tools/verify_receipt.py",
            "Ed25519",
            "tools/event_store/store.py",
            "state_store/store.py",
            "daemons/run-watchdog.sh",
            "schemas/gate-denial.v1.json",
            "hooks/claude/gate-denial.mjs",
            "hooks/claude/no-heredoc-file-authoring.mjs",
            "hooks/claude/no-scrollback-as-record.mjs",
            "tools/verify_handback.py",
            "tools/retry_ledger.py",
        ):
            self.assertIn(ref, text, f"Missing component reference: {ref}")

        links = MD_LINK_RE.findall(text)
        link_targets = {target for _, target in links}
        self.assertIn("../tools/verify_receipt.py", link_targets)
        self.assertIn("../daemons/run-watchdog.sh", link_targets)
        self.assertIn("../tools/event_store/store.py", link_targets)
        self.assertIn("../state_store/store.py", link_targets)
        self.assertIn("../hooks/claude/no-heredoc-file-authoring.mjs", link_targets)
        self.assertIn("../hooks/claude/no-scrollback-as-record.mjs", link_targets)

        broken: list[str] = []
        for _, target in links:
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            rel_part = target.split("#", 1)[0]
            resolved = (HARNESS_DOC.parent / rel_part).resolve()
            if not resolved.exists():
                broken.append(target)
        self.assertEqual(broken, [], f"Broken markdown links in {HARNESS_DOC.name}: {broken}")

    def test_exact_interfaces_for_three_new_pieces(self):
        text = HARNESS_DOC.read_text(encoding="utf-8")
        json_blocks = [
            json.loads(body)
            for lang, body in extract_fenced_blocks(text)
            if lang == "json"
        ]
        self.assertGreaterEqual(
            len(json_blocks),
            2,
            "Expected JSON payload examples for gate-denial v1 and verify_handback verdict",
        )

        denial_payloads = [
            obj for obj in json_blocks
            if isinstance(obj, dict) and obj.get("v") == 1 and obj.get("deny") is True
        ]
        self.assertEqual(
            len(denial_payloads),
            1,
            "Expected exactly one v1 gate-denial JSON example",
        )
        denial = denial_payloads[0]
        self.assertEqual(
            set(denial.keys()),
            {"v", "deny", "gate", "reason", "remedy", "allowed_tools"},
        )
        self.assertEqual(denial["gate"], "no-heredoc-file-authoring")
        self.assertEqual(
            denial["reason"],
            "Heredocs are banned in Bash commands. Write a script file with the Write tool, then run it.",
        )
        self.assertEqual(
            denial["remedy"],
            {"action": "use_tool", "args": {"tools": ["Write", "Edit"]}},
        )
        self.assertEqual(denial["allowed_tools"], ["Write", "Edit"])

        for action in ("dispatch_reader", "retry_with", "use_tool", "stop_and_report"):
            self.assertIn(action, text, f"Missing gate-denial remedy action: {action}")
        self.assertIn("GATE-DENY", text)

        for handback_term in (
            "verify_receipt.py",
            "test_run",
            "exit_code",
            "ACCEPT",
            "REJECT",
            "ERROR",
        ):
            self.assertIn(handback_term, text, f"Missing verify_handback term: {handback_term}")

        for ledger_term in (
            "AESOP_STATE_ROOT",
            "start(task)",
            "heartbeat(task)",
            "fail(task, reason)",
            "status(task)",
            "RUNNING",
            "RETRY",
            "BLOCKED",
            "3",
            "stale(threshold_s)",
        ):
            self.assertIn(ledger_term, text, f"Missing retry_ledger term: {ledger_term}")

    def test_verify_handback_v1_verdict_schema(self):
        text = HARNESS_DOC.read_text(encoding="utf-8")
        json_blocks = [
            json.loads(body)
            for lang, body in extract_fenced_blocks(text)
            if lang == "json"
        ]
        handback_payloads = [
            obj for obj in json_blocks
            if isinstance(obj, dict) and "verdict" in obj
        ]
        self.assertEqual(
            len(handback_payloads),
            1,
            "Expected exactly one verify_handback v1 verdict JSON example",
        )
        verdict = handback_payloads[0]
        self.assertEqual(
            set(verdict.keys()),
            {"v", "ok", "verdict", "lane", "head_sha", "checks", "reasons"},
        )
        self.assertEqual(verdict["v"], 1)
        self.assertIs(verdict["ok"], True)
        self.assertEqual(verdict["verdict"], "ACCEPT")
        self.assertIsInstance(verdict["lane"], str)
        self.assertIsInstance(verdict["head_sha"], str)
        self.assertIsInstance(verdict["reasons"], list)
        self.assertIsInstance(verdict["checks"], dict)
        self.assertEqual(
            set(verdict["checks"].keys()),
            {"receipt", "worktree_clean", "scope", "tests"},
        )
        self.assertEqual(
            set(verdict["checks"]["receipt"].keys()),
            {"ok", "status", "exit_code", "reasons"},
        )
        self.assertEqual(
            set(verdict["checks"]["worktree_clean"].keys()),
            {"ok", "status", "dirty_paths", "reasons"},
        )
        self.assertEqual(
            set(verdict["checks"]["scope"].keys()),
            {"ok", "status", "declared_scope", "diff_paths", "out_of_scope", "reasons"},
        )
        self.assertEqual(
            set(verdict["checks"]["tests"].keys()),
            {"ok", "status", "command", "exit_code", "reasons"},
        )

    def test_readme_and_docs_index_links(self):
        readme_text = ROOT_README.read_text(encoding="utf-8")
        learn_more = extract_section(readme_text, "## Learn more")
        self.assertTrue(learn_more, "Missing '## Learn more' section in README.md")
        concepts_lines = [
            line for line in learn_more.splitlines() if "**Concepts**" in line
        ]
        self.assertEqual(len(concepts_lines), 1, "Expected one Concepts bullet in README.md")
        self.assertIn(
            "[deterministic harness](./docs/DETERMINISTIC-HARNESS.md)",
            concepts_lines[0],
        )
        self.assertNotIn(EM_DASH, concepts_lines[0])
        self.assertNotIn("--", concepts_lines[0])

        docs_readme_text = DOCS_INDEX.read_text(encoding="utf-8")
        understand_it = extract_section(docs_readme_text, "## Understand it")
        self.assertTrue(understand_it, "Missing '## Understand it' section in docs/README.md")
        matching_rows = [
            line
            for line in understand_it.splitlines()
            if "[Deterministic harness](./DETERMINISTIC-HARNESS.md)" in line
        ]
        self.assertEqual(
            len(matching_rows),
            1,
            "Expected one Deterministic harness row under '## Understand it' in docs/README.md",
        )
        self.assertTrue(matching_rows[0].strip().startswith("|"))
        self.assertNotIn(EM_DASH, matching_rows[0])
        self.assertNotIn("--", matching_rows[0])


if __name__ == "__main__":
    unittest.main()
