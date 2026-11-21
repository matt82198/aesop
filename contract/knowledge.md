# STEP: knowledge (recon and read-only lanes; read together with the floor above)
A recon lane changes no file, so its terminal state is the hand-back; the landing rules (sections 7, 8b) do not apply to it.

## 9a. Index-first reading (knowledge tree)
1. Before any grep/rg/find over a brain repo (`~/.claude`, `~/scripts`, `~/conductor3`) or any repo with a
   generated KNOWLEDGE.md, read KNOWLEDGE.md at the root, then the one domain INDEX*.md the task names.
2. Grep is a last resort for a symbol the index does not list. The report says which index was read first.
3. Counting things an index already counts (tests per file, entries per domain) is read from the index,
   never re-grepped. (Matt 2026-10-18: "see how python-pro is grepping for test counts lol, that emphasizes
   the issue".)
4. A lane that finds the index wrong fixes the source header (one derived line) and re-runs
   `python ~/scripts/knowledge_tree.py --write` for that root. It never hand-edits INDEX*.md.

## Recon hand-backs
Recon lanes return their findings in the hand-back message, never as RECON.md or any other report file in the repo (an operator rule denies such writes).
