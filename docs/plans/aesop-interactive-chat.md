# Aesop interactive chat: plan

Status: DRAFT plan, 2026-10-31. Owner: Matt. No code in this PR.
Companion docs: [stdio pipelines plan](./native-stdio-pipelines.md), [auth plan](./aesop-auth.md)

## 1. Intent

Matt (owner), 2026-10-31, verbatim: "and how to launch aesop as an interactive chat, it can't be just a dead CLI, it needs to be at minumum as visual as claude code".

Scope: `aesop` with no arguments (or `aesop chat`) on a terminal opens a full-screen interactive chat at least as visual as Claude Code. When stdout is not a TTY the same binary is a clean Unix filter, so the stdio pipeline contract is preserved. The chat is a client of the pipe contract and the auth service, never a separate code path.

## 2. Inventory of what exists (cited; code reads, nothing run)

- Entry point: bin/cli.js:38 reads argv. With no args and an interactive terminal (cli.js:397-399, isInteractive = stdin.isTTY and stdout.isTTY) it runs the onboarding wizard (cli.js:694, wizardMode). With no args and no TTY it appears to fall through to scaffolding a default directory (cli.js:710-711; inference, not executed, needs confirmation in a throwaway directory). Help at cli.js:673-677; unknown dash flags exit 2 (cli.js:142-147). Dispatch tiers: pythonNamespaces :46, runtimeCommands :247, commandMap :380.
- Web dashboard: ui/serve.py (stdlib backend, ui/CLAUDE.md:26) on 127.0.0.1, default port 8770 (ui/config.py:68). `aesop dash` spawns it via tools/dash.js:57-60,110. Server-sent events: ui/handler.py:1188 writes `event: <name>` plus `data:` frames; sections in ui/sse.py:34-35 (data, backlog, agents, tracker, status, cost) plus collector_health (sse.py:359); keepalive about 15 s (ui/CLAUDE.md:115); also /api/state, /api/wave/prs (5 s cache), /api/wave/dispatch, /api/queue (ui/CLAUDE.md:105).
- Terminal dashboards: dash/watchdog-gui.sh (bash, 4 s loop; ANSI via escape strings at :9-16, clear/redraw at :40-41, :161-170; shows watchdog ALIVE/STALE, per-file heartbeats from state/.heartbeats/*, security alert counts, backup state; does not show lanes, PR state or cost). dash/dash-extra.mjs (Node; ANSI palette :67-74; --json mode :473; scans transcripts modified within 12 minutes; shows agent phase and activity). ~/scripts/launch_tui.py only opens a new window running a given script via Git Bash or wt.exe (idempotent by pidfile, :118-130); it renders nothing.
- Rendering techniques in the repo: no curses, no tput, no readline in ui/, dash/ or tools/. readline appears only in bin/cli.js:6 for prompts (promptUser :424). ANSI escapes are emitted unconditionally in tools/status.js:118-122, tools/doctor.js:26-29, tools/reproduce.js:27-32. There is no NO_COLOR handling and no isTTY check in bin/, ui/, dash/ or tools/ (grep negative): piped output of these commands carries escape codes today, which violates the pipe contract and is a phase 0 finding.
- Runtime minimums: Node >=18 (package.json:54-55; .nvmrc 22), Python >=3.11 (pyproject.toml:19; docs disagree in places: ANY-REPO.md:16 says 3.10).
- Chat status data sources: lane and agent rows and phase from ui/wave_dispatch.py and dash/dash-extra.mjs --json; heartbeat age from state/.heartbeats/* (dash/CLAUDE.md:14-19, ui/collectors.py); cost from state/ledger/OUTCOMES-LEDGER.md via ui/cost.py and ui/wave_telemetry.py; PR state from ui/wave_prs.py (gh, degrades when gh missing); orchestrator phase from ui/wave_telemetry.py; single call GET /api/state or subscribe to /events.

## 3. Architecture

1. One event stream. Everything the chat shows comes from the same event contract that feeds the web dashboard: the SSE sections above, and (new) the pipe control channel from the stdio plan. A shared "state feed" module produces normalised events; the TUI and the web UI are two renderers of that feed. No renderer reads files directly.
2. Chat session loop. User input and slash commands are sent as control messages over the pipe contract to a session backend (the driver seam: driver/CLAUDE.md, backend_config.py) and streamed assistant output returns as data frames of content type text/markdown. The chat never calls a model provider directly.
3. Auth and models. `/auth` and `/model` map one to one onto the auth plan commands (`aesop auth ...`, `aesop models ...`) and render their JSON (auth plan section 7). The chat shows the active account label and model in the status line and refuses to start a billable session with no default account (fail closed with a hint to run `/auth login`).
4. TTY detection decides the renderer (stdio plan 5.13): stdin and stdout both TTY and no subcommand gives the full-screen chat; stdout not a TTY gives a clean filter (no ANSI, no prompts, no spinners); TTY with --plain or NO_COLOR-and-dumb-TERM gives line mode (readline-style prompt, no cursor control). The same input is accepted in all three: in filter mode, stdin is read as the prompt (whole stream, or line by line with --lines) and the answer streams to stdout as plain text or framed output.
5. Escape-sequence discipline. Renderers write only through a single `term` module that checks the output stream is a TTY; a conformance test fails any ESC byte on non-TTY stdout. Diagnostics go to stderr.
6. Local only. The chat process talks to the loopback services; no network listener is opened by the TUI.

## 4. Rendering approach under the ZERO DEPENDENCY constraint

Choice: Node standard library, recommended. One-sentence recommendation for the TUI runtime: build the chat in Node using only process.stdout/stdin raw mode, ANSI/VT sequences and readline.emitKeypressEvents, because Python's stdlib has no curses on Windows and would need two input backends (msvcrt and termios) plus hand-rolled rendering.

Honest comparison with the repo's split:
- Repo split: tools/, ui/ backend, driver/ are Python; bin/ and the existing agent TUI (dash/dash-extra.mjs) and MCP server are Node. A Node TUI matches precedent (dash-extra.mjs already emits ANSI) and Node is already a hard dependency of the npm package (bin/cli.js).
- Python option: curses is absent from the Windows stdlib (windows-curses is third party, forbidden here); termios/tty are POSIX-only; msvcrt gives getch on Windows; ctypes can enable VT mode. Workable but more code and two backends.
- Cost of choosing Node: the auth service is Python (auth plan 7), so the chat talks to it over the pipe contract; that boundary already exists by design.

Techniques (all public knowledge; Windows specifics UNVERIFIED until run on real terminals):
- Detection: process.stdout.isTTY/stdin.isTTY; tty.WriteStream.getColorDepth/hasColors; NO_COLOR (any non-empty value disables colour), FORCE_COLOR, COLORTERM=truecolor|24bit, TERM=dumb means line mode.
- Input: process.stdin.setRawMode(true) after the isTTY check, restore on every exit path (normal, SIGINT, uncaughtException, process exit); readline.emitKeypressEvents for decoded keys; bracketed paste (ESC[?2004h) so pasted text is not interpreted as keystrokes.
- Output: CSI sequences (cursor move, clear line, scroll region, hide/show cursor); optional synchronized output (ESC[?2026h/l) to reduce flicker where supported, treated as an enhancement only.
- Screen model: inline rendering in the normal buffer for the conversation (keeps native terminal scrollback, like Claude Code, UNVERIFIED for its current version) with a small redrawn region at the bottom (input box, status line, active spinner or lane rows). The alternate screen (ESC[?1049h) is used only for full-screen overlays (lane view, help), always restored on exit.
- Resize: process.stdout.on('resize'); columns/rows undefined when not a TTY so default 80x24; the redraw is idempotent.
- Width: display width is not string length (CJK and most emoji are 2 columns, combining marks 0). Node has Intl.Segmenter for graphemes but no width table, so ship a small stdlib-only width table module; unknown or ambiguous glyphs are treated as width 1 and the table is covered by tests.
- Windows: Windows Terminal parses VT natively; legacy conhost needs ENABLE_VIRTUAL_TERMINAL_PROCESSING (Node/libuv attempts to enable it for TTY stdout; behaviour per Node version UNVERIFIED); ConPTY (Windows 10 1809+) underlies modern hosts. v1 supports Windows Terminal and VS Code terminal as tier 1, legacy conhost as tier 2 with automatic line-mode fallback if VT is not confirmed, tested by actually running on both before claiming support.
- Markdown: a small line-based renderer for the useful subset (ATX headings, bold, italic, inline code, fenced code blocks, bullet and numbered lists, block quotes, simple pipe tables using the width table). Anything outside the subset is printed as plain text. Streaming-safe: render completed lines; hold back an unfinished fence or table row until its end.
- Syntax highlighting: a tokenizer-per-language of regex rules (keywords, strings, comments, numbers) applied per line, for a short list (js, ts, py, sh, json, yaml, diff, md); unknown languages stay unhighlighted. Quality is below editor-grade by design.
- Colour: 24-bit when COLORTERM says so, else 256, else 16, else none; one theme table; no information conveyed by colour alone (symbols and text accompany it).

## 5. Feature parity checklist against Claude Code's visible surface

Claude Code labels and bindings below are from general public knowledge and are UNVERIFIED against the current version; the checklist is a target list to be confirmed by watching it, not a spec copied from it.

| Surface | v1 or later | Notes |
|---|---|---|
| Streaming assistant text, rendered incrementally | v1 | Markdown subset; line-complete rendering |
| Persistent multi-line input box (Enter send, Shift+Enter or backslash newline), history up/down | v1 | Bracketed paste |
| Status line: model, account label, lane count, context usage, cost, phase | v1 | Fed by the state feed; context usage needs a driver field (question for the driver seam) |
| Spinner with rotating verb, elapsed time, token count | v1 | Disabled in plain/NO_COLOR/reduced-motion mode |
| Tool-call rows (name, args summary, status, duration) | v1 | From pipe event messages |
| Agent/lane progress rows with heartbeat age and PR state | v1 | Aesop-specific, from ui/wave_dispatch.py and ui/wave_prs.py data |
| Slash commands: /power /afk /runwave /auth /model /help /clear /exit | v1 | /power, /afk, /runwave map to existing orchestration entry points (to be listed by the owner); /auth and /model map to the auth plan |
| Slash command completion and a command palette list | v1 | Tab completion from a registry |
| Scrollback (native terminal scrollback in inline mode) and in-app lane view overlay | v1 | |
| Esc to interrupt the running turn; Ctrl+C twice to exit | v1 | Restores the terminal on every path |
| Permission prompts (approve or deny a tool or merge action) | v1 | Required for fail-closed gating; default deny on timeout or EOF |
| Mode indicators (for example plan/auto) cycled with a key | later | Needs aesop mode definitions |
| Diffs rendered inline with colour | later | v1 shows unified diff via the diff highlighter |
| Compaction/checkpoint notices | later | Depends on the context/checkpoint events existing |
| @-file mentions with completion | later | |
| Image paste, mouse support, themes | later | |
| Session resume/list | later | Disk is the memory; resume reads event store |
| Plain line mode with identical commands | v1 | Accessibility and non-TTY |

## 6. Accessibility and NO_COLOR

- NO_COLOR (non-empty) disables all colour; FORCE_COLOR overrides only for non-TTY capture and is documented as a testing aid.
- --plain (and TERM=dumb) selects line mode: no cursor movement, no redraw spinners; status is printed as discrete lines on change. Screen readers and braille displays work best with discrete output.
- --no-motion disables spinners and animation (no standard env var exists for reduced motion; flag only).
- No meaning is carried by colour alone. Minimum contrast checked on the default theme in tests (static table check).
- All interactions are keyboard-only.

## 7. Phased delivery

All lanes: Haiku, file-disjoint, TDD-first (failing test first, mutation proof per LANE-CONTRACT s2 and s5), read LANE-CONTRACT.md and one domain CLAUDE.md. The lead applies edits to shared files (bin/cli.js dispatch, docs/README.md). Node tests are named *.test.mjs and run through the existing Node runner; new tests are confirmed counted.

Phase 0, tests and spec first:
- C0-FEED owns docs/guides/chat-state-feed.md: the normalised event vocabulary for the feed (doc lint only).
- C0-TTY owns tests/chat-tty-fallback.test.mjs: test_no_escape_bytes_on_non_tty_stdout, test_no_args_with_pipe_stdin_runs_filter_not_chat, test_no_args_tty_opens_chat_not_wizard_after_wizard_done, test_plain_flag_selects_line_mode, test_term_dumb_selects_line_mode, test_no_color_env_disables_all_sgr.
- C0-RENDER owns tests/chat-render-snapshot.test.mjs (renderer against a fake TTY object with settable columns/rows and a recording write sink; snapshots are literal expected byte strings): test_markdown_headings_lists_fences_snapshot, test_streaming_unfinished_fence_held_back, test_table_alignment_with_wide_glyphs, test_highlight_js_py_sh_json_snapshot, test_unknown_language_unhighlighted, test_truecolor_256_16_none_downgrade, test_resize_redraw_idempotent, test_status_line_truncates_to_columns.
- C0-INPUT owns tests/chat-input.test.mjs: test_keypress_decode_arrows_enter_esc, test_bracketed_paste_not_executed_as_commands, test_multiline_input_shift_enter, test_history_navigation, test_raw_mode_restored_on_sigint_and_exception.
- C0-SLASH owns tests/chat-slash.test.mjs: test_slash_registry_completion, test_auth_slash_maps_to_auth_cli_json, test_model_slash_maps_to_models_cli_json, test_unknown_slash_command_is_error_not_sent_to_model.
- C0-PERM owns tests/chat-permission.test.mjs: test_permission_prompt_default_deny_on_eof, test_permission_prompt_default_deny_on_timeout.
- C0-WIDTH owns tests/chat-width.test.mjs: test_wcwidth_cjk_emoji_combining_vectors.
- C0-ZERODEP extends the stdlib gate from the stdio plan (tools/check_pipe_stdlib.py, lead-owned) with chat paths: test_no_non_builtin_require_in_chat_package; planted third-party import must turn the gate red.
- C0-WIN owns tests/chat-windows-vt.test.mjs: test_vt_unconfirmed_falls_back_to_line_mode (fake TTY reporting no VT support).
Phase 1, core: chat/term.js (TTY and colour detection, single write path), chat/width.js, chat/input.js, chat/markdown.js, chat/highlight.js, chat/render.js, chat/statusline.js, chat/slash.js, chat/session.js, chat/index.js (one lane per file, directory chat/ is a new Node domain with its own CLAUDE.md written by the lead). Goal: phase 0 green.
Phase 2, feed and wiring: shared state feed module (reads ui SSE /events and /api/state; owner lane for chat/feed.js); lane rows and PR state; `aesop` no-arg dispatch change in bin/cli.js (lead).
Phase 3, orchestration slash commands wired to the owner's entry points; permission prompts wired to merge/dispatch actions.
Phase 4, later items from section 5.
Also a prerequisite fix lane (separate from chat): add isTTY and NO_COLOR handling to tools/status.js, tools/doctor.js, tools/reproduce.js so piped output is escape-free (tests: test_status_pipe_output_has_no_escape_bytes and equivalents).
Dependency: phase 0 parallel; phase 1 after the red baseline; phase 2 after the stdio plan phase 1 and the auth plan phase 1; phase 3 after phase 2.

## 8. Risks

1. Windows console quirks (legacy conhost VT, emoji width, raw mode restore). Mitigation: tier-1 terminals only for v1, VT confirmation with line-mode fallback, run on real Windows Terminal and conhost before claiming support.
2. Redraw performance and flicker. Mitigation: redraw only the bottom region, coalesce updates to a frame budget, optional synchronized output.
3. Mixing chat output with pipe stdout (escape bytes in a pipe). Mitigation: single write path with TTY check, C0-TTY tests, existing tools fixed in the prerequisite lane.
4. Scope creep toward matching Claude Code feature by feature. Mitigation: v1 column is explicit; later items need a decision.
5. Terminal left in raw mode after a crash. Mitigation: restore on every exit path; test with injected exceptions.
6. Two code paths (chat versus pipe) drifting. Mitigation: chat is a pipe client; shared feed; tests drive both with the same fixtures.
7. Unverified Claude Code surface details. Mitigation: confirm by watching it before locking v1 behaviour.
8. Zero-dependency markdown/highlight quality is limited. Mitigation: documented subset; unknown input is passed through unstyled.
9. Non-TTY no-arg behaviour today (possible directory scaffold at bin/cli.js:710-711) is surprising for a filter. Mitigation: confirm and change in phase 2 with a test.

## 9. Open decisions for Matt
- H1. Runtime: Node stdlib TUI (recommended) versus Python.
- H2. Rendering model: inline in the normal buffer with a redrawn bottom region (recommended) versus full alternate-screen app.
- H3. Terminal support tiers for v1 (Windows Terminal and VS Code terminal tier 1; legacy conhost line-mode only?).
- H4. Which orchestration entry points /power, /afk, /runwave bind to, and what is in the v1 slash list.
- H5. Which Claude Code behaviours are must-have for "at minimum as visual": confirm the v1 column in section 5.
- H6. What the chat's session backend is for v1 (driver seam, Claude Code subprocess, or direct provider via the auth layer).
- H7. Non-TTY no-argument behaviour: usage text and exit 2 (recommended) versus today's scaffold.
- H8. Directory name and ownership for the new Node domain (chat/ versus bin/lib/chat/).
