# hooks/ -- Git policy enforcement

## pre-push-policy.sh

**Checks & Exit Contract**:
1. `check_branch_policy()` -- documented pre-push check; exit 1 on violation
2. `check_secret_scan()` -- documented pre-push check; exit 1 on violation
3. `check_metrics()` -- documented pre-push check; exit 1 on violation

## pre-commit-waveguard.sh

Unrelated section; `check_never_documented_here()` must not be attributed above.
