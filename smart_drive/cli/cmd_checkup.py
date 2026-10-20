"""smart_drive.cli.cmd_checkup - Read-only 0-100 drive health score and top 3 recommended next steps."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import locale
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Mapping, Optional, Union

from smart_drive.cli.cmd_paths import inspect_broken_paths
from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.config import (
    CLUSTER_SIZE_BYTES,
    JunkTier,
    verify_anti_indexing_markers,
)
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.fsinfo import detect_filesystem
from smart_drive.core.health import check_drive_health
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.offloader import get_scan_summary
from smart_drive.core.purge_engine import list_quarantine_batches
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex


# Explicit, transparent scoring weights (maximum penalty per category, out of 100 total points).
SCORE_WEIGHTS: Dict[str, int] = {
    "free_space": 25,          # Low free space (<15% warning, <5% critical)
    "reclaimable_junk": 20,    # Tier 1-2 junk files & caches accumulating on drive
    "broken_paths": 20,        # Stale IDE/agent/shortcut/PATH references into the drive (others are listed, not scored)
    "unorganized_root": 15,    # Loose unorganized folders/files at the drive root
    "trim_and_shields": 10,    # SSD TRIM disabled or anti-indexing markers missing
    "quarantine_and_aux": 10,  # Unpurged quarantine batches, C: caches, or duplicate files (with --dup)
}


def detect_preferred_lang(explicit_lang: Optional[str] = None) -> str:
    """Resolves 'vi' or 'en' from explicit flag, SMARTDRIVE_LANG, or OS locale."""
    if explicit_lang:
        low = explicit_lang.strip().lower()
        if low.startswith("vi"):
            return "vi"
        if low.startswith("en"):
            return "en"

    for env_key in ("SMARTDRIVE_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(env_key, "").strip().lower()
        if val.startswith("vi"):
            return "vi"
        if val.startswith("en"):
            return "en"

    try:
        loc = locale.getlocale()[0] or ""
        if loc.lower().startswith("vi"):
            return "vi"
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            # Primary language ID 0x2a is Vietnamese (e.g. 0x042a = 1066)
            if (lcid & 0x3FF) == 0x2A:
                return "vi"
        except Exception:
            pass

    return "en"


def _format_bytes(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / (1024 ** 3):.2f} GB"
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / (1024 ** 2):.2f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} B"


def run_checkup(
    root: str,
    include_dup: bool = False,
    lang: Optional[str] = None,
    home: Optional[Path] = None,
    app_data: Optional[Path] = None,
    reader: Optional[Union[RegistryReader, Mapping[str, Any]]] = None,
    scan_c_caches: Optional[bool] = None,
) -> Dict[str, Any]:
    """Executes a read-only drive checkup, updates <root>/.smart_drive/checkup.json, and returns the report."""
    root_abs = os.path.abspath(root)
    resolved_lang = detect_preferred_lang(lang)

    # 1. Read previous score if <root>/.smart_drive/checkup.json exists
    checkup_file = os.path.join(root_abs, ".smart_drive", "checkup.json")
    previous_score: Optional[int] = None
    previous_time: Optional[str] = None
    if os.path.isfile(checkup_file):
        try:
            with open(checkup_file, "r", encoding="utf-8") as f:
                prev_data = json.load(f)
            if isinstance(prev_data, dict) and isinstance(prev_data.get("score"), int):
                previous_score = int(prev_data["score"])
                previous_time = prev_data.get("timestamp")
        except Exception:
            previous_score = None

    # 2. Filesystem & real cluster geometry
    fs_info = detect_filesystem(root_abs)
    real_cluster = fs_info.block_size if fs_info.block_size > 0 else CLUSTER_SIZE_BYTES

    # 3. Capacity & SSD health (read-only)
    try:
        usage = shutil.disk_usage(root_abs)
        total_bytes = int(usage.total)
        free_bytes = int(usage.free)
        used_bytes = max(0, total_bytes - free_bytes)
        free_percent = round((free_bytes / total_bytes) * 100.0, 2) if total_bytes > 0 else 100.0
    except OSError:
        total_bytes = 0
        free_bytes = 0
        used_bytes = 0
        free_percent = 100.0

    drive_spec = os.path.splitdrive(root_abs)[0] or root_abs
    try:
        health_rep = check_drive_health(drive_spec)
        trim_enabled = health_rep.trim_enabled
        trim_msg = health_rep.trim_status_message
    except Exception:
        trim_enabled = None
        trim_msg = "unavailable"

    shields_map = verify_anti_indexing_markers(root_abs)
    shields_ok = all(shields_map.values())

    # 4. Storage auditor summary
    auditor = StorageAuditor(root_abs, cluster_size=real_cluster)
    audit_rep = auditor.run_audit()

    # 5. What Windows and installed applications depend on: read once, then shared by the junk scan and AutoZoner
    ref_index = SystemRefIndex.collect(home=home, reader=reader, shortcut_roots=[root_abs])

    # 6. Tier 1-2 reclaimable junk (dry-run only; nothing inside a folder the system depends on)
    detector = JunkDetector(root_abs, max_tier=JunkTier.TIER_2_DEV_CACHE, system_refs=ref_index)
    junk_items = detector.find_junk()
    junk_count = len(junk_items)
    junk_bytes = sum(int(getattr(j, "size", 0) or (j.get("size", 0) if isinstance(j, dict) else 0)) for j in junk_items)

    # 7. Quarantine batches
    q_batches = list_quarantine_batches(root_abs)
    q_batch_count = len(q_batches)
    q_total_bytes = sum(int(b.get("total_bytes", 0) or 0) for b in q_batches)

    # 8. AutoZoner dry-run & system-protected folders
    zoner = AutoZoner(root_abs, cluster_size=real_cluster, system_refs=ref_index)
    zoning_plan = zoner.generate_plan()
    unorganized_count = len(zoning_plan)
    protected_by_system = zoner.protected_by_system

    # 9. Broken paths & stale agent/IDE entries (T16)
    broken_report = inspect_broken_paths(
        root=root_abs,
        home=home,
        app_data=app_data,
        reader=reader,
    )
    broken_total = int(broken_report["total_broken"])
    broken_fixable = int(broken_report["fixable_count"])
    # Only references into this drive are scored and recommended: the rest of the computer's stale entries
    # (an IDE's recent-project list, a PATH folder on C:) cannot be repaired by anything done on the drive.
    broken_on_drive = int(broken_report["on_drive_count"])
    broken_fixable_on_drive = int(broken_report["fixable_on_drive_count"])
    stale_ide_count = len(broken_report["ide_and_agents"])

    # 10. Large C: developer caches on Windows
    do_c_scan = (sys.platform == "win32" and home is None) if scan_c_caches is None else scan_c_caches
    c_cache_summary: Optional[Dict[str, Any]] = None
    c_reclaimable_bytes = 0
    c_discovered_count = 0
    if do_c_scan:
        try:
            c_cache_summary = get_scan_summary()
            c_reclaimable_bytes = int(c_cache_summary.get("total_reclaimable_bytes", 0) or 0)
            c_discovered_count = int(c_cache_summary.get("discovered_count", 0) or 0)
        except Exception:
            c_cache_summary = None

    # 11. Optional duplicate scan (--dup)
    dup_summary: Optional[Dict[str, Any]] = None
    dup_reclaimable_bytes = 0
    dup_group_count = 0
    if include_dup:
        dup_plan = DuplicateDetector(root_abs, cluster_size=real_cluster).generate_reclamation_plan()
        dup_group_count = int(dup_plan.get("duplicate_group_count", 0) or 0)
        dup_reclaimable_bytes = int(dup_plan.get("total_reclaimable_bytes", 0) or 0)
        dup_summary = {
            "enabled": True,
            "duplicate_group_count": dup_group_count,
            "duplicate_file_count": int(dup_plan.get("duplicate_file_count", 0) or 0),
            "total_reclaimable_bytes": dup_reclaimable_bytes,
        }
    else:
        dup_summary = {"enabled": False}

    # ==========================================================================
    # Transparent Score Calculation (0 - 100)
    # ==========================================================================
    penalties: Dict[str, int] = {k: 0 for k in SCORE_WEIGHTS}

    # A. Free space (max 25)
    if total_bytes > 0:
        if free_percent < 5.0:
            penalties["free_space"] = 25
        elif free_percent < 15.0:
            penalties["free_space"] = 12

    # B. Reclaimable Tier 1-2 junk (max 20)
    if junk_count > 0:
        if junk_bytes >= 500 * (1024 ** 2) or junk_count >= 50:
            penalties["reclaimable_junk"] = 20
        elif junk_bytes >= 50 * (1024 ** 2) or junk_count >= 10:
            penalties["reclaimable_junk"] = 12
        else:
            penalties["reclaimable_junk"] = 6

    # C. Broken paths & stale agent/IDE entries (max 20)
    if broken_on_drive > 0:
        penalties["broken_paths"] = min(20, 5 + broken_on_drive * 3)

    # D. Unorganized root folders/files (max 15)
    if unorganized_count > 0:
        penalties["unorganized_root"] = min(15, 3 + unorganized_count * 3)

    # E. TRIM & anti-indexing shields (max 10)
    trim_pen = 5 if trim_enabled is False else 0
    shield_pen = 0 if shields_ok else 5
    penalties["trim_and_shields"] = min(10, trim_pen + shield_pen)

    # F. Quarantine & auxiliary (max 10)
    aux_pen = 0
    if q_batch_count > 0:
        aux_pen += 5
    if c_reclaimable_bytes >= 500 * (1024 ** 2):
        aux_pen += 5
    if include_dup and dup_group_count > 0:
        aux_pen += 5
    penalties["quarantine_and_aux"] = min(10, aux_pen)

    total_penalty = sum(penalties.values())
    score = max(0, min(100, 100 - total_penalty))
    score_delta = (score - previous_score) if previous_score is not None else None

    # ==========================================================================
    # Build Prioritized Next Steps (Top 3)
    # ==========================================================================
    candidate_steps: List[Dict[str, Any]] = []

    if broken_on_drive > 0:
        cmd = "smart-drive paths --broken --fix" if broken_fixable_on_drive > 0 else "smart-drive paths --broken"
        if resolved_lang == "vi":
            title = (
                f"Sửa {broken_on_drive} đường dẫn IDE/shortcut/PATH bị gãy trỏ vào ổ này "
                f"({broken_fixable_on_drive} mục sửa tự động được)"
            )
            desc = "Cập nhật lại đường dẫn dự án cho IDE, coding agent và shortcut sau khi dời thư mục."
        else:
            title = (
                f"Repair {broken_on_drive} broken IDE/shortcut/PATH reference(s) into this drive "
                f"({broken_fixable_on_drive} auto-fixable)"
            )
            desc = "Repoint stale IDE recent projects, coding agents, and Windows shortcuts."
        candidate_steps.append({
            "priority": 100,
            "category": "broken_paths",
            "title": title,
            "description": desc,
            "command": cmd,
        })

    if junk_count > 0:
        # Tier 1-2 is deleted at once, not quarantined: the step is to look first, and only then to apply
        if resolved_lang == "vi":
            title = f"Xem trước {junk_count} mục rác có thể dọn ({_format_bytes(junk_bytes)})"
            desc = (
                "Mục Tier 1-2 tái tạo được: metadata hệ điều hành, bytecode, cache, thư mục build/dist của dự án. "
                "--apply xóa vĩnh viễn (không qua khu cách ly), nên hãy xem danh sách dry-run trước."
            )
        else:
            title = f"Preview {junk_count} junk item(s) that clean can remove ({_format_bytes(junk_bytes)})"
            desc = (
                "Regenerable Tier 1-2 items: OS metadata, bytecode, caches, project build/dist folders. "
                "--apply deletes them permanently (no quarantine), so read the dry-run list first."
            )
        candidate_steps.append({
            "priority": 90 if junk_bytes >= 50 * (1024 ** 2) else 75,
            "category": "reclaimable_junk",
            "title": title,
            "description": desc,
            "command": "smart-drive clean --tier 2",
            "apply_command": "smart-drive clean --tier 2 --apply",
        })

    if unorganized_count > 0:
        if resolved_lang == "vi":
            title = f"Sắp xếp {unorganized_count} mục chưa phân vùng ở gốc ổ đĩa"
            desc = "Đưa các thư mục/file lẻ ở gốc ổ vào đúng phân vùng chuẩn và tự động relink IDE."
        else:
            title = f"Organize {unorganized_count} loose item(s) at the drive root"
            desc = "Move unorganized root items into standard taxonomy zones and relink IDEs."
        candidate_steps.append({
            "priority": 80,
            "category": "unorganized_root",
            "title": title,
            "description": desc,
            "command": "smart-drive organize --apply",
        })

    if not shields_ok:
        if resolved_lang == "vi":
            title = "Khôi phục khiên chống lập chỉ mục nền (.metadata_never_index)"
            desc = "Ngăn Spotlight / Windows Search quét ngầm làm chậm ổ SSD."
        else:
            title = "Restore anti-indexing shields (.metadata_never_index)"
            desc = "Prevent OS background indexers from thrashing the SSD."
        candidate_steps.append({
            "priority": 70,
            "category": "trim_and_shields",
            "title": title,
            "description": desc,
            "command": "smart-drive sentinel",
        })

    if q_batch_count > 0:
        if resolved_lang == "vi":
            title = f"Xem hoặc dọn {q_batch_count} đợt cách ly ({_format_bytes(q_total_bytes)})"
            desc = "Xóa vĩnh viễn các đợt cách ly cũ sau khi đã kiểm tra không cần khôi phục."
        else:
            title = f"Review or purge {q_batch_count} quarantine batch(es) ({_format_bytes(q_total_bytes)})"
            desc = "Permanently remove old quarantined items once you no longer need them."
        candidate_steps.append({
            "priority": 60,
            "category": "quarantine_and_aux",
            "title": title,
            "description": desc,
            "command": "smart-drive clean --purge-quarantine --older-than 7 --apply",
        })

    if c_discovered_count > 0 and c_reclaimable_bytes > 0:
        if resolved_lang == "vi":
            title = f"Chuyển {c_discovered_count} bộ nhớ đệm lớn trên ổ C: ({_format_bytes(c_reclaimable_bytes)}) sang ổ phụ"
            desc = "Giải phóng dung lượng ổ hệ thống C: bằng NTFS junction an toàn."
        else:
            title = f"Offload {c_discovered_count} heavy C: drive cache(s) ({_format_bytes(c_reclaimable_bytes)})"
            desc = "Free system drive space using transactional NTFS junctions."
        candidate_steps.append({
            "priority": 55,
            "category": "quarantine_and_aux",
            "title": title,
            "description": desc,
            "command": "smart-drive offload --scan",
        })

    if include_dup and dup_group_count > 0:
        if resolved_lang == "vi":
            title = f"Kiểm tra {dup_group_count} nhóm file trùng lặp ({_format_bytes(dup_reclaimable_bytes)})"
            desc = "Xem danh sách các file trùng SHA-256 để thu hồi dung lượng."
        else:
            title = f"Inspect {dup_group_count} duplicate file group(s) ({_format_bytes(dup_reclaimable_bytes)})"
            desc = "Review exact SHA-256 duplicate files to reclaim wasted space."
        candidate_steps.append({
            "priority": 50,
            "category": "quarantine_and_aux",
            "title": title,
            "description": desc,
            "command": "smart-drive dup",
        })

    # Always provide up to 3 helpful steps even on a clean drive
    fallback_steps_vi = [
        {
            "priority": 10,
            "category": "audit",
            "title": "Xuất báo cáo dung lượng trực quan (HTML Treemap)",
            "description": "Xem biểu đồ dung lượng thư mục và 50 mục lớn nhất trên ổ đĩa.",
            "command": "smart-drive audit --html",
        },
        {
            "priority": 9,
            "category": "index",
            "title": "Cập nhật chỉ mục tìm kiếm nhanh SQLite FTS5",
            "description": "Đồng bộ chỉ mục để tìm file dưới 10ms.",
            "command": "smart-drive update",
        },
        {
            "priority": 8,
            "category": "dup",
            "title": "Quét file trùng lặp SHA-256 trên toàn ổ",
            "description": "Kiểm tra xem có file lớn nào bị lưu trùng nhiều bản không.",
            "command": "smart-drive checkup --dup",
        },
    ]
    fallback_steps_en = [
        {
            "priority": 10,
            "category": "audit",
            "title": "Export interactive HTML storage treemap report",
            "description": "Inspect folder space usage and top 50 largest items visually.",
            "command": "smart-drive audit --html",
        },
        {
            "priority": 9,
            "category": "index",
            "title": "Sync the SQLite FTS5 instant search index",
            "description": "Keep sub-10ms file search up to date with recent changes.",
            "command": "smart-drive update",
        },
        {
            "priority": 8,
            "category": "dup",
            "title": "Run a full SHA-256 duplicate file scan",
            "description": "Check for redundant copies of large files across the drive.",
            "command": "smart-drive checkup --dup",
        },
    ]

    candidate_steps.sort(key=lambda s: s["priority"], reverse=True)
    fallbacks = fallback_steps_vi if resolved_lang == "vi" else fallback_steps_en
    for fb in fallbacks:
        if len(candidate_steps) >= 3:
            break
        candidate_steps.append(fb)
    next_steps = candidate_steps[:3]

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if previous_score is not None:
        if resolved_lang == "vi":
            comparison_text = f"Lần trước {previous_score} → giờ {score}"
        else:
            comparison_text = f"Previous {previous_score} → now {score}"
    else:
        comparison_text = None

    report: Dict[str, Any] = {
        "timestamp": now_iso,
        "root": root_abs,
        "lang": resolved_lang,
        "score": score,
        "previous_score": previous_score,
        "previous_timestamp": previous_time,
        "score_delta": score_delta,
        "comparison_text": comparison_text,
        "weights": dict(SCORE_WEIGHTS),
        "penalties": penalties,
        "filesystem": {
            "type": fs_info.fs_type,
            "display_name": fs_info.display_name,
            "cluster_size_bytes": real_cluster,
            "summary": fs_info.summary(),
        },
        "health": {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
            "free_percent": free_percent,
            "trim_enabled": trim_enabled,
            "trim_status_message": trim_msg,
            "anti_indexing_shields_ok": shields_ok,
            "anti_indexing_shields": shields_map,
        },
        "storage": {
            "total_files": audit_rep["total_files"],
            "total_dirs": audit_rep["total_directories"],
            "nominal_bytes": audit_rep["total_logical_bytes"],
            "allocated_bytes": audit_rep["total_allocated_bytes"],
            "slack_bytes": audit_rep["total_slack_bytes"],
            "slack_percentage": round(float(audit_rep["total_slack_percentage"]), 2),
        },
        "junk": {
            "tier_1_2_count": junk_count,
            "reclaimable_bytes": junk_bytes,
            # Entries named like junk that were left alone (installed packages, folders the system depends on...)
            "kept_count": detector.get_kept_summary()["total"],
            "kept_by_reason": dict(detector.kept),
        },
        "quarantine": {
            "batch_count": q_batch_count,
            "total_bytes": q_total_bytes,
        },
        "unorganized": {
            "count": unorganized_count,
            "items": [a.to_dict() for a in zoning_plan[:20]],
        },
        "broken_paths": {
            "total_broken": broken_total,
            "fixable_count": broken_fixable,
            "on_drive_count": broken_on_drive,
            "fixable_on_drive_count": broken_fixable_on_drive,
            "stale_ide_and_agent_count": stale_ide_count,
            "broken_shortcut_count": len(broken_report["shortcuts"]),
            "broken_path_entry_count": len(broken_report["path_entries"]),
        },
        "protected_by_system": protected_by_system,
        "offload_caches": {
            "scanned": bool(c_cache_summary is not None),
            "discovered_count": c_discovered_count,
            "reclaimable_bytes": c_reclaimable_bytes,
        },
        "duplicates": dup_summary,
        "next_steps": next_steps,
    }

    # Persist single checkup state file (<root>/.smart_drive/checkup.json)
    try:
        os.makedirs(os.path.dirname(checkup_file), exist_ok=True)
        with open(checkup_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

    return report


def _print_checkup(report: Dict[str, Any]) -> None:
    lang = report["lang"]
    score = report["score"]
    comp = report.get("comparison_text")
    fs = report["filesystem"]
    health = report["health"]
    storage = report["storage"]
    junk = report["junk"]
    quar = report["quarantine"]
    unorg = report["unorganized"]
    broken = report["broken_paths"]
    prot = report["protected_by_system"]

    print("=" * 64)
    if lang == "vi":
        print(f"  SmartDrive-OS: Khám Sức Khỏe Ổ Đĩa ({report['root']})")
        score_line = f"  Điểm sức khỏe: {score}/100"
    else:
        print(f"  SmartDrive-OS: Drive Health Checkup ({report['root']})")
        score_line = f"  Health Score:  {score}/100"
    if comp:
        score_line += f"  ({comp})"
    print(score_line)
    print("=" * 64)

    if lang == "vi":
        print(f"  - Hệ tệp & Cluster:   {fs['display_name']} (cluster {fs['cluster_size_bytes'] // 1024 or 1} KB)")
        print(
            f"  - Dung lượng trống:   {_format_bytes(health['free_bytes'])} / "
            f"{_format_bytes(health['total_bytes'])} ({health['free_percent']:.1f}%)"
        )
        print(
            f"  - Đang lưu trữ:       {storage['total_files']:,} file, {storage['total_dirs']:,} thư mục "
            f"(slack: {_format_bytes(storage['slack_bytes'])})"
        )
        print(f"  - Rác Tier 1-2:       {junk['tier_1_2_count']} mục ({_format_bytes(junk['reclaimable_bytes'])})")
        if junk.get("kept_count"):
            print(f"  - Giống rác, đã giữ:  {junk['kept_count']} mục (gói đã cài, thư mục hệ thống đang dùng...)")
        print(f"  - Khu cách ly:        {quar['batch_count']} đợt ({_format_bytes(quar['total_bytes'])})")
        print(f"  - Mục lẻ ở gốc ổ:     {unorg['count']} mục")
        print(
            f"  - Đường dẫn bị gãy:   {broken['on_drive_count']} mục trỏ vào ổ này "
            f"(cả máy: {broken['total_broken']}; {broken['fixable_count']} mục sửa tự động được)"
        )
        print(f"  - Được bảo vệ (OS):   {len(prot)} thư mục")
        print("\n3 việc nên làm tiếp theo:")
    else:
        print(f"  - Filesystem:         {fs['display_name']} ({fs['cluster_size_bytes'] // 1024 or 1} KB cluster)")
        print(
            f"  - Free Space:         {_format_bytes(health['free_bytes'])} / "
            f"{_format_bytes(health['total_bytes'])} ({health['free_percent']:.1f}%)"
        )
        print(
            f"  - Indexed Content:    {storage['total_files']:,} files, {storage['total_dirs']:,} dirs "
            f"(slack: {_format_bytes(storage['slack_bytes'])})"
        )
        print(f"  - Tier 1-2 Junk:      {junk['tier_1_2_count']} item(s) ({_format_bytes(junk['reclaimable_bytes'])})")
        if junk.get("kept_count"):
            print(f"  - Kept, not junk:     {junk['kept_count']} item(s) (installed packages, folders the system uses...)")
        print(f"  - Quarantine:         {quar['batch_count']} batch(es) ({_format_bytes(quar['total_bytes'])})")
        print(f"  - Loose Root Items:   {unorg['count']} item(s)")
        print(
            f"  - Broken Paths:       {broken['on_drive_count']} item(s) into this drive "
            f"({broken['total_broken']} on the whole computer; {broken['fixable_count']} auto-fixable)"
        )
        print(f"  - System-Protected:   {len(prot)} folder(s)")
        print("\nTop 3 recommended next steps:")

    for idx, step in enumerate(report["next_steps"], 1):
        print(f"  {idx}. {step['title']}")
        print(f"     {step['description']}")
        print(f"     -> {step['command']}")
        if step.get("apply_command"):
            follow_up = "rồi, nếu danh sách ổn" if lang == "vi" else "then, once the list looks right"
            print(f"        {follow_up}: {step['apply_command']}")


def cmd_checkup(args: argparse.Namespace) -> int:
    """Handles the `checkup` subcommand."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as err:
        sys.stderr.write(f"{err}\n")
        return 2

    include_dup = bool(getattr(args, "dup", False))
    lang = getattr(args, "lang", None)
    as_json = bool(getattr(args, "json", False))

    report = run_checkup(root=root, include_dup=include_dup, lang=lang)
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_checkup(report)
    return 0


__all__ = ["SCORE_WEIGHTS", "cmd_checkup", "detect_preferred_lang", "run_checkup"]
