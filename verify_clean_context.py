#!/usr/bin/env python3
"""Verify that the seated context pack is clean (no leaked verdicts)."""

import json
import sys
from pathlib import Path

# Add driver/ to sys.path
REPO_ROOT = Path(__file__).resolve().parent
DRIVER_DIR = REPO_ROOT / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from context_pack import build_context_pack

# Load the corpus to get item-9 (use neutral corpus for clean test)
corpus_path = REPO_ROOT / "driver" / "decisions" / "shadow" / "corpus-neutral-2026-07-24.jsonl"
corpus_items = {}
if corpus_path.exists():
    with open(corpus_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                corpus_items[obj["id"]] = obj

# Build context pack for item-9
item_9 = corpus_items.get("whitelist-gate-weakening")
if not item_9:
    print("ERROR: item-9 (whitelist-gate-weakening) not found in corpus")
    sys.exit(1)

# Build sources dict (same as seated adjudication)
sources = {
    "state": None,
    "buildlog_tail:50": None,
    "tracker_open": None,
}

# Build evidence dict (same as seated adjudication)
finding_brief = f"""FINDING: {item_9['finding_text']}

Source lens: {item_9['source_lens']}

Please adjudicate this finding."""

evidence_dict = {"finding": finding_brief}
for idx, evidence_text in enumerate(item_9.get("evidence", [])):
    evidence_dict[f"evidence_{idx}"] = evidence_text

# Build context pack
print("Building context pack for item-9 (whitelist-gate-weakening)...")
pack = build_context_pack(
    decision_type="adjudicate_finding",
    sources=sources,
    repo_root=str(REPO_ROOT),
    conductor_root=str(Path.home() / "conductor3"),
    size_cap=32768,
    evidence=evidence_dict,
    evidence_cap=8192,
)

# Pack all content into a single string for leak detection
all_text = json.dumps(pack.content) + json.dumps(pack.evidence) + json.dumps(pack.manifest)

# Check for leaked verdicts/context
leak_patterns = [
    "whitelist-gate",
    "false_positive",
    "seated",
    "adjudication",
    "item.9",
    "item-9",
    "undetermined",
    "real_defect",
]

print("\nVerifying clean context (checking for leaked experiment content)...\n")
found_leaks = []
for pattern in leak_patterns:
    count = all_text.lower().count(pattern.lower())
    if count > 0:
        found_leaks.append((pattern, count))
        print(f"  LEAK FOUND: '{pattern}' appears {count} time(s)")

if found_leaks:
    print(f"\nERROR: Context pack contains {len(found_leaks)} leak pattern(s)!")
    print("\nContext pack sources:")
    for source, content in pack.content.items():
        print(f"\n--- {source} ---")
        if len(content) > 500:
            print(content[:500] + "\n... TRUNCATED ...")
        else:
            print(content)
    sys.exit(1)
else:
    print("[OK] Context pack is CLEAN (no leaked experiment content)\n")
    print("Manifest:")
    for entry in pack.manifest:
        print(f"  {entry['source']}: {entry['size_bytes']} bytes, "
              f"included={entry['included']}, truncated={entry['truncated']}")
    sys.exit(0)
