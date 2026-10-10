"""tests/test_learning.py - Unit and Integration Tests for Learning-Aware Content Index.

Covers:
1. Chunkers for Aurora deck.json, course.json, Polaris facts, sources, roadmap, errorlog,
   outlines, research, and captures (page markers [p. N], time markers [mm:ss], and §k blocks).
2. Caps and error handling (malformed JSON, >8MB files, progress.jsonl exclusion, chunk/file limits).
3. Incremental index re-chunking and deletion cleanup.
4. Search engine with kind: filter, --content flag, locators, and Vietnamese diacritic-insensitive matching.
5. MCP ssd_search integration with kind and content parameters.
6. CLI update [path] --quiet and path containment rejection.
7. Database schema migration from v1 to v2 and backfill.

100% Python Standard Library. Zero external dependencies.
"""

from __future__ import annotations

import json
import os
import sqlite3
import unittest
from pathlib import Path

from smart_drive.cli.main import main
from smart_drive.indexer.db import DatabaseManager, SCHEMA_VERSION
from smart_drive.indexer.learning import (
    DocChunk,
    is_learning_file,
    parse_learning_file,
)
from smart_drive.indexer.manager import IndexManager
from smart_drive.mcp.server import SmartDriveMCPServer
from smart_drive.search.engine import SearchEngine
from smart_drive.search.formatter import format_table
from smart_drive.search.parser import parse_search_query
from tests.helpers import SmartDriveTestCase, run_smart_drive_cli


class TestLearningChunkers(SmartDriveTestCase):
    """Tests for individual file chunkers and recognizers."""

    def test_deck_json_chunker(self) -> None:
        deck_dir = self.test_dir / "02-python-lab"
        deck_dir.mkdir(parents=True, exist_ok=True)
        deck_file = deck_dir / "deck.json"

        deck_data = {
            "title": "Chuyên đề Python Cơ Bản",
            "slides": [
                {
                    "id": "intro-slide",
                    "title": "Giới thiệu cú pháp",
                    "subtitle": "Các khái niệm nền tảng",
                    "text": "Python là ngôn ngữ thông dịch mạnh mẽ.",
                    "notes": "Nhấn mạnh về thụt đầu dòng (indentation).",
                },
                {
                    "id": "regex-traps",
                    "title": "Bẫy Regex lười",
                    "quiz": {
                        "question": "Sự khác biệt giữa * và *? là gì?",
                        "options": ["Tham lam vs Không tham lam", "Khác kiểu dữ liệu"],
                        "explanation": "Toán tử *? là non-greedy (lười).",
                    },
                    "code": {
                        "file": "matcher.py",
                        "lines": "10-25",
                        "code": "re.findall(r'<.*?>', html)",
                    },
                },
                {
                    "title": "Cung điện trí nhớ",
                    "palace": {
                        "stations": [
                            {"name": "Cửa chính", "description": "Biến và kiểu dữ liệu"},
                            {"name": "Phòng khách", "description": "Vòng lặp và điều kiện"},
                        ]
                    },
                },
            ],
        }
        deck_file.write_text(json.dumps(deck_data, ensure_ascii=False), encoding="utf-8")

        rel_path = "02-python-lab/deck.json"
        self.assertTrue(is_learning_file(rel_path))

        chunks = parse_learning_file(str(deck_file), rel_path)
        self.assertEqual(len(chunks), 3)

        # Slide 1
        c1 = chunks[0]
        self.assertEqual(c1.kind, "slide")
        self.assertEqual(c1.unit_id, "Chuyên đề Python Cơ Bản")
        self.assertEqual(c1.locator, "slide 1 #intro-slide")
        self.assertIn("Giới thiệu cú pháp", c1.snippet)
        self.assertIn("thụt đầu dòng", c1.content)

        # Slide 2
        c2 = chunks[1]
        self.assertEqual(c2.kind, "slide")
        self.assertEqual(c2.locator, "slide 2 #regex-traps")
        self.assertIn("Bẫy Regex lười", c2.snippet)
        self.assertIn("Tham lam vs Không tham lam", c2.content)
        self.assertIn("file: matcher.py lines: 10-25", c2.content)

        # Slide 3 (no id)
        c3 = chunks[2]
        self.assertEqual(c3.locator, "slide 3")
        self.assertIn("Cửa chính", c3.content)
        self.assertIn("Phòng khách", c3.content)

    def test_course_json_chunker(self) -> None:
        course_file = self.test_dir / "course.json"
        course_data = {
            "title": "Mastering LLM Engineering",
            "lessons": [
                {
                    "title": "01. Kiến trúc Transformer",
                    "summary": "Mổ xẻ Multi-Head Attention và Feed Forward layers.",
                    "dir": "01-transformer-arch",
                    "when": "Tuần 1",
                },
                {
                    "title": "02. Fine-tuning với LoRA",
                    "summary": "Tối ưu hóa tham số thấp (Low-Rank Adaptation).",
                    "dir": "02-lora-tuning",
                    "when": "Tuần 2",
                },
            ],
        }
        course_file.write_text(json.dumps(course_data, ensure_ascii=False), encoding="utf-8")

        rel_path = "course.json"
        self.assertTrue(is_learning_file(rel_path))

        chunks = parse_learning_file(str(course_file), rel_path)
        self.assertEqual(len(chunks), 2)

        c1 = chunks[0]
        self.assertEqual(c1.kind, "lesson")
        self.assertEqual(c1.unit_id, "Mastering LLM Engineering")
        self.assertEqual(c1.locator, "lesson 1 (01-transformer-arch)")
        self.assertIn("01. Kiến trúc Transformer", c1.snippet)
        self.assertIn("Multi-Head Attention", c1.content)
        self.assertIn("Tuần 1", c1.content)

    def test_polaris_facts_sources_roadmap(self) -> None:
        polaris_dir = self.test_dir / "polaris"
        polaris_dir.mkdir(parents=True, exist_ok=True)

        # facts.json
        facts_file = polaris_dir / "facts.json"
        facts_data = [
            {
                "id": "01",
                "claim": "Cơ chế Self-Attention tính toán ma trận tương quan giữa tất cả các token.",
                "locator": "p. 42",
                "source": "S-01",
                "label": "core",
                "modules": ["M-01"],
            },
            {
                "id": "F-02",
                "claim": "Residual connections giúp giải quyết vấn đề triệt tiêu đạo hàm.",
                "locator": "p. 45",
                "source": "S-01",
                "label": "stability",
                "modules": ["M-01"],
            },
        ]
        facts_file.write_text(json.dumps(facts_data, ensure_ascii=False), encoding="utf-8")

        # sources.json
        sources_file = polaris_dir / "sources.json"
        sources_data = [
            {
                "id": "01",
                "kind": "paper",
                "title": "Attention Is All You Need",
                "author": "Vaswani et al.",
                "url": "https://arxiv.org/abs/1706.03762",
                "label": "foundational",
                "quality": {"score": 9.8, "why": "Seminal transformer paper"},
            }
        ]
        sources_file.write_text(json.dumps(sources_data, ensure_ascii=False), encoding="utf-8")

        # roadmap.json
        roadmap_file = polaris_dir / "roadmap.json"
        roadmap_data = {
            "modules": [
                {
                    "id": "01",
                    "title": "Transformer Nền Tảng",
                    "why": "Hiểu kiến trúc cốt lõi của GenAI",
                    "outputs": ["attention_numpy.py"],
                    "core": [{"text": "Q, K, V matrices and Scaled Dot-Product Attention."}],
                    "misconceptions": [
                        {
                            "wrong": "Self-attention có độ phức tạp tuyến tính.",
                            "why": "Ma trận N x N yêu cầu O(N^2) bộ nhớ.",
                            "fix": "Sử dụng FlashAttention hoặc sliding window.",
                        }
                    ],
                }
            ]
        }
        roadmap_file.write_text(json.dumps(roadmap_data, ensure_ascii=False), encoding="utf-8")

        # Test facts
        fact_chunks = parse_learning_file(str(facts_file), "polaris/facts.json")
        self.assertEqual(len(fact_chunks), 2)
        self.assertEqual(fact_chunks[0].kind, "fact")
        self.assertEqual(fact_chunks[0].unit_id, "F-01")
        self.assertEqual(fact_chunks[0].locator, "p. 42 · S-01")
        self.assertIn("Self-Attention", fact_chunks[0].content)

        # Test sources
        src_chunks = parse_learning_file(str(sources_file), "polaris/sources.json")
        self.assertEqual(len(src_chunks), 1)
        self.assertEqual(src_chunks[0].kind, "source")
        self.assertEqual(src_chunks[0].unit_id, "S-01")
        self.assertEqual(src_chunks[0].locator, "https://arxiv.org/abs/1706.03762")
        self.assertIn("Vaswani et al.", src_chunks[0].content)

        # Test roadmap
        mod_chunks = parse_learning_file(str(roadmap_file), "polaris/roadmap.json")
        self.assertEqual(len(mod_chunks), 1)
        self.assertEqual(mod_chunks[0].kind, "module")
        self.assertEqual(mod_chunks[0].unit_id, "M-01")
        self.assertEqual(mod_chunks[0].locator, "Transformer Nền Tảng")
        self.assertIn("FlashAttention", mod_chunks[0].content)

    def test_polaris_markdown_files(self) -> None:
        polaris_dir = self.test_dir / "polaris"
        captures_dir = polaris_dir / "captures"
        lessons_dir = polaris_dir / "lessons"
        research_dir = polaris_dir / "research"
        for d in [captures_dir, lessons_dir, research_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 1. Errorlog
        errorlog_file = polaris_dir / "errorlog.md"
        errorlog_file.write_text(
            "# Nhật ký lỗi\n\n"
            "### Lỗi tràn bộ nhớ CUDA (OOM)\n"
            "Khi chạy batch size 32 trên GPU 16GB, xảy ra CUDA out of memory.\n"
            "Khắc phục: Giảm batch size xuống 8 và dùng gradient accumulation.\n\n"
            "### Lỗi Nan Loss khi huấn luyện FP16\n"
            "Gradient bị tràn số dưới định dạng float16.\n"
            "Khắc phục: Chuyển sang BF16 hoặc dùng loss scaling.\n",
            encoding="utf-8",
        )
        err_chunks = parse_learning_file(str(errorlog_file), "polaris/errorlog.md")
        self.assertEqual(len(err_chunks), 2)
        self.assertEqual(err_chunks[0].kind, "error")
        self.assertEqual(err_chunks[0].unit_id, "Lỗi tràn bộ nhớ CUDA (OOM)")
        self.assertIn("gradient accumulation", err_chunks[0].content)

        # 2. Captures with [p. N] markers
        p_cap_file = captures_dir / "deep_learning_paper.md"
        p_cap_file.write_text(
            "[p. 1]\nKhái quát về mạng nơ-ron sâu và các thuật toán tối ưu.\n\n"
            "[p. 2]\nĐạo hàm riêng và giải thuật lan truyền ngược (Backpropagation).\n",
            encoding="utf-8",
        )
        p_chunks = parse_learning_file(str(p_cap_file), "polaris/captures/deep_learning_paper.md")
        self.assertEqual(len(p_chunks), 2)
        self.assertEqual(p_chunks[0].kind, "capture")
        self.assertEqual(p_chunks[0].locator, "p. 1")
        self.assertEqual(p_chunks[1].locator, "p. 2")

        # 3. Captures with [mm:ss] markers
        t_cap_file = captures_dir / "lecture_recording.md"
        t_cap_file.write_text(
            "[00:00]\nChào mừng các bạn đến với buổi học về PyTorch.\n\n"
            "[01:05]\nKhởi tạo tensor và các phép toán cơ bản trên GPU.\n",
            encoding="utf-8",
        )
        t_chunks = parse_learning_file(str(t_cap_file), "polaris/captures/lecture_recording.md")
        self.assertEqual(len(t_chunks), 2)
        self.assertEqual(t_chunks[0].kind, "capture")
        self.assertEqual(t_chunks[0].locator, "00:00")
        self.assertEqual(t_chunks[1].locator, "01:05")

        # 4. Captures fallback block chunks
        raw_cap_file = captures_dir / "raw_text.md"
        raw_cap_file.write_text("Dữ liệu ghi chú tự do không có dấu trang hoặc mốc thời gian. " * 50, encoding="utf-8")
        raw_chunks = parse_learning_file(str(raw_cap_file), "polaris/captures/raw_text.md")
        self.assertTrue(len(raw_chunks) >= 1)
        self.assertEqual(raw_chunks[0].locator, "§1")

        # 5. Outlines and Research split by headings
        outline_file = lessons_dir / "01.outline.md"
        outline_file.write_text(
            "# Bài 1: Mở đầu\nNội dung giới thiệu tổng quan.\n\n"
            "## Phần 1: Cài đặt môi trường\nHướng dẫn uv và conda.\n",
            encoding="utf-8",
        )
        out_chunks = parse_learning_file(str(outline_file), "polaris/lessons/01.outline.md")
        self.assertEqual(len(out_chunks), 2)
        self.assertEqual(out_chunks[0].kind, "outline")

        research_file = research_dir / "notes.md"
        research_file.write_text(
            "# Khảo sát mô hình\nSo sánh Llama 3 và Qwen 2.5.\n",
            encoding="utf-8",
        )
        res_chunks = parse_learning_file(str(research_file), "polaris/research/notes.md")
        self.assertEqual(len(res_chunks), 1)
        self.assertEqual(res_chunks[0].kind, "research")

    def test_progress_jsonl_and_non_learning_excluded(self) -> None:
        polaris_dir = self.test_dir / "polaris"
        polaris_dir.mkdir(parents=True, exist_ok=True)
        prog_file = polaris_dir / "progress.jsonl"
        prog_file.write_text('{"event": "completed", "score": 100}\n', encoding="utf-8")

        self.assertFalse(is_learning_file("polaris/progress.jsonl"))
        chunks = parse_learning_file(str(prog_file), "polaris/progress.jsonl")
        self.assertEqual(len(chunks), 0)

        # File outside polaris or not deck/course
        random_file = self.test_dir / "random.md"
        random_file.write_text("# Hello World", encoding="utf-8")
        self.assertFalse(is_learning_file("random.md"))
        self.assertEqual(len(parse_learning_file(str(random_file), "random.md")), 0)

    def test_malformed_json_handled_gracefully(self) -> None:
        bad_json = self.test_dir / "deck.json"
        bad_json.write_text("{ unclosed json", encoding="utf-8")
        chunks = parse_learning_file(str(bad_json), "deck.json")
        self.assertEqual(chunks, [])


class TestLearningIndexManager(SmartDriveTestCase):
    """Tests for indexing, incremental re-chunking, deletion and backfill."""

    def test_incremental_rechunk_and_deletion(self) -> None:
        mock_root = self.test_dir / "mock_drive"
        mock_root.mkdir(parents=True, exist_ok=True)
        db_path = mock_root / ".smart_drive" / "index.db"

        deck_dir = mock_root / "02_Learning_Knowledge" / "deck_intro"
        deck_dir.mkdir(parents=True, exist_ok=True)
        deck_path = deck_dir / "deck.json"

        deck_path.write_text(
            json.dumps({
                "title": "Thuật toán cơ bản",
                "slides": [{"id": "s1", "title": "Vòng lặp For", "text": "Cú pháp for i in range(10): pass"}]
            }, ensure_ascii=False),
            encoding="utf-8"
        )

        db = DatabaseManager(str(db_path))
        db.initialize_schema()
        mgr = IndexManager(db, str(mock_root))

        # Initial full index
        stats = mgr.full_index()
        self.assertTrue(stats.indexed_files >= 1)

        con = db.get_connection()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM doc_chunks WHERE kind='slide';")
        self.assertEqual(cur.fetchone()[0], 1)

        # Search check
        engine = SearchEngine(db)
        res = engine.search(parse_search_query("kind:slide 'Vòng lặp'"))
        self.assertEqual(res.total_count, 1)
        self.assertEqual(res.matches[0].locator, "slide 1 #s1")

        # Modify deck.json
        import time
        time.sleep(0.05)
        deck_path.write_text(
            json.dumps({
                "title": "Thuật toán cơ bản v2",
                "slides": [
                    {"id": "s1", "title": "Vòng lặp While", "text": "Cú pháp while True: break"},
                    {"id": "s2", "title": "Hàm đệ quy", "text": "def recurse(n): return recurse(n-1)"}
                ]
            }, ensure_ascii=False),
            encoding="utf-8"
        )

        inc = mgr.incremental_update()
        self.assertEqual(inc.modified, 1)

        cur.execute("SELECT COUNT(*) FROM doc_chunks WHERE kind='slide';")
        self.assertEqual(cur.fetchone()[0], 2)

        res_new = engine.search(parse_search_query("kind:slide 'đệ quy'"))
        self.assertEqual(res_new.total_count, 1)
        self.assertEqual(res_new.matches[0].locator, "slide 2 #s2")

        # Delete deck.json
        deck_path.unlink()
        inc_del = mgr.incremental_update()
        self.assertEqual(inc_del.deleted, 1)

        cur.execute("SELECT COUNT(*) FROM doc_chunks;")
        self.assertEqual(cur.fetchone()[0], 0)
        db.close()

    def test_backfill_on_upgrade(self) -> None:
        mock_root = self.test_dir / "mock_drive"
        mock_root.mkdir(parents=True, exist_ok=True)
        db_path = mock_root / ".smart_drive" / "index.db"

        polaris_dir = mock_root / "polaris"
        polaris_dir.mkdir(parents=True, exist_ok=True)
        facts_file = polaris_dir / "facts.json"
        facts_file.write_text(
            json.dumps([{"id": "01", "claim": "Backfill test fact claim.", "locator": "p. 1"}], ensure_ascii=False),
            encoding="utf-8"
        )

        db = DatabaseManager(str(db_path))
        con = db.get_connection()
        # Simulate an old v1 database by populating files table without doc_chunks
        con.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                extension TEXT,
                size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                category TEXT NOT NULL,
                indexed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS index_meta (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO files (path, filename, extension, size, mtime, category, indexed_at)
            VALUES ('polaris/facts.json', 'facts.json', '.json', 100, 1000.0, 'Code', 1000.0);
        """)
        con.commit()

        # Run initialize_schema (migration to v2)
        db.initialize_schema()

        cur = con.cursor()
        cur.execute("SELECT value FROM index_meta WHERE key='schema_version';")
        self.assertEqual(cur.fetchone()[0], SCHEMA_VERSION)
        cur.execute("SELECT COUNT(*) FROM doc_chunks;")
        self.assertEqual(cur.fetchone()[0], 0)

        # Run incremental_update which must backfill chunks for existing files
        mgr = IndexManager(db, str(mock_root))
        mgr.incremental_update()

        cur.execute("SELECT COUNT(*) FROM doc_chunks WHERE kind='fact';")
        self.assertEqual(cur.fetchone()[0], 1)
        db.close()


class TestLearningSearchEngine(SmartDriveTestCase):
    """Tests for Vietnamese diacritic-insensitive matching, kind filters, and locators."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.test_dir / "mock_drive"
        self.mock_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.mock_root / ".smart_drive" / "index.db"

        # Create deck.json
        deck_dir = self.mock_root / "02_Learning_Knowledge" / "02-mo-xe-lab-1"
        deck_dir.mkdir(parents=True, exist_ok=True)
        (deck_dir / "deck.json").write_text(
            json.dumps({
                "title": "Mổ Xẻ Regular Expression",
                "slides": [
                    {"id": "traps", "title": "Bẫy Regex lười", "text": "Toán tử lười non-greedy matching trong Python."},
                    {"id": "loops", "title": "Cấu trúc Vòng lặp", "text": "Vòng lặp for và while trong lập trình."},
                ]
            }, ensure_ascii=False),
            encoding="utf-8"
        )

        db = DatabaseManager(str(self.db_path))
        db.initialize_schema()
        mgr = IndexManager(db, str(self.mock_root))
        mgr.full_index()
        db.close()

    def test_vietnamese_diacritic_insensitive_search(self) -> None:
        db = DatabaseManager(str(self.db_path))
        engine = SearchEngine(db)

        # Query without diacritics: "vong lap" -> matches "Vòng lặp"
        p1 = parse_search_query("kind:slide vong lap")
        r1 = engine.search(p1)
        self.assertEqual(r1.total_count, 1)
        self.assertEqual(r1.matches[0].kind, "slide")
        self.assertIn("02-mo-xe-lab-1/deck.json", r1.matches[0].path)
        self.assertEqual(r1.matches[0].locator, "slide 2 #loops")

        # Query without diacritics: "bay regex" -> matches "Bẫy Regex lười"
        p2 = parse_search_query("kind:slide bay regex")
        r2 = engine.search(p2)
        self.assertEqual(r2.total_count, 1)
        self.assertEqual(r2.matches[0].locator, "slide 1 #traps")
        self.assertIn("Bẫy Regex lười", r2.matches[0].snippet)

        # Formatter check
        formatted = format_table(r2)
        self.assertIn("02-mo-xe-lab-1/deck.json", formatted)
        self.assertIn("slide 1 #traps", formatted)
        self.assertIn("Bẫy Regex lười", formatted)

        # Plain search backwards compatibility (without kind/content)
        p_plain = parse_search_query("deck")
        r_plain = engine.search(p_plain)
        self.assertTrue(r_plain.total_count >= 1)
        self.assertIsNone(r_plain.matches[0].kind)
        self.assertNotIn("kind", r_plain.matches[0].to_dict())

        db.close()


class TestMCPAndCLILearning(SmartDriveTestCase):
    """Tests for MCP server ssd_search tool and CLI update commands."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.test_dir / "mock_drive"
        self.mock_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.mock_root / ".smart_drive" / "index.db"

        deck_dir = self.mock_root / "deck_folder"
        deck_dir.mkdir(parents=True, exist_ok=True)
        (deck_dir / "deck.json").write_text(
            json.dumps({
                "title": "Khóa Học Lập Trình",
                "slides": [{"id": "start", "title": "Khởi Động", "text": "Học cú pháp căn bản."}]
            }, ensure_ascii=False),
            encoding="utf-8"
        )

        db = DatabaseManager(str(self.db_path))
        db.initialize_schema()
        mgr = IndexManager(db, str(self.mock_root))
        mgr.full_index()
        db.close()

    def test_mcp_ssd_search_kind_and_content(self) -> None:
        server = SmartDriveMCPServer(root=str(self.mock_root))

        # 1. Compact mode search
        res_compact = server.handle_ssd_search({
            "query": "can ban",
            "kind": "slide",
            "compact": True,
        })
        self.assertEqual(res_compact.get("total_count"), 1)
        matches = res_compact.get("matches", [])
        self.assertEqual(len(matches), 1)
        m0 = matches[0]
        self.assertEqual(m0.get("kind"), "slide")
        self.assertEqual(m0.get("loc"), "slide 1 #start")
        self.assertIn("Khởi Động", m0.get("snip", ""))

        # 2. Full mode search
        res_full = server.handle_ssd_search({
            "query": "can ban",
            "kind": "slide",
            "compact": False,
        })
        self.assertEqual(res_full.get("total_count"), 1)
        m_full = res_full.get("matches", [])[0]
        self.assertEqual(m_full.get("unit_id"), "Khóa Học Lập Trình")
        self.assertEqual(m_full.get("locator"), "slide 1 #start")

    def test_caps_and_limits(self) -> None:
        polaris_dir = self.test_dir / "polaris"
        polaris_dir.mkdir(parents=True, exist_ok=True)

        # 1. Test >4000 chars per chunk gets capped
        long_fact = polaris_dir / "facts.json"
        huge_claim = "A" * 6000
        long_fact.write_text(
            json.dumps([{"id": "01", "claim": huge_claim}], ensure_ascii=False),
            encoding="utf-8"
        )
        chunks = parse_learning_file(str(long_fact), "polaris/facts.json")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0].content), 4000)

        # 2. Test >400 chunks per file gets capped at 400
        many_facts = [{"id": str(i), "claim": f"Fact number {i}"} for i in range(500)]
        long_fact.write_text(json.dumps(many_facts, ensure_ascii=False), encoding="utf-8")
        chunks_many = parse_learning_file(str(long_fact), "polaris/facts.json")
        self.assertEqual(len(chunks_many), 400)

        # 3. Test >8MB file skipped
        # Create a file > 8MB by writing sparse or large bytes
        big_deck = self.test_dir / "deck.json"
        with open(big_deck, "wb") as f:
            f.seek(8 * 1024 * 1024 + 1024)
            f.write(b"0")
        big_chunks = parse_learning_file(str(big_deck), "deck.json")
        self.assertEqual(len(big_chunks), 0)

    def test_kind_learning_and_content_search(self) -> None:
        db = DatabaseManager(str(self.db_path))
        engine = SearchEngine(db)

        # Search with kind:learning
        p_learn = parse_search_query("kind:learning 'khởi động'")
        r_learn = engine.search(p_learn)
        self.assertEqual(r_learn.total_count, 1)
        self.assertEqual(r_learn.matches[0].kind, "slide")

        # Search with content=True
        p_content = parse_search_query("cú pháp")
        p_content.content = True
        r_content = engine.search(p_content)
        self.assertEqual(r_content.total_count, 1)

        # CSV export test
        from smart_drive.search.formatter import export_csv, export_json
        csv_out = export_csv(r_learn)
        self.assertIn("kind,unit_id,locator,snippet", csv_out)
        self.assertIn("slide 1 #start", csv_out)

        json_out = export_json(r_learn)
        self.assertIn("Khóa Học Lập Trình", json_out)
        db.close()

    def test_cli_search_content_and_kind(self) -> None:
        proc_kind = run_smart_drive_cli(
            ["search", "cú pháp", "--kind", "slide", "--root", str(self.mock_root)],
            cwd=self.mock_root,
        )
        self.assertEqual(proc_kind.returncode, 0)
        self.assertIn("deck.json", proc_kind.stdout)
        self.assertIn("slide 1 #start", proc_kind.stdout)

        proc_content = run_smart_drive_cli(
            ["search", "cú pháp", "--content", "--root", str(self.mock_root)],
            cwd=self.mock_root,
        )
        self.assertEqual(proc_content.returncode, 0)
    def test_cli_update_quiet_and_containment(self) -> None:
        # 1. Successful quiet update inside root
        deck_dir = self.mock_root / "deck_folder"
        proc_quiet = run_smart_drive_cli(
            ["update", str(deck_dir), "--quiet", "--root", str(self.mock_root)],
            cwd=self.mock_root,
        )
        self.assertEqual(proc_quiet.returncode, 0)
        self.assertEqual(proc_quiet.stdout.strip(), "")

        # 2. Path escaping root rejection
        outside_dir = self.test_dir / "outside_dir"
        outside_dir.mkdir(parents=True, exist_ok=True)
        proc_escape = run_smart_drive_cli(
            ["update", str(outside_dir), "--root", str(self.mock_root)],
            cwd=self.mock_root,
        )
        self.assertNotEqual(proc_escape.returncode, 0)
        self.assertIn("escapes drive root", proc_escape.stderr)


if __name__ == "__main__":
    unittest.main()
