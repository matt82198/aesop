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
    [switch]$Plan,
    [string]$ExistingTasksJson = '',
    [switch]$EnableAuditLog,
    [switch]$EnableMergeQueue,
    [switch]$All,
    [switch]$Force
)

# Enable strict error handling
$ErrorActionPreference = 'Stop'

# Repetition duration every aesop trigger is registered with (compared on reinstall too).
$TriggerDurationDays = 3650

# Every Aesop task lives in the ROOT folder. Get-ScheduledTask -TaskName matches a name in
# EVERY folder when no -TaskPath is given, so the disabled \Aesop\<name> copy would be returned
# alongside the root task. All scheduler calls therefore pass $TaskFolder explicitly.
$TaskFolder = '\'

# -Plan computes what the installer WOULD do from -ExistingTasksJson and prints it as JSON.
# -Plan never calls a scheduler cmdlet. The real path is the only code that does.
# AESOP_INSTALL_TASKS_DRY_RUN=1 makes the installer refuse the real path (registration or
# unregistration); -Plan and -DryRun remain allowed under it.

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

function Get-HiddenLauncherPath {
    # daemons\bin\run-hidden.exe is the task action for every bash command. It is a GUI-subsystem
    # program that starts bash with CreateNoWindow, so no console window is ever shown.
    return (Join-Path $PSScriptRoot 'bin\run-hidden.exe')
}

function Ensure-HiddenLauncher {
    # Compiles daemons\bin\run-hidden.exe from run-hidden.cs with the .NET Framework C# compiler
    # when it is missing or older than its source. Fail closed: any failure exits 1 before a
    # task is touched, so no task ever points at a missing launcher.
    $source = Join-Path $PSScriptRoot 'run-hidden.cs'
    $exe = Get-HiddenLauncherPath
    if (-not (Test-Path -LiteralPath $source)) {
        Write-Error "hidden launcher source not found: $source"
        exit 1
    }
    if (Test-Path -LiteralPath $exe) {
        $exeTime = (Get-Item -LiteralPath $exe).LastWriteTime
        $srcTime = (Get-Item -LiteralPath $source).LastWriteTime
        if ($exeTime -ge $srcTime) {
            return $exe
        }
    }
    $csc = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not (Test-Path -LiteralPath $csc)) {
        Write-Error "cannot build hidden launcher: csc.exe not found at $csc"
        exit 1
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $exe) | Out-Null
    & $csc /nologo /target:winexe "/out:$exe" $source | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exe)) {
        Write-Error "failed to build hidden launcher from $source (csc exit $LASTEXITCODE)"
        exit 1
    }
    return $exe
}

function ConvertTo-TaskTimeSpan {
    param(
        $Value
    )

    # CIM reports repetition Interval/Duration as ISO-8601 duration strings (PT1H, P3650D).
    if ($null -eq $Value) { return $null }
    if ($Value -is [TimeSpan]) { return $Value }
    try {
        return [System.Xml.XmlConvert]::ToTimeSpan([string]$Value)
    }
    catch {
        return $null
    }
}

function ConvertTo-WholeMinutes {
    # Whole minutes of an ISO-8601 duration, or -1 when absent, unparseable, or not a whole
    # minute count. -1 never equals a declared spec value, so it always diverges.
    param(
        $Value
    )

    $span = ConvertTo-TaskTimeSpan -Value $Value
    if ($null -eq $span) { return -1 }
    if ($span.TotalMinutes -ne [math]::Floor($span.TotalMinutes)) { return -1 }
    return [int]$span.TotalMinutes
}

function Get-TaskSpec {
    # The declared spec for one task. Execute and Arguments are what the scheduler runs;
    # LogonType and IntervalMinutes are what the installer sets. Plan and the real path
    # both compare existing tasks against this same object.
    param(
        [string]$Command,
        [int]$IntervalMinutes
    )

    return [pscustomobject]@{
        Execute         = Get-HiddenLauncherPath
        Arguments       = '"' + $BashExe + '" -lc "' + $Command + '"'
        LogonType       = 'Interactive'
        IntervalMinutes = $IntervalMinutes
        DurationMinutes = $TriggerDurationDays * 1440
    }
}

function ConvertTo-TaskRecord {
    # Normalises a live Get-ScheduledTask object into the same record shape that
    # -ExistingTasksJson fixtures use.
    param($Task)

    $action = $Task.Actions | Select-Object -First 1
    $trigger = $Task.Triggers | Select-Object -First 1
    $interval = -1
    $duration = -1
    if ($trigger -and $trigger.Repetition) {
        $interval = ConvertTo-WholeMinutes -Value $trigger.Repetition.Interval
        $duration = ConvertTo-WholeMinutes -Value $trigger.Repetition.Duration
    }
    return [pscustomobject]@{
        TaskName        = [string]$Task.TaskName
        TaskPath        = [string]$Task.TaskPath
        Execute         = [string]$action.Execute
        Arguments       = [string]$action.Arguments
        LogonType       = [string]$Task.Principal.LogonType
        IntervalMinutes = $interval
        DurationMinutes = $duration
    }
}

function Get-TaskDivergence {
    # Names of the spec fields that differ from an existing record (D45). Empty = converged.
    param(
        $Existing,
        $Spec
    )

    $diff = @()
    if ([string]$Existing.Execute -ne [string]$Spec.Execute) {
        $diff += 'action.Execute'
    }
    if ([string]$Existing.Arguments -ne [string]$Spec.Arguments) {
        $diff += 'action.Arguments'
    }
    if ([string]$Existing.LogonType -ne [string]$Spec.LogonType) {
        $diff += 'principal.LogonType'
    }
    if ([string]$Existing.IntervalMinutes -ne [string]$Spec.IntervalMinutes) {
        $diff += 'trigger.interval'
    }
    if ([string]$Existing.DurationMinutes -ne [string]$Spec.DurationMinutes) {
        $diff += 'trigger.duration'
    }
    return ,$diff
}

function Get-TaskDecision {
    # 'register' when absent, 'unchanged' when converged, otherwise
    # 're-register-because-<field>[+<field>]-diverged'.
    param(
        $Existing,
        $Spec
    )

    if (-not $Existing) {
        return [pscustomobject]@{ Decision = 'register'; DivergedFields = @() }
    }
    $diff = Get-TaskDivergence -Existing $Existing -Spec $Spec
    if ($diff.Count -eq 0) {
        return [pscustomobject]@{ Decision = 'unchanged'; DivergedFields = @() }
    }
    return [pscustomobject]@{
        Decision       = 're-register-because-' + ($diff -join '+') + '-diverged'
        DivergedFields = $diff
    }
}

function Build-TaskAction {
    param($Spec)

    return New-ScheduledTaskAction -Execute $Spec.Execute -Argument $Spec.Arguments
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
        [string]$BashExe,
        [string]$AesopRoot
    )

    $spec = Get-TaskSpec -Command $Command -IntervalMinutes $IntervalMinutes

    # Check if task already exists in the root folder only (see $TaskFolder).
    $existingTask = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder -ErrorAction SilentlyContinue
    $existing = $null
    if ($existingTask) {
        $existing = ConvertTo-TaskRecord -Task $existingTask
    }

    if ($existing -and -not $Force) {
        $verdict = Get-TaskDecision -Existing $existing -Spec $spec
        if ($verdict.Decision -eq 'unchanged') {
            # Same action, principal, and interval as the declared spec: nothing to do.
            Write-Host "Task already exists with same action: $TaskName (idempotent)"
            Append-AuditLog -AesopRoot $AesopRoot -Action 'register' -TaskName $TaskName -Outcome 'idempotent'
            return
        }
        # Any divergence from the declared spec is converged by re-registering (D45).
        Write-Host "Task '$TaskName' diverges from the declared spec ($($verdict.DivergedFields -join ', ')); re-registering." -ForegroundColor Yellow
        Write-Host "  Existing action: $($existing.Execute) $($existing.Arguments)" -ForegroundColor Yellow
        Write-Host "  Declared action: $($spec.Execute) $($spec.Arguments)" -ForegroundColor Yellow
        Append-AuditLog -AesopRoot $AesopRoot -Action 'register' -TaskName $TaskName -Outcome 'reregistered-divergent'
    }

    # Task doesn't exist, diverges from the spec, or -Force was requested: register it.
    $action = Build-TaskAction -Spec $spec

    # Build the trigger: Once, starting in 1 minute, repeating every N minutes for 10 years
    $startTime = (Get-Date).AddMinutes(1)
    $trigger = New-ScheduledTaskTrigger `
        -Once `
        -At $startTime `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days $TriggerDurationDays)

    # Build the settings: Hidden, IgnoreNew for multiple instances, 1-hour timeout
    $settings = New-ScheduledTaskSettingsSet `
        -Hidden `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -StartWhenAvailable

    if ($DryRun) {
        # Print DryRun output
        Write-Host "DRYRUN: $TaskName -> $($spec.Execute) $($spec.Arguments) (interval=$IntervalMinutes`m, Hidden=True)"
        # Still log dry-run registrations if enabled
        Append-AuditLog -AesopRoot $AesopRoot -Action 'register' -TaskName $TaskName -Outcome 'dryrun'
    }
    else {
        # Register the task (force overwrite if -Force is passed, or if task didn't exist)
        try {
            Register-ScheduledTask `
                -TaskName $TaskName `
                -TaskPath $TaskFolder `
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
        $task = Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder -Confirm:$false -ErrorAction Stop
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

function Get-ManageScope {
    # Determine which tasks should be managed in this invocation (scoping).
    # Default: manage watchdog only.
    # If -All: manage all three tasks.
    # If -EnableMergeQueue and/or -MonitorCommand: manage only those.
    # Requirement: "-EnableMergeQueue registers ONLY AesopMergeQueue, nothing else"
    if ($All) {
        return @('watchdog', 'monitor', 'mergequeue')
    }
    if ($EnableMergeQueue -or $MonitorCommand) {
        $scope = @()
        if ($EnableMergeQueue) {
            $scope += 'mergequeue'
        }
        if ($MonitorCommand) {
            $scope += 'monitor'
        }
        return $scope
    }
    return @('watchdog')
}

function Resolve-DaemonCommands {
    # Derive default commands for the tasks in scope (sets the script-scope command params).
    param([string[]]$Manage)

    if ($Manage -contains 'watchdog' -and -not $WatchdogCommand) {
        $posixRoot = ConvertTo-PosixPath $aesopRoot

        # P2: Detect apostrophe in derived path (breaks bash syntax if not escaped)
        if ($posixRoot -like "*'*") {
            Write-Error "Repository path contains apostrophe, which would break the derived command: $posixRoot`nPass -WatchdogCommand explicitly."
            exit 1
        }

        $script:WatchdogCommand = "bash '$posixRoot/daemons/run-watchdog.sh' --once >> '$posixRoot/state/cron-watchdog.log' 2>&1"
    }

    # OPT-IN merge queue: this task merges to main with no interactive session, so it is
    # never switched on as a side effect of running the installer. Pass -EnableMergeQueue
    # (derives the command) or -MergeQueueCommand (explicit).
    if ($Manage -contains 'mergequeue' -and -not $MergeQueueCommand) {
        $posixRootMq = ConvertTo-PosixPath $aesopRoot

        # Same apostrophe guard as the watchdog derivation: an apostrophe in
        # the path would break the single-quoted bash command.
        if ($posixRootMq -like "*'*") {
            Write-Error "Repository path contains apostrophe, which would break the derived command: $posixRootMq`nPass -MergeQueueCommand explicitly."
            exit 1
        }

        $script:MergeQueueCommand = "bash '$posixRootMq/daemons/run-merge-queue.sh' --once >> '$posixRootMq/state/cron-merge-queue.log' 2>&1"
    }
}

function Write-InstallPlan {
    # Prints the plan as JSON. Reads only -ExistingTasksJson; calls no scheduler cmdlet.
    param([string[]]$Manage)

    if (-not $ExistingTasksJson) {
        Write-Error '-Plan requires -ExistingTasksJson <path> (JSON array of existing task records).'
        exit 1
    }
    if (-not (Test-Path -LiteralPath $ExistingTasksJson -PathType Leaf)) {
        Write-Error "-ExistingTasksJson not found: $ExistingTasksJson"
        exit 1
    }
    $raw = Get-Content -LiteralPath $ExistingTasksJson -Raw -Encoding UTF8
    $existingRecords = @()
    if ($raw -and $raw.Trim()) {
        $existingRecords = @($raw | ConvertFrom-Json)
    }

    $specs = @()
    if ($Manage -contains 'watchdog') {
        $specs += [pscustomobject]@{ Name = "${TaskPrefix}WatchdogDaemon"; Command = $script:WatchdogCommand; Interval = $WatchdogIntervalMinutes }
    }
    if (($Manage -contains 'monitor') -and $MonitorCommand) {
        $specs += [pscustomobject]@{ Name = "${TaskPrefix}RefinementMonitor"; Command = $MonitorCommand; Interval = $MonitorIntervalMinutes }
    }
    if ($Manage -contains 'mergequeue') {
        $specs += [pscustomobject]@{ Name = "${TaskPrefix}MergeQueue"; Command = $script:MergeQueueCommand; Interval = $MergeQueueIntervalMinutes }
    }

    $tasks = @()
    $ignored = @()
    foreach ($item in $specs) {
        $spec = Get-TaskSpec -Command $item.Command -IntervalMinutes $item.Interval
        $rootRecord = $existingRecords | Where-Object { $_.TaskName -eq $item.Name -and $_.TaskPath -eq $TaskFolder } | Select-Object -First 1
        $verdict = Get-TaskDecision -Existing $rootRecord -Spec $spec
        $decision = $verdict.Decision
        if ($Force -and $decision -ne 'register') {
            $decision = 'register'
        }
        $tasks += [ordered]@{
            name            = $item.Name
            taskPath        = $TaskFolder
            execute         = $spec.Execute
            arguments       = $spec.Arguments
            intervalMinutes = $spec.IntervalMinutes
            durationMinutes = $spec.DurationMinutes
            logonType       = $spec.LogonType
            decision        = $decision
            divergedFields  = @($verdict.DivergedFields)
        }
        # Same-named tasks outside the root folder are never read or written by the real path.
        foreach ($other in @($existingRecords | Where-Object { $_.TaskName -eq $item.Name -and $_.TaskPath -ne $TaskFolder })) {
            $ignored += [ordered]@{ name = $item.Name; taskPath = [string]$other.TaskPath; reason = 'outside root folder; not managed' }
        }
    }

    $plan = [ordered]@{
        mode  = 'plan'
        tasks = @($tasks)
        ignored = @($ignored)
    }
    ConvertTo-Json -InputObject $plan -Depth 6
}

function Main {
    # Refuse the real path when the test-double env var is set. Runs before anything else
    # touches the scheduler. -Plan and -DryRun never register or unregister, so they stay allowed.
    if ($env:AESOP_INSTALL_TASKS_DRY_RUN -eq '1' -and -not $Plan -and -not $DryRun) {
        Write-Error 'AESOP_INSTALL_TASKS_DRY_RUN=1 refuses the real install path (registration or -Uninstall). Use -Plan or -DryRun.'
        exit 1
    }

    # Resolve paths
    $aesopRoot = Get-WorktreeRoot

    # VALIDATION: Check for double quotes in commands (contract violation)
    # This check runs early and always, before any operations
    if ($WatchdogCommand -like '*"*') {
        Write-Error "WatchdogCommand contains double quotes, which are not allowed (launcher argument contract violation)."
        exit 1
    }
    if ($MonitorCommand -like '*"*') {
        Write-Error "MonitorCommand contains double quotes, which are not allowed (launcher argument contract violation)."
        exit 1
    }
    if ($MergeQueueCommand -like '*"*') {
        Write-Error "MergeQueueCommand contains double quotes, which are not allowed (launcher argument contract violation)."
        exit 1
    }

    # PLAN: compute and print the plan. Nothing below this block runs in -Plan mode.
    if ($Plan) {
        if ($Uninstall) {
            Write-Error '-Plan and -Uninstall cannot be combined.'
            exit 1
        }
        $manageTasks = Get-ManageScope
        Resolve-DaemonCommands -Manage $manageTasks
        Write-InstallPlan -Manage $manageTasks
        exit 0
    }

    # PATH VALIDATION: Only enforce file existence checks if not in DryRun mode
    # In DryRun, downgrade to warnings so preview works on machines without Git Bash
    if (-not $DryRun) {
        if (-not (Test-Path -LiteralPath $BashExe)) {
            Write-Error "bash.exe not found at: $BashExe"
            exit 1
        }
    }
    else {
        if (-not (Test-Path -LiteralPath $BashExe)) {
            Write-Warning "bash.exe not found at: $BashExe (DryRun mode)"
        }
    }

    # Fail closed: the hidden launcher must exist before any task is registered. Built here
    # from run-hidden.cs when missing; a failed build exits 1 with nothing registered.
    # Uninstall and DryRun need no launcher.
    if (-not $Uninstall -and -not $DryRun) {
        $null = Ensure-HiddenLauncher
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

    $manageTasks = Get-ManageScope
    Resolve-DaemonCommands -Manage $manageTasks

    # Register watchdog task if in scope
    if ($manageTasks -contains 'watchdog') {
        $watchdogTaskName = "${TaskPrefix}WatchdogDaemon"
        Register-DaemonTask `
            -TaskName $watchdogTaskName `
            -Command $WatchdogCommand `
            -IntervalMinutes $WatchdogIntervalMinutes `
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
                -BashExe $BashExe `
                -AesopRoot $aesopRoot
        }
    }

    # Register the merge-queue task if in scope (opt-in, see Resolve-DaemonCommands).
    if ($manageTasks -contains 'mergequeue') {
        $mergeQueueTaskName = "${TaskPrefix}MergeQueue"
        Register-DaemonTask `
            -TaskName $mergeQueueTaskName `
            -Command $MergeQueueCommand `
            -IntervalMinutes $MergeQueueIntervalMinutes `
            -BashExe $BashExe `
            -AesopRoot $aesopRoot
    }

    exit 0
}

Main
