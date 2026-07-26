# PowerShell syntax validator using System.Management.Automation.Language.Parser
# Checks all tracked *.ps1 files for parse errors
# Exit code: 0 if all files parse, 1 if any parse errors found

param(
    [string]$RepoRoot = '.'
)

$ErrorActionPreference = 'Stop'
$parseErrors = @()

# Get all tracked PS1 files
try {
    $ps1Files = @(git -C $RepoRoot ls-files '*.ps1')
}
catch {
    Write-Error "Failed to list tracked PowerShell files: $_"
    exit 1
}

# If no PS1 files exist, pass silently
if ($ps1Files.Count -eq 0) {
    Write-Host "No tracked *.ps1 files found; check passed"
    exit 0
}

Write-Host "Checking PowerShell syntax for $($ps1Files.Count) file(s)..."

# Check each file
foreach ($file in $ps1Files) {
    $fullPath = Join-Path $RepoRoot $file

    if (-not (Test-Path $fullPath)) {
        Write-Warning "File not found (may be deleted): $fullPath"
        continue
    }

    try {
        $content = Get-Content -Path $fullPath -Raw -ErrorAction Stop
        $tokens = $null
        $errors = $null

        # Parse the file content; Parser.ParseInput returns $null on success, tokens/errors on failure
        [System.Management.Automation.Language.Parser]::ParseInput($content, [ref]$tokens, [ref]$errors) | Out-Null

        if ($errors -and $errors.Count -gt 0) {
            $parseErrors += @{
                File   = $file
                Errors = $errors
            }
            Write-Host "  FAIL: $file"
            foreach ($err in $errors) {
                Write-Host "    Line $($err.Extent.StartLineNumber): $($err.Message)"
            }
        }
        else {
            Write-Host "  OK: $file"
        }
    }
    catch {
        Write-Error "Error parsing $file : $_"
        exit 1
    }
}

# Summary
if ($parseErrors.Count -gt 0) {
    Write-Host ""
    Write-Host "PowerShell syntax check FAILED: $($parseErrors.Count) file(s) with errors"
    exit 1
}
else {
    Write-Host ""
    Write-Host "PowerShell syntax check PASSED: All $($ps1Files.Count) file(s) are valid"
    exit 0
}
