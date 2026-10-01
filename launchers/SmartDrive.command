#!/bin/bash
# SmartDrive-OS - Master 1-Touch Control Center (macOS / Linux)

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

# Tier 4: Execution (Master 1-Touch Menu Loop)
while true; do
    clear 2>/dev/null || echo ""
    echo "========================================================"
    echo "  SmartDrive-OS: Master 1-Touch Control Center"
    echo "  Autonomous Storage Management for External SSDs"
    echo "========================================================"
    echo "  Drive Root: $DRIVE_ROOT"
    echo "========================================================"
    echo ""
    echo "  [1] Initialize Drive & Presets (smart-drive init)"
    echo "  [2] Sentinel & Integrity Health Check (smart-drive sentinel)"
    echo "  [3] SQLite FTS5 File Search (smart-drive search)"
    echo "  [4] Storage & Cluster Slack Audit (smart-drive audit)"
    echo "  [5] Safe Junk Cleaner (smart-drive clean)"
    echo "  [6] Auto-Zoning & Anti-Slack Rebalancer (smart-drive organize)"
    echo "  [7] Register AI Coding Agents MCP (smart-drive mcp register)"
    echo "  [8] Launch Web Dashboard UI (smart-drive ui)"
    echo "  [0] Exit"
    echo ""
    read -r -p "Select an option [0-8]: " OPT

    case "$OPT" in
        1)
            echo ""
            echo "Select a Preset Profile:"
            echo "  [1] AI Developer"
            echo "  [2] Data Science"
            echo "  [3] General Workspace (Default)"
            read -r -p "Enter profile number [1-3] (Default: 3): " C_PROF
            P_NAME="general-workspace"
            if [ "$C_PROF" = "1" ]; then
                P_NAME="ai-developer"
            elif [ "$C_PROF" = "2" ]; then
                P_NAME="data-science"
            fi
            echo ""
            "$PYTHON_CMD" -m smart_drive init --profile "$P_NAME"
            echo ""
            read -r -p "Press Enter to return to menu..."
            ;;
        2)
            echo ""
            "$PYTHON_CMD" -m smart_drive sentinel
            echo ""
            read -r -p "Press Enter to return to menu..."
            ;;
        3)
            echo ""
            read -r -p "Enter search query: " Q_TERM
            echo ""
            "$PYTHON_CMD" -m smart_drive search "$Q_TERM"
            echo ""
            read -r -p "Press Enter to return to menu..."
            ;;
        4)
            echo ""
            "$PYTHON_CMD" -m smart_drive audit
            echo ""
            read -r -p "Press Enter to return to menu..."
            ;;
        5)
            echo ""
            "$PYTHON_CMD" -m smart_drive clean --dry-run
            echo ""
            read -r -p "Execute safe Tier 1 purge? [y/N]: " CONF_CLN
            if [ "$CONF_CLN" = "y" ] || [ "$CONF_CLN" = "Y" ]; then
                echo ""
                "$PYTHON_CMD" -m smart_drive clean --apply
            else
                echo ""
                echo "Safe purge aborted."
            fi
            echo ""
            read -r -p "Press Enter to return to menu..."
            ;;
        6)
            echo ""
            "$PYTHON_CMD" -m smart_drive organize
            echo ""
            read -r -p "Apply auto-zoning reorganization? [y/N]: " CONF_ORG
            if [ "$CONF_ORG" = "y" ] || [ "$CONF_ORG" = "Y" ]; then
                echo ""
                "$PYTHON_CMD" -m smart_drive organize --apply
            else
                echo ""
                echo "Reorganization plan aborted."
            fi
            echo ""
            read -r -p "Press Enter to return to menu..."
            ;;
        7)
            echo ""
            "$PYTHON_CMD" -m smart_drive mcp register 2>/dev/null || "$PYTHON_CMD" -m smart_drive mcp-config
            echo ""
            read -r -p "Press Enter to return to menu..."
            ;;
        8)
            echo ""
            echo "Starting Web Dashboard (Press Ctrl+C to stop)..."
            "$PYTHON_CMD" -m smart_drive ui
            echo ""
            read -r -p "Press Enter to return to menu..."
            ;;
        0)
            echo ""
            echo "Exiting SmartDrive-OS. Goodbye!"
            break
            ;;
        *)
            ;;
    esac
done

# Tier 5: Safe Pause on Exit
echo ""
