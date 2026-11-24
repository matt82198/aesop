# Paging orchestrator context at lane finish

In a replay of a real orchestration session, paging completed hand-backs **increased** main-thread tokens admitted per lane finish. The initial receipts were shorter. Reading the reports again cost more than those receipts saved.

## What stays in the thread

Keep the user's goal and constraints, active lane assignments, the next decision, unresolved blockers, and the current PR head for each pending decision. At delivery, a final hand-back that starts with the status word `DONE` can become a receipt containing the report's first line and its page handle. Keep the sender and the surrounding trust notice. A worker's status remains a claim until its evidence is checked. Keep blocked, partial, ambiguous, and review-required reports in full until the orchestrator resolves them.

## What goes on disk

Use a private directory under the configured state root, `context-pages/<session>/`. Write an immutable page before handing its receipt to the thread. On a failed write, keep the full source resident. Keep the index recoverable from the original event identifiers.

| Page | Session-relative location | When to read it |
| --- | --- | --- |
| Hand-back | `handbacks/<delivery-id>.txt` holds the exact final report. Index the latest delivery by lane. | A user asks for the detail, a tool needs the lane's evidence, or a decision depends on something omitted from the first line. An explicit handle selects that version. |
| PR state | `prs/<repo-key>/<pr>/<head>/<observation-id>.json` holds the observed head, checks, review findings, and observation time. | Before acting on a PR, check its current head and checks. If either has changed, record a fresh observation and reassess the decision. |
| Lane result | `lanes/<lane>/<run>/result.json` holds terminal status, the checked-out revision, commands, exit status, and paths to evidence. | A dependent lane needs a result, verification fails, or a claim conflicts with PR state. Retrieve the exact run attached to the hand-back. |

Use opaque, filesystem-safe keys. Keep page bodies and evidence local. If a referenced page is missing, recover it from its original hand-back or run record before acting. After the decision, retain its outcome, head, and page reference in the thread. The detailed report can leave the working context again.

## Paired replay

The benchmark reads the same archived main-thread messages in delivery order for both arms. A finish is a distinct lane with a structured final peer hand-back. Provisional notifications, tool output quoting a report, and sidechain records cannot establish a finish. Redelivery of an event UUID counts once.

* **Baseline** admits each recorded user and assistant message's content in full, including tool inputs and outputs.
* **Paged** substitutes a receipt only for the indented body of an eligible hand-back. A later human message or assistant tool input that explicitly names a paged lane or its handle triggers a read. The replay charges the read request and the exact stored report, including repeated reads. A corrected hand-back supersedes the lane's previous receipt.

The bench counts local `cl100k_base` tokens over compact JSON message content as it enters the thread. It sums admissions and divides by the same finished-lane count in both arms. Repeated prompt transmission, system prompts, cache pricing, and model output changes in response to a receipt are outside this measure. References expressed only in prose can require additional reads in practice. The replay varies hand-back delivery only. PR-state and lane-result pages describe the design for retrieving supporting evidence.

| Measured quantity | Baseline | Paged |
| --- | ---: | ---: |
| Recorded main-thread messages | 622 | 622 |
| Distinct lane finishes | 19 | 19 |
| Tokens admitted | 305,292 | 308,334 |
| Tokens admitted per lane finish (rounded) | 16,068 | 16,228 |

The paged arm replaced 8 reports and charged 7,153 tokens for 11 reads. This session gives no basis for enabling the measured policy by default.

## Reproduce

Use the source JSONL identified by the session in the command. The [results file](../bench/results/orchestrator-context-paging.json) records its byte digest, the local tokenizer version, and the unrounded means. With that tokenizer version installed and its encoding table cached, the measurement runs offline from the repository root.

```sh
python bench/measure_context_per_lane.py --session a80aed77-fcf1-46a2-9646-af3f0d436850 --out bench/results/orchestrator-context-paging.json
```

The default lookup uses the local session archive. Pass `--transcript PATH` to replay a copy elsewhere. Comparing the generated JSON, including its digest, checks that the input and measurements match. The private transcript is required for independent reproduction.
