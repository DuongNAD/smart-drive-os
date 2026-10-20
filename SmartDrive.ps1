# SmartDrive-OS - Master 1-Touch Control Center (Windows PowerShell)
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
    Read-Host "Press Enter to exit..."
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

function Run-SmartDrive {
    param([string[]]$Arguments)
    if ($PythonCmd -eq "py -3") {
        & py -3 -m smart_drive @Arguments
    } else {
        & $PythonCmd -m smart_drive @Arguments
    }
}

# Tier 4: Execution (Master 1-Touch Menu Loop)
# The label lets option 0 leave the loop: a bare break inside switch only leaves the switch.
:menu while ($true) {
    Clear-Host
    Write-Host "========================================================" -ForegroundColor Cyan
    Write-Host "  SmartDrive-OS: Master 1-Touch Control Center" -ForegroundColor Cyan
    Write-Host "  Autonomous Storage Management for External SSDs" -ForegroundColor Cyan
    Write-Host "========================================================" -ForegroundColor Cyan
    $DriveRootDisplay = if ($env:SMART_DRIVE_ROOT) { $env:SMART_DRIVE_ROOT } else { "(auto)" }
    Write-Host "  Drive Root: $DriveRootDisplay"
    Write-Host "========================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] Initialize Drive & Presets (smart-drive init)"
    Write-Host "  [2] Sentinel & Integrity Health Check (smart-drive sentinel)"
    Write-Host "  [3] SQLite FTS5 File Search (smart-drive search)"
    Write-Host "  [4] Storage & Cluster Slack Audit (smart-drive audit)"
    Write-Host "  [5] Safe Junk Cleaner (smart-drive clean)"
    Write-Host "  [6] Auto-Zoning & Anti-Slack Rebalancer (smart-drive organize)"
    Write-Host "  [7] Register AI Coding Agents MCP (smart-drive mcp register)"
    Write-Host "  [8] Relink Coding Agents After Moving Folders (smart-drive relink --find)"
    Write-Host "  [9] Drive Health Checkup & Score (smart-drive checkup)"
    Write-Host "  [10] Safe Guided Organizer (Smart_Organize)"
    Write-Host "  [11] Relink Coding Agents to the Latest Move (smart-drive relink)"
    Write-Host "  [0] Exit"
    Write-Host ""
    $Opt = Read-Host "Select an option [0-11]"

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
            Run-SmartDrive @("relink", "--find")
            Write-Host ""
            $Confirm = Read-Host "Relink the folders marked with a check now and ask about each ? folder? Open coding agents will be waited for. [y/N]"
            if ($Confirm -eq "y" -or $Confirm -eq "Y") {
                Write-Host ""
                Run-SmartDrive @("relink", "--find", "--apply", "--ask", "--wait")
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "9" {
            Write-Host ""
            Run-SmartDrive @("checkup")
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "10" {
            Write-Host ""
            $SoPath = Join-Path $ScriptDir "Smart_Organize.ps1"
            if (Test-Path $SoPath) {
                & $SoPath
            } elseif ($PythonCmd -eq "py -3") {
                & py -3 -m smart_drive.cli.smart_organize_runner
                Write-Host ""
                Read-Host "Press Enter to return to menu..."
            } else {
                & $PythonCmd -m smart_drive.cli.smart_organize_runner
                Write-Host ""
                Read-Host "Press Enter to return to menu..."
            }
        }
        "11" {
            Write-Host ""
            Run-SmartDrive @("relink", "--dry-run")
            Write-Host ""
            $Confirm = Read-Host "Relink the coding agents to the latest move now? Open coding agents will be waited for, so quit them. [y/N]"
            if ($Confirm -eq "y" -or $Confirm -eq "Y") {
                Write-Host ""
                Run-SmartDrive @("relink", "--wait")
            } else {
                Write-Host ""
                Write-Host "Relink aborted." -ForegroundColor Green
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }
        "0" {
            Write-Host ""
            Write-Host "Exiting SmartDrive-OS. Goodbye!" -ForegroundColor Cyan
            break menu
        }
    }
}

# Tier 5: Safe Pause on Exit
Write-Host ""
