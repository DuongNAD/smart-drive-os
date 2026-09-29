# SmartDrive-OS — Hướng Dẫn Sử Dụng (Tiếng Việt)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Zero Pip Dependencies](https://img.shields.io/badge/dependencies-0%20external%20pip-success.svg)](#)
[![Privacy: 100% Local](https://img.shields.io/badge/Privacy-100%25%20Local-success?style=flat-square&logo=shield)](PRIVACY.md)
[![exFAT 512KB Optimized](https://img.shields.io/badge/filesystem-exFAT%20512KB%20Guard-orange.svg)](#)
[![Web Dashboard](https://img.shields.io/badge/UI-Embedded%20Dark%20SPA-blueviolet.svg)](#)
[![MCP Protocol](https://img.shields.io/badge/MCP-JSON--RPC%202.0%20stdio-purple.svg)](https://modelcontextprotocol.io/)
[![MCP Grade A](https://img.shields.io/badge/MCP%20Audit-Grade%20A%20(100%2F100)-brightgreen.svg)](#)
[![M8ven Score](https://m8ven.ai/badge/mcp/duongnad-smart-drive-os-1kxkwu)](https://m8ven.ai/mcp/duongnad-smart-drive-os)
[![Release: v1.1.0](https://img.shields.io/badge/release-v1.1.0-blue.svg)](https://github.com/DuongNAD/smart-drive-os/releases/tag/v1.1.0)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 100% Pass](https://img.shields.io/badge/tests-565%2F565%20passed%20(100%25)-brightgreen.svg)](#)
[![NTFS 4KB Native](https://img.shields.io/badge/filesystem-NTFS%204KB%20Native-blueviolet.svg)](#)
[![Branch: internal-secondary-drive](https://img.shields.io/badge/branch-internal--secondary--drive-purple.svg)](https://github.com/DuongNAD/smart-drive-os/tree/internal-secondary-drive)

> **Hệ điều phối & Quản trị ổ cứng SSD di động (exFAT), Giao diện Web Dashboard trực quan, Hệ thống Snapshot SHA-256 bảo vệ dữ liệu và Công cụ tìm kiếm tức thì SQLite FTS5 cho AI Coding Agents và lập trình viên.**
> 
> 🚀 **Kiến trúc ổ phụ gắn trong & Di chuyển Cache ổ C:**: Bạn muốn quy hoạch ổ SSD phụ trong máy trạm (`D:`, `E:`), di chuyển cache khổng lồ (HuggingFace, Ollama, Docker) bằng NTFS Directory Junctions (`mklink /J`) hoặc kiểm tra sức khỏe SSD TRIM? Xem ngay tài liệu chuyên sâu [README_INTERNAL.md](README_INTERNAL.md)!

---

## 1. Vấn đề cốt lõi: "Cạm bẫy" Cluster Slack 512KB trên exFAT

Các dòng ổ cứng SSD di động dung lượng lớn (như **Kingston XS2000 2TB**) khi định dạng theo chuẩn **exFAT** thường mặc định kích thước đơn vị phân bổ (cluster size) lên đến **512 KB (524.288 bytes)**.

$$\text{Cluster Allocation} = \left\lceil \frac{\text{File Size}}{524.288} \right\rceil \times 524.288 \text{ bytes}$$

| Kích thước danh nghĩa file | Dung lượng vật lý chiếm dụng | Cluster Slack lãng phí | Tỷ lệ lãng phí |
|---|---|---|---|
| **0 bytes** | 0 bytes (chỉ ghi metadata) | 0 bytes | 0.0% |
| **1 byte** | **524.288 bytes** (512 KB) | **524.287 bytes** | **99.9998%** |
| **10 KB** (code / config) | **524.288 bytes** (512 KB) | **514.048 bytes** | **98.05%** |
| **100 KB** (JSON / docs) | **524.288 bytes** (512 KB) | **421.888 bytes** | **80.47%** |
| **524.289 bytes** (512 KB + 1B) | **1.048.576 bytes** (1.024 KB) | **524.287 bytes** | **50.00%** |

### Tác động thực tế:
- Một dự án lập trình chứa **10.000 file nhỏ** (tổng dung lượng thực chỉ **15 MB**) khi sao chép sang SSD exFAT sẽ chiếm dụng hơn **5.12 GB** dung lượng vật lý — **lãng phí >99%** ổ đĩa!
- Trình lập chỉ mục của hệ điều hành (macOS Spotlight `mds`, Windows Search) liên tục quét và ghi đè các file rác nhỏ (`.DS_Store`, `Thumbs.db`, `.fseventsd`), gây nóng ổ, hao pin và giảm tuổi thọ chip nhớ SSD.
- Khi các AI coding agents (Antigravity, Claude, Cursor) khám phá các cây thư mục sâu, việc quét file đệ quy làm tràn cửa sổ ngữ cảnh (context window) và cạn kiệt token.

**SmartDrive-OS** giải quyết triệt để vấn đề này với triết lý **Zero-Dependency** (100% Python Standard Library: `http.server`, `hashlib`, `json`, `sqlite3`, `urllib`, `shutil`, `pathlib`), tự động cài đặt khiên chống quét rác, tích hợp công cụ tìm kiếm SQLite FTS5 siêu tốc (<10ms), giao diện Web trực quan nhúng sẵn và hệ thống bảo vệ toàn vẹn dữ liệu bằng Snapshot SHA-256.

---

## 2. Kiến trúc hệ thống

```
+-------------------------------------------------------------------------------------------------+
|                               Giao diện Người dùng & AI Agents                                  |
|   +-----------------------+    +-----------------------+    +---------------+    +----------+   |
|   |  Khởi chạy 1-chạm     |    |   Python CLI & TUI    |    |  AI Agents    |    |  Web UI  |   |
|   |  (.bat / .command)    |    |   (smart-drive CLI)   |    |  (MCP Clients)|    | (SPA App)|   |
|   +-----------+-----------+    +-----------+-----------+    +-------+-------+    +----+-----+   |
+---------------|----------------------------|------------------------|-----------------|---------+
                |                            |                        |                 |
                +----------------------------+                        |                 |
                                             |                        | JSON-RPC 2.0    | HTTP 8765
                                             v                        v                 v
+-------------------------------------------------------------------------------------------------+
|                                    Nhân điều hành SmartDrive-OS                                 |
|  +--------------------+  +--------------------+  +--------------------+  +-------------------+  |
|  | Drive Initializer  |  |  Storage Auditor   |  |   MCP Server       |  | Web UI Không Thư  |  |
|  | - Preset Profiles  |  |  - Hình học 512KB  |  |   - stdio RPC 2.0  |  |   Viện Ngoài      |  |
|  | - Cài khiên bảo vệ |  |  - Phân tích Slack |  |   - 8 Agent Tools  |  | - ThreadingHTTP   |  |
|  +--------------------+  +--------------------+  +--------------------+  +-------------------+  |
|  +--------------------+  +--------------------+  +--------------------+  +-------------------+  |
|  | Purge / Dọn Rác    |  | Tìm kiếm FTS5      |  | Snapshot & Backup  |  | Bộ phân loại sâu  |  |
|  | - Quy tắc 3 tầng   |  | - unicode61 BM25   |  | - Băm SHA-256 dòng |  | - Kiểm tra Magic  |  |
|  | - Khiên Whitelist  |  | - Phản hồi <10ms   |  | - Chống hỏng tệp   |  |   Bytes & Cấu trúc|  |
|  +--------------------+  +--------------------+  +--------------------+  +-------------------+  |
+-------------------------------------------------------------------------------------------------+
                                             |
                                             v
+-------------------------------------------------------------------------------------------------+
|                          Phân vùng Ổ cứng đích (Kingston XS2000 2TB exFAT)                      |
|   01_AI_Models/           02_Learning_Knowledge/        03_Development_Projects/                |
|   04_System_Workspaces/   05_Dev_Toolbox/               06_Archives_Storage/                    |
|   .metadata_never_index   .fseventsd/no_log             .smart_drive/index.db                   |
|   .smart_drive/snapshots/<tên>.json                     <đích>/backup_manifest.json             |
+-------------------------------------------------------------------------------------------------+
```

---

## 3. Các tính năng mới nổi bật trên v1.1.0

### 1. Giao diện Web Dashboard trực quan (`smart-drive ui`)
- **Zero-Dependency Web SPA**: Hoạt động hoàn toàn trên `http.server.ThreadingHTTPServer` của Python Standard Library, không cần cài Flask, FastAPI, Node.js hay npm.
- **Dark Mode hiện đại & Responsive**: Hiển thị biểu đồ phân bổ dung lượng 6 nhóm taxonomy và trực quan hóa chi tiết dung lượng cluster slack 512KB bị lãng phí.
- **Tìm kiếm FTS5 tương tác tức thì (<10ms)**: Tìm kiếm file thời gian thực kết hợp bộ lọc phân loại, phần mở rộng tệp và khoảng dung lượng.
- **Bảng điều khiển dọn rác 3 tầng an toàn**: Cho phép xem trước danh sách file rác (Dry-run preview) và thực hiện dọn dẹp an toàn bằng 1-chạm có hộp thoại xác nhận.
- **Cờ lệnh**: `smart-drive ui --port 8765 --no-browser --root <đường_dẫn> --db <đường_dẫn>`.

### 2. Hệ thống Snapshot & Backup bảo vệ toàn vẹn dữ liệu (`smart-drive snapshot` / `backup`)
- **Tạo Snapshot điểm phục hồi**: Ghi nhận trạng thái cho các phân vùng trọng yếu (`02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox`).
- **Mã băm SHA-256 dạng dòng (Streaming SHA-256)**: Chia file thành các khối 64KB băm liên tục với bộ nhớ hằng số $O(1)$, kiểm tra chính xác hiện tượng suy thoái dữ liệu (bit rot) hoặc sửa đổi trái phép.
- **Sao lưu tăng số (Incremental Backup)**: Tự động phát hiện và chỉ sao chép các tệp mới hoặc có thay đổi sang ổ cứng đích, tự động loại trừ rác hệ thống và lưu bản kê khai `backup_manifest.json`.
- **Các lệnh**:
  - `smart-drive snapshot create [tên]`: Ghi lại toàn bộ trạng thái file và checksum SHA-256.
  - `smart-drive snapshot list`: Liệt kê tất cả các bản snapshot đã lưu cùng số lượng tệp và dung lượng.
  - `smart-drive snapshot verify <tên>`: Kiểm toán tính toàn vẹn, phát hiện file bị sửa đổi, bị xóa hoặc file lạ chưa theo dõi.
  - `smart-drive backup --target <thư_mục_đích>`: Sao lưu tăng số thông minh (hỗ trợ cờ `--dry-run`, `--hash`, `--partitions`).

### 3. Bộ phân loại thông minh & Auto-Tagger (`smart-drive classify`)
- **Nhận diện sâu định dạng tệp qua Magic Bytes & Cấu trúc**:
  - **Mô hình AI & Trọng số**: GGUF (`GGUF` magic), Safetensors (đọc JSON header), ONNX (protobuf header), PyTorch (`.pt`/`.pth`), cấu hình HuggingFace (`config.json`, `tokenizer.json`).
  - **Bộ dữ liệu (Datasets)**: Apache Parquet (`PAR1`), Apache Arrow (`ARROW1`), JSONL, CSV, HDF5 (`\x89HDF\r\n\x1a\n`).
  - **Nghiên cứu & Tài liệu**: PDF (`%PDF-`), ePub (PK container chứa `application/epub+zip`), ghi chú Markdown.
  - **Kho mã nguồn**: Tự nhận diện dự án Git (`.git`), Node.js (`package.json`), Python (`pyproject.toml`, `setup.py`), Rust (`Cargo.toml`).
- **Tự động đề xuất & Gom file an toàn**: Đề xuất di chuyển file tự do vào đúng nhóm taxonomy với cơ chế đặt tên chống trùng lặp và ghi đè.
- **Lệnh**: `smart-drive classify [thư_mục] --suggest --dry-run --apply --json`.

### 4. Kiến trúc quy hoạch ổ cứng phụ gắn trong & Di chuyển Cache ổ C: (`smart-drive offload` / `health`)
- **Di chuyển Cache ổ C: an toàn tuyệt đối**: Quét và tự động phát hiện các cache dung lượng khổng lồ trên ổ Windows C: (HuggingFace, Ollama, PyTorch, Docker WSL2, pip, uv, npm, Conda, Gradle, Cargo), di chuyển sang ổ phụ (`D:\04_System_Offload_Caches\<tên>`) qua quy trình chuyển giao 7 giai đoạn có cơ chế rollback hoàn tác tức thì nếu gặp sự cố.
- **Động cơ liên kết NTFS Directory Junction (`mklink /J`)**: Tạo các điểm nối phần cứng trong suốt ở cấp hệ điều hành mà không cần cấp quyền Administrator hay kích hoạt Developer Mode, giải phóng hàng chục đến hàng trăm GB trên ổ C: mà phần mềm vẫn hoạt động bình thường.
- **Cấu hình máy trạm chuyên sâu (`internal-developer-vault`)**: Cung cấp cấu trúc 6 phân vùng chuyên sâu (`01_AI_Models`, `02_Development_Workspaces`, `03_Data_Vault`, `04_System_Offload_Caches`, `05_Dev_Toolbox`, `06_Archives_Storage`) được bảo vệ vĩnh viễn bởi whitelist.
- **Kiểm tra sức khỏe SSD TRIM & Cluster Geometry**: Kiểm tra trạng thái kích hoạt TRIM (`fsutil behavior query DisableDeleteNotify`), kích thước cluster phân bổ (4KB NTFS vs 512KB exFAT) và cảnh báo ngưỡng dung lượng trống.
- **Tài liệu hướng dẫn chuyên sâu**: Xem chi tiết tại [README_INTERNAL.md](README_INTERNAL.md).

### 5. Kiến trúc MCP Grade A & Phòng thủ Cấp Doanh Nghiệp (Enterprise Hardening)
- **Đạt Chuẩn Kiểm Định Bảo Mật Grade A (100/100)**: Vượt qua toàn diện 100% các tiêu chí kiểm tra bảo mật và chất lượng của MCP Registry / Inspector, nâng hạng ngoạn mục từ Grade B (89/100) lên Grade A (100/100) không còn bất kỳ cảnh báo nào.
- **Cách ly Handler Độc lập Phân tích Tĩnh (AST-Resolvable Handler Isolation)**: Thiết lập bảng ánh xạ `TOOL_HANDLERS` ở cấp class và chuỗi điều phối tĩnh `if-elif` trong `dispatch_tool()`, giúp các trình quét bảo mật phân tích tĩnh AST nhận diện trực tiếp 100% từng hàm xử lý mà không bị bỏ qua.
- **Cơ chế Xác thực Token Handshake Bằng Thư viện Chuẩn**: Tích hợp thuật toán bảo mật bất biến thời gian `hmac.compare_digest` qua phương thức JSON-RPC `auth/handshake`. Hỗ trợ tham số dòng lệnh `--auth-token`, `--require-auth` và biến môi trường `SMART_DRIVE_MCP_AUTH_TOKEN`, trong khi vẫn giữ nguyên cơ chế kết nối stdio mượt mà mặc định cho các local AI agent.
- **Khóa Chặt Network Endpoints Chỉ Trên Loopback & Bộ Lọc CORS**: Máy chủ web nhúng kiểm tra nghiêm ngặt địa chỉ socket với `ALLOWED_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")`, từ chối ngay lập tức các hành vi bind ra `0.0.0.0` hoặc IP mạng ngoài bằng `ValueError`, kết hợp bộ lọc nguồn gốc CORS loopback.
- **Đồng Bộ Hoàn Toàn Mô Tả Công Cụ & Schema Tham Số**: Chuẩn hóa chính xác 100% docstring, gợi ý boolean (`readOnlyHint`, `destructiveHint`, v.v.) và hành vi thực tế của cả 8 công cụ MCP (`ssd_search`, `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index`, `ssd_check_safety`, `ssd_status`, `ssd_auto_organize`).
- **Chuẩn Hóa Nhất Quán Tên Miền & Tác Giả**: Xác minh danh tính tác giả `DuongNAD`, bộ URL chính thức (Homepage, Repository, Docs, Issues, Privacy) và hệ thống phân loại Trove trên `pyproject.toml`.
- **Mở Rộng Bộ Kiểm Thử Lên 565 Tests (Pass 100%)**: Bổ sung bộ bài kiểm thử chuyên sâu `tests/test_mcp_grade_a.py` gồm 42 test case mới kiểm tra AST resolution, xác thực handshake và bảo mật endpoint.

---

## 4. Khởi động nhanh trong 3 bước

### Bước 1: Chuẩn bị môi trường
Dự án chỉ yêu cầu máy tính có sẵn **Python 3.9+** (sử dụng 100% Python Standard Library):
```bash
python -m smart_drive --help
```

Hoặc cài đặt chế độ phát triển:
```bash
pip install -e .
```

### Bước 2: Khởi tạo ổ đĩa 1-chạm
Bạn có thể nhấp đúp chuột vào tệp khởi chạy nhanh:
- **Windows**: Nhấp đúp vào `Setup_SSD.bat`
- **macOS / Linux**: Nhấp đúp vào `Setup_SSD.command`

Hoặc chạy lệnh từ terminal:
```bash
python -m smart_drive init --profile ai-developer
```

#### Các cấu hình mẫu có sẵn:
1. `ai-developer`: Phù hợp lập trình AI/LLM, chứa sẵn thư mục mô hình (`checkpoints`, `gguf`, `safetensors`), môi trường ảo có gắn shield `.noindex`, thư mục workspace cho AI agent.
2. `data-science`: Phù hợp phân tích dữ liệu, sổ tay Jupyter notebook, pipeline dữ liệu, hướng dẫn lưu trữ định dạng Parquet thay cho CSV nhỏ.
3. `general-workspace` (Mặc định): Cấu trúc tiêu chuẩn cho lập trình tổng quát, tài liệu học tập, sách và công cụ tiện ích.
4. `internal-developer-vault`: Cấu trúc kho lưu trữ máy trạm 6 phân vùng cho ổ SSD phụ gắn trong (`D:`, `E:`), chuyên tiếp nhận các thư mục cache từ C:, dataset và mô hình AI.

### Bước 3: Mở Web Dashboard hoặc kết nối AI Agent (MCP)
Khởi chạy giao diện Web trực quan:
```bash
smart-drive ui
```

Hoặc tự động cấu hình SmartDrive-OS làm MCP Server cho các trợ lý AI:
```bash
python -m smart_drive mcp-config
```
Hỗ trợ trực tiếp: **Google Antigravity 2.0**, **Claude Desktop / Claude Code**, **Cursor IDE**, và **Windsurf**.

---

## 5. Bộ khởi chạy 1-chạm (Cross-Platform Launchers)

Các tệp khởi chạy có sẵn ở cả thư mục gốc và thư mục `launchers/` để bạn sử dụng ngay:

| Tệp Windows (`.bat`) | Tệp macOS / Linux (`.command`) | Chức năng |
|---|---|---|
| `Setup_SSD.bat` | `Setup_SSD.command` | Khởi tạo ổ đĩa, tạo 6 nhóm thư mục và cài khiên bảo vệ. |
| `Quick_Search.bat` | `Quick_Search.command` | Tìm kiếm tệp tin tức thì bằng SQLite FTS5. |
| `Quick_Clean.bat` | `Quick_Clean.command` | Quét xem trước file rác (Dry-run) và dọn dẹp an toàn khi xác nhận. |
| `Quick_Audit.bat` | `Quick_Audit.command` | Thống kê cấu trúc dung lượng và đo lường lãng phí cluster slack. |

---

## 6. Bảng tra cứu các lệnh CLI

Chạy qua lệnh `smart-drive <lệnh>` hoặc `python -m smart_drive <lệnh>`:

| Lệnh | Tham số chính | Mô tả chi tiết |
|---|---|---|
| `ui` | `--port <cổng>`, `--no-browser`, `--root <đường_dẫn>`, `--db <đường_dẫn>` | Khởi chạy Web Dashboard giao diện Dark Mode trực quan cục bộ. |
| `snapshot create` | `[tên]`, `--partitions <danh_sách>`, `--root <đường_dẫn>`, `--json` | Tạo snapshot ghi lại trạng thái và băm SHA-256 dạng dòng cho các phân vùng. |
| `snapshot list` | `--root <đường_dẫn>`, `--json` | Liệt kê tất cả các bản snapshot kèm số tệp và dung lượng chi tiết. |
| `snapshot verify` | `<tên>`, `--no-untracked`, `--root <đường_dẫn>`, `--json` | Kiểm toán tính toàn vẹn dữ liệu so với bản snapshot để phát hiện file lỗi/sửa đổi. |
| `backup` | `--target <đích>`, `--dry-run`, `--no-skip-junk`, `--hash`, `--json` | Sao lưu tăng số an toàn sang ổ đĩa hoặc thư mục đích chỉ định. |
| `classify` | `[thư_mục]`, `--suggest`, `--dry-run`, `--apply`, `--json` | Nhận diện sâu magic bytes và phân loại thông minh tệp AI, dữ liệu, tài liệu, dự án. |
| `offload` | `--scan`, `--move <tên>`, `--target <ổ>`, `--revert <tên>`, `--dry-run`, `--json` | Quét và di chuyển cache khổng lồ từ ổ C: sang ổ phụ qua NTFS Directory Junctions. |
| `health` | `[ổ_đĩa]`, `--root <đường_dẫn>`, `--json` | Kiểm tra sức khỏe SSD, trạng thái kích hoạt TRIM, cluster geometry và dung lượng trống. |
| `init` | `--profile`, `--root`, `--force`, `--json` | Khởi tạo ổ đĩa, tạo cấu trúc phân loại, cài khiên và cơ sở dữ liệu FTS5. |
| `status` | `--root`, `--json` | Kiểm tra tình trạng điểm gắn ổ đĩa, khiên bảo vệ và các thư mục nghiệp vụ. |
| `audit` | `--root`, `--json`, `--markdown`, `--export` | Kiểm toán chi tiết dung lượng và tỷ lệ lãng phí cluster slack 512KB. |
| `clean` | `--dry-run`, `--apply`, `--tier {1,2,3}`, `--log`, `--json` | Dọn dẹp rác hệ thống (mặc định luôn chạy mô phỏng trước, bảo vệ whitelist). |
| `search` | `<từ_khóa>`, `--ext`, `--size`, `--category`, `--dir`, `--limit` | Tìm kiếm siêu tốc (<10ms) với bộ lọc đa tiêu chí và thuật toán BM25. |
| `organize` | `--dry-run`, `--apply`, `--clean`, `--json` | Tự động phân loại file tự do vào đúng thư mục nghiệp vụ để giảm slack. |
| `sentinel` | `--root`, `--auto-heal`, `--no-heal`, `--json` | Kiểm tra sức khỏe ổ cứng, phục hồi khiên bảo vệ bị thiếu (alias: `agent-check`). |
| `mcp` | `--root <đường_dẫn>`, `--auth-token <token>`, `--require-auth` | Khởi chạy máy chủ MCP Server stdio với tùy chọn cơ chế xác thực Token Handshake. |
| `mcp-config`| `--target-dir`, `--antigravity`, `--claude`, v.v. | Tự động cấu hình MCP vào file thiết lập của các IDE. |
| `dup` | `--root`, `--json` | Tìm file trùng lặp qua 3 giai đoạn (kích thước -> băm nhanh 8KB -> SHA-256). |
| `index` | `--root`, `--db`, `--batch <n>` | Lập chỉ mục toàn bộ ổ đĩa với SQLite FTS5 (>15.000 tệp/giây). |
| `update` | `--root`, `--db`, `--json` | Đồng bộ chỉ mục tìm kiếm tăng số siêu nhanh (<2 giây). |

---

## 7. Cú pháp tìm kiếm nâng cao

Công cụ tìm kiếm FTS5 hỗ trợ lọc linh hoạt:
- Tìm theo từ khóa: `smart-drive search "deep learning"`
- Lọc theo đuôi mở rộng: `smart-drive search "weights ext:gguf"`
- Lọc theo kích thước: `smart-drive search "dataset size:>50MB"` (hoặc `size:<500KB`)
- Lọc theo danh mục: `smart-drive search "transformer cat:ai_models"`
- Lọc theo thư mục con: `smart-drive search "setup dir:03_Development_Projects"`
- Truy vấn kết hợp: `smart-drive search "resnet ext:safetensors size:>50MB"`

---

## 8. Cơ chế dọn dẹp rác an toàn tuyệt đối (Safe Cleaner)

Hệ thống phân tầng rác làm 3 cấp độ:
- **Tier 1 (An toàn 100%)**: File rác hệ điều hành macOS `.DS_Store`, `._*` AppleDouble; Windows `Thumbs.db`, `desktop.ini`, `.Spotlight-V100`, `.Trashes`.
- **Tier 2 (Cache phát triển)**: Python `__pycache__`, `*.pyc`, `.pytest_cache`, Node cache, Rust build artifacts.
- **Tier 3 (File tạm thời)**: `*.log`, `*.tmp`, crash dumps.

> **Danh sách bảo vệ bất khả xâm phạm (Whitelist Guard)**: Các tệp chỉ thị gốc (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`), các script khởi chạy (`Setup_*.bat`, `Setup_*.command`), và 6 thư mục phân loại gốc **không bao giờ bị xóa** trong bất kỳ trường hợp nào.

---

## 9. Máy chủ Model Context Protocol (MCP) Server

SmartDrive-OS tích hợp sẵn máy chủ MCP stdio chuẩn JSON-RPC 2.0, cung cấp 8 công cụ chuyên dụng cho AI Agents:

1. `ssd_search`: Tìm kiếm FTS5 tức thì trả về token rút gọn (<2.000 tokens/lần truy vấn). *(chỉ đọc)*
2. `ssd_audit`: Thống kê dung lượng và phân tích lãng phí cluster slack. *(chỉ đọc)*
3. `ssd_clean`: Dọn rác an toàn có khiên bảo vệ whitelist và hỗ trợ dry-run. *(xóa rác)*
4. `ssd_find_duplicates`: Tìm file trùng lặp qua 3 giai đoạn SHA-256. *(chỉ đọc)*
5. `ssd_update_index`: Đồng bộ chỉ mục tìm kiếm tăng số (<2s). *(idempotent)*
6. `ssd_check_safety`: Kiểm tra tính tương thích exFAT (quét 9 ký tự cấm Win32, 22 từ khóa DOS, và phát hiện symlink). *(chỉ đọc)*
7. `ssd_status`: Kiểm tra trạng thái gắn kết SSD, khiên bảo vệ và phân vùng. *(chỉ đọc)*
8. `ssd_auto_organize`: Tự động phân luồng và gom file giảm thiểu lãng phí slack. *(sắp xếp/xóa rác)*

### Kiến trúc MCP Grade A & Phòng thủ Cấp Doanh nghiệp
- **Cách ly Handler Độc lập 100% Phân tích Tĩnh (AST)**: Toàn bộ 8 công cụ được ánh xạ qua `SmartDriveMCPServer.TOOL_HANDLERS` ở cấp class và định tuyến tĩnh `if-elif` trong `dispatch_tool()`, cho phép các công cụ bảo mật quét và kiểm tra độc lập 100% nội dung hàm.
- **Xác thực Token Handshake Thời gian Bất biến**: Xác minh token qua `hmac.compare_digest` với phương thức JSON-RPC `auth/handshake`. Hỗ trợ tham số `--auth-token`, `--require-auth` và biến môi trường `SMART_DRIVE_MCP_AUTH_TOKEN`, đồng thời giữ nguyên kết nối stdio mặc định thuận tiện cho các AI Agent trên máy tính.
- **Giới hạn Tần suất Trượt (Sliding-Window Rate Limiting)**: Tích hợp sẵn `SlidingWindowRateLimiter` đa luồng, chống tình trạng Agent gửi request ồ ạt gây nghẽn máy chủ, trả về mã lỗi JSON-RPC `-32000` và header `Retry-After` chính xác tới millisecond.
- **Rào chắn Lọc Đường dẫn Đầu vào**: Kiểm tra biên an toàn (`_resolve_safe_path`), ngăn chặn tấn công Path Traversal (`../`), Null-byte (`\0`) và chuyển đổi phân vùng ổ đĩa trái phép.
- **Gợi ý Hành vi & Khớp Schema Tuyệt Đối**: Khai báo đủ 4 thuộc tính boolean (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) và đồng bộ 100% với hành vi mã nguồn thực tế cho Claude Code, Antigravity, Cursor và OpenAI.

### Cấu hình tự động cho IDE
Chạy lệnh:
```bash
smart-drive mcp-config
```
Hệ thống sẽ tự nhận diện và cập nhật cấu hình cho Antigravity, Claude Desktop, Cursor và Windsurf.

---

## 10. Kiểm thử tự động & Độ tin cậy

Hệ thống được bảo vệ bởi bộ kiểm thử tự động toàn diện gồm **565 test case** (unit tests, integration tests, stress tests, adversarial tests) chạy hoàn toàn trên thư viện chuẩn `unittest`:

```bash
python -m unittest discover tests -v
```

Kết quả:
```text
Ran 565 tests in ~54s
OK (55 subtests passed)
```

Chạy riêng bộ kiểm thử tuân thủ MCP Grade A:
```bash
python -m unittest tests.test_mcp_grade_a -v
```

---

## 11. Bảo Mật & Quyền Riêng Tư Dữ Liệu (Privacy & Security)

SmartDrive-OS tuân thủ triệt để nguyên tắc **Local-First & Quyền riêng tư tối đa**:

- **Hoạt động 100% cục bộ (Local-Only)**: Mọi thao tác phân tích ổ đĩa, lập chỉ mục tìm kiếm SQLite FTS5 và xác thực snapshot SHA-256 đều thực thi trực tiếp trên máy và ổ cứng của bạn. Tuyệt đối không sao lưu hay đồng bộ dữ liệu lên đám mây.
- **Zero Telemetry**: Hoàn toàn không chứa mã thu thập dữ liệu hành vi, không gửi telemetry hay pingback về bất kỳ máy chủ nào.
- **Zero PII Logging**: Tuyệt đối không đọc, bóc tách hay thu thập nội dung tệp nhạy cảm (mã nguồn bí mật, khóa riêng tư, thông tin cá nhân); chỉ lưu trữ metadata cần thiết trong cơ sở dữ liệu nội bộ `.smart_drive/index.db`.
- **Sẵn sàng cho môi trường Air-Gap**: Không phụ thuộc vào bất kỳ thư viện bên ngoài nào (`dependencies = []`). Giao diện Web nhúng chỉ lắng nghe trên `127.0.0.1` (`localhost`) và máy chủ MCP giao tiếp thuần túy qua luồng `stdio`.
- **Bảo vệ Whitelist tuyệt đối**: Ngăn chặn hoàn toàn việc xóa nhầm các tệp cấu hình cốt lõi (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `PRIVACY.md`) và các phân vùng dữ liệu chuẩn.

Xem toàn văn cam kết bảo mật và quyền riêng tư tại tệp [PRIVACY.md](PRIVACY.md).

---

## 12. Đóng góp mã nguồn mở (Contributing)

SmartDrive-OS là dự án **hoàn toàn mở** và chào đón mọi sự đóng góp từ cộng đồng:
- 🌟 **Star & Fork** repository trên GitHub: [DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os)
- 🐛 Báo lỗi hoặc đề xuất ý tưởng mới tại [GitHub Issues](https://github.com/DuongNAD/smart-drive-os/issues)
- 🔀 Gửi code cải tiến qua [Pull Requests](https://github.com/DuongNAD/smart-drive-os/pulls)
- 📖 Xem hướng dẫn quy trình đóng góp tại [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 13. Giấy phép mã nguồn mở (License)

Dự án được phát hành tự do 100% theo **Giấy phép MIT** (xem chi tiết tại tệp [LICENSE](LICENSE)).  
Bạn và bất kỳ ai trong cộng đồng đều có toàn quyền **sử dụng, sao chép, chỉnh sửa, ghép nối, phân phối hoặc dùng cho mục đích thương mại** hoàn toàn miễn phí và không bị ràng buộc.
