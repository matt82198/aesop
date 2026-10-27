#!/usr/bin/env python3
"""
TDD tests for incident_report.py parser and classifier.

Tests verify:
- Pattern recognition for incident classes (fake-green, ci-drift, test-pollution, flake, conflict, etc.)
- Deterministic git commit parsing
- Entry ordering and idempotency
- Markdown generation and --check mode
"""

import unittest


class TestIncidentClassifier(unittest.TestCase):
    """Test incident pattern classification."""

    def test_classify_fake_green_from_commit_subject(self):
        """ci(browser-proofs): never-ran or green-never-ran pattern."""
        from tools.incident_report import IncidentClassifier

        classifier = IncidentClassifier()

        # Pattern: commit subject contains "green-never-ran" or "never-ran"
        result = classifier.classify_commit_subject(
            "ci(browser-proofs): actually execute playwright specs + minimal dashboard smoke (#464)"
        )
        self.assertEqual(result, "fake-green",
            "Should classify 'actually execute' + 'browser-proofs never ran' as fake-green")

    def test_classify_ci_drift_from_subject(self):
        """CI workflow file drift (missing dependencies, env setup)."""
        from tools.incident_report import IncidentClassifier

        classifier = IncidentClassifier()

        # Pattern: "fix(ci):" or "fix(workflow):"
        result = classifier.classify_commit_subject(
            "fix(ci): add pytest to main-full workflow (post-#450 drift)"
        )
        self.assertIn(result, ["ci-drift", "test-pollution"],
            "Should classify missing workflow dependency as ci-drift")

    def test_classify_test_pollution_from_subject(self):
        """Test pollution (config leaks, mock state, isolation)."""
        from tools.incident_report import IncidentClassifier

        classifier = IncidentClassifier()

        # Pattern: "test-pollution", "shard", "leak", "isolat"
        result = classifier.classify_commit_subject(
            "fix(tests): stop test_ui_wave_context leaking MockConfig into sys.modules (shard isolation)"
        )
        self.assertEqual(result, "test-pollution",
            "Should classify MockConfig leak as test-pollution")

    def test_classify_flake_from_subject(self):
        """Flaky test (timing, race, deflake)."""
        from tools.incident_report import IncidentClassifier

        classifier = IncidentClassifier()

        # Pattern: "deflake" or "flake"
        result = classifier.classify_commit_subject(
            "fix: deflake watchdog boundary tests with logical time (#432)"
        )
        self.assertEqual(result, "flake",
            "Should classify deflake as flake")

    def test_classify_conflict_from_subject(self):
        """Merge conflict or module shadowing."""
        from tools.incident_report import IncidentClassifier

        classifier = IncidentClassifier()

        # Pattern: "conflict", "shadowing", "module"
        result = classifier.classify_commit_subject(
            "fix(ws3): restore original wave_scheduler spec + add lane_scheduler pilot"
        )
        self.assertIn(result, ["conflict", "test-pollution"],
            "Should classify module shadowing as conflict")

    def test_classify_gate_activation_from_subject(self):
        """Secret gate or verification bypass caught."""
        from tools.incident_report import IncidentClassifier

        classifier = IncidentClassifier()

        # Pattern: "gate", "secret", "--no-verify", "--admin"
        result = classifier.classify_commit_subject(
            "fix(docs): remove invented precision from Gates That Fired paragraph"
        )
        self.assertIn(result, ["gate-activation", "doc-invented"],
            "Should classify gate activation as gate-activation")


class TestIncidentParser(unittest.TestCase):
    """Test parsing incidents from git history."""

    def test_parse_commit_hash(self):
        """Extract commit hash from git log output."""
        from tools.incident_report import IncidentParser

        parser = IncidentParser()

        # Test that parser can extract what_happened from subject
        subject = "ci(browser-proofs): actually execute playwright specs (#464)"
        what_happened = parser._extract_what_happened(subject, "")

        self.assertIsNotNone(what_happened)
        self.assertIn('actually execute', what_happened.lower())

    def test_incident_from_synthetic_commits(self):
        """Extract incident metadata from synthetic test commits (hermetic).

        Creates a temporary git repo with 4 commits shaped like real incidents:
        - fake-green: browser-proofs tests never ran
        - ci-drift: pytest missing from workflow
        - flake: deflake timing test
        - gate-activation: secret gate caught bypass

        Verifies the parser correctly classifies each type.
        """
        from tools.incident_report import IncidentParser
        import subprocess
        import os

        # Create isolated temp directory for test repo (hygiene rule: no cwd pollution)
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a test repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir,
                capture_output=True,
                check=True
            )

            # Set git identity to avoid pollution
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                capture_output=True,
                check=True
            )

            # Create synthetic commits matching incident patterns
            commits = [
                {
                    "file": "test.txt",
                    "subject": "ci(browser-proofs): actually execute playwright specs",
                    "body": "Fixes the long-standing issue where browser-proofs never ran any specs",
                    "expected_class": "fake-green"
                },
                {
                    "file": "test.txt",
                    "subject": "fix(ci): add pytest to main-full workflow (post-#450 drift)",
                    "body": "The main-full.yml workflow was missing pytest",
                    "expected_class": "ci-drift"
                },
                {
                    "file": "test.txt",
                    "subject": "fix: deflake watchdog boundary tests with logical time",
                    "body": "Root cause: timing race condition in boundary tests",
                    "expected_class": "flake"
                },
                {
                    "file": "test.txt",
                    "subject": "fix: secret-scan gate closes worktree bypasses",
                    "body": "Pre-push gate now detects file changes in commits",
                    "expected_class": "gate-activation"
                },
            ]

            # Create commits in test repo
            for i, commit in enumerate(commits):
                # Write file
                filepath = os.path.join(tmpdir, commit["file"])
                with open(filepath, 'w') as f:
                    f.write(f"Content {i}\n")

                # Stage
                subprocess.run(
                    ["git", "add", commit["file"]],
                    cwd=tmpdir,
                    capture_output=True,
                    check=True
                )

                # Commit with message
                subprocess.run(
                    ["git", "commit", "-m", f"{commit['subject']}\n\n{commit['body']}"],
                    cwd=tmpdir,
                    capture_output=True,
                    check=True
                )

            # Parse incidents from the test repo
            parser = IncidentParser(repo_root=tmpdir)
            incidents = parser.find_all_incidents()

            # Verify we found 4 incidents
            self.assertEqual(len(incidents), 4,
                "Should find exactly 4 synthetic incidents in test repo")

            # Verify each incident is classified correctly.
            # Order-insensitive: synthetic commits created in rapid succession can
            # share identical timestamps (fast CI runners), making date-desc order
            # unstable between same-second commits. The contract under test is the
            # classification set, not the tiebreak order.
            expected_classes = [c["expected_class"] for c in commits]
            actual_classes = [i["class"] for i in incidents]

            self.assertCountEqual(actual_classes, expected_classes,
                f"Incident classes mismatch: {actual_classes} != {expected_classes}")

    def test_incident_from_real_history_skip_shallow(self):
        """Optional test for real git history (skips in shallow clones).

        Only runs when full history is available. This test validates that
        the parser works against real incident patterns in actual repo history.
        """
        from tools.incident_report import IncidentParser
        import subprocess

        # Check if this is a shallow clone
        result = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=".",
            capture_output=True,
            text=True,
            check=False
        )

        is_shallow = result.stdout.strip() == "true"
        if is_shallow:
            self.skipTest("Shallow clone (fetch-depth 1 in CI) — skipping real-history test; hermetic test validates classification logic")

        # Full history available: validate against real commits
        parser = IncidentParser(repo_root=".")
        incidents = parser.find_all_incidents(limit=10)

        # Should find at least some incidents in full history
        self.assertGreater(len(incidents), 0,
            "Should find incidents in full repository history")

    def test_deterministic_ordering(self):
        """Entries should be stable-ordered (date, then hash)."""
        from tools.incident_report import IncidentParser

        parser = IncidentParser(repo_root=".")

        incidents1 = parser.find_all_incidents()
        incidents2 = parser.find_all_incidents()

        # Same order both times
        hashes1 = [i['hash'] for i in incidents1]
        hashes2 = [i['hash'] for i in incidents2]

        self.assertEqual(hashes1, hashes2,
            "Incident order should be deterministic across runs")

    def test_multiline_body_keeps_real_dates_and_stable_order(self):
        """Multi-line bodies and '|' in bodies must not lose commit dates.

        Regression: git log records were split on newlines and '|', so the date
        field was dropped and replaced by datetime.now(); the sort key then changed
        on every run. Hermetic: builds a throwaway repo with known commit dates.
        """
        from tools.incident_report import IncidentParser
        import os
        import subprocess
        import tempfile

        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        })
        message = "fix: stall check hung process\n\nfirst body line | with a pipe\nsecond line\nthird line"
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True, env=env)
            for i in range(6):
                with open(os.path.join(tmpdir, f"f{i}.txt"), "w", encoding="utf-8") as fh:
                    fh.write(f"{i}\n")
                commit_env = dict(env, GIT_COMMITTER_DATE=f"2026-03-0{i + 1}T12:00:00+00:00")
                subprocess.run(["git", "add", "-A"], cwd=tmpdir, check=True, env=commit_env)
                subprocess.run(["git", "commit", "-q", "-m", message], cwd=tmpdir, check=True,
                               env=commit_env, encoding="utf-8", errors="replace")

            listing = subprocess.run(
                ["git", "log", "--format=%h %cI", "--all"],
                cwd=tmpdir, capture_output=True, text=True, encoding="utf-8", errors="replace",
                check=True, env=env,
            ).stdout
            real_dates = dict(line.split(" ", 1) for line in listing.splitlines() if line.strip())

            parser = IncidentParser(repo_root=tmpdir)
            first = parser.find_all_incidents()
            second = parser.find_all_incidents()

            self.assertEqual([i["hash"] for i in first], [i["hash"] for i in second],
                "Incident order must not change between runs on identical history")
            self.assertEqual(len(first), 6, "Every commit should yield one incident")
            for incident in first:
                self.assertIn(incident["hash"], real_dates,
                    f"Incident {incident['hash']!r} is not a real commit (record split)")
                self.assertEqual(incident["date"], real_dates[incident["hash"]],
                    "Commit date must come from git, not a fallback clock value")


class TestIncidentMarkdownGeneration(unittest.TestCase):
    """Test Markdown output format."""

    def test_generate_markdown_table(self):
        """Generate deterministic Markdown table."""
        from tools.incident_report import IncidentMarkdown

        markdown = IncidentMarkdown()

        incidents = [
            {
                'hash': '7e20522',
                'class': 'fake-green',
                'what_happened': 'Browser-proofs CI job never ran any Playwright specs',
                'resolution': 'Added Playwright TypeScript test infrastructure',
                'source_ref': 'PR #464'
            },
            {
                'hash': '0e03440',
                'class': 'ci-drift',
                'what_happened': 'pytest missing from main-full workflow',
                'resolution': 'Added pytest to workflow Python dependencies',
                'source_ref': 'PR #461'
            }
        ]

        output = markdown.generate_table(incidents)

        # Should contain table structure
        self.assertIn('| Class |', output)
        self.assertIn('fake-green', output)
        self.assertIn('ci-drift', output)
        self.assertIn('PR #464', output)
        self.assertIn('PR #461', output)

    def test_markdown_deterministic(self):
        """Markdown output should be byte-identical across runs."""
        from tools.incident_report import IncidentMarkdown

        markdown = IncidentMarkdown()

        incidents = [
            {
                'hash': 'abc123',
                'class': 'flake',
                'what_happened': 'Test timeout',
                'resolution': 'Added logical time mock',
                'source_ref': 'PR #432'
            }
        ]

        output1 = markdown.generate_table(incidents)
        output2 = markdown.generate_table(incidents)

        self.assertEqual(output1, output2,
            "Markdown output should be byte-identical")


class TestCheckMode(unittest.TestCase):
    """Test --check mode for CI drift detection."""

    def _fixture_with_log(self, tmpdir, log_text):
        """Write docs/INCIDENTS.md under tmpdir (the repo must already exist)."""
        import os
        from tools.incident_report import write_table
        os.makedirs(os.path.join(tmpdir, "docs"), exist_ok=True)
        write_table(os.path.join(tmpdir, "docs", "INCIDENTS.md"), log_text)

    def test_check_mode_valid_file(self):
        """--check exits 0 when docs/INCIDENTS.md equals the generated output."""
        from tools.incident_report import IncidentChecker, IncidentMarkdown, IncidentParser
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix: stall check hung process", "")])
            incidents, errors = IncidentParser(repo_root=tmpdir).parse()
            self.assertEqual(errors, [])
            self._fixture_with_log(tmpdir, IncidentMarkdown().generate_table(incidents))
            ok, message = IncidentChecker(repo_root=tmpdir).check_incidents_file("docs/INCIDENTS.md")
            cli = _run_incident_cli(["--check", "--repo", tmpdir], cwd=tmpdir)
        self.assertTrue(ok, message)
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)

    def test_check_mode_drift_detected(self):
        """--check exits 1 with a DRIFT message when docs/INCIDENTS.md is stale."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix: stall check hung process", "")])
            self._fixture_with_log(tmpdir, "# Incidents\n\n*(No incidents recorded)*\n")
            cli = _run_incident_cli(["--check", "--repo", tmpdir], cwd=tmpdir)
        self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
        self.assertIn("DRIFT", cli.stdout + cli.stderr)


class TestSecretScanning(unittest.TestCase):
    """Test that no credentials leak in output."""

    def test_no_secrets_in_commit_hashes(self):
        """Commit hashes should never contain sensitive patterns."""
        from tools.incident_report import IncidentValidator

        validator = IncidentValidator()

        # Commit hashes are 40 hex chars (SHA-1) or 7-12 short hash
        test_hash = "7e20522"

        self.assertTrue(validator.is_valid_git_hash(test_hash),
            "Should accept valid short commit hash")

    def test_no_secrets_in_pr_refs(self):
        """PR references should be #NNN format only."""
        from tools.incident_report import IncidentValidator

        validator = IncidentValidator()

        self.assertTrue(validator.is_valid_source_ref("PR #464"),
            "Should accept valid PR reference")

        self.assertTrue(validator.is_valid_source_ref("commit 7e20522"),
            "Should accept commit reference")


class TestIncidentPortabilityAndEol(unittest.TestCase):
    """Generated log must pass the portability gate and be EOL-deterministic."""

    def _table_with(self, what_happened, resolution):
        from tools.incident_report import IncidentMarkdown
        incidents = [{
            "hash": "abc1234", "full_hash": "abc1234" + "0" * 33,
            "class": "gate-activation", "what_happened": what_happened,
            "resolution": resolution, "source_ref": "PR #856",
            "date": "2026-10-14T00:00:00+00:00",
        }]
        return IncidentMarkdown().generate_table(incidents)

    def test_generated_table_redacts_private_machine_tokens(self):
        """Whole-word private tokens from old commit subjects must not reach docs/INCIDENTS.md."""
        table = self._table_with(
            "guard: isolate shell tests from real conductor3 state",
            "matt8 box resolution text")
        self.assertNotIn("conductor3", table)
        self.assertNotIn("matt8 ", table)
        self.assertIn("[redacted]", table)

    def test_redaction_keeps_longer_identifiers(self):
        """Redaction is whole-word only: matt82198 (GitHub user) stays intact."""
        table = self._table_with("merge from matt82198/feat/x", "ok resolution text")
        self.assertIn("matt82198", table)

    def test_write_table_emits_lf_only(self):
        """The writer must not depend on the host EOL (Windows text mode writes CRLF)."""
        import os
        import tempfile
        from tools.incident_report import write_table
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "INCIDENTS.md")
            write_table(path, "# Incidents\n\nrow\n")
            with open(path, "rb") as fh:
                data = fh.read()
        self.assertEqual(data, b"# Incidents\n\nrow\n")
class _FixtureRepo:
    """Hermetic throwaway git repo: one commit per (subject, body) pair, fixed dates."""

    @staticmethod
    def build(tmpdir, commits):
        import os
        import subprocess
        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        })
        subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True, env=env)
        subprocess.run(["git", "-C", tmpdir, "config", "commit.gpgsign", "false"], cwd=tmpdir, check=True, env=env)
        for i, (subject, body) in enumerate(commits):
            with open(os.path.join(tmpdir, f"f{i}.txt"), "w", encoding="utf-8") as fh:
                fh.write(f"{i}\n")
            day = f"2026-03-{i + 1:02d}T12:00:00+00:00"
            commit_env = dict(env, GIT_AUTHOR_DATE=day, GIT_COMMITTER_DATE=day)
            message = f"{subject}\n\n{body}" if body else subject
            subprocess.run(["git", "add", "-A"], cwd=tmpdir, check=True, env=commit_env)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", message], cwd=tmpdir,
                           check=True, env=commit_env, encoding="utf-8", errors="replace")

    @staticmethod
    def short_head(tmpdir):
        import subprocess
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=tmpdir, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", check=True).stdout.strip()


def _run_incident_cli(args, cwd, extra_env=None):
    """Run tools/incident_report.py as a real subprocess and return the CompletedProcess."""
    import os
    import subprocess
    import sys
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "incident_report.py")
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, tool] + list(args), cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env)


class TestTrailerChannel(unittest.TestCase):
    """Incident-Class trailer is authoritative; unknown values fail closed."""

    def test_trailer_class_is_authoritative_for_new_classes(self):
        """A commit carrying Incident-Class: hook-fail-open classifies as hook-fail-open."""
        from tools.incident_report import IncidentParser
        import tempfile
        commits = [
            ("fix: hooks exit 0 on malformed stdin", "Incident-Class: hook-fail-open\nIncident-Category: C brain hooks"),
            ("fix: daemon heartbeat path", "Incident-Class: daemon-liveness\nIncident-Category: B daemons/fleet"),
            ("fix: state not pulled", "Incident-Class: sync-drift\nIncident-Category: D sync/state"),
            ("fix: lane claims relayed unverified", "Incident-Class: conduct\nIncident-Category: E orchestrator conduct"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, commits)
            incidents = IncidentParser(repo_root=tmpdir).find_all_incidents()
        self.assertCountEqual([i["class"] for i in incidents],
                              ["hook-fail-open", "daemon-liveness", "sync-drift", "conduct"])
        for incident in incidents:
            self.assertFalse(incident.get("inferred", False), "trailer rows are explicit, not inferred")

    def test_prose_wrapped_trailer_text_is_not_a_trailer(self):
        """A prose line wrapping onto 'Incident-Class: x' in an earlier paragraph is ignored; the final trailer block wins."""
        from tools.incident_report import IncidentParser
        import tempfile
        body = ("Causal chain notes.\n"
                "Incident-Class: environment-shape, which was not one of the 12 documented\n"
                "classes, so the gate went red.\n"
                "\n"
                "Incident-Class: gate-activation")
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix(incident): taxonomy", body)])
            incidents, errors = IncidentParser(repo_root=tmpdir).parse()
        self.assertEqual(errors, [], f"prose text must not produce trailer errors: {errors}")
        self.assertEqual([i["class"] for i in incidents], ["gate-activation"])

    def test_trailer_overrides_subject_keyword(self):
        """Trailer wins over a subject keyword that would have matched a different class."""
        from tools.incident_report import IncidentParser
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix(ci): add pytest to main-full workflow (post-#450 drift)",
                                         "Incident-Class: daemon-liveness")])
            incidents = IncidentParser(repo_root=tmpdir).find_all_incidents()
        self.assertEqual([i["class"] for i in incidents], ["daemon-liveness"])

    def test_unknown_trailer_value_fails_closed_in_parser(self):
        """An unknown Incident-Class value is reported as an error naming the commit, never dropped."""
        from tools.incident_report import IncidentParser
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix: something happened", "Incident-Class: hook-fial-open")])
            short = _FixtureRepo.short_head(tmpdir)
            _, errors = IncidentParser(repo_root=tmpdir).parse()
        self.assertTrue(errors, "unknown trailer value must produce an error")
        self.assertTrue(any(short in e and "hook-fial-open" in e for e in errors),
                        f"error must name the commit {short}: {errors}")

    def test_unknown_trailer_value_makes_regenerate_exit_nonzero(self):
        """CLI --regenerate exits non-zero, names the commit, and writes nothing."""
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [
                ("fix: stall check hung process", ""),
                ("fix: something happened", "Incident-Class: hook-fial-open"),
            ])
            short = _FixtureRepo.short_head(tmpdir)
            result = _run_incident_cli(["--repo", tmpdir, "--regenerate", "--output", "out/INCIDENTS.md"], tmpdir)
            self.assertNotEqual(result.returncode, 0, f"fail-closed must exit non-zero: {result.stdout}{result.stderr}")
            self.assertIn(short, result.stdout + result.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "out", "INCIDENTS.md")),
                             "a fail-closed run must not write the incident log")

    def test_unknown_category_fails_closed(self):
        """An Incident-Category outside A..E is an error, not silently accepted."""
        from tools.incident_report import IncidentParser
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix: stall check hung process",
                                         "Incident-Class: stall\nIncident-Category: Z nonsense")])
            _, errors = IncidentParser(repo_root=tmpdir).parse()
        self.assertTrue(any("Z nonsense" in e for e in errors), f"bad category must be an error: {errors}")

    def test_check_mode_fails_closed_naming_commit(self):
        """--check reports the unknown trailer and the commit, not a generic drift message."""
        from tools.incident_report import IncidentChecker
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix: something happened", "Incident-Class: not-a-class")])
            short = _FixtureRepo.short_head(tmpdir)
            ok, message = IncidentChecker(repo_root=tmpdir).check_incidents_file("docs/INCIDENTS.md")
        self.assertFalse(ok)
        self.assertIn(short, message or "")


class TestFallbackClassification(unittest.TestCase):
    """Without a trailer: subject-only keyword fallback, marked inferred, merges excluded."""

    def test_body_keywords_do_not_classify(self):
        """'feat: port dash-extra.mjs' with gate/deadlock words in the body is NOT an incident."""
        from tools.incident_report import IncidentParser
        import tempfile
        body = "Adds the secret-scan gate wiring. Notes: stall check, deadlock, --no-verify mentioned."
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("feat: port dash-extra.mjs and secret_scan.py for complete public template", body)])
            incidents = IncidentParser(repo_root=tmpdir).find_all_incidents()
        self.assertEqual(incidents, [], f"body keywords must not classify a commit: {incidents}")

    def test_subject_fallback_is_marked_inferred(self):
        """Subject keyword match without trailer classifies, and the row is flagged inferred."""
        from tools.incident_report import IncidentParser
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix: stall check hung process", "")])
            incidents = IncidentParser(repo_root=tmpdir).find_all_incidents()
        self.assertEqual([i["class"] for i in incidents], ["stall"])
        self.assertTrue(incidents[0].get("inferred"), "fallback rows must be flagged inferred")

    def test_merge_commit_subject_is_not_an_incident(self):
        """A merge whose branch name contains a keyword is not an incident by itself."""
        from tools.incident_report import IncidentParser
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("Merge pull request #100 from x/feat/wave12-stall-check", "")])
            incidents = IncidentParser(repo_root=tmpdir).find_all_incidents()
        self.assertEqual(incidents, [])

    def test_manifest_only_subject_is_not_an_incident(self):
        """aesop #1315: a manifest-only commit whose subject merely names a secret-gate test is not an incident."""
        from tools.incident_report import IncidentParser
        import tempfile
        subject = "chore(loop-manifest): backup-fleet.sh lists its secret-gate test as a proof test"
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [(subject, "")])
            incidents = IncidentParser(repo_root=tmpdir).find_all_incidents()
        self.assertEqual(incidents, [],
                         f"a manifest-only subject must not infer an incident: {incidents}")

    def test_real_gate_activation_subject_is_inferred(self):
        """A subject describing a real gate firing still infers gate-activation, flagged inferred."""
        from tools.incident_report import IncidentParser
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix(ci): secret-scan gate blocked a push with a leaked token", "")])
            incidents = IncidentParser(repo_root=tmpdir).find_all_incidents()
        self.assertEqual([i["class"] for i in incidents], ["gate-activation"])
        self.assertTrue(incidents[0].get("inferred"), "subject-inferred rows must be flagged inferred")

    def test_explicit_trailer_always_wins(self):
        """Incident-Class: gate-activation makes the commit an explicit incident regardless of its subject."""
        from tools.incident_report import IncidentParser
        import tempfile
        subject = "chore(loop-manifest): backup-fleet.sh lists its secret-gate test as a proof test"
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [(subject, "Incident-Class: gate-activation")])
            incidents = IncidentParser(repo_root=tmpdir).find_all_incidents()
        self.assertEqual([i["class"] for i in incidents], ["gate-activation"])
        self.assertFalse(incidents[0].get("inferred", False), "trailer rows are explicit, not inferred")

    def test_generated_table_marks_inferred_rows(self):
        """Fallback rows read 'class (inferred)'; trailer rows read the bare class."""
        from tools.incident_report import IncidentMarkdown
        output = IncidentMarkdown().generate_table([
            {"hash": "aaaaaaa", "class": "stall", "what_happened": "x", "resolution": "y",
             "source_ref": "commit aaaaaaa", "inferred": True},
            {"hash": "bbbbbbb", "class": "conduct", "what_happened": "x", "resolution": "y",
             "source_ref": "commit bbbbbbb", "inferred": False},
        ])
        self.assertIn("| stall (inferred) |", output)
        self.assertIn("| conduct |", output)


class TestCliCrashes(unittest.TestCase):
    """Regression: absolute --output crashed; cp1252 stdout crashed on U+FEFF."""

    def test_regenerate_accepts_absolute_output_path(self):
        """--regenerate --output <absolute path> writes the file and exits 0."""
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix: stall check hung process", "")])
            out = os.path.join(tmpdir, "out", "INCIDENTS.md")
            result = _run_incident_cli(["--repo", tmpdir, "--regenerate", "--output", out], tmpdir)
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            self.assertTrue(os.path.exists(out))
            with open(out, encoding="utf-8") as fh:
                self.assertIn("stall", fh.read())

    def test_print_mode_survives_cp1252_stdout(self):
        """Default print mode with a cp1252 stdout encoding must not crash on U+FEFF in a subject."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix: stall check ﻿hung process", "")])
            result = _run_incident_cli(["--repo", tmpdir], tmpdir, extra_env={"PYTHONIOENCODING": "cp1252"})
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            self.assertIn("stall", result.stdout)


class TestTaxonomyEnvironmentShapeAndAlias(unittest.TestCase):
    """environment-shape is a documented class; test-isolation is an alias of test-pollution;
    the --check scan covers HEAD's history only, so unrelated branches cannot fail the gate."""

    def test_environment_shape_is_documented_and_renders(self):
        """A commit tagged environment-shape parses cleanly and renders its own row."""
        from tools.incident_report import IncidentParser, IncidentMarkdown
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix: 8.3 path resolves wrong on runner", "Incident-Class: environment-shape")])
            incidents, errors = IncidentParser(repo_root=tmpdir).parse()
        self.assertEqual(errors, [], f"environment-shape must be a valid class: {errors}")
        self.assertEqual([i["class"] for i in incidents], ["environment-shape"])
        self.assertFalse(incidents[0]["inferred"])
        table = IncidentMarkdown().generate_table(incidents)
        self.assertIn("| environment-shape | ", table)
        self.assertIn("host or runner shape", table)

    def test_test_isolation_alias_normalizes_to_test_pollution(self):
        """Incident-Class: test-isolation is reported under test-pollution, never as its own row."""
        from tools.incident_report import IncidentParser, IncidentMarkdown
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [("fix: shard leak into state root", "Incident-Class: test-isolation")])
            incidents, errors = IncidentParser(repo_root=tmpdir).parse()
        self.assertEqual(errors, [], f"test-isolation alias must be accepted: {errors}")
        self.assertEqual([i["class"] for i in incidents], ["test-pollution"])
        self.assertFalse(incidents[0]["inferred"])
        table = IncidentMarkdown().generate_table(incidents)
        self.assertIn("| test-pollution | ", table)
        self.assertNotIn("test-isolation", table)

    def _repo_with_unrelated_bad_branch(self, tmpdir):
        """HEAD carries one valid commit; a side branch carries a commit with an unknown class."""
        import subprocess
        _FixtureRepo.build(tmpdir, [("fix: valid flake deflake", "Incident-Class: flake")])
        subprocess.run(["git", "checkout", "-q", "-b", "unrelated-bad"], cwd=tmpdir, check=True)
        _FixtureRepo.build(tmpdir, [("fix: side branch work", "Incident-Class: not-a-class")])
        bad_short = _FixtureRepo.short_head(tmpdir)
        subprocess.run(["git", "checkout", "-q", "-"], cwd=tmpdir, check=True)
        return bad_short

    def test_unrelated_branch_does_not_affect_check_for_head(self):
        """A bad trailer on an unrelated local branch must not fail --check for HEAD."""
        import os
        import tempfile
        from tools.incident_report import IncidentChecker, IncidentParser, IncidentMarkdown, write_table
        with tempfile.TemporaryDirectory() as tmpdir:
            self._repo_with_unrelated_bad_branch(tmpdir)
            incidents, errors = IncidentParser(repo_root=tmpdir).parse()
            self.assertEqual(errors, [], f"HEAD scope must not see the side branch: {errors}")
            os.makedirs(os.path.join(tmpdir, "docs"), exist_ok=True)
            write_table(os.path.join(tmpdir, "docs", "INCIDENTS.md"), IncidentMarkdown().generate_table(incidents))
            ok, message = IncidentChecker(repo_root=tmpdir).check_incidents_file("docs/INCIDENTS.md")
            cli = _run_incident_cli(["--check", "--repo", tmpdir], cwd=tmpdir)
        self.assertTrue(ok, message)
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)

    def test_all_flag_scans_unrelated_branches_for_forensics(self):
        """--all restores the whole-repo scan, so the side-branch commit is reported by name."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_short = self._repo_with_unrelated_bad_branch(tmpdir)
            cli = _run_incident_cli(["--check", "--all", "--repo", tmpdir], cwd=tmpdir)
        self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
        self.assertIn(bad_short, cli.stdout + cli.stderr)


class TestIncidentOrdering(unittest.TestCase):
    """parse() documents newest-first ordering with a hash tiebreak; find_all_incidents()
    must return exactly that order (it is a limit-only wrapper, never a re-sort)."""

    def test_find_all_incidents_matches_documented_newest_first_order(self):
        """Rows are sorted by (commit date desc, short hash asc) and find_all_incidents agrees."""
        import tempfile
        from datetime import datetime
        from tools.incident_report import IncidentParser
        with tempfile.TemporaryDirectory() as tmpdir:
            _FixtureRepo.build(tmpdir, [
                ("fix: stall check hung process", "Incident-Class: stall"),
                ("fix: flaky deflake test", "Incident-Class: flake"),
                ("fix: shard leak into state root", "Incident-Class: test-pollution"),
            ])
            parser = IncidentParser(repo_root=tmpdir)
            incidents, errors = parser.parse()
            wrapped = parser.find_all_incidents()
        self.assertEqual(errors, [])
        self.assertEqual(len(incidents), 3)
        self.assertEqual([i["hash"] for i in wrapped], [i["hash"] for i in incidents])
        keys = [(-datetime.fromisoformat(i["date"]).timestamp(), i["hash"]) for i in incidents]
        self.assertEqual(keys, sorted(keys), "rows must be newest first, then hash ascending")


if __name__ == '__main__':
    unittest.main()
