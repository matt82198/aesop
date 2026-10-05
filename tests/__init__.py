"""
Test package initializer -- the single shared entry point for every way the Python
test suite can be run: `pytest tests/`, `pytest tests/test_foo.py`, `python -m
unittest discover -s tests`, and tools/ci_shard_runner.py's own in-process module
import AND its `python -m pytest ...` subprocess mode (which re-imports this package
in the child process). Python's import system guarantees `tests/__init__.py` runs
before ANY `tests.test_*` submodule is imported, so this is the one place that is
reached no matter which of the above entry points a developer or CI uses.

Incident (2026-10-05): tests/test_merge_train_halt_enforcement.py and
tests/test_merge_queue_halt_enforcement.py ran tools/merge_train.py /
tools/merge_queue.py as REAL subprocesses with `env = os.environ.copy()` (no
isolation at all) once halt was cleared, on the assumption the real `gh`/`git`
calls would harmlessly fail with no auth/network. On a box with live `gh` auth,
they did not: six `integrate/batch-20261005-15xx` branches were pushed to the
real origin and a worktree was repurposed.

Per-test mocking cannot be the only guard against this -- a test author can
always forget it, exactly as happened here. Applying the isolation here, as a
package-level side effect, makes the whole Python test PROCESS structurally
incapable of reaching the real origin or GitHub API, regardless of what any
individual test does or forgets. See tools/test_network_isolation.py for the
mechanism and tests/test_test_network_isolation.py for the behavioral proof.
"""
import sys
from pathlib import Path

_TOOLS_DIR = str(Path(__file__).resolve().parent.parent / "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from test_network_isolation import apply_test_isolation_env  # noqa: E402

apply_test_isolation_env()
