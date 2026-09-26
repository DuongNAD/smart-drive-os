"""__main__.py - Package entry point for smart_drive.

Enables invocation via:
    python -m smart_drive <subcommand> [options]
Forwards sys.argv arguments to smart_drive.cli.main:main().
"""
from __future__ import annotations

import sys
from smart_drive.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
