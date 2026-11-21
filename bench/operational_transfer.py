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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1", action="store_true", help="Replay the first rubric and its three original cases")
    args = parser.parse_args()
    cases = json.loads((HERE / "fixtures" / "operational-transfer.json").read_text(encoding="utf-8"))
    rubric_path = ROOT / "driver/decisions/operational-triage.md"
    if args.v1:
        cases = cases[:3]
        rubric_path = HERE / "fixtures/operational-triage-v1.md"
    rubric = rubric_path.read_text(encoding="utf-8")
    for index, case in enumerate(cases):
        item = CorpusItem(case["id"], case["finding_text"], case["source_lens"], "", "", "")
        pack = build_finding_context_pack(item, str(ROOT), str(ROOT))
        arms = ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
        for arm in arms:
            seat = OpenAICompatibleOrchestratorBackend(
                model=MODEL, base_url="http://127.0.0.1:11434/v1", is_local=True, timeout_s=240,
            )
            backend = ShadowAdjudicationBackend(
                seat, model=MODEL, adjudication_rubric=rubric if arm == "candidate" else "",
            )
            driver = OrchestratorDriver(backend, schema_dir=str(ROOT / "driver"), max_retries=0)
            decision = driver.decide("adjudicate_finding", pack)
            print(json.dumps({
                "case": item.id, "arm": arm, "model": MODEL, "decision": decision,
                "expected": case["expected"], "tokens": seat.get_tokens_spent(),
            }), flush=True)
            if decision["verdict"] == "DECISION_FAILED":
                raise SystemExit("A call failed. Resolve the infrastructure error before scoring.")


if __name__ == "__main__":
    main()
