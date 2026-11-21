# FOREIGN-AGENTS.md - rules for non-Claude agents (Codex, agy/Antigravity/Gemini)

Canonical interop text. Source of truth: aesop `docs/FOREIGN-AGENTS.md`. The per-tool files
(`AGENTS.md`, `GEMINI.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`) are short pointers to this.

Why: the Claude orchestrator, Codex, and agy share the same repos on one machine. Without a shared
contract a foreign agent can edit a primary checkout or a file a Claude lane owns, and nothing
traces it back. These rules keep the fleets from colliding.

## Rules

1. Read `<AESOP_ROOT>/LANE-CONTRACT.md` before writing anything. Report only commands you ran.
2. Work only in a sibling git worktree on a feature branch (`git worktree add ../<repo>-wt-<task> -b feature/<task> origin/main`). Open a PR. Never publish to `main`/`master`. Never use `--admin` or force-merge.
3. Never edit primary checkouts: `<AESOP_ROOT>`, `<CLAUDE_HOME>`, `<SCRIPTS_HOME>`, `<CONDUCTOR3_HOME>`. Never edit `~/.claude` hooks or `settings.json`. Never delete files outside your own worktree.
4. Before editing, check what Claude lanes own and skip it: `<CLAUDE_HOME>/state/lane-holdings` (one JSON per live lane) and the branches in `git worktree list`. If your target is held, pick other work or leave an inbox note.
5. Before every remote publish run `python ~/scripts/secret_scan.py --staged`; exit 1 means stop.
6. To talk to the Claude orchestrator, append one line to the inbox that `~/scripts/inbox_drain.py` reads: `<CONDUCTOR3_HOME>/state/ui-inbox.md`, format `- [ISO-ts] <codex|agy>: <text>`. `/power` drains it. Append only; never rewrite the file. (`<AESOP_ROOT>/INBOX.md` is NOT read by inbox_drain.py.)
7. Keep a ledger of what you changed (repo, branch, PR) in your PR descriptions and in an inbox line when you finish.

## Which file each tool reads (from the tools' published docs, not from this repo)

| Tool | Global | Repo | Docs |
|------|--------|------|------|
| Codex CLI | `~/.codex/AGENTS.md` (or `AGENTS.override.md`) | `AGENTS.md` from repo root down to cwd, concatenated | https://developers.openai.com/codex/guides/agents-md |
| Gemini CLI | `~/.gemini/GEMINI.md` | `GEMINI.md` in cwd and parents; filenames configurable via `context.fileName` in `settings.json` (can include `AGENTS.md`) | https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md |
| Antigravity (agy) | `~/.gemini/GEMINI.md` | `AGENTS.md` and/or `GEMINI.md` at workspace root, plus `.agent/rules/` | https://antigravity.google/docs (rules and workflows) |

Caveat: citations reflect the tools' documentation as known at authoring time; the authoring
session had no web access. Whether the installed agy build loads each file is unconfirmed until an
agy session is observed honoring one.
