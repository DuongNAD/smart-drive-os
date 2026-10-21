"""smart_drive.cli.smart_organize_runner - Guided safe organizer workflow for Smart_Organize launchers.

Implements the interactive end-user flow for `Smart_Organize.{bat,ps1,command,sh}`:
1. Dry-run preview (`organize --dry-run --json`) showing planned moves and protected items; it writes nothing to the
   drive.
2. Wait for `busy_apps` (open IDEs / coding agents) to close before moving; when the list of running programs cannot
   be read (`busy_apps_unknown`), ask for the coding agents and IDEs to be closed all the same.
3. Explicit `YES` confirmation prompt.
4. Execute `organize --apply --json` and clearly report items that could NOT be moved with reasons.
5. Post-move verification (`relink --dry-run` has nothing left to fix, git/worktree integrity, venv paths).
6. Interactive post-move prompt: `R` (retry relink/checks), `U` (undo via `smart-drive undo --apply`), or `Enter` (keep).

Supports:
- Bilingual i18n: English (`en`) by default; Vietnamese (`vi`) when system locale is `vi-*` or `SMARTDRIVE_LANG=vi`.
- Self-test mode when `SD_SELFTEST=1` (or `--selftest`): the preview and the checks, and nothing is written to the drive.
- Execution log at `<root>/.smart_drive/smart-organize.log`, written once the moves are confirmed with `YES`. The preview
  keeps its log lines in memory (`OrganizeLog`), so a run that ends before that (nothing to move, `Q` while apps are
  open, anything but `YES`, the self-test) leaves the drive exactly as it was, and no log.
"""

from __future__ import annotations

import argparse
import ctypes
import io
import json
import locale
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.relink import last_undoable_moves, relink
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


# ==============================================================================
# 1. INTERNATIONALIZATION (EN default, VI when system is vi-* or SMARTDRIVE_LANG=vi)
# ==============================================================================

I18N_STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "banner_title": "SmartDrive-OS - Smart Organize (Safe Guided Move)",
        "drive_root": "Drive Root: {root}",
        "log_path": "Log File:   {log_file} (written once you confirm the moves)",
        "step1_title": "[1/5] Scanning drive (Dry-Run Preview)...",
        "planned_header": "Planned folder/file moves ({count}):",
        "planned_item": "  + {src}  -->  {dst}  ({reason})",
        "no_planned_moves": "All root items are already in the right place (0 moves needed).",
        "cannot_move_header": "Items that CANNOT be moved ({count}) and why:",
        "cannot_move_item": "  ! {name} — kept in place ({reasons})",
        "cannot_move_none": "No blocked or failed items.",
        "step2_busy_title": "[2/5] Open coding agents / IDEs detected: {apps}",
        "step2_busy_hint": "Please close these apps so SmartDrive can safely update their project paths.",
        "step2_unknown_title": "[2/5] Could not check which coding agents / IDEs are open: the list of running programs could not be read.",
        "step2_unknown_hint": "Please close your coding agents and IDEs so SmartDrive can safely update their project paths.",
        "step2_busy_prompt": "Press Enter to re-check, [C] to continue anyway, or [Q] to cancel: ",
        "step2_clear": "[2/5] No conflicting open apps detected.",
        "step3_confirm_prompt": "[3/5] Type YES to move {count} item(s) safely (or anything else to cancel): ",
        "cancelled": "Cancelled. No files or folders were moved.",
        "step4_apply_title": "[4/5] Moving items and relinking IDEs/agents (organize --apply)...",
        "moved_summary": "Successfully moved {moved} item(s).",
        "move_errors_header": "Items that FAILED to move during execution ({count}):",
        "move_error_item": "  x {error}",
        "step5_verify_title": "[5/5] Post-move safety verification:",
        "verify_relink_ok": "  [OK]   Relink check (relink --dry-run): 0 pending paths left to fix.",
        "verify_relink_warn": "  [WARN] Relink check (relink --dry-run): {count} path(s) still pending ({apps}).",
        "verify_git_ok": "  [OK]   Git / worktree check: {checked} repository(ies) verified intact.",
        "verify_git_warn": "  [WARN] Git / worktree check: {broken} issue(s) found: {details}",
        "verify_venv_ok": "  [OK]   Virtualenv (.venv) check: {checked} environment(s) verified clean.",
        "verify_venv_warn": "  [WARN] Virtualenv (.venv) check: {stale} stale path(s) found: {details}",
        "decision_prompt": "Choose action: [R] Retry relink & check | [U] Undo move batch | [Enter] Keep changes: ",
        "retrying": "Retrying relink and post-move verification...",
        "undoing": "Undoing batch via `smart-drive undo --apply`...",
        "undo_done": "Undo completed: restored {restored} item(s) to original locations.",
        "undo_partial": "Undo only partly done: restored {restored} item(s), {failed} could not be moved back. Close the apps using them and run `smart-drive undo --apply` again.",
        "undo_not_this_run": "Nothing was undone: this run moved no folders, or the move journal does not match this run. Review the latest batch with `smart-drive undo`.",
        "retry_no_moves": "This run moved no folders, so there is nothing to retry.",
        "kept_done": "Changes kept. Safe organization complete!",
        "selftest_ok": "[SELFTEST OK] Read-only Smart_Organize self-test passed (lang={lang}, root={root}).",
    },
    "vi": {
        "banner_title": "SmartDrive-OS - Smart Organize (Dời thư mục an toàn)",
        "drive_root": "Ổ đĩa gốc:  {root}",
        "log_path": "Nhật ký:    {log_file} (được ghi khi bạn xác nhận dời)",
        "step1_title": "[1/5] Đang quét xem trước (Dry-Run Preview)...",
        "planned_header": "Các mục dự kiến dời ({count}):",
        "planned_item": "  + {src}  -->  {dst}  ({reason})",
        "no_planned_moves": "Mọi thư mục ở gốc ổ đĩa đã gọn gàng (không cần dời thêm mục nào).",
        "cannot_move_header": "Các mục KHÔNG dời được ({count}) kèm lý do:",
        "cannot_move_item": "  ! {name} — giữ nguyên tại chỗ ({reasons})",
        "cannot_move_none": "Không có mục nào bị chặn hoặc lỗi.",
        "step2_busy_title": "[2/5] Phát hiện ứng dụng / IDE đang mở: {apps}",
        "step2_busy_hint": "Vui lòng đóng các ứng dụng này để SmartDrive cập nhật đường dẫn dự án an toàn.",
        "step2_unknown_title": "[2/5] Không kiểm tra được agent / IDE nào đang mở: không đọc được danh sách chương trình đang chạy.",
        "step2_unknown_hint": "Vui lòng đóng các agent và IDE đang mở để SmartDrive cập nhật đường dẫn dự án an toàn.",
        "step2_busy_prompt": "Nhấn Enter để kiểm tra lại, [C] để tiếp tục, hoặc [Q] để hủy: ",
        "step2_clear": "[2/5] Không có ứng dụng xung đột nào đang mở.",
        "step3_confirm_prompt": "[3/5] Gõ YES để xác nhận dời {count} mục an toàn (hoặc nhập khác để hủy): ",
        "cancelled": "Đã hủy. Không có file hay thư mục nào bị thay đổi.",
        "step4_apply_title": "[4/5] Đang dời thư mục và cập nhật IDE/agent (organize --apply)...",
        "moved_summary": "Đã dời thành công {moved} mục.",
        "move_errors_header": "Các mục KHÔNG dời được do lỗi khi thực thi ({count}):",
        "move_error_item": "  x {error}",
        "step5_verify_title": "[5/5] Kiểm tra an toàn sau khi dời:",
        "verify_relink_ok": "  [OK]   Kiểm tra relink (relink --dry-run): không còn đường dẫn nào cần sửa.",
        "verify_relink_warn": "  [WARN] Kiểm tra relink (relink --dry-run): còn {count} đường dẫn chờ sửa ({apps}).",
        "verify_git_ok": "  [OK]   Kiểm tra Git / worktree: {checked} kho code hoạt động bình thường.",
        "verify_git_warn": "  [WARN] Kiểm tra Git / worktree: phát hiện {broken} vấn đề: {details}",
        "verify_venv_ok": "  [OK]   Kiểm tra môi trường ảo (.venv): {checked} môi trường hợp lệ.",
        "verify_venv_warn": "  [WARN] Kiểm tra môi trường ảo (.venv): còn {stale} đường dẫn cũ: {details}",
        "decision_prompt": "Chọn thao tác: [R] Thử lại kiểm tra/relink | [U] Hoàn tác (Undo) | [Enter] Giữ nguyên: ",
        "retrying": "Đang thử lại relink và kiểm tra sau khi dời...",
        "undoing": "Đang hoàn tác đợt dời qua `smart-drive undo --apply`...",
        "undo_done": "Đã hoàn tác: trả {restored} mục về vị trí ban đầu.",
        "undo_partial": "Mới hoàn tác được một phần: trả {restored} mục, còn {failed} mục chưa trả về được. Hãy đóng các ứng dụng đang dùng chúng rồi chạy lại `smart-drive undo --apply`.",
        "undo_not_this_run": "Chưa hoàn tác gì: lần chạy này không dời thư mục nào, hoặc nhật ký dời không khớp với lần chạy này. Xem đợt gần nhất bằng `smart-drive undo`.",
        "retry_no_moves": "Lần chạy này không dời thư mục nào nên không có gì để thử lại.",
        "kept_done": "Đã giữ nguyên thay đổi. Hoàn tất dời an toàn!",
        "selftest_ok": "[SELFTEST OK] Kiểm tra tự động Smart_Organize (chỉ đọc) thành công (lang={lang}, root={root}).",
    },
}


def detect_language(
    env: Optional[Mapping[str, str]] = None,
    system_locale: Optional[str] = None,
) -> str:
    """Resolves UI language: 'vi' when SMARTDRIVE_LANG=vi or system locale is vi-*, else 'en'."""
    env_map = env if env is not None else os.environ

    sd_lang = (env_map.get("SMARTDRIVE_LANG") or "").strip().lower()
    if sd_lang.startswith("vi"):
        return "vi"
    if sd_lang.startswith("en"):
        return "en"

    if system_locale is not None:
        if system_locale.strip().lower().startswith("vi"):
            return "vi"
        if system_locale.strip():
            return "en"

    for key in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        val = (env_map.get(key) or "").strip().lower()
        if val:
            if val.startswith("vi"):
                return "vi"
            return "en"

    # Check Windows UI language when running on Windows without explicit env vars
    if sys.platform == "win32" and env is None and system_locale is None:
        try:
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            # Primary language ID 0x2a (42) is Vietnamese (0x042a = vi-VN)
            if (lang_id & 0x3FF) == 0x2A:
                return "vi"
        except Exception:
            pass

    if env is None and system_locale is None:
        try:
            loc = locale.getlocale()[0] or ""
            if loc.lower().startswith("vi"):
                return "vi"
        except Exception:
            pass

    return "en"


def t(key: str, _lang: str = "en", **kwargs: Any) -> str:
    """Formats a localized message string for the given key and language."""
    table = I18N_STRINGS.get(_lang, I18N_STRINGS["en"])
    template = table.get(key) or I18N_STRINGS["en"].get(key, key)
    if kwargs:
        return template.format(**kwargs)
    return template


# ==============================================================================
# 2. LOGGING HELPER (<root>/.smart_drive/smart-organize.log)
# ==============================================================================

def get_log_path(root: str) -> str:
    """Returns `<root>/.smart_drive/smart-organize.log`."""
    return os.path.join(os.path.abspath(root), ".smart_drive", "smart-organize.log")


def _utc_timestamp() -> str:
    """The current UTC time as `YYYY-MM-DDTHH:MM:SSZ`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_line(timestamp: str, event: str, details: str = "") -> str:
    """One log line: `[YYYY-MM-DDTHH:MM:SSZ] EVENT | details` (without ` | details` when there are none)."""
    line = f"[{timestamp}] {event}"
    if details:
        line += f" | {details}"
    return line


def _write_log_lines(root: str, lines: List[str]) -> str:
    """Appends `lines` as UTF-8 to `<root>/.smart_drive/smart-organize.log`, creating `.smart_drive/` if needed.

    A log that cannot be written never stops a run.
    """
    log_file = get_log_path(root)
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("".join(line + "\n" for line in lines))
    except OSError:
        pass
    return log_file


def append_organize_log(root: str, event: str, details: str = "") -> str:
    """Appends a timestamped UTF-8 log entry to `<root>/.smart_drive/smart-organize.log` at once.

    The guided run itself goes through `OrganizeLog`, which waits for the confirmation before it writes.
    """
    return _write_log_lines(root, [_log_line(_utc_timestamp(), event, details)])


class OrganizeLog:
    """The log of one guided run. It writes nothing to the drive until `flush()`.

    The preview promises to change nothing, so what happens before the person confirms the moves is kept in memory,
    each line stamped with the time of its event. `flush()` writes those lines to the log file, in order, and from then
    on every event is appended as it happens. A run that ends before `flush()` (nothing to move, `Q` while apps are
    open, anything but `YES`, the self-test) leaves no log file and no `.smart_drive/` folder behind.
    """

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        self.path = get_log_path(self.root)
        self._waiting: Optional[List[str]] = []  # None once flushed

    def add(self, event: str, details: str = "") -> None:
        """Records an event: kept in memory until `flush()`, appended to the log file at once after it."""
        line = _log_line(_utc_timestamp(), event, details)
        if self._waiting is None:
            _write_log_lines(self.root, [line])
        else:
            self._waiting.append(line)

    def flush(self) -> None:
        """Writes the lines kept so far, in order, and switches to writing every later event at once."""
        waiting, self._waiting = self._waiting, None
        if waiting:
            _write_log_lines(self.root, waiting)


# ==============================================================================
# 3. POST-MOVE VERIFICATION (relink --dry-run, git/worktree, virtualenv)
# ==============================================================================

def _extract_relink_dict(rep: Any) -> Dict[str, Any]:
    """Returns a relink report as a dict; a report object adds `busy_apps_unknown` (relink could not read the process list)."""
    if rep is None:
        return {"busy_apps": [], "changed": []}
    if isinstance(rep, dict):
        return rep
    unreadable = bool(getattr(rep, "_unreadable_processes", False))
    if hasattr(rep, "to_dict"):
        data = rep.to_dict()
        data["busy_apps_unknown"] = unreadable
        return data
    return {
        "busy_apps": list(getattr(rep, "busy_apps", []) or []),
        "changed": list(getattr(rep, "changed", []) or []),
        "busy_apps_unknown": unreadable,
    }


def _format_protected_entries(
    raw_protected: Any,
    zoner: Optional[AutoZoner] = None,
) -> List[Tuple[str, str]]:
    """Normalizes `protected_by_system` (list of dicts or dict) into `[(name, reason_str), ...]`."""
    results: List[Tuple[str, str]] = []
    if isinstance(raw_protected, dict):
        for name, reasons in sorted(raw_protected.items()):
            if isinstance(reasons, (list, tuple)):
                r_str = ", ".join(str(r) for r in reasons)
            else:
                r_str = str(reasons)
            results.append((str(name), r_str))
    elif isinstance(raw_protected, list):
        for entry in raw_protected:
            if not isinstance(entry, dict):
                continue
            p = str(entry.get("path") or entry.get("name") or "")
            name = os.path.basename(p.rstrip("\\/")) or p
            if zoner is not None and hasattr(zoner, "describe_protected_by_system") and p:
                try:
                    desc = zoner.describe_protected_by_system(p)
                    if desc:
                        results.append((name, desc))
                        continue
                except Exception:
                    pass
            reasons_list = entry.get("reasons") or []
            parts: List[str] = []
            for r in reasons_list:
                if isinstance(r, dict):
                    parts.append(str(r.get("source") or r.get("kind") or ""))
                else:
                    parts.append(str(r))
            results.append((name, ", ".join(x for x in parts if x) or "system reference"))
    return results


def verify_post_organize(
    root: str,
    moved_pairs: List[Tuple[str, str]],
    relink_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Verifies relink completion, git/worktree integrity, and virtualenv paths after moves."""
    root_abs = os.path.abspath(root)
    run_relink = relink_fn or relink

    # 1. relink --dry-run check: ensure nothing remains unfixed
    pending_relinks = 0
    busy_apps: List[str] = []
    if moved_pairs:
        try:
            rel_raw = run_relink(moved_pairs, root=root_abs, dry_run=True)
            rel_rep = _extract_relink_dict(rel_raw)
            for item in rel_rep.get("changed", rel_rep.get("updated", [])):
                if isinstance(item, dict):
                    pending_relinks += int(item.get("count", item.get("changes", 1)) or 1)
                else:
                    pending_relinks += 1
            busy_apps = list(rel_rep.get("busy_apps") or [])
        except Exception:
            pass

    # 2. git / worktree check inside moved destinations
    git_checked = 0
    git_issues: List[str] = []
    for _old_src, new_dst in moved_pairs:
        dst_path = Path(new_dst)
        if not dst_path.exists():
            continue
        git_candidates: List[Path] = []
        if (dst_path / ".git").exists():
            git_candidates.append(dst_path / ".git")
        if dst_path.is_dir():
            try:
                for child in dst_path.iterdir():
                    if child.is_dir() and (child / ".git").exists():
                        git_candidates.append(child / ".git")
            except OSError:
                pass
        for gpath in git_candidates:
            git_checked += 1
            try:
                if gpath.is_dir():
                    if not (gpath / "HEAD").is_file():
                        git_issues.append(f"{gpath}: missing HEAD")
                elif gpath.is_file():
                    raw = gpath.read_text(encoding="utf-8", errors="ignore").strip()
                    if raw.lower().startswith("gitdir:"):
                        target_gitdir = raw.split(":", 1)[1].strip()
                        resolved_gitdir = (gpath.parent / target_gitdir).resolve()
                        if not resolved_gitdir.exists():
                            git_issues.append(f"{gpath}: broken worktree gitdir -> {target_gitdir}")
            except OSError as exc:
                git_issues.append(f"{gpath}: {exc}")

    # 3. venv (.venv / venv) check inside moved destinations
    venv_checked = 0
    venv_stale: List[str] = []
    for old_src, new_dst in moved_pairs:
        dst_path = Path(new_dst)
        if not dst_path.is_dir():
            continue
        old_norm = os.path.normcase(os.path.abspath(old_src))
        venv_dirs: List[Path] = []
        for vname in (".venv", "venv", "env"):
            cand = dst_path / vname
            if (cand / "pyvenv.cfg").is_file():
                venv_dirs.append(cand)
        for vdir in venv_dirs:
            venv_checked += 1
            for sub in ("Scripts", "bin"):
                act = vdir / sub / "activate"
                if act.is_file():
                    try:
                        text = act.read_text(encoding="utf-8", errors="ignore")
                        if old_norm in os.path.normcase(text):
                            venv_stale.append(str(act))
                    except OSError:
                        pass

    return {
        "relink_ok": pending_relinks == 0 and len(busy_apps) == 0,
        "relink_pending_count": pending_relinks,
        "busy_apps": busy_apps,
        "git_ok": len(git_issues) == 0,
        "git_checked": git_checked,
        "git_issues": git_issues,
        "venv_ok": len(venv_stale) == 0,
        "venv_checked": venv_checked,
        "venv_stale": venv_stale,
        "root": root_abs,
    }


# ==============================================================================
# 4. MAIN GUIDED RUNNER
# ==============================================================================

def run_smart_organize(
    root: str,
    *,
    lang: Optional[str] = None,
    selftest: bool = False,
    input_fn: Callable[[str], str] = input,
    output_stream: Any = None,
    relink_fn: Optional[Callable[..., Any]] = None,
    after_moves_fn: Optional[Callable[..., Any]] = None,
) -> int:
    """Executes the guided Smart_Organize workflow."""
    out = output_stream if output_stream is not None else sys.stdout
    resolved_lang = lang or detect_language()
    root_abs = os.path.abspath(root)
    log = OrganizeLog(root_abs)  # writes nothing to the drive until the moves are confirmed (see below)
    run_relink = relink_fn or relink

    def emit(line: str = "") -> None:
        out.write(line + "\n")
        out.flush()

    log.add("START", f"lang={resolved_lang} selftest={selftest} root={root_abs}")

    emit("=" * 64)
    emit("  " + t("banner_title", resolved_lang))
    emit("=" * 64)
    emit("  " + t("drive_root", resolved_lang, root=root_abs))
    emit("  " + t("log_path", resolved_lang, log_file=log.path))
    emit("")

    # Step 1: Dry-run preview
    emit(t("step1_title", resolved_lang))
    zoner = AutoZoner(root_abs)
    actions = zoner.generate_plan()
    protected_entries = _format_protected_entries(getattr(zoner, "protected_by_system", []), zoner)
    planned_pairs = [(a.src_path, a.dest_path) for a in actions]

    relink_preview: Dict[str, Any] = {"busy_apps": [], "changed": []}
    if planned_pairs:
        try:
            if after_moves_fn is not None:
                raw_prev = after_moves_fn(root_abs, planned_pairs, "organize", dry_run=True)
            else:
                raw_prev = run_relink(planned_pairs, root=root_abs, dry_run=True)
            relink_preview = _extract_relink_dict(raw_prev)
        except Exception:
            pass

    log.add(
        "DRY_RUN",
        f"planned={len(actions)} protected_by_system={len(protected_entries)} busy_apps={relink_preview.get('busy_apps', [])}",
    )

    if actions:
        emit(t("planned_header", resolved_lang, count=len(actions)))
        for act in actions:
            src_rel = os.path.relpath(act.src_path, root_abs)
            dst_rel = os.path.relpath(act.dest_path, root_abs)
            emit(t("planned_item", resolved_lang, src=src_rel, dst=dst_rel, reason=act.reason))
    else:
        emit(t("no_planned_moves", resolved_lang))
    emit("")

    # Always show items that CANNOT be moved and their reasons
    if protected_entries:
        emit(t("cannot_move_header", resolved_lang, count=len(protected_entries)))
        for item_name, reason_str in protected_entries:
            emit(t("cannot_move_item", resolved_lang, name=item_name, reasons=reason_str))
            log.add("CANNOT_MOVE_PROTECTED", f"{item_name}: {reason_str}")
    else:
        emit(t("cannot_move_none", resolved_lang))
    emit("")

    # SD_SELFTEST=1 mode: verify dry-run + checks without modifying anything (the log is never flushed)
    if selftest:
        ver = verify_post_organize(root_abs, [], relink_fn=relink_fn)
        if ver["relink_ok"]:
            emit(t("verify_relink_ok", resolved_lang))
        emit(t("verify_git_ok", resolved_lang, checked=ver["git_checked"]))
        emit(t("verify_venv_ok", resolved_lang, checked=ver["venv_checked"]))
        emit(t("selftest_ok", resolved_lang, lang=resolved_lang, root=root_abs))
        log.add("SELFTEST_OK", f"lang={resolved_lang}")
        return 0

    if not actions:
        log.add("END", "nothing_to_move")
        return 0

    # Step 2: Check and wait for busy_apps to close (or be closed, when the list of running programs cannot be read)
    busy_apps = list(relink_preview.get("busy_apps") or [])
    busy_unknown = bool(relink_preview.get("busy_apps_unknown"))
    while busy_apps or busy_unknown:
        if busy_apps:
            apps_str = ", ".join(busy_apps)
            emit(t("step2_busy_title", resolved_lang, apps=apps_str))
            emit(t("step2_busy_hint", resolved_lang))
        else:
            apps_str = "unknown"
            emit(t("step2_unknown_title", resolved_lang))
            emit(t("step2_unknown_hint", resolved_lang))
        log.add("BUSY_APPS", apps_str)
        try:
            ans = input_fn(t("step2_busy_prompt", resolved_lang)).strip().lower()
        except EOFError:
            ans = "q"
        if ans == "q":
            emit(t("cancelled", resolved_lang))
            log.add("CANCELLED_AT_BUSY_APPS", apps_str)
            return 0
        if ans == "c":
            break
        try:
            if after_moves_fn is not None:
                raw_prev = after_moves_fn(root_abs, planned_pairs, "organize", dry_run=True)
            else:
                raw_prev = run_relink(planned_pairs, root=root_abs, dry_run=True)
            relink_preview = _extract_relink_dict(raw_prev)
            busy_apps = list(relink_preview.get("busy_apps") or [])
            busy_unknown = bool(relink_preview.get("busy_apps_unknown"))
        except Exception:
            busy_apps = []
            busy_unknown = False

    if not busy_apps and not busy_unknown:
        emit(t("step2_clear", resolved_lang))
    emit("")

    # Step 3: Prompt to type YES
    try:
        confirm = input_fn(t("step3_confirm_prompt", resolved_lang, count=len(actions))).strip()
    except EOFError:
        confirm = ""

    if confirm.upper() != "YES":
        emit(t("cancelled", resolved_lang))
        log.add("CANCELLED_CONFIRM", f"input={confirm!r}")
        return 0

    # The drive changes from here on, so the log starts here: what the preview recorded goes in first, in order
    log.flush()

    # Step 4: Execute organize --apply --json
    emit("")
    emit(t("step4_apply_title", resolved_lang))
    from smart_drive.cli.cmd_organize import cmd_organize

    buf = io.StringIO()
    org_args = argparse.Namespace(
        root=root_abs,
        dry_run=False,
        apply=True,
        clean=False,
        json=True,
        only_moves=planned_pairs,
    )
    with redirect_stdout(buf):
        cmd_organize(org_args)

    raw_json = buf.getvalue().strip()
    try:
        apply_payload = json.loads(raw_json) if raw_json else {}
    except json.JSONDecodeError:
        apply_payload = {}

    zoning_res = apply_payload.get("zoning") or {}
    moved_count = int(zoning_res.get("moved_count", 0) or 0)
    errors = list(zoning_res.get("errors") or [])
    protected_post = _format_protected_entries(
        apply_payload.get("protected_by_system") or getattr(zoner, "protected_by_system", []),
        zoner,
    )
    applied_moves = list(zoning_res.get("moved") or [])
    # Exactly what this run moved (and journaled); retry and undo act on these only.
    applied_pairs = [
        (str(m.get("src")), str(m.get("dest")))
        for m in applied_moves
        if isinstance(m, dict) and m.get("src") and m.get("dest")
    ]
    moved_pairs = list(applied_pairs)
    if not moved_pairs:
        moved_pairs = [
            (src, dst) for (src, dst) in planned_pairs if os.path.exists(dst)
        ]

    emit(t("moved_summary", resolved_lang, moved=moved_count))
    log.add("APPLY", f"moved={moved_count} errors={len(errors)} protected={len(protected_post)}")

    # Clearly display items that could NOT be moved with their reasons
    if protected_post or errors:
        if protected_post:
            emit(t("cannot_move_header", resolved_lang, count=len(protected_post)))
            for item_name, reason_str in protected_post:
                emit(t("cannot_move_item", resolved_lang, name=item_name, reasons=reason_str))
        if errors:
            emit(t("move_errors_header", resolved_lang, count=len(errors)))
            for err in errors:
                if isinstance(err, dict):
                    err_str = f"{os.path.basename(str(err.get('src', '')))}: {err.get('error', '')}"
                else:
                    err_str = str(err)
                emit(t("move_error_item", resolved_lang, error=err_str))
                log.add("CANNOT_MOVE_ERROR", err_str)
    else:
        emit(t("cannot_move_none", resolved_lang))
    emit("")

    # Step 5 & 6: Verification + R (retry) / U (undo) / Enter (keep) loop
    while True:
        emit(t("step5_verify_title", resolved_lang))
        ver = verify_post_organize(root_abs, moved_pairs, relink_fn=relink_fn)
        if ver["relink_ok"]:
            emit(t("verify_relink_ok", resolved_lang))
        else:
            emit(
                t(
                    "verify_relink_warn",
                    resolved_lang,
                    count=ver["relink_pending_count"],
                    apps=", ".join(ver["busy_apps"]) or "locked",
                )
            )

        if ver["git_ok"]:
            emit(t("verify_git_ok", resolved_lang, checked=ver["git_checked"]))
        else:
            emit(
                t(
                    "verify_git_warn",
                    resolved_lang,
                    broken=len(ver["git_issues"]),
                    details="; ".join(ver["git_issues"][:3]),
                )
            )

        if ver["venv_ok"]:
            emit(t("verify_venv_ok", resolved_lang, checked=ver["venv_checked"]))
        else:
            emit(
                t(
                    "verify_venv_warn",
                    resolved_lang,
                    stale=len(ver["venv_stale"]),
                    details="; ".join(ver["venv_stale"][:3]),
                )
            )

        log.add("VERIFY", f"relink_ok={ver['relink_ok']} git_ok={ver['git_ok']} venv_ok={ver['venv_ok']}")
        emit("")

        try:
            choice = input_fn(t("decision_prompt", resolved_lang)).strip().upper()
        except EOFError:
            choice = ""

        if choice == "R":
            log.add("ACTION", "RETRY")
            if not applied_pairs:
                emit(t("retry_no_moves", resolved_lang))
                continue
            emit(t("retrying", resolved_lang))
            # The moves are already journaled by the apply step: only re-run the relink.
            try:
                run_relink(applied_pairs, root=root_abs, dry_run=False)
            except Exception as err:
                log.add("RETRY_ERROR", str(err))
            continue
        elif choice == "U":
            log.add("ACTION", "UNDO")
            # `smart-drive undo` reverts the latest journal batch: only run it when that batch is this run's.
            latest = last_undoable_moves(root_abs)
            if not applied_pairs or latest is None or set(latest.get("moves") or []) != set(applied_pairs):
                emit(t("undo_not_this_run", resolved_lang))
                log.add("UNDO_REFUSED", "latest undoable batch is not this run's")
                continue
            emit(t("undoing", resolved_lang))
            from smart_drive.cli.cmd_undo import cmd_undo

            undo_buf = io.StringIO()
            undo_args = argparse.Namespace(root=root_abs, apply=True, dry_run=False, json=True)
            with redirect_stdout(undo_buf):
                undo_rc = cmd_undo(undo_args)
            try:
                undo_rep = json.loads(undo_buf.getvalue().strip() or "{}")
            except json.JSONDecodeError:
                undo_rep = {}
            restored = int(undo_rep.get("restored_count", 0) or 0)
            failed = int(undo_rep.get("failed_count", 0) or 0)
            if undo_rc or failed:
                emit(t("undo_partial", resolved_lang, restored=restored, failed=max(failed, 1)))
                log.add("END", f"undo_partial restored={restored} failed={failed}")
                return 1
            emit(t("undo_done", resolved_lang, restored=restored))
            log.add("END", f"undone restored={restored}")
            return 0
        else:
            emit(t("kept_done", resolved_lang))
            log.add("ACTION", "KEEP")
            log.add("END", "kept")
            return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smart-organize",
        description="SmartDrive-OS Safe Guided Organizer (Smart_Organize)",
    )
    parser.add_argument("--root", help="Drive root directory")
    parser.add_argument("--lang", choices=["en", "vi"], default=None, help="Override display language")
    parser.add_argument("--selftest", action="store_true", help="Run read-only self-test (or set SD_SELFTEST=1)")
    args = parser.parse_args(argv)

    selftest = bool(args.selftest or os.environ.get("SD_SELFTEST") == "1")
    try:
        resolved = resolve_drive_root(args.root)
        root = resolved.path
    except DriveRootNotFound as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    return run_smart_organize(root, lang=args.lang, selftest=selftest)


if __name__ == "__main__":
    sys.exit(main())
