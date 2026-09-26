@echo off
chcp 65001 >nul
title SmartDrive-OS - Instant FTS5 Search

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

echo.
pause
