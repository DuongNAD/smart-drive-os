#!/bin/bash
# SmartDrive-OS - Safe Junk Cleaner (macOS / Linux)

# Tier 1: Directory & Root Resolution
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
if [ -d "$DIR/smart_drive" ]; then
    DRIVE_ROOT="$DIR"
elif [ -d "$DIR/../smart_drive" ]; then
    DRIVE_ROOT="$( cd "$DIR/.." >/dev/null 2>&1 && pwd )"
else
    DRIVE_ROOT="$DIR"
fi
cd "$DRIVE_ROOT" || exit 1

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
    read -r -p "Press Enter to exit..."
    exit 1
fi

# Tier 3: exFAT Optimization & Host Isolation
export PYTHONIOENCODING="utf-8"
export PYTHONUTF8="1"
export PYTHONDONTWRITEBYTECODE="1"
export GIT_TERMINAL_PROMPT="0"
export GIT_CONFIG_NOSYSTEM="1"
export SMART_DRIVE_ROOT="$DRIVE_ROOT"
export PYTHONPATH="$DRIVE_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Tier 4: Execution
echo "========================================================"
echo "  SmartDrive-OS: Safe Junk Cleaner (Dry-Run Preview)"
echo "========================================================"
echo ""

"$PYTHON_CMD" -m smart_drive clean --dry-run

echo ""
read -r -p "Execute safe Tier 1 purge? [y/N]: " CONFIRM

if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
    echo ""
    echo "Purging safe junk files..."
    "$PYTHON_CMD" -m smart_drive clean --apply
else
    echo ""
    echo "Safe purge aborted. No files were modified."
fi

# Tier 5: Safe Pause on Exit
echo ""
read -r -p "Press Enter to exit..."
