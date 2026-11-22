#!/usr/bin/env bash
# INDEX: Local non-gating convenience port of check-ps1-syntax.ps1 (CI ps1-syntax-check stays on pwsh + check-ps1-syntax.ps1 AST parser): validates tracked *.ps1 files via PowerShell AST Parser when pwsh/powershell is present and a deterministic Python structural lexer on POSIX hosts without PowerShell; CLI: `bash tools/check-ps1-syntax.sh [REPO_ROOT]`; exit 0=valid, 1=syntax/parse error
set -uo pipefail

repo_root="."
while [ "$#" -gt 0 ]; do
  case "$1" in
    -RepoRoot|--repo-root)
      shift
      repo_root="${1:-.}"
      ;;
    *)
      repo_root="$1"
      ;;
  esac
  shift || true
done

if ! mapfile_out=$(git -C "$repo_root" ls-files '*.ps1' 2>&1); then
  printf 'Error: Failed to list tracked PowerShell files: %s\n' "$mapfile_out" >&2
  exit 1
fi

ps1_files=()
while IFS= read -r line; do
  line="${line%$'\r'}"
  [ -n "$line" ] && ps1_files+=("$line")
done <<< "$mapfile_out"

count="${#ps1_files[@]}"
if [ "$count" -eq 0 ]; then
  printf 'No tracked *.ps1 files found; check passed\n'
  exit 0
fi

printf 'Checking PowerShell syntax for %d file(s)...\n' "$count"

ps_bin=""
if [ "${CHECK_PS1_FORCE_POSIX:-0}" != "1" ]; then
  if command -v pwsh >/dev/null 2>&1; then
    ps_bin="pwsh"
  elif command -v powershell >/dev/null 2>&1; then
    ps_bin="powershell"
  fi
fi

if [ -n "$ps_bin" ]; then
  ps_root="$repo_root"
  if command -v cygpath >/dev/null 2>&1; then
    ps_root="$(cygpath -w "$repo_root")"
  fi
  printf '%s\n' "${ps1_files[@]}" | CHECK_PS1_ROOT="$ps_root" "$ps_bin" -NoProfile -NonInteractive -Command '
    $ErrorActionPreference = "Stop"
    $Root = $env:CHECK_PS1_ROOT
    $Files = @($input | ForEach-Object { [string]$_ } | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" })
    $bad = 0
    foreach ($f in $Files) {
      $full = Join-Path $Root $f
      if (-not (Test-Path -LiteralPath $full)) {
        Write-Warning "File not found (may be deleted): $full"
        continue
      }
      $content = Get-Content -LiteralPath $full -Raw -ErrorAction Stop
      if ($null -eq $content) { $content = "" }
      $tokens = $null
      $errors = $null
      [System.Management.Automation.Language.Parser]::ParseInput($content, [ref]$tokens, [ref]$errors) | Out-Null
      if ($errors -and $errors.Count -gt 0) {
        $bad++
        Write-Host "  FAIL: $f"
        foreach ($e in $errors) {
          Write-Host ("    Line {0}: {1}" -f $e.Extent.StartLineNumber, $e.Message)
        }
      } else {
        Write-Host "  OK: $f"
      }
    }
    Write-Host ""
    if ($bad -gt 0) {
      Write-Host "PowerShell syntax check FAILED: $bad file(s) with errors"
      exit 1
    }
    Write-Host ("PowerShell syntax check PASSED: All {0} file(s) are valid" -f $Files.Count)
    exit 0
  '
  exit $?
fi

py_bin="python3"
command -v python3 >/dev/null 2>&1 || py_bin="python"

"$py_bin" - "$repo_root" "${ps1_files[@]}" <<'PYEOF'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
files = sys.argv[2:]


def check_ps1_text(text: str):
    errors = []
    if "\x00" in text:
        errors.append((1, "NUL byte in PowerShell source"))
        return errors

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    n = len(text)
    i = 0
    line = 1
    stack = []  # (opener_char, line_no)
    pairs = {"{": "}", "(": ")", "[": "]"}
    closing = {"}": "{", ")": "(", "]": "["}

    while i < n:
        ch = text[i]

        if ch == "\n":
            line += 1
            i += 1
            continue

        # Block comment <# ... #>
        if ch == "<" and i + 1 < n and text[i + 1] == "#":
            start_line = line
            i += 2
            closed = False
            while i < n:
                if text[i] == "\n":
                    line += 1
                elif text[i] == "#" and i + 1 < n and text[i + 1] == ">":
                    i += 2
                    closed = True
                    break
                i += 1
            if not closed:
                errors.append((start_line, "unterminated <# ... #> block comment"))
                return errors
            continue

        # Line comment # ...
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue

        # Here-strings @" ... "@ and @' ... '@
        if ch == "@" and i + 1 < n and text[i + 1] in ('"', "'"):
            q = text[i + 1]
            # PowerShell here-string opens only when @"/ @' is followed by optional whitespace + newline
            j = i + 2
            while j < n and text[j] in (" ", "\t"):
                j += 1
            if j < n and text[j] == "\n":
                start_line = line
                line += 1
                i = j + 1
                end_marker = q + "@"
                closed = False
                at_line_start = True
                while i < n:
                    if at_line_start and text.startswith(end_marker, i):
                        i += 2
                        closed = True
                        break
                    if text[i] == "\n":
                        line += 1
                        at_line_start = True
                    else:
                        at_line_start = False
                    i += 1
                if not closed:
                    errors.append((start_line, f"unterminated @{q}...{q}@ here-string"))
                    return errors
                continue

        # Double-quoted string "..." (backtick escapes next character; single-line unless escaped/valid,
        # wait: in PowerShell double-quoted strings CAN span multiple lines, so EOF without closing " is error,
        # AND an unescaped " closes it). Wait: what about broken_quote.ps1 where a single string is never closed?
        # EOF without closing " catches it!
        if ch == '"':
            start_line = line
            i += 1
            closed = False
            while i < n:
                c2 = text[i]
                if c2 == "`":
                    if i + 1 < n and text[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if c2 == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    closed = True
                    break
                if c2 == "\n":
                    line += 1
                i += 1
            if not closed:
                errors.append((start_line, 'unterminated double-quoted string literal'))
                return errors
            continue

        # Single-quoted string '...' ('' escapes literal single quote)
        if ch == "'":
            start_line = line
            i += 1
            closed = False
            while i < n:
                c2 = text[i]
                if c2 == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    closed = True
                    break
                if c2 == "\n":
                    line += 1
                i += 1
            if not closed:
                errors.append((start_line, "unterminated single-quoted string literal"))
                return errors
            continue

        # Backtick outside string (line continuation or escaped char)
        if ch == "`":
            if i + 1 < n and text[i + 1] == "\n":
                line += 1
            i += 2
            continue

        if ch in pairs:
            stack.append((ch, line))
        elif ch in closing:
            want_open = closing[ch]
            if not stack:
                errors.append((line, f"unexpected closing '{ch}'"))
            elif stack[-1][0] != want_open:
                open_ch, open_line = stack.pop()
                errors.append((line, f"mismatched '{ch}' for '{open_ch}' opened on line {open_line}"))
            else:
                stack.pop()

        i += 1

    for open_ch, open_line in stack:
        errors.append((open_line, f"unclosed '{open_ch}' (missing '{pairs[open_ch]}')"))

    return errors


bad = 0
for rel in files:
    full = root / rel
    if not full.exists():
        print(f"WARNING: File not found (may be deleted): {full}")
        continue
    try:
        raw = full.read_bytes()
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        bad += 1
        print(f"  FAIL: {rel}")
        print(f"    Line 1: invalid UTF-8 encoding ({exc})")
        continue

    errs = check_ps1_text(content)
    if errs:
        bad += 1
        print(f"  FAIL: {rel}")
        for ln, msg in errs:
            print(f"    Line {ln}: {msg}")
    else:
        print(f"  OK: {rel}")

print("")
if bad > 0:
    print(f"PowerShell syntax check FAILED: {bad} file(s) with errors")
    sys.exit(1)
print(f"PowerShell syntax check PASSED: All {len(files)} file(s) are valid")
sys.exit(0)
PYEOF
