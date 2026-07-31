# Demo Clips

Two short clips cut from a 61-minute recording of a live autonomous session
(`Animation.gif`, 948x1094, 33,289 frames).

Both are **sped up** so the terminal reads as continuous activity. The raw recording
is a TUI that repaints in discrete bursts roughly every 2-3 seconds and is otherwise
a still frame; the speed-up removes that dead air without cutting content. Playback
rate is stated per clip.

Each clip is offered as `.mp4` (948x1094, native resolution) and `.gif`
(700x808, 7 fps fallback).

## Clips

### parallel-audit-lenses

**Source:** 13:07.8-13:33.5 of the recording (25.7s) played at 2.15x -> 12.1s
**Files:** `parallel-audit-lenses.mp4` (609,835 B) - `parallel-audit-lenses.gif` (1,304,111 B)

Five audit-lens agents running concurrently while the backlog fleet lands its work.
Over the clip:

- both feature branches push, then `Agent "Merge train integration branch" finished - 5m 40s`
- all three backlog agents report done; the merge-train agent **landed 25 new tests
  (41 total passing)**, and the integration branch is pushed
- `Agent "Correctness audit lens" finished - 3m 18s` -> the lens **finds a P1**
  (`compact_claims()` snapshot integrity), and rather than trusting it the orchestrator
  spawns a *Verify compact_claims P1 bug* agent for adversarial confirmation
- that verification agent's tool calls accumulate live: `+3` -> `+5` -> `+8` -> `+9 tool uses`
- the agent panel at the bottom holds five concurrent lenses (Security, Test/CI integrity,
  Architecture doc-drift, Shippability, Verify P1) with **every timer and token counter
  climbing in every frame** - 109.6k -> 113.1k, 34.0k -> 38.9k, and so on

The tail includes a real `API error - Retrying in 0s - attempt 1/10`, which is left in:
it is what the retry path actually looks like.

### lens-fanout-haiku-pin

**Source:** 8:19.6-8:51.0 of the recording (31.4s) played at 3.0x -> 10.5s
**Files:** `lens-fanout-haiku-pin.mp4` (478,128 B) - `lens-fanout-haiku-pin.gif` (897,205 B)

A wave opening: delta detection sizing the work, then the audit fleet fanning out, with
the dispatch-policy hook visibly rewriting each agent's model.

- `427 commits / 528 files changed since last audit SHA (ff4c706). That's a full-audit trigger.`
- `Big delta - 523 meaningful files across bench(144), tests(134), tools(79), ui(55),
  docs(39), driver(11). Running 5 key audit lenses in parallel, scoped to this delta.
  The 3 backlog agents are still working alongside.`
- the launch list grows in-frame: 2 agents -> 3 agents -> `Running 4 agents...`
  (Security / Correctness / Test/CI integrity / Architecture doc-drift)
- for **each** spawn the guardrail fires and is echoed on screen:
  `PreToolUse:Agent says: subagent model -> haiku - non-specialist dispatch (was: inherit)`
  - the "subagents are always Haiku" rule enforced as code, not prose
- the agent panel grows from 3 rows to 6 (`1 more`) as the lenses come up, token counters
  ticking from 0 as each new agent starts drawing context

## Why only two

A motion profile was computed over all 33,289 frames (per-frame inter-frame difference).
The recording is **static ~90% of the time** <!-- metrics-verified: ffmpeg tblend=difference + signalstats YAVG over all 33,289 source frames --> - median frame delta 0.006,
p95 0.046, versus p99 10.5 for an actual repaint.
 There is no window anywhere in the hour with continuously
fast motion; the best that exists is a burst every 2-3 seconds. These two windows are the
densest sustained stretches in the recording (max dead-air gap 4.0s and 5.9s respectively
before speed-up), and they are also the two that are legible and on-message. Padding the
set with weaker windows would mean shipping stills with a ticking timer, which is exactly
what the previous set was.

Windows that were dense but **not** shipped:

- **20:48** - three PRs created (#594/#595/#597), a stalled agent detected, and the
  tail-takeover rule stopping it. Excellent content, but an absolute local path is on
  screen for ~6 seconds of the window and cannot be cropped out without breaking the
  layout.
- **3:12** - `/power` prime -> backlog -> "Dispatching 3 parallel Haiku agents in
  worktrees". Good narrative, but only one agent is actually in flight, and the
  pending-decisions line names unrelated private repos.
- **37:22**, **51:19**, **52:47** - high measured delta, but the motion turns out to be
  elapsed-time counters ticking under a horizontally-panned or modal-covered view.

## Total weight

2 clips, 4 files, 3,289,279 B (3.14 MiB) - replacing 10 files at 3,215,049 B.
