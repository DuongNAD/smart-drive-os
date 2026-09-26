@echo off
chcp 65001 >nul
title SmartDrive-OS - Safe Junk Cleaner

if exist "%~dp0smart_drive" (
    cd /d "%~dp0"
) else if exist "%~dp0..\smart_drive" (
    cd /d "%~dp0.."
) else (
    cd /d "%~dp0"
)

:: Locate Python
set "PYTHON_CMD="
python --version >nul 2>&1 && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
    py -3 --version >nul 2>&1 && set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
    py --version >nul 2>&1 && set "PYTHON_CMD=py"
)
if not defined PYTHON_CMD (
    python3 --version >nul 2>&1 && set "PYTHON_CMD=python3"
)

if not defined PYTHON_CMD (
    echo ========================================================
    echo   [ERROR] Python 3 not found on system PATH!
    echo ========================================================
    echo.
    pause
    exit /b 1
)

echo ========================================================
echo   SmartDrive-OS: Safe Junk Cleaner (Dry-Run Preview)
echo ========================================================
echo.

%PYTHON_CMD% -m smart_drive clean --dry-run

echo.
set "CONFIRM=N"
set /p CONFIRM="Execute safe Tier 1 purge? [y/N]: "

if /i "%CONFIRM%"=="y" (
    echo.
    echo Purging safe junk files...
    %PYTHON_CMD% -m smart_drive clean --apply
) else (
    echo.
    echo Safe purge aborted. No files were modified.
)

echo.
pause
