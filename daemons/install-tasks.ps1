param(
    [string]$BashExe = 'C:\Program Files\Git\bin\bash.exe',
    [string]$WatchdogCommand = '',
    [string]$MonitorCommand = '',
    [string]$MergeQueueCommand = '',
    [int]$WatchdogIntervalMinutes = 5,
    [int]$MonitorIntervalMinutes = 20,
    [int]$MergeQueueIntervalMinutes = 5,
    [string]$TaskPrefix = 'Aesop',
    [switch]$Uninstall,
    [switch]$DryRun,
    [switch]$EnableAuditLog,
    [switch]$EnableMergeQueue,
    [switch]$All,
    [switch]$Force
)

# Enable strict error handling
$ErrorActionPreference = 'Stop'

function ConvertTo-PosixPath {
    param([string]$WindowsPath)
    # Convert C:\foo\bar to /c/foo/bar
    # Rejects UNC paths (\\server\share) — error out instead of mangling
    if ($WindowsPath -match '^\\\\') {
        Write-Error "UNC paths are unsupported (got: $WindowsPath). Pass -WatchdogCommand explicitly with a valid path."
        exit 1
    }
    $posixPath = $WindowsPath -replace '\\', '/'
    $posixPath = $posixPath -replace '^([A-Za-z]):', '/$1'
    return $posixPath
}

function Get-WorktreeRoot {
    # Derive worktree root from $PSScriptRoot (daemons/)
    # $PSScriptRoot is C:\...\aesop\daemons
    # Parent is C:\...\aesop
    $daemonsDir = $PSScriptRoot
    $aesopRoot = Split-Path -Parent $daemonsDir
    return $aesopRoot
}

function Get-TaskActionPath {
    param(
        [string]$TaskName
    )

    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            # Return the action's Arguments (plural -- the CIM property name).
            # Execute is always "wscript.exe" for every aesop task, so it can never
            # distinguish one worktree from another; the worktree path lives in Arguments.
            $action = $task.Actions[0]
            if ($action) {
                return $action.Arguments
            }
        }
        return $null
    }
    catch {
        return $null
    }
}

function Build-TaskAction {
    param(
        [string]$RunHiddenVbs,
        [string]$BashExe,
        [string]$Command
    )

    # Build the action: wscript.exe //B //Nologo "path\to\run-hidden.vbs" "<bash>" -lc "<command>"
    return New-ScheduledTaskAction `
        -Execute 'wscript.exe' `
        -Argument "//B //Nologo ""$RunHiddenVbs"" ""$BashExe"" -lc ""$Command"""
}

function Append-AuditLog {
    param(
        [string]$AesopRoot,
        [string]$Action,
        [string]$TaskName,
        [string]$Outcome
    )

    # Only write if audit logging is enabled
    if (-not $EnableAuditLog) {
        return
    }

    # Build audit log path: $aesopRoot/state/install-tasks-audit.log
    $auditLogDir = Join-Path $AesopRoot 'state'
    $auditLogPath = Join-Path $auditLogDir 'install-tasks-audit.log'

    # Generate ISO-8601 timestamp
    $timestamp = (Get-Date).ToUniversalTime().ToString('o')

    # Build audit line: timestamp|action|taskname|outcome
    $auditLine = "$timestamp|$Action|$TaskName|$Outcome"

    # Attempt to write the log entry; swallow errors (never block the caller)
    try {
        # Create state directory if it doesn't exist
        if (-not (Test-Path $auditLogDir -PathType Container)) {
            New-Item -ItemType Directory -Path $auditLogDir -Force | Out-Null
        }

        # Append the audit line (create or append)
        Add-Content -Path $auditLogPath -Value $auditLine -ErrorAction Stop
    }
    catch {
        # Log-write failures are swallowed; never block installation
        # (silent failure is intentional per requirements)
    }
}

function Register-DaemonTask {
    param(
        [string]$TaskName,
        [string]$Command,
        [int]$IntervalMinutes,
        [string]$RunHiddenVbs,
        [string]$BashExe,
        [string]$AesopRoot
    )

    # Check if task already exists
    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

    if ($existingTask -and -not $Force) {
        # Task exists. Check if it has the same action path.
        # For idempotency, we compare the bash command (not the full wscript invocation).
        # If the path differs, warn and skip (don't re-register).
        # NOTE: the CIM action object exposes 'Arguments' (plural). Reading 'Argument'
        # (the New-ScheduledTaskAction *parameter* name) yields $null, which would make
        # every existing task look divergent and render the idempotent branch unreachable.

        # Extract the command from existing task's action
        $existingAction = $existingTask.Actions[0]
        if ($existingAction) {
            $existingArgument = $existingAction.Arguments

            # Check if our desired command is already in the arguments
            # The format is: //B //Nologo "<path>" "<bashexe>" -lc "<command>"
            if ($existingArgument -match [regex]::Escape($Command)) {
                # Existing task has the same command; idempotent
                Write-Host "Task already exists with same action: $TaskName (idempotent)"
                Append-AuditLog -AesopRoot $AesopRoot -Action 'register' -TaskName $TaskName -Outcome 'idempotent'
                return
            } else {
                # Existing task has a different action path; warn and skip
                Write-Host "WARNING: Task '$TaskName' already exists with a DIFFERENT action path." -ForegroundColor Yellow
                Write-Host "  Existing path in action: $existingArgument" -ForegroundColor Yellow
                Write-Host "  This invocation would have used: $Command" -ForegroundColor Yellow
                Write-Host "  NOT re-registering. If you want to force overwrite, pass -Force." -ForegroundColor Yellow
                Append-AuditLog -AesopRoot $AesopRoot -Action 'register' -TaskName $TaskName -Outcome 'divergent-skipped'
                return
            }
        }
    }

    # Task doesn't exist, or force-overwrite was requested; proceed with registration
    $action = Build-TaskAction -RunHiddenVbs $RunHiddenVbs -BashExe $BashExe -Command $Command

    # Build the trigger: Once, starting in 1 minute, repeating every N minutes for 10 years
    $startTime = (Get-Date).AddMinutes(1)
    $trigger = New-ScheduledTaskTrigger `
        -Once `
        -At $startTime `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    # Build the settings: Hidden, IgnoreNew for multiple instances, 1-hour timeout
    $settings = New-ScheduledTaskSettingsSet `
        -Hidden `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -StartWhenAvailable

    if ($DryRun) {
        # Print DryRun output
        Write-Host "DRYRUN: $TaskName -> wscript.exe //B //Nologo ""$RunHiddenVbs"" ""$BashExe"" -lc ""$Command"" (interval=$IntervalMinutes`m, Hidden=True)"
        # Still log dry-run registrations if enabled
        Append-AuditLog -AesopRoot $AesopRoot -Action 'register' -TaskName $TaskName -Outcome 'dryrun'
    }
    else {
        # Register the task (force overwrite if -Force is passed, or if task didn't exist)
        try {
            Register-ScheduledTask `
                -TaskName $TaskName `
                -Action $action `
                -Trigger $trigger `
                -Settings $settings `
                -Force `
                -ErrorAction Stop | Out-Null
            Write-Host "Registered task: $TaskName (interval=$IntervalMinutes minutes)"
            # Log the successful registration
            Append-AuditLog -AesopRoot $AesopRoot -Action 'register' -TaskName $TaskName -Outcome 'success'
        }
        catch {
            Write-Error "Failed to register task $TaskName : $_"
            # Log the failed registration
            Append-AuditLog -AesopRoot $AesopRoot -Action 'register' -TaskName $TaskName -Outcome "error: $_"
            exit 1
        }
    }
}

function Unregister-DaemonTask {
    param(
        [string]$TaskName,
        [string]$AesopRoot
    )

    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
            Write-Host "Unregistered task: $TaskName"
            # Log the successful unregistration
            Append-AuditLog -AesopRoot $AesopRoot -Action 'unregister' -TaskName $TaskName -Outcome 'success'
            return $true
        }
        else {
            Write-Host "Task not found: $TaskName (already unregistered or never existed)"
            # Log the not-found case
            Append-AuditLog -AesopRoot $AesopRoot -Action 'unregister' -TaskName $TaskName -Outcome 'not-found'
            return $true
        }
    }
    catch {
        Write-Error "Failed to unregister $TaskName : $_"
        # Log the failed unregistration
        Append-AuditLog -AesopRoot $AesopRoot -Action 'unregister' -TaskName $TaskName -Outcome "error: $_"
        return $false
    }
}

function Main {
    # Resolve paths
    $aesopRoot = Get-WorktreeRoot
    $runHiddenVbs = Join-Path $PSScriptRoot 'run-hidden.vbs'

    # VALIDATION: Check for double quotes in commands (contract violation)
    # This check runs early and always, before any operations
    if ($WatchdogCommand -like '*"*') {
        Write-Error "WatchdogCommand contains double quotes, which are not allowed (vbs launcher contract violation)."
        exit 1
    }
    if ($MonitorCommand -like '*"*') {
        Write-Error "MonitorCommand contains double quotes, which are not allowed (vbs launcher contract violation)."
        exit 1
    }
    if ($MergeQueueCommand -like '*"*') {
        Write-Error "MergeQueueCommand contains double quotes, which are not allowed (vbs launcher contract violation)."
        exit 1
    }

    # PATH VALIDATION: Only enforce file existence checks if not in DryRun mode
    # In DryRun, downgrade to warnings so preview works on machines without Git Bash
    if (-not $DryRun) {
        if (-not (Test-Path $runHiddenVbs)) {
            Write-Error "run-hidden.vbs not found at: $runHiddenVbs"
            exit 1
        }
        if (-not (Test-Path $BashExe)) {
            Write-Error "bash.exe not found at: $BashExe"
            exit 1
        }
    }
    else {
        if (-not (Test-Path $runHiddenVbs)) {
            Write-Warning "run-hidden.vbs not found at: $runHiddenVbs (DryRun mode)"
        }
        if (-not (Test-Path $BashExe)) {
            Write-Warning "bash.exe not found at: $BashExe (DryRun mode)"
        }
    }

    # Handle Uninstall mode
    if ($Uninstall) {
        $watchdog_ok = Unregister-DaemonTask -TaskName "${TaskPrefix}WatchdogDaemon" -AesopRoot $aesopRoot
        $monitor_ok = Unregister-DaemonTask -TaskName "${TaskPrefix}RefinementMonitor" -AesopRoot $aesopRoot
        $mergequeue_ok = Unregister-DaemonTask -TaskName "${TaskPrefix}MergeQueue" -AesopRoot $aesopRoot
        if (-not $watchdog_ok -or -not $monitor_ok -or -not $mergequeue_ok) {
            exit 1
        }
        exit 0
    }

    # Determine which tasks should be managed in this invocation (scoping).
    # Default: manage watchdog only.
    # If -All: manage all three tasks.
    # If -EnableMergeQueue and/or -MonitorCommand: manage only those.
    # Requirement: "-EnableMergeQueue registers ONLY AesopMergeQueue, nothing else"

    $manageTasks = @()

    if ($All) {
        # -All: manage all three tasks
        $manageTasks = @('watchdog', 'monitor', 'mergequeue')
    }
    elseif ($EnableMergeQueue -or $MonitorCommand) {
        # If any scoping flags are passed, only manage those
        if ($EnableMergeQueue) {
            $manageTasks += 'mergequeue'
        }
        if ($MonitorCommand) {
            $manageTasks += 'monitor'
        }
    }
    else {
        # Default (no scoping flags): manage watchdog only
        $manageTasks = @('watchdog')
    }

    # Derive default commands if not provided (for tasks being managed)
    if ($manageTasks -contains 'watchdog' -and -not $WatchdogCommand) {
        $posixRoot = ConvertTo-PosixPath $aesopRoot

        # P2: Detect apostrophe in derived path (breaks bash syntax if not escaped)
        if ($posixRoot -like "*'*") {
            Write-Error "Repository path contains apostrophe, which would break the derived command: $posixRoot`nPass -WatchdogCommand explicitly."
            exit 1
        }

        $WatchdogCommand = "bash '$posixRoot/daemons/run-watchdog.sh' --once >> '$posixRoot/state/cron-watchdog.log' 2>&1"
    }

    # Register watchdog task if in scope
    if ($manageTasks -contains 'watchdog') {
        $watchdogTaskName = "${TaskPrefix}WatchdogDaemon"
        Register-DaemonTask `
            -TaskName $watchdogTaskName `
            -Command $WatchdogCommand `
            -IntervalMinutes $WatchdogIntervalMinutes `
            -RunHiddenVbs $runHiddenVbs `
            -BashExe $BashExe `
            -AesopRoot $aesopRoot
    }

    # Register monitor task if in scope and command provided
    if ($manageTasks -contains 'monitor') {
        if ($MonitorCommand) {
            $monitorTaskName = "${TaskPrefix}RefinementMonitor"
            Register-DaemonTask `
                -TaskName $monitorTaskName `
                -Command $MonitorCommand `
                -IntervalMinutes $MonitorIntervalMinutes `
                -RunHiddenVbs $runHiddenVbs `
                -BashExe $BashExe `
                -AesopRoot $aesopRoot
        }
    }

    # Register the merge-queue task if in scope. OPT-IN: this task merges to main with
    # no interactive session, so it is never switched on as a side effect of
    # running the installer. Pass -EnableMergeQueue (derives the command) or
    # -MergeQueueCommand (explicit).
    if ($manageTasks -contains 'mergequeue') {
        if (-not $MergeQueueCommand) {
            $posixRootMq = ConvertTo-PosixPath $aesopRoot

            # Same apostrophe guard as the watchdog derivation: an apostrophe in
            # the path would break the single-quoted bash command.
            if ($posixRootMq -like "*'*") {
                Write-Error "Repository path contains apostrophe, which would break the derived command: $posixRootMq`nPass -MergeQueueCommand explicitly."
                exit 1
            }

            $MergeQueueCommand = "bash '$posixRootMq/daemons/run-merge-queue.sh' --once >> '$posixRootMq/state/cron-merge-queue.log' 2>&1"
        }

        $mergeQueueTaskName = "${TaskPrefix}MergeQueue"
        Register-DaemonTask `
            -TaskName $mergeQueueTaskName `
            -Command $MergeQueueCommand `
            -IntervalMinutes $MergeQueueIntervalMinutes `
            -RunHiddenVbs $runHiddenVbs `
            -BashExe $BashExe `
            -AesopRoot $aesopRoot
    }

    exit 0
}

Main
