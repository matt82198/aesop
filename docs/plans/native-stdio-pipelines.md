# Native stdin/stdout pipelines: v1 plan

Status: DRAFT plan, 2026-10-31. Owner: Matt. Priority: number 1. No code in this PR.
Companion docs: [auth plan](./aesop-auth.md), [interactive chat plan](./aesop-interactive-chat.md).

## 1. Intent

Matt (owner), 2026-10-31, verbatim:

- "spin up a team to begin planning the native stdin and stdout pipelines"
- "they will be priority number 1"
- "we will need some more requirements"
- "specifically designing them to work with codal infrastructure as they are adopting the infra"
- "generally, they need to be generic... codal has all types of different data formats they use and pipes/streams and this is 0 dependency"

Nothing in the repo, STATE.md, BUILDLOG.md, docs, issues or PRs defines "native stdin and stdout pipelines". This plan does not invent Matt's spec. It proposes a definition, states what is unknown, and is shaped so that requirements slot in without redesign (section 6 and the open question).

## 2. Hard requirements (from Matt, 2026-10-31)

Each is a gate with its own phase-0 conformance tests (section 7.1).

### 2.1 GENERIC
The pipeline is not bound to one payload format. A thin framing layer carries opaque bytes with a declared content type (application/json, application/x-ndjson, text/csv, text/plain, application/octet-stream, or any MIME-like tag the adopter invents). Aesop messages (receipts, events, control) are one content type among many (application/vnd.aesop.control+json), never the only one.

Unix filter stance, and the default: with no flag, every aesop pipe tool passes bytes through untouched. Aesop interprets framed control messages only when a flag (--frame) or a stream header (the magic line in 5.3) opts in. A stream with no opt-in is never parsed, never re-encoded, never newline-normalised.

### 2.2 PIPES AND STREAMS
Two modes, same contract:

- Whole-stream: read stdin to EOF, write stdout, exit code.
- Incremental: line- or record-delimited (or length-prefixed frames), backpressure-aware, never buffering the whole input in memory (the memory bound is a constant, asserted by test).

stderr is reserved for diagnostics so stdout stays clean for the next stage. Named pipes and sockets are a later phase (phase 4, optional); the v1 contract is plain stdin, stdout, stderr, exit code.

### 2.3 ZERO DEPENDENCY
The pipeline implementation uses only the language standard library: Python stdlib and Node stdlib (precedent: mcp/server.mjs is stdlib-only). No third-party packages, no native modules. This is a gate, not a preference: a phase-0 test fails if a non-stdlib import appears in the pipeline package, and a CI check enforces it (7.1).

Known tension: Ed25519 signing in tools/receipt_common.py:45-51 needs the third-party cryptography package. The pipeline package must not import it. Signing stays in the receipts layer and is invoked as a separate process or an optional adapter outside the zero-dependency package (5.10, open decision D5).

## 3. Definition candidates

- A. Orchestrator-to-worker stream protocol over stdio. The kernel starts a worker process and exchanges structured messages over its stdin/stdout (the shape of Claude Code's input/output stream-json formats).
- B. Every aesop tool is a Unix filter: --from-stdin, clean stdout, diagnostics on stderr, stable exit codes. Extends PR #443.
- C. An external adopter contract: a third party such as Codal drives aesop from their own CI or agent host by writing to aesop's stdin and reading receipts/events from its stdout.

### Recommendation: a generic framed byte pipeline (the substrate) with an aesop control channel layered on top; B and C are the first two consumers, A is the third.

1. Matt's 2026-10-31 constraint (generic, many formats, zero dependency) rules out A-as-defined (an aesop-only message protocol) as the foundation. A bakes aesop's schema into the transport; Codal pipes their own formats.
2. B is the lowest-risk, highest-value start: six tools already read stdin (4.1) and the convention exists (tools/CLAUDE.md:52). Making it uniform is mostly conformance work.
3. C is the purpose (Codal is adopting the infra). C cannot be finalised until the open question is answered, so the substrate must not depend on any one Codal answer.
4. A becomes a profile of the substrate: a worker stream is a framed stream whose control channel carries start/event/result/error messages. Doing A last avoids designing it twice.

Tradeoff note: a generic framed substrate is more work than "JSONL for everything" and adds a header line that plain Unix users never see. We accept this because (a) raw passthrough is the default so plain pipes are unaffected, and (b) JSONL alone cannot carry binary or arbitrary-delimiter payloads. If Codal answers show only line-delimited JSON, the frame layer can shrink to "JSONL with a content-type header line" with no consumer-visible change. We do not disagree with the lean toward a generic framed byte pipeline; we flag that frames are only needed in framed mode (5.3).

### Disambiguation from STATE.md G5
STATE.md G5 uses "pipeline" for a chain of stage lanes S1 SPEC, S2 IMPL, S3 GATE, S4 SHIP, S5 REVIEW (STATE.md:27; docs/STAGE-CONTRACTS.md:7, 11-17). Handoff there is a durable artifact (path or sha) named in a hand-back, orchestrator-gated and serial (STAGE-CONTRACTS.md:168-170). No bytes flow lane to lane.

A stdio pipeline is OS byte streams between concurrent processes, with backpressure and no durable artifact unless tee'd. Relationship: G5 stages are coarse, durable, orchestrator-mediated; stdio is fine-grained, streaming, kernel-mediated. A G5 stage MAY run its tools as a stdio pipeline internally, and a stage's hand-back MAY be a sealed stream digest (5.10). This plan writes "stdio pipeline" or "pipe" and never bare "pipeline".

Also, "G1 receipts/hash-chain" conflates two subsystems: signed receipts (not hash-chained) and the hash-chained event store (not signed). See 4.4.

## 4. Current state inventory (cited; code reads, nothing here was reproduced at runtime)

### 4.1 Tools (tools/, bin/)
- 150 tools/*.py files use argparse; 81 declare --json; only 6 read stdin: commit_lint.py:149, generated_paths.py:479-480, git_integrity_check.py:156, secret_scan.py:1240 (--from-stdin argparse at :1103; empty stdin documented as not-clean at :1075), verify_receipt.py:125 ("-" = stdin), wave_preflight.py:724 (--from-stdin, :707, :721-730).
- bin/cli.js reads process.stdin only for a TTY-gated interactive prompt (cli.js:399, 1663).
- Exit-code convention 0 clean, 1 findings, 2 usage/cannot-evaluate: tools/cli.py:171-177, tools/CLAUDE.md:52, secret_scan.py:1131/1322/1325. bin/cli.js:25-30 maps signal/spawn failure to 2, never 0.
- 127 is NOT a CLI convention; it appears as a sentinel in generated_paths.py:362 and as spawn failure in driver/proc_util.py:99-100.
- stderr discipline is by convention: secret_scan.py:1131, commit_lint.py:151, generated_paths.py:464-476, bin/cli.js:1297-1305. No enforcing test found.
- Encoding: encoding='utf-8', errors='replace' on subprocess (secret_scan.py:786, 936-937); sys.stdout.reconfigure in boot_state.py:394, healthcheck.py:31-37; scripts/verify-stats.sh sets PYTHONIOENCODING=utf-8.
- Gaps: secret_scan.py has no --json; wave_preflight.py reads all stdin into a list before checking (:724-728), so it is not streaming; verify_receipt.py takes one receipt not a stream.

### 4.2 Process layer (driver/)
- proc_util.run_shell_bounded(command, cwd, timeout_s) at driver/proc_util.py:73; kill_process_tree at :41. Popen is shell=True, stdout/stderr=PIPE, utf-8, errors=replace (:88-98), no bufsize, no stdin argument. OSError becomes exit 127 (:99-100).
- Capture uses communicate(timeout) (:103): deadlock-safe but unbounded in memory. No idle or heartbeat timeout; only a whole-run wall clock. Exit 124 on timeout (:131-133) is overloaded with a child's own 124 (wave_loop.py:45-46).
- Windows: CREATE_NEW_PROCESS_GROUP (:82), taskkill /T /F with a 5 s timeout (:45-49). A grandchild holding the pipe can make the bounded drain return nothing (:124-128): candidate hazard, not reproduced.
- driver/CLAUDE.md:13-16 documents the shell=True timeout hang and its fix. MSBuild node reuse holding stdout is recorded at STATE.md:415; no code handles it. The process-tree-kill story is at docs/archive/interview-stories/process-tree-kill.md.
- Implication: the existing runner is capture-then-return. A streaming reader must be a separate code path (5.8), not a patch to communicate().

### 4.3 Reusable framing already in the repo
- MCP server: newline-delimited JSON-RPC 2.0 over stdio (not Content-Length). Read via readline (mcp/server.mjs:127, 133-144); write JSON.stringify(resp)+newline (:150). Error codes -32600/-32601/-32603 (:1445, 1458, 1478, 1493). No -32700: unparseable lines are silently dropped (:139-141, 1471-1473), which is fail-open and must not be copied. Tests spawn node ./mcp/server.mjs --root <tmp> and write NDJSON (tests/mcp-fleet.test.mjs:27-84).
- Possible defects noted by recon (unverified, need a repro before filing): rl.once('line') per request may drop pipelined lines (server.mjs:133-144); unknown-method reply to notifications (:1493).
- Hooks: PreToolUse JSON object on stdin (tool_name, tool_input, session_id, cwd); decision as stdout JSON with permissionDecision allow or deny, exit 0 (hooks/claude/force-model-policy.mjs:113-120, 147-178). force-model-policy.mjs:93-109 races stdin against a 2 s timer and denies on empty, malformed or timeout (:1-33, 130-137). Two other hooks allow on malformed stdin (no-heredoc-file-authoring.mjs:124, no-scrollback-as-record.mjs:169), which conflicts with fail-closed; flagged to the orchestrator, outside this plan.
- docs/HOOK-INSTALL.md lives at docs/archive/HOOK-INSTALL.md; docs/PORTING.md has no stdio content.
- Reusable: NDJSON writer helpers (mcp/server.mjs:149-177), error-code set, stdin-with-timeout reader pattern, test-harness pattern. New: frame layer, control schema, parse-error reply, per-request queue, any write path (server.mjs is read-only by design, mcp/CLAUDE.md:109-110).

### 4.4 Receipts and event store
- Receipts are signed, not hash-chained. Fields: tools/emit_receipt.py:760-783 (head_sha, base_sha, tree_hash, parts, skipped, host, env, timestamp). Sign at emit_receipt.py:983. Canonical form tools/receipt_common.py:40 is sorted keys, compact separators, ensure_ascii; this is NOT strict RFC 8785 JCS (float formatting may differ, inference).
- Schemes: Ed25519 via the cryptography package (receipt_common.py:45-51, third-party), HMAC-SHA256 fallback (stdlib). Keyring: tools/receipt_pubkey.pub plus tools/receipt_pubkeys/*.pub (receipt_common.py:143-161), keyed by key_id = sha256(PEM)[:16] (:81). Verify: tools/verify_receipt.py:194, exit 0/1/2.
- Two event stores, neither is the receipt store: state_store/store.py:252 EventStore.append (SQLite WAL, versioned per stream, no hash) and tools/event_store/store.py:94 Store.append (JSONL with prev = sha256 of previous line; tamper-evident, unsigned, last line unanchored; docs/concepts/event-store.md:149). The latter has no visible lock (store.py:106, 113-115): concurrent stdio writers could fork the chain (inference).

### 4.5 What does not exist
No stream-json-style JSONL between kernel and workers; no framing layer; no content-type negotiation; no pipe conformance suite.

## 5. Proposed architecture

Working name: aesop-pipe/1. Package: tools/aesop_pipe/ (Python reference) with a Node twin bin/lib/aesop-pipe.js; both stdlib-only and held equal by shared fixtures.

### 5.1 Layers
1. L0 raw bytes: stdin to stdout, untouched. Default. Filters behave as cat does.
2. L1 framing (opt-in): a stream of frames carrying (channel, content_type, bytes).
3. L2 channels: data (opaque payload, any content type) and control (aesop messages).
4. L3 profiles: receipts, events, worker session (definition A), each just a control-message vocabulary.

### 5.2 Protocol candidates considered
- Plain JSONL (NDJSON): trivial, stdlib in both languages; no binary, no content type. Verdict: use inside the control channel and as one data content type.
- Claude Code stream-json: already used by agent hosts Codal may run; defined for Claude events only and details are UNVERIFIED here. Verdict: provide an adapter later (phase 4), not the substrate.
- Hook-style single JSON object: repo precedent and fail-closed rule; one object, not a stream. Verdict: hooks only; reuse the fail-closed rule.
- MCP JSON-RPC over NDJSON: stdlib, existing; request/response shaped and read-only. Verdict: reuse error codes, not the substrate.
- Length-prefixed frames with a text header: binary-safe, incremental, stdlib, trivially parseable; needs a spec and two codecs. Verdict: chosen for L1.

### 5.3 Framing (L1), proposed
Opt-in via --frame (sender) and --accept-frames (receiver). No auto-sniffing: a stream is raw unless told otherwise (fail-closed against misparsing Codal data).

Stream begins with one ASCII line: AESOP-PIPE/1 followed by LF.
Each frame is a header line, payload, terminator:

    F <channel> <content-type> <length>LF
    <length bytes>
    LF

- channel: data or control (extensible tokens [a-z0-9._-]{1,32}).
- content-type: MIME-like token without spaces, "-" for unspecified.
- length: decimal bytes, capped by --max-frame (default 1 MiB; hard error above it). Payload is raw bytes: no escaping, no delimiter collision.
- End of stream: an "E <exit>" frame header with the sender's logical exit code, then EOF. EOF without E is a truncation error (exit 2).
- Anything that does not parse is a framing error: stop, write one diagnostic to stderr, exit 2. Never skip-and-continue (the MCP silent drop at mcp/server.mjs:139-141 is the anti-pattern).

Incremental use: the reader holds one frame at a time (constant memory = --max-frame); the writer flushes per frame. Raw record-delimited streams (lines, NUL, length-prefixed) are handled in L0 by --delimiter line|nul|none so a filter can work record by record without frames.

### 5.4 Control channel (L2)
A control payload is one JSON object (application/vnd.aesop.control+json). Minimal v1 vocabulary: hello {version, caps}, meta {key, value}, event {kind, ts, text, refs}, receipt {receipt, sig}, seal {algo, digest, frames, bytes}, error {code, message}, done {exit}. Unknown type values are ignored only if the object carries "critical": false; otherwise they are an error (fail closed, forward compatible). The event shape mirrors tools/event_store/store.py (kind, actor, text, refs) so events append without remapping.

### 5.5 Exit codes
Align with the repo: 0 success/clean, 1 findings/negative verdict, 2 usage, malformed input, framing error or cannot-evaluate (fail closed). 124 timeout (note the existing overload: a child's own 124 is reported as 1 with a stderr diagnostic, decision D6). 127 spawn failure, matching proc_util. Downstream closed early (EPIPE/SIGPIPE): exit 0 silently when output so far is complete, else 2 (decision D7).

### 5.6 stderr discipline
stderr is diagnostics only: human text by default, --diag json for one JSON object per line. stdout carries payload only. A conformance test fails any tool that prints a diagnostic to stdout in pipe mode.

### 5.7 Backpressure
Blocking reads and writes with bounded buffers. The reader never reads ahead more than one frame (or one record). The writer does a blocking write per frame and propagates EPIPE. No unbounded queue anywhere. Tested with a slow consumer and a memory ceiling (P0-STREAM).

### 5.8 Windows and Linux parity
- Binary mode on stdio: Python sys.stdin.buffer and sys.stdout.buffer; Node raw Buffers. Never text mode: Windows CRLF translation and cp1252 corrupt payload bytes (encoding rule, LANE-CONTRACT s4).
- Header lines are ASCII with LF only, never CRLF, so framing is identical on both OSes.
- Streaming spawn helper (new, not communicate()): reader threads, never wait()-then-read (deadlock when a pipe buffer fills; inference). Python selectors do not support pipes on Windows, so use threads. Tree kill reuses proc_util logic (driver/proc_util.py:41-59).
- Parity is proven by running the same fixture corpus on both OSes; Linux proof via tools/linux_shape_check.py (LANE-CONTRACT s9).

### 5.9 Fail closed on malformed input
Truncated frame, oversized frame, bad length, bad channel token, missing E, invalid JSON on the control channel, unknown critical message: each exits 2 with a stderr diagnostic and no success output beyond already-emitted frames. Empty stdin in framed mode is an error (matches secret_scan.py:1075). Raw mode passes empty through, as cat does.

### 5.10 Receipts and hash-chain integration
- Optional --seal: the tool keeps a running sha256 over the framed stream and, at E, emits a control seal message.
- The seal digest can be signed through the receipts layer by adding a stream-digest field in build_receipt before signing (emit_receipt.py:760, 983). Caution: verify() checks only named fields (verify_receipt.py:194-254), so a new field is signed but unchecked until the verifier is extended (inference). Phase 5 extends the verifier and adds a failing test first.
- Events can be appended to the hash-chained store (tools/event_store/store.py:94) with kind "stdio" and refs {sha256}. That store needs a single writer (no lock found), so pipe tools write through one event-sink process.
- The zero-dependency package only produces the digest and the control message; Ed25519 signing runs in tools/emit_receipt.py in its own process. HMAC (stdlib) is the fallback scheme.

### 5.11 MCP relationship
MCP stays a read-only fleet-status server. The control channel borrows its error codes and NDJSON habits but is not MCP. An MCP-to-pipe bridge is possible later (phase 4). Do not widen mcp/server.mjs to write paths (mcp/CLAUDE.md:109-110).

### 5.12 Adopter contract (definition C)
Documented in phase 3 as docs/guides/stdio-adopter-contract.md:
- Invocation: aesop pipe <stage> [flags]: stdin in, stdout out, exit code, nothing on disk required.
- What Codal sees: raw passthrough by default; with --frame a self-describing stream; --json summaries for gate-style tools; receipts verifiable offline with a public keyring (open question section 8).
- Identity and accounts are handled by the [auth plan](./aesop-auth.md). The pipe carries no credentials in-band. A credential, when needed, is passed as a handle (auth_ref) resolved by the auth layer, never as a data payload or control message; secret_scan stays the gate.
- Everything Codal-specific (CI system, host, transport, formats) is a open question answer, not an assumption.

### 5.13 TTY versus pipe (relationship to the interactive chat)
The same `aesop` binary serves two modes, decided by TTY detection (see [interactive chat plan](./aesop-interactive-chat.md)): when stdout is not a TTY, or a subcommand such as `aesop pipe` is used, aesop is a clean filter obeying this plan (no ANSI, no prompts, no spinners, diagnostics on stderr). When stdin and stdout are both TTYs and no subcommand is given, aesop opens the chat. The chat is a client of the same pipe/event contract, not a separate code path. A test (P0-TTY) asserts that no escape sequence is ever written to a non-TTY stdout.

## 6. How new requirements slot in
Requirements enter at three seams, none requiring redesign: (1) a new content type (a registry row), (2) a new control message type (the vocabulary table with its critical flag), (3) a new profile or adapter (a module that maps an external format to frames). A requirement that cannot be expressed at a seam triggers a design review, not a patch.

## 7. Phased delivery

Lane rule for every unit: Haiku, file-disjoint, TDD-first (failing test committed first in the same PR, proven failing with the fix removed and passing with it restored, per LANE-CONTRACT s2 and s5), reads LANE-CONTRACT.md and exactly one domain CLAUDE.md. Each new test file is declared with its real extension and counted by the runner. New files under tools/ need `python tools/gen_tool_index.py --regenerate` and CLAUDE.md sync (LANE-CONTRACT s4a).

### 7.1 Phase 0: spec and conformance tests (no implementation)
Deliverable: docs/guides/stdio-pipe-spec.md, a language-neutral fixture corpus, and conformance suites that run against a stub that fails every test (red first).

- P0-SPEC owns docs/guides/stdio-pipe-spec.md (doc lint only).
- P0-FIX owns tests/fixtures/pipe/ (fixture manifest self-check).
- P0-FRAME owns tests/test_pipe_frames.py: test_roundtrip_every_content_type, test_binary_payload_all_256_bytes_roundtrip, test_crlf_and_nul_in_payload_unchanged, test_truncated_frame_exits_2, test_oversized_frame_exits_2, test_bad_length_token_exits_2, test_missing_end_frame_exits_2, test_empty_framed_stdin_exits_2, test_unknown_channel_token_exits_2.
- P0-RAW owns tests/test_pipe_passthrough.py: test_raw_mode_is_byte_identical_for_1MiB_random, test_raw_mode_never_parses_json_looking_input, test_no_flag_means_no_frame_interpretation, test_stdout_has_no_diagnostics.
- P0-STREAM owns tests/test_pipe_streaming.py: test_incremental_first_frame_emitted_before_eof, test_memory_bound_constant_for_256MiB_stream, test_slow_consumer_backpressure_no_growth, test_epipe_downstream_close_exit_semantics, test_line_nul_and_none_delimiters.
- P0-CTRL owns tests/test_pipe_control.py: test_control_unknown_critical_message_exits_2, test_control_unknown_noncritical_ignored, test_invalid_json_on_control_exits_2, test_event_shape_matches_event_store_fields, test_seal_digest_matches_sha256_of_stream.
- P0-ZERODEP owns tests/test_pipe_zero_dependency.py and tools/check_pipe_stdlib.py: test_no_non_stdlib_import_in_pipe_package (AST walk against sys.stdlib_module_names), test_no_non_builtin_require_in_node_twin (module.builtinModules), test_no_native_addon_or_package_json_deps, test_gate_fails_on_planted_third_party_import (mutation: plant a third-party import, the gate must go red).
- P0-PARITY owns tests/test_pipe_parity.py and tests/pipe-parity.test.mjs: test_python_and_node_codecs_agree_on_every_fixture, test_header_lines_use_lf_only_on_windows.
- P0-PROC owns tests/test_pipe_spawn.py: test_streaming_spawn_does_not_deadlock_on_64KiB_stderr, test_grandchild_holding_pipe_does_not_hang_past_bound, test_child_exit_124_is_not_reported_as_timeout.
- P0-TTY owns tests/test_pipe_tty_fallback.py: test_no_ansi_on_non_tty_stdout, test_no_args_non_tty_prints_usage_not_chat, test_chat_only_when_stdin_and_stdout_are_ttys (uses a pty on Linux, a fake-TTY shim elsewhere).
- Each suite carries a mutation proof (break codec, break bound, break fail-closed): red output pasted, restored, green pasted.

Zero-dependency CI check: a new step running `python tools/check_pipe_stdlib.py --check`, wired through the gate-inventory process (`python tools/new_gate_check.py`, LANE-CONTRACT s5), which also owns the EXPECTED_GATES counter. The check classifies imports via sys.stdlib_module_names (Python 3.10+; confirm the repo minimum in phase 0, since docs disagree: 3.11 in ADOPTION-CHECKLIST.md:43, 3.10 in ANY-REPO.md:16) and fails closed on anything it cannot classify.

### 7.2 Phase 1: kernel stdio entrypoint
Owns tools/aesop_pipe/ (frames.py, control.py, stream.py, cli.py) and bin/lib/aesop-pipe.js. The single shared edit (a pipe verb in the pythonNamespaces table, bin/cli.js:46) is applied by the lead, not a lane. Goal: phase-0 suites go green via `aesop pipe cat|frame|unframe|seal|verify`. Units: P1-FRAME (frames.py), P1-STREAM (stream.py plus a streaming spawn helper beside, not editing, proc_util), P1-CTRL (control.py), P1-NODE (aesop-pipe.js), P1-CLI (cli.py, help text, tests/cli-runtime-help.test.mjs case). Exit gate: all P0 tests green, executed == declared, zero-dependency gate green.

### 7.3 Phase 2: tool filters
One lane per tool, each owning that tool and its test: secret_scan.py (add --json; D8), wave_preflight.py (streaming roots), generated_paths.py, commit_lint.py, git_integrity_check.py, verify_receipt.py (multi-receipt framed input), conflict_marker_check.py (add stdin), regen_all.py, pr_symbol_survival_check.py. Tests per tool: test_<tool>_reads_stdin_equals_args, test_<tool>_json_stdout_only, test_<tool>_empty_stdin_exit_2, test_<tool>_diagnostics_on_stderr_only. Each lane also passes encoding_lint and regenerates tools/INDEX.md.

### 7.4 Phase 3: adopter contract and docs
Owns docs/guides/stdio-adopter-contract.md, examples/stdio-adopter/ (runnable shell, Python and Node samples validated against bin/cli.js --help per the examples/ invariants), and the docs/README.md entry. Tests: test_examples_run_and_match_golden_output, test_every_documented_flag_exists_in_help. Begins only with open question answers for sections 1, 4 and 5; otherwise ships with the stated defaults flagged as assumptions.

### 7.5 Phase 4 (optional, later)
Named pipes and sockets; Claude stream-json adapter; MCP bridge. Each is a separate profile at seam 3.

### 7.6 Phase 5: receipts and event-store seal
Extend the verifier first (failing test test_verify_rejects_tampered_stream_digest), then the emitter field, then the event-sink process. Needs D5.

### 7.7 Dependency order
P0 all parallel; P1 after the P0 red baseline merges; P2 units parallel after P1; P3 after P1 and open question answers; P5 after P1.

## 8. Risks
1. Terminology: "pipeline" means three things (G5 lanes, stdio pipe, CI pipelines). Mitigation: rule in section 3.
2. Over-design before Codal answers. Mitigation: raw default, minimal frame layer, three seams.
3. Windows pipe hangs (grandchild holding handles; MSBuild node reuse, STATE.md:415). Mitigation: P0-PROC, thread readers, bounded drain.
4. Zero dependency versus Ed25519. Mitigation: sign out of process (D5).
5. Canonical JSON differs from strict JCS (receipt_common.py:40) if Codal verifiers expect RFC 8785. Mitigation: open question; optional strict-JCS in phase 5.
6. Event-store chain forks under concurrent writers (store.py:106, 113-115). Mitigation: single-writer sink.
7. The MCP silent-drop precedent (server.mjs:139-141) and two allow-on-malformed hooks break fail-closed. Mitigation: do not copy; flagged for separate fix lanes.
8. Exit 124 overload and 127 reuse. Mitigation: D6.
9. Python minimum version unknown. Mitigation: verify in phase 0.
10. Codal infra unknown: every infra assumption is a open question row with a default, never a fact. The Codal deck aesop-kernel-codal-20261029-v5.pptx was not found on disk by recon; the demo runbook (skills/demo/SKILL.md) states no Codal infra facts.
11. Text-mode stdio on Windows corrupts bytes. Mitigation: parity tests.
12. Mixing chat output with pipe stdout. Mitigation: P0-TTY.

## 9. Open decisions for Matt
- D1. Confirm the definition: generic framed byte pipeline substrate, B and C first, A last.
- D2. Raw passthrough as the default, frames strictly opt-in (recommended yes).
- D3. Frame format: proposed length-prefixed frames, or JSONL-only if Codal needs no binary (decide after open question section 4).
- D4. Which Codal answers gate phase 3 (proposed: sections 1, 4, 5).
- D5. Signing: out of process (recommended) or admit cryptography as an optional adapter outside the zero-dependency package.
- D6. Exit codes 124/127 semantics for pipe tools.
- D7. Downstream-closed (EPIPE) exit: 0 silent, or 2.
- D8. secret_scan.py structured output: add --json, keep --from-stdin text-only, or both.
- D9. Package location tools/aesop_pipe/ (recommended) or a new top-level pipe/ domain with its own CLAUDE.md.
- D10. Whether credentials may ever ride the control channel (recommended no).

## 10. Discovery cost
Seven Haiku researchers produced the cited briefs (process and pipes, tool conventions, MCP and hook framing, receipts and event store, Codal context and prior art, auth inventory, CLI and model surface). Findings are code reads; no defect was reproduced and none is claimed.
