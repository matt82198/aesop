#!/usr/bin/env node
// Behavioral test. The DENY cases are real transcript-mining command shapes; the ALLOW
// cases are the control-file reads that must stay open.
import { verdictFor } from './no-scrollback-as-record.mjs';

const DENY = 'deny', ALLOW = 'allow';
const cases = [
  // ---- must DENY: the real commands from the incident, on a Windows-style home ----
  [DENY, 'Bash', 'cd C:/Users/dev/.claude/projects/C--Users-dev-myproject && grep -ohE "SELECT[^\\"]{0,180}funnel_events" *.jsonl', null, 'cd projects + grep *.jsonl'],
  [DENY, 'Bash', 'grep -lE "funnel_events|report_view" C:/Users/dev/.claude/projects/C--Users-dev-myproject/*.jsonl', null, 'grep transcripts by path'],
  [DENY, 'Bash', 'ls -la C:/Users/dev/.claude/projects/C--Users-dev-myproject/3348b35a.jsonl', null, 'direct transcript path'],
  [DENY, 'Bash', 'python -c "open(r\'C:/Users/dev/.claude/projects/X/a.jsonl\').read()"', null, 'python reading a transcript'],
  [DENY, 'Bash', 'tail -n 200 ~/.claude/projects/proj/sess.jsonl', null, 'tail a transcript'],
  [DENY, 'Read', '', ['C:/Users/dev/.claude/projects/C--Users-dev-myproject/3348b35a.jsonl'], 'Read tool on a transcript'],
  [DENY, 'Grep', '', ['C:/Users/dev/.claude/projects/C--Users-dev-myproject'], 'Grep tool at the projects dir with jsonl'],

  // ---- must DENY: the same shapes on a POSIX-style home (adopters are mostly not on Windows) ----
  [DENY, 'Bash', 'grep -lE "funnel_events" /home/dev/.claude/projects/-home-dev-myproject/*.jsonl', null, 'POSIX home: grep transcripts by path'],
  [DENY, 'Bash', 'cat /home/dev/.claude/projects/-home-dev-myproject/3348b35a.jsonl', null, 'POSIX home: cat a transcript'],
  [DENY, 'Read', '', ['/home/dev/.claude/projects/-home-dev-myproject/3348b35a.jsonl'], 'POSIX home: Read tool on a transcript'],

  // ---- must ALLOW: the control files that ARE the record ----
  [ALLOW, 'Read', '', ['C:/Users/dev/conductor/STATE.md'], 'STATE.md'],
  [ALLOW, 'Read', '', ['C:/Users/dev/conductor/BUILDLOG.md'], 'BUILDLOG.md'],
  [ALLOW, 'Read', '', ['C:/Users/dev/.claude/projects/C--Users-dev-myproject/memory/MEMORY.md'], 'MEMORY.md under projects/ but not a transcript'],
  [ALLOW, 'Read', '', ['/home/dev/.claude/projects/-home-dev-myproject/memory/MEMORY.md'], 'POSIX home: MEMORY.md under projects/ but not a transcript'],
  [ALLOW, 'Bash', 'git log --oneline -10', null, 'git log'],
  [ALLOW, 'Bash', 'ls ~/.claude/projects/', null, 'listing the dir without reading jsonl'],
  [ALLOW, 'Bash', 'python tools/some_gate.py --check', null, 'the toolkit'],
  [ALLOW, 'Bash', 'cat /app/data/pages/x/facts.json', null, 'a real artifact'],
  [ALLOW, 'Bash', 'grep -n "def team_label" engine/standings.py', null, 'source grep'],
];

let pass = 0, fail = 0;
for (const [want, toolName, command, paths, label] of cases) {
  const got = verdictFor({ toolName, command, paths }) ? DENY : ALLOW;
  if (got === want) pass++;
  else { fail++; console.log(`FAIL [${label}] want=${want} got=${got}\n  ${JSON.stringify(command || paths).slice(0, 120)}`); }
}
console.log(`\n${pass} passed, ${fail} failed, ${cases.length} total`);
process.exit(fail ? 1 : 0);
