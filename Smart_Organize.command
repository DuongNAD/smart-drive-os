#!/bin/bash
# SmartDrive-OS - Smart Organize (Safe Guided Move - macOS / Linux)

# Tier 1: Directory & Root Resolution
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
if [ -d "$DIR/smart_drive" ]; then
    CODE_DIR="$DIR"
elif [ -d "$DIR/../smart_drive" ]; then
    CODE_DIR="$( cd "$DIR/.." >/dev/null 2>&1 && pwd )"
else
    CODE_DIR="$DIR"
    if [ -z "${SMART_DRIVE_ROOT:-}" ]; then
        export SMART_DRIVE_ROOT="$DIR"
    fi
fi
cd "$CODE_DIR" || exit 1

# Tier 2: Python 3.9+ Discovery & Version Check
PYTHON_CMD=""
for cmd in python3 python py; do
    if command -v "$cmd" >/dev/null 2>&1; then
        if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >/dev/null 2>&1; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "========================================================"
    echo "  [ERROR] Python 3.9+ was not found on system PATH!"
    echo "========================================================"
    echo "  SmartDrive-OS requires Python 3.9 or higher."
    echo "  Zero external pip packages are required."
    echo "  Please install from https://www.python.org/downloads/"
    echo ""
    if [ "${SD_SELFTEST:-0}" != "1" ]; then
        read -r -p "Press Enter to exit..."
    fi
    exit 1
fi

# Tier 3: exFAT Optimization & Host Isolation
export PYTHONIOENCODING="utf-8"
export PYTHONUTF8="1"
export PYTHONDONTWRITEBYTECODE="1"
export GIT_TERMINAL_PROMPT="0"
export GIT_CONFIG_NOSYSTEM="1"
export PYTHONPATH="$CODE_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Tier 4: Guided Safe Organize Flow
# Flow: dry-run -> wait busy_apps -> type YES -> organize --apply --json -> verify (relink --dry-run, git/worktree, venv) -> R / U / Enter
# Supports SMARTDRIVE_LANG=vi|en and SD_SELFTEST=1 (writes nothing); logs to .smart_drive/smart-organize.log once the moves are confirmed
"$PYTHON_CMD" -m smart_drive.cli.smart_organize_runner "$@"
EXIT_CODE=$?

# Tier 5: Safe Pause on Exit
if [ "${SD_SELFTEST:-0}" = "1" ]; then
    exit "$EXIT_CODE"
fi
echo ""
read -r -p "Press Enter to exit..."
exit "$EXIT_CODE"
