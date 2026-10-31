# Kế Hoạch Nâng Cấp SmartDrive-OS v1.5 — Quản Trị Vòng Đời & Phân Tầng Dự Án (`Project Sub-Zoning & Lifecycle`)

> **Tài liệu đặc tả kỹ thuật cho Giai đoạn D (v1.5)** — bổ sung cho [`docs/UPGRADE_PLAN.md`](UPGRADE_PLAN.md).  
> Mục tiêu: Giải quyết triệt để tình trạng quá tải thư mục phẳng (flat-folder overload) trong phân vùng `03_Development_Projects` khi người dùng có nhiều dự án nghiên cứu theo đợt (Waves), hạ tầng MCP server, dự án Hackathon cũ và dự án đang phát triển.

---

## 1. Bối cảnh & Nguyên nhân gốc

1. **Sự mất cân xứng giữa `02_Learning_Knowledge` và `03_Development_Projects`:**
   - Với `02_Learning_Knowledge`, `SmartDrive-OS` đã có [`AcademicClassifier`](../smart_drive/core/academic_classifier.py) tự động bóc tách học kỳ (`Hoc_Ky_1..9`, `SP26`, `FA25`), mã môn học (`PRN211`, `DBI202`...) và bài Lab để sắp vào cấu trúc cây thư mục gọn gàng.
   - Với `03_Development_Projects`, [`AutoZoner.classify_item`](../smart_drive/core/auto_zoner.py) (dòng 367–389) chỉ kiểm tra sự tồn tại của `.git`, `package.json`, `pyproject.toml`... rồi đưa tất cả vào `03_Development_Projects/<tên_dự_án>` ở cùng một cấp phẳng.
2. **Hệ quả thực tế trên ổ SSD di động (`D:\03_Development_Projects`):**
   - Khi thêm một chuỗi 6 dự án nghiên cứu mới (`reflex_bench`, `plasmid_relay`, `synapse_rocq`, `myelin_mastery`, `tcell_sieve`, `codon_lint`) bên cạnh các dự án sẵn có (`BioNexus`, `ai_agents`, `Yu_Nian_an`, `orchestrate-october26`, `hackerrank-orchestrate`, `Gamota_NextGenAI_Pitching_BAN_CUOI`...), thư mục `03_Development_Projects` phình ra hơn một chục thư mục ngang hàng.
   - Người làm độc lập (solo developer) không nhìn ra ngay đâu là **mũi nhọn đang làm tuần này (WIP ≤ 2)**, đâu là **dự án ấp ủ cho đợt sau (Wave 2–3)**, và đâu là **dự án đã kết thúc cần cất đi**.
   - Nếu tự tay tạo thư mục con và kéo thả dự án vào đó, các đường dẫn tuyệt đối được lưu trong IDE (VS Code `state.vscdb`/`storage.json`, JetBrains `recentProjects.xml`), coding agent (Antigravity, Claude Code `~/.claude.json`, `mcp_config.json`), môi trường ảo Python (`.venv`) và git worktree sẽ bị gãy.

---

## 2. Kiến trúc Phân tầng Chuẩn trong `03_Development_Projects`

Khi tính năng **Project Sub-Zoning** được bật, các dự án bên trong `03_Development_Projects` được tổ chức theo **5 phân khu vòng đời (Lifecycle Zones)**:

```text
03_Development_Projects/
├── 01_Active_Focus/        # [WIP ≤ 3] Dự án mũi nhọn đang code hàng ngày (Wave 1 Primary/Backup, BioNexus)
├── 02_Wave_Incubator/      # Dự án đã dựng khung/thiết kế, chờ mở khóa theo đợt (Wave 2, Wave 3)
├── 03_MCP_Agents/          # Hạ tầng MCP servers & Agent chạy nền (ai_agents: polaris, orion, vega...)
├── 04_Hackathons_Pitch/    # Dự án thi đấu ngắn hạn, bài test, pitching (hackerrank, Gamota, orchestrate...)
├── 99_Archive_Dormant/     # Dự án đã hoàn thành hoặc ngủ đông > 60 ngày không chỉnh sửa
├── ROADMAP_KANBAN.md       # Bảng điều khiển tiến độ & Cổng quyết định (Decision Gates)
└── *.code-workspace        # Các file VS Code / Antigravity Workspace theo từng đợt (Wave 1, Wave 2...)
```

---

## 3. Đặc tả Chi tiết Các Hạng mục Kỹ thuật (D1 – D4)

### D1. Module `smart_drive/core/project_classifier.py`

1. **Thứ tự ưu tiên phân loại (`classify_project(project_path: str) -> ProjectZoneResult`):**
   - **Ưu tiên 1 — Nhãn tường minh của người dùng (`.smart_drive_stage` hoặc `.project.json`):**
     - Nếu trong thư mục dự án có tệp `.smart_drive_stage` (chứa một dòng: `active`, `incubator`, `mcp`, `hackathon`, hoặc `archive`) hoặc tệp `.project.json` có khóa `"stage"` / `"wave"`, luôn phân vào đúng phân khu đó (`reason="Explicit project stage marker"`).
   - **Ưu tiên 2 — Bảo vệ và nhận diện Hạ tầng MCP (`03_MCP_Agents` & `protected_by_mcp`):**
     - Quét các tệp cấu hình MCP (`mcp_config.json`, `claude_desktop_config.json`, `.mcp.json`, `~/.claude.json`).
     - Nếu thư mục (hoặc thư mục con trực tiếp như `ai_agents/polaris_mcp`) được tham chiếu làm MCP server:
       - Đánh dấu `protected_by_mcp = True` (mặc định ghim tại chỗ khi chạy `organize` tổng quát để không làm gián đoạn MCP server đang chạy).
       - Phân loại mục tiêu là `03_MCP_Agents`.
   - **Ưu tiên 3 — Nhận diện Dự án Thi đấu / Ngắn hạn (`04_Hackathons_Pitch`):**
     - Tách token tên thư mục theo `_folder_name_tokens`: nếu khớp với tập từ khóa `{"hackathon", "hackerrank", "pitch", "pitching", "contest", "challenge", "rush", "orchestrate", "comp"}` ➔ `04_Hackathons_Pitch`.
   - **Ưu tiên 4 — Nhận diện Dự án Ấp ủ / Khung khởi tạo (`02_Wave_Incubator`):**
     - Nếu thư mục có cấu trúc dự án (`README.md`, `.gitignore`, `docs/`, `src/`, `tests/`) nhưng tổng số tệp mã nguồn thực thi (`.py`, `.ts`, `.js`, `.rs`, `.go`, `.java`, `.cpp`, `.v`) `<= 2` (chưa viết logic chính, mới tạo khung thiết kế) ➔ `02_Wave_Incubator`.
   - **Ưu tiên 5 — Phân loại theo Hoạt động Git / File mtime (`01_Active_Focus` vs `99_Archive_Dormant`):**
     - Lấy thời điểm hoạt động gần nhất `last_activity = max(git_head_commit_time, latest_source_file_mtime)`.
     - Nếu `age_days <= 14` (hoặc có `uncommitted changes` / `unpushed commits`) ➔ `01_Active_Focus`.
     - Nếu `age_days >= 60` và cây Git sạch (`changes == 0`) ➔ `99_Archive_Dormant`.
     - Trường hợp ở giữa (`14 < age_days < 60`): giữ nguyên vị trí hiện tại hoặc xếp vào `02_Wave_Incubator`.

2. **Bất biến an toàn (Safety Invariants):**
   - Không bao giờ coi bản thân các thư mục phân khu (`01_Active_Focus`, `02_Wave_Incubator`, `03_MCP_Agents`, `04_Hackathons_Pitch`, `99_Archive_Dormant`, `99_Archive`) là một dự án lẻ cần dời lồng vào nhau.
   - Mọi kiểm tra hệ thống (`protected_by_system`: biến môi trường `PATH`, Windows Services, Registry Uninstall, Shortcut `.lnk`) từ `SystemRefIndex` vẫn được áp dụng đầy đủ.

---

### D2. Lệnh CLI `smart-drive projects` và `smart-drive stage` (`smart_drive/cli/cmd_projects.py`)

1. **`smart-drive projects [--root <path>] [--json]` (Mặc định chỉ đọc — tuân thủ `test_previews_write_nothing.py`):**
   - In bảng tổng hợp tất cả dự án trong `03_Development_Projects`:
     - Cột: `Project`, `Current Zone`, `Suggested Zone`, `Last Active`, `Git Status` (`clean` / `M:3` / `↑1`), `Size (Slack)`, `Referenced By` (`Antigravity`, `VS Code`, `MCP`, `.lnk`).
     - Hiển thị cảnh báo nếu số dự án trong `01_Active_Focus` vượt ngưỡng WIP (`> 3`).

2. **`smart-drive projects --organize [--apply] [--wait] [--json]`:**
   - Mặc định (`--dry-run`): Liệt kê các dự án sẽ được dời vào 5 phân khu con trong `03_Development_Projects`, các dự án được giữ nguyên (`protected_by_mcp`, `protected_by_system`), và số lượng cấu hình IDE/agent sẽ được `relink`.
   - Khi có `--apply`:
     1. Kiểm tra tiến trình đang mở (`busy_apps`) và `in_use` handles bên dưới các dự án sắp dời.
     2. Dừng `git fsmonitor--daemon` trong các repo liên quan (`release_git_locks`).
     3. Dời từng dự án vào phân khu đích bằng `_move` (atomic rename trên cùng ổ đĩa).
     4. Ghi toàn bộ cặp `(src, dest)` vào `.smart_drive/moves.jsonl` để `smart-drive undo --apply` có thể đảo ngược toàn bộ đợt phân khu về vị trí cũ.
     5. Gọi `relink` để cập nhật mọi đường dẫn tuyệt đối trong Antigravity, Claude, Cursor, VS Code (`storage.json`, `state.vscdb`), JetBrains, Obsidian, `.venv`, git worktree và shortcut `.lnk`.
     6. Đồng bộ lại các file `.code-workspace` và cập nhật chỉ mục FTS5 (`IndexManager.incremental_update("03_Development_Projects")`).

3. **`smart-drive stage <project_name> <stage> [--apply] [--wait]`:**
   - Cho phép chuyển nhanh một dự án sang giai đoạn mới khi làm việc theo Đợt (Waves):
     - Ví dụ:
       ```bash
       smart-drive stage reflex_bench active --apply
       smart-drive stage tcell_sieve incubator --apply
       smart-drive stage hackerrank-orchestrate archive --apply
       ```
   - Tự động tìm dự án `<project_name>` trong `03_Development_Projects` (dù đang nằm phẳng hay nằm trong một phân khu con), ghi nhãn vào `<project>/.smart_drive_stage`, dời sang phân khu mới, chạy `relink` và cập nhật `.code-workspace`.

---

### D3. Đồng bộ `.code-workspace` & Kiểm tra WIP trong `checkup`

1. **Module `smart_drive/core/workspace_sync.py`:**
   - Khi một dự án trong `03_Development_Projects` được dời qua `projects --organize --apply` hoặc `stage --apply`, quét các tệp `*.code-workspace` và `ROADMAP_KANBAN.md` nằm trong `03_Development_Projects/`.
   - Viết lại các đường dẫn `folders[].path` trong `.code-workspace` (giữ nguyên định dạng JSON và tạo bản sao lưu `.bak.smart-drive` hoặc cập nhật theo vị trí mới) để người dùng nhấp đúp mở Workspace không bao giờ gặp thư mục trống.
2. **Tích hợp vào `smart-drive checkup` (`smart_drive/core/health.py`):**
   - Bổ sung kiểm tra nhẹ `unorganized_dev_projects` và `wip_overload`:
     - Nếu có `> 5` dự án nằm phẳng ngoài các phân khu con trong `03_Development_Projects`, gợi ý trong `next_actions`: `smart-drive projects --organize`.
     - Nếu có `> 3` dự án trong `01_Active_Focus`, gợi ý cất bớt dự án ít hoạt động nhất sang `02_Wave_Incubator` hoặc `99_Archive_Dormant`.

---

### D4. Tích hợp MCP (`ssd_auto_organize` & `ssd_status`)

- Trong `smart_drive/mcp/server.py`:
  - Thêm tham số tùy chọn `"subzone_projects": {"type": "boolean", "description": "If true, also classifies and organizes projects inside 03_Development_Projects into lifecycle zones (01_Active_Focus .. 99_Archive_Dormant) with full IDE/agent relinking.", "default": false}` vào `ssd_auto_organize`.
  - Giữ mặc định `False` để mọi test hiện hữu (`tests/test_mcp_grade_a.py`, `tests/test_previews_write_nothing.py`) giữ nguyên hành vi 100%.

---

## 4. Kế hoạch Kiểm thử (Verification Plan)

1. **`tests/test_project_classifier.py`**:
   - Kiểm tra phân loại đúng cả 5 phân khu (`01_Active_Focus`, `02_Wave_Incubator`, `03_MCP_Agents`, `04_Hackathons_Pitch`, `99_Archive_Dormant`).
   - Kiểm tra `.smart_drive_stage` ghi đè mọi heuristic.
   - Kiểm tra dự án đang đăng ký trong `mcp_config.json` giả lập được gắn cờ `protected_by_mcp=True` và không bị tự động dời.
2. **`tests/test_cmd_projects.py`**:
   - Kiểm tra `smart-drive projects` và `smart-drive projects --organize` (dry-run) **không ghi bất kỳ byte nào** lên ổ đĩa tạm.
   - Kiểm tra `smart-drive stage <proj> active --apply` dời thư mục, cập nhật đường dẫn trong `storage.json` giả lập + `.venv` giả lập + `Wave1_Focus.code-workspace`, và `smart-drive undo --apply` khôi phục cả thư mục lẫn đường dẫn về nguyên trạng ban đầu.
3. **Chạy toàn bộ bộ test hiện có**:
   - `py -m unittest discover tests` và `py -m smart_drive gitignore audit` đảm bảo không phát sinh bất kỳ hồi quy nào.
