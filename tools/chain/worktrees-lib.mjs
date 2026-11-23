import { cappedRows, guarded } from './runtime.mjs';

export const treeArgs = path => ['--no-optional-locks', '-C', path, 'worktree', 'list', '--porcelain', '-z'];
export const statusArgs = path => ['--no-optional-locks', '-C', path, 'status', '--porcelain=v2',
  '--branch', '--ahead-behind', '--untracked-files=all', '-z'];

export function entries(raw) {
  return raw.split('\0\0').filter(Boolean).map(record => {
    const fields = record.split('\0');
    if (!fields[0].startsWith('worktree ')) throw new Error('invalid worktree record');
    return { path: fields[0].slice(9), bare: fields.includes('bare') };
  });
}

function status(raw) {
  let branch, upstream = null, ahead = null, behind = null, dirty = 0;
  const fields = raw.split('\0');
  for (let i = 0; i < fields.length; i++) {
    const line = fields[i];
    if (line.startsWith('# branch.head ')) branch = line.slice(14);
    else if (line.startsWith('# branch.upstream ')) upstream = line.slice(18);
    else if (line.startsWith('# branch.ab ')) {
      const match = /^# branch\.ab \+(\d+) -(\d+)$/.exec(line);
      if (!match) throw new Error('invalid divergence');
      ahead = Number(match[1]);
      behind = Number(match[2]);
    } else if (/^[12u?] /.test(line)) {
      dirty++;
      if (line.startsWith('2 ')) i++; // Second NUL field is the original filename.
    } else if (line && !line.startsWith('# ')) throw new Error('invalid status record');
  }
  if (!branch) throw new Error('missing branch header');
  return { branch, upstream, ahead, behind, dirty };
}

export async function worktrees(c, run) {
  const trees = entries(await run('git', treeArgs(c.checkout), c));
  if (!trees.length) throw new Error('empty worktree listing');
  const all = [];
  for (let i = 0; i < trees.length; i += 8) {
    all.push(...await Promise.all(trees.slice(i, i + 8).map(tree => guarded(tree.path, async () => {
      if (tree.bare) return { bare: true };
      return status(await run('git', statusArgs(tree.path), c));
    }))));
  }
  return cappedRows(all.filter(s => !s.error), all.filter(s => s.error));
}
