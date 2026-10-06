// Harness-level HOME isolation for the Node test suite.
//
// Loaded via `node --import ./tests/helpers/isolated-env.mjs --test ...` (see
// package.json's test/test:node scripts and .github/workflows/ci.yml's windows-shard
// "Run Node.js tests" step). `node --test` spawns one child process per test FILE, and
// each child re-applies the same CLI flags (confirmed empirically: a preload module
// logs a distinct pid per test file) -- so this module runs once per test-file process,
// BEFORE that file's own top-level code, and every child process THAT FILE spawns
// (e.g. bin/cli.js via spawnSync) inherits the overridden env. One implementation,
// zero per-test opt-in required.
//
// Why this exists (incident, 2026-10-05): running the Node suite on a developer box
// let installSkills() in bin/cli.js write aesop's scaffold skill templates over the
// REAL ~/.claude/skills/{power,dashboard,buildsystem}/SKILL.md (4 files, restored from
// git), because a test spawned the CLI without --no-skills / AESOP_SKILLS_HOME. Relying
// on every test author to remember a flag is not a gate -- this module makes the whole
// test process structurally incapable of reaching the real profile, regardless of what
// any individual test does or forgets. tools/test_isolation_tripwire.py is the
// belt-and-suspenders check that proves it (hashes the real profile before/after).
//
// Escape hatch: a test that legitimately needs the REAL home sets AESOP_TEST_REAL_HOME=1
// in its OWN env before spawning the thing that needs it, with a comment at the call site
// explaining why. As of 2026-10, no test in this repo needs it -- audited when this module
// was added (grepped every tests/*.mjs for os.homedir()/process.env.HOME/USERPROFILE; the
// only hits compare a CLI child's generated config against the *same* (now-fake) homedir
// the test process itself resolves, which is a self-consistent assertion either way).

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

if (process.env.AESOP_TEST_REAL_HOME !== '1') {
  const isolatedHome = fs.mkdtempSync(path.join(os.tmpdir(), 'aesop-test-home-'));
  const appData = path.join(isolatedHome, 'AppData', 'Roaming');
  const localAppData = path.join(isolatedHome, 'AppData', 'Local');
  fs.mkdirSync(appData, { recursive: true });
  fs.mkdirSync(localAppData, { recursive: true });

  // POSIX + Git-Bash resolution
  process.env.HOME = isolatedHome;
  // Windows os.homedir()/many CLIs
  process.env.USERPROFILE = isolatedHome;
  process.env.APPDATA = appData;
  process.env.LOCALAPPDATA = localAppData;
  // bin/cli.js's installSkills() target + a general "brain root" override for anything
  // that reads it (defense in depth alongside the harness isolation above).
  process.env.AESOP_SKILLS_HOME = path.join(isolatedHome, '.claude', 'skills');
  process.env.AESOP_HOME = path.join(isolatedHome, '.claude');
  // Belt-and-suspenders signal to in-process code (bin/cli.js installSkills guard) that
  // this is a test harness run, independent of NODE_ENV (which some tooling also sets for
  // unrelated reasons).
  process.env.AESOP_TEST_HARNESS = '1';

  // Git isolation (PR #784, CI-only hang): overriding HOME/USERPROFILE above stops git
  // from finding a *user*-level .gitconfig, but a CI runner image's *global* config path
  // (GIT_CONFIG_GLOBAL, when already set in the runner's own env) or a hooksPath/signing
  // hook defined there can still be picked up by any git invocation a spawned CLI makes
  // (e.g. bin/cli.js's initializeGitRepo), and on Windows a hung grandchild (credential UI,
  // pinentry, hook script) can hold a spawnSync child's stdio pipes open well past its own
  // `timeout` option -- observed as a full node --test file-level 180s timeout with zero
  // subtest output on CI's windows-shard(0). Point GIT_CONFIG_GLOBAL at an empty file
  // inside the isolated home (so no runner-image global config applies) and disable
  // terminal prompts so git fails fast instead of blocking with no TTY attached.
  const isolatedGitConfig = path.join(isolatedHome, 'gitconfig');
  fs.writeFileSync(isolatedGitConfig, '');
  process.env.GIT_CONFIG_GLOBAL = isolatedGitConfig;
  process.env.GIT_TERMINAL_PROMPT = '0';

  const cleanup = () => {
    try {
      fs.rmSync(isolatedHome, { recursive: true, force: true, maxRetries: 3 });
    } catch {
      // Best-effort; CI runners are ephemeral and a leftover temp dir on a dev box is
      // harmless (it is never the real profile).
    }
  };
  process.once('exit', cleanup);
}
