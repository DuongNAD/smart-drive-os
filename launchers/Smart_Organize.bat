@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title SmartDrive-OS - Smart Organize (Safe Guided Move)

:: Tier 1: Directory & Root Resolution
if exist "%~dp0smart_drive" (
    set "CODE_DIR=%~dp0"
) else if exist "%~dp0..\smart_drive" (
    set "CODE_DIR=%~dp0..\"
) else (
    set "CODE_DIR=%~dp0"
    if not defined SMART_DRIVE_ROOT set "SMART_DRIVE_ROOT=%~dp0"
)
cd /d "%CODE_DIR%"

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
    if not "%SD_SELFTEST%"=="1" pause
    exit /b 1
)

:: Tier 3: exFAT Optimization & Host Isolation
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "GIT_TERMINAL_PROMPT=0"
set "GIT_CONFIG_NOSYSTEM=1"
if defined PYTHONPATH (
    set "PYTHONPATH=%CODE_DIR%;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%CODE_DIR%"
)

:: Tier 4: Guided Safe Organize Flow
:: Flow: dry-run -> wait busy_apps -> type YES -> organize --apply --json -> verify (relink --dry-run, git/worktree, venv) -> R / U / Enter
:: Supports SMARTDRIVE_LANG=vi|en and read-only SD_SELFTEST=1; logs to .smart_drive/smart-organize.log
%PYTHON_CMD% -m smart_drive.cli.smart_organize_runner %*
set "EXIT_CODE=%ERRORLEVEL%"

:: Tier 5: Safe Pause on Exit
if "%SD_SELFTEST%"=="1" exit /b %EXIT_CODE%
echo.
pause
exit /b %EXIT_CODE%
