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


if __name__ == "__main__":
    unittest.main()
