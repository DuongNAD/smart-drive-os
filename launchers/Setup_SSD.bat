@echo off
chcp 65001 >nul
title SmartDrive-OS - 1-Touch SSD Setup

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
    echo   Please install Python 3.9+ from https://www.python.org/
    echo   Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

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
echo.
pause
