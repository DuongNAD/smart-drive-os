"""smart_drive.ui - Zero-Dependency Web Dashboard and Visual UI.

Provides pure Python standard library HTTP server and embedded Dark Mode SPA
for visual storage auditing, 512KB cluster slack metrics, FTS5 instant search,
and 3-tier safe cleanup.
"""

from __future__ import annotations

from smart_drive.ui.dashboard import get_dashboard_html
from smart_drive.ui.server import (
    SmartDriveRequestHandler,
    ThreadingHTTPServer,
    create_server,
    run_server,
)

__all__ = [
    "SmartDriveRequestHandler",
    "ThreadingHTTPServer",
    "create_server",
    "run_server",
    "get_dashboard_html",
]
