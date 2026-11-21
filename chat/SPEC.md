# chat/SPEC.md - durable improvement spec for the aesop chat TUI

## Goal

Make `aesop chat` a robust, readable terminal chat on par with Claude Code, Codex CLI and aider for
everyday use: input never gets stuck or lost, repaints stay correct through resize, errors are
visible, and Windows consoles behave. Zero dependencies (Node stdlib only), pure reducer and
renderer, offline-testable through `runChat` with injected stdin/stdout.

Scope: chat/*.mjs and the `chat` wiring only. Out of scope: an orchestrator backend, network calls,
PR 1425 (paste-stuck recovery: Esc/ctrl-c leave paste, idle timeout) and PR 1426 (atomic history save
with a visible failure line), which this spec deliberately does not duplicate.

Each requirement is one line of observable behavior, then `Check:` the acceptance check.
`tests/chat-spec.test.mjs` holds one test per requirement named `R<n>: ...`.
`node tools/chat_spec_drift.mjs --baseline <sha>` measures progress and plan drift.

## Requirements

R1. A ctrl-c quit hint disappears on the next non-ctrl-c key. Check: ctrl-c then `a`; the latest frame does not contain `press ctrl-c again`.
R2. Esc after one ctrl-c cancels the pending quit. Check: ctrl-c, Esc, ctrl-c does not exit; two more ctrl-c do.
R3. The input reducer reads no clock. Check: with Date.now throwing, applyKey on a normal key (also while pasting) does not throw.
R4. Two chat instances sharing one history file do not lose each other's lines. Check: A and B start, A submits `a`, B submits `b`; the file holds both.
R5. Submitting the same line twice in a row stores one history entry. Check: submit `hi` twice; the file is `["hi"]`.
R6. A backslash-continued multi-line entry persists as one history entry. Check: `a\`, Enter, `b`, Enter; the file is `["a\nb"]`.
R7. History is capped at 500 entries, newest kept. Check: 600 preloaded plus one submit leaves 500 entries ending in the new line.
R8. After a resize every frame line fits the new width. Check: shrink 80 to 30 columns; every line of the next frame is at most 30 columns.
R9. A resize repaint moves the cursor up over the physical rows the old frame now occupies after reflow. Check: the new ESC[nA count equals the sum of ceil(width/newColumns) over the previous frame's lines, minus one.
R10. A frame never exceeds `rows` lines and the input line is always last. Check: 40 transcript lines at rows=10; at most 10 lines, last starts with `> `.
R11. The first frame tells the user how to get help. Check: the first frame contains `/help`.
R12. `/help` documents the keys (ctrl-u, ctrl-d, history). Check: after `/help` the output contains `ctrl-u`, `ctrl-d` and `history`.
R13. An unknown slash command shows an error naming it and never reaches the backend. Check: `/bogus` shows `unknown command: /bogus`; the source is never called.
R14. TAB on a lone slash prefix completes the command. Check: `/he` + TAB gives `/help`; `/q` + TAB gives `/quit`.
R15. Wide (CJK) characters are measured as two columns when wrapping. Check: no frame line exceeds the column count measured in display width.
R16. A backend error mid-stream is shown in the transcript and the session keeps working. Check: the line `stream error: boom` appears and a second turn still reaches the source.
R17. Error lines are drawn in red when color is on. Check: a system line starting `stream error:` or `error:` carries ESC[31m.
R18. When the frame truncates old transcript, it says how many lines are hidden. Check: a frame line matches `(N earlier lines hidden)` and the frame still fits rows.
R19. A bare Windows console (no TERM, no WT_SESSION) gets ASCII borders; Windows Terminal gets unicode. Check: resolveUnicode with platform win32 returns false, then true with WT_SESSION set; ASCII frames contain no box characters.
R20. A CRLF paste produces one newline per line break. Check: paste-start, `a`, return, enter, `b`, paste-end yields `a\nb`.
R21. ctrl-w deletes the word before the cursor. Check: `foo bar` with cursor at end becomes `foo `.
R22. ctrl-k deletes from the cursor to the end of the line. Check: `foobar` with cursor 3 becomes `foo`.
R23. A corrupt or non-array history file starts empty and the session works. Check: file `{"x":1}`; a submit succeeds, exit code 0, file becomes `["hi"]`.
R24. Exit restores the terminal exactly once. Check: one PASTE_ON and exactly one PASTE_OFF after ctrl-c twice, exit code 0.
