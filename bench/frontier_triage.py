"""Run the fixed sixteen-case, three-repeat frontier comparison using the Messages API."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from operational_transfer import CorpusItem, OrchestratorDriver, ROOT, build_finding_context_pack
from anthropic_transport import make_anthropic_transport
from frontier_slice import MODEL_PRICING_USD_PER_MTOK

MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
OUTPUT_LIMIT = 1024
INPUT_ALLOWANCE = 8192
REPEATS = 3


class MessagesSeat:
    def __init__(self, model):
        self.model = model
        self.transport = make_anthropic_transport(timeout_s=90)
        self.usage = {}
        self.served = None

    def decide_call(self, prompt, *, schema):
        if len(prompt.encode("utf-8")) + 1024 > INPUT_ALLOWANCE:
            raise ValueError("Prompt exceeds reserved input allowance")
        response = self.transport({
            "model": self.model, "max_tokens": OUTPUT_LIMIT,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": {"type": "json_schema", "schema": {
                "type": "object", "additionalProperties": False,
                "required": ["verdict", "evidence", "confidence"],
                "properties": {"verdict": schema["properties"]["verdict"],
                               "evidence": {"type": "array", "items": {"type": "string"}},
                               "confidence": {"type": "number"}},
            }}},
        })
        self.served = response["model"]
        self.usage = response["usage"]
        return "".join(block["text"] for block in response["content"] if block["type"] == "text")


def run_cell(cell):
    case, model, repeat = cell
    item = CorpusItem(case["id"], case["finding_text"], case["source_lens"], "", "", "")
    pack = build_finding_context_pack(item, str(ROOT), str(ROOT))
    seat = MessagesSeat(model)
    decision = OrchestratorDriver(seat, schema_dir=str(ROOT / "driver"), max_retries=0).decide("adjudicate_finding", pack)
    if not seat.usage:
        raise RuntimeError(f"{model} {item.id}: {decision['evidence']}")
    return {"case": item.id, "model": model, "served": seat.served, "repeat": repeat,
            "decision": decision, "expected": case["expected"], "usage": seat.usage}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-usd", required=True, type=float)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    cases = []
    for name in ("operational-transfer", "operational-transfer-holdout", "operational-tier-corpus"):
        cases.extend(json.loads((ROOT / f"bench/fixtures/{name}.json").read_text(encoding="utf-8")))
    cells = [(case, model, repeat) for repeat in range(1, REPEATS + 1) for case in cases for model in MODELS]
    reserve = sum((INPUT_ALLOWANCE * MODEL_PRICING_USD_PER_MTOK[m][0] +
                   OUTPUT_LIMIT * MODEL_PRICING_USD_PER_MTOK[m][1]) / 1_000_000 for _, m, _ in cells)
    if not reserve <= args.budget_usd:
        parser.error(f"Reserved maximum ${reserve:.2f} exceeds budget")
    with args.output.open("x", encoding="utf-8") as out:
        # Probe all three seats before scheduling the remaining paid cells.
        for cell in cells[:3]:
            print(json.dumps(run_cell(cell)), file=out, flush=True)
        with ThreadPoolExecutor(max_workers=3) as pool:
            for i in range(3, len(cells), 3):
                for row in pool.map(run_cell, cells[i:i + 3]):
                    print(json.dumps(row), file=out, flush=True)


if __name__ == "__main__":
    main()
