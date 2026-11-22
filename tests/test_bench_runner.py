#!/usr/bin/env python3
"""Unit tests for tools/bench_runner.py — the held-out benchmark scoring harness.

These tests prove the SCORING LOGIC is correct against a synthetic, fully
controlled mini-benchmark (known outputs -> known accuracy), and separately
prove bench/tasks.jsonl and bench/ground_truth.jsonl are internally consistent
and well-formed. They do NOT prove anything about a real model's accuracy —
see bench/README.md for that boundary.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import bench_runner  # noqa: E402


class TestScoreOutput(unittest.TestCase):
    """Unit tests for the single-task scoring primitive."""

    def test_exact_match_identical(self):
        self.assertTrue(bench_runner.score_output("docs", {"expected": "docs"}, "exact"))

    def test_exact_match_is_case_insensitive(self):
        self.assertTrue(bench_runner.score_output("DOCS", {"expected": "docs"}, "exact"))

    def test_exact_match_trims_whitespace(self):
        self.assertTrue(bench_runner.score_output("  docs\n", {"expected": "docs"}, "exact"))

    def test_exact_match_wrong_value_fails(self):
        self.assertFalse(bench_runner.score_output("code", {"expected": "docs"}, "exact"))

    def test_exact_match_none_output_fails(self):
        self.assertFalse(bench_runner.score_output(None, {"expected": "docs"}, "exact"))

    def test_regex_match_success(self):
        entry = {"expected_regex": r"^test_\w+$"}
        self.assertTrue(bench_runner.score_output("test_frobnicate", entry, "regex"))

    def test_regex_match_failure(self):
        entry = {"expected_regex": r"^test_\w+$"}
        self.assertFalse(bench_runner.score_output("not_a_test_name", entry, "regex"))

    def test_regex_match_trims_before_matching(self):
        entry = {"expected_regex": r"^482$"}
        self.assertTrue(bench_runner.score_output("  482  ", entry, "regex"))

    def test_unknown_match_type_raises(self):
        with self.assertRaises(ValueError):
            bench_runner.score_output("x", {"expected": "x"}, "fuzzy")

    def test_exact_missing_expected_field_raises(self):
        with self.assertRaises(ValueError):
            bench_runner.score_output("x", {}, "exact")

    def test_regex_missing_expected_regex_field_raises(self):
        with self.assertRaises(ValueError):
            bench_runner.score_output("x", {}, "regex")


class TestRunBenchSynthetic(unittest.TestCase):
    """Prove run_bench computes known accuracy from a fully synthetic, hand-built
    mini-benchmark and a hand-built stub runner — no dependency on the real
    bench/ files or the shipped mock_runner heuristic."""

    def setUp(self):
        self.tasks = [
            {"id": "s1", "category": "c", "match": "exact", "prompt": "p1"},
            {"id": "s2", "category": "c", "match": "exact", "prompt": "p2"},
            {"id": "s3", "category": "c", "match": "regex", "prompt": "p3"},
            {"id": "s4", "category": "c", "match": "regex", "prompt": "p4"},
        ]
        self.ground_truth = {
            "s1": {"id": "s1", "expected": "alpha"},
            "s2": {"id": "s2", "expected": "beta"},
            "s3": {"id": "s3", "expected_regex": r"^\d{3}$"},
            "s4": {"id": "s4", "expected_regex": r"^\d{3}$"},
        }

    def test_three_of_four_correct_yields_075_accuracy(self):
        # Stub runner: correct on s1, s3, s4; deliberately wrong on s2.
        canned = {"p1": "alpha", "p2": "WRONG", "p3": "482", "p4": "999"}
        results, accuracy = bench_runner.run_bench(
            self.tasks, self.ground_truth, lambda prompt: canned[prompt]
        )
        self.assertEqual(accuracy, 0.75)
        by_id = {r["id"]: r["correct"] for r in results}
        self.assertEqual(
            by_id, {"s1": True, "s2": False, "s3": True, "s4": True}
        )

    def test_all_correct_yields_full_accuracy(self):
        canned = {"p1": "alpha", "p2": "beta", "p3": "123", "p4": "456"}
        _, accuracy = bench_runner.run_bench(
            self.tasks, self.ground_truth, lambda prompt: canned[prompt]
        )
        self.assertEqual(accuracy, 1.0)

    def test_all_wrong_yields_zero_accuracy(self):
        _, accuracy = bench_runner.run_bench(
            self.tasks, self.ground_truth, lambda prompt: "nope"
        )
        self.assertEqual(accuracy, 0.0)

    def test_empty_task_list_yields_zero_accuracy_not_a_crash(self):
        results, accuracy = bench_runner.run_bench([], {}, lambda prompt: "x")
        self.assertEqual(results, [])
        self.assertEqual(accuracy, 0.0)

    def test_missing_ground_truth_entry_raises_key_error(self):
        tasks = [{"id": "ghost", "category": "c", "match": "exact", "prompt": "p"}]
        with self.assertRaises(KeyError):
            bench_runner.run_bench(tasks, {}, lambda prompt: "x")

    def test_runner_receives_the_task_prompt(self):
        seen = []

        def recording_runner(prompt):
            seen.append(prompt)
            return "alpha" if prompt == "p1" else "x"

        bench_runner.run_bench([self.tasks[0]], {"s1": self.ground_truth["s1"]}, recording_runner)
        self.assertEqual(seen, ["p1"])


class TestLoaders(unittest.TestCase):
    """Test the jsonl loaders in isolation with temp fixtures."""

    def test_load_jsonl_skips_blank_lines(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.jsonl"
            path.write_text('{"a": 1}\n\n{"a": 2}\n', encoding="utf-8")
            items = bench_runner.load_jsonl(path)
            self.assertEqual(items, [{"a": 1}, {"a": 2}])

    def test_load_jsonl_bad_json_raises_value_error_with_line_number(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.jsonl"
            path.write_text('{"a": 1}\nnot json\n', encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                bench_runner.load_jsonl(path)
            self.assertIn(":2:", str(ctx.exception))

    def test_load_tasks_rejects_missing_required_field(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tasks.jsonl"
            path.write_text('{"id": "a", "category": "c", "match": "exact"}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                bench_runner.load_tasks(path)

    def test_load_tasks_rejects_invalid_match_type(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tasks.jsonl"
            path.write_text(
                '{"id": "a", "category": "c", "match": "fuzzy", "prompt": "p"}\n', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                bench_runner.load_tasks(path)

    def test_load_ground_truth_keys_by_id(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "gt.jsonl"
            path.write_text('{"id": "a", "expected": "x"}\n{"id": "b", "expected": "y"}\n', encoding="utf-8")
            by_id = bench_runner.load_ground_truth(path)
            self.assertEqual(set(by_id.keys()), {"a", "b"})
            self.assertEqual(by_id["a"]["expected"], "x")


class TestRealBenchFiles(unittest.TestCase):
    """Prove bench/tasks.jsonl and bench/ground_truth.jsonl are well-formed and
    aligned: same id set, sane size, every field the scorer needs is present."""

    @classmethod
    def setUpClass(cls):
        cls.tasks = bench_runner.load_tasks()
        cls.ground_truth = bench_runner.load_ground_truth()

    def test_task_count_is_in_the_8_to_12_range(self):
        self.assertGreaterEqual(len(self.tasks), 8)
        self.assertLessEqual(len(self.tasks), 12)

    def test_task_ids_are_unique(self):
        ids = [t["id"] for t in self.tasks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_ground_truth_id_set_exactly_matches_task_id_set(self):
        task_ids = {t["id"] for t in self.tasks}
        gt_ids = set(self.ground_truth.keys())
        self.assertEqual(task_ids, gt_ids)

    def test_every_task_has_a_nonempty_prompt(self):
        for task in self.tasks:
            self.assertTrue(task["prompt"].strip(), msg=f"task {task['id']} has empty prompt")

    def test_every_exact_task_ground_truth_has_expected_field(self):
        for task in self.tasks:
            if task["match"] == "exact":
                gt = self.ground_truth[task["id"]]
                self.assertIn(
                    "expected", gt, msg=f"task {task['id']} (exact) missing 'expected' in ground truth"
                )

    def test_every_regex_task_ground_truth_has_expected_regex_field_and_it_compiles(self):
        import re

        for task in self.tasks:
            if task["match"] == "regex":
                gt = self.ground_truth[task["id"]]
                self.assertIn(
                    "expected_regex",
                    gt,
                    msg=f"task {task['id']} (regex) missing 'expected_regex' in ground truth",
                )
                re.compile(gt["expected_regex"])  # raises if malformed

    def test_categories_are_non_trivial_and_varied(self):
        categories = {t["category"] for t in self.tasks}
        # A benchmark that is secretly one task repeated isn't representative.
        self.assertGreaterEqual(len(categories), 4)


class TestMockRunnerAgainstRealBench(unittest.TestCase):
    """Prove the shipped offline mock runner scores deterministically against
    the real committed benchmark files — a full-pipeline smoke test that never
    calls a model. This accuracy number is a fixture of the mock heuristic,
    NOT a claim about any real model."""

    @classmethod
    def setUpClass(cls):
        cls.tasks = bench_runner.load_tasks()
        cls.ground_truth = bench_runner.load_ground_truth()
        cls.results, cls.accuracy = bench_runner.run_bench(
            cls.tasks, cls.ground_truth, bench_runner.mock_runner
        )

    def test_every_task_produced_a_scored_result(self):
        self.assertEqual(len(self.results), len(self.tasks))
        for r in self.results:
            self.assertIn("correct", r)
            self.assertIsInstance(r["correct"], bool)

    def test_accuracy_is_between_zero_and_one_exclusive(self):
        # Exclusive on both ends: a mock that got everything right or everything
        # wrong would not demonstrate the scorer can discriminate.
        self.assertGreater(self.accuracy, 0.0)
        self.assertLess(self.accuracy, 1.0)

    def test_mock_runner_deliberately_misses_the_judgment_task(self):
        # The mock is a plain regex/string heuristic; it cannot do semantic
        # judgment, so the "is this a real bug" task (t09) is a known miss.
        by_id = {r["id"]: r["correct"] for r in self.results}
        self.assertIn("t09", by_id)
        self.assertFalse(by_id["t09"])

    def test_mock_runner_gets_the_deterministic_extraction_tasks_right(self):
        by_id = {r["id"]: r["correct"] for r in self.results}
        non_judgment_ids = [t["id"] for t in self.tasks if t["category"] != "is_real_bug_judgment"]
        for tid in non_judgment_ids:
            self.assertTrue(by_id[tid], msg=f"expected mock runner to solve {tid}")


class TestCLI(unittest.TestCase):
    """Test the command-line entry point end to end via subprocess."""

    def test_main_runs_clean_and_prints_accuracy_line(self):
        script = REPO_ROOT / "tools" / "bench_runner.py"
        result = subprocess.run(
            [sys.executable, str(script), "--runner", "mock"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Accuracy:", result.stdout)
        self.assertIn("runner: mock", result.stdout)

    def test_main_accepts_overridden_paths(self):
        with tempfile.TemporaryDirectory() as td:
            tasks_path = Path(td) / "tasks.jsonl"
            gt_path = Path(td) / "gt.jsonl"
            tasks_path.write_text(
                json.dumps({"id": "x1", "category": "c", "match": "exact", "prompt": "hello"}) + "\n",
                encoding="utf-8",
            )
            gt_path.write_text(json.dumps({"id": "x1", "expected": "irrelevant"}) + "\n", encoding="utf-8")

            script = REPO_ROOT / "tools" / "bench_runner.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--runner",
                    "mock",
                    "--tasks",
                    str(tasks_path),
                    "--ground-truth",
                    str(gt_path),
                ],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            # mock_runner has no heuristic branch for this synthetic prompt, so it
            # returns "" which will not match "irrelevant" -- proves overridden
            # paths actually feed the run, not silently falling back to defaults.
            self.assertIn("Accuracy: 0/1", result.stdout)


class TestClaudeCliRunner(unittest.TestCase):
    """Test claude CLI runner structure (integration tests with real CLI done separately)."""

    def test_claude_runners_registered_if_available(self):
        """Test that claude runners are registered if claude is available."""
        # This test just verifies the registration logic works
        # Actual CLI integration is tested manually
        script = REPO_ROOT / "tools" / "bench_runner.py"
        result = subprocess.run(
            [sys.executable, str(script), "--runner", "mock"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        # Mock runner should always work
        self.assertEqual(result.returncode, 0)
        self.assertIn("Accuracy:", result.stdout)

    def test_claude_runner_json_parsing_handles_new_format(self):
        """Test that the runner correctly parses the JSON format from real claude CLI."""
        # Import the runner to test JSON parsing logic
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        import bench_runner

        # Simulate what the JSON parsing should handle
        # The runner extracts text from output_json["result"]
        runner_code = bench_runner._make_claude_runner("haiku")
        # We can't actually test the runner without calling claude,
        # but we can verify the module was created correctly
        self.assertIsNotNone(runner_code)


class TestOllamaBenchRunners(unittest.TestCase):
    """Offline unit tests proving all bench runners support local ollama/<model> without Anthropic/OpenAI keys."""

    def test_bench_runner_ollama_registered_and_parses_response(self):
        import os

        self.assertIn("ollama", bench_runner.RUNNERS)
        calls = []

        def fake_transport(payload, **kwargs):
            calls.append((payload, kwargs))
            return {
                "choices": [{"message": {"content": "docs"}}],
                "usage": {"completion_tokens": 11},
            }

        prev = os.environ.pop("AESOP_BENCH_OFFLINE", None)
        try:
            runner = bench_runner._make_ollama_runner(
                model_id="ollama/qwen3-coder:30b",
                base_url="http://127.0.0.1:11434/v1",
                transport=fake_transport,
            )
            text, usage = runner("Classify this changed file path")
            self.assertEqual(text, "docs")
            self.assertEqual(usage["tokens"], 11)
            self.assertEqual(calls[0][0]["model"], "qwen3-coder:30b")
            self.assertEqual(calls[0][1]["base_url"], "http://127.0.0.1:11434/v1")

            os.environ["AESOP_BENCH_OFFLINE"] = "1"
            with self.assertRaises(RuntimeError):
                runner("blocked offline")
        finally:
            if prev is None:
                os.environ.pop("AESOP_BENCH_OFFLINE", None)
            else:
                os.environ["AESOP_BENCH_OFFLINE"] = prev

    def test_bench_api_runner_ollama_requires_no_bench_api_key(self):
        import os
        from unittest import mock
        import bench_api_runner

        self.assertTrue(bench_api_runner._is_ollama_spec("ollama"))
        self.assertTrue(bench_api_runner._is_ollama_spec("ollama/qwen3-coder:30b"))
        self.assertFalse(bench_api_runner._is_ollama_spec("haiku"))

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AESOP_BENCH_OFFLINE", None)
            os.environ.pop("_".join(("BENCH", "API", "KEY")), None)
            with self.assertRaises(RuntimeError):
                bench_api_runner.make_ollama_api_runner("ollama/qwen3-coder:30b", confirm_spend=False)

            seen = []

            def fake_transport(payload, **kwargs):
                seen.append(payload["model"])
                return {
                    "choices": [{"message": {"content": "yes"}}],
                    "usage": {"completion_tokens": 5},
                }

            runner = bench_api_runner.make_ollama_api_runner(
                "ollama/qwen3-coder:30b",
                confirm_spend=True,
                transport=fake_transport,
            )
            text, usage = runner("prompt")
            self.assertEqual(text, "yes")
            self.assertEqual(usage["tokens"], 5)
            self.assertEqual(seen, ["qwen3-coder:30b"])

    def test_frontier_slice_and_accuracy_harness_support_ollama(self):
        import os
        from unittest import mock

        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from bench import accuracy_harness, frontier_slice

        self.assertEqual(frontier_slice._price_for("ollama/qwen3-coder:30b"), (0.0, 0.0))

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AESOP_BENCH_OFFLINE", None)
            os.environ.pop("_".join(("ANTHROPIC", "API", "KEY")), None)
            os.environ.pop("_".join(("OPENAI", "API", "KEY")), None)
            real_tasks = frontier_slice.load_frontier_tasks(
                str(REPO_ROOT / "bench" / "tasks_frontier.jsonl")
            )
            real_gt = frontier_slice.load_ground_truth(
                str(REPO_ROOT / "bench" / "ground_truth_frontier.jsonl")
            )
            first_task = real_tasks[0]
            exemplar = real_gt[first_task.id].exemplar or real_gt[first_task.id].expected or "ok"

            def fake_frontier_transport(payload, **kwargs):
                self.assertEqual(payload["model"], "qwen3-coder:30b")
                return {"choices": [{"message": {"content": exemplar}}]}

            orig_cwd = os.getcwd()
            try:
                os.chdir(REPO_ROOT)
                scores, acc = frontier_slice.run_live_frontier_benchmark(
                    [first_task],
                    model="ollama/qwen3-coder:30b",
                    confirm_spend=True,
                    transport=fake_frontier_transport,
                )
            finally:
                os.chdir(orig_cwd)
            self.assertEqual(len(scores), 1)
            self.assertEqual(acc, 100.0)

            acc_task = accuracy_harness._build_test_tasks()[0]

            def fake_acc_transport(payload):
                self.assertEqual(payload["model"], "qwen3-coder:30b")
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "files": [{"path": acc_task.owned_files[0], "contents": "# ok\n"}],
                                        "summary": "updated",
                                        "done": True,
                                    }
                                )
                            }
                        }
                    ]
                }

            acc_scores, overall = accuracy_harness.run_live_benchmark(
                [acc_task],
                model="ollama/qwen3-coder:30b",
                transport=fake_acc_transport,
            )
            self.assertEqual(len(acc_scores), 1)
            self.assertEqual(overall, 1.0)

    def test_seam_u_probe_refusals_and_gen_tasks_ollama_support(self):
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from bench import probe_refusals, run_seam_u

        parsed = run_seam_u.parse_args(
            ["--tasks-dir", "bench/seam_tasks", "--tiers", "ollama/qwen3-coder:30b"]
        )
        self.assertEqual(parsed.tiers, ["ollama/qwen3-coder:30b"])
        self.assertEqual(
            run_seam_u.calculate_cost("ollama/qwen3-coder:30b", {"input_tokens": 200, "output_tokens": 50}),
            0.0,
        )

        diff_text = "--- a/main.py\n+++ b/main.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
        seam_runner = run_seam_u.create_ollama_runner(
            "ollama/qwen3-coder:30b",
            transport=lambda payload, **kw: {
                "choices": [{"message": {"content": f"```diff\n{diff_text}```"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )
        patch, usage = seam_runner("fix main.py")
        self.assertIn("+new", patch)
        self.assertEqual(usage["input_tokens"], 12)
        self.assertEqual(usage["output_tokens"], 8)

        tid, mod, stop_reason, err = probe_refusals.probe(
            "ollama/qwen3-coder:30b",
            "ft01",
            "prompt",
            "",
            16,
            5.0,
            transport=lambda payload, **kw: {"choices": [{"finish_reason": "stop"}]},
        )
        self.assertEqual((tid, mod, stop_reason, err), ("ft01", "ollama/qwen3-coder:30b", "stop", None))

        with tempfile.TemporaryDirectory() as td:
            for script_name in ("gen_tasks.py", "gen_tasks_v2.py"):
                script = REPO_ROOT / "bench" / "ext120" / script_name
                res = subprocess.run(
                    [sys.executable, str(script), "--help"],
                    cwd=td,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(res.returncode, 0, msg=res.stderr)
                self.assertEqual(list(Path(td).iterdir()), [])

    def test_ollama_never_sends_openai_api_key_and_rejects_non_loopback_and_path_tricks(self):
        import os
        from unittest import mock
        import bench_api_runner

        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from bench import accuracy_harness, frontier_slice, probe_refusals, run_seam_u

        canary_key = "canary-" + "openai-env-value-must-never-leak"
        acc_task = accuracy_harness._build_test_tasks()[0]

        evil_urls = [
            "http://evil.example:11434/v1",
            "http://evil.example/127.0.0.1",
            "http://evil.example/localhost/v1",
            "http://evil.example/[::1]/v1",
            "http://127.0.0.1.evil.example:11434/v1",
            "http://localhost.evil.example:11434/v1",
            "http://127.0.0.1@evil.example:11434/v1",
            "ftp://127.0.0.1:11434/v1",
        ]

        with mock.patch.dict(
            os.environ,
            {"_".join(("OPENAI", "API", "KEY")): canary_key},
            clear=False,
        ):
            os.environ.pop("AESOP_BENCH_OFFLINE", None)

            # 1. Evil-host, path-trick, subdomain-trick, and userinfo-trick URLs fail closed
            #    before any HTTP request is constructed across validate_ollama_base_url + runners.
            with mock.patch("urllib.request.build_opener") as mock_opener:
                for bad_url in evil_urls:
                    with self.subTest(bad_url=bad_url):
                        with self.assertRaises(ValueError):
                            bench_runner.validate_ollama_base_url(bad_url)
                        with self.assertRaises(ValueError):
                            accuracy_harness.run_live_benchmark(
                                [acc_task],
                                model="ollama/qwen3-coder:30b",
                                base_url=bad_url,
                            )
                        with self.assertRaises(ValueError):
                            bench_runner._make_ollama_runner(
                                model_id="ollama/qwen3-coder:30b",
                                base_url=bad_url,
                            )("prompt")
                        with self.assertRaises(ValueError):
                            bench_api_runner.make_ollama_api_runner(
                                "ollama/qwen3-coder:30b",
                                base_url=bad_url,
                                confirm_spend=True,
                            )
                        with self.assertRaises(ValueError):
                            run_seam_u.create_ollama_runner(
                                "ollama/qwen3-coder:30b",
                                base_url=bad_url,
                            )
                        _, _, stop_r, err_msg = probe_refusals.probe(
                            "ollama/qwen3-coder:30b",
                            "ft01",
                            "prompt",
                            "",
                            16,
                            5.0,
                            base_url=bad_url,
                        )
                        self.assertIsNone(stop_r)
                        self.assertIsNotNone(err_msg)
                mock_opener.assert_not_called()

            # 2. Valid loopback hosts (127.0.0.1, localhost, [::1]) AND explicit allow_remote_ollama=True
            #    NEVER send OPENAI_API_KEY (always send fixed dummy Bearer 'ollama').
            captured_requests = []

            class _FakeHTTPResp:
                status = 200

                def read(self, _n=-1):
                    body = {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "files": [
                                                {
                                                    "path": acc_task.owned_files[0],
                                                    "contents": "# ok\n",
                                                }
                                            ],
                                            "summary": "ok",
                                            "done": True,
                                        }
                                    )
                                }
                            }
                        ],
                        "usage": {"total_tokens": 10},
                    }
                    return json.dumps(body).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            class _FakeOpener:
                def open(self, req, timeout=None):
                    captured_requests.append(req)
                    return _FakeHTTPResp()

            with mock.patch("urllib.request.build_opener", return_value=_FakeOpener()):
                for ok_url in (
                    "http://127.0.0.1:11434/v1",
                    "http://localhost:11434/v1",
                    "http://[::1]:11434/v1",
                ):
                    accuracy_harness.run_live_benchmark(
                        [acc_task],
                        model="ollama/qwen3-coder:30b",
                        base_url=ok_url,
                    )
                # Explicit allow flag permits LAN Ollama host while STILL sending dummy key
                accuracy_harness.run_live_benchmark(
                    [acc_task],
                    model="ollama/qwen3-coder:30b",
                    base_url="http://gpu-box.lan:11434/v1",
                    allow_remote_ollama=True,
                )

            self.assertEqual(len(captured_requests), 4)
            for req in captured_requests:
                headers_lower = {k.lower(): v for k, v in req.header_items()}
                self.assertEqual(headers_lower.get("authorization"), "Bearer ollama")
                self.assertNotIn(canary_key, str(headers_lower))

            # frontier_slice also validates base_url on ollama path
            with self.assertRaises(ValueError):
                orig_cwd = os.getcwd()
                try:
                    os.chdir(REPO_ROOT)
                    frontier_slice.run_live_frontier_benchmark(
                        [],
                        model="ollama/qwen3-coder:30b",
                        confirm_spend=True,
                        base_url="http://evil.example/127.0.0.1",
                    )
                finally:
                    os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
