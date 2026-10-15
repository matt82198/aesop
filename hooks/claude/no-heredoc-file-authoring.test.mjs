#!/usr/bin/env node
// Behavioral test for no-heredoc-file-authoring.mjs.
// Each case is a real command shape from the incident that motivated this gate.
import { verdictFor } from './no-heredoc-file-authoring.mjs';

const DENY = 'deny', ALLOW = 'allow';
const cases = [
  // ---- must DENY: the exact shape that failed repeatedly ----
  [DENY, "cat > /tmp/zombie.html <<'HTMLEOF'\n<title>x</title>\nHTMLEOF", 'cat > file heredoc'],
  [DENY, "cat >> notes.md <<'EOF'\nline\nEOF", 'cat >> file heredoc'],
  [DENY, "cat <<'EOF' > /tmp/out.txt\nbody\nEOF", 'redirect after heredoc'],
  [DENY, "tee /tmp/a.json <<'J'\n{}\nJ", 'tee file heredoc'],
  [DENY, "mkdir -p /tmp/d && cat > /tmp/d/f.py <<'PYEOF'\nx=1\nPYEOF\necho done", 'heredoc + trailing cmd'],

  // ---- must DENY: interpreter heredocs too (the exception that kept failing) ----
  [DENY, "python - \"$W\" <<'PY'\nimport sys\nPY", 'python stdin heredoc'],
  [DENY, "node <<'JS'\nconsole.log(1)\nJS", 'node stdin heredoc'],
  [DENY, "sqlite3 db.sqlite <<'SQL'\nSELECT 1;\nSQL", 'sqlite heredoc'],
  [DENY, "printf '%s\\n' 'import os' | ssh host 'python3 -'\ncat <<'X'\ny\nX", 'heredoc anywhere in the command'],

  // ---- must ALLOW: `<<` that is not a heredoc ----
  [ALLOW, "python -c \"print(1 << 3)\"", 'bit shift, not a heredoc'],
  [ALLOW, "awk '{ x = 1 << 2; print x }' f.txt", 'bit shift in awk'],

  // ---- must ALLOW: ordinary shell work ----
  [ALLOW, "printf '%s\\n' \"- [X](y.md) - hook\" >> MEMORY.md", 'single-line printf append'],
  [ALLOW, "echo hi > /tmp/f", 'echo redirect, no heredoc'],
  [ALLOW, "ssh host -C \"python -c \\\"import sqlite3\\\"\"", 'ssh with inline -c'],
  [ALLOW, "grep -n 'def foo' web/leagues.py | head -5", 'grep'],
  [ALLOW, "git diff origin/main..HEAD -- tests/", 'git'],
  [ALLOW, "sed -n '1,40p' file.py", 'sed read'],
  [ALLOW, "cat file.txt", 'plain cat'],
  [ALLOW, "cat a.txt > b.txt", 'cat redirect without heredoc'],
];

let pass = 0, fail = 0;
for (const [want, cmd, label] of cases) {
  const got = verdictFor(cmd) ? DENY : ALLOW;
  if (got === want) { pass++; }
  else { fail++; console.log(`FAIL [${label}] want=${want} got=${got}\n  cmd: ${JSON.stringify(cmd).slice(0,110)}`); }
}
console.log(`\n${pass} passed, ${fail} failed, ${cases.length} total`);
process.exit(fail ? 1 : 0);
