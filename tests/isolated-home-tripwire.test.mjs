// Tripwire: proves the Node test harness is actually launched with
// `--import ./tests/helpers/isolated-env.mjs` wired in (package.json's
// test/test:node scripts, .github/workflows/ci.yml's "Run Node.js tests"
// steps, docs/TESTING.md, docs/HOOK-INSTALL.md).
//
// GAP fixed 2026-10-06: tests/helpers/isolated-env.mjs (added by PR #831 to
// redirect HOME/USERPROFILE/AESOP_SKILLS_HOME/AESOP_HOME to a temp dir so
// tests cannot write to the real ~/.claude) existed on main but was never
// actually loaded by any Node test invocation -- no `--import` flag anywhere.
// That made it an inert gate: every Node test except the one file that now
// imports isolated-env.mjs directly ran against the REAL home directory
// (the #784/2026-10-05 incident class -- installSkills() in bin/cli.js
// overwrote the real ~/.claude/skills during a test run).
//
// This test asserts the isolation is actually ACTIVE in the current process,
// not merely that the fixture file exists and parses. Run it two ways to see
// the tripwire bite:
//   RED  (no --import):  node --test tests/isolated-home-tripwire.test.mjs
//   GREEN (wired):        node --import ./tests/helpers/isolated-env.mjs \
//                           --test tests/isolated-home-tripwire.test.mjs
// `npm run test:node` / `npm test` already run the wired (green) form.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';

test('Node test harness HOME/USERPROFILE resolve inside the OS temp dir, not the real profile', () => {
  const home = process.env.HOME;
  const userProfile = process.env.USERPROFILE;

  assert.ok(home, 'HOME must be set in the test process environment');
  assert.ok(userProfile, 'USERPROFILE must be set in the test process environment');

  const resolvedHome = path.resolve(home);
  const resolvedUserProfile = path.resolve(userProfile);
  const resolvedTmp = path.resolve(os.tmpdir());

  const insideTmp = (p) => p === resolvedTmp || p.startsWith(resolvedTmp + path.sep);

  assert.ok(
    insideTmp(resolvedHome),
    `HOME (${resolvedHome}) must resolve inside the OS temp dir (${resolvedTmp}). ` +
      'This fails when the Node test process is launched WITHOUT ' +
      '--import ./tests/helpers/isolated-env.mjs -- see tests/CLAUDE.md, ' +
      "package.json's test/test:node scripts, and .github/workflows/ci.yml's " +
      '"Run Node.js tests" steps.'
  );
  assert.ok(
    insideTmp(resolvedUserProfile),
    `USERPROFILE (${resolvedUserProfile}) must resolve inside the OS temp dir (${resolvedTmp}); ` +
      'same wiring requirement as HOME above.'
  );

  // os.homedir() is what most Node/npm code actually calls (not process.env.HOME
  // directly on every platform) -- must agree with the overridden env.
  const resolvedOsHomedir = path.resolve(os.homedir());
  assert.ok(
    insideTmp(resolvedOsHomedir),
    `os.homedir() (${resolvedOsHomedir}) must also resolve inside the OS temp dir (${resolvedTmp})`
  );

  // ~/.claude must resolve inside the isolated temp home, never the real profile.
  const claudeDir = path.resolve(path.join(home, '.claude'));
  assert.ok(
    insideTmp(claudeDir),
    `~/.claude (${claudeDir}) must resolve inside the isolated temp home (${resolvedTmp}), ` +
      'never the real developer profile.'
  );

  // Belt-and-suspenders: isolated-env.mjs sets this explicitly so in-process
  // code (e.g. bin/cli.js installSkills guard) can detect the harness.
  assert.strictEqual(
    process.env.AESOP_TEST_HARNESS,
    '1',
    'tests/helpers/isolated-env.mjs must set AESOP_TEST_HARNESS=1 -- ' +
      'if unset, isolated-env.mjs was never loaded for this process.'
  );
});
