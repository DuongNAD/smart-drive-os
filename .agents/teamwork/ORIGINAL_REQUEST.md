# Original User Request

## 2026-09-29T13:39:47Z

Comprehensive enhancement of SmartDrive-OS to achieve top-tier directory trust compliance (OpenAI, Claude, M8ven), elevate Trust Index score, and harden MCP server defenses while maintaining 100% Python Standard Library zero-dependency architecture.

Working directory: d:\teamwork_projects\smart_drive_os
Integrity mode: development

## Requirements

### R1. Directory & Marketplace Compliance (OpenAI, Claude, M8ven)
Implement all missing compliance criteria highlighted by the M8ven audit:
- Create a comprehensive `PRIVACY.md` detailing strict data isolation: local-only drive operations, zero telemetry, zero logging of personal data, and zero external network transmission. Reference this in `README.md` and `README_VN.md`.
- Enrich `pyproject.toml` with full marketplace metadata: official homepage, documentation URL, GitHub issue tracker, PyPI classifiers, and keyword tags for optimal domain consistency.

### R2. MCP Server Defensive Hardening & In-Memory Rate Limiting
Harden the Model Context Protocol engine in `smart_drive/mcp/server.py`:
- Implement a pure Python Standard Library in-memory sliding-window or token-bucket rate limiter to protect MCP endpoints against accidental flood or DoS attacks.
- Ensure all 8 MCP tools enforce strict boundary checks and input sanitization on paths and query parameters.
- Verify that all 8 tools maintain explicit boolean declarations for `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint`.

### R3. Comprehensive Test Verification & Zero-Dependency Invariant
- Expand the test suite (`tests/test_mcp_server.py` or new dedicated test module) to verify:
  1. Privacy policy structure and assertions.
  2. Rate limiting behavior (burst allowance, throttle trigger, window reset).
  3. Input sanitization boundaries on all 8 MCP tools.
- Maintain the hard project invariant: zero external runtime pip dependencies (100% Python Standard Library).

## Acceptance Criteria

### Directory Compliance
- [ ] `PRIVACY.md` exists at repository root, clearly articulating local-only storage, zero telemetry, and security guarantees.
- [ ] `README.md` and `README_VN.md` contain clickable links to `PRIVACY.md`.
- [ ] `pyproject.toml` contains `homepage`, `repository`, `documentation`, `issues`, `keywords`, and standard classifiers.

### Defensive Hardening
- [ ] In-memory rate limiting is integrated into `SmartDriveMCPServer` using only standard library modules (`time`, `collections`, `threading`).
- [ ] Rate limiting is configurable via parameters or environment variables, allowing normal operation without throttling standard agent workflows.
- [ ] All 8 MCP tools contain valid annotations and boundary protections.

### Automated Verification & Integrity
- [ ] All existing and new tests pass cleanly with `pytest` (100% pass rate).
- [ ] `pyproject.toml` runtime dependencies remain strictly empty (`dependencies = []`).
- [ ] No regression on SSD safety rules (512KB cluster slack protection, whitelist immutability, illegal character prevention).

## 2026-09-29T16:34:33Z

Nghiên cứu và nâng cấp toàn diện SmartDrive-OS nhằm giải quyết triệt để 5 cảnh báo bảo mật/chất lượng từ báo cáo kiểm định MCP (Grade B 89/100 nâng lên Grade A 95-100/100), tái cấu trúc cơ chế handler isolation, kiểm soát network endpoints, bổ sung xác thực và chuẩn hóa siêu dữ liệu đóng gói.

Working directory: d:/teamwork_projects/smart_drive_os
Integrity mode: development

## Requirements

### R1. Kiến trúc Handler Isolation & Ánh xạ Tool độc lập
Refactor cơ chế khai báo và thực thi của toàn bộ 8 công cụ MCP từ mô hình lower-level raw dispatch sang kiến trúc handler độc lập, tường minh có thể phân tích tĩnh (static AST analysis resolve được 100% tool handlers). Đảm bảo mỗi công cụ có mô tả chính xác (Tool description accuracy) khớp hoàn toàn với hành vi mã nguồn thực thi.

### R2. Cơ chế Xác thực (Authentication) & Kiểm soát Network Endpoints
Thiết lập cơ chế kiểm soát truy cập và xác thực (token/key handshake) hỗ trợ bảo vệ máy chủ MCP khi vận hành qua các transport công cộng/mạng mà vẫn đảm bảo tính tương thích trong suốt (zero-friction) đối với kết nối stdio cục bộ của các AI Agent (Antigravity, Claude, Cursor). Rà soát và khoanh vùng chặt chẽ toàn bộ network endpoints (chỉ cho phép binding cục bộ 127.0.0.1 loopback, không gọi ra ngoài ngoài ý muốn).

### R3. Chuẩn hóa Định danh & Siêu dữ liệu Đóng gói (Domain Consistency)
Cập nhật và hoàn thiện hồ sơ dự án (pyproject.toml, metadata, package descriptors, URLs, author verification) nhằm đảm bảo hệ thống quét tự động xác minh được tính nhất quán giữa tác giả, repository và phạm vi gói (Domain Consistency).

### R4. Tuân thủ Bất biến exFAT và Bảo toàn Bộ kiểm thử
Duy trì nghiêm ngặt các quy tắc an toàn đĩa exFAT (cluster slack 512KB, không sử dụng symlink, không chứa ký tự cấm trên Windows `\ / : * ? " < > |`, không recursive find/grep bừa bãi). Đảm bảo toàn bộ 523 bài test hiện có tiếp tục pass 100% cùng với các bài test mới kiểm chứng tính năng cách ly handler và xác thực.

## Acceptance Criteria

### Báo cáo & Đánh giá MCP
- [ ] Handler isolation coverage đạt 100% (cả 8 công cụ đều có handler độc lập được scanner resolve trực tiếp thay vì bị bỏ qua).
- [ ] Tool description accuracy được giải quyết triệt để (scanner đối soát được hành vi mã nguồn tương ứng với từng tool description).
- [ ] Network endpoints được kiểm soát và ghi nhận an toàn (cô lập 100% trên loopback 127.0.0.1, loại bỏ chuỗi format không an toàn).
- [ ] Authentication mechanism được hiện thực hóa và có test suite xác thực hợp lệ.
- [ ] Domain consistency được chuẩn hóa trong metadata dự án.

### Chất lượng mã nguồn & Hồi quy
- [ ] 100% bộ kiểm thử hiện tại (523 tests) tiếp tục chạy pass hoàn toàn mà không có bất kỳ regression nào.
- [ ] Bổ sung các bài kiểm thử tự động mới (unit & integration tests) bao phủ tính năng authentication, handler isolation, và endpoint security.
- [ ] Cả 8 công cụ MCP (`ssd_search`, `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index`, `ssd_check_safety`, `ssd_status`, `ssd_auto_organize`) hoạt động trơn tru trên môi trường thực tế.
## 2026-10-01T07:40:41Z

Tối ưu hóa Model Context Protocol (MCP) cho các AI coding agents (Antigravity 2.0, Claude, Codex, Cursor), sửa triệt để các lỗi xử lý đường dẫn đa nền tảng, tinh gọn mã nguồn chuẩn Zero-Dependency và cung cấp bộ launcher phân phối portable an toàn.

Working directory: /Users/duongnad/Documents/tool/smart-drive-os
Integrity mode: development

## Requirements

### R1. Tối ưu hóa MCP Server cho AI Coding Agents (Antigravity 2.0, Claude, Codex, Cursor)
- Tối ưu định dạng dữ liệu trả về của các công cụ MCP (`ssd_search`, `ssd_audit`, `ssd_status`, `ssd_clean`, v.v.) theo hướng tinh gọn, tiết kiệm token tối đa (token-efficient formatting) và chống tràn context window khi agent gọi tìm kiếm/kiểm toán ổ đĩa.
- Cải thiện System Descriptions & Tool Prompts trong MCP để AI coding agents hiểu chính xác khi nào nên ưu tiên dùng FTS5 index thay vì quét đĩa thủ công (`find`, `grep`).
- Nâng cấp `smart_drive/mcp/registrar.py` để tự động phát hiện và đăng ký 1-click vào cấu hình của các agent hàng đầu:
  - Google Antigravity 2.0 (`~/.gemini/antigravity/mcp_config.json` hoặc IDE settings)
  - Claude Desktop & Claude Code (`claude_desktop_config.json`)
  - OpenAI Codex / Cursor (`~/.cursor/mcp.json`)
  - Windsurf (`~/.codeium/windsurf/mcp_config.json`)
  - Local Workspace (`.mcp.json`)

### R2. Sửa triệt để các lỗi Xử lý Đường dẫn & Bảo mật Traversal trong MCP (100% Tests Pass)
- Xử lý dứt điểm các lỗi kiểm thử về an toàn đường dẫn trong `smart_drive/mcp/server.py` và `purge_engine.py`:
  - Chặn triệt để Path Traversal (`..\..\Windows\System32`, `C:\`, `/etc/passwd`) trên cả môi trường POSIX và Windows.
  - Xử lý an toàn đường dẫn UNC (`\\server\share`) và ký tự ổ đĩa chéo (`C:`, `Z:`).
  - Khắc phục lỗi kiểm tra ký tự cấm và phân tách thư mục trung gian.
- Đảm bảo toàn bộ 565 tests tích hợp đạt tỷ lệ đậu 100% trên cả macOS, Linux và Windows.

### R3. Tinh gọn Codebase & Duy trì Tuyệt đối Chuẩn Zero-Dependencies
- Rà soát toàn bộ dự án, loại bỏ code chết, tối ưu cấu trúc AST handler và bảo vệ tính toàn vẹn của giao thức JSON-RPC 2.0 stdio.
- Duy trì nguyên tắc cốt lõi: 100% sử dụng thư viện chuẩn Python 3.9+ (`sqlite3`, `json`, `hashlib`, `pathlib`, v.v.), không thêm bất kỳ thư viện ngoài (zero pip dependencies).

### R4. Phân phối Độc lập & Launcher 1-Click An toàn
- Cung cấp kịch bản chạy độc lập portable (`.bat`, `.ps1` cho Windows và `.command`, `.sh` cho macOS) có khả năng tự kiểm tra môi trường, phòng ngừa nhầm lẫn với repo profile cá nhân trên máy mới hoặc máy của người khác.

## Verification Resources
- Toàn bộ bộ test tích hợp và kiểm thử đối kháng trong thư mục `tests/` (565 tests).
- Lệnh kiểm thử tự động: `python3 -m unittest discover tests` hoặc `pytest`.

## Acceptance Criteria

### MCP Protocol & Agent Integration
- [ ] Các công cụ MCP (`ssd_search`, `ssd_status`, `ssd_clean`, v.v.) phản hồi với cấu trúc JSON-RPC súc tích, độ trễ <10ms, kèm metadata phân trang rõ ràng giúp coding agents tiết kiệm token.
- [ ] Lệnh đăng ký MCP (`python -m smart_drive mcp register`) hỗ trợ và ghi đúng định dạng cho Antigravity 2.0, Claude, Cursor/Codex và `.mcp.json`.

### Path Security & Test Suite
- [ ] 565/565 unit & adversarial tests pass 100% không còn lỗi (`failures=0, errors=0`).
- [ ] Các payload tấn công directory escape (`C:\`, `..\..`, UNC paths) bị chặn 100% bởi `_resolve_safe_path` và `ssd_check_safety`.

### Packaging & Compatibility
- [ ] Không có bất kỳ external pip package nào được đưa vào `pyproject.toml`.
- [ ] Launcher portable Windows chạy kiểm tra ổ đĩa độc lập mà không yêu cầu cấu hình Git hay thông tin xác thực tài khoản.

## 2026-10-01T10:09:26Z

Nâng cấp kiến trúc SmartDrive-OS lên chuẩn Workstation Hybrid: giải quyết triệt để 7 vấn đề thực tế trên máy Windows (ổ D: fixed NTFS), tự bảo vệ chính mình khỏi AutoZoner, loại trừ thư mục dịch vụ/game nhạy cảm, tích hợp AcademicClassifier nhận diện môn học đại học FPTU & sửa lỗi font mojibake, và duy trì 100% tests pass đa nền tảng.

Working directory: /Users/duongnad/Documents/tool/smart-drive-os
Integrity mode: development

## Requirements

### R1. Loại bỏ giả định cứng về phần cứng (Hardware & Filesystem Abstraction)
- Cập nhật các test suite (`tests/test_drive_detector.py`, `tests/test_adversarial_filesystem.py`) để không còn giả định cứng ổ D: luôn là Kingston XS2000 USB exFAT 512KB. Sử dụng `inspect_drive(drive_letter)` để kiểm tra kiểu phần cứng và định dạng thực tế (NTFS vs exFAT) trước khi assert.
- Bọc khối `try ... except` quanh `Path.home()` trong `smart_drive/mcp/registrar.py`, fallback về `os.environ.get("USERPROFILE")` hoặc `Path("C:/Users/Default")` / `Path("/tmp")` khi môi trường biến bị xóa trắng trên Python 3.13.
- Bổ sung `@unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows")` cho các test case gọi `os.symlink` trực tiếp trong `tests/test_mcp_adversarial_challenger1.py`.

### R2. Cơ chế tự bảo vệ tuyệt đối trong AutoZoner & Bảo vệ ứng dụng/dịch vụ Windows
- Ngăn chặn triệt để nguy cơ AutoZoner "tự di chuyển chính mình": tự động đưa đường dẫn gốc thực thi của SmartDrive-OS (`Path(__file__).resolve()`) vào danh sách bảo vệ bất biến.
- Mở rộng `PROTECTED_ROOT_DIRS` và `DEFAULT_EXCLUDE_DIRS` trong `smart_drive/core/config.py`:
  - Thư mục hệ thống: `WindowsApps`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`, `Program Files`, `$Recycle.Bin`, `System Volume Information`.
  - Thư mục game/ứng dụng: `SteamLibrary`, `Riot Games`, `LDPlayer`, `SQL2022`, `Downloads`, `32837`, `fo4`.
  - Thư mục chứa cơ sở dữ liệu và dịch vụ đang chạy: `DBI202_VuPT\MSSQL16.MSSQLSERVER` (Microsoft SQL Server 2022).
- Thêm cơ chế xử lý ngoại lệ an toàn trong `smart_drive/core/scanner.py` để không spam lỗi `Scanner error [PERMISSION_DENIED]` từ các thư mục độc quyền của hệ thống.

### R3. Profile `workstation-hybrid` & Bộ phân loại `AcademicClassifier` (Việt Nam Localization)
- Bổ sung profile `workstation-hybrid` trong `DriveInitializer` (`cmd_init.py`) và `AutoZoner` tối ưu riêng cho ổ cứng phụ gắn trong (Fixed Internal SSD NTFS) đang chứa Game, ứng dụng Windows và dữ liệu học tập/dự án.
- Xây dựng module `AcademicClassifier` trong `smart_drive/core/academic_classifier.py`:
  - Nhận diện mã môn học đại học qua Regex (`[A-Z]{2,4}\d{3}[a-z]?` như DBI202, WED201c, SWE202c, OSG, PRN211, PRJ301, LAB1_sp26, PE_WED201c,...).
  - Nhận diện từ khóa học thuật tiếng Việt: `hoc ky`, `ky`, `fptu`, `pe_`, `lab`, `bai tap`, `de thi`, `on luyen`.
  - Phân loại và gom cấu trúc thông minh theo Học kỳ & Môn học: `02_Learning_Knowledge/FPTU/Ky_X/<Mã_Môn>/`.
  - Tích hợp bộ giải mã / sửa lỗi font mojibake tên thư mục (ví dụ `h?c k? 3 fptu` ➔ `Hoc_Ky_3_FPTU`, `k 1 fptu` ➔ `Ky_1_FPTU`, `n luy?n pe dbi202` ➔ `On_Luyen_PE_DBI202`).
  - Gom tài liệu và bộ cài cá nhân: `dich truyen` ➔ `02_Learning_Knowledge/Personal_Books/`, `LENOVO` ➔ `05_Dev_Toolbox/OEM_Drivers/`, `SQL2022` ➔ `05_Dev_Toolbox/Installers/`.

### R4. Tiện ích Môi trường CLI & Kiểm định Chất lượng Toàn diện (100% Tests Pass)
- Thêm lệnh CLI `smart-drive self-path-check` để hướng dẫn người dùng kiểm tra và cấu hình Scripts vào User PATH trên Windows.
- Đảm bảo các launcher `.bat`, `.ps1` luôn ưu tiên thực thi bằng cú pháp `python -m smart_drive <lệnh>` để hoạt động độc lập không phụ thuộc biến PATH.
- Bổ sung bộ test mới cho `AcademicClassifier`, `workstation-hybrid`, và cơ chế tự bảo vệ của `AutoZoner`. Đảm bảo 100% toàn bộ bộ test (700+ tests) pass sạch sẽ trên cả macOS và Windows.

## Verification Resources
- Toàn bộ bộ test hiện tại trong `tests/` (697 tests).
- Lệnh kiểm thử tự động: `python3 -m unittest discover tests` hoặc `pytest`.

## Acceptance Criteria

### Tương Thích Filesystem & Nền Tảng
- [ ] Không còn bất kỳ test nào fail do giả định cứng ổ D: là USB exFAT Kingston khi chạy trên máy Windows có ổ D: NTFS.
- [ ] `Path.home()` trong `registrar.py` không crash trên Python 3.13 khi môi trường bị xóa trắng.
- [ ] Các test case symlink trên Windows không gặp lỗi `WinError 1314`.

### An Toàn & Bảo Vệ Tuyệt Đối
- [ ] AutoZoner tuyệt đối không bao giờ tạo action di chuyển chính thư mục `smart-drive-os` hoặc các thư mục hệ thống/game/SQL Server.
- [ ] Lệnh `smart-drive clean` không còn in tràn lỗi `[PERMISSION_DENIED]` từ `WindowsApps` hay log SQL Server.

### AcademicClassifier & Localization
- [ ] Toàn bộ các thư mục môn học FPTU (`DBI202_VuPT`, `wed201c`, `PE_WED201c_SP26`, `h?c k? 3 fptu`) được tự động gom gọn theo kỳ/môn và chuẩn hóa tên sạch font vào `02_Learning_Knowledge/FPTU/`.
- [ ] Profile `workstation-hybrid` khởi tạo cấu trúc phân vùng phù hợp cho ổ đĩa phụ hybrid.

### Zero-Dependency & Test Suite
- [ ] Duy trì tuyệt đối chuẩn Zero external pip dependencies (`dependencies = []`).
- [ ] Toàn bộ bộ test (700+ tests) pass 100% với 0 failures, 0 errors.

