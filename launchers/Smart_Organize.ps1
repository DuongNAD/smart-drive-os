# SmartDrive-OS - Smart Organize (Safe Guided Move - Windows PowerShell)
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Tier 1: Directory & Root Resolution
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path (Join-Path $ScriptDir "smart_drive")) {
    $CODE_DIR = $ScriptDir
} elseif (Test-Path (Join-Path (Split-Path -Parent $ScriptDir) "smart_drive")) {
    $CODE_DIR = Split-Path -Parent $ScriptDir
} else {
    $CODE_DIR = $ScriptDir
    if (-not $env:SMART_DRIVE_ROOT) {
        $env:SMART_DRIVE_ROOT = $ScriptDir
    }
}
Set-Location $CODE_DIR

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
    if ($env:SD_SELFTEST -ne "1") {
        Read-Host "Press Enter to exit..."
    }
    exit 1
}

# Tier 3: exFAT Optimization & Host Isolation
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:GIT_TERMINAL_PROMPT = "0"
$env:GIT_CONFIG_NOSYSTEM = "1"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$CODE_DIR;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = "$CODE_DIR"
}

# Tier 4: Guided Safe Organize Flow
# Flow: dry-run -> wait busy_apps -> type YES -> organize --apply --json -> verify (relink --dry-run, git/worktree, venv) -> R / U / Enter
# Supports SMARTDRIVE_LANG=vi|en and SD_SELFTEST=1 (writes nothing); logs to .smart_drive/smart-organize.log once the moves are confirmed
if ($PythonCmd -eq "py -3") {
    & py -3 -m smart_drive.cli.smart_organize_runner @args
} else {
    & $PythonCmd -m smart_drive.cli.smart_organize_runner @args
}
$ExitCode = $LASTEXITCODE

# Tier 5: Safe Pause on Exit
if ($env:SD_SELFTEST -eq "1") {
    exit $ExitCode
}
Write-Host ""
Read-Host "Press Enter to exit..."
exit $ExitCode
