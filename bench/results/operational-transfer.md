# Operational finding triage transfer pilot

**Decision. Keep the rubric in shadow evaluation.** The longer rule correctly classified three enhancements the baseline missed across the full replay, but it also downgraded two real heartbeat defects to undetermined. The compact rule reproduced the baseline's verdicts on all eleven cases and consumed more tokens.

## Source and hypothesis

In the July shadow corpus, item 8 reports that task registration writes to stdout and leaves no persistent audit trail. The project filed it as a refinement. Haiku 4.5 and Sonnet 5 classified it as a real defect. Opus 4.8 classified it as an enhancement. These are historical single-run classifications. The model outputs establish their decisions. They cannot establish the internal cause of an error.

The local 14B baseline repeated the finding itself as evidence for a defect. Its citation establishes that a later investigation is difficult. It supplies no retention promise or consumer that depends on that record. The candidate rubric asks the worker to identify the existing obligation and the concrete failure. A missing or incorrect signal can itself violate that obligation.

## Protocol

The [case manifest](../fixtures/operational-transfer.json) identifies the project decisions used to label the cases. Prompts contain the finding and source lens. Expected verdict, commit reference and prior model decisions stay with the scorer. For each round the rule and case labels were written before inference. The source used the historical finding verbatim. The transfer briefs paraphrase later Aesop changes and were authored by the experimenter who knew the dispositions. The timeline control in round two shares a subsystem with the earlier duration control.

Each arm ran the same locally installed Ollama model `qwen2.5-coder:14b`, tag ID `9ec8897f747e`, through Aesop's adjudication driver with its production verdict schema, temperature zero, a fresh request, and zero retries. The only per-pair prompt difference was the prepended rubric. Arm order alternated by case. One call per cell measures that call's verdict. Historic Claude results and current Qwen results are distinct experiments.

## Verdicts

| Case and role | Expected | Baseline | First rule | Revised rule |
| --- | --- | --- | --- | --- |
| Registration audit, source | Enhancement | Defect | Defect | Enhancement |
| Tracker stage latency, fresh in round one | Enhancement | Defect | Enhancement | Enhancement |
| Unknown duration rendered as `nulls`, round-one control | Defect | Defect | Defect | Defect |
| CI shard runtime balancing, fresh in round two | Enhancement | Enhancement | Unrun | Enhancement |
| Invalid UTC timestamp breaks live timeline, round-two control | Defect | Defect | Unrun | Defect |

Round one used 1,890 baseline tokens and 2,179 candidate tokens. Round two used 3,166 and 3,827 respectively. On the two genuinely new round-two cases both arms scored 2/2. The earlier tracker case has become a regression test. Counting it again as fresh would overstate transfer. The evidence supports further evaluation on independently selected infra cases. It does not establish a fleet-wide quality or cost improvement, or a change in actual dispatch or merge outcomes.

## Compaction trial

Freeze the compact text and six extra cases in Git before inference. Replay the five earlier cases as regressions. Score the six extra cases separately, with an unassisted arm, the revised rule, and the compact rule. Rotate request order and use the same model, evidence, schema, and retry budget. Accept compaction for further shadow use only if it preserves every case the revised rule gets right and uses fewer metered tokens overall. A claim of new transfer also requires a correct decision on a fresh case missed by baseline. No merge follows automatically.

The six cases come from the newest three previously unused fixes and three previously unused features in the base commit's non-merge history matching heartbeat, timing, diagnostics, observability or telemetry in tools, daemons or monitor. Case authors saw the commit dispositions. The heartbeat cases overlap in subject matter. Treat case totals as descriptive.

Commit `a710b838` froze the protocol, texts and labels before the 33 model calls. Each call returned a schema-valid decision with zero retries.

| Arm | Earlier five | Fresh six | Total metered tokens |
| --- | ---: | ---: | ---: |
| Baseline | 3/5 | 5/6 | 6,916 |
| Revised rule | 5/5 | 4/6 | 8,498 |
| Compact rule | 3/5 | 5/6 | 7,360 |

On the six fresh cases, the revised rule recognized a proposed spend forecast as an enhancement. It also abstained on two defects that baseline correctly identified. The CLI reported missing heartbeats and the dashboard reported stale data while the shared conductor had a fresh heartbeat. In both decisions the worker asked for more evidence about expected behavior or downstream impact despite the measurement contradicting its source. This exposes a limit of requiring explicit proof of an operational obligation.

Compaction saved 1,138 tokens relative to the longer rule. It lost the correct source, tracker and forecast decisions and failed the frozen preservation criterion. It also failed to improve on baseline on any fresh case. Replay with `python -B bench/operational_transfer.py --compact`. [All 33 verdicts and citations](operational-transfer-compact.jsonl).

## Replay

With the local Ollama model available, run `python -B bench/operational_transfer.py --v1` for the first round and `python -B bench/operational_transfer.py` for the revision. The raw decisions include citations, schema status and metered tokens. [Round one](operational-transfer-v1.jsonl) and [round two](operational-transfer-v2.jsonl).

On the existing full shadow corpus, the optional `--operational-triage` switch on `tools/shadow_adjudication.py --live` prepends the revised rule and gives the output a distinct suffix. Production adjudication retains its existing configuration.
