"""Shared sys.modules snapshot helper for test suites.

Tests that delete and re-import a module (e.g. `del sys.modules["ui"]`) must put the
original entries back, otherwise a sibling module that bound the old object at import
time keeps a stale binding for the rest of the process. Usage inside a TestCase method
or setUp, placed BEFORE the first deletion:

    self.addCleanup(snapshot_sys_modules("ui.serve", "ui"))

The snapshot is taken immediately; the returned callable restores each named entry to
its original object, or drops it if it was absent, when the test's cleanups run.
"""
import sys


def snapshot_sys_modules(*names):
    """Snapshot the named sys.modules entries now; return a zero-arg restore callable."""
    saved = {name: sys.modules.get(name) for name in names}

    def restore():
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    return restore
