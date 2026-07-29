#!/usr/bin/env python3
import json

# Task definitions
tasks = []
ground_truths = []

# ft121: GitHub Actions workflow matrix/needs/if semantics
tasks.append({
    "id": "ft121",
    "category": "github_actions_workflow_semantics",
    "match": "regex",
    "prompt": """You are given the following GitHub Actions workflow YAML. Determine exactly which jobs will execute their steps on a single push to main.

```yaml
jobs:
  setup:
    runs-on: ubuntu-latest
    steps:
      - run: echo "setup"

  build:
    needs: setup
    if: failure()
    runs-on: ubuntu-latest
    steps:
      - run: echo "build"

  test:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - run: echo "test"

  cleanup:
    needs: build
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: echo "cleanup"
```

A job is "executing steps" if it runs to completion. A job is "skipped" if its if: condition is false or if a required predecessor was skipped.

First line of your response: exactly JOBS_A_CL / JOBS_A_CL_T / JOBS_A_B_T_CL / JOBS_ALL""",
    "discrimination_rationale": "Tests understanding that if: failure() on build will be false (since setup succeeds), causing build to be skipped. This skips test. But cleanup runs because it has if: always(). The trap: assuming if: on build doesn't affect test."
})

ground_truths.append({
    "id": "ft121",
    "expected_regex": r"(?im)^\s*(?:answer:\s*)?\*{0,2}JOBS_A_CL\b",
    "exemplar": """JOBS_A_CL

Setup runs successfully. Build has if: failure() which is false (since setup succeeded), so build is skipped. Test depends on build with no if: always() override, so test is also skipped. Cleanup has if: always() which ensures it runs regardless. Therefore, exactly setup and cleanup execute steps.""",
    "counter_example": "JOBS_A_B_T_CL\n\nAll jobs might run because the if: condition on build does not stop its dependents."
})

# ft122: Git rebase --onto semantics
tasks.append({
    "id": "ft122",
    "category": "git_rebase_semantics",
    "match": "regex",
    "prompt": """You are given the following git commit graph and file states.

Initial state:
```
Commit A: file.txt = "root"
Commit B: file.txt = "main content"
Commit C (on main, child of B): file.txt = "main version 2"
Commit D (on feature, child of B): file.txt = "feature version 1"
Commit E (on feature, child of D): file.txt = "feature version 2"

main:    A - B - C
feature: A - B - D - E
```

Operation: git checkout feature && git rebase --onto C D

After the rebase, what commits exist and what is the final state?

First line of your response: exactly GRAPH_C_E / GRAPH_C_E_CONFLICT / GRAPH_B_E / GRAPH_C_D_E""",
    "discrimination_rationale": "Tests understanding of rebase --onto. This rebases all commits after D (which is E) onto C. Since both C and D modified file.txt differently, replaying E on C causes a conflict. The trap: thinking D is still in the graph or misunderstanding --onto semantics."
})

ground_truths.append({
    "id": "ft122",
    "expected_regex": r"(?im)^\s*(?:answer:\s*)?\*{0,2}GRAPH_C_E_CONFLICT\b",
    "exemplar": """GRAPH_C_E_CONFLICT

The rebase --onto C D command takes all commits after D (which is E) and replays them on C. The resulting graph is C - E'. However, since D changed file.txt to "feature version 1" and C changed it to "main version 2", when E is replayed on C, git detects a conflict on file.txt. The rebase stops in a conflict state.""",
    "counter_example": "GRAPH_C_E\n\nThe commits would exist without a conflict because both changes can coexist."
})

# ft123: npm semver dependency resolution
tasks.append({
    "id": "ft123",
    "category": "semver_dependency_resolution",
    "match": "regex",
    "prompt": """A project has three dependencies on lodash:
- package-a requires: lodash ^4.17.15
- package-b requires: lodash ~4.17.0
- package-c requires: lodash >=4.17.0 <4.18.0

Available versions: 4.17.11, 4.17.15, 4.17.21, 5.0.0

Using npm's highest-satisfying-version resolution (where ^ means >=X.Y.Z <(X+1).0.0 and ~ means >=X.Y.Z <X.(Y+1).0), which version will be installed?

First line of your response: exactly VERSION_4_17_11 / VERSION_4_17_15 / VERSION_4_17_21 / VERSION_5_0_0""",
    "discrimination_rationale": "Tests semver understanding. ^4.17.15 is >=4.17.15 <5.0.0. ~4.17.0 is >=4.17.0 <4.18.0. Intersection: >=4.17.15 <4.18.0. Highest: 4.17.21. Trap: confusing caret/tilde semantics."
})

ground_truths.append({
    "id": "ft123",
    "expected_regex": r"(?im)^\s*(?:answer:\s*)?\*{0,2}VERSION_4_17_21\b",
    "exemplar": """VERSION_4_17_21

Resolving constraints: ^4.17.15 expands to >=4.17.15 <5.0.0. ~4.17.0 expands to >=4.17.0 <4.18.0. Taking the intersection: >=4.17.15 and <4.18.0. The highest available version is 4.17.21.""",
    "counter_example": "VERSION_4_17_15\n\nWhile 4.17.15 satisfies all constraints, it is not the highest available version in the valid range."
})

# ft124: POSIX sh expansion
tasks.append({
    "id": "ft124",
    "category": "posix_shell_expansion",
    "match": "regex",
    "prompt": """Given the following bash script executed in an empty /tmp directory:

```bash
cd /tmp
rm -f x*.txt 2>/dev/null || true
touch x1.txt x2.txt
shopt -s nullglob
for f in x*.txt; do echo "$f"; done
set -- x*.txt
echo $#
rm -f x*.txt
```

What is the exact stdout output?

First line of your response: exactly OUTPUT_A / OUTPUT_B / OUTPUT_C / OUTPUT_D

where:
- OUTPUT_A: x1.txt and x2.txt on separate lines, then 2
- OUTPUT_B: literal string x*.txt once, then 1
- OUTPUT_C: x1.txt and x2.txt on separate lines, then 0
- OUTPUT_D: no output""",
    "discrimination_rationale": "Tests shell expansion. With nullglob, unmatched globs become empty. But the files exist, so the pattern matches. The for loop echoes x1.txt and x2.txt. set -- expands to two parameters, so $# is 2. Trap: misunderstanding nullglob behavior."
})

ground_truths.append({
    "id": "ft124",
    "expected_regex": r"(?im)^\s*(?:answer:\s*)?\*{0,2}OUTPUT_A\b",
    "exemplar": """OUTPUT_A

The script creates x1.txt and x2.txt. With nullglob, the pattern x*.txt matches both files. The for loop echoes x1.txt and x2.txt on separate lines. Then set -- expands the glob to two parameters, so $# outputs 2. Total output: x1.txt, x2.txt, 2.""",
    "counter_example": "OUTPUT_B\n\nThe glob pattern does not expand to the literal string because the files exist."
})

# ft125: Dockerfile layer caching
tasks.append({
    "id": "ft125",
    "category": "dockerfile_layer_caching",
    "match": "regex",
    "prompt": """You have a Dockerfile:

```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y curl
COPY requirements.txt /app/
RUN pip install -r /app/requirements.txt
COPY . /app/
RUN make -C /app build
CMD ["./start.sh"]
```

Scenario: All layers are cached. You change ONLY a source file (src/main.py). requirements.txt is unchanged.

Which layers are re-executed on rebuild?

First line of your response: exactly LAYERS_5_6 / LAYERS_5_6_7 / LAYERS_1_5_6 / LAYERS_2_3_4_5_6""",
    "discrimination_rationale": "Tests Dockerfile caching. Layers 1-4 are unchanged, so cached. Layer 5 (COPY . /app/) has modified source files, invalidating cache. Layer 6 must re-execute because layer 5 changed. Layer 7 (CMD) is metadata. Trap: thinking CMD invalidates or layer 5 is cached."
})

ground_truths.append({
    "id": "ft125",
    "expected_regex": r"(?im)^\s*(?:answer:\s*)?\*{0,2}LAYERS_5_6\b",
    "exemplar": """LAYERS_5_6

Layers 1-4 have unchanged inputs, so they are cached. Layer 5 is COPY . /app/, and the source context has changed (src/main.py was modified). This invalidates layer 5's cache. Layer 6 (make build) depends on layer 5's output, so it must re-execute. Layer 7 (CMD) is metadata only and does not re-execute.""",
    "counter_example": "LAYERS_1_5_6\n\nLayers 2, 3, 4 are not re-executed because their inputs remain identical."
})

# ft126: YAML merge key precedence
tasks.append({
    "id": "ft126",
    "category": "yaml_merge_key_precedence",
    "match": "regex",
    "prompt": """Given this YAML document (PyYAML safe_load):

```yaml
defaults: &defaults
  timeout: 30
  retries: 3
  max_concurrent: 5

overrides: &overrides
  retries: 10
  max_concurrent: 8

config:
  <<: *defaults
  timeout: 15
  <<: *overrides
```

What is the value of config.retries?

First line of your response: exactly RETRIES_3 / RETRIES_10 / RETRIES_5 / RETRIES_8""",
    "discrimination_rationale": "Tests YAML merge key precedence. Merge keys are processed in order. First << defaults merges in (retries=3). Then timeout:15 overrides timeout. Then << overrides merges in (retries=10, overriding previous). Result: retries=10. Trap: thinking the first merge wins."
})

ground_truths.append({
    "id": "ft126",
    "expected_regex": r"(?im)^\s*(?:answer:\s*)?\*{0,2}RETRIES_10\b",
    "exemplar": """RETRIES_10

PyYAML processes the YAML sequentially. First, << *defaults merges in retries=3. Then, the explicit key timeout: 15 overrides timeout. Finally, << *overrides merges in retries=10, overriding the previous value. The final config.retries is 10.""",
    "counter_example": "RETRIES_3\n\nThe first merged defaults set retries to 3, but the subsequent merge of overrides changes it to 10."
})

# ft127: HTTP exponential backoff retry count
tasks.append({
    "id": "ft127",
    "category": "http_retry_arithmetic",
    "match": "regex",
    "prompt": """An HTTP client uses exponential backoff with:
- Initial delay: 1 second
- Multiplier: 2.0
- Max delay: 60 seconds
- Max elapsed time: 300 seconds

Backoff sequence: 1s, 2s, 4s, 8s, 16s, 32s, 60s, 60s, ...

Retry logic: attempt, fail, wait, retry. If the next attempt would START after 300s, stop.

How many total attempts are made?

First line of your response: exactly ATTEMPTS_9 / ATTEMPTS_10 / ATTEMPTS_11 / ATTEMPTS_12""",
    "discrimination_rationale": "Tests precise arithmetic. Attempts at T=0, 1, 3, 7, 15, 31, 63, 123, 183, 243. Next wait (60s) from T=243 would reach T=303 > 300s, so no attempt. Total: 10 attempts. Trap: off-by-one errors."
})

ground_truths.append({
    "id": "ft127",
    "expected_regex": r"(?im)^\s*(?:answer:\s*)?\*{0,2}ATTEMPTS_10\b",
    "exemplar": """ATTEMPTS_10

Calculating: Attempt 1 at T=0 (wait 1s), 2 at T=1 (wait 2s), 3 at T=3 (wait 4s), 4 at T=7 (wait 8s), 5 at T=15 (wait 16s), 6 at T=31 (wait 32s), 7 at T=63 (wait 60s), 8 at T=123 (wait 60s), 9 at T=183 (wait 60s), 10 at T=243. The next attempt would start at T=303 > 300s, so we stop. Total: 10 attempts.""",
    "counter_example": "ATTEMPTS_9\n\nIf we incorrectly count only through T=183, we miss the 10th attempt at T=243 which still complies with the 300s limit."
})

# ft128: Multi-service log root cause
tasks.append({
    "id": "ft128",
    "category": "log_trace_root_cause",
    "match": "regex",
    "prompt": """Analyze this multi-service log to identify the root cause:

```
[T=100.100] [database] ERROR: Connection pool exhausted, unable to accept new connections
[T=100.150] [api] WARN: Request to database timed out after 2 seconds, falling back to read-only replica
[T=100.200] [cache] ERROR: Cache invalidation event missed, stale data now served to clients
[T=100.250] [database] INFO: Connection pool recovered, accepting connections again
[T=100.300] [api] INFO: Successfully processed 1250 requests from queue
```

The tempting root cause is the cache invalidation issue. The real root cause occurred earlier. Which event is the true root cause?

First line of your response: exactly ROOT_CACHE_STALE / ROOT_DATABASE_POOL / ROOT_API_TIMEOUT / ROOT_QUEUE_BACKLOG""",
    "discrimination_rationale": "Tests causal reasoning. Database exhaustion at T=100.100 caused api timeout at T=100.150, which caused cache to miss invalidation at T=100.200. Cache staleness is a symptom. Trap: mistaking the most visible error for the root cause."
})

ground_truths.append({
    "id": "ft128",
    "expected_regex": r"(?im)^\s*(?:answer:\s*)?\*{0,2}ROOT_DATABASE_POOL\b",
    "exemplar": """ROOT_DATABASE_POOL

Following the causal chain: Database connection pool exhaustion at T=100.100 is the earliest event. This caused the api to time out at T=100.150 when it tried to reach the database. The api timeout prevented the cache from receiving its invalidation event, leading to stale cache at T=100.200. The true root cause is the database connection pool exhaustion, not the cache staleness.""",
    "counter_example": "ROOT_CACHE_STALE\n\nThe cache staleness is the most visible symptom, but it is a downstream effect of the api timeout, which was caused by the database exhaustion."
})

# ft129: Makefile recipe execution
tasks.append({
    "id": "ft129",
    "category": "makefile_recipe_execution",
    "match": "regex",
    "prompt": """You have this Makefile:

```makefile
.PHONY: all clean

all: program

program: main.o utils.o
	gcc -o program main.o utils.o

main.o: main.c
	gcc -c -o main.o main.c

utils.o: utils.c
	gcc -c -o utils.o utils.c

clean:
	rm -f *.o program
```

File state (current time 10:00):
- main.c: 09:50
- utils.c: 09:45
- main.o: does not exist
- utils.o: 09:55
- program: 09:58

You run: make clean && make all

Which recipes are executed?

First line of your response: exactly RECIPES_CLEAN_MAINO_UTILO_PROG / RECIPES_CLEAN_MAINO_PROG / RECIPES_ALL_FOUR / RECIPES_NONE""",
    "discrimination_rationale": "Tests Makefile timestamp logic. Clean deletes all .o and program. Then main.o must rebuild (doesn't exist). utils.o must rebuild (was deleted). program must rebuild (depends on rebuilt files). Total: clean, main.o, utils.o, program. Trap: not accounting for clean deleting everything."
})

ground_truths.append({
    "id": "ft129",
    "expected_regex": r"(?im)^\s*(?:answer:\s*)?\*{0,2}RECIPES_CLEAN_MAINO_UTILO_PROG\b",
    "exemplar": """RECIPES_CLEAN_MAINO_UTILO_PROG

Make clean removes *.o and program. Then make all rebuilds: main.o is deleted, so rebuild from main.c. utils.o is deleted, so rebuild from utils.c. program depends on both rebuilt files, so rebuild. Total recipes: clean, main.o (gcc -c), utils.o (gcc -c), program (gcc -o).""",
    "counter_example": "RECIPES_CLEAN_MAINO_PROG\n\nAfter clean deletes utils.o, it must be rebuilt; skipping it would leave program unable to link."
})

# ft130: Cron day-of-month and day-of-week OR
tasks.append({
    "id": "ft130",
    "category": "cron_scheduling_semantics",
    "match": "regex",
    "prompt": """A crontab entry is:
```
0 9 15 * 5
```

This runs at 09:00 on the 15th of any month OR on any Friday (day 5).

In cron, when BOTH day-of-month and day-of-week are restricted (not *), the job runs when EITHER condition is true.

Calendar: October 2024
- October 15 is a Tuesday
- October 4, 11, 18, 25 are Fridays

How many days in October will this cron job fire?

First line of your response: exactly FIRES_4 / FIRES_5 / FIRES_6 / FIRES_7""",
    "discrimination_rationale": "Tests cron's OR semantics. Fridays: 4, 11, 18, 25 (4 days). 15th: Oct 15 (1 day). Disjoint, so total is 5. Trap: using AND logic (never fires) or forgetting OR semantics."
})

ground_truths.append({
    "id": "ft130",
    "expected_regex": r"(?im)^\s*(?:answer:\s*)?\*{0,2}FIRES_5\b",
    "exemplar": """FIRES_5

The cron entry 0 9 15 * 5 runs at 09:00 when (day-of-month = 15) OR (day-of-week = 5). In October 2024, this fires on: Oct 4 (Friday), Oct 11 (Friday), Oct 15 (15th), Oct 18 (Friday), Oct 25 (Friday). Total: five distinct days.""",
    "counter_example": "FIRES_4\n\nIf we only counted Fridays (Oct 4, 11, 18, 25) and forgot that the 15th also triggers the job, we'd miss one firing."
})

# Write files
with open('tasks_ft121-130.jsonl', 'w', encoding='utf-8') as f:
    for task in tasks:
        f.write(json.dumps(task) + '\n')

with open('gt_ft121-130.jsonl', 'w', encoding='utf-8') as f:
    for gt in ground_truths:
        f.write(json.dumps(gt) + '\n')

print(f"Created {len(tasks)} tasks and {len(ground_truths)} ground truths")
