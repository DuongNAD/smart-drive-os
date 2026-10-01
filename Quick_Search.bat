@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title SmartDrive-OS - Instant FTS5 Search

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
echo   SmartDrive-OS: Instant SQLite FTS5 Search
echo ========================================================
echo.
set "QUERY="
set /p QUERY="Enter search query (supports ext:, size:, cat:, dir:): "

if "%QUERY%"=="" (
    echo.
    echo No query entered. Showing search syntax examples:
    echo   - llama ext:gguf
    echo   - project size:>10MB
    echo   - cat:code dir:Development_Projects
    echo.
    set /p QUERY="Enter query: "
)

echo.
%PYTHON_CMD% -m smart_drive search "%QUERY%"

:: Tier 5: Safe Pause on Exit
echo.
pause
