"""Offline regression coverage for the native ticket evaluation."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bench.native_headless import qualify


def trace(model, provider):
    return [
        {"type": "session_meta", "payload": {
            "source": "exec", "originator": "codex_exec", "model_provider": provider}},
        {"type": "turn_context", "payload": {"model": model}},
    ]


class NativeQualificationTests(unittest.TestCase):
    def test_package_driver_in_clean_interpreter(self):
        root = Path(qualify.__file__).resolve().parents[2]
        code = (
            "import sys\nsys.path.insert(0, sys.argv[1])\n"
            "from driver.agent_driver import AgentDriver\n"
            "from driver.headless_cli_driver import HeadlessCliDriver\n"
            "assert isinstance(HeadlessCliDriver('codex-exec'), AgentDriver)"
        )
        proc = subprocess.run([sys.executable, "-I", "-c", code, str(root)],
                              capture_output=True, encoding="utf-8", errors="replace", timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_frozen_fixture_and_grader(self):
        from bench.native_headless import grading, tickets
        fixture = tickets.repository()
        self.assertEqual(grading.digest(grading.encode(fixture)),
                         "334f1e622b1319d8ab881300f4ef057a91c76571d22e6a512c0b9a7deeeef032")
        ticket = next(t for t in tickets.TICKETS if t.key == "unique")
        command = [sys.executable, "-I", "-B", "-c", grading.GRADE]
        self.assertFalse(grading.grade(ticket, fixture["unique.py"], command))
        candidate = ("def solve(items):\n    seen = {}\n    for item in items:\n"
                     "        seen.setdefault(item.casefold(), item)\n    return list(seen.values())\n")
        self.assertTrue(grading.grade(ticket, candidate, command))

    def test_gateway_checks_selected_model_and_rejects_remote_weights(self):
        from bench.native_headless import gateway
        for details in ({}, {"remote_model": "remote"}):
            with self.subTest(details=details), patch.object(gateway, "backend") as backend:
                tags = {"models": [{"name": "other", "size": 1, "digest": "wrong"},
                                   {"name": "chosen", "size": 1, "digest": "expected"}]}
                response = io.BytesIO(json.dumps(details).encode())
                response.status = 200
                conn = Mock()
                backend.side_effect = [(conn, io.BytesIO(json.dumps(tags).encode())), (conn, response)]
                if details:
                    with self.assertRaisesRegex(ValueError, "local weights"):
                        gateway.model_digest("chosen")
                else:
                    self.assertEqual(gateway.model_digest("chosen"), "expected")
                self.assertEqual(json.loads(backend.call_args.args[1]), {"model": "chosen"})

    def test_missing_arm_is_usage_error_before_reading_schedule(self):
        args = ["qualify.py", "--kernel", ".", "--factory", "absent", "--out", "absent",
                "--ticket", "windows"]
        for supplied, missing in [([], "--model"), (["--model", "chosen"], "--provider"),
                                  (["--provider", "openai"], "--model")]:
            with self.subTest(supplied=supplied), patch.object(sys, "argv", args + supplied):
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    with self.assertRaises(SystemExit) as failure:
                        qualify.main()
                self.assertEqual(failure.exception.code, 2)
                self.assertIn(missing, err.getvalue())

    def test_identity_requires_consistent_trial_evidence(self):
        events = trace("observed", "local_eval")
        arm = qualify.observed_arm(events)
        self.assertEqual((arm.driver, arm.model, arm.provider),
                         ("codex-exec", "observed", "local_eval"))
        for invalid in (events[:1], events[1:], events + trace("other", "local_eval"),
                        events + trace("observed", "openai"), trace("", "local_eval"),
                        trace("observed", None)):
            with self.subTest(events=invalid), self.assertRaises(ValueError):
                qualify.observed_arm(invalid)

    def test_render_groups_same_driver_by_observed_model_and_provider(self):
        from bench.native_headless.report import render
        rows = [dict(driver="codex-exec", model=m, provider=p, ticket="t", repeat=1,
                     tests_pass=True, scoped=True, accepted=a)
                for m, p, a in (("a", "local_eval", False), ("a", "openai", True),
                                ("b", "openai", False))]
        report = render(rows)
        for row in rows:
            label = " / ".join(row[k] for k in ("driver", "model", "provider"))
            self.assertIn(label, report.splitlines()[0])
            self.assertIn(f"| {label} | 1/1 | 1/1 | {int(row['accepted'])}/1 |", report)
        with self.assertRaises(ValueError):
            render(rows + rows[:1])

    def test_shard_dispatch_and_output_use_explicit_arm_despite_stale_schedule(self):
        from contextlib import ExitStack
        def job(path, remaining):
            data = json.loads(path.read_bytes())
            requested = data["arm"]
            self.assertEqual(requested, {"driver": "codex-exec", "model": "chosen", "provider": "openai"})
            repo = Path(data["repo"])
            (repo / "windows.py").write_bytes(b"pass")
            events = trace("different" if mismatch else "chosen", "openai")
            events[1]["payload"].update(approval_policy="on-request", approvals_reviewer="auto_review",
                                        sandbox_policy={"type": "workspace-write"})
            events.append({"type": "event_msg", "payload": {"type": "token_count",
                           "info": {"total_token_usage": {"total_tokens": 2}}}})
            path.with_suffix(".jsonl").write_bytes(b"\n".join(json.dumps(e).encode() for e in events))
            return {"ok": True, "error": None}

        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
                root = Path(temp)
                schedule = root / "evals/harness/rerun-03/run.json"
                schedule.parent.mkdir(parents=True)
                prompt_hash = "3ec1248b2b6e396ffc2af85aaf81ff84aed3f5ba06a8ffa727ae362f72de61f0"
                fixture_hash = "334f1e622b1319d8ab881300f4ef057a91c76571d22e6a512c0b9a7deeeef032"
                slots = [dict(profile="codex-exec", repeat=i, ticket="windows", prompt_sha256=prompt_hash)
                         for i in (1, 2, 3)]
                prior = dict(model="stale", model_digest="old", planned=36, baseline_sha256=fixture_hash,
                             timeout_s=180, image="image", cli_versions={"codex": "cli"},
                             rows=slots + [dict(profile="another")] * 33)
                schedule.write_bytes(json.dumps(prior).encode())
                out = root / "out"
                argv = ["qualify.py", "--factory", temp, "--kernel", temp, "--out", str(out),
                        "--ticket", "windows", "--model", "chosen", "--provider", "openai"]
                stack.enter_context(patch.object(sys, "argv", argv))
                stack.enter_context(patch.object(qualify, "grade", return_value=True))
                stack.enter_context(patch.dict(os.environ, EVAL_MODEL="wrong-env"))
                stack.enter_context(patch.object(qualify, "call", side_effect=lambda *a, **kw:
                                                 "cli" if "--version" in a else "image"))
                stack.enter_context(patch.object(qualify.tempfile, "mkdtemp", return_value=temp))
                stack.enter_context(patch.object(qualify, "run_job", side_effect=job))
                stack.enter_context(patch.object(qualify, "traces_for", side_effect=lambda seat, repo, start:
                                                 [repo.parent / "job.jsonl"]))
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                if mismatch:
                    with self.assertRaisesRegex(ValueError, "observed.*different"):
                        qualify.main()
                    self.assertFalse((out / "run.json").exists())
                else:
                    qualify.main()
                    rows = json.loads((out / "run.json").read_bytes())["rows"]
                    self.assertEqual(len(rows), 3)
                    self.assertTrue(all(r["accepted"] and (r["driver"], r["model"], r["provider"]) ==
                                        ("codex-exec", "chosen", "openai") for r in rows))
                    self.assertEqual(output.getvalue().count("codex-exec / chosen / openai"), 3)

    def test_committed_report_renders_from_receipt_rows(self):
        from bench.native_headless.report import render
        folder = Path(qualify.__file__).parent
        rows = json.loads((folder / "receipts.json").read_bytes())["rows"]
        self.assertEqual(render(rows), (folder / "RESULTS.md").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 18)
