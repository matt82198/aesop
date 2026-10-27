"""Child process that holds one file handle open for the teardown-race tests.

Usage:
    python tests/_teardown_holder.py <path> timed [<seconds>]
    python tests/_teardown_holder.py <path> until-eof

  timed      open <path>, report readiness, sleep <seconds> (default 0.5),
             close the handle and exit. Models a git child that releases its
             handle on its own shortly after the parent starts teardown.
  until-eof  open <path>, report readiness, block until stdin reaches EOF,
             close the handle and exit. Models a handle that is never released
             while the parent's teardown runs.

The line "held" on stdout is the readiness signal the parent waits for after
the handle is open. Exit status is 0 on a clean release, 2 on bad arguments.
"""

import sys
import time

MODES = ("timed", "until-eof")
DEFAULT_SECONDS = 0.5


def main(argv):
    if len(argv) < 3 or argv[2] not in MODES:
        sys.stderr.write("usage: _teardown_holder.py <path> {timed|until-eof} [seconds]\n")
        return 2
    path, mode = argv[1], argv[2]
    seconds = float(argv[3]) if len(argv) > 3 else DEFAULT_SECONDS
    handle = open(path, "w", encoding="utf-8")
    try:
        sys.stdout.write("held\n")
        sys.stdout.flush()
        if mode == "timed":
            time.sleep(seconds)
        else:
            sys.stdin.read()
    finally:
        handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
