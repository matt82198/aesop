import unittest
import tempfile
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.secret_scan import scan_file


def _j(*parts):
    """Join fragments at runtime so secret-shaped strings never appear
    contiguously in this source file (the scanned artifact)."""
    return "".join(parts)


class TestSecretScanRegexLiteral(unittest.TestCase):
    """generic_credential_assignment must not fire on a JS regex literal assigned
    to a credential-named constant (f-332fcec2), while a real credential value of
    the same assignment shape stays flagged."""

    def _findings_for(self, line):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "fleet-scan.example.mjs"
            target.write_text(line + "\n", encoding="utf-8")
            return scan_file(target)

    def _credential_findings(self, line):
        return [f for f in self._findings_for(line)
                if f[1] == "generic_credential_assignment"]

    def test_regex_literal_assigned_to_cred_name_not_flagged(self):
        """FALSE POSITIVE (red before fix): a slash-delimited regex body assigned
        to a *_CRED constant is a rule literal, not a credential."""
        regex_body = _j("tokenvalue", "123456")
        line = _j("const SUPPRESSED_", "CRED = /", regex_body, "/;")
        self.assertEqual(self._credential_findings(line), [],
                         "regex literal /.../ must not be a generic_credential_assignment")

    def test_regex_literal_with_flags_not_flagged(self):
        """The same regex literal followed by JS flags stays exempt."""
        regex_body = _j("tokenvalue", "123456")
        line = _j("SUPPRESSED_", "CRED = /", regex_body, "/gi")
        self.assertEqual(self._credential_findings(line), [])

    def test_real_credential_same_shape_still_flagged(self):
        """GUARD: an unwrapped credential value of the same assignment shape
        (SUFFIX_CRED = <value>) must still be flagged."""
        real_value = _j("Xk9pQ2", "vL7mZ")
        line = _j("SUPPRESSED_", "CRED = ", real_value)
        findings = self._credential_findings(line)
        self.assertEqual(len(findings), 1,
                         "a real credential assignment of the same shape must stay flagged")

    def test_unclosed_slash_value_still_flagged(self):
        """GUARD: a leading slash with no closing slash is not a regex literal and
        must still be flagged."""
        real_value = _j("Xk9pQ2", "vL7mZ")
        line = _j("SUPPRESSED_", "CRED = /", real_value)
        findings = self._credential_findings(line)
        self.assertEqual(len(findings), 1,
                         "an unclosed slash-prefixed credential must stay flagged")


if __name__ == "__main__":
    unittest.main()
