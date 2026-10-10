"""smart_drive.indexer.learning - Learning-Aware Content Chunker & Recognizer.

Recognizes and chunks structured educational resources from:
1. Aurora Slides: deck.json (slides), course.json (lessons).
2. Polaris: facts.json, sources.json, roadmap.json, errorlog.md,
   lessons/*.outline.md, research/*.md, captures/*.md.

100% Python Standard Library. Zero external dependencies. Python 3.9+ compatible.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("smart_drive.indexer.learning")

MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 MB
MAX_CHUNK_CHARS = 4000
MAX_CHUNKS_PER_FILE = 400
MAX_SNIPPET_CHARS = 160

RECOGNIZED_KINDS = {
    "slide",
    "lesson",
    "fact",
    "source",
    "module",
    "capture",
    "outline",
    "research",
    "error",
}


@dataclass
class DocChunk:
    """Individual chunk extracted from a learning document."""
    kind: str
    unit_id: Optional[str]
    locator: Optional[str]
    snippet: str
    content: str


def is_learning_file(rel_path: str) -> bool:
    """Checks whether rel_path matches a recognized learning file pattern."""
    if not rel_path:
        return False
    norm = rel_path.replace("\\", "/").strip("/")
    parts = norm.split("/")
    filename = parts[-1]
    fn_lower = filename.lower()

    if fn_lower == "progress.jsonl":
        return False

    if fn_lower == "deck.json":
        return True

    if fn_lower == "course.json":
        return True

    parts_lower = [p.lower() for p in parts[:-1]]
    if "polaris" in parts_lower:
        if fn_lower in {"facts.json", "sources.json", "roadmap.json", "errorlog.md"}:
            return True
        if fn_lower.endswith(".outline.md"):
            return True
        if "research" in parts_lower and fn_lower.endswith(".md"):
            return True
        if "captures" in parts_lower and fn_lower.endswith(".md"):
            return True
        if "lessons" in parts_lower and fn_lower.endswith(".md"):
            return True

    return False


def _make_snippet(primary: Optional[str], fallback: str, max_len: int = MAX_SNIPPET_CHARS) -> str:
    """Produces a clean snippet <= max_len characters."""
    candidate = ""
    if primary and primary.strip():
        candidate = primary.strip()
    else:
        for line in fallback.split("\n"):
            line = line.strip()
            if line:
                candidate = line
                break
    clean = re.sub(r"\s+", " ", candidate).strip()
    if len(clean) > max_len:
        clean = clean[:max_len].strip()
    return clean


def _cap_chunks(chunks: List[DocChunk]) -> List[DocChunk]:
    """Enforces per-chunk character caps and per-file chunk limit."""
    capped = []
    for c in chunks[:MAX_CHUNKS_PER_FILE]:
        content = c.content[:MAX_CHUNK_CHARS].strip()
        snippet = c.snippet[:MAX_SNIPPET_CHARS].strip()
        if content:
            capped.append(
                DocChunk(
                    kind=c.kind,
                    unit_id=c.unit_id[:255] if c.unit_id else None,
                    locator=c.locator[:255] if c.locator else None,
                    snippet=snippet or content[:MAX_SNIPPET_CHARS],
                    content=content,
                )
            )
    return capped


def _extract_slide_text(slide: Dict[str, Any]) -> str:
    """Collects all learner-visible text from an Aurora slide dictionary."""
    lines: List[str] = []

    def add_val(val: Any) -> None:
        if val is None:
            return
        if isinstance(val, str):
            s = val.strip()
            if s:
                lines.append(s)
        elif isinstance(val, (int, float, bool)):
            lines.append(str(val))
        elif isinstance(val, list):
            for item in val:
                add_val(item)
        elif isinstance(val, dict):
            for k, v in val.items():
                add_val(v)

    if "title" in slide:
        add_val(slide["title"])
    if "subtitle" in slide:
        add_val(slide["subtitle"])
    if "text" in slide:
        add_val(slide["text"])
    if "items" in slide:
        add_val(slide["items"])
    if "notes" in slide:
        add_val(slide["notes"])
    if "quiz" in slide:
        q = slide["quiz"]
        if isinstance(q, dict):
            add_val(q.get("question"))
            add_val(q.get("options"))
            add_val(q.get("explanation") or q.get("explain"))
        else:
            add_val(q)
    if "notebook" in slide:
        add_val(slide["notebook"])
    if "predict" in slide:
        p = slide["predict"]
        if isinstance(p, dict):
            add_val(p.get("question"))
            add_val(p.get("explain") or p.get("explanation"))
            add_val(p.get("output") or p.get("expected"))
        else:
            add_val(p)
    if "palace" in slide:
        pal = slide["palace"]
        if isinstance(pal, dict):
            add_val(pal.get("stations") or pal.get("rooms"))
        else:
            add_val(pal)
    if "flashcards" in slide or "cards" in slide:
        add_val(slide.get("flashcards") or slide.get("cards"))
    if "code" in slide:
        c = slide["code"]
        if isinstance(c, dict):
            f_ref = c.get("file")
            l_ref = c.get("lines")
            if f_ref or l_ref:
                parts = []
                if f_ref:
                    parts.append(f"file: {f_ref}")
                if l_ref:
                    parts.append(f"lines: {l_ref}")
                lines.append(" ".join(parts))
            add_val(c.get("code") or c.get("content"))
        else:
            add_val(c)
    if "source" in slide:
        add_val(slide["source"])

    handled_keys = {
        "id", "type", "layout", "title", "subtitle", "text", "items", "notes",
        "quiz", "notebook", "predict", "palace", "flashcards", "cards", "code", "source"
    }
    for k, v in slide.items():
        if k not in handled_keys:
            add_val(v)

    seen = set()
    unique_lines = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique_lines.append(line)

    return "\n".join(unique_lines)


def _chunk_deck_json(data: Any) -> List[DocChunk]:
    if not isinstance(data, dict):
        return []
    slides = data.get("slides")
    if not isinstance(slides, list):
        return []

    deck_title = str(data.get("title") or "").strip()
    chunks: List[DocChunk] = []

    for idx, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            continue
        slide_id = str(slide.get("id") or "").strip()
        locator = f"slide {idx}"
        if slide_id:
            locator += f" #{slide_id}"

        content = _extract_slide_text(slide)
        if not content:
            continue

        slide_title = str(slide.get("title") or "").strip()
        snippet = _make_snippet(slide_title, content)

        chunks.append(
            DocChunk(
                kind="slide",
                unit_id=deck_title or None,
                locator=locator,
                snippet=snippet,
                content=content,
            )
        )

    return chunks


def _chunk_course_json(data: Any) -> List[DocChunk]:
    if not isinstance(data, dict):
        return []
    lessons = data.get("lessons")
    if not isinstance(lessons, list):
        return []

    course_title = str(data.get("title") or "").strip()
    chunks: List[DocChunk] = []

    for idx, lesson in enumerate(lessons, start=1):
        if not isinstance(lesson, dict):
            continue
        l_title = str(lesson.get("title") or "").strip()
        l_summary = str(lesson.get("summary") or "").strip()
        l_dir = str(lesson.get("dir") or "").strip()
        l_when = str(lesson.get("when") or "").strip()

        locator = f"lesson {idx}"
        if l_dir:
            locator += f" ({l_dir})"

        content_parts = [p for p in [l_title, l_summary, l_when] if p]
        content = "\n".join(content_parts)
        if not content:
            continue

        snippet = _make_snippet(l_title, content)
        chunks.append(
            DocChunk(
                kind="lesson",
                unit_id=course_title or None,
                locator=locator,
                snippet=snippet,
                content=content,
            )
        )

    return chunks


def _chunk_facts_json(data: Any) -> List[DocChunk]:
    if not isinstance(data, list):
        return []
    chunks: List[DocChunk] = []

    for fact in data:
        if not isinstance(fact, dict):
            continue
        fact_id = str(fact.get("id") or "").strip()
        unit_id = fact_id if fact_id.startswith("F-") else f"F-{fact_id}"
        fact_loc = str(fact.get("locator") or "").strip()
        source_id = str(fact.get("source") or "").strip()

        if fact_loc and source_id:
            locator = f"{fact_loc} · {source_id}"
        else:
            locator = fact_loc or source_id or None

        claim = str(fact.get("claim") or "").strip()
        label = str(fact.get("label") or "").strip()
        modules = fact.get("modules")

        content_parts = [claim]
        if label:
            content_parts.append(f"Label: {label}")
        if isinstance(modules, list) and modules:
            content_parts.append(f"Modules: {', '.join(str(m) for m in modules)}")
        elif isinstance(modules, str) and modules.strip():
            content_parts.append(f"Modules: {modules.strip()}")

        content = "\n".join(p for p in content_parts if p)
        if not content:
            continue

        snippet = _make_snippet(claim, content)
        chunks.append(
            DocChunk(
                kind="fact",
                unit_id=unit_id,
                locator=locator,
                snippet=snippet,
                content=content,
            )
        )

    return chunks


def _chunk_sources_json(data: Any) -> List[DocChunk]:
    if not isinstance(data, list):
        return []
    chunks: List[DocChunk] = []

    for src in data:
        if not isinstance(src, dict):
            continue
        src_id = str(src.get("id") or "").strip()
        unit_id = src_id if src_id.startswith("S-") else f"S-{src_id}"
        locator = str(src.get("url") or src.get("path") or "").strip() or None

        title = str(src.get("title") or "").strip()
        author = str(src.get("author") or "").strip()
        label = str(src.get("label") or "").strip()
        src_kind = str(src.get("kind") or "").strip()

        content_parts = [title]
        if author:
            content_parts.append(f"Author: {author}")
        if src_kind:
            content_parts.append(f"Kind: {src_kind}")
        if label:
            content_parts.append(f"Label: {label}")
        if locator:
            content_parts.append(f"Location: {locator}")

        quality = src.get("quality")
        if isinstance(quality, dict):
            score = quality.get("score")
            why = quality.get("why")
            if score is not None or why:
                content_parts.append(f"Quality: score={score} why={why}")

        content = "\n".join(p for p in content_parts if p)
        if not content:
            continue

        snippet = _make_snippet(title, content)
        chunks.append(
            DocChunk(
                kind="source",
                unit_id=unit_id,
                locator=locator,
                snippet=snippet,
                content=content,
            )
        )

    return chunks


def _chunk_roadmap_json(data: Any) -> List[DocChunk]:
    if not isinstance(data, dict):
        return []
    modules = data.get("modules")
    if not isinstance(modules, list):
        return []
    chunks: List[DocChunk] = []

    for mod in modules:
        if not isinstance(mod, dict):
            continue
        mod_id = str(mod.get("id") or "").strip()
        unit_id = mod_id if mod_id.startswith("M-") else f"M-{mod_id}"
        title = str(mod.get("title") or "").strip()
        locator = title or f"Module {mod_id}"
        why = str(mod.get("why") or "").strip()
        outputs = mod.get("outputs")

        content_parts = [title]
        if why:
            content_parts.append(f"Why: {why}")
        if isinstance(outputs, list):
            content_parts.append(f"Outputs: {', '.join(str(o) for o in outputs)}")
        elif isinstance(outputs, str) and outputs:
            content_parts.append(f"Outputs: {outputs}")

        core = mod.get("core")
        if isinstance(core, list):
            for c_item in core:
                if isinstance(c_item, dict):
                    t = str(c_item.get("text") or "").strip()
                    if t:
                        content_parts.append(t)

        misconceptions = mod.get("misconceptions")
        if isinstance(misconceptions, list):
            for m_item in misconceptions:
                if isinstance(m_item, dict):
                    wrong = str(m_item.get("wrong") or "").strip()
                    m_why = str(m_item.get("why") or "").strip()
                    fix = str(m_item.get("fix") or "").strip()
                    parts = [p for p in [f"Wrong: {wrong}" if wrong else "", f"Why: {m_why}" if m_why else "", f"Fix: {fix}" if fix else ""] if p]
                    if parts:
                        content_parts.append(" | ".join(parts))

        content = "\n".join(p for p in content_parts if p)
        if not content:
            continue

        snippet = _make_snippet(title, content)
        chunks.append(
            DocChunk(
                kind="module",
                unit_id=unit_id,
                locator=locator,
                snippet=snippet,
                content=content,
            )
        )

    return chunks


def _chunk_errorlog_md(text: str) -> List[DocChunk]:
    chunks: List[DocChunk] = []
    # Split by ### headings
    sections = re.split(r"(?:^|\n)(?=###\s+)", text)
    for sec in sections:
        sec = sec.strip()
        if not sec or not sec.startswith("###"):
            continue
        first_line, _, body = sec.partition("\n")
        heading = first_line.lstrip("#").strip()
        if not heading:
            continue
        content = sec
        snippet = _make_snippet(heading, content)
        chunks.append(
            DocChunk(
                kind="error",
                unit_id=heading,
                locator=heading,
                snippet=snippet,
                content=content,
            )
        )
    return chunks


def _chunk_headings_md(text: str, kind: str, base_name: str) -> List[DocChunk]:
    chunks: List[DocChunk] = []
    # Split by #, ##, ###, #### headings
    sections = re.split(r"(?:^|\n)(?=#{1,4}\s+)", text)
    for idx, sec in enumerate(sections, start=1):
        sec = sec.strip()
        if not sec:
            continue
        first_line, _, _ = sec.partition("\n")
        heading = first_line.lstrip("#").strip()
        locator = heading or f"Section {idx}"
        unit_id = heading or base_name
        snippet = _make_snippet(heading, sec)
        chunks.append(
            DocChunk(
                kind=kind,
                unit_id=unit_id,
                locator=locator,
                snippet=snippet,
                content=sec,
            )
        )
    return chunks


def _parse_time_seconds(time_str: str) -> Optional[int]:
    """Parses mm:ss or hh:mm:ss to total seconds."""
    parts = time_str.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, TypeError):
        return None
    return None


def _chunk_captures_md(text: str, base_name: str) -> List[DocChunk]:
    chunks: List[DocChunk] = []

    # 1. Page markers: [p. N]
    page_matches = list(re.finditer(r"\[p\.\s*(\d+)\]", text, re.IGNORECASE))
    if page_matches:
        for i, match in enumerate(page_matches):
            p_num = match.group(1)
            start_pos = match.end()
            end_pos = page_matches[i + 1].start() if i + 1 < len(page_matches) else len(text)
            segment = text[start_pos:end_pos].strip()
            if not segment:
                continue
            locator = f"p. {p_num}"
            snippet = _make_snippet(None, segment)
            chunks.append(
                DocChunk(
                    kind="capture",
                    unit_id=base_name,
                    locator=locator,
                    snippet=snippet,
                    content=segment,
                )
            )
        return chunks

    # 2. Timestamp markers: [mm:ss] or [hh:mm:ss]
    time_matches = list(re.finditer(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", text))
    if time_matches:
        # Group into windows of approximately 60 seconds
        current_window_start_sec: Optional[int] = None
        current_locator: Optional[str] = None
        current_texts: List[str] = []

        def flush_window() -> None:
            nonlocal current_texts, current_locator, current_window_start_sec
            if current_texts and current_locator:
                joined = "\n".join(t for t in current_texts if t).strip()
                if joined:
                    chunks.append(
                        DocChunk(
                            kind="capture",
                            unit_id=base_name,
                            locator=current_locator,
                            snippet=_make_snippet(None, joined),
                            content=joined,
                        )
                    )
            current_texts = []
            current_locator = None
            current_window_start_sec = None

        for i, match in enumerate(time_matches):
            t_str = match.group(1)
            t_sec = _parse_time_seconds(t_str) or 0
            start_pos = match.end()
            end_pos = time_matches[i + 1].start() if i + 1 < len(time_matches) else len(text)
            segment = text[start_pos:end_pos].strip()

            if current_window_start_sec is None:
                current_window_start_sec = t_sec
                current_locator = t_str
                current_texts.append(segment)
            else:
                if (t_sec - current_window_start_sec) >= 55:
                    flush_window()
                    current_window_start_sec = t_sec
                    current_locator = t_str
                    current_texts.append(segment)
                else:
                    current_texts.append(segment)

        flush_window()
        return chunks

    # 3. Fallback: split into ~1500-char blocks (locator §1, §2...)
    BLOCK_SIZE = 1500
    pos = 0
    block_idx = 1
    text_len = len(text)

    while pos < text_len:
        end_pos = min(pos + BLOCK_SIZE, text_len)
        if end_pos < text_len:
            # Try to break at a clean boundary (newline or sentence)
            newline_cut = text.rfind("\n", pos, end_pos)
            if newline_cut > pos + (BLOCK_SIZE // 2):
                end_pos = newline_cut + 1
            else:
                space_cut = text.rfind(" ", pos, end_pos)
                if space_cut > pos + (BLOCK_SIZE // 2):
                    end_pos = space_cut + 1

        block_text = text[pos:end_pos].strip()
        if block_text:
            chunks.append(
                DocChunk(
                    kind="capture",
                    unit_id=base_name,
                    locator=f"§{block_idx}",
                    snippet=_make_snippet(None, block_text),
                    content=block_text,
                )
            )
            block_idx += 1
        pos = end_pos

    return chunks


def parse_learning_file(full_path: str, rel_path: str) -> List[DocChunk]:
    """Reads and parses a recognized learning file into DocChunk objects.

    Enforces:
    - Skip files > 8 MB
    - Zero unhandled exceptions (catches JSON errors, encoding errors, IO errors)
    - Per-chunk caps (<= 4000 chars) and per-file caps (<= 400 chunks)
    """
    if not is_learning_file(rel_path):
        return []

    try:
        stat = os.stat(full_path)
        if stat.st_size > MAX_FILE_SIZE:
            logger.debug("Skipping learning file > 8MB: %s (%d bytes)", rel_path, stat.st_size)
            return []
    except (OSError, PermissionError) as e:
        logger.debug("Cannot stat %s: %s", rel_path, e)
        return []

    norm = rel_path.replace("\\", "/").strip("/")
    parts = norm.split("/")
    filename = parts[-1]
    fn_lower = filename.lower()
    base_name = os.path.splitext(filename)[0]

    # Aurora deck.json
    if fn_lower == "deck.json":
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            chunks = _chunk_deck_json(data)
            return _cap_chunks(chunks)
        except Exception as e:
            logger.debug("Failed parsing deck.json at %s: %s", rel_path, e)
            return []

    # Aurora course.json
    if fn_lower == "course.json":
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            chunks = _chunk_course_json(data)
            return _cap_chunks(chunks)
        except Exception as e:
            logger.debug("Failed parsing course.json at %s: %s", rel_path, e)
            return []

    parts_lower = [p.lower() for p in parts[:-1]]
    if "polaris" not in parts_lower:
        return []

    if fn_lower == "facts.json":
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            chunks = _chunk_facts_json(data)
            return _cap_chunks(chunks)
        except Exception as e:
            logger.debug("Failed parsing facts.json at %s: %s", rel_path, e)
            return []

    if fn_lower == "sources.json":
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            chunks = _chunk_sources_json(data)
            return _cap_chunks(chunks)
        except Exception as e:
            logger.debug("Failed parsing sources.json at %s: %s", rel_path, e)
            return []

    if fn_lower == "roadmap.json":
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            chunks = _chunk_roadmap_json(data)
            return _cap_chunks(chunks)
        except Exception as e:
            logger.debug("Failed parsing roadmap.json at %s: %s", rel_path, e)
            return []

    if fn_lower == "errorlog.md":
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            chunks = _chunk_errorlog_md(text)
            return _cap_chunks(chunks)
        except Exception as e:
            logger.debug("Failed parsing errorlog.md at %s: %s", rel_path, e)
            return []

    if fn_lower.endswith(".outline.md") or "lessons" in parts_lower:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            chunks = _chunk_headings_md(text, kind="outline", base_name=base_name)
            return _cap_chunks(chunks)
        except Exception as e:
            logger.debug("Failed parsing outline at %s: %s", rel_path, e)
            return []

    if "research" in parts_lower and fn_lower.endswith(".md"):
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            chunks = _chunk_headings_md(text, kind="research", base_name=base_name)
            return _cap_chunks(chunks)
        except Exception as e:
            logger.debug("Failed parsing research markdown at %s: %s", rel_path, e)
            return []

    if "captures" in parts_lower and fn_lower.endswith(".md"):
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            chunks = _chunk_captures_md(text, base_name=base_name)
            return _cap_chunks(chunks)
        except Exception as e:
            logger.debug("Failed parsing captures markdown at %s: %s", rel_path, e)
            return []

    return []


__all__ = [
    "MAX_FILE_SIZE",
    "MAX_CHUNK_CHARS",
    "MAX_CHUNKS_PER_FILE",
    "MAX_SNIPPET_CHARS",
    "RECOGNIZED_KINDS",
    "DocChunk",
    "is_learning_file",
    "parse_learning_file",
]
