#!/bin/bash
# SmartDrive-OS - 1-Touch SSD Setup (macOS / Linux)

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
echo "  SmartDrive-OS: 1-Touch SSD Initialization"
echo "  Autonomous Storage Management for External SSDs"
echo "========================================================"
echo ""
echo "Select a Preset Profile for your drive:"
echo ""
echo "  [1] AI Developer      (Models, checkpoints, GGUF, agent workspaces)"
echo "  [2] Data Science      (EDA notebooks, pipelines, parquet/raw data)"
echo "  [3] General Workspace (Universal code, docs, notes, toolbox)"
echo ""
read -r -p "Enter profile number [1-3] (Default: 3): " CHOICE

PROFILE="general-workspace"
if [ "$CHOICE" = "1" ]; then
    PROFILE="ai-developer"
elif [ "$CHOICE" = "2" ]; then
    PROFILE="data-science"
elif [ "$CHOICE" = "3" ]; then
    PROFILE="general-workspace"
fi

echo ""
echo "Initializing SmartDrive-OS with profile: $PROFILE..."
echo ""

"$PYTHON_CMD" -m smart_drive init --profile "$PROFILE"

echo ""
echo "========================================================"
echo "  Initialization complete!"
echo "========================================================"

# Tier 5: Safe Pause on Exit
echo ""
read -r -p "Press Enter to exit..."
