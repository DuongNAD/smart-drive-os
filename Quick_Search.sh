#!/bin/bash
# SmartDrive-OS - Instant FTS5 Search (macOS / Linux)

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
echo "  SmartDrive-OS: Instant SQLite FTS5 Search"
echo "========================================================"
echo ""
read -r -p "Enter search query (supports ext:, size:, cat:, dir:): " QUERY

if [ -z "$QUERY" ]; then
    echo ""
    echo "No query entered. Showing search syntax examples:"
    echo "  - llama ext:gguf"
    echo "  - project size:>10MB"
    echo "  - cat:code dir:Development_Projects"
    echo ""
    read -r -p "Enter query: " QUERY
fi

echo ""
"$PYTHON_CMD" -m smart_drive search "$QUERY"

# Tier 5: Safe Pause on Exit
echo ""
read -r -p "Press Enter to exit..."
