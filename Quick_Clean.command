#!/bin/bash
# SmartDrive-OS - Safe Junk Cleaner (macOS / Linux)
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
else
    echo "========================================================"
    echo "  [ERROR] Python 3 not found on system PATH!"
    echo "========================================================"
    echo ""
    read -r -p "Press Enter to exit..."
    exit 1
fi

echo "========================================================"
echo "  SmartDrive-OS: Safe Junk Cleaner (Dry-Run Preview)"
echo "========================================================"
echo ""

$PYTHON_CMD -m smart_drive clean --dry-run

echo ""
read -r -p "Execute safe Tier 1 purge? [y/N]: " CONFIRM
if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo ""
    echo "Purging safe junk files..."
    $PYTHON_CMD -m smart_drive clean --apply
else
    echo ""
    echo "Safe purge aborted. No files were modified."
fi

echo ""
read -r -p "Press Enter to exit..."
