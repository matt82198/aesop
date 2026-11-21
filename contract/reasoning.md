# STEP: reasoning (plan and design lanes; read together with the floor above)
A plan lane returns its plan in the hand-back and changes no product code.

## 10. Plan-vs-output check (intent decay)
1. At every project checkpoint (and before any release) the orchestrator dispatches a read-only Haiku lane that reads the project's plan/design doc goals (e.g. keyrun design/BIBLE.md, aesop docs/ROADMAP or the STATE.md intent section), the current STATE.md and the shipped artifact, and answers only: does what was built match what this was for? It does not assess whether it works.
2. The verdict is one BUILDLOG line `[intent-check] <project> MATCH|DRIFT: <one sentence>`.
3. DRIFT blocks new feature lanes in that project until the orchestrator records the correction (re-plan or re-scope) in STATE.md.
4. The check names the artifact's status (real/throwaway/oracle) from the PURPOSE pins it finds, and flags any throwaway that has accumulated more than one feature lane.

- **REARCH sections 69+ are orchestrator-reserved.** Claim an unreserved number AT WRITE TIME and
  record it; ask before taking 69+.
