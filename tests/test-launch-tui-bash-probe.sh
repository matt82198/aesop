#!/usr/bin/env bash
# Bash test verifying tools/launch_tui.py inspects bash processes via /proc, bash, and ps (never PowerShell).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCH_TUI="$REPO_ROOT/tools/launch_tui.py"

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/launch-tui-bash-test.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

if grep -Eqi 'powershell|Get-CimInstance|Win32_Process' "$LAUNCH_TUI"; then
  printf 'FAIL: tools/launch_tui.py still contains PowerShell process probe references\n'
  exit 1
fi

py_bin="python3"
command -v python3 >/dev/null 2>&1 || py_bin="python"

"$py_bin" - "$REPO_ROOT/tools" "$TMP_ROOT" <<'PYEOF'
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
import launch_tui  # noqa: E402

proc_root = pathlib.Path(sys.argv[2]) / "proc"
p1 = proc_root / "7123"
p1.mkdir(parents=True)
script = str(pathlib.Path(sys.argv[2]) / "run_tui.sh")
(p1 / "cmdline").write_bytes(f"/usr/bin/bash\x00{script}\x00".encode("utf-8"))
(p1 / "winpid").write_text("8844\n", encoding="utf-8")

pid = launch_tui.find_bash_process_by_script(script, proc_root=str(proc_root))
if pid != 8844:
    print(f"FAIL: expected winpid 8844 from /proc scan, got {pid!r}")
    sys.exit(1)
print("PASS: launch_tui.find_bash_process_by_script resolves /proc cmdline + winpid without PowerShell")
PYEOF
