import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from retry import retry  # noqa: E402


class RetryTests(unittest.TestCase):
    def test_backoff_then_success(self):
        calls = {"n": 0}
        sleeps = []

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("boom")
            return "ok"

        self.assertEqual(retry(flaky, attempts=3, base_delay=0.1, sleep=sleeps.append), "ok")
        self.assertEqual(sleeps, [0.1, 0.2])

    def test_reraises_last_error(self):
        def always():
            raise KeyError("nope")

        with self.assertRaises(KeyError):
            retry(always, attempts=2, sleep=lambda _d: None)


if __name__ == "__main__":
    unittest.main()
