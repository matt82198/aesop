# Operational finding triage transfer pilot

**Decision. Keep the rubric in shadow evaluation.** The first revision improved one fresh case. The second corrected the source case and retained that earlier gain. Its two new cases tied the baseline and consumed 264 additional tokens in total.

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

## Replay

With the local Ollama model available, run `python -B bench/operational_transfer.py --v1` for the first round and `python -B bench/operational_transfer.py` for the revision. The raw decisions include citations, schema status and metered tokens. [Round one](operational-transfer-v1.jsonl) and [round two](operational-transfer-v2.jsonl).

On the existing full shadow corpus, the optional `--operational-triage` switch on `tools/shadow_adjudication.py --live` prepends the revised rule and gives the output a distinct suffix. Production adjudication retains its existing configuration.
