#!/bin/bash
# SmartDrive-OS - Instant FTS5 Search (macOS / Linux)
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
$PYTHON_CMD -m smart_drive search "$QUERY"

echo ""
read -r -p "Press Enter to exit..."
