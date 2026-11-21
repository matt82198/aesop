"""Paired local replay of a frozen operational-triage rule; prints JSONL."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from shadow_adjudication import (
    CorpusItem, OrchestratorDriver, OpenAICompatibleOrchestratorBackend,
    ShadowAdjudicationBackend, build_finding_context_pack,
)

HERE = Path(__file__).resolve().parent
MODEL = "qwen2.5-coder:14b"
SHARED_CASES = (
    "vbs-waitforexit", "dryrun-blocked", "uninstall-exit0", "unc-paths",
    "hardcoded-username", "audit-log-observability", "ps1-syntax-gate",
    "test-hardcoded-path", "fixreview-parents1", "regression-ui-suite",
    "cimergewait-exit0", "vbs-syntax-validity",
)


def shared_cases():
    path = ROOT / "driver/decisions/shadow/corpus-2026-07-23.jsonl"
    source = {row["id"]: row for row in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
    return [
        {**source[key], "expected": source[key]["labels"]["ground_truth"]}
        for key in SHARED_CASES
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--v1", action="store_true", help="Replay the first rubric and its three original cases")
    mode.add_argument("--compact", action="store_true", help="Compare baseline, revised and compact rules on regression and holdout cases")
    mode.add_argument("--baseline-model", metavar="OLLAMA_TAG", help="Run one unassisted local model on the full tier comparison corpus")
    parser.add_argument("--shared-corpus", action="store_true", help="Use the historical findings shared with the archived model scorecards")
    args = parser.parse_args()
    if args.shared_corpus and not args.baseline_model:
        parser.error("--shared-corpus requires --baseline-model")
    cases = json.loads((HERE / "fixtures" / "operational-transfer.json").read_text(encoding="utf-8"))
    rubric_path = ROOT / "driver/decisions/operational-triage.md"
    if args.v1:
        cases = cases[:3]
        rubric_path = HERE / "fixtures/operational-triage-v1.md"
    rubric = rubric_path.read_text(encoding="utf-8")
    rules = {"baseline": "", "candidate": rubric}
    if args.compact or args.baseline_model:
        cases.extend(json.loads((HERE / "fixtures/operational-transfer-holdout.json").read_text(encoding="utf-8")))
    if args.compact:
        rules["compact"] = (HERE / "fixtures/operational-triage-compact.md").read_text(encoding="utf-8")
    model = args.baseline_model or MODEL
    if args.baseline_model:
        cases.extend(json.loads((HERE / "fixtures/operational-tier-corpus.json").read_text(encoding="utf-8")))
    if args.shared_corpus:
        cases = shared_cases()
    for index, case in enumerate(cases):
        item = CorpusItem(case["id"], case["finding_text"], case["source_lens"], "", "", "")
        pack = build_finding_context_pack(item, str(ROOT), str(ROOT))
        arms = ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
        if args.compact:
            ordered = tuple(rules)
            start = index % len(ordered)
            arms = ordered[start:] + ordered[:start]
        if args.baseline_model:
            arms = ("baseline",)
        for arm in arms:
            seat = OpenAICompatibleOrchestratorBackend(
                model=model, base_url="http://127.0.0.1:11434/v1", is_local=True, timeout_s=240,
            )
            backend = ShadowAdjudicationBackend(
                seat, model=model, adjudication_rubric=rules[arm],
            )
            driver = OrchestratorDriver(backend, schema_dir=str(ROOT / "driver"), max_retries=0)
            decision = driver.decide("adjudicate_finding", pack)
            print(json.dumps({
                "case": item.id, "arm": arm, "model": model, "decision": decision,
                "expected": case["expected"], "tokens": seat.get_tokens_spent(),
            }), flush=True)
            if decision["verdict"] == "DECISION_FAILED" and not args.baseline_model:
                raise SystemExit("A call failed. Resolve the infrastructure error before scoring.")


if __name__ == "__main__":
    main()
