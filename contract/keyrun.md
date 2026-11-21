# APPENDIX: keyrun (read by any lane that touches matt82198/keyrun)

## CI contract (keyrun)
Applies to every lane that pushes to `matt82198/keyrun`. Sources: `game.yml@main` and `tests.yml@main` read
via `gh api repos/matt82198/keyrun/contents/.github/workflows/<f>?ref=main`; keyrun's game gate script (named in keyrun/LANE-CONTRACT.md); keyrun
`CLAUDE.md`, `game/CLAUDE.md`. Line numbers are `game.yml@main` unless stated. Measured 2026-10-21.

**1. Jobs.** Required on main (`gh api repos/matt82198/keyrun/branches/main/protection --jq
'.required_status_checks.contexts'` -> `["test","e2e","game-test","game-gate","game-e2e"]`). Every push to a PR
head re-runs both workflows (`on: pull_request`, tests.yml:3-5, game.yml:5-8); `cancel-in-progress` cancels the
older run (game.yml:18-20). Wall times from `gh api repos/matt82198/keyrun/actions/runs/<id>/jobs --jq '.jobs[]
| "\(.name) \(.started_at) \(.completed_at)"'`, last two green main runs (Game 39605588895 @0f6a8989, Game
39602147154 @6ce74317; Tests 39605588877, 39602147012):

| Job | Runner / timeout | Runs | Wall (run 1, run 2) |
|---|---|---|---|
| `test` (Tests) | ubuntu-latest, no timeout set | Node 24: `npm ci`, `npm run typecheck`, `npm test`, `npm run build` (tests.yml:10-20) | 35s, 43s |
| `e2e` (Tests) | ubuntu-latest, no timeout set | `npm ci`, playwright chromium, `python tools/ci/e2e_wait_ratchet.py`, `npm run test:e2e` (tests.yml:21-31) | 68s, 79s |
| `game-test` | windows-latest, 20 min (:24-25) | `dotnet test game/Keyrun.sln` on 8.0.x (:31-33), then `launch-lock-test.ps1` stub (:35-44, must print `launch-lock-test: PASS`) | 3m38s, 4m16s |
| `game-e2e` | windows-latest, 30 min (:47), per-launch cap `KEYRUN_GODOT_TIMEOUT_SEC=660` (:51), `GODOT_VERSION` 4.7.2 (:14) | Godot download (SHA512-checked, cached) -> `--build-solutions` (:92-97, step timeout 12) -> 9 scene tests -> boot smoke -> export (:218-223, step timeout 12) -> exported-exe smoke -> `E2eAssert.ps1` (:264-266, step timeout 5) | 12m32s, 12m42s |
| `game-gate` | ubuntu-latest, 5 min | `tools/ci/game_gate.py`: a PR touching `game/**` or `tools/godot/**` needs `game-e2e` success; needs both game jobs, `if: always()` | 8s, 5s |
| `balance` (non-required) | not measured here; see keyrun `game.yml` | Fork parity: `Bots.Tests` Category=Balance. Reported, never gates a merge (paragraph below). | not measured |

**balance is reported, not gated.** The `balance` job is not a required check: a red balance never blocks a merge and is never disarmed over. The standing finding (chanter.cadence groupSong dominated by swift; warden.bulwark fortress dominated by mobile) is carried in the checkpoint, not fixed by a lane without a ruling. `dotnet test game/Keyrun.sln` exits 1 while balance is red, so a lane runs the test projects individually (Content, Sim, Bots, Playtest) or reports that sln exit as the balance red.

Whole Game workflow wall: 12m57s and 14m18s (run `createdAt`->`updatedAt`, `gh run list --repo matt82198/keyrun
--branch main --limit 8 --json databaseId,name,conclusion,createdAt,updatedAt,headSha`). Budget a push at about
15 minutes of CI. game.yml lines 3-4 call game-test/game-e2e non-required; the protection query above is the
truth: they ARE required. `gh api repos/matt82198/keyrun/actions/jobs/119240827516 --jq '.steps[]...'` (game-e2e of
run 1) shows where the time goes: HUD scene test 5m32s, Run-end bonus 1m11s, Nameplate declutter 1m08s, Combat
text overlap 55s, `E2eAssert` 52s, `--build-solutions` 50s, export 37s, the other scene tests 3-17s each. A change
that adds ticks, frames or launches to a scene test raises its row; state it (item 6).

**2. Local reproduction before a push** (cd to your worktree; redirect output to scratch, read the summary line):
- tests.yml `test`: in a fresh worktree (no node_modules is carried) run `npm ci --no-audit --no-fund` first, before any vitest run; then `npm run typecheck && npm test && npm run build` (`npx vitest run` = `npm test`).
  `e2e`: `npm run test:e2e` after `npx playwright install chromium` plus `python tools/ci/e2e_wait_ratchet.py`.
- game-test: the keyrun game gate in all-projects mode, command in keyrun/LANE-CONTRACT.md (builds the sln with
  warnings as errors and the Keyrun.Sim banned-API analyzer, then the three xUnit projects; prints
  its PASS line `<gate>: PASS in <n>s`; without all-projects mode it maps the diff to projects and an unmapped path selects all,
  keyrun's gate script lines 33-53,98-116, per keyrun/LANE-CONTRACT.md), then the CI command itself: `C:/Users/Mattt/.dotnet/dotnet.exe test game/Keyrun.sln --nologo`.
  The keyrun game gate is the per-step loop and replaces neither `dotnet test game/Keyrun.sln` nor CI (its script lines 15-16). While `balance` is red, `dotnet test game/Keyrun.sln` exits 1 (item 1): a lane runs the test projects individually (Content, Sim, Bots, Playtest) or reports that sln exit as the balance red, never as its own failure.
- game-e2e scene tests (each needs Godot, runs through the launch lock, prints `<Name>: PASS`; script order is
  the CI order): `game/src/Host/SimDriverSceneTest.ps1` (:99), `HudSceneTest.ps1` (:109), `CombatViewSceneTest.ps1`
  (:119); `game/src/Hud/ScreenSceneTest.ps1` (:129), `ProgressionSaveSceneTest.ps1` (:139),
  `RunEndBonusSceneTest.ps1` (:149), `NameplateDeclutterTest.ps1` (:159), `HelpHintSceneTest.ps1` (:169),
  `CombatTextOverlapTest.ps1` (:179); then boot smoke (:189), export (:218) and `game/src/Host/E2eAssert.ps1` (:264).
  A lane runs the scene tests its change can affect (which file under `game/src/**`, `game/scenes/**`,
  `game/content/**` or the sim feeds which test), one at a time, in the shape claude-config #233 allows (reason
  `scene-test-run`, serialised by the launch lock): ONE `pwsh -File` (or `powershell -File`) of a
  `game/src/**/*SceneTest.ps1` or `*Test.ps1` with plain literal args and an optional redirect into the temp dir, e.g.
  `pwsh -File game/src/Host/HudSceneTest.ps1 > C:/Users/Mattt/AppData/Local/Temp/<lane>-Hud.txt 2>&1` (the script path is
  RELATIVE to the worktree root; an absolute script path or a `*>` redirect is denied by #233's parser, which accepts only
  `>`, `>>`, `1>`, `2>`, `2>&1` into an absolute path under the OS temp dir). Get the worktree root as the shell's working
  directory with a lone `cd C:/Users/Mattt/Desktop/keyrun-wt/<lane>` in its own earlier call (the directory persists
  between calls), never as a `cd &&` prefix; no quotes, no `&`, no `$`, no pipe or chain. The lane reads
  the output file and pastes the `<X>SceneTest: PASS` lines in the hand-back. A fresh worktree needs
  `tools/godot/setup-worktree.ps1` (build plus one-time import) first once keyrun #472 lands; until then the build
  and import commands keyrun `game/CLAUDE.md` names. Captures (`screens.ps1`), raw Godot launches and `--autorun`
  outside a scene script stay the orchestrator's. Capture shape: the `screens.ps1` run is one unchained PowerShell command; its OutDir folder is created in a separate Bash `mkdir -p` call first, never chained with `;`. *Why: 2026-10-21 afternoon the no-scrollback hook refused a chained New-Item + screens.ps1 line.* If the hook still denies a scene run, follow "one compliant retry"
  in "Shell and hook shapes"; the hand-back then lists the scene steps not run and why.

**3. One push per green local state.** A push restarts the whole run from blank (new head sha, both workflows,
the older run cancelled, game.yml:18-20); every job above re-queues and the ~15 min starts over. A fold, a
"fix up" or an unrelated commit pushed to an open PR costs a full cycle and invalidates any
`--match-head-commit` arm. So: finish the change, run every item-2 command that applies, paste the summary lines,
push once. Never push to "see what CI says"; CI is confirmation, not discovery. If the gates cannot all run
locally, the push is still one push and the hand-back names which job was not reproduced and why.

**4. Goldens and pinned fixtures (never loosened, never hand-edited).** These compare against committed
recordings or pinned counts, so a sim, content or host change can redden them with no code error:
- `game/fixtures/input/example-enter-ping.input-log.json` (HUD scene test step K1: the recorded input must equal
  the committed file byte for byte; HudSceneTest.ps1:171-181) and `example-yaw-walk.input-log.json` (same,
  :189-194; the one `startHeading` header line is ignored in the K1 comparison, :217).
- `$ReplayTickDigest = 'b950474eff2d9967'` (E2eAssert.ps1:63, checked :270) and the `game/sim/Keyrun.Sim.Tests/Fixtures/oracle/` traces + manifest
  hash: recordings of the C# sim (game/CLAUDE.md:41-50). Re-record only with
  `ORACLE_WRITE=1 npx vitest run tests/oracle` (ORACLE.md:14) and a stated reason next to the new value.
- `game/sim/Keyrun.Sim.Tests/Encounter/EncounterTraceTests.cs` pins record counts (`Assert.Equal(600, t.Records)`
  :239, `400` :249); `game/bots/Keyrun.Bots.Tests/ForkParityTests.cs` pins fork parity margins (read the file
  for the constants before touching bots, sim rules or talent variants).
Rule: a changed golden is RE-DERIVED by a real run of the producer, and the hand-back states `old -> new` and why
the rule change makes it so. Editing a number, margin, tick or seed until the test passes is a fitted green
(section 3) and the lane FAILED. A failing golden you did not intend to move is a finding: stop and report.

**5. Reading a failure** (foreground, one call each, output to a file):
- `gh pr checks <N> --repo matt82198/keyrun --json name,bucket,link` -> which job, then the job id from the link.
- `gh run view --repo matt82198/keyrun --job <id> --log-failed > <scratch>/fail.txt`, then read the tail.
- per-step timing: `gh api repos/matt82198/keyrun/actions/jobs/<id> --jq '.steps[] | "\(.name) \(.conclusion)
  \(.started_at) \(.completed_at)"'`. A step far over the table in item 1 (or a step that hit its 12/5 min
  timeout) is a hang or a budget breach, not a flake; a 660s launch cap kill reads the same.
- Scene-test output shape: asserted lines first, then one terminal line `<Name>SceneTest: PASS` or
  `<Name>SceneTest: FAIL (n)` (n = failed assertions). The workflow throws if the exit code is nonzero or the
  line `(?m)^<Name>: PASS` is missing (e.g. game.yml:99-107), so read the `FAIL` lines directly above the
  terminal line, not the PowerShell stack. `SMOKE ok` and `E2eAssert: PASS` follow the same rule.
- "Pre-existing flake" is a claim (section 1): attach a rerun and the main run id that shows the same step green.

**6. Hand-back must state about CI:** (a) the local gate lines verbatim (the keyrun gate's `PASS in <n>s` line, dotnet
`Passed!`/`Failed!` totals per project, vitest `Test Files`/`Tests` lines, each scene test's PASS line if run, or
the not-run list with reasons from item 2); (b) the expected `game-e2e` wall time if a scene budget (ticks,
frames, launches, timeout) changed, as old -> new; (c) the PR head sha (`gh pr view <N> --repo matt82198/keyrun
--json headRefOid`) that the `--match-head-commit` arm uses; any push after that sha restarts item 1.

**7. Lane row files (`game/lanes/`).** Every keyrun lane writes its game/lanes row file (`game/lanes/README.md`): exactly two non-empty lines, a lane bullet then a BUILDLOG line, never a PURPOSE line. A PURPOSE line belongs in the brief and the PR body. `tests/lanes/row-files.test.ts` enforces it in the required `test` job. *Why: the hedge made one lane ask.*

**8. Enemy content tree.** Enemy telegraphs, abilities and layout live in the repo-root content tree, `content/data/enemies/*.json`, embedded by `Keyrun.Content.csproj`. `game/content/data` is a different tree. Briefs and lanes name the repo-root path.

**9. Capture log writer.** Only `Driver.HostLine` reaches `demo.log`; `GD.Print` and Console output never do. Any log receipt a capture must show is emitted through `Driver.HostLine`. The view's ANIM/VIEW channel (`GD.Print` in `game/src/View`, e.g. ActorView's flash print) never reaches `demo.log` either; any receipt a capture must show is emitted through `Driver.HostLine` or the sim EVENT list (Run.cs emit list -> SimDriver -> RunLog). *Why: #488 found a `GD.Print` receipt would never appear in the capture log, and a recon could not see the hit flash in the log because it was a `GD.Print`.*
