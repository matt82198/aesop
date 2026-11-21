# Infrastructure worker comparison

## Protocol

Compare all three locally installed Ollama tags on the same sixteen historical infrastructure findings. Give each worker the same brief, adjudication schema and one unassisted call at temperature zero. Report schema or transport failure separately from disagreement. A model's size or brand alone cannot establish a reasoning tier. The local study measures finding classification. The archived study measures whether a patch passed hidden tests.

Eleven local cases appeared in prior pilot runs. Five further briefs cover state-store writes, recovery, and provider support. The case author saw historical project dispositions. One feature commit added adoption tooling to repair an incident that hid 174 accepted events. Its case is labeled from the failed projection, independently of the commit prefix. No local cases carry the false-positive or undetermined label. Do not generalize the results to those classes.

Group cases by the judgment they require before inspecting the three-model matrix:

| Judgment | Cases |
| --- | --- |
| Establish whether a new capability was promised | registration-audit, tracker-latency, shard-timing, merge-contention, journal-latency, burn-projection, task-stream, new-worker-provider |
| Compare a reported value with its authoritative source or consumer contract | wave-duration, timeline-timestamps, status-heartbeat, dashboard-root |
| Trace lost progress, state or durability across steps | backup-cycle, concurrent-store-append, tracker-retry, disk-only-items |

For the archived runs, preserve task, model, repeat and experimental arm. Count every recorded attempt. Separate a scored incorrect patch from a structured-output or submission failure when the record distinguishes them. Task families describe what an item tests, not the internal cause of a model decision. No machinery is promoted by this comparison.

### Shared historical findings

Replay twelve operational findings verbatim from the July adjudication corpus so all three local models can be compared with historical model decisions on the same examples. The selection covers process lifecycle and exit status, portability, diagnostic scope, and refutation of a review finding. The cases were selected retrospectively and include the original audit-log case. Score against the project's recorded disposition. The brief and source lens go to the worker. Corpus evidence, historical answers and labels stay out of the prompt. Exclude enriched scorecards, retain failed blind runs as unavailable for semantic comparison, and disclose harness differences. Agreement measures classification only.

## Archived executable tasks

The four files contain 720 attempts on 12 distinct coding tasks. Each cell below has three repeats per task, for 36 attempts per model and condition. A count is a hidden-test pass. Delivery failures remain in the denominator.

| Archived model | One shot | One shot with reproduction test | Seated one pass | Seated with reproduction and repair |
| --- | ---: | ---: | ---: | ---: |
| GPT-4o mini | 5 | 5 | 13 | 15 |
| Haiku 4.5 | 18 | 30 | 24 | 34 |
| Sonnet 5 | 31 | 36 | 31 | 31 |
| Opus 5 | 31 | 25 | 31 | 31 |
| Fable 5 | 25 | 32 | 23 | 28 |

Three examples show why a total score cannot choose the machinery.

- **Delivering a usable patch.** With the reproduction test in the one-shot prompt, GPT-4o mini passed 5 times, returned 3 applied patches that failed, and returned 28 patches the harness could not apply. Opus had 25 passes, 11 application failures, and zero scored wrong patches in that condition. Measure submission and application before diagnosing code reasoning.
- **Following state across requests.** On the retry/cache interaction (`st12`), seated one-pass Haiku scored 0/3 while Sonnet and Opus each scored 3/3. With reproduction and repair, all three scored 3/3. This identifies a candidate for teaching how to follow state across calls. Several things changed between arms, so the records cannot attribute the gain to a particular prompt instruction.
- **Interpreting a failure record.** The repair archive marked 19 attempts as `refusal`. Their worker verdicts distinguish five explicit API refusals, three tool-input validation failures, and eleven visible-test failures. A single refusal count would attribute three different problems to the model.

In the 12 shared historical findings, Haiku, Sonnet and Opus each reached 10/12. Haiku and Sonnet got 7/7 defects and 0/2 enhancements. Opus got 6/7 defects and 1/2 enhancements. All three got the same 3/3 false positives. The case where Opus gained was task-registration logging. The case where it lost was a scheduled launcher reporting completion before its child finished. An escalation policy based only on the total score would hide both differences.

## Fresh Claude comparison

The API account listed the requested Haiku, Sonnet and Opus 5.5 IDs, but the first Messages request returned HTTP 400 for insufficient credits. Zero metered inference responses were obtained. The historical scores above are the only Claude evidence in this report so far. The prepared trial submits the same sixteen cases three times per model and records usage and served model IDs. Before sending requests it reserves the full run's input and output budget at the verified model rates. [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
