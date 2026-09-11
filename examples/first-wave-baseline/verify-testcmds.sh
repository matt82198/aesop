#!/usr/bin/env bash
# verify-testcmds.sh -- prove the replay kit's testCmds are fail-closed.
#
# Builds a throwaway fixture repo matching the file layout the manifest assumes,
# then runs every item's testCmd VERBATIM (read from wave-manifest.json) twice:
#
#   PRE-WORK  : the work is deliberately NOT done  -> every testCmd must exit non-zero
#   POST-WORK : the work is applied                -> every testCmd must exit zero
#
# A testCmd that passes PRE-WORK is fail-open: it would bless an item the worker
# never actually did. This script exists so that claim is executed, not asserted.
#
# Requires: bash, python3, node 18+, npm, and network access (installs eslint@8).
# Usage:    bash examples/first-wave-baseline/verify-testcmds.sh
# Exit:     0 = all testCmds fail-closed; 1 = at least one gate is fail-open or broken.

set -u

HERE=$(cd "$(dirname "$0")" && pwd)
MANIFEST="$HERE/wave-manifest.json"
FIXTURE="${TMPDIR:-/tmp}/aesop-replay-kit-fixture.$$"
FAILURES=0

cleanup() { rm -rf "$FIXTURE"; }
trap cleanup EXIT

py() { python3 "$@" 2>/dev/null || python "$@"; }

slugs() {
  py -c "import json,sys;print(' '.join(i['slug'] for i in json.load(open(sys.argv[1],encoding='utf-8'))['items']))" "$MANIFEST"
}

testcmd_for() {
  py -c "import json,sys;print(next(i['testCmd'] for i in json.load(open(sys.argv[1],encoding='utf-8'))['items'] if i['slug']==sys.argv[2]))" "$MANIFEST" "$1"
}

build_fixture() {
  rm -rf "$FIXTURE"
  mkdir -p "$FIXTURE/tests" "$FIXTURE/docs" "$FIXTURE/src/utils"
  cd "$FIXTURE" || exit 1

  # item 1 pre-state: the typo is present
  printf '# Demo Repo\nThis project uses agent orchestraion to do things.\n' > README.md

  # item 2 pre-state: the test is skipped
  cat > tests/test_example.js <<'EOF'
const { it } = require('node:test');
const assert = require('node:assert');
it.skip('adds numbers', () => { assert.strictEqual(1 + 1, 2); });
EOF

  # item 4 pre-state: two links point at files that do not exist
  printf '# Architecture\nSee [setup](SETUP.md) and [missing](NOPE.md) and [ext](https://example.com).\n' > docs/ARCHITECTURE.md
  printf '# Setup\nBack to [architecture](ARCHITECTURE.md), also [gone](../src/nothere.js).\n' > docs/SETUP.md

  # item 5 pre-state: duplicated helpers, no "Refactor goal:" marker
  cat > src/utils/helpers.js <<'EOF'
function addOne(x) { return x + 1; }
function addTwo(x) { return x + 2; }
module.exports = { addOne, addTwo };
EOF
  cat > src/utils/helpers.test.js <<'EOF'
const { test } = require('node:test');
const assert = require('node:assert');
const { addOne, addTwo } = require('./helpers.js');
test('addOne', () => { assert.strictEqual(addOne(1), 2); });
test('addTwo', () => { assert.strictEqual(addTwo(1), 3); });
EOF

  # item 3 pre-state: no .eslintrc.json, no lint script. Note "eslint" DOES appear
  # in devDependencies -- that is exactly what a naive `grep -q lint package.json`
  # would have matched, which is why this gate now runs the linter for real.
  cat > package.json <<'EOF'
{
  "name": "aesop-replay-kit-fixture",
  "version": "1.0.0",
  "scripts": { "test": "node --test" },
  "devDependencies": { "eslint": "^8.0.0" }
}
EOF

  npm install --no-audit --no-fund --silent eslint@8 >/dev/null 2>&1 || {
    echo "FATAL: could not install eslint@8 (network required)"; exit 1;
  }
}

apply_work() {
  cd "$FIXTURE" || exit 1

  # item 1: fix the typo
  printf '# Demo Repo\nThis project uses agent orchestration to do things.\n' > README.md

  # item 2: drop the .skip
  cat > tests/test_example.js <<'EOF'
const { it } = require('node:test');
const assert = require('node:assert');
// Re-enabled: the arithmetic bug this guarded against is fixed.
it('adds numbers', () => { assert.strictEqual(1 + 1, 2); });
EOF

  # item 3: real eslint config + a real lint script
  cat > .eslintrc.json <<'EOF'
{
  "root": true,
  "env": { "node": true, "es2022": true },
  "parserOptions": { "ecmaVersion": 2022, "sourceType": "script" },
  "extends": "eslint:recommended"
}
EOF
  py -c "
import json,io,sys
p=sys.argv[1]
d=json.load(io.open(p,encoding='utf-8'))
d['scripts']['lint']='eslint src/ tests/'
io.open(p,'w',encoding='utf-8',newline='\n').write(json.dumps(d,indent=2)+'\n')
" "$FIXTURE/package.json"

  # item 4: repoint both broken links at files that exist
  printf '# Architecture\nSee [setup](SETUP.md) and [helpers](../src/utils/helpers.js) and [ext](https://example.com).\n' > docs/ARCHITECTURE.md
  printf '# Setup\nBack to [architecture](ARCHITECTURE.md), also [readme](../README.md).\n' > docs/SETUP.md

  # item 5: real refactor + the required marker
  cat > src/utils/helpers.js <<'EOF'
// Refactor goal: collapse addOne/addTwo into one parameterised adder so new
// offsets need no new function.
const addBy = (n) => (x) => x + n;
const addOne = addBy(1);
const addTwo = addBy(2);
module.exports = { addBy, addOne, addTwo };
EOF
}

run_phase() {
  phase="$1"; want="$2"   # want=fail | want=pass
  echo "########## $phase ##########"
  cd "$FIXTURE" || exit 1
  for s in $(slugs); do
    cmd=$(testcmd_for "$s")
    bash -c "$cmd" >/dev/null 2>&1
    rc=$?
    if [ "$want" = "fail" ]; then
      if [ "$rc" -ne 0 ]; then verdict="OK   (fails as required)"; else verdict="FAIL-OPEN (passed before the work was done)"; FAILURES=$((FAILURES+1)); fi
    else
      if [ "$rc" -eq 0 ]; then verdict="OK   (passes as required)"; else verdict="BROKEN (failed after the work was done)"; FAILURES=$((FAILURES+1)); fi
    fi
    printf '  %-26s exit=%-3s %s\n' "$s" "$rc" "$verdict"
  done
  echo
}

build_fixture
run_phase "PRE-WORK: nothing implemented -- every gate must FAIL" fail
apply_work
run_phase "POST-WORK: all 5 items implemented -- every gate must PASS" pass

if [ "$FAILURES" -eq 0 ]; then
  echo "RESULT: all 5 testCmds are fail-closed."
  exit 0
fi
echo "RESULT: $FAILURES gate(s) are not fail-closed."
exit 1
