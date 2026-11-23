import { cappedRows, guarded, positive, repo, result } from './runtime.mjs';

export const listFields = 'number,headRefOid,mergeStateStatus,statusCheckRollup';
export const detailFields = 'state,mergeCommit,statusCheckRollup,comments';
export const listArgs = name => ['pr', 'list', '--repo', repo(name), '--state', 'open',
  '--limit', '1001', '--json', listFields];
export const detailArgs = c => ['pr', 'view', String(positive(c.pr.number, 'PR number')),
  '--repo', repo(c.pr.repo), '--json', detailFields];
export const logArgs = c => ['run', 'view', String(positive(c.run.id, 'run id')),
  '--repo', repo(c.run.repo), '--log'];

function checks(rollup) {
  return rollup.map(check => {
    const name = check.name || check.context;
    const state = check.conclusion || check.state || check.status;
    if (!name || !state) throw new Error('unrecognized check shape');
    return [name, state];
  });
}

export async function pulls(c, run) {
  if (!Array.isArray(c.repos) || !c.repos.length) throw new Error('repos must be a nonempty array');
  const sources = await Promise.all(c.repos.map(name => guarded(name, async () => {
    const list = JSON.parse(await run('gh', listArgs(name), c));
    const rows = list.slice(0, 1000).map(pr => {
      if (!pr.headRefOid || !pr.mergeStateStatus || !pr.number) throw new Error('incomplete PR');
      const all = checks(pr.statusCheckRollup);
      return [name, pr.number, pr.headRefOid, pr.mergeStateStatus, all.length,
        all.filter(([, state]) => state !== 'SUCCESS')];
    });
    return { rows, more: list.length > rows.length };
  })));
  const errors = sources.filter(s => s.error);
  const rows = sources.flatMap(s => s.rows ?? []);
  const emptyRepos = sources.filter(s => s.rows?.length === 0).map(s => s.source);
  return cappedRows(rows, errors, sources.some(s => s.more), { emptyRepos });
}

export async function detail(c, run) {
  const pr = JSON.parse(await run('gh', detailArgs(c), c));
  if (!pr.state || !Object.hasOwn(pr, 'mergeCommit')) throw new Error('incomplete PR');
  return result({ state: pr.state, mergeCommit: pr.mergeCommit?.oid ?? null,
    checks: checks(pr.statusCheckRollup),
    comments: pr.comments.slice(-3).map(comment => comment.body.split(/\r?\n/, 1)[0]) });
}

const pass = /^(?:ok\s+\d+\b|PASS(?:ED)?\b|[✓✔]\s)|\b\d+ passed\b/i;
const failure = /##\[error\]|^not ok\b|\b(?:fail(?:ed|ure|ures)?|error|exception|fatal|panic)\b|\w+(?:Error|Exception):/i;

export async function failures(c, run) {
  const log = await run('gh', logArgs(c), c);
  const verdicts = [], hits = [], other = [];
  let count = 0;
  for (const [i, raw] of log.split(/\r?\n/).entries()) {
    const body = raw.replace(/^.*?\t.*?\t/, '').replace(/^\S+Z\s+/, '').trim();
    if (!body) continue;
    const strong = failure.test(body);
    if (!strong && pass.test(body)) continue;
    count++;
    const verdict = /^(?:not ok\b|FAIL(?:ED)?\s|##\[error\])/i.test(body);
    const bucket = verdict ? verdicts : strong ? hits : other;
    if (bucket.length < 16) bucket.push([i + 1, raw]);
  }
  const rows = [...verdicts, ...hits, ...other].slice(0, 16).sort((a, b) => a[0] - b[0]);
  const clipped = rows.some(([, line]) => line.length > 1024);
  return cappedRows(rows.map(([n, line]) => [n, line.slice(0, 1024)]), [], clipped || count > rows.length);
}
