# SmartDrive-OS - 1-Touch SSD Setup (Windows PowerShell)
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
Write-Host "  SmartDrive-OS: 1-Touch SSD Initialization" -ForegroundColor Cyan
Write-Host "  Autonomous Storage Management for External SSDs" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Select a Preset Profile for your drive:"
Write-Host ""
Write-Host "  [1] AI Developer      (Models, checkpoints, GGUF, agent workspaces)"
Write-Host "  [2] Data Science      (EDA notebooks, pipelines, parquet/raw data)"
Write-Host "  [3] General Workspace (Universal code, docs, notes, toolbox)"
Write-Host ""
$Choice = Read-Host "Enter profile number [1-3] (Default: 3)"
if ([string]::IsNullOrWhiteSpace($Choice)) { $Choice = "3" }

$Profile = "general-workspace"
if ($Choice -eq "1") { $Profile = "ai-developer" }
if ($Choice -eq "2") { $Profile = "data-science" }
if ($Choice -eq "3") { $Profile = "general-workspace" }

Write-Host ""
Write-Host "Initializing SmartDrive-OS with profile: $Profile..." -ForegroundColor Green
Write-Host ""

if ($PythonCmd -eq "py -3") {
    & py -3 -m smart_drive init --profile $Profile
} else {
    & $PythonCmd -m smart_drive init --profile $Profile
}

Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Initialization complete!" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

# Tier 5: Safe Pause on Exit
Write-Host ""
Read-Host "Press Enter to exit..."
