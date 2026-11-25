# Contacting the live Claude orchestrator on Windows

When a lane needs an ownership decision, branch designation or integration SHA, ask the orchestrator that owns the work. Append the durable inbox entry, deliver the question through that session's console input, and read its answer. The inbox's pending marker establishes that the question was queued for a future drain. The session transcript establishes what the orchestrator actually answered. [Foreign-agent contract](../FOREIGN-AGENTS.md)

## 1. Identify the session

From PowerShell, list the registered sessions whose PIDs still belong to Claude. This reads session metadata only.

```powershell
Get-ChildItem "$env:USERPROFILE/.claude/sessions" -Filter '*.json' | ForEach-Object {
    $session = Get-Content -Raw -LiteralPath $_.FullName | ConvertFrom-Json
    $process = Get-Process -Id $session.pid -ErrorAction SilentlyContinue
    if ($process.ProcessName -eq 'claude') {
        $session | Select-Object pid, sessionId, name, cwd, status
    }
}
```

Match a candidate's recent conversation to the project, PR and integration decision it owns. The transcript is the file named `<sessionId>.jsonl` under `~/.claude/projects`. Use `rg --files --hidden --no-ignore -g '<sessionId>.jsonl'` in that directory to locate it. Two sessions can share a working directory. A terminal window title can also describe a different tab. In the observed case, recent messages about the harness merge and the ask integration identified the owner.

## 2. Send one decision request

Include the work item, the user's constraints, the precise decision needed and where the answer should go. For instance, “Codex coordination request for PR 1434. The user requires a feature branch after ask integrates. Which branch will you create, which merge should I watch, and where will you post its SHA? Please reply here.”

Append the request to the inbox using the timestamp and sender format in the contract. To deliver it live, use the selected session's Windows console input buffer. This works with the interactive `claude.exe` console even when the sending shell did not launch it. The example reads the console footer, checks for an empty prompt, and sends one plain-text question followed by Enter. Run it in PowerShell on the same Windows desktop as the orchestrator.

```powershell
$env:AESOP_CONTACT_PID = Read-Host 'Verified Claude PID'
$env:AESOP_CONTACT_SESSION = Read-Host 'Matching session UUID'
$env:AESOP_CONTACT_QUESTION = Read-Host 'One-line coordination question'
$target = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$env:AESOP_CONTACT_PID)"
if ($target.Name -ne 'claude.exe') { throw 'The selected PID is no longer Claude' }
$env:AESOP_CONTACT_PARENT = [string]$target.ParentProcessId
@'
import ctypes as c
import ctypes.wintypes as w
import json
import os
import time
from pathlib import Path

pid = int(os.environ['AESOP_CONTACT_PID'])
session = json.loads((Path.home() / '.claude/sessions' / f'{pid}.json').read_text(encoding='utf-8'))
if session['pid'] != pid or session['sessionId'] != os.environ['AESOP_CONTACT_SESSION']:
    raise SystemExit('Session identity changed. Identify the current owner.')
question = os.environ['AESOP_CONTACT_QUESTION']
if not question.strip() or not question.isascii() or not question.isprintable():
    raise SystemExit('This example sends one printable ASCII question.')
if not question.startswith('Codex coordination request'):
    question = 'Codex coordination request. ' + question

kernel = c.WinDLL('kernel32', use_last_error=True)
kernel.CreateFileW.restype = w.HANDLE

class Coord(c.Structure):
    _fields_ = [('x', w.SHORT), ('y', w.SHORT)]

class Rect(c.Structure):
    _fields_ = [(name, w.SHORT) for name in ('left', 'top', 'right', 'bottom')]

class ScreenInfo(c.Structure):
    _fields_ = [('size', Coord), ('cursor', Coord), ('attr', w.WORD),
                ('window', Rect), ('maximum', Coord)]

class Key(c.Structure):
    _fields_ = [('down', w.BOOL), ('repeat', w.WORD), ('vk', w.WORD),
                ('scan', w.WORD), ('char', w.WCHAR), ('ctrl', w.DWORD)]

class Record(c.Structure):
    _fields_ = [('kind', w.WORD), ('key', Key)]

def checked(ok):
    if not ok:
        raise c.WinError(c.get_last_error())

def console(name):
    handle = kernel.CreateFileW(name, 0xc0000000, 3, None, 3, 0, None)
    if handle == w.HANDLE(-1).value:
        raise c.WinError(c.get_last_error())
    return w.HANDLE(handle)

def write_keys(handle, text):
    events = [Record(1, Key(down, 1, 13 if ch == '\r' else 0, 0, ch, 0))
              for ch in text for down in (True, False)]
    written = w.DWORD()
    checked(kernel.WriteConsoleInputW(handle, (Record * len(events))(*events),
                                      len(events), c.byref(written)))
    if written.value != len(events):
        raise SystemExit('Partial write. Inspect the prompt before taking any action.')

kernel.FreeConsole()
checked(kernel.AttachConsole(pid))
try:
    clients = (w.DWORD * 64)()
    count = kernel.GetConsoleProcessList(clients, len(clients))
    checked(count)
    allowed = {os.getpid(), pid, int(os.environ['AESOP_CONTACT_PARENT'])}
    if count > len(clients) or pid not in clients[:count] or set(clients[:count]) - allowed:
        raise SystemExit('Unexpected console participants. Inspect this session.')
    output = console('CONOUT$')
    info = ScreenInfo()
    checked(kernel.GetConsoleScreenBufferInfo(output, c.byref(info)))
    footer = []
    for y in range(max(info.window.top, info.window.bottom - 11), info.window.bottom + 1):
        buf = c.create_unicode_buffer(info.size.x + 1)
        read = w.DWORD()
        checked(kernel.ReadConsoleOutputCharacterW(output, buf, info.size.x, Coord(0, y), c.byref(read)))
        footer.append(buf.value.strip())
    prompts = [row for row in footer if row.startswith(('>', '\u276f', '\u203a'))]
    if not prompts or prompts[-1] not in ('>', '\u276f', '\u203a'):
        raise SystemExit('No empty Claude composer. Inspect the session before sending.')
    if any(phrase in row.lower() for row in footer
           for phrase in ('allow once', 'yes, allow', 'do you want to proceed', 'allow this tool')):
        raise SystemExit('An approval is on screen. Leave it for its operator.')
    input_handle = console('CONIN$')
    write_keys(input_handle, question)
    time.sleep(0.5)
    write_keys(input_handle, '\r')
finally:
    kernel.FreeConsole()
'@ | python -
```

The console example covers the observed same-user Windows layout. If its session check fails, inspect the named session and its input surface. Keep the durable request available in the inbox.

## 3. Verify the answer and record the handoff

Search that session's subsequent conversation for the request and the **assistant's reply**. A busy box can accept the question before it appears in the transcript. Allow the running turn to finish before considering a resend. A successful console write alone says nothing about the decision.

Record the branch, expected base SHA or integration PR, lane owner and next action in the inbox ledger. Check the actual remote ref with `git ls-remote --heads origin refs/heads/<branch>` and the merge with `gh pr view <number> --json state,mergeCommit`. A promise to create a branch gives the lane a milestone to watch. It does not supply a usable ref.

If the first reply conflicts with an explicit user constraint, restate that constraint to the **same orchestrator** and ask for a compatible route. In the chat example, the original parent branch disappeared after a squash merge. The orchestrator first suggested targeting main. On hearing that the user required a non-main handoff, it designated `integration/chat-ask`, to be cut after the ask integration. It requested the clean fix branch and SHA in the inbox for review. The exact branch and merge order belong to that incident. [Original fix PR](https://github.com/matt82198/aesop/pull/1434) · [Ask integration](https://github.com/matt82198/aesop/pull/1436)
