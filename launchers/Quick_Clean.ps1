# SmartDrive-OS - Safe Junk Cleaner (Windows PowerShell)
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Tier 1: Directory & Root Resolution
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path (Join-Path $ScriptDir "smart_drive")) {
    $DriveRoot = $ScriptDir
} elseif (Test-Path (Join-Path (Split-Path -Parent $ScriptDir) "smart_drive")) {
    $DriveRoot = Split-Path -Parent $ScriptDir
} else {
    $DriveRoot = $ScriptDir
}
Set-Location $DriveRoot

# Tier 2: Python 3.9+ Discovery & Version Check
$PythonCmd = $null
foreach ($cmd in @("python", "py", "python3")) {
    try {
        $check = Start-Process -FilePath $cmd -ArgumentList '-c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"' -NoNewWindow -PassThru -Wait -ErrorAction SilentlyContinue
        if ($check.ExitCode -eq 0) {
            $PythonCmd = $cmd
            break
        }
    } catch {}
}
if (-not $PythonCmd) {
    try {
        $check = Start-Process -FilePath "py" -ArgumentList '-3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"' -NoNewWindow -PassThru -Wait -ErrorAction SilentlyContinue
        if ($check.ExitCode -eq 0) {
            $PythonCmd = "py -3"
        }
    } catch {}
}
if (-not $PythonCmd) {
    Write-Host "========================================================" -ForegroundColor Red
    Write-Host "  [ERROR] Python 3.9+ was not found on system PATH!" -ForegroundColor Red
    Write-Host "========================================================" -ForegroundColor Red
    Write-Host "  SmartDrive-OS requires Python 3.9 or higher."
    Write-Host "  Zero external pip packages are required."
    Write-Host "  Please install from https://www.python.org/downloads/"
    Write-Host ""
    Read-Host "Press Enter to exit..."
    exit 1
}

# Tier 3: exFAT Optimization & Host Isolation
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:GIT_TERMINAL_PROMPT = "0"
$env:GIT_CONFIG_NOSYSTEM = "1"
$env:SMART_DRIVE_ROOT = $DriveRoot
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$DriveRoot;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = "$DriveRoot"
}

# Tier 4: Execution
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  SmartDrive-OS: Safe Junk Cleaner (Dry-Run Preview)" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

if ($PythonCmd -eq "py -3") {
    & py -3 -m smart_drive clean --dry-run
} else {
    & $PythonCmd -m smart_drive clean --dry-run
}

Write-Host ""
$Confirm = Read-Host "Execute safe Tier 1 purge? [y/N]"

if ($Confirm -eq "y" -or $Confirm -eq "Y") {
    Write-Host ""
    Write-Host "Purging safe junk files..." -ForegroundColor Yellow
    if ($PythonCmd -eq "py -3") {
        & py -3 -m smart_drive clean --apply
    } else {
        & $PythonCmd -m smart_drive clean --apply
    }
} else {
    Write-Host ""
    Write-Host "Safe purge aborted. No files were modified." -ForegroundColor Green
}

# Tier 5: Safe Pause on Exit
Write-Host ""
Read-Host "Press Enter to exit..."
