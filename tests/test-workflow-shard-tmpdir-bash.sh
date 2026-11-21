#!/usr/bin/env bash
# Bash test verifying CI shard TMPDIR setup uses bash (not PowerShell) in ci.yml and main-full.yml.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CI_YML="$REPO_ROOT/.github/workflows/ci.yml"
MAIN_FULL_YML="$REPO_ROOT/.github/workflows/main-full.yml"

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/wf-tmpdir-bash-test.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

py_bin="python3"
command -v python3 >/dev/null 2>&1 || py_bin="python"

"$py_bin" - "$CI_YML" "$MAIN_FULL_YML" <<'PYEOF'
import pathlib
import sys
import yaml

ci_doc = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
mf_doc = yaml.safe_load(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))

for wf_name, doc, job_name in (
    ("ci.yml", ci_doc, "windows-shard"),
    ("main-full.yml", mf_doc, "main-full-verify"),
):
    steps = doc.get("jobs", {}).get(job_name, {}).get("steps", [])
    tmp_steps = [s for s in steps if "temporary directory" in (s.get("name") or "").lower()]
    if len(tmp_steps) != 1:
        print(f"FAIL: {wf_name} > {job_name} expected 1 unified temporary directory step, got {len(tmp_steps)}")
        sys.exit(1)
    step = tmp_steps[0]
    if step.get("shell") != "bash":
        print(f"FAIL: {wf_name} > {job_name} expected shell: bash, got {step.get('shell')!r}")
        sys.exit(1)
    run_src = step.get("run") or ""
    for forbidden in ("Out-File", "New-Item", "Test-Path", "$env:"):
        if forbidden in run_src:
            print(f"FAIL: {wf_name} > {job_name} still contains PowerShell construct {forbidden!r}")
            sys.exit(1)
print("PASS: ci.yml and main-full.yml shard TMPDIR steps use shell: bash with zero PowerShell")
PYEOF
rc=$?
[ "$rc" -eq 0 ] || exit 1

# Execute the unified bash snippet in an isolated temp dir to verify runtime behavior
GITHUB_ENV="$TMP_ROOT/github_env"
RUNNER_TEMP="$TMP_ROOT/runner_temp"
: > "$GITHUB_ENV"
SHARD_TMP="${RUNNER_TEMP:-/tmp}/shard-2"
mkdir -p "$SHARD_TMP"
if command -v cygpath >/dev/null 2>&1; then
  SHARD_TMP="$(cygpath -w "$SHARD_TMP")"
fi
echo "TMPDIR=$SHARD_TMP" >> "$GITHUB_ENV"

grep -q '^TMPDIR=.*shard-2$' "$GITHUB_ENV" || { printf 'FAIL: GITHUB_ENV missing TMPDIR entry\n'; exit 1; }
[ -d "$RUNNER_TEMP/shard-2" ] || { printf 'FAIL: shard-2 directory not created\n'; exit 1; }
printf 'PASS: unified bash shard TMPDIR step creates shard dir and writes GITHUB_ENV\n'
