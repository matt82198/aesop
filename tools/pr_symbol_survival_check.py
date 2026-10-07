#!/usr/bin/env python3
"""
PR symbol survival check — detects conflict-resolution symbol loss.
INDEX: Guardrail G13: PR symbol survival check — collects top-level symbols ADDED by a PR branch's
own non-merge commits (Python def/class/async def at column 0 or one indent inside a class; JS/MJS
export function|const|class and top-level function name(; shell name() {{ }}) and verifies each
still exists in the final head tree (same file, or anywhere in the repo if renamed/moved, reported
as INFO) -- catching the "took origin/main's whole file, silently dropped the PR's functions" merge-
conflict-resolution class (PR #745 incident: bisect_is_exhausted/build_bisect_batches/parse_bisect_lineage
were dropped by commit 2e0a24e2 which took origin/main's tools/merge_queue.py wholesale while
resolving a conflict, and CI stayed green because only the PR's OWN tests exercised those symbols).
CLI: `--base REF` (default origin/main) `--head REF` (default HEAD) `--original-head SHA` (optional:
diff base..original-head as a single unit instead of walking each non-merge commit) `--json`.
Exit 0=all survived (MOVED is advisory/INFO, still exit 0) / 1=symbols missing / 2=usage or git error.
Read-only: no writes, no git state mutation.

Usage:
    python tools/pr_symbol_survival_check.py [--base REF] [--head REF] [--original-head SHA] [--json]

Historical reproduction (PR #745 bisect-function drop):
    python tools/pr_symbol_survival_check.py --base 2e305b22 --head 2e0a24e2
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_UNIT_SEP = "\x1f"

PY_DEF_RE = re.compile(r'^(async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)')
JS_EXPORT_RE = re.compile(r'^export\s+(?:function|const|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)')
JS_FUNC_RE = re.compile(r'^function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(')
SH_FUNC_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{')

LANG_BY_EXT = {'.py': 'py', '.js': 'js', '.mjs': 'js', '.sh': 'sh'}


class GitError(RuntimeError):
    pass


def git(args, check=True):
    """Run a read-only git command. encoding='utf-8' per G10 (no text=True)."""
    result = subprocess.run(
        ['git'] + args,
        capture_output=True,
        encoding='utf-8',
        errors='replace',
        timeout=60,
    )
    if check and result.returncode != 0:
        raise GitError(
            "git {} failed (exit {}): {}".format(' '.join(args), result.returncode, result.stderr.strip())
        )
    return result


def rev_parse(ref):
    return git(['rev-parse', ref]).stdout.strip()


def commit_subject(sha):
    try:
        return git(['log', '-1', '--format=%s', sha]).stdout.strip()
    except GitError:
        return '(unknown subject)'


def list_commits(base, head):
    """Commits reachable from head but not base, oldest-first, with parent shas."""
    fmt = '%H{sep}%P{sep}%s'.format(sep=_UNIT_SEP)
    out = git(['log', '--topo-order', '--reverse', '--format=' + fmt, '{}..{}'.format(base, head)]).stdout
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, parents, subject = line.split(_UNIT_SEP, 2)
        parent_list = parents.split()
        commits.append({
            'sha': sha,
            'parents': parent_list,
            'subject': subject,
            'is_merge': len(parent_list) > 1,
        })
    return commits


def lang_for(path):
    return LANG_BY_EXT.get(Path(path).suffix)


def show_blob(rev, path):
    """Return file content at rev:path, or None if it doesn't exist there."""
    result = subprocess.run(
        ['git', 'show', '{}:{}'.format(rev, path)],
        capture_output=True,
        encoding='utf-8',
        errors='replace',
        timeout=60,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def extract_symbols(content, lang):
    """Top-level symbol names defined in `content` for the given language.

    Python: def/class/async def at column 0, OR indented exactly one level (4
    spaces) -- the "method directly inside a top-level class" case.
    JS/MJS: `export function|const|class NAME` or top-level `function NAME(`.
    Shell:  `name() {` at column 0.
    """
    symbols = {}
    for lineno, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.rstrip('\r')
        if lang == 'py':
            indent = len(line) - len(line.lstrip(' '))
            if indent not in (0, 4):
                continue
            stripped = line[indent:]
            match = PY_DEF_RE.match(stripped)
            if match:
                symbols.setdefault(match.group(2), lineno)
        elif lang == 'js':
            match = JS_EXPORT_RE.match(line) or JS_FUNC_RE.match(line)
            if match:
                symbols.setdefault(match.group(1), lineno)
        elif lang == 'sh':
            match = SH_FUNC_RE.match(line)
            if match:
                symbols.setdefault(match.group(1), lineno)
    return symbols


def diff_pairs_for_commits(commits):
    """(parent_sha, commit_sha) pairs for each non-merge commit with a parent."""
    pairs = []
    for commit in commits:
        if commit['is_merge']:
            continue
        if not commit['parents']:
            continue
        pairs.append((commit['parents'][0], commit['sha'], commit['sha'], commit['subject']))
    return pairs


def symbols_added_between(parent, head_sha, label_sha, label_subject):
    """Symbols newly present at head_sha that weren't at parent, per touched file.

    Uses rename-aware name-status diffing so a pure rename of an unmodified
    file never reports its whole symbol set as "newly added".
    """
    added = {}
    status_out = git(['diff', '--name-status', '-M', parent, head_sha]).stdout
    for line in status_out.splitlines():
        if not line.strip():
            continue
        parts = line.split('\t')
        status = parts[0]
        if status.startswith('D'):
            continue
        if status.startswith('R') or status.startswith('C'):
            old_path, new_path = parts[1], parts[2]
        else:
            old_path = new_path = parts[1]

        lang = lang_for(new_path)
        if lang is None:
            continue

        before = {}
        if status.startswith('A'):
            before = {}
        else:
            before_content = show_blob(parent, old_path)
            if before_content is not None:
                before = extract_symbols(before_content, lang)

        after_content = show_blob(head_sha, new_path)
        if after_content is None:
            continue
        after = extract_symbols(after_content, lang)

        for name in after:
            if name in before:
                continue
            if name in added:
                continue
            added[name] = {
                'name': name,
                'file': new_path,
                'lang': lang,
                'commit': label_sha,
                'commit_subject': label_subject,
            }
    return added


def collect_added_symbols(base, head, original_head=None):
    """Top-level symbols added by the branch's own non-merge commits."""
    added = {}
    if original_head:
        batch = symbols_added_between(base, original_head, original_head, commit_subject(original_head))
        added.update(batch)
        return added

    commits = list_commits(base, head)
    for parent, commit_sha, label_sha, label_subject in diff_pairs_for_commits(commits):
        batch = symbols_added_between(parent, commit_sha, label_sha, label_subject)
        for name, info in batch.items():
            if name not in added:
                added[name] = info
    return added


def grep_repo_for_symbol(head, name, lang, exclude_file):
    """Search the whole head tree for a definition of `name`; return the file
    path it's found in (excluding exclude_file), or None."""
    escaped = re.escape(name)
    if lang == 'py':
        pattern = r'^(async[ \t]+def|def|class)[ \t]+{0}\b|^[ ]{{4}}(async[ \t]+def|def)[ \t]+{0}\b'.format(escaped)
        pathspecs = ['*.py']
    elif lang == 'js':
        pattern = r'^export[ \t]+(function|const|class)[ \t]+{0}\b|^function[ \t]+{0}[ \t]*\('.format(escaped)
        pathspecs = ['*.js', '*.mjs']
    elif lang == 'sh':
        pattern = r'^{0}[ \t]*\([ \t]*\)[ \t]*\{{'.format(escaped)
        pathspecs = ['*.sh']
    else:
        return None

    cmd = ['grep', '-nE', pattern, head, '--'] + pathspecs
    result = subprocess.run(
        ['git'] + cmd,
        capture_output=True,
        encoding='utf-8',
        errors='replace',
        timeout=60,
    )
    if result.returncode not in (0, 1):
        return None
    prefix = head + ':'
    for line in result.stdout.splitlines():
        # `git grep <rev> -- <pathspec>` prefixes every line with "<rev>:".
        if line.startswith(prefix):
            line = line[len(prefix):]
        path = line.split(':', 1)[0]
        if path != exclude_file:
            return path
    return None


def find_drop_commit(base, head, name, file_path, lang):
    """First merge commit in base..head where `name` was present in its first
    parent's version of file_path but absent from the merge's own tree."""
    for commit in list_commits(base, head):
        if not commit['is_merge']:
            continue
        parent_content = show_blob(commit['parents'][0], file_path)
        present_before = bool(parent_content) and name in extract_symbols(parent_content, lang)
        merge_content = show_blob(commit['sha'], file_path)
        present_after = bool(merge_content) and name in extract_symbols(merge_content, lang)
        if present_before and not present_after:
            return commit
    return None


def git_log_pickaxe(base, head, name):
    try:
        out = git(['log', '--oneline', '-S{}'.format(name), '{}..{}'.format(base, head)], check=False)
    except GitError:
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def run_check(base_ref, head_ref, original_head_ref):
    base_sha = rev_parse(base_ref)
    head_sha = rev_parse(head_ref)
    original_head_sha = rev_parse(original_head_ref) if original_head_ref else None

    added = collect_added_symbols(base_sha, head_sha, original_head_sha)

    ok = []
    moved = []
    missing = []

    for name in sorted(added):
        info = added[name]
        lang = info['lang']
        file_path = info['file']

        head_content = show_blob(head_sha, file_path)
        survives_same_file = bool(head_content) and name in extract_symbols(head_content, lang)

        if survives_same_file:
            ok.append(name)
            continue

        moved_to = grep_repo_for_symbol(head_sha, name, lang, exclude_file=file_path)
        if moved_to:
            moved.append({
                'symbol': name,
                'lang': lang,
                'old_file': file_path,
                'new_file': moved_to,
                'added_by': {'sha': info['commit'], 'subject': info['commit_subject']},
            })
            continue

        drop_commit = find_drop_commit(base_sha, head_sha, name, file_path, lang)
        missing.append({
            'symbol': name,
            'lang': lang,
            'file': file_path,
            'added_by': {'sha': info['commit'], 'subject': info['commit_subject']},
            'dropped_by': (
                {'sha': drop_commit['sha'], 'subject': drop_commit['subject']}
                if drop_commit else None
            ),
            'git_log_pickaxe': git_log_pickaxe(base_sha, head_sha, name),
        })

    return {
        'base': base_sha,
        'head': head_sha,
        'original_head': original_head_sha,
        'symbols_checked': len(added),
        'ok': ok,
        'moved': moved,
        'missing': missing,
    }


def render_text(report):
    lines = []
    lines.append(
        'pr_symbol_survival_check: base={} head={}'.format(report['base'][:8], report['head'][:8])
    )
    lines.append(
        '  checked {} symbol(s) added by non-merge commits -- {} OK, {} MOVED, {} MISSING'.format(
            report['symbols_checked'], len(report['ok']), len(report['moved']), len(report['missing'])
        )
    )
    for entry in report['moved']:
        lines.append(
            '  INFO    MOVED    {}: {} -> {}'.format(
                entry['symbol'], entry['old_file'], entry['new_file']
            )
        )
    for entry in report['missing']:
        lines.append('  FAIL    MISSING  {} (was in {})'.format(entry['symbol'], entry['file']))
        added_by = entry['added_by']
        lines.append('          added by   {} {}'.format(added_by['sha'][:8], added_by['subject']))
        if entry['dropped_by']:
            dropped_by = entry['dropped_by']
            lines.append('          dropped by {} {}'.format(dropped_by['sha'][:8], dropped_by['subject']))
        else:
            lines.append('          dropped by UNKNOWN (no merge commit in range lost it)')
        if entry['git_log_pickaxe']:
            lines.append('          git log -S{} --oneline:'.format(entry['symbol']))
            for pick_line in entry['git_log_pickaxe']:
                lines.append('            {}'.format(pick_line))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Verify top-level symbols added by a PR branch survive merge-conflict resolution.'
    )
    parser.add_argument('--base', default='origin/main', help='Base ref (default: origin/main)')
    parser.add_argument('--head', default='HEAD', help='Head ref (default: HEAD)')
    parser.add_argument(
        '--original-head',
        default=None,
        help='Optional: diff base..original-head as a single unit instead of walking each non-merge commit',
    )
    parser.add_argument('--json', action='store_true', help='Emit JSON instead of text')
    args = parser.parse_args(argv)

    try:
        report = run_check(args.base, args.head, args.original_head)
    except GitError as exc:
        print('ERROR: {}'.format(exc), file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired as exc:
        print('ERROR: git command timed out: {}'.format(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))

    return 1 if report['missing'] else 0


if __name__ == '__main__':
    sys.exit(main())
