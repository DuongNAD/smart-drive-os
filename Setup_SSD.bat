@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title SmartDrive-OS - 1-Touch SSD Setup

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

:: Tier 4: Execution
echo ========================================================
echo   SmartDrive-OS: 1-Touch SSD Initialization
echo   Autonomous Storage Management for External SSDs
echo ========================================================
echo.
echo Select a Preset Profile for your drive:
echo.
echo   [1] AI Developer      (Models, checkpoints, GGUF, agent workspaces)
echo   [2] Data Science      (EDA notebooks, pipelines, parquet/raw data)
echo   [3] General Workspace (Universal code, docs, notes, toolbox)
echo.
set "CHOICE=3"
set /p CHOICE="Enter profile number [1-3] (Default: 3): "

set "PROFILE=general-workspace"
if "%CHOICE%"=="1" set "PROFILE=ai-developer"
if "%CHOICE%"=="2" set "PROFILE=data-science"
if "%CHOICE%"=="3" set "PROFILE=general-workspace"

echo.
echo Initializing SmartDrive-OS with profile: %PROFILE%...
echo.

%PYTHON_CMD% -m smart_drive init --profile %PROFILE%

echo.
echo ========================================================
echo   Initialization complete!
echo ========================================================

:: Tier 5: Safe Pause on Exit
echo.
pause
