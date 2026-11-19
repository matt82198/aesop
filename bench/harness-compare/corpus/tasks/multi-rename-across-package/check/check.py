"""Hidden check for multi-rename-across-package. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import os
import sys

WORKDIR = sys.argv[1]
sys.path.insert(0, WORKDIR)

failures = []

for name in sorted(os.listdir(os.path.join(WORKDIR, "pkg"))):
    if not name.endswith(".py"):
        continue
    with open(os.path.join(WORKDIR, "pkg", name), encoding="utf-8") as fh:
        if "fetch_record" in fh.read():
            failures.append("pkg/%s still contains fetch_record" % name)

try:
    import pkg
    import pkg.api
    import pkg.store
except ImportError as exc:
    print("import failed: %s" % exc)
    sys.exit(1)

if not hasattr(pkg, "get_record"):
    failures.append("pkg.get_record is missing")
if hasattr(pkg, "fetch_record"):
    failures.append("pkg.fetch_record still exists")
if not hasattr(pkg.store, "get_record"):
    failures.append("pkg.store.get_record is missing")

pkg.store.save_record("k", 42)
if getattr(pkg, "get_record", lambda k: None)("k") != 42:
    failures.append("get_record('k') did not return 42")
if pkg.api.handle_get("k") != {"status": 200, "body": 42}:
    failures.append("handle_get('k') wrong: %r" % (pkg.api.handle_get("k"),))
if pkg.api.handle_get("missing") != {"status": 404, "body": None}:
    failures.append("handle_get('missing') wrong")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
