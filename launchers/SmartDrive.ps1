# SmartDrive-OS - Master 1-Touch Control Center (Windows PowerShell)
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

function Run-SmartDrive {
    param([string[]]$Arguments)
    if ($PythonCmd -eq "py -3") {
        & py -3 -m smart_drive @Arguments
    } else {
        & $PythonCmd -m smart_drive @Arguments
    }
}

# Tier 4: Execution (Master 1-Touch Menu Loop)
while ($true) {
    Clear-Host
    Write-Host "========================================================" -ForegroundColor Cyan
    Write-Host "  SmartDrive-OS: Master 1-Touch Control Center" -ForegroundColor Cyan
    Write-Host "  Autonomous Storage Management for External SSDs" -ForegroundColor Cyan
    Write-Host "========================================================" -ForegroundColor Cyan
    Write-Host "  Drive Root: $DriveRoot"
    Write-Host "========================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] Initialize Drive & Presets (smart-drive init)"
    Write-Host "  [2] Sentinel & Integrity Health Check (smart-drive sentinel)"
    Write-Host "  [3] SQLite FTS5 File Search (smart-drive search)"
    Write-Host "  [4] Storage & Cluster Slack Audit (smart-drive audit)"
    Write-Host "  [5] Safe Junk Cleaner (smart-drive clean)"
    Write-Host "  [6] Auto-Zoning & Anti-Slack Rebalancer (smart-drive organize)"
    Write-Host "  [7] Register AI Coding Agents MCP (smart-drive mcp register)"
    Write-Host "  [8] Launch Web Dashboard UI (smart-drive ui)"
    Write-Host "  [0] Exit"
    Write-Host ""
    $Opt = Read-Host "Select an option [0-8]"

    switch ($Opt) {
        "1" {
            Write-Host ""
            Write-Host "Select a Preset Profile:"
            Write-Host "  [1] AI Developer"
            Write-Host "  [2] Data Science"
            Write-Host "  [3] General Workspace (Default)"
            $C = Read-Host "Enter profile number [1-3] (Default: 3)"
            $Prof = "general-workspace"
            if ($C -eq "1") { $Prof = "ai-developer" }
            if ($C -eq "2") { $Prof = "data-science" }
            Write-Host ""
            Run-SmartDrive @("init", "--profile", $Prof)
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "2" {
            Write-Host ""
            Run-SmartDrive @("sentinel")
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "3" {
            Write-Host ""
            $Q = Read-Host "Enter search query"
            Write-Host ""
            Run-SmartDrive @("search", $Q)
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "4" {
            Write-Host ""
            Run-SmartDrive @("audit")
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "5" {
            Write-Host ""
            Run-SmartDrive @("clean", "--dry-run")
            Write-Host ""
            $Confirm = Read-Host "Execute safe Tier 1 purge? [y/N]"
            if ($Confirm -eq "y" -or $Confirm -eq "Y") {
                Write-Host ""
                Run-SmartDrive @("clean", "--apply")
            } else {
                Write-Host ""
                Write-Host "Safe purge aborted." -ForegroundColor Green
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "6" {
            Write-Host ""
            Run-SmartDrive @("organize")
            Write-Host ""
            $Confirm = Read-Host "Apply auto-zoning reorganization? [y/N]"
            if ($Confirm -eq "y" -or $Confirm -eq "Y") {
                Write-Host ""
                Run-SmartDrive @("organize", "--apply")
            } else {
                Write-Host ""
                Write-Host "Reorganization plan aborted." -ForegroundColor Green
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "7" {
            Write-Host ""
            try {
                Run-SmartDrive @("mcp", "register")
            } catch {
                Run-SmartDrive @("mcp-config")
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "8" {
            Write-Host ""
            Write-Host "Starting Web Dashboard (Press Ctrl+C to stop)..." -ForegroundColor Yellow
            Run-SmartDrive @("ui")
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "0" {
            Write-Host ""
            Write-Host "Exiting SmartDrive-OS. Goodbye!" -ForegroundColor Cyan
            break
        }
    }
}

# Tier 5: Safe Pause on Exit
Write-Host ""
