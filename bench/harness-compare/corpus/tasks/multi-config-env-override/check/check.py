"""Hidden check for multi-config-env-override. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import os
import sys

WORKDIR = sys.argv[1]
sys.path.insert(0, WORKDIR)

failures = []

try:
    from config import defaults
    from config.loader import load_config
except ImportError as exc:
    print("import failed: %s" % exc)
    sys.exit(1)

snapshot = dict(defaults.DEFAULTS)


def expect(label, got, want):
    if got != want:
        failures.append("%s: got %r want %r" % (label, got, want))


expect("no env, no overrides", load_config(environ={}), snapshot)
expect("env int", load_config(environ={"APP_PORT": "9000"})["port"], 9000)
expect("env bool yes", load_config(environ={"APP_DEBUG": "yes"})["debug"], True)
expect("env bool off", load_config(environ={"APP_DEBUG": "OFF"})["debug"], False)
expect("env string", load_config(environ={"APP_NAME": "svc"})["name"], "svc")
expect("unknown env ignored", "unknown" in load_config(environ={"APP_UNKNOWN": "1"}), False)
expect("non-prefixed ignored", load_config(environ={"PORT": "1"})["port"], 8080)
expect(
    "overrides beat env",
    load_config(environ={"APP_PORT": "9000"}, overrides={"port": 7000})["port"],
    7000,
)

os.environ["APP_NAME"] = "fromos"
try:
    expect("default environ is os.environ", load_config()["name"], "fromos")
finally:
    del os.environ["APP_NAME"]

expect("DEFAULTS unchanged", dict(defaults.DEFAULTS), snapshot)

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
