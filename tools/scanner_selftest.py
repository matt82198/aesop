#!/usr/bin/env python3
"""
Regression harness for secret_scan.py — validates scanner behavior across TP/FP cases.
INDEX: Regression harness for secret_scan.py

Usage: python scanner_selftest.py [--temp-dir DIR]

Runs test cases against the scanner and reports pass/fail. Uses system temp by default.

# secretscan: allow-pattern-docs
"""
import os
import sys
import subprocess
import tempfile
import shutil
from pathlib import Path


def run_test(name, file_content, filename, expected_exit, expected_keyword):
    """Generate a test file, run scanner, verify exit code and output."""
    # Create temp directory for this test
    test_dir = Path(test_temp_dir) / name
    test_dir.mkdir(parents=True, exist_ok=True)

    test_file = test_dir / filename
    test_file.write_text(file_content)

    # Run secret_scan.py
    try:
        result = subprocess.run(
            [sys.executable, scanner_path, str(test_file)],
            capture_output=True,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=10
        )
    except subprocess.TimeoutExpired:
        # Retry once after 30s as per instructions
        import time
        time.sleep(30)
        try:
            result = subprocess.run(
                [sys.executable, scanner_path, str(test_file)],
                capture_output=True,
                text=True,
                encoding='utf-8', errors='replace',
                timeout=10
            )
        except Exception as e:
            return False, f"TIMEOUT/ERROR: {e}"
    except Exception as e:
        return False, f"ERROR: {e}"

    # Check exit code
    if result.returncode != expected_exit:
        return False, f"Exit {result.returncode} (expected {expected_exit}). Output: {result.stdout} {result.stderr}"

    # Check for expected keyword in output
    output = result.stdout + result.stderr
    if expected_keyword and expected_keyword not in output:
        return False, f"Keyword '{expected_keyword}' not in output: {output}"

    return True, "OK"


def main():
    global test_temp_dir, scanner_path

    # Paths
    scripts_dir = Path(__file__).parent
    scanner_path = scripts_dir / "secret_scan.py"

    if not scanner_path.exists():
        print(f"ERROR: {scanner_path} not found")
        return 1

    # Create temp directory in system temp
    with tempfile.TemporaryDirectory(prefix="scanner_selftest_") as tmpdir:
        test_temp_dir = Path(tmpdir)

        # Test cases: (name, file_content, filename, expected_exit, expected_keyword_in_output)
        cases = [
            (
                "tp_ghp_token",
                f"{'# GitHub token:'}\\ntoken = '{'ghp_' + 'a' * 32}'\\n",
                "test.py",
                1,
                "token"
            ),
            (
                "tp_akia_key",
                f"{'# AWS key:'}\\naccess_key = '{'AKIA' + 'A' * 16}'\\n",
                "config.py",
                1,
                "key"
            ),
            (
                "tp_password_literal",
                f"password = '{'hunter2secret99'}'\\n",
                "app.py",
                1,
                "password"
            ),
            (
                "tp_env_secret",
                f"{'SECRET_KEY=realvalue123456'}\\n",
                ".env",
                1,
                "FOUND"
            ),
            (
                "tp_credentials_filename",
                "some data here\n",
                ".credentials.json",
                1,
                "FOUND"
            ),
            (
                "fp_java_orderTokens",
                "for (String oToken : orderTokens) {\\n    process(oToken);\\n}\\n",
                "OrderProcessor.java",
                0,
                ""
            ),
            (
                "fp_python_fstring",
                "result = f\"txn:{inv.txn_id}\"\\n",
                "invoice.py",
                0,
                ""
            ),
            (
                "fp_clean_file",
                "#!/usr/bin/env python3\\ndef hello():\\n    return 'world'\\n",
                "clean.py",
                0,
                "CLEAN"
            ),
            (
                # Pragma is scoped to doc-shaped rules ONLY: a GitHub token is a
                # fatal class and must block even with the pragma present.
                "tp_pragma_github_token_still_fatal",
                f"# secretscan: allow-pattern-docs\\ntoken = '{'ghp_' + 'b' * 32}'\\n",
                "docs.py",
                1,
                "HIGH"
            ),
            (
                # Fatal class: PEM private key + pragma must still exit 1.
                # Header fragment-assembled so this source never holds the shape.
                "tp_pragma_pem_still_fatal",
                "# secretscan: allow-pattern-docs\\n"
                + "-----BEGIN RSA " + "PRIVATE" + " KEY-----" + "\\n"
                + "MIIEdummy1234567890abcdef\\n"
                + "-----END RSA " + "PRIVATE" + " KEY-----" + "\\n",
                "keydoc.txt",
                1,
                "pem_private_key"
            ),
            (
                # Doc-shaped rule: generic assignment IS softened by pragma.
                "tp_pragma_generic_softened",
                "# secretscan: allow-pattern-docs\n"
                + "password = \"this_is_a_doc_sample_value_12345\"\n",
                "pattern_docs.py",
                0,
                "ALLOWED-DOC"
            ),
            (
                # Doc-shaped rule: env access IS softened by pragma.
                "tp_pragma_env_access_softened",
                "# secretscan: allow-pattern-docs\\n"
                + "v = " + "os.envi" + "ron['API_" + "TOKEN']" + "\\n",
                "env_docs.py",
                0,
                "ALLOWED-DOC"
            ),
            (
                "fp_java_baseurl_constant",
                'private static final String BASE_URL = "https://api.example.com";\\n',
                "Config.java",
                0,
                "CLEAN"
            ),
            (
                "fp_python_qbo_version",
                "QBO_MINOR_VERSION = 65\\n",
                "version.py",
                0,
                "CLEAN"
            ),
            (
                "fp_python_recv_batchsize",
                "RECV_BATCH_SIZE = 200\\n",
                "processor.py",
                0,
                "CLEAN"
            ),
            (
                "fp_env_amount_limit",
                "AMOUNT_LIMIT=99999999\\n",
                ".env",
                0,
                "CLEAN"
            ),
            (
                "tp_env_secret_key_envfile",
                "SECRET_KEY=realvalue123456\\n",
                ".env",
                1,
                "FOUND"
            ),
            (
                "fp_connection_localhost_fixture",
                "# Test fixture: localhost DB\\ndb_url = 'postgresql://user:testpass@localhost:5432/testdb'\\n",
                "test_db.py",
                0,
                "CLEAN"
            ),
            (
                "fp_connection_127_fixture",
                "# Test fixture: loopback address\\ndb_url = 'mysql://admin:changeme@127.0.0.1:3306/test'\\n",
                "test_config.py",
                0,
                "CLEAN"
            ),
            (
                "fp_connection_example_fixture",
                "# Example URL from docs\\nurl = 'postgres://user:password@example.com:5432/db'\\n",
                "docs_example.py",
                0,
                "CLEAN"
            ),
            (
                # Fixture fragment-assembled so this selftest source never holds a
                # contiguous connection-string shape (fatal class, pragma-immune).
                "tp_connection_real_credentials",
                "# Real production DB\\ndb_url = '"
                + "postgresql:" + "//produser:RealPassword123"
                + "@db.production.io:5432/production" + "'\\n",
                "config_prod.py",
                1,
                "connection_string"
            ),
            (
                # Large-file bypass closed: >1MB file with a key in the first 1MB
                # must still block (bounded FATAL_RULES scan; key runtime-assembled).
                "tp_large_file_with_key",
                "k = '" + "AKIA" + "A" * 16 + "'\n" + "x" * (1024 * 1024 + 100) + "\n",
                "bigfile.txt",
                1,
                "aws_access_key"
            ),
            (
                # Binary bypass closed: an embedded null byte no longer exempts the
                # file; latin-1 decode + FATAL_RULES scan must still block.
                "tp_binary_file_with_key",
                "\x00binaryjunk\n" + "v = '" + "AKIA" + "A" * 16 + "'\n",
                "blob.bin",
                1,
                "aws_access_key"
            ),
            (
                # Large clean file: exit 0, with SKIPPED-LARGE note on stderr.
                "fp_large_clean_file",
                "x" * (1024 * 1024 + 100) + "\n",
                "bigclean.log",
                0,
                "SKIPPED-LARGE"
            ),
            (
                # REGRESSION: Unquoted password assignment should be detected
                # (previously required quotes to match the pattern)
                "tp_unquoted_password_assignment",
                "password = supersecretvalue123\n",
                "config.py",
                1,
                "generic_secret_assignment"
            ),
            (
                # REGRESSION: Unquoted API key assignment should be detected
                "tp_unquoted_api_key_assignment",
                "api_key = longsecretapikey1234567890\n",
                "settings.py",
                1,
                "generic_secret_assignment"
            ),
            (
                # REGRESSION: Unquoted token in .env should be detected
                "tp_unquoted_token_env_file",
                "API_TOKEN=verylongtokenvalue123456789\n",
                ".env",
                1,
                "env_assignment"
            ),
        ]

        results = []
        for name, content, filename, exp_exit, exp_kw in cases:
            passed, msg = run_test(name, content, filename, exp_exit, exp_kw)
            results.append((name, passed, msg))
            status = "PASS" if passed else "FAIL"
            print(f"{status:5} {name:30} {msg}")

        # Self-scan invariant: the scanner must scan its OWN source CLEAN with
        # zero pragma reliance (fatal-class pattern literals are runtime-assembled).
        try:
            r = subprocess.run(
                [sys.executable, str(scanner_path), str(scanner_path)],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            )
            self_ok = (r.returncode == 0 and "CLEAN" in r.stdout
                       and "ALLOWED-DOC" not in r.stdout)
            msg = "OK" if self_ok else f"Exit {r.returncode}. Output: {r.stdout} {r.stderr}"
        except Exception as e:
            self_ok, msg = False, f"ERROR: {e}"
        results.append(("self_scan_clean_no_pragma", self_ok, msg))
        print(f"{'PASS' if self_ok else 'FAIL':5} {'self_scan_clean_no_pragma':30} {msg}")

        # Summary
        passed_count = sum(1 for _, p, _ in results if p)
        total_count = len(results)
        print(f"\nSELFTEST: {passed_count}/{total_count} passed")

        # Exit 0 only if all pass
        return 0 if passed_count == total_count else 1


if __name__ == "__main__":
    sys.exit(main())
