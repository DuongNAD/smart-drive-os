#!/bin/bash
# SmartDrive-OS - 1-Touch SSD Setup (macOS / Linux)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [ -d "$DIR/smart_drive" ]; then
    cd "$DIR" || exit 1
elif [ -d "$DIR/../smart_drive" ]; then
    cd "$DIR/.." || exit 1
else
    cd "$DIR" || exit 1
fi

if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
elif command -v py >/dev/null 2>&1; then
    PYTHON_CMD="py"
else
    echo "========================================================"
    echo "  [ERROR] Python 3 not found on system PATH!"
    echo "========================================================"
    echo "  Please install Python 3.9+ from https://www.python.org/"
    echo ""
    read -r -p "Press Enter to exit..."
    exit 1
fi

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

$PYTHON_CMD -m smart_drive init --profile "$PROFILE"

echo ""
echo "========================================================"
echo "  Initialization complete!"
echo "========================================================"
echo ""
read -r -p "Press Enter to exit..."
