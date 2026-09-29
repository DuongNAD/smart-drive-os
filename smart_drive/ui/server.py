"""smart_drive.ui.server - Zero-Dependency Threading HTTP Server & REST API.

Provides pure Python standard library HTTP server and REST API endpoints:
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
        root_path: Union[str, Path],
        db_path: Optional[Union[str, Path]] = None,
        cluster_size: int = CLUSTER_SIZE_BYTES,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.root_path = os.path.realpath(os.path.abspath(str(root_path)))
        self.db_path = (
            os.path.realpath(os.path.abspath(str(db_path)))
            if db_path
            else os.path.join(self.root_path, ".smart_drive", "index.db")
        )
        self.cluster_size = cluster_size


class SmartDriveRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler dispatching REST API routes and serving the embedded SPA."""

    server: ThreadingHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        """Route request logs to Python logging to keep stdout clean."""
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _set_cors_headers(self) -> None:
        """Set standard CORS headers for local API consumption."""
        origin = self.headers.get("Origin", "") if hasattr(self, "headers") and self.headers else ""
        if origin and ("127.0.0.1" in origin or "localhost" in origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        elif origin:
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8765")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-MCP-Auth-Token")

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
            elif path in ("/api/status", "/api/audit", "/api/search", "/api/junk"):
                self.send_error_response(f"Method not allowed for {path}", status=405)
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
            if t in tier_groups:
                tier_groups[t].append(item_dict)

            if t in tier_stats:
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


ALLOWED_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def create_server(
    root_path: Union[str, Path],
    port: int = 8765,
    db_path: Optional[Union[str, Path]] = None,
    host: str = "127.0.0.1",
    cluster_size: int = CLUSTER_SIZE_BYTES,
) -> ThreadingHTTPServer:
    """Instantiates a configured ThreadingHTTPServer ready for serve_forever()."""
    if host not in ALLOWED_LOOPBACK_HOSTS:
        raise ValueError(
            "Security restriction: Network listening sockets must bind strictly to '127.0.0.1' loopback. Binding to external interfaces is prohibited."
        )
    server_address = (host, port)
    return ThreadingHTTPServer(
        server_address=server_address,
        RequestHandlerClass=SmartDriveRequestHandler,
        root_path=root_path,
        db_path=db_path,
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
    if host not in ALLOWED_LOOPBACK_HOSTS:
        raise ValueError(
            "Security restriction: Network listening sockets must bind strictly to '127.0.0.1' loopback. Binding to external interfaces is prohibited."
        )
    server = create_server(root_path=root_path, port=port, db_path=db_path, host=host)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}"

    print("=" * 70)
    print("  SmartDrive-OS Web Dashboard (v1.1.0)")
    print(f"  Root:    {server.root_path}")
    print(f"  URL:     {url}")
    print(f"  Cluster: {server.cluster_size // 1024} KB ({server.cluster_size:,} bytes)")
    print("  Zero External Dependencies | 100% Offline Single Page Application")
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


__all__ = [
    "ALLOWED_LOOPBACK_HOSTS",
    "ThreadingHTTPServer",
    "SmartDriveRequestHandler",
    "create_server",
    "run_server",
]
