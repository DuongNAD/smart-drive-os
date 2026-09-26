# Milestone 1 Technical Implementation Blueprint: Zero-Dependency Web Dashboard & Visual UI (`smart-drive ui`)

**Document ID**: `SMART-DRIVE-M1-BLUEPRINT`  
**Target Release**: SmartDrive-OS v1.1.0  
**Milestone**: M1 (Features F01 through F11)  
**Author**: Explorer M1  
**Status**: Ready for Implementation  

---

## 1. Executive Summary & Problem Boundary

### 1.1 Objective
Milestone 1 introduces a high-performance, visual, local web management interface (`smart-drive ui`) for SmartDrive-OS. It provides operators and developers with an intuitive visual dashboard to monitor 512KB cluster slack waste across exFAT volumes, perform sub-10ms instant FTS5 file searches with interactive filters, and safely execute 3-tier junk purges with dry-run simulations and 1-click confirmation dialogs.

### 1.2 Core Constraints & Architectural Directives
1. **Zero External Dependencies**: The entire HTTP server, JSON REST API, and embedded web UI must run strictly on the Python Standard Library (`http.server`, `urllib.parse`, `json`, `socketserver`, `threading`, `webbrowser`, `unittest`). No Flask, FastAPI, Starlette, Jinja2, or requests are permitted.
2. **Zero CDN / 100% Offline Single Page Application (SPA)**: The embedded UI in `smart_drive/ui/dashboard.py` must be completely self-contained. No external fonts, external stylesheets, or remote JavaScript libraries (Google Fonts, Tailwind CDN, Bootstrap, FontAwesome, React, Vue, jQuery) are allowed. All icons must be pure inline SVGs, styles must be embedded modern CSS, and logic must be vanilla JavaScript.
3. **Inviolable Security Guard Enforcement**: Purge operations initiated from the web interface must execute strictly through `PurgeEngine` and `SecurityGuard`, preventing accidental deletion of root configuration files (`GEMINI.md`, `CLAUDE.md`, `AGENTS.md`, `README.md`), anti-indexing shield markers (`.metadata_never_index`, `.fseventsd/no_log`), or entire business taxonomy directories.
4. **Hardware-Accurate 512KB Cluster Geometry**: All storage breakdown and waste visualizations must reflect the 524,288-byte cluster allocation mechanics of Kingston XS2000 exFAT storage.

---

## 2. Package Architecture & File Layout

The implementation will introduce a new top-level subpackage `smart_drive.ui` and register the `ui` command in the CLI.

```
smart_drive/
├── ui/
│   ├── __init__.py           # Package interface exporting run_server, create_server, get_dashboard_html
│   ├── server.py             # ThreadingHTTPServer, REST API request handlers, CORS & error handling
│   └── dashboard.py          # Embedded Dark Mode SPA HTML, CSS & Vanilla JS generator
├── cli/
│   ├── cmd_ui.py             # Subcommand handler for `smart-drive ui`
│   └── main.py               # Subparser registration for `ui` and dispatcher binding
tests/
└── test_ui.py                # 100% standard library unit test suite for server, API routes, and CLI
```

---

## 3. Feature Breakdown & Technical Specifications (F01 – F11)

### F01: CLI Subcommand `smart-drive ui`
- **Location**: `smart_drive/cli/cmd_ui.py` registered in `smart_drive/cli/main.py`.
- **CLI Syntax**:
  ```bash
  smart-drive ui [--root <path>] [--port <port>] [--no-browser]
  ```
- **Arguments**:
  - `--port`: HTTP listen port. Integer, default `8765`.
  - `--no-browser`: Flag to suppress automatic browser launching. Default `False` (opens browser unless specified).
  - `--root`: Target SSD root path. Optional; defaults to `detect_default_root()`.
- **Behavior**: Resolves database path via `get_default_db_path(root)`, prints launch banner with the server URL (`http://127.0.0.1:<port>`), spawns the browser if not disabled, and blocks in `serve_forever()` until `SIGINT` / `Ctrl+C`.

### F02: Standard Library HTTP Server
- **Location**: `smart_drive/ui/server.py`.
- **Engine**: Standard library `socketserver.ThreadingMixIn` combined with `http.server.HTTPServer` (or `http.server.ThreadingHTTPServer` on Python 3.7+).
- **Properties**:
  - `daemon_threads = True`: Prevents worker threads from blocking process shutdown on SIGINT.
  - `allow_reuse_address = True`: Prevents `OSError: [Errno 98/10048] Address already in use` upon rapid restarts.
- **Request Context**: Context objects (`root_path`, `db_path`, `cluster_size`) are attached directly to the server instance (`self.server.root_path`, `self.server.db_path`), allowing clean, thread-safe access from within `SmartDriveRequestHandler`.

### F03: Embedded Dark Mode SPA
- **Location**: `smart_drive/ui/dashboard.py`.
- **Design Aesthetic**:
  - Premium dark theme inspired by modern developer platforms:
    - Primary background: `#0b0f19` (Obsidian deep dark).
    - Surface / Card background: `#1e293b` (Slate).
    - Borders: `#334155`.
    - Text: `#f8fafc` (Primary), `#94a3b8` (Secondary), `#64748b` (Muted).
    - Accents: `#38bdf8` (Cyan), `#10b981` (Emerald), `#f59e0b` (Amber), `#f43f5e` (Rose), `#818cf8` (Indigo).
  - Typography: Native modern system font stack (`system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif`).
  - Layout: Responsive CSS Grid and Flexbox with mobile/tablet breakpoints (`@media (max-width: 900px)`).
  - Navigation: Header status bar with drive metrics + tabbed view switcher:
    - Tab 1: **Storage & Slack Overview**
    - Tab 2: **Instant FTS5 Search**
    - Tab 3: **Safe 3-Tier Cleanup**

### F04: Taxonomy Breakdown Visualization
- **Data Source**: `GET /api/audit` calling `StorageAuditor.run_audit()`.
- **Content**:
  - 6 Canonical Kingston Taxonomies:
    - `01_AI_Models`
    - `02_Learning_Knowledge`
    - `03_Personal_Documents` / `03_Development_Projects`
    - `04_Creative_Assets` / `04_System_Workspaces`
    - `05_Dev_Toolbox`
    - `06_Archives_Storage`
  - Workspace & System aggregates.
- **Visualization**:
  - Proportional CSS horizontal bars indicating logical bytes, physical allocated bytes, and slack waste ratio.
  - Interactive metric cards showing file counts, directory counts, nominal bytes, and waste percentage.

### F05: 512KB Cluster Slack Visualization
- **Data Source**: `GET /api/audit` summary and `top_slack_directories`.
- **Content**:
  - Global slack overhead gauge displaying percentage of physical storage lost to 512KB cluster slack.
  - Comparison cards: Nominal Size vs Physical Allocated Size vs Cluster Slack Wasted.
  - Allocated Cluster Count (`allocated_bytes // 524288`).
  - Cluster Slack Hotspots Table: Top 10 directories with highest recursive cluster slack caused by small files, showing path, recursive file count, recursive nominal size, wasted slack bytes, and slack percentage.

### F06: Instant FTS5 Search API
- **Endpoint**: `GET /api/search`
- **Query Parameters**:
  - `q`: Search keyword or compound query (e.g. `llama`, `ext:pdf size:>10MB`).
  - `category`: Category name (`AI Models`, `Code`, `Books/Learning`, `Docs`, `Media`, `Archives`, `System Junk`).
  - `ext`: Comma-separated extensions (e.g. `gguf,safetensors` or `.pdf`).
  - `min_size`: Minimum nominal size in bytes.
  - `max_size`: Maximum nominal size in bytes.
  - `limit`: Result count (default 100, max 1000).
  - `offset`: Pagination offset (default 0).
- **Execution**:
  - Validates `db_path.exists()`. If not found, returns `{ "results": [], "total": 0, "latency_ms": 0.0, "index_exists": false }`.
  - Connects to SQLite via `DatabaseManager` and calls `SearchEngine.search(params)`.
  - Sub-10ms response target achieved via SQLite FTS5 index with BM25 ranking.
- **Response Schema**:
  ```json
  {
    "results": [
      {
        "id": 1,
        "path": "01_AI_Models/GGUF/llama-3-8b.Q4_K_M.gguf",
        "name": "llama-3-8b.Q4_K_M.gguf",
        "extension": ".gguf",
        "size": 10240,
        "allocated_size": 524288,
        "slack_bytes": 514048,
        "mtime": 1727330000.0,
        "category": "AI Models",
        "rank": -1.234
      }
    ],
    "total": 1,
    "latency_ms": 3.45,
    "index_exists": true
  }
  ```

### F07: Interactive Search Filters in UI
- **Front-end Controls**:
  - Real-time search input with 150ms debouncing.
  - Category selector dropdown / pill buttons.
  - Quick-extension badges (`.gguf`, `.pdf`, `.py`, `.json`, `.zip`, `.parquet`).
  - Size quick-filters (`All Sizes`, `> 100 MB`, `> 10 MB`, `> 1 MB`, `< 100 KB`, `0 bytes`).
  - Live result count & latency indicator (`⚡ 2.8ms | 24 results`).
  - Sortable results table with column headers: Name, Path, Category, Size, Slack, Modified.

### F08: 3-Tier Safe Cleanup Dashboard
- **Data Source**: `GET /api/junk` calling `JunkDetector.find_junk()`.
- **Tier Architecture**:
  - **Tier 1 (Safe OS Junk)**: AppleDouble `._*`, `.DS_Store`, `Thumbs.db`, `__pycache__`, `*.pyc`.
  - **Tier 2 (Dev & Build Caches)**: `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `build`, `dist`, `*.egg-info`.
  - **Tier 3 (Sensitive & Temp Dumps)**: `*.dmp`, `core.*`, `*.tmp`, `*.swp`.
- **UI Presentation**:
  - 3 interactive tier summary cards with toggle checkboxes.
  - Total items, nominal bytes, and cluster slack bytes reclaimable per tier.
  - Warning badges and descriptions explaining what each tier contains.

### F09: Cleanup Dry-Run Preview
- **Behavior**:
  - "Scan / Preview" button triggers `GET /api/junk`.
  - Shows candidate item list in an expandable data table before any unlinking occurs.
  - Displays each file's relative path, size, allocated size, and matched rule description.
  - Zero files are touched during scan/preview.

### F10: 1-Click Confirmed Purge
- **Endpoint**: `POST /api/junk/clean`
- **Request Body**:
  ```json
  {
    "tiers": [1],
    "dry_run": false
  }
  ```
- **Execution & Safeguards**:
  - In UI: Clicking "Clean Selected Tiers" does NOT immediately delete. It triggers a custom modal confirmation dialog:
    > "Confirm Permanent Purge: You are about to purge <N> items across selected tiers (<size> reclaimable). This action cannot be undone. Proceed?"
  - Only when the user clicks "Yes, Purge Now" is the `POST` request sent with `dry_run: false`.
  - The backend executes `PurgeEngine.purge_items()`, enforcing `SecurityGuard` boundary checks.
  - Returns reclaimed statistics (`nominal_bytes_reclaimed`, `allocated_bytes_reclaimed`, `records`).
  - UI updates live with a success toast notification and automatically refreshes the audit and junk summaries.

### F11: Headless Fallback
- **Cross-Platform Browser Launch**:
  - Wrapped in `try...except (Exception, webbrowser.Error)`.
  - In headless environments (CI/CD, Docker containers, remote SSH, Windows headless runners), if `webbrowser.open()` fails or cannot find a display/browser, the error is caught, logged as a warning (`"Could not open web browser automatically: <err>"`), and server execution continues unhindered.
  - Suppressed cleanly when `--no-browser` is passed on CLI.

---

## 4. REST API Endpoint Specifications

All API endpoints return JSON with header `Content-Type: application/json; charset=utf-8` and CORS header `Access-Control-Allow-Origin: *`.

| Method | Endpoint | Query / Body Params | Response Status | Response Description |
|---|---|---|---|---|
| `GET` | `/` | none | `200 OK` | Serves embedded SPA HTML (`text/html; charset=utf-8`) |
| `GET` | `/api/status` | none | `200 OK` | System status, drive root, version, cluster size, index info |
| `GET` | `/api/audit` | none | `200 OK` | Taxonomy and 512KB cluster slack audit report |
| `GET` | `/api/search` | `q`, `category`, `ext`, `min_size`, `max_size`, `limit`, `offset` | `200 OK` | FTS5 search results with sub-10ms BM25 ranking and latency |
| `GET` | `/api/junk` | none | `200 OK` | Detected junk categorized into Tiers 1, 2, 3 with slack |
| `POST` | `/api/junk/clean` | `{"tiers": [1, 2], "dry_run": bool, "paths": [...]}` | `200 OK` | Purge execution or dry-run simulation with reclaimed stats |
| `OPTIONS` | any `/api/*` | none | `204 No Content` | CORS preflight response |

---

## 5. Implementation Code Blueprints

### 5.1 `smart_drive/ui/__init__.py`
```python
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
```

### 5.2 `smart_drive/ui/server.py`
```python
"""smart_drive.ui.server - Zero-Dependency Threading HTTP Server & REST API.

Provides local REST API endpoints:
- GET /: Embedded Dark Mode SPA HTML
- GET /api/status: System status, drive root, index status, cluster geometry
- GET /api/audit: Storage breakdown & 512KB cluster slack audit
- GET /api/search: Sub-10ms SQLite FTS5 search with compound filters
- GET /api/junk: 3-tier junk detection preview
- POST /api/junk/clean: Dry-run simulation and safe junk purge execution
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import socketserver
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.config import CLUSTER_SIZE_BYTES, JunkTier, calculate_allocated_bytes, calculate_slack_bytes
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine
from smart_drive.indexer.db import DatabaseManager
from smart_drive.search.engine import SearchEngine
from smart_drive.search.parser import SearchParams, parse_search_query
from smart_drive.ui.dashboard import get_dashboard_html

logger = logging.getLogger("smart_drive.ui.server")


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded HTTP server using daemon threads for clean process shutdown."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        RequestHandlerClass: type,
        root_path: str,
        db_path: Optional[str] = None,
        cluster_size: int = CLUSTER_SIZE_BYTES,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.root_path = os.path.realpath(os.path.abspath(root_path))
        self.db_path = os.path.realpath(os.path.abspath(db_path)) if db_path else os.path.join(self.root_path, ".smart_drive", "index.db")
        self.cluster_size = cluster_size


class SmartDriveRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler dispatching REST API routes and serving the embedded SPA."""

    server: ThreadingHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        """Route request logs to Python logging to keep stdout clean."""
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _set_cors_headers(self) -> None:
        """Set standard CORS headers for local API consumption."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def send_json_response(self, data: Any, status: int = 200) -> None:
        """Helper to serialize data as JSON response with correct headers."""
        try:
            payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        except Exception as exc:
            logger.error("JSON serialization failed: %s", exc)
            self.send_error_response(f"Serialization error: {exc}", status=500)
            return

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def send_html_response(self, html_content: str, status: int = 200) -> None:
        """Helper to send HTML content."""
        payload = html_content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def send_error_response(self, message: str, status: int = 400) -> None:
        """Helper to send structured error JSON."""
        err_data = {"error": message, "status": status}
        self.send_json_response(err_data, status=status)

    def do_OPTIONS(self) -> None:
        """Handles CORS preflight requests."""
        self.send_response(204)
        self._set_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        """Dispatch GET requests."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query_params = urllib.parse.parse_qs(parsed_url.query)

        try:
            if path in ("", "/"):
                self.handle_dashboard()
            elif path == "/api/status":
                self.handle_api_status()
            elif path == "/api/audit":
                self.handle_api_audit()
            elif path == "/api/search":
                self.handle_api_search(query_params)
            elif path == "/api/junk":
                self.handle_api_junk()
            else:
                self.send_error_response(f"Endpoint not found: {path}", status=404)
        except Exception as exc:
            logger.exception("Error processing GET %s: %s", self.path, exc)
            self.send_error_response(f"Internal server error: {exc}", status=500)

    def do_POST(self) -> None:
        """Dispatch POST requests."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        try:
            if path == "/api/junk/clean":
                self.handle_api_junk_clean()
            else:
                self.send_error_response(f"Endpoint not found: {path}", status=404)
        except Exception as exc:
            logger.exception("Error processing POST %s: %s", self.path, exc)
            self.send_error_response(f"Internal server error: {exc}", status=500)

    # --------------------------------------------------------------------------
    # Route Handlers
    # --------------------------------------------------------------------------

    def handle_dashboard(self) -> None:
        """Serves the self-contained SPA HTML."""
        html = get_dashboard_html()
        self.send_html_response(html)

    def handle_api_status(self) -> None:
        """Returns drive root, cluster geometry, and database readiness."""
        db_exists = os.path.exists(self.server.db_path)
        db_size = os.path.getsize(self.server.db_path) if db_exists else 0

        data = {
            "status": "ready",
            "version": "1.1.0",
            "root": self.server.root_path,
            "cluster_size_bytes": self.server.cluster_size,
            "cluster_size_kb": self.server.cluster_size // 1024,
            "database": {
                "path": self.server.db_path,
                "exists": db_exists,
                "size_bytes": db_size,
            },
            "index_exists": db_exists,
        }
        self.send_json_response(data)

    def handle_api_audit(self) -> None:
        """Performs storage audit and returns taxonomy & cluster slack data."""
        auditor = StorageAuditor(self.server.root_path, cluster_size=self.server.cluster_size)
        audit_res = auditor.run_audit()
        self.send_json_response(audit_res)

    def handle_api_search(self, query_params: Dict[str, List[str]]) -> None:
        """Executes FTS5 search with compound filters and measures latency."""
        t0 = time.perf_counter()
        q_raw = query_params.get("q", [""])[0]
        cat_param = query_params.get("category", [""])[0]
        ext_param = query_params.get("ext", [""])[0]
        limit_param = int(query_params.get("limit", [100])[0])
        offset_param = int(query_params.get("offset", [0])[0])

        limit = max(1, min(limit_param, 1000))
        offset = max(0, offset_param)

        db_path = self.server.db_path
        if not os.path.exists(db_path):
            self.send_json_response({
                "results": [],
                "matches": [],
                "total": 0,
                "total_count": 0,
                "latency_ms": 0.0,
                "elapsed_ms": 0.0,
                "index_exists": False,
                "message": "SQLite search index not found. Run 'smart-drive index' to build index.",
            })
            return

        # Parse compound query or build SearchParams
        if q_raw:
            params = parse_search_query(q_raw)
        else:
            params = SearchParams()

        params.limit = limit
        params.offset = offset

        if cat_param:
            params.category = cat_param
        if ext_param:
            for ext in ext_param.split(","):
                clean = ext.strip().lstrip(".")
                if clean:
                    params.extensions.add(clean)

        min_size = query_params.get("min_size", [None])[0]
        max_size = query_params.get("max_size", [None])[0]
        if min_size is not None and min_size.isdigit():
            params.min_size = int(min_size)
        if max_size is not None and max_size.isdigit():
            params.max_size = int(max_size)

        db = DatabaseManager(db_path)
        engine = SearchEngine(db)
        res = engine.search(params)

        matches_data = []
        for m in res.matches:
            item_dict = m.to_dict()
            # Augment with exact cluster allocation
            alloc = calculate_allocated_bytes(m.size, self.server.cluster_size)
            item_dict["allocated_size"] = alloc
            item_dict["slack_bytes"] = alloc - m.size
            matches_data.append(item_dict)

        total_latency = (time.perf_counter() - t0) * 1000.0

        self.send_json_response({
            "results": matches_data,
            "matches": matches_data,
            "total": res.total_count,
            "total_count": res.total_count,
            "latency_ms": round(total_latency, 2),
            "elapsed_ms": round(total_latency, 2),
            "index_exists": True,
        })

    def handle_api_junk(self) -> None:
        """Scans drive and returns 3-tier categorized junk preview with slack metrics."""
        detector = JunkDetector(self.server.root_path, max_tier=JunkTier.TIER_3_SENSITIVE, cluster_size=self.server.cluster_size)
        all_junk = detector.find_junk()

        tier_groups: Dict[int, List[Dict[str, Any]]] = {1: [], 2: [], 3: []}
        tier_stats: Dict[int, Dict[str, Any]] = {
            1: {"name": "Tier 1: Safe OS Junk", "items": [], "count": 0, "nominal_bytes": 0, "allocated_bytes": 0, "slack_bytes": 0},
            2: {"name": "Tier 2: Dev & Build Caches", "items": [], "count": 0, "nominal_bytes": 0, "allocated_bytes": 0, "slack_bytes": 0},
            3: {"name": "Tier 3: Temporary & Crash Dumps", "items": [], "count": 0, "nominal_bytes": 0, "allocated_bytes": 0, "slack_bytes": 0},
        }

        total_nominal = 0
        total_allocated = 0

        for item in all_junk:
            t = int(item.tier)
            item_dict = item.to_dict()
            slack = item.allocated_size - item.size
            item_dict["slack_bytes"] = slack
            tier_groups[t].append(item_dict)

            tier_stats[t]["items"].append(item_dict)
            tier_stats[t]["count"] += 1
            tier_stats[t]["nominal_bytes"] += item.size
            tier_stats[t]["allocated_bytes"] += item.allocated_size
            tier_stats[t]["slack_bytes"] += slack

            total_nominal += item.size
            total_allocated += item.allocated_size

        total_slack = total_allocated - total_nominal

        response = {
            "tiers": {str(k): v for k, v in tier_stats.items()},
            "tier1": tier_groups[1],
            "tier2": tier_groups[2],
            "tier3": tier_groups[3],
            "summary": {
                "total_items": len(all_junk),
                "total_nominal_bytes": total_nominal,
                "total_allocated_bytes": total_allocated,
                "total_slack_bytes": total_slack,
            },
            "total_bytes": total_nominal,
            "total_slack_bytes": total_slack,
        }
        self.send_json_response(response)

    def handle_api_junk_clean(self) -> None:
        """Executes dry-run preview or confirmed purge across selected tiers."""
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            self.send_error_response("Missing JSON request body", status=400)
            return

        try:
            body = self.rfile.read(content_len)
            req_data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            self.send_error_response(f"Invalid JSON: {exc}", status=400)
            return

        selected_tiers = req_data.get("tiers", [1])
        dry_run = bool(req_data.get("dry_run", True))
        specific_paths = req_data.get("paths", None)

        if not isinstance(selected_tiers, list) or not selected_tiers:
            self.send_error_response("Field 'tiers' must be a non-empty list of integers", status=400)
            return

        # Scan for junk items
        max_tier_req = max(selected_tiers)
        try:
            detector_max = JunkTier(max_tier_req)
        except ValueError:
            detector_max = JunkTier.TIER_3_SENSITIVE

        detector = JunkDetector(self.server.root_path, max_tier=detector_max, cluster_size=self.server.cluster_size)
        items = detector.find_junk()

        # Filter candidates by selected tiers
        candidates = [item for item in items if int(item.tier) in selected_tiers]

        # Filter candidates by specific paths if requested
        if specific_paths is not None:
            path_set = set(specific_paths)
            candidates = [c for c in candidates if c.path in path_set or c.rel_path in path_set]

        # Execute purge or simulation
        engine = PurgeEngine(self.server.root_path, dry_run=dry_run, cluster_size=self.server.cluster_size)
        summary = engine.purge_items(candidates)

        response = {
            "dry_run": summary.dry_run,
            "total_attempted": summary.total_attempted,
            "total_succeeded": summary.total_succeeded,
            "total_blocked": summary.total_blocked,
            "total_failed": summary.total_failed,
            "nominal_bytes_reclaimed": summary.nominal_bytes_reclaimed,
            "allocated_bytes_reclaimed": summary.allocated_bytes_reclaimed,
            "reclaimed_bytes": summary.nominal_bytes_reclaimed,
            "purged": [r.to_dict() for r in summary.records if r.status in ("DELETED", "SIMULATED")],
            "records": [r.to_dict() for r in summary.records],
        }
        self.send_json_response(response)


def create_server(
    root_path: Union[str, Path],
    port: int = 8765,
    db_path: Optional[Union[str, Path]] = None,
    host: str = "127.0.0.1",
    cluster_size: int = CLUSTER_SIZE_BYTES,
) -> ThreadingHTTPServer:
    """Instantiates a configured ThreadingHTTPServer ready for serve_forever()."""
    server_address = (host, port)
    return ThreadingHTTPServer(
        server_address=server_address,
        RequestHandlerClass=SmartDriveRequestHandler,
        root_path=str(root_path),
        db_path=str(db_path) if db_path else None,
        cluster_size=cluster_size,
    )


def run_server(
    root_path: Union[str, Path],
    port: int = 8765,
    open_browser: bool = True,
    db_path: Optional[Union[str, Path]] = None,
    host: str = "127.0.0.1",
) -> None:
    """Runs the SmartDrive-OS HTTP server until interrupted."""
    server = create_server(root_path=root_path, port=port, db_path=db_path, host=host)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}"

    print("=" * 70)
    print(f"  SmartDrive-OS Web Dashboard (v1.1.0)")
    print(f"  Root:    {server.root_path}")
    print(f"  URL:     {url}")
    print(f"  Cluster: {server.cluster_size // 1024} KB ({server.cluster_size:,} bytes)")
    print(f"  Zero External Dependencies | 100% Offline Single Page Application")
    print("=" * 70)
    print("Press Ctrl+C to terminate the dashboard server.")

    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as exc:
            logger.warning("Could not launch web browser automatically: %s", exc)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down SmartDrive-OS Web Dashboard...")
    finally:
        server.shutdown()
        server.server_close()
        print("Server stopped cleanly.")
```

### 5.3 `smart_drive/ui/dashboard.py`
The embedded SPA returns a complete HTML document with responsive CSS and vanilla JS:
- Modern CSS Variables for Dark Mode styling.
- Pure inline SVGs for UI icons (Drive, Search, Trash, Chart, Checkmark, Warning, Sparkles, Close).
- Tabs for Overview, Instant Search, Safe Cleanup.
- Vanilla JS event listeners for:
  - Polling `/api/status`, `/api/audit`, and `/api/junk` on load.
  - Interactive search input with 150ms debounce calling `/api/search?q=...`.
  - Category selector buttons updating query parameters.
  - Tier selection checkboxes and "Preview Candidates" table toggle.
  - Confirmation Modal for 1-click purge action sending `POST /api/junk/clean` with `dry_run: false`.
  - Reusable formatting helpers for bytes and counts matching backend formatting.

```python
"""smart_drive.ui.dashboard - Embedded Dark Mode Single Page Application.

Zero external dependencies. No CDN. No external scripts or fonts. 100% Offline.
"""

def get_dashboard_html() -> str:
    """Generates the complete HTML/CSS/JS source for the SmartDrive-OS Web Dashboard."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SmartDrive-OS Dashboard</title>
  <style>
    :root {
      --bg-base: #0b0f19;
      --bg-surface: #111827;
      --bg-card: #1e293b;
      --bg-card-hover: #283548;
      --border-base: #334155;
      --border-subtle: #1e293b;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --text-faint: #64748b;
      --accent-cyan: #38bdf8;
      --accent-emerald: #10b981;
      --accent-amber: #f59e0b;
      --accent-rose: #f43f5e;
      --accent-indigo: #818cf8;
      --accent-purple: #c084fc;
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 16px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-base);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      line-height: 1.5;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border-base);
      padding: 16px 28px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-icon {
      width: 32px;
      height: 32px;
      background: linear-gradient(135deg, var(--accent-cyan), var(--accent-indigo));
      border-radius: var(--radius-sm);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #0b0f19;
      font-weight: 800;
      font-size: 18px;
    }
    .brand-title {
      font-size: 18px;
      font-weight: 700;
      letter-spacing: -0.02em;
    }
    .brand-badge {
      font-size: 11px;
      background: rgba(56, 189, 248, 0.15);
      color: var(--accent-cyan);
      padding: 2px 8px;
      border-radius: 9999px;
      border: 1px solid rgba(56, 189, 248, 0.3);
      font-weight: 600;
    }
    .nav-tabs {
      display: flex;
      gap: 8px;
    }
    .nav-tab {
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-muted);
      padding: 8px 16px;
      border-radius: var(--radius-sm);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.15s ease;
    }
    .nav-tab:hover {
      background: var(--bg-card);
      color: var(--text-main);
    }
    .nav-tab.active {
      background: var(--bg-card);
      color: var(--accent-cyan);
      border-color: var(--border-base);
    }
    main {
      flex: 1;
      max-width: 1440px;
      width: 100%;
      margin: 0 auto;
      padding: 24px;
    }
    .drive-banner {
      background: linear-gradient(180deg, var(--bg-surface), var(--bg-card));
      border: 1px solid var(--border-base);
      border-radius: var(--radius-lg);
      padding: 20px 24px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }
    .drive-meta h2 {
      font-size: 16px;
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 4px;
    }
    .drive-meta .path {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 15px;
      color: var(--accent-cyan);
      word-break: break-all;
    }
    .metrics-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .metric-card {
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-md);
      padding: 16px 20px;
      position: relative;
    }
    .metric-card .label {
      font-size: 13px;
      color: var(--text-muted);
      font-weight: 500;
      margin-bottom: 6px;
    }
    .metric-card .val {
      font-size: 24px;
      font-weight: 700;
      letter-spacing: -0.02em;
    }
    .metric-card .sub {
      font-size: 12px;
      color: var(--text-faint);
      margin-top: 4px;
    }
    .metric-slack {
      border-color: rgba(245, 158, 11, 0.4);
      background: linear-gradient(135deg, var(--bg-card), rgba(245, 158, 11, 0.05));
    }
    .metric-slack .val {
      color: var(--accent-amber);
    }
    .section-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-lg);
      padding: 24px;
      margin-bottom: 24px;
    }
    .section-title {
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    /* Taxonomy Bars */
    .tax-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
    }
    .tax-card {
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-md);
      padding: 16px;
    }
    .tax-head {
      display: flex;
      justify-content: space-between;
      margin-bottom: 10px;
      font-size: 14px;
      font-weight: 600;
    }
    .progress-bar-bg {
      height: 8px;
      background: var(--bg-surface);
      border-radius: 4px;
      overflow: hidden;
      margin-bottom: 8px;
      position: relative;
    }
    .progress-bar-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--accent-cyan), var(--accent-indigo));
      border-radius: 4px;
      transition: width 0.3s ease;
    }
    .progress-bar-slack {
      background: linear-gradient(90deg, var(--accent-amber), var(--accent-rose));
    }
    .tax-metrics {
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      color: var(--text-muted);
    }
    /* Tables */
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      text-align: left;
    }
    th {
      background: var(--bg-card);
      color: var(--text-muted);
      font-weight: 600;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border-base);
    }
    td {
      padding: 10px 14px;
      border-bottom: 1px solid var(--border-subtle);
      color: var(--text-main);
    }
    tr:hover td {
      background: rgba(255, 255, 255, 0.02);
    }
    .code-font {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
    }
    /* Search Bar & Filters */
    .search-box-row {
      display: flex;
      gap: 12px;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }
    .search-input-wrap {
      flex: 1;
      position: relative;
      min-width: 280px;
    }
    .search-input {
      width: 100%;
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-sm);
      padding: 12px 16px;
      color: var(--text-main);
      font-size: 15px;
      outline: none;
      transition: border-color 0.15s ease;
    }
    .search-input:focus {
      border-color: var(--accent-cyan);
      box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2);
    }
    .filter-btn-group {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }
    .filter-chip {
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      color: var(--text-muted);
      padding: 6px 12px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
    }
    .filter-chip:hover {
      color: var(--text-main);
      border-color: var(--text-muted);
    }
    .filter-chip.active {
      background: rgba(56, 189, 248, 0.15);
      border-color: var(--accent-cyan);
      color: var(--accent-cyan);
    }
    /* Cleanup Tiers */
    .tier-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }
    .tier-box {
      background: var(--bg-card);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-md);
      padding: 20px;
      position: relative;
    }
    .tier-box.selected {
      border-color: var(--accent-cyan);
      box-shadow: 0 0 0 1px var(--accent-cyan);
    }
    .tier-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }
    .tier-title {
      font-size: 15px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .tier-checkbox {
      width: 18px;
      height: 18px;
      cursor: pointer;
      accent-color: var(--accent-cyan);
    }
    .badge {
      font-size: 11px;
      padding: 2px 6px;
      border-radius: 4px;
      font-weight: 600;
      text-transform: uppercase;
    }
    .badge-safe { background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald); }
    .badge-dev { background: rgba(56, 189, 248, 0.15); color: var(--accent-cyan); }
    .badge-optin { background: rgba(245, 158, 11, 0.15); color: var(--accent-amber); }
    /* Buttons */
    .btn {
      padding: 10px 18px;
      border-radius: var(--radius-sm);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid transparent;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: all 0.15s ease;
    }
    .btn-primary {
      background: var(--accent-cyan);
      color: #0b0f19;
    }
    .btn-primary:hover {
      background: #7dd3fc;
    }
    .btn-danger {
      background: var(--accent-rose);
      color: #fff;
    }
    .btn-danger:hover {
      background: #fb7185;
    }
    .btn-secondary {
      background: var(--bg-card);
      border-color: var(--border-base);
      color: var(--text-main);
    }
    .btn-secondary:hover {
      background: var(--bg-card-hover);
    }
    /* Modal Dialog */
    .modal-backdrop {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(4px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 100;
    }
    .modal-box {
      background: var(--bg-surface);
      border: 1px solid var(--border-base);
      border-radius: var(--radius-lg);
      max-width: 520px;
      width: 90%;
      padding: 24px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    }
    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 12px;
      margin-top: 24px;
    }
    .hidden { display: none !important; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-icon">S</div>
      <div>
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="brand-title">SmartDrive-OS</span>
          <span class="brand-badge">v1.1.0</span>
        </div>
      </div>
    </div>
    <div class="nav-tabs">
      <button class="nav-tab active" onclick="switchTab('overview')">Overview & Slack</button>
      <button class="nav-tab" onclick="switchTab('search')">Instant Search</button>
      <button class="nav-tab" onclick="switchTab('cleanup')">Safe Cleanup</button>
    </div>
  </header>

  <main>
    <!-- Drive Info Banner -->
    <div class="drive-banner">
      <div class="drive-meta">
        <h2>Target Drive Root</h2>
        <div class="path" id="drive-root-display">Loading...</div>
      </div>
      <div style="display: flex; gap: 16px; align-items: center;">
        <div style="text-align: right;">
          <div style="font-size: 12px; color: var(--text-muted);">Cluster Allocation Unit</div>
          <div style="font-weight: 700; color: var(--accent-cyan);" id="cluster-size-display">512 KB</div>
        </div>
        <button class="btn btn-secondary" onclick="refreshAll()" title="Refresh Dashboard Data">⟳ Refresh</button>
      </div>
    </div>

    <!-- Global Metrics Cards -->
    <div class="metrics-row">
      <div class="metric-card">
        <div class="label">Total Files</div>
        <div class="val" id="metric-total-files">--</div>
        <div class="sub" id="metric-total-dirs">-- directories</div>
      </div>
      <div class="metric-card">
        <div class="label">Nominal Data Size</div>
        <div class="val" id="metric-nominal-bytes">--</div>
        <div class="sub">Logical byte length</div>
      </div>
      <div class="metric-card">
        <div class="label">Physical Allocated Space</div>
        <div class="val" id="metric-allocated-bytes">--</div>
        <div class="sub" id="metric-total-clusters">-- clusters (512KB)</div>
      </div>
      <div class="metric-card metric-slack">
        <div class="label">Wasted Cluster Slack</div>
        <div class="val" id="metric-slack-bytes">--</div>
        <div class="sub" id="metric-slack-pct">-- overhead</div>
      </div>
    </div>

    <!-- Tab 1: Overview & Cluster Slack -->
    <div id="tab-overview">
      <div class="section-card">
        <div class="section-title">
          <span>Canonical Taxonomy Storage Allocation</span>
          <span style="font-size: 13px; font-weight: 500; color: var(--text-muted);">Nominal vs 512KB Cluster Slack</span>
        </div>
        <div class="tax-grid" id="taxonomy-grid">
          <!-- Populated dynamically -->
        </div>
      </div>

      <div class="section-card">
        <div class="section-title">
          <span>Top Cluster Slack Hotspots</span>
          <span style="font-size: 13px; font-weight: 500; color: var(--text-muted);">Directories with excessive small file overhead</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>Subtree Directory</th>
              <th>Files</th>
              <th>Nominal Size</th>
              <th>Allocated</th>
              <th>Wasted Slack</th>
              <th>Waste %</th>
            </tr>
          </thead>
          <tbody id="slack-hotspots-table">
            <!-- Populated dynamically -->
          </tbody>
        </table>
      </div>
    </div>

    <!-- Tab 2: Instant Search -->
    <div id="tab-search" class="hidden">
      <div class="section-card">
        <div class="section-title">
          <span>Instant SQLite FTS5 Search</span>
          <span id="search-latency-badge" style="font-size: 12px; color: var(--accent-emerald); font-weight: 600;">⚡ Ready</span>
        </div>
        <div class="search-box-row">
          <div class="search-input-wrap">
            <input type="text" id="search-input" class="search-input" placeholder="Type keyword, phrase, or query syntax (e.g. llama, ext:gguf, size:>100MB)..." oninput="onSearchInput()">
          </div>
          <button class="btn btn-secondary" onclick="executeSearch()">Search</button>
        </div>
        <div class="filter-btn-group" id="category-filter-chips">
          <button class="filter-chip active" onclick="setCategoryFilter('')">All Categories</button>
          <button class="filter-chip" onclick="setCategoryFilter('AI Models')">AI Models</button>
          <button class="filter-chip" onclick="setCategoryFilter('Code')">Code</button>
          <button class="filter-chip" onclick="setCategoryFilter('Books/Learning')">Books/Learning</button>
          <button class="filter-chip" onclick="setCategoryFilter('Docs')">Docs</button>
          <button class="filter-chip" onclick="setCategoryFilter('Media')">Media</button>
          <button class="filter-chip" onclick="setCategoryFilter('Archives')">Archives</button>
        </div>
        <div id="search-results-container">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Relative Path</th>
                <th>Category</th>
                <th>Nominal Size</th>
                <th>Cluster Slack</th>
                <th>Rank</th>
              </tr>
            </thead>
            <tbody id="search-results-body">
              <tr><td colspan="6" style="text-align: center; color: var(--text-faint);">Enter a keyword or click a category to search.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Tab 3: Safe Cleanup -->
    <div id="tab-cleanup" class="hidden">
      <div class="section-card">
        <div class="section-title">
          <span>Safe 3-Tier Storage Cleanup Dashboard</span>
          <button class="btn btn-secondary" onclick="loadJunkPreview()">⟳ Scan Junk</button>
        </div>
        <div class="tier-grid">
          <!-- Tier 1 -->
          <div class="tier-box selected" id="tier-box-1">
            <div class="tier-header">
              <div class="tier-title">
                <input type="checkbox" id="chk-tier-1" class="tier-checkbox" checked onchange="updateTierSelection()">
                <span>Tier 1: Safe OS Junk</span>
              </div>
              <span class="badge badge-safe">Recommended</span>
            </div>
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
              macOS AppleDouble (._*), .DS_Store, Windows Thumbs.db, and Python bytecode caches (__pycache__).
            </p>
            <div style="font-size: 12px; color: var(--text-faint);">
              Items: <strong id="t1-count" style="color: var(--text-main);">0</strong> | 
              Slack: <strong id="t1-slack" style="color: var(--accent-amber);">0 B</strong>
            </div>
          </div>
          <!-- Tier 2 -->
          <div class="tier-box" id="tier-box-2">
            <div class="tier-header">
              <div class="tier-title">
                <input type="checkbox" id="chk-tier-2" class="tier-checkbox" onchange="updateTierSelection()">
                <span>Tier 2: Dev & Test Caches</span>
              </div>
              <span class="badge badge-dev">Regenerable</span>
            </div>
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
              Pytest, Ruff, Mypy caches, and packaging build artifacts (build, dist).
            </p>
            <div style="font-size: 12px; color: var(--text-faint);">
              Items: <strong id="t2-count" style="color: var(--text-main);">0</strong> | 
              Slack: <strong id="t2-slack" style="color: var(--accent-amber);">0 B</strong>
            </div>
          </div>
          <!-- Tier 3 -->
          <div class="tier-box" id="tier-box-3">
            <div class="tier-header">
              <div class="tier-title">
                <input type="checkbox" id="chk-tier-3" class="tier-checkbox" onchange="updateTierSelection()">
                <span>Tier 3: Temporary & Dumps</span>
              </div>
              <span class="badge badge-optin">Opt-in</span>
            </div>
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
              Crash dumps (*.dmp, core.*), temporary scratch files (*.tmp), and editor swap files.
            </p>
            <div style="font-size: 12px; color: var(--text-faint);">
              Items: <strong id="t3-count" style="color: var(--text-main);">0</strong> | 
              Slack: <strong id="t3-slack" style="color: var(--accent-amber);">0 B</strong>
            </div>
          </div>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
          <div style="font-size: 14px;">
            Selected for Purge: <strong id="selected-summary-count">0</strong> items (<strong id="selected-summary-nominal" style="color: var(--accent-cyan);">0 B</strong> nominal, <strong id="selected-summary-slack" style="color: var(--accent-amber);">0 B</strong> cluster slack)
          </div>
          <div style="display: flex; gap: 12px;">
            <button class="btn btn-secondary" onclick="simulateDryRun()">Dry-Run Simulation</button>
            <button class="btn btn-danger" onclick="openPurgeModal()">1-Click Clean Now</button>
          </div>
        </div>

        <div id="junk-items-preview">
          <table>
            <thead>
              <tr>
                <th>Tier</th>
                <th>File / Folder</th>
                <th>Rule Description</th>
                <th>Nominal Size</th>
                <th>Allocated Space</th>
                <th>Cluster Slack</th>
              </tr>
            </thead>
            <tbody id="junk-preview-tbody">
              <!-- Populated dynamically -->
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </main>

  <!-- Confirmation Modal -->
  <div id="purge-modal" class="modal-backdrop hidden">
    <div class="modal-box">
      <h3 style="font-size: 18px; margin-bottom: 12px; color: var(--accent-rose);">⚠️ Confirm Permanent Purge</h3>
      <p style="font-size: 14px; color: var(--text-muted); margin-bottom: 16px;">
        You are about to permanently purge <strong id="modal-item-count" style="color: var(--text-main);">0</strong> junk items.
        Anti-indexing shields and root configuration files remain protected.
      </p>
      <div style="background: var(--bg-card); padding: 12px; border-radius: var(--radius-sm); font-size: 13px; margin-bottom: 16px;">
        <div>Reclaimable Nominal Size: <strong id="modal-nominal-size" style="color: var(--accent-cyan);">0 B</strong></div>
        <div>Reclaimable Cluster Slack: <strong id="modal-slack-size" style="color: var(--accent-amber);">0 B</strong></div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-secondary" onclick="closePurgeModal()">Cancel</button>
        <button class="btn btn-danger" onclick="executeConfirmedPurge()">Yes, Purge Now</button>
      </div>
    </div>
  </div>

  <script>
    let activeCategory = '';
    let searchTimeout = null;
    let cachedJunkData = null;

    function formatBytes(bytes) {
      if (bytes === 0) return '0 B';
      if (bytes < 1024) return bytes + ' B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
    }

    function switchTab(tabId) {
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      ['overview', 'search', 'cleanup'].forEach(id => {
        const el = document.getElementById('tab-' + id);
        if (el) el.classList.add('hidden');
      });
      const activeBtn = Array.from(document.querySelectorAll('.nav-tab')).find(b => b.innerText.toLowerCase().includes(tabId));
      if (activeBtn) activeBtn.classList.add('active');
      const target = document.getElementById('tab-' + tabId);
      if (target) target.classList.remove('hidden');

      if (tabId === 'cleanup' && !cachedJunkData) {
        loadJunkPreview();
      }
    }

    async function loadStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        document.getElementById('drive-root-display').innerText = data.root;
        document.getElementById('cluster-size-display').innerText = (data.cluster_size_bytes / 1024) + ' KB';
      } catch (err) {
        console.error('Failed to load status:', err);
      }
    }

    async function loadAudit() {
      try {
        const res = await fetch('/api/audit');
        const data = await res.json();
        const summary = data.summary || {};
        document.getElementById('metric-total-files').innerText = (summary.total_files || 0).toLocaleString();
        document.getElementById('metric-total-dirs').innerText = (summary.total_directories || 0).toLocaleString() + ' directories';
        document.getElementById('metric-nominal-bytes').innerText = formatBytes(summary.total_logical_bytes || 0);
        document.getElementById('metric-allocated-bytes').innerText = formatBytes(summary.total_allocated_bytes || 0);
        document.getElementById('metric-total-clusters').innerText = (summary.total_clusters || 0).toLocaleString() + ' clusters';
        document.getElementById('metric-slack-bytes').innerText = formatBytes(summary.total_slack_bytes || 0);
        document.getElementById('metric-slack-pct').innerText = (summary.total_slack_percentage || 0) + '% overhead';

        // Render Taxonomies
        const taxGrid = document.getElementById('taxonomy-grid');
        taxGrid.innerHTML = '';
        const taxonomies = data.taxonomies || {};
        for (const [name, stat] of Object.entries(taxonomies)) {
          const card = document.createElement('div');
          card.className = 'tax-card';
          const pct = stat.slack_percentage || 0;
          card.innerHTML = `
            <div class="tax-head">
              <span>${name}</span>
              <span style="color: var(--accent-amber);">${pct.toFixed(1)}% waste</span>
            </div>
            <div class="progress-bar-bg">
              <div class="progress-bar-fill progress-bar-slack" style="width: ${Math.min(100, Math.max(5, pct))}%;"></div>
            </div>
            <div class="tax-metrics">
              <span>${stat.file_count.toLocaleString()} files</span>
              <span>${formatBytes(stat.nominal_bytes)} (alloc ${formatBytes(stat.allocated_bytes)})</span>
            </div>
          `;
          taxGrid.appendChild(card);
        }

        // Render Hotspots
        const hotspotsTable = document.getElementById('slack-hotspots-table');
        hotspotsTable.innerHTML = '';
        const topSlack = data.top_slack_directories || [];
        for (const d of topSlack.slice(0, 10)) {
          const row = document.createElement('tr');
          row.innerHTML = `
            <td class="code-font">${d.rel_path}</td>
            <td>${(d.recursive_files || 0).toLocaleString()}</td>
            <td>${formatBytes(d.recursive_bytes || 0)}</td>
            <td>${formatBytes(d.recursive_allocated || 0)}</td>
            <td style="color: var(--accent-amber); font-weight: 600;">${formatBytes(d.recursive_slack || 0)}</td>
            <td>${(d.recursive_slack_percentage || 0).toFixed(1)}%</td>
          `;
          hotspotsTable.appendChild(row);
        }
      } catch (err) {
        console.error('Failed to load audit:', err);
      }
    }

    function onSearchInput() {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(executeSearch, 150);
    }

    function setCategoryFilter(cat) {
      activeCategory = cat;
      document.querySelectorAll('#category-filter-chips .filter-chip').forEach(b => {
        b.classList.toggle('active', (cat === '' && b.innerText.includes('All')) || (cat && b.innerText === cat));
      });
      executeSearch();
    }

    async function executeSearch() {
      const q = document.getElementById('search-input').value.trim();
      const badge = document.getElementById('search-latency-badge');
      badge.innerText = '⚡ Searching...';

      let url = `/api/search?limit=100`;
      if (q) url += `&q=${encodeURIComponent(q)}`;
      if (activeCategory) url += `&category=${encodeURIComponent(activeCategory)}`;

      try {
        const t0 = performance.now();
        const res = await fetch(url);
        const data = await res.json();
        const latency = data.latency_ms || Math.round(performance.now() - t0);
        badge.innerText = `⚡ ${latency}ms | ${data.total || 0} matches`;

        const tbody = document.getElementById('search-results-body');
        tbody.innerHTML = '';
        const results = data.results || [];
        if (results.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-faint);">No matching files found.</td></tr>';
          return;
        }

        for (const m of results) {
          const row = document.createElement('tr');
          row.innerHTML = `
            <td style="font-weight: 600; color: var(--accent-cyan);">${m.name}</td>
            <td class="code-font" style="color: var(--text-muted);">${m.path}</td>
            <td><span class="badge badge-dev">${m.category || 'Other'}</span></td>
            <td>${formatBytes(m.size)}</td>
            <td style="color: var(--accent-amber);">${formatBytes(m.slack_bytes || 0)}</td>
            <td style="color: var(--text-faint);">${m.rank || '0.0'}</td>
          `;
          tbody.appendChild(row);
        }
      } catch (err) {
        badge.innerText = 'Error';
        console.error('Search failed:', err);
      }
    }

    async function loadJunkPreview() {
      try {
        const res = await fetch('/api/junk');
        const data = await res.json();
        cachedJunkData = data;

        const tiers = data.tiers || {};
        document.getElementById('t1-count').innerText = (tiers['1']?.count || 0) + ' items';
        document.getElementById('t1-slack').innerText = formatBytes(tiers['1']?.slack_bytes || 0);

        document.getElementById('t2-count').innerText = (tiers['2']?.count || 0) + ' items';
        document.getElementById('t2-slack').innerText = formatBytes(tiers['2']?.slack_bytes || 0);

        document.getElementById('t3-count').innerText = (tiers['3']?.count || 0) + ' items';
        document.getElementById('t3-slack').innerText = formatBytes(tiers['3']?.slack_bytes || 0);

        updateTierSelection();
      } catch (err) {
        console.error('Failed to load junk:', err);
      }
    }

    function getSelectedTiers() {
      const selected = [];
      if (document.getElementById('chk-tier-1').checked) selected.push(1);
      if (document.getElementById('chk-tier-2').checked) selected.push(2);
      if (document.getElementById('chk-tier-3').checked) selected.push(3);
      return selected;
    }

    function updateTierSelection() {
      const selected = getSelectedTiers();
      [1, 2, 3].forEach(t => {
        const box = document.getElementById('tier-box-' + t);
        if (box) box.classList.toggle('selected', selected.includes(t));
      });

      if (!cachedJunkData) return;
      const tiers = cachedJunkData.tiers || {};
      let totalCount = 0;
      let totalNominal = 0;
      let totalSlack = 0;
      const previewItems = [];

      selected.forEach(t => {
        const stat = tiers[String(t)];
        if (stat) {
          totalCount += stat.count;
          totalNominal += stat.nominal_bytes;
          totalSlack += stat.slack_bytes;
          previewItems.push(...(stat.items || []));
        }
      });

      document.getElementById('selected-summary-count').innerText = totalCount.toLocaleString();
      document.getElementById('selected-summary-nominal').innerText = formatBytes(totalNominal);
      document.getElementById('selected-summary-slack').innerText = formatBytes(totalSlack);

      const tbody = document.getElementById('junk-preview-tbody');
      tbody.innerHTML = '';
      if (previewItems.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-faint);">No junk items in selected tiers.</td></tr>';
        return;
      }

      for (const item of previewItems.slice(0, 50)) {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td><span class="badge ${item.tier === 1 ? 'badge-safe' : item.tier === 2 ? 'badge-dev' : 'badge-optin'}">T${item.tier}</span></td>
          <td class="code-font">${item.rel_path}</td>
          <td>${item.description || item.rule || ''}</td>
          <td>${formatBytes(item.size)}</td>
          <td>${formatBytes(item.allocated_size)}</td>
          <td style="color: var(--accent-amber);">${formatBytes(item.slack_bytes || 0)}</td>
        `;
        tbody.appendChild(row);
      }
    }

    function openPurgeModal() {
      const selected = getSelectedTiers();
      if (selected.length === 0) {
        alert('Please select at least one tier to clean.');
        return;
      }
      document.getElementById('modal-item-count').innerText = document.getElementById('selected-summary-count').innerText;
      document.getElementById('modal-nominal-size').innerText = document.getElementById('selected-summary-nominal').innerText;
      document.getElementById('modal-slack-size').innerText = document.getElementById('selected-summary-slack').innerText;
      document.getElementById('purge-modal').classList.remove('hidden');
    }

    function closePurgeModal() {
      document.getElementById('purge-modal').classList.add('hidden');
    }

    async function simulateDryRun() {
      const selected = getSelectedTiers();
      if (selected.length === 0) {
        alert('Please select at least one tier for dry-run.');
        return;
      }
      try {
        const res = await fetch('/api/junk/clean', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tiers: selected, dry_run: true })
        });
        const result = await res.json();
        alert(`[Dry-Run Simulation Complete]\\nAttempted: ${result.total_attempted}\\nSucceeded: ${result.total_succeeded}\\nNominal Space: ${formatBytes(result.nominal_bytes_reclaimed)}\\nAllocated Space: ${formatBytes(result.allocated_bytes_reclaimed)}\\nFiles remain untouched on disk.`);
      } catch (err) {
        alert('Dry run failed: ' + err);
      }
    }

    async function executeConfirmedPurge() {
      closePurgeModal();
      const selected = getSelectedTiers();
      try {
        const res = await fetch('/api/junk/clean', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tiers: selected, dry_run: false })
        });
        const result = await res.json();
        alert(`✓ Purge Execution Complete!\\nReclaimed: ${formatBytes(result.nominal_bytes_reclaimed)} (${result.total_succeeded} files unlinked).\\nAllocated cluster slack freed: ${formatBytes(result.allocated_bytes_reclaimed)}`);
        refreshAll();
      } catch (err) {
        alert('Purge execution failed: ' + err);
      }
    }

    function refreshAll() {
      loadStatus();
      loadAudit();
      loadJunkPreview();
    }

    window.addEventListener('DOMContentLoaded', () => {
      refreshAll();
    });
  </script>
</body>
</html>
"""
```

### 5.4 `smart_drive/cli/cmd_ui.py`
```python
"""smart_drive.cli.cmd_ui - CLI Subcommand Handler for `smart-drive ui`."""

from __future__ import annotations

import argparse
import os
import sys

from smart_drive.core.sentinel import detect_drive_root, get_default_db_path
from smart_drive.ui.server import run_server


def cmd_ui(args: argparse.Namespace) -> int:
    """Handles the `smart-drive ui` command to launch the web dashboard."""
    root = os.path.abspath(getattr(args, "root", None) or detect_drive_root())
    port = int(getattr(args, "port", 8765) or 8765)
    open_browser = not getattr(args, "no_browser", False)
    db_path = getattr(args, "db", None) or get_default_db_path(root)

    try:
        run_server(root_path=root, port=port, open_browser=open_browser, db_path=db_path)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        sys.stderr.write(f"Error starting web dashboard: {exc}\n")
        return 1


__all__ = ["cmd_ui"]
```

### 5.5 Changes to `smart_drive/cli/main.py`
In `build_parser()`:
```python
    # 13. ui
    p_ui = subparsers.add_parser("ui", help="Launch zero-dependency web dashboard & visual UI")
    p_ui.add_argument("--root", help="Root directory of the SSD")
    p_ui.add_argument("--port", type=int, default=8765, help="HTTP port to listen on (default: 8765)")
    p_ui.add_argument("--no-browser", action="store_true", help="Do not open web browser automatically")
    p_ui.add_argument("--db", help="Path to SQLite search database file")
```
In `dispatch` mapping:
```python
    from smart_drive.cli.cmd_ui import cmd_ui
    ...
    dispatch = {
        ...
        "ui": cmd_ui,
    }
```

---

## 6. Comprehensive Unit Test Strategy (`tests/test_ui.py`)

The test suite must be strictly written in pure Python standard library `unittest` and `urllib.request`. To guarantee non-flaky test execution:
1. All server instances during testing will bind to port `0` (`create_server(root, port=0)`), allowing the operating system kernel to allocate an ephemeral free port.
2. The server will be started in a daemon thread, and cleanly stopped in `tearDown()` via `server.shutdown()`.
3. Requests will be executed via `urllib.request.urlopen()`.

### Test Cases Outline:
1. `TestWebServerLifecycle`:
   - `test_create_server_binds_free_port`: verifies ephemeral port binding and attribute initialization.
   - `test_server_thread_startup_and_shutdown`: starts thread, verifies HTTP 200 response on `/`, shuts down cleanly.
2. `TestRestEndpoints`:
   - `test_get_root_serves_spa`: verifies status 200, Content-Type `text/html; charset=utf-8`, verifies `<title>SmartDrive-OS Dashboard</title>`, embedded CSS, and JavaScript.
   - `test_get_api_status`: verifies status 200, Content-Type `application/json`, verifies `status == 'ready'`, `version == '1.1.0'`, `cluster_size_bytes == 524288`.
   - `test_get_api_audit`: verifies status 200, returns taxonomy breakdown for 6 taxonomies, categories, top slack directories, and valid cluster slack statistics.
   - `test_get_api_search_with_keywords_and_filters`: tests search queries (`q=llama`, `category=AI Models`, `ext=gguf`, `limit=5`), verifies sub-10ms response time and ranking.
   - `test_get_api_search_missing_index_graceful_fallback`: points server to empty database path, verifies 200 return with `index_exists: False` without raising SQLite errors.
   - `test_get_api_junk_preview`: verifies status 200, returns categorized Tiers 1, 2, 3 with nominal and cluster slack byte calculations.
   - `test_post_api_junk_clean_dry_run`: tests POST `/api/junk/clean` with `{"tiers": [1], "dry_run": true}`, verifies files remain on disk and `dry_run: True` in JSON response.
   - `test_post_api_junk_clean_apply`: tests POST `/api/junk/clean` with `{"tiers": [1], "dry_run": false}`, verifies safe junk items are unlinked, space reclaimed, and protected files (`GEMINI.md`, models) are preserved.
   - `test_error_handling_and_cors`:
     - Unknown route (`GET /api/unknown_route`) returns 404 JSON.
     - Method not allowed (`POST /api/status`) returns 405 JSON.
     - Malformed POST body returns 400 JSON.
     - CORS preflight `OPTIONS /api/search` returns 204 with `Access-Control-Allow-Origin: *`.
3. `TestCliAndHeadless`:
   - `test_cmd_ui_parser_flags`: verifies argparse configuration for `smart-drive ui --port 9000 --no-browser`.
   - `test_headless_browser_fallback`: mocks `webbrowser.open` to raise `webbrowser.Error`, verifies `cmd_ui` handles it gracefully without crashing.

---

## 7. Verification Method

Once Milestone 1 is implemented, the implementation will be verified by running:
```bash
python -m unittest tests/test_ui.py
python -m unittest discover tests
```
Both commands must finish with `OK` and 100% pass rate.
