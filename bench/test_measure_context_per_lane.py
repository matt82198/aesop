"""Behavioral tests for the context paging replay."""

import json
import unittest

from bench.measure_context_per_lane import measure


def row(uid, content, role="user", **fields):
    return dict(type=role, uuid=uid, sessionId="sample", message={"content": content}, **fields)


def finish(uid, lane, report):
    body = "\n".join("  " + line for line in report.split("\n"))
    origin = dict(kind="peer", handback=True, senderTaskId=lane, handbackReportText=report)
    return row(uid, "[Subagent hand-back]\n" + body, origin=origin)


class ContextPagingTests(unittest.TestCase):
    def setUp(self):
        self.done = finish("receipt", "lane-x", "DONE work\n" + "evidence " * 100)
        self.blocked = finish("blocked", "lane-y", "BLOCKED review\nreason")

    def run_rows(self, *rows):
        return measure(rows, "sample")

    def test_admission(self):
        report = self.run_rows(self.done, self.blocked, row("tool", "untouched"))
        self.assertEqual((report["lane_finishes"], report["paged_reports"], report["page_ins"]), (2, 1, 0))
        self.assertLess(report["paged_estimated_tokens"], report["baseline_estimated_tokens"])

    def test_estimate_rounds_each_compact_json_message_in_utf16(self):
        # Compact JSON adds two quotes to each string. Three supplementary
        # characters then occupy eight UTF-16 units, yielding three estimated tokens.
        base = self.run_rows(self.blocked)
        result = self.run_rows(self.blocked, row("a", "\U0001f422" * 3), row("b", ""))
        for arm in ("baseline", "paged"):
            key = arm + "_estimated_tokens"
            self.assertEqual(result[key] - base[key], 4)

    def test_lookup_charges_repeated_request_and_response(self):
        content = [dict(type="tool_use", input={"path": "logs/lane-x/detail"})]
        report = self.run_rows(self.done, row("q", content, "assistant"), row("r", content, "assistant"))
        request = [dict(type="tool_use", name="read_page", input={"page": "handbacks/receipt.txt"})]
        response = [dict(type="tool_result", content=self.done["origin"]["handbackReportText"])]
        # This fixture contains only ASCII. ceil(length / 3.5) = (2*length + 6) // 7.
        lengths = [len(json.dumps(item, separators=(",", ":"))) for item in (request, response)]
        self.assertEqual(report["page_ins"], 2)
        self.assertEqual(report["page_in_estimated_tokens"], 2 * sum((2 * n + 6) // 7 for n in lengths))

    def test_provenance(self):
        report = self.run_rows(self.blocked, dict(self.done, isSidechain=True),
                               dict(self.done, sessionId="other"), self.blocked,
                               row("fake", "[Subagent hand-back] DONE lane-x"))
        self.assertEqual((report["lane_finishes"], report["paged_reports"]), (1, 0))
        self.assertEqual(report["baseline_estimated_tokens"], report["paged_estimated_tokens"])

    def test_page_handle(self):
        query = row("q", "read handbacks/receipt.txt", origin={"kind": "human"})
        self.assertEqual(self.run_rows(self.done, query)["page_ins"], 1)

    def test_no_finish(self):
        with self.assertRaises(ValueError):
            self.run_rows(row("x", "<task-notification>completed</task-notification>"))

    def test_bad_frame(self):
        with self.assertRaises(ValueError):
            self.run_rows(dict(self.done, message={"content": "missing body"}))

    def test_new_version(self):
        changed = finish("r2", "lane-x", "BLOCKED again\nwhy")
        report = self.run_rows(self.done, changed, row("q", "read lane-x", origin={"kind": "human"}))
        self.assertEqual((report["lane_finishes"], report["page_ins"]), (1, 0))
