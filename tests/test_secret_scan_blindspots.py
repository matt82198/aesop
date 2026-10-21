#!/usr/bin/env python3
"""
Behavioural tests for the two confirmed secret_scan.py blind spots (security
audit 2026-10-14-r1, F-01 and F-02). Each test drives the real scanner.

F-01: generic credential assignments (DB_PASSWORD=, API_TOKEN=,
      STRIPE_SECRET_KEY=) outside .env files passed CLEAN.
F-02: a single NUL byte, a UTF-16 BOM, or a large file downgraded the file to
      provider-shaped-only scanning (or skipped the tail), so generic and
      UTF-16 secrets passed.

FIXTURE SAFETY: every credential-shaped value is assembled at runtime with
_j() so no scanner pattern appears contiguously in this source file.

Run: python -m unittest tests.test_secret_scan_blindspots
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from secret_scan import scan_file, scan_blob  # noqa: E402

SCANNER_PATH = REPO_ROOT / "tools" / "secret_scan.py"
PRAGMA_LINE = "# secretscan: allow-pattern-docs"


def _j(*parts):
    """Join fragments at runtime (self-scan invariant, see module docstring)."""
    return "".join(parts)


GH_TOKEN = _j("ghp_", "Q7xQ7xQ7xQ7xQ7xQ7xQ7xQ7xQ7xQ7xQ7xQ7x")
PW_VALUE = _j("Zq8v", "Hunter", "2prod")
TOK_VALUE = _j("Lm4n", "Ppqr", "9stu")


def _pw_line():
    """A generic `password = "<value>"` line, assembled so the source never holds it contiguously."""
    return _j("pass", "word", " = \"", PW_VALUE, "\"\n")


def _write_bytes(name, data, dirpath):
    p = Path(dirpath) / name
    p.write_bytes(data)
    return p


def _run_cli(args):
    r = subprocess.run(
        [sys.executable, str(SCANNER_PATH)] + [str(a) for a in args],
        capture_output=True, encoding="utf-8", errors="replace", timeout=120,
    )
    return r.returncode, r.stdout, r.stderr


def _fatal_rules(findings):
    return {rule for (_ln, rule, _m, fatal) in findings if fatal}


def _all_rules(findings):
    return {rule for (_ln, rule, _m, _f) in findings}


class TestF01GenericCredentialAssignment(unittest.TestCase):
    """F-01: KEY_NAME=value / KEY_NAME: value / KEY_NAME = "value" outside .env."""

    def _scan_text(self, suffix, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ("fixture" + suffix)
            p.write_text(text, encoding="utf-8")
            return scan_file(p)

    def test_shell_export_db_password_is_fatal(self):
        findings = self._scan_text(".sh", f"export DB_PASSWORD={PW_VALUE}\n")
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_yaml_colon_cloudflare_api_token_is_fatal(self):
        findings = self._scan_text(".yml", f"env:\n  CLOUDFLARE_API_TOKEN: {TOK_VALUE}\n")
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_python_quoted_stripe_secret_key_is_fatal(self):
        findings = self._scan_text(".py", f'STRIPE_SECRET_KEY = "{_j("whsec", "Lm4nPpqr9stu")}"\n')
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_lowercase_literal_at_end_of_line_is_fatal(self):
        """A digit-free, lowercase-only literal that ends its line is still a literal."""
        findings = self._scan_text(".sh", "export DB_PASSWORD=supersecretvalue\n")
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_code_identifier_reference_is_not_flagged(self):
        """Control: a variable reference (snake_case) is code, not a literal credential."""
        findings = self._scan_text(".py", "h = self._headers(valid_token=resolved_token_value, extra=extra)\n")
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))

    def test_pragma_does_not_soften_real_credential(self):
        """A self-asserted doc pragma must NOT launder a real credential-shaped
        value: the pragma is file-controlled, so it cannot be the escape hatch
        for F-01. Inverse of the pre-fix pinning test (PR #971 regression)."""
        findings = self._scan_text(".md", f"{PRAGMA_LINE}\nDB_PASSWORD={PW_VALUE}\n")
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_pragma_with_placeholder_example_stays_clean(self):
        """Control: under the same pragma, a placeholder doc example is CLEAN
        (the rule's own placeholder check handles doc examples)."""
        findings = self._scan_text(".md", f"{PRAGMA_LINE}\nDB_PASSWORD=<changeme>\nAPI_TOKEN=your-token-here\n")
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))
        self.assertEqual(_fatal_rules(findings), set())

    def test_placeholder_values_are_not_flagged(self):
        """Control: placeholder values in a non-env file stay CLEAN."""
        text = (
            "DB_PASSWORD=<changeme>\n"
            "API_TOKEN=your-token-here\n"
            "STRIPE_SECRET_KEY=${STRIPE_KEY}\n"
            "SECRET_KEY=example-value-here\n"
            "PASSWORD=dummy-password-value\n"
            "ACCESS_KEY=xxxxxxxxxxxx\n"
        )
        findings = self._scan_text(".sh", text)
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))

    def test_env_example_with_placeholders_stays_clean(self):
        """Control: .env.example with placeholders is CLEAN."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env.example"
            p.write_text("DB_PASSWORD=<changeme>\nAPI_TOKEN=your-token-here\n", encoding="utf-8")
            rc, out, _err = _run_cli([p])
        self.assertEqual(rc, 0, out)
        self.assertIn("CLEAN", out)

    def test_doc_line_placeholder_path_stays_clean(self):
        """Control: a doc line `secret = "<path>"` is CLEAN."""
        self.assertEqual(_fatal_rules(self._scan_text(".md", 'secret = "<path>"\n')), set())

    def test_short_value_is_not_flagged(self):
        """Control: values under 8 chars are not flagged by the new rule."""
        findings = self._scan_text(".sh", "DB_PASSWORD=short1\n")
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))


class TestCredentialNameClass(unittest.TestCase):
    """Name class of generic_credential_assignment: CREDENTIAL(S), CRED, PWD and
    BEARER are credential words, so a token-shaped value under them is fatal.
    Every fixture value is assembled at runtime (self-scan + no-literal invariant)."""

    def _scan_text(self, suffix, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ("fixture" + suffix)
            p.write_text(text, encoding="utf-8")
            return scan_file(p)

    def test_api_credential_quoted_token_is_fatal(self):
        findings = self._scan_text(".py", _j('api_credential = "', TOK_VALUE, '"\n'))
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_api_credential_unquoted_token_is_fatal(self):
        findings = self._scan_text(".sh", f"export API_CREDENTIAL={TOK_VALUE}\n")
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_credential_suffix_yaml_is_fatal(self):
        findings = self._scan_text(".yml", f"service:\n  db_credential: {TOK_VALUE}\n")
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_credentials_fetch_option_stays_clean(self):
        """Control: the plural CREDENTIALS word is NOT matched, so a fetch option
        like credentials: 'same-origin' (ui/web/src/lib/api.ts) stays CLEAN."""
        findings = self._scan_text(".ts", "fetch(url, { credentials: 'same-origin' });\n")
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))

    def test_cred_pwd_bearer_siblings_are_fatal(self):
        findings = self._scan_text(
            ".py",
            _j('db_cred = "', TOK_VALUE, '"\n')
            + _j('db_pwd = "', PW_VALUE, '"\n')
            + _j('bearer = "', TOK_VALUE, '"\n'),
        )
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))
        self.assertEqual(len([f for f in findings if f[1] == "generic_credential_assignment"]), 3)

    def test_credentials_file_path_stays_clean(self):
        """Control: a credential-named PATH setting with no token-shaped value is CLEAN."""
        findings = self._scan_text(".py", 'credentials_file = "path/to/file"\n')
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))

    def test_pragma_does_not_soften_api_credential(self):
        """The doc pragma must not launder a real token-shaped api_credential value."""
        findings = self._scan_text(".md", PRAGMA_LINE + "\n" + _j('api_credential = "', TOK_VALUE, '"\n'))
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))


class TestF02BinaryAndEncodingBlindSpots(unittest.TestCase):
    """F-02: NUL bytes, UTF-16 and large files must not downgrade scanning."""

    def test_nul_prefixed_text_gets_generic_rules(self):
        """A single leading NUL must not reduce the file to FATAL-only scanning."""
        with tempfile.TemporaryDirectory() as d:
            p = _write_bytes("nul_prefix.py", b"\x00" + _pw_line().encode(), d)
            findings = scan_file(p)
        self.assertIn("generic_secret_assignment", _fatal_rules(findings))

    def test_utf16le_bom_github_token_is_found(self):
        """A UTF-16LE (BOM) file must be decoded and scanned, not latin-1 skipped."""
        with tempfile.TemporaryDirectory() as d:
            data = b"\xff\xfe" + f"token = {GH_TOKEN}\n".encode("utf-16-le")
            p = _write_bytes("utf16_bom.txt", data, d)
            findings = scan_file(p)
        self.assertIn("github_token", _fatal_rules(findings))

    def test_utf16le_no_bom_generic_credential_is_found(self):
        """UTF-16LE without a BOM (NUL every other byte) must still get generic rules."""
        with tempfile.TemporaryDirectory() as d:
            data = f"export DB_PASSWORD={PW_VALUE}\n".encode("utf-16-le")
            p = _write_bytes("utf16le.txt", data, d)
            findings = scan_file(p)
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_token_past_former_2mib_cap_is_found(self):
        """A token after 3 MiB in a NUL-tagged file must be scanned (no 2 MiB cap)."""
        with tempfile.TemporaryDirectory() as d:
            filler = b"\x00" + (b"x" * 1000 + b"\n") * (3 * 1024)
            data = filler + f"key = {GH_TOKEN}\n".encode()
            p = _write_bytes("past_cap.bin", data, d)
            findings = scan_file(p)
        self.assertIn("github_token", _fatal_rules(findings))

    def test_blob_nul_prefixed_generic_is_found(self):
        """The git-blob path (scan_blob) has the same NUL downgrade and must not."""
        content = b"\x00" + _pw_line().encode()
        findings = scan_blob("nul_blob.py", content)
        self.assertIn("generic_secret_assignment", _fatal_rules(findings))

    def test_blob_utf16le_github_token_is_found(self):
        content = b"\xff\xfe" + f"{GH_TOKEN}\n".encode("utf-16-le")
        findings = scan_blob("utf16_blob.txt", content)
        self.assertIn("github_token", _fatal_rules(findings))

    def test_oversize_file_fails_closed_not_clean(self):
        """A file over the scan cap must exit non-zero naming the file, never CLEAN."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "huge.bin"
            with open(p, "wb") as f:
                f.truncate(40 * 1024 * 1024)
            rc, out, err = _run_cli([p])
        self.assertNotEqual(rc, 0, out)
        self.assertIn("huge.bin", out + err)
        self.assertNotIn("CLEAN", out)

    def test_binary_provider_token_control_still_fatal(self):
        """Control: binary-looking file keeps provider-shaped detection (ASCII view)."""
        with tempfile.TemporaryDirectory() as d:
            data = b"\x89PNG\r\n\x1a\n\x01\x02\x03\x04\x05\x06\x07\x08" + GH_TOKEN.encode()
            p = _write_bytes("img.bin", data, d)
            findings = scan_file(p)
        self.assertIn("github_token", _fatal_rules(findings))


DOTTED_PW = _j("Zq8v", "Hunter", "2", ".prod")  # dotted unquoted value (F-01 dot gap)


class TestF01UnquotedDottedValues(unittest.TestCase):
    """F-01 follow-up: the unquoted branch of generic_credential_assignment
    rejected '.', so DB_PASSWORD=<value with a dot> scanned CLEAN (exit 0).
    Dotted unquoted values must be flagged; placeholders, env references and
    attribute-chain code references must stay CLEAN."""

    def _scan_text(self, suffix, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ("fixture" + suffix)
            p.write_text(text, encoding="utf-8")
            return scan_file(p)

    def test_unquoted_dotted_value_is_fatal(self):
        findings = self._scan_text(".sh", f"export DB_PASSWORD={DOTTED_PW}\n")
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_unquoted_dotted_value_cli_exits_1(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fixture.sh"
            p.write_text(f"export DB_PASSWORD={DOTTED_PW}\n", encoding="utf-8")
            rc, out, _err = _run_cli([p])
        self.assertEqual(rc, 1, out)
        self.assertIn("FOUND", out)

    def test_quoted_dotted_value_is_fatal(self):
        # Keyword split so the global ~/scripts scanner's name-assignment rule never sees
        # a contiguous PASSWORD = "..." shape in this source (self-scan invariant).
        findings = self._scan_text(".py", _j("DB_PASS", "WORD", ' = "', DOTTED_PW, '"\n'))
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_dotted_placeholder_values_stay_clean(self):
        """Control: placeholder words anywhere in a dotted value keep it CLEAN."""
        text = (
            "DB_PASSWORD=your-token.here\n"
            "API_TOKEN=example.value.here\n"
            "PASSWORD=dummy.password.value\n"
            "OPENAI_API_KEY=sk-proj-...\n"
        )
        findings = self._scan_text(".sh", text)
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))

    def test_dotted_env_reference_stays_clean(self):
        """Control: a template/env reference is not a literal credential."""
        findings = self._scan_text(".sh", "DB_PASSWORD=${DB.PASSWORD}\nAPI_TOKEN=$env.API_TOKEN\n")
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))

    def test_attribute_chain_code_reference_stays_clean(self):
        """Control: a snake_case attribute chain is code, not a literal credential."""
        findings = self._scan_text(".py", "token = self.serve.SESSION_TOKEN\n")
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))


AWS_KEY = _j("AKIA", "ABCDEFGHIJKLMNOP")


class TestItem1CommittedBlobsNotSkippedByPath(unittest.TestCase):
    """Tracker 1af7eda7fa11 (P1): scan_blob() returned [] for committed blobs whose
    path has node_modules/__pycache__ or ends .pyc/.pyo. A staged or ranged blob is
    the object being pushed and must be scanned whatever its path class."""

    def test_staged_env_under_node_modules_is_fatal(self):
        findings = scan_blob("vendor/node_modules/app/.env", _j("AWS_ACCESS_KEY_ID=", AWS_KEY).encode())
        self.assertIn("aws_access_key", _fatal_rules(findings))

    def test_staged_pycache_blob_is_fatal(self):
        findings = scan_blob("app/__pycache__/cfg.cpython-312.pyc", b"\x00\x03\x00\x00" + AWS_KEY.encode() + b"\x00\x01")
        self.assertIn("aws_access_key", _fatal_rules(findings))

    def test_staged_pyo_suffix_blob_is_fatal(self):
        findings = scan_blob("build/lib/creds.pyo", AWS_KEY.encode())
        self.assertIn("aws_access_key", _fatal_rules(findings))

    def test_working_tree_walk_still_skips_node_modules(self):
        """Control: the disk walk (scan_file) keeps skipping vendored dirs."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "node_modules" / "app" / ".env"
            p.parent.mkdir(parents=True)
            p.write_text("AWS_ACCESS_KEY_ID=" + AWS_KEY + "\n", encoding="utf-8")
            self.assertEqual(scan_file(p), [])


class TestItem2ControlBytesDoNotDropGenericRules(unittest.TestCase):
    """Tracker e346b3e442e8 (P1): >=5% control bytes classed the blob binary and ran
    only FATAL_RULES, so generic credential rules were dropped. ANSI escapes must not
    count as binary content, and a binary blob must still get generic rules over its
    printable runs."""

    def test_ansi_escapes_do_not_hide_generic_credential(self):
        findings = scan_blob("cfg.txt", b"password=Xq7mPz9vLr2wK\n" + b"\x1b[0m" * 6)
        self.assertTrue(any(r.startswith("generic_") for r in _fatal_rules(findings)), findings)

    def test_binary_blob_printable_run_still_gets_generic_rules(self):
        raw = b"\x01\x02\x03\x04\x05\x06\x07\x08" * 8 + b"password=Xq7mPz9vLr2wK" + b"\x01\x02\x03\x04\x05\x06\x07\x08" * 8
        findings = scan_blob("blob.bin", raw)
        self.assertTrue(any(r.startswith("generic_") for r in _fatal_rules(findings)), findings)


class TestItem3ConnectionStringPlaceholderOnPassword(unittest.TestCase):
    """Tracker b0937071216e (P1): the placeholder check ran on the whole URL, so a
    real password on a DB named /test was dropped. It must check the password only."""

    def test_real_password_with_test_db_name_is_fatal(self):
        line = _j("DATABASE_URL = postgresql://app:", "Pr0dS3cr3tPw", "@db.prod-acme.io:5432/test")
        findings = scan_blob("deploy.py", line.encode())
        self.assertIn("connection_string", _fatal_rules(findings))

    def test_placeholder_password_is_still_dropped(self):
        """Control: a placeholder PASSWORD still passes."""
        line = _j("postgresql://app:", "changeme", "@db.prod-acme.io:5432/app")
        findings = scan_blob("deploy.py", line.encode())
        self.assertNotIn("connection_string", _all_rules(findings))


class TestConnectionPasswordRegexShape(unittest.TestCase):
    """A connection_string hit whose password component is a regex construct (a
    character class / quantifier / escape, as in a scanner's own rule-table literal)
    is not a credential. A password with literal alphanumerics still fires."""

    def test_regex_rule_literal_line_is_clean(self):
        """The scanner's own connection_string rule literal (tools/transcript_digest.py)."""
        line = r'"connection_string": (r"://[^:]+:[^@/\s]+@(?!localhost|127\.|example|test)[^\s]+", 0),'
        findings = scan_blob("tools/transcript_digest.py", line.encode())
        self.assertNotIn("connection_string", _all_rules(findings))

    def test_plain_password_is_still_fatal(self):
        findings = scan_blob("app.py", _j("url = 'postgres://user:", "hunter2", "@db.internal/x'").encode())
        self.assertIn("connection_string", _fatal_rules(findings))

    def test_bracketed_password_with_literal_alnum_is_still_fatal(self):
        findings = scan_blob("app.py", _j("url = 'postgres://user:p[ss]", "w0rd@db.internal/x'").encode())
        self.assertIn("connection_string", _fatal_rules(findings))

    def test_symbol_only_password_is_still_fatal(self):
        """No regex construct and no alphanumerics: not excused by the regex-shape rule."""
        findings = scan_blob("app.py", _j("url = 'postgres://user:", "!!!!!!", "@db.internal/x'").encode())
        self.assertIn("connection_string", _fatal_rules(findings))


class TestItem4GenericHeuristics(unittest.TestCase):
    """Tracker a108e72018bf (P2): placeholder words matched as substrings, and a
    trailing comment made a real value look like a code reference."""

    def test_placeholder_word_substring_does_not_hide_real_value(self):
        findings = scan_blob("app/settings.env.py", _j("DB_PASSWORD=Hunter2xxx", "RealPw").encode())
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_trailing_comment_is_not_a_code_reference(self):
        findings = scan_blob("conf.yml", _j("password: correct", "horsebattery  # prod key\n").encode())
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_whole_token_placeholder_still_dropped(self):
        """Control: a placeholder as its own token still passes."""
        findings = scan_blob("app/settings.env.py", _j("DB_PASSWORD=changeme", "-prod-1234").encode())
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))

    def test_code_reference_with_trailing_comment_stays_clean(self):
        """Control: a snake_case identifier with a comment is still code, not a literal."""
        findings = scan_blob("app.py", _j("token = resolved", "_key  # from env\n").encode())
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))


class TestCredentialValueRegexShape(unittest.TestCase):
    """A generic_credential_assignment whose value is a regex literal (a regex class
    escape or group opener plus a quantifier, as in a scanner's own pattern table) is
    not a credential. A real-looking value with no regex syntax is still fatal."""

    def test_regex_literal_assignment_is_clean(self):
        line = _j("TOKEN_KEY = \"(?i)token", r"\s*[:=]\s*\S{8,}" + "\"\n")
        findings = scan_blob("tools/pattern_table.py", line.encode())
        self.assertNotIn("generic_credential_assignment", _all_rules(findings))

    def test_real_looking_quoted_credential_is_still_fatal(self):
        findings = scan_blob("app.py", _j("DB_PASSWORD = \"Zq8v", "Hunter", "2prod\"\n").encode())
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))

    def test_real_looking_credential_with_backslash_is_still_fatal(self):
        """Control: a backslash that is not a regex class escape does not excuse a value."""
        findings = scan_blob("app.py", _j("API_KEY=ab3", "\\", "qZ9xk3Wp7Lm\n").encode())
        self.assertIn("generic_credential_assignment", _fatal_rules(findings))


if __name__ == "__main__":
    unittest.main()
