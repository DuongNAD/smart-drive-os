"""tests/test_db_connections_closed.py - Whatever opens the search database must close it.

Regression: `search`, `index`, `update`, `organize --apply`, `init`, the MCP search tool and the
sentinel's database check left their SQLite connection to the garbage collector.
Python 3.13 reports that as `ResourceWarning: unclosed database` on stderr (it surfaced as a CI failure on
3.13 only, where a test expects a clean stderr). The check below looks at the connection objects
themselves, so it holds on every Python version.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Iterator, List
from unittest.mock import patch

from smart_drive.cli.main import main
from smart_drive.core.sentinel import check_search_database
from smart_drive.mcp.server import SmartDriveMCPServer


class _TrackedConnection(sqlite3.Connection):
    """Remembers whether close() was called; asking a connection afterwards would not work across threads."""

    explicitly_closed = False

    def close(self) -> None:
        self.explicitly_closed = True
        super().close()


@contextlib.contextmanager
def tracked_connections() -> Iterator[List[_TrackedConnection]]:
    opened: List[_TrackedConnection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("factory", _TrackedConnection)
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    with patch("sqlite3.connect", tracking_connect):
        yield opened


class _Drive(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_close_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "notes").mkdir()
        (self.root / "notes" / "a.txt").write_text("alpha", encoding="utf-8")
        (self.root / "notes" / "tool.py").write_text("print(1)", encoding="utf-8")

    def cli(self, *argv: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(argv))

    def assert_all_closed(self, opened: List[_TrackedConnection], expected_at_least: int = 1) -> None:
        self.assertGreaterEqual(len(opened), expected_at_least, "the command never opened the database")
        for connection in opened:
            self.assertTrue(connection.explicitly_closed, "a SQLite connection was left for the garbage collector")


class TestCommandsCloseTheirDatabase(_Drive):
    def run_tracked(self, *argv: str) -> List[_TrackedConnection]:
        with tracked_connections() as opened:
            self.assertEqual(self.cli(*argv), 0)
        return opened

    def test_index(self) -> None:
        self.assert_all_closed(self.run_tracked("index", "--root", str(self.root)))

    def test_update(self) -> None:
        self.cli("index", "--root", str(self.root))
        self.assert_all_closed(self.run_tracked("update", "--root", str(self.root)))
        self.assert_all_closed(self.run_tracked("update", "--root", str(self.root), "--json"))

    def test_search(self) -> None:
        self.cli("index", "--root", str(self.root))
        self.assert_all_closed(self.run_tracked("search", "--root", str(self.root), "--json", "ext:py"))

    def test_init(self) -> None:
        self.assert_all_closed(self.run_tracked("init", "--root", str(self.root)))

    def test_organize_apply_refreshes_the_index_and_closes_it(self) -> None:
        self.cli("index", "--root", str(self.root))
        self.assert_all_closed(self.run_tracked("organize", "--root", str(self.root), "--apply", "--json"))


class TestServersCloseTheirDatabase(_Drive):
    def setUp(self) -> None:
        super().setUp()
        self.cli("index", "--root", str(self.root))

    def test_mcp_search(self) -> None:
        server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)
        with tracked_connections() as opened:
            result = server.handle_ssd_search({"query": "ext:py"})
        self.assertEqual(result["total_count"], 1)
        self.assert_all_closed(opened)


class TestSentinelClosesEvenOnADamagedFile(_Drive):
    def test_healthy_database(self) -> None:
        self.cli("index", "--root", str(self.root))
        with tracked_connections() as opened:
            report = check_search_database(str(self.root))
        self.assertTrue(report["healthy"])
        self.assert_all_closed(opened)

    def test_damaged_database(self) -> None:
        state = self.root / ".smart_drive"
        state.mkdir()
        (state / "index.db").write_bytes(b"this is not an sqlite database" * 200)
        with tracked_connections() as opened:
            report = check_search_database(str(self.root))
        self.assertFalse(report["healthy"])
        self.assert_all_closed(opened)


if __name__ == "__main__":
    unittest.main()
