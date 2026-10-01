@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title SmartDrive-OS - Master 1-Touch Control Center

:: Tier 1: Directory & Root Resolution
if exist "%~dp0smart_drive" (
    set "DRIVE_ROOT=%~dp0"
) else if exist "%~dp0..\smart_drive" (
    set "DRIVE_ROOT=%~dp0..\"
) else (
    set "DRIVE_ROOT=%~dp0"
)
cd /d "%DRIVE_ROOT%"

:: Tier 2: Python 3.9+ Discovery & Version Check
set "PYTHON_CMD="
for %%P in (python py python3) do (
    if not defined PYTHON_CMD (
        %%P -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PYTHON_CMD=%%P"
    )
)
if not defined PYTHON_CMD (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
    echo ========================================================
    echo   [ERROR] Python 3.9+ was not found on system PATH!
    echo ========================================================
    echo   SmartDrive-OS requires Python 3.9 or higher.
    echo   Zero external pip packages are required.
    echo   Please install Python 3.9+ from https://www.python.org/
    echo   Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

:: Tier 3: exFAT Optimization & Host Isolation
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "GIT_TERMINAL_PROMPT=0"
set "GIT_CONFIG_NOSYSTEM=1"
set "SMART_DRIVE_ROOT=%DRIVE_ROOT%"
if defined PYTHONPATH (
    set "PYTHONPATH=%DRIVE_ROOT%;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%DRIVE_ROOT%"
)

:: Tier 4: Execution (Master 1-Touch Menu Loop)
:MENU_LOOP
cls
echo ========================================================
echo   SmartDrive-OS: Master 1-Touch Control Center
echo   Autonomous Storage Management for External SSDs
echo ========================================================
echo   Drive Root: %DRIVE_ROOT%
echo ========================================================
echo.
echo   [1] Initialize Drive & Presets (smart-drive init)
echo   [2] Sentinel & Integrity Health Check (smart-drive sentinel)
echo   [3] SQLite FTS5 File Search (smart-drive search)
echo   [4] Storage & Cluster Slack Audit (smart-drive audit)
echo   [5] Safe Junk Cleaner (smart-drive clean)
echo   [6] Auto-Zoning & Anti-Slack Rebalancer (smart-drive organize)
echo   [7] Register AI Coding Agents MCP (smart-drive mcp register)
echo   [8] Launch Web Dashboard UI (smart-drive ui)
echo   [0] Exit
echo.
set "OPT="
set /p OPT="Select an option [0-8]: "

if "%OPT%"=="1" goto OP_INIT
if "%OPT%"=="2" goto OP_SENTINEL
if "%OPT%"=="3" goto OP_SEARCH
if "%OPT%"=="4" goto OP_AUDIT
if "%OPT%"=="5" goto OP_CLEAN
if "%OPT%"=="6" goto OP_ORGANIZE
if "%OPT%"=="7" goto OP_MCP
if "%OPT%"=="8" goto OP_UI
if "%OPT%"=="0" goto OP_EXIT
goto MENU_LOOP

:OP_INIT
echo.
echo Select a Preset Profile:
echo   [1] AI Developer
echo   [2] Data Science
echo   [3] General Workspace (Default)
set "C_PROF=3"
set /p C_PROF="Enter profile number [1-3] (Default: 3): "
set "P_NAME=general-workspace"
if "%C_PROF%"=="1" set "P_NAME=ai-developer"
if "%C_PROF%"=="2" set "P_NAME=data-science"
echo.
%PYTHON_CMD% -m smart_drive init --profile %P_NAME%
echo.
pause
goto MENU_LOOP

:OP_SENTINEL
echo.
%PYTHON_CMD% -m smart_drive sentinel
echo.
pause
goto MENU_LOOP

:OP_SEARCH
echo.
set "Q_TERM="
set /p Q_TERM="Enter search query: "
echo.
%PYTHON_CMD% -m smart_drive search "%Q_TERM%"
echo.
pause
goto MENU_LOOP

:OP_AUDIT
echo.
%PYTHON_CMD% -m smart_drive audit
echo.
pause
goto MENU_LOOP

:OP_CLEAN
echo.
%PYTHON_CMD% -m smart_drive clean --dry-run
echo.
set "CONF_CLN=N"
set /p CONF_CLN="Execute safe Tier 1 purge? [y/N]: "
if /i "%CONF_CLN%"=="y" (
    echo.
    %PYTHON_CMD% -m smart_drive clean --apply
) else (
    echo.
    echo Safe purge aborted.
)
echo.
pause
goto MENU_LOOP

:OP_ORGANIZE
echo.
%PYTHON_CMD% -m smart_drive organize
echo.
set "CONF_ORG=N"
set /p CONF_ORG="Apply auto-zoning reorganization? [y/N]: "
if /i "%CONF_ORG%"=="y" (
    echo.
    %PYTHON_CMD% -m smart_drive organize --apply
) else (
    echo.
    echo Reorganization plan aborted.
)
echo.
pause
goto MENU_LOOP

:OP_MCP
echo.
%PYTHON_CMD% -m smart_drive mcp register 2>nul || %PYTHON_CMD% -m smart_drive mcp-config
echo.
pause
goto MENU_LOOP

:OP_UI
echo.
echo Starting Web Dashboard (Press Ctrl+C to stop)...
%PYTHON_CMD% -m smart_drive ui
echo.
pause
goto MENU_LOOP

:OP_EXIT
echo.
echo Exiting SmartDrive-OS. Goodbye!

:: Tier 5: Safe Pause on Exit
echo.
