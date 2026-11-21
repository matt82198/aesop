# GEMINI.md (agy, Gemini CLI)

You share this machine with a Claude orchestrator. Read and follow `docs/FOREIGN-AGENTS.md` in this repo. Short form:
worktree + feature branch + PR only; never publish to main/master or use --admin; never edit primary checkouts or ~/.claude hooks/settings;
skip files held in `C:/Users/Mattt/.claude/state/lane-holdings`; run `python ~/scripts/secret_scan.py --staged` before remote publishes;
message the orchestrator by appending `- [ISO-ts] <codex|agy>: <text>` to `C:/Users/Mattt/conductor3/state/ui-inbox.md`.
