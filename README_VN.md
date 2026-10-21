<p align="center"><img src="docs/banner.jpg" alt="SmartDrive-OS — Sao Thổ với các vành đai ngăn nắp giữa không gian vũ trụ" width="100%"></p>

# SmartDrive-OS — Hướng Dẫn Sử Dụng (Tiếng Việt)

**Dời và sắp xếp gọn thư mục trên ổ đĩa mà không làm gãy coding agent và IDE của bạn.**

[![PyPI](https://img.shields.io/pypi/v/smart-drive-os)](https://pypi.org/project/smart-drive-os/)
[![Phiên bản Python](https://img.shields.io/pypi/pyversions/smart-drive-os)](https://pypi.org/project/smart-drive-os/)
[![CI](https://github.com/DuongNAD/smart-drive-os/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/DuongNAD/smart-drive-os/actions/workflows/tests.yml)
[![Phiên bản: v1.4.2](https://img.shields.io/badge/phi%C3%AAn%20b%E1%BA%A3n-v1.4.2-blue.svg)](https://github.com/DuongNAD/smart-drive-os/releases/tag/v1.4.2)
[![Giấy phép: MIT](https://img.shields.io/badge/Gi%E1%BA%A5y%20ph%C3%A9p-MIT-yellow.svg)](LICENSE)
[![Privacy: 100% Cục bộ](https://img.shields.io/badge/Privacy-100%25%20C%E1%BB%A5c%20b%E1%BB%99-success?style=flat-square&logo=shield)](PRIVACY.md)
[![M8ven Score](https://m8ven.ai/badge/mcp/duongnad-smart-drive-os-1kxkwu)](https://m8ven.ai/mcp/duongnad-smart-drive-os)

SmartDrive-OS là công cụ dòng lệnh (CLI) kiêm máy chủ MCP dành cho những ai để dự án trên ổ đĩa dung lượng lớn và làm việc với coding agent. Khi dời một thư mục, nó cập nhật mọi nơi còn nhớ đường dẫn cũ: Claude Code, Claude Desktop, Codex, Cursor, Antigravity, Windsurf, danh sách dự án gần đây của các IDE họ VS Code và JetBrains, shortcut Windows, git worktree và venv Python (`.pth`, launcher của console script, script `activate`). Nó còn dọn rác an toàn, tìm tệp trùng và tìm kiếm trên ổ đĩa tức thì. Nó chỉ dùng thư viện chuẩn Python 3.9+ (không phụ thuộc gói ngoài) và mọi thứ chạy cục bộ.

> 🌐 **English**: [README.md](README.md) · 🚀 **Hướng dẫn Ổ phụ Gắn trong (NTFS) & Dời Cache Ổ C:**: [README_INTERNAL.md](README_INTERNAL.md)

**Đi nhanh tới:** [Vì sao](#vì-sao-cần-smartdrive-os) · [Khởi động nhanh](#khởi-động-nhanh) · [Làm được gì](#làm-được-gì) · [Dời thư mục](#dời-thư-mục-mà-không-làm-gãy-agent-và-ide) · [MCP](#dùng-với-coding-agent-mcp) · [Dọn Rác An Toàn](#dọn-rác-an-toàn) · [Dùng hằng ngày](#sử-dụng-hằng-ngày) · [Tính năng khác](#tính-năng-khác) · [Tra cứu CLI](#bảng-tra-cứu-lệnh-cli) · [Bảo mật](#bảo-mật--quyền-riêng-tư-dữ-liệu)

## Vì Sao Cần SmartDrive-OS

- **Tự dời thư mục dự án bằng tay sẽ làm gãy mọi thứ.** Coding agent, IDE, shortcut, git worktree và môi trường ảo Python đều nhớ thư mục bằng đường dẫn tuyệt đối, còn exFAT không để lại liên kết ở chỗ cũ. Sau khi dời tay, agent không còn tìm thấy dự án, session và máy chủ MCP của nó, danh sách dự án gần đây của IDE trỏ tới chỗ không còn tồn tại, và venv đã dời vẫn trỏ về thư mục cũ.
- **SmartDrive dời thư mục và cập nhật đường dẫn trong một bước.** `smart-drive organize` mặc định chỉ xem trước; `--apply` dời thư mục rồi viết lại mọi đường dẫn đã nhớ. Mỗi lần dời đều được ghi nhật ký vào `.smart_drive/moves.jsonl`, nên `smart-drive undo --apply` đưa đợt dời về chỗ cũ và trỏ mọi thứ về đường dẫn gốc.
- **Coding agent có thể tự dùng.** 9 công cụ MCP (`ssd_auto_organize`, `ssd_clean`, `ssd_search`, ...) chạy dưới cùng các quy tắc an toàn: mặc định xem trước, thư mục hệ thống và ứng dụng được ghim tại chỗ, whitelist không bao giờ bị xóa.

## Khởi Động Nhanh

### 1. Cài đặt

```bash
pipx install smart-drive-os   # cần Python 3.9+; cho bạn lệnh `smart-drive`
```
Không muốn cài gì? `uvx` chỉ cần [uv](https://docs.astral.sh/uv/), uv còn tự tải Python khi máy chưa có, và `pip` thông thường cũng dùng được:
```bash
uvx smart-drive-os <lệnh>        # chạy lệnh con bất kỳ, không cài gì
pip install smart-drive-os       # hoặc cài bằng pip
```

<details>
<summary>Từ mã nguồn</summary>

```bash
git clone https://github.com/DuongNAD/smart-drive-os.git
cd smart-drive-os
pip install -e .              # cài lệnh `smart-drive` từ bản clone
python -m smart_drive <lệnh>  # hoặc chạy trực tiếp trong dự án
py -m smart_drive <lệnh>      # Windows Python Launcher (khi `python` không có trong PATH)
smart-drive self-path-check   # kiểm tra PATH và in lệnh cấu hình 1-chạm
```
Cài ở chế độ editable (`pip install -e .`) cần pip 21.3 trở lên: nếu pip cũ hơn (Python 3.9 đi kèm Xcode trên macOS chỉ có pip 21.2), hãy chạy `python -m pip install -U pip` trước.

</details>

### 2. Kết nối coding agent

```bash
smart-drive mcp register --all   # Antigravity, Claude, Cursor, Codex, Windsurf, .mcp.json
```
Lệnh này đăng ký bản cài vừa rồi; với uvx thì `uvx smart-drive-os mcp register --all` đăng ký `uvx smart-drive-os mcp`. Muốn tự dán mục đăng ký, xem [Dùng Với Coding Agent (MCP)](#dùng-với-coding-agent-mcp).

### 3. Kiểm tra ổ đĩa

```bash
smart-drive checkup   # điểm sức khỏe 0–100 và 3 việc nên làm tiếp, dạng lệnh để sao chép
```
Lệnh không đụng tới tệp của bạn (chỉ lưu điểm lần gần nhất trong `.smart_drive/`). Các lệnh về ổ đĩa nhận `--root <đường dẫn>` (hoặc biến `SMART_DRIVE_ROOT`); nếu không có cả hai, SmartDrive dùng thư mục gốc SmartDrive nằm phía trên thư mục hiện tại, rồi đến ổ đang gắn vào máy. `smart-drive init` chuẩn bị một ổ mới (xem [cấu hình mẫu](#tính-năng-khác)).

### 4. Sắp xếp lại an toàn

```bash
smart-drive organize          # chỉ xem trước: thư mục nào dời đi đâu, thư mục nào ở lại, agent và IDE nào được cập nhật
smart-drive organize --apply  # đóng coding agent và IDE trước; dời thư mục rồi cập nhật agent, IDE và shortcut
smart-drive undo --apply      # đổi ý? dời đợt gần nhất về chỗ cũ và relink về đường dẫn gốc
```
`organize` đưa các thư mục rời ở gốc ổ đĩa vào 6 phân vùng chuẩn, phân luồng môn FPTU và giảm cluster slack. `Smart_Organize.bat` / `Smart_Organize.command` / `./Smart_Organize.sh` là bản dẫn dắt từng bước: xem trước dry-run, chờ ứng dụng bận đóng, xác nhận `YES` và hoàn tác 1 phím `U`. Nếu agent đang mở lúc chạy `organize --apply`, hãy thoát agent rồi chọn `[11]` trong menu `SmartDrive.*` hoặc chạy `smart-drive relink`.

## Làm Được Gì

| Việc cần làm | Lệnh | An toàn theo mặc định |
|---|---|---|
| Điểm sức khỏe và bước tiếp theo | `smart-drive checkup` | Không đụng tới tệp của bạn |
| Sắp xếp ổ đĩa và hoàn tác | `smart-drive organize` / `smart-drive undo` | Chỉ xem trước cho tới khi có `--apply`; mỗi đợt dời đều có nhật ký |
| Sửa đường dẫn sau khi tự dời thư mục | `smart-drive relink "CŨ" "MỚI"` / `smart-drive paths --broken` | `--dry-run` xem trước; tệp cấu hình được sao lưu `.bak.smart-drive` trước khi ghi; `paths --broken` chỉ liệt kê (`--fix` sửa các khớp chắc chắn) |
| Dọn rác | `smart-drive clean` | Chạy thử cho tới khi có `--apply`; chỉ rác Tier 3 vào khu cách ly có thể khôi phục |
| Tìm kiếm trên ổ đĩa tức thì | `smart-drive search "<truy_vấn>"` | Chỉ đọc |
| Tìm tệp trùng | `smart-drive dup` | Chỉ báo cáo: không xóa gì |
| Xem dung lượng đi đâu | `smart-drive audit --html` | Chỉ đọc; ghi một tệp báo cáo |
| Giữ `.gitignore` đúng chuẩn | `smart-drive gitignore audit` | Kiểm toán chỉ đọc; `gitignore apply` chỉ xem trước cho tới khi có `--yes` |
| Dời cache ổ `C:` sang ổ khác | `smart-drive offload --scan` | `--scan` chỉ liệt kê; từ chối khi ứng dụng dùng cache đang chạy, trừ khi có `--wait` (chờ ứng dụng đóng) hoặc `--force` |
| Snapshot và sao lưu | `smart-drive snapshot create <tên>` / `smart-drive backup --target <đích>` | Không bao giờ sửa tệp của bạn; `backup --dry-run` xem trước |

---

## Dời Thư Mục Mà Không Làm Gãy Agent Và IDE

Các coding agent, IDE và shortcut màn hình nhớ dự án và máy chủ MCP bằng đường dẫn tuyệt đối, còn exFAT không để lại liên kết ở chỗ cũ. Khi `organize` hoặc `classify` dời thư mục, SmartDrive ghim bảo vệ các thư mục đang được hệ thống dùng trước rồi tự viết lại các đường dẫn còn lại:

- **Bảo vệ Thư mục Hệ thống & Ứng dụng (`protected_by_system`)**: Trước khi lên kế hoạch dời, SmartDrive quét biến môi trường `PATH` (User & System), Windows Services (`ImagePath`), Ứng dụng đã cài (`Uninstall` registry `InstallLocation` / `DisplayIcon` / `UninstallString`), Startup (`Run`), shortcut `.lnk` (Desktop, Start Menu, Quick Launch, gốc ổ đĩa), thư mục chứa `unins000.exe` / `uninstall.exe` và thư viện game (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`). Mọi thư mục khớp đều được ghim tại chỗ kèm lý do cụ thể trong `protected_by_system`.
- **Giữ thư mục riêng của bạn tại chỗ**: `organize` chỉ dời những gì nằm trực tiếp ở gốc ổ đĩa, nên thư mục bạn để bên trong một trong 6 phân vùng (ví dụ `03_Development_Projects\my-app`) sẽ được giữ nguyên. Trên Windows, thư mục còn được `protected_by_system` bảo vệ khi có shortcut (`.lnk`) trên Desktop, trong Start Menu hoặc trên taskbar trỏ tới nó hay tới một thứ nằm bên trong nó: `organize`, `classify`, `clean` và `dup` đều không động tới, và `organize --dry-run` liệt kê nó là mục bị bỏ qua. Shortcut đặt ở gốc ổ đĩa cũng được tính, nhưng `classify --apply` sắp các tệp rời ở gốc vào phân vùng, kể cả tệp `.lnk`, và khi shortcut bị dời đi thì sự bảo vệ cũng mất.
- **Cấu hình Agent, IDE & Shortcut**: Claude Code (`~/.claude.json`), Claude desktop (cấu hình, session, worktree, kể cả bản Microsoft Store MSIX), Codex (`config.toml`), Gemini CLI, Antigravity, Cursor, Windsurf, VS Code, VSCodium, Positron (`storage.json` và SQLite `state.vscdb`), các IDE JetBrains (`recentProjects.xml` gồm cả macro `$USER_HOME$`), Obsidian (`obsidian.json`), shortcut `.lnk` trên Windows, manifest của Constellation và `.mcp.json` của ổ đĩa. Mỗi tệp giữ nguyên định dạng và sao lưu thành `<file>.bak.smart-drive`; tệp SQLite `state.vscdb` đang bị IDE mở sẽ được bỏ qua kèm cảnh báo (`busy_apps`).
- **Lịch sử, repository và venv**: lịch sử và memory của Claude Code chuyển sang thư mục dự án mới, git worktree được sửa, và venv Python đã dời dùng lại được: các mục `.pth` và `.egg-link`, đường dẫn trình thông dịch trong console script (launcher của pip và script có shebang) cùng đường dẫn venv trong script `activate` đều được viết lại.
- **Nhật ký hoàn tác (`undo`)**: mỗi đợt dời ghi vào `.smart_drive/moves.jsonl` (nhận diện bằng `time`) để `smart-drive undo --apply` dời đợt `organize`/`classify` gần nhất về vị trí gốc và relink lại toàn bộ agent/IDE/shortcut. Các lần dời do `relink` và `paths` ghi lại không hoàn tác được.

Cách chạy:
```bash
smart-drive organize --dry-run                           # xem trước thư mục dời, mục được bảo vệ và agent/IDE được cập nhật
smart-drive organize --apply                             # dời thư mục rồi cập nhật đường dẫn cho agent, IDE, Obsidian, shortcut
smart-drive undo                                         # xem trước hoàn tác đợt dời gần nhất
smart-drive undo --apply                                 # hoàn tác đợt dời gần nhất và relink về chỗ cũ
smart-drive relink                                       # áp dụng lại đợt dời gần nhất (sau khi tắt IDE/agent)
smart-drive relink "E:\Cu\du_an" "E:\Moi\du_an" --dry-run # thư mục tự dời tay: xem trước
smart-drive relink "E:\Cu\du_an" "E:\Moi\du_an"           # ...rồi áp dụng
smart-drive relink --find                                # liệt kê đường dẫn cũ của agent/IDE và nơi thư mục đã tới
smart-drive relink --find --apply --wait                 # cập nhật mục chắc chắn sau khi ứng dụng đang mở đóng
smart-drive relink --find --apply --ask --wait           # ...và hỏi y/N cho từng mục ? trước
smart-drive paths --broken                               # kiểm tra shortcut, mục IDE, PATH và cấu hình agent bị đứt
smart-drive paths --broken --fix                         # tự sửa các mục khớp chắc chắn qua relink
```
Mục `[8]` trong menu `SmartDrive` (hoặc mục `[10]` / `Smart_Organize.*` cho trình dời an toàn có phím `U` hoàn tác ngay) làm việc này trực quan, còn mục `[11]` áp dụng lại đợt dời `organize`/`classify` gần nhất: xem trước bằng `smart-drive relink --dry-run`, hỏi `[y/N]`, rồi chạy `smart-drive relink --wait`. Dùng `[11]` sau khi chạy `organize --apply` từ bên trong một coding agent: hãy thoát agent trước (agent còn chạy có thể ghi lại đường dẫn cũ), rồi mở `SmartDrive` và chọn `[11]`. Đóng các coding agent và IDE trước khi relink, hoặc thêm `--wait` để SmartDrive chờ chúng đóng rồi mới ghi tệp. Nếu lệnh trong `.venv` đã dời vẫn trỏ về chỗ cũ (báo cáo relink có cảnh báo), chạy `uv sync --reinstall` trong dự án đó.

<details>
<summary>Thư mục bị khóa: khi Windows không cho đổi tên thư mục (git fsmonitor)</summary>

Windows không cho đổi tên thư mục khi có tiến trình đang giữ handle bên trong, và repository có `core.fsmonitor=true` sẽ được git bật một `git fsmonitor--daemon` chạy nền làm đúng việc đó sau lần `git status` đầu tiên (`[WinError 5] Access is denied`). Mọi lệnh git của SmartDrive chạy với `-c core.fsmonitor=false` nên không bao giờ tự bật daemon, và mỗi lần dời sẽ tắt trước daemon của các repository nằm trong thư mục đó (`--dry-run` liệt kê chúng ở `fsmonitor_to_stop`, `--apply` báo lại ở `fsmonitor_stopped`; git tự bật lại daemon ở lần `git status` kế tiếp). Nếu việc dời vẫn bị từ chối, thông báo lỗi cho biết thứ có thể đang giữ thư mục: một chương trình, một terminal, một cửa sổ Explorer hay một daemon còn chạy, và trên Windows là các tiến trình đang mở file ngay trong thư mục. Windows còn không cho đổi tên thư mục khi có thứ gì *bên dưới* nó đang được mở, nên một bước dò chỉ đọc (tối đa 8 cấp, 20.000 mục, 5 giây; không đổi tên hay giữ mở thứ gì) sẽ thử mở độc quyền từng thư mục và file, rồi nêu những mục sâu nhất đang bị dùng (`in use: E:\project\...\Images`, và trường `in_use` trong kết quả `--json` của `organize`, `undo`, `classify` và công cụ MCP `ssd_auto_organize`). Khi một thư mục bên trong được ghim trong Quick Access của Explorer, gợi ý nói rõ điều đó (Explorer giữ handle trên mọi thư mục được ghim và mở lại chúng ở mỗi lần đăng nhập, nên chỉ khởi động lại Windows thì không giúp được): hãy bỏ ghim, rồi khởi động lại Windows Explorer từ Task Manager. Khi không gọi được tên tiến trình nào cho thứ đang bị dùng, gợi ý chỉ tới `explorer.exe` (cửa sổ Explorer đang mở hoặc Quick Access) và Resource Monitor chạy bằng quyền quản trị (`resmon` → CPU → Associated Handles).

</details>

<details>
<summary>relink --find: mục chắc chắn (✓) và mục chưa chắc (?)</summary>

`--find` chỉ đánh dấu ✓ khi nhật ký dời thư mục, hoặc những gì agent nhớ bên trong thư mục cũ, cho biết thư mục đã tới đâu. Thư mục chỉ trùng tên được đánh dấu `?` và `--apply` không bao giờ tự cập nhật nó, vì đó có thể là một dự án khác còn thư mục cũ đã bị xóa: hãy kiểm tra rồi chạy lệnh `smart-drive relink "CŨ" "MỚI"` được in ngay bên cạnh. Hoặc thêm `--ask` (mục `[8]` đã dùng sẵn): lệnh hỏi `Relink CŨ -> MỚI? [y/N]` cho từng thư mục `?` chỉ có một ứng viên, rồi cập nhật các thư mục bạn trả lời `y` cùng lúc với các mục ✓. Nhấn Enter nghĩa là không. Lệnh chỉ hỏi khi chạy trong terminal: với `--json` hoặc đầu vào qua pipe thì không hỏi gì.

</details>

## Dùng Với Coding Agent (MCP)

SmartDrive-OS cung cấp máy chủ MCP chuẩn JSON-RPC 2.0 qua `stdio` với **9 công cụ**:

1. `ssd_search`: Tìm kiếm FTS5 tức thì trả về token rút gọn (<2.000 tokens/truy vấn). *(chỉ đọc)*
2. `ssd_audit`: Thống kê dung lượng và phân tích lãng phí cluster slack theo cluster thật của ổ, có chế độ nén gọn. *(chỉ đọc)*
3. `ssd_clean`: Dọn rác an toàn có bảo vệ whitelist và hỗ trợ dry-run. *(xóa rác)*
4. `ssd_find_duplicates`: Tìm file trùng qua 3 giai đoạn SHA-256 có phân trang và giới hạn xem trước. *(chỉ đọc)*
5. `ssd_update_index`: Đồng bộ chỉ mục tìm kiếm gia tăng (<2s). *(idempotent)*
6. `ssd_check_safety`: Kiểm tra tương thích exFAT (9 ký tự cấm Win32, 22 từ khóa DOS và symlink). *(chỉ đọc)*
7. `ssd_status`: Kiểm tra trạng thái gắn kết SSD, khiên bảo vệ và phân vùng. *(chỉ đọc)*
8. `ssd_auto_organize`: Tự phân luồng và gom file giảm lãng phí slack kết hợp `AcademicClassifier`. *(sắp xếp/xóa rác)*
9. `ssd_gitignore`: Kiểm toán và áp dụng khối `.gitignore` theo stack, bảo vệ dữ liệu cá nhân, kiểm toán secret và untrack an toàn. *(idempotent)*

<details>
<summary>Chi tiết công cụ</summary>

- `ssd_audit`: slack tính theo cluster thật của ổ, như `smart-drive audit` (`cluster_size`; `cluster_size_source` là `detected`, hoặc `assumed` khi không đọc được và dùng cluster exFAT 512 KB).
- `ssd_clean`: `sub_dir` được xét như lúc quét cả ổ, kể cả các thư mục bên trên: nằm trong ứng dụng đã cài thì không quét gì và `scan_skipped` nêu thư mục cùng lý do; kết quả còn liệt kê mục đã giữ lại (`kept`) và các thư mục `protected_by_system`.
- `ssd_find_duplicates`: `min_size` loại tệp nhỏ trước khi băm, slack tính theo cluster thật của ổ (`cluster_size`; `cluster_size_source` là `detected`, hoặc `assumed` khi không đọc được); `sub_dir` nằm trong ứng dụng đã cài hoặc thư mục phụ thuộc thì không được quét (`scan_skipped`).

</details>

Đăng ký bằng `smart-drive mcp register --all` (hoặc `smart-drive mcp-config --all`). Cài bằng pipx hoặc pip thì mục đăng ký dùng chính Python đã cài gói, chạy bằng `uvx smart-drive-os` thì đăng ký `uvx smart-drive-os mcp`, còn khi chạy từ mã nguồn (chưa `pip install`), mỗi mục tự kèm `PYTHONPATH` trỏ về dự án. Mẫu trong `configs/`: `.mcp.json` (workspace), `mcp_config.json` (Antigravity), `claude_desktop_config.json` (Claude Desktop & Code), `cursor_mcp.json` (Cursor / Codex), `windsurf_mcp.json` (Windsurf).

Hoặc tự thêm mục đăng ký. Với Claude Desktop (`claude_desktop_config.json`), Cursor (`~/.cursor/mcp.json`), Antigravity (`~/.gemini/antigravity/mcp_config.json`), Windsurf (`~/.codeium/windsurf/mcp_config.json`) và `.mcp.json` của dự án:
```json
{
  "mcpServers": {
    "smart-drive": {
      "command": "uvx",
      "args": ["smart-drive-os", "mcp"],
      "env": { "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1" }
    }
  }
}
```
Claude Code:
```bash
claude mcp add --scope user smart-drive -- uvx smart-drive-os mcp
```
Codex (`~/.codex/config.toml`):
```toml
[mcp_servers.smart-drive]
command = "uvx"
args = ["smart-drive-os", "mcp"]
```
Cài bằng pipx thì dùng `"command": "smart-drive", "args": ["mcp"]`. Ứng dụng có giao diện đồ họa (ví dụ Claude Desktop trên macOS) có thể không thấy `~/.local/bin`: nếu báo không tìm thấy `uvx` hay `smart-drive`, hãy ghi đường dẫn đầy đủ vào `command` (xem bằng `which uvx` hoặc `where uvx`); `smart-drive mcp register` tự ghi đường dẫn đầy đủ. Muốn cố định ổ đĩa, thêm `"--root", "E:\\"` vào `args` hoặc đặt biến `SMART_DRIVE_ROOT`; nếu không, máy chủ tìm thư mục gốc SmartDrive từ thư mục mà agent khởi động nó trở lên, rồi đến các ổ đang gắn vào máy.

<details>
<summary>Tùy chọn: cài hệ sinh thái 7 máy chủ MCP (smart-drive setup)</summary>

Tải Constellation, Polaris, Aurora Slides, Vega, Orion, Sirius và Mira vào `~/.smart-drive/mcp` (ngoài ổ exFAT), đăng ký cùng SmartDrive vào các coding agent đã cài (Claude Code, Antigravity, Cursor, Codex) và kiểm tra sức khỏe. Lệnh liệt kê các máy chủ và hỏi bạn muốn cài những máy nào trước khi tải hay đăng ký bất cứ thứ gì, chạy lại an toàn:
```bash
python3 -m smart_drive setup  # macOS / Linux (Windows: python -m smart_drive setup hoặc py -m smart_drive setup)
```
Trả lời `a` (tất cả), `n` (không cài, mặc định) hoặc số/tên như `1,3` hay `polaris,vega`; Constellation (mặt phẳng điều khiển) luôn được cài kèm khi chưa có. Máy chủ đã tải về cũng được liệt kê, kèm tên các agent, nếu nó sắp được thêm vào cấu hình của một agent. `--yes` bỏ qua câu hỏi (`--only polaris,vega --yes` chỉ cài những máy đó). Coding agent không có terminal để trả lời: lần chạy của agent không cài gì, thoát với mã `4` kèm danh sách, rồi agent hỏi bạn ngay trong khung chat của nó trước khi chạy `setup --only <lựa chọn của bạn> --yes`.

Cần Node.js >= 22.4 và git. `--dry-run` xem trước; `--check --json` báo trạng thái (mã thoát `3` = cần cài đặt); `--no-clone` ngoại tuyến; `--agents claude,cursor` giới hạn agent. Máy chủ đã đăng ký được dùng lại, không clone lần hai (`mcp-agy` tách riêng: venv riêng, Python >= 3.10, API key).

</details>

<details>
<summary>Chứng chỉ M8ven MCP Directory (Hạng A 100/100) & Tối ưu Token</summary>

Được kiểm định trên [M8ven MCP Directory](https://m8ven.ai/mcp/duongnad-smart-drive-os) với **Điểm Tin Cậy Hạng A (100/100)** (mã: `duongnad-smart-drive-os-1kxkwu`):
- **AST Handler Isolation**: Ánh xạ tĩnh qua `SmartDriveMCPServer.TOOL_HANDLERS`, không dùng `eval()` hay reflection động.
- **JSON nén kèm phân trang**: Trường `limit`, `offset`, `total_groups`, `returned_group_count`, `has_more`, `next_offset` và giới hạn 10 tệp xem trước/nhóm trùng (`truncated_files_count`).
- **Xác thực HMAC & Giới hạn tần suất**: `hmac.compare_digest` qua `auth/handshake` (`--auth-token`, `--require-auth`, `SMART_DRIVE_MCP_AUTH_TOKEN`) và `SlidingWindowRateLimiter` đa luồng (`-32000` kèm `Retry-After`).
- **Cô lập đường dẫn, Loopback & Tool Hints**: `_resolve_safe_path` chặn `../`, `\0` và nhảy ổ đĩa; khóa `ALLOWED_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")`; khai báo `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` cho cả 9 công cụ.
- **Không kết nối ngầm**: 100% `stdio` cục bộ; mạng duy nhất là `git clone` tường minh khi chạy `smart-drive setup` (bỏ qua bằng `--no-clone`).

</details>

## Dọn Rác An Toàn

Tệp rác chia làm 3 tầng với mặc định chạy thử (`--dry-run`, cần `--apply` để cách ly hoặc xóa thật):
1. **Tier 1 (Rác Metadata Hệ Điều Hành & Bytecode)**: macOS `.DS_Store`, `._*` AppleDouble, `.Spotlight-V100`, `.Trashes`, `.TemporaryItems`; Windows `Thumbs.db`, `desktop.ini`, `ehthumbs.db`, `ehthumbs_vista.db`, `$RECYCLE.BIN`; Python `__pycache__`, `*.pyc`, `*.pyo`.
2. **Tier 2 (Cache Biên Dịch & Phát Triển)**: `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.tox`, `.nox`, `.coverage`, `htmlcov`, `*.egg-info`; thư mục `build`, `dist` và `.cache` nằm cạnh một tệp dự án; cache cũ `polaris/.cache/**`.
3. **Tier 3 (Tệp Tạm & Crash Dump)**: `*.tmp`, `*.temp`, `*.swp`, `*.swo`, `*~`, `*.dmp`, `core` và `core.<pid>` (core dump Unix; `core.py` hay `core.js` là mã nguồn, không bao giờ khớp), `hs_err_pid*.log`.

```bash
smart-drive clean                      # xem trước (mặc định Tier 1)
smart-drive clean --tier 2             # xem trước Tier 1-2: những gì --apply sẽ xóa vĩnh viễn
smart-drive clean --apply --tier 3     # xóa rác Tier 1-2, cách ly rác Tier 3
```

`clean` chỉ liệt kê thứ thật sự tái tạo được. Nó không đụng tới gói đã cài, thư mục mà Windows hay ứng dụng đã cài đang dùng, và mục git đang theo dõi:
- **Gói đã cài**: không bao giờ liệt kê mục Tier 2-3 nằm trong thư mục phụ thuộc (`node_modules`, `bower_components`, `site-packages`, `.venv`, `vendor`...). Đó là mã của gói đã cài, và `npm install` không phát hiện một thư mục `dist/` đã bị xóa.
- **Thư mục Windows hoặc ứng dụng đã cài đang dùng**: không bao giờ liệt kê mục nằm bên trong (cùng chỉ mục `protected_by_system` mà `organize` dùng: PATH, Services, Ứng dụng đã cài, shortcut, trình gỡ cài đặt).
- **Thư mục build và mục git đang theo dõi**: thư mục `build`, `dist`, `.cache` chỉ tính khi có tệp dự án (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `*.csproj`...) nằm cạnh, và mục Tier 2-3 mà git đang theo dõi (hoặc nằm trong repo mà git không trả lời được) được giữ lại.

Báo cáo cho biết bao nhiêu mục giống rác đã được giữ lại và vì sao (`kept` và `protected_by_system` trong `--json`).

`smart-drive clean --apply` xóa vĩnh viễn và ngay lập tức rác Tier 1-2 vì nó tái tạo được (metadata hệ điều hành, cache biên dịch), nên không bao giờ qua cách ly và không khôi phục được. Hãy xem trước bằng `smart-drive clean --tier 2` (`smart-drive checkup` cũng gợi ý đúng thứ tự này). Chỉ mục Tier 3 (`--tier 3`: tệp tạm, tệp swap, crash dump) được dời vào thư mục cách ly có thể khôi phục tại `<root>/.smart_drive/quarantine/<batch_id>/` (kèm `manifest.json`) thay vì xóa vĩnh viễn:
- Chỉ cách ly rác Tier 3 và không xóa gì khác (`--tier 3 --apply` còn xóa vĩnh viễn cả mục Tier 1-2): xem trước bằng `smart-drive clean --only-tier 3`, rồi chạy `smart-drive clean --only-tier 3 --apply`
- Xem các đợt cách ly: `smart-drive clean --list-quarantine`
- Khôi phục một đợt về vị trí gốc: `smart-drive clean --restore latest` (hoặc `--restore <batch_id>`)
- Xóa vĩnh viễn các đợt cách ly cũ: xem trước bằng `smart-drive clean --purge-quarantine`, rồi `smart-drive clean --purge-quarantine --apply` xóa vĩnh viễn các đợt cũ hơn 30 ngày (`--older-than <ngày>` đặt số ngày khác, còn `--older-than 0` xóa mọi đợt)
- Xóa thẳng mục Tier 3 thay vì cách ly (ví dụ khi ổ đĩa sắp hết chỗ): `smart-drive clean --apply --tier 3 --no-quarantine`

> **Whitelist Bất Khả Xâm Phạm**: Tệp chỉ thị gốc (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`, `PRIVACY.md`), kịch bản khởi chạy (`Setup_*`, `Quick_*`, `Smart_Organize.*`, `SmartDrive.*`), 6 phân vùng gốc, thư mục Windows (`WindowsApps`, `Program Files`, `DumpStack.log.tmp`), thư mục đang được hệ thống dùng (`protected_by_system`: Windows `PATH`, Services, Ứng dụng đã cài, Startup, đích `.lnk`, trình gỡ cài đặt), thư viện game (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`), cơ sở dữ liệu đang chạy (`Microsoft SQL Server 2022`) và `smart-drive-os` được bảo vệ vĩnh viễn.

---

## Sử Dụng Hằng Ngày

```bash
smart-drive checkup                       # điểm sức khỏe 0–100 bằng ngôn ngữ dễ hiểu kèm lệnh gợi ý để sao chép (--lang vi: tiếng Việt)
smart-drive sentinel                      # kiểm tra điểm gắn ổ đĩa, hệ tệp, khiên chặn rác (tự phục hồi), chỉ mục FTS5 và Git
smart-drive health                        # sức khỏe SSD, kiểm tra TRIM (Windows), cấu trúc phân vùng và dung lượng
smart-drive search "llama cat:ai_models"  # SQLite FTS5 xếp hạng BM25 thay vì quét đĩa đệ quy (find, grep, dir /s), dưới 10ms
smart-drive dup                           # tệp trùng (SHA-256 3 giai đoạn) và cluster slack chúng lãng phí
smart-drive audit                         # dung lượng và cluster slack theo kích thước cụm thật của ổ đĩa
smart-drive audit --html                  # tương tự, nhưng xuất thành báo cáo treemap HTML tự chứa chạy offline
```
Dọn rác có [mục riêng](#dọn-rác-an-toàn); snapshot, sao lưu, quản trị `.gitignore` và các tính năng còn lại nằm ở [Tính Năng Khác](#tính-năng-khác).

<details>
<summary>Cú pháp tìm kiếm</summary>

Công cụ SQLite FTS5 (`unicode61` + BM25) xử lý truy vấn đa tiêu chí dưới 10ms:

- **Từ khóa tự do**: `smart-drive search "machine learning"`
- **Lọc phần mở rộng**: `smart-drive search "weights ext:gguf"`
- **Lọc dung lượng**: `smart-drive search "dataset size:>100MB"` (`size:<1MB`, `size:0`). `>`/`<` không tính biên, `>=`/`<=` có tính; đơn vị nhị phân (`1KB = 1024` bytes). Bộ lọc sai cú pháp (`size:>abc`, `size:>10 MB`) phát cảnh báo (stderr ở CLI, `warnings` ở MCP) thay vì bỏ qua âm thầm.
- **Lọc danh mục**: `smart-drive search "llama cat:ai_models"`
- **Lọc thư mục**: `smart-drive search "de thi dir:FPTU"`
- **Lọc loại học liệu**: `smart-drive search --kind slide "vòng lặp"` (hoặc `kind:fact`, `--kind capture`, `--kind lesson`)
- **Truy vấn kết hợp**: `smart-drive search "exam ext:pdf size:>1MB dir:DBI202"`

</details>

24 launcher portable (`.bat` cho Windows CMD, `.ps1` cho PowerShell, `.command` cho macOS, `.sh` cho Linux/POSIX) nằm ở cả gốc dự án và `launchers/`, không cần Git:
- `SmartDrive.*`: menu TUI đầy đủ, gồm mục `[8]` Relink Agents sau khi dời tay, `[9]` Checkup, `[10]` Smart_Organize và `[11]` Relink theo đợt dời gần nhất.
- `Smart_Organize.*`: trình dời an toàn có dẫn dắt từng bước: xem trước dry-run, chờ đóng ứng dụng bận, xác nhận `YES`, kiểm tra sau dời và hoàn tác 1 phím `U` (EN/VI).
- `Quick_Search.*`, `Quick_Clean.*`, `Quick_Audit.*`: nhập từ khóa tìm kiếm tức thì bằng SQLite FTS5; xem trước file rác (dry-run) rồi xác nhận xóa Tier 1; thống kê dung lượng và đo lãng phí cluster slack.
- `Setup_SSD.*`: hướng dẫn khởi tạo ổ đĩa và chọn profile (xem [Tính Năng Khác](#tính-năng-khác)).

`smart-drive self-path-check` kiểm tra môi trường Python (venv, pyenv, user) và in lệnh PowerShell `SetEnvironmentVariable`, `export` cho `~/.bashrc`/`~/.zshrc` hoặc `pyenv rehash` nếu `smart-drive` chưa có trong `PATH`.

## Tính Năng Khác

<details>
<summary>Cấu hình mẫu (Profiles): init và các launcher Setup_SSD</summary>

Chạy `Setup_SSD.bat` / `Setup_SSD.ps1` (Windows), `Setup_SSD.command` (macOS), `./Setup_SSD.sh` (Linux) hoặc `smart-drive init --profile <tên>`; chỉ một chạm là tạo các thư mục phân vùng, cài khiên bảo vệ và gieo chỉ mục FTS5. Chọn một trong 5 cấu hình mẫu:
```bash
python -m smart_drive init --profile workstation-hybrid
python -m smart_drive init --profile ai-developer
```

| Cấu hình mẫu | Đối tượng mục tiêu | Cấu trúc phân vùng & Thư mục con |
|---|---|---|
| **`workstation-hybrid`** *(Khuyên dùng)* | Ổ phụ của bạn (`D:`, `E:`…) chứa game, ứng dụng Windows, bài học | `01_AI_Models`, `02_Learning_Knowledge` (`FPTU/`, `Personal_Books/`, `Notes/`), `03_Development_Projects` (`active/`, `archive/`), `04_System_Workspaces`, `05_Dev_Toolbox` (`OEM_Drivers/`, `Installers/`, `scripts/`), `06_Archives_Storage` |
| **`internal-developer-vault`** | Ổ phụ NVMe/SATA (`D:`, `E:`…) nhận cache chuyển từ C: | `01_AI_Models`, `02_Development_Workspaces`, `03_Data_Vault`, `04_System_Offload_Caches` (`huggingface/`, `ollama/`, `pip/`, `uv/`), `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`ai-developer`** | Ổ SSD di động cho LLM, checkpoint, AI agent | `01_AI_Models` (`checkpoints/`, `gguf/`, `safetensors/`, `datasets/`), `02_Learning_Knowledge`, `03_Development_Projects` (`ai_agents/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`data-science`** | Kỹ sư dữ liệu, pipeline và Jupyter Notebook | `01_AI_Models`, `02_Learning_Knowledge`, `03_Development_Projects` (`notebooks/`, `data_raw/`, `pipelines/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`general-workspace`** *(Mặc định)* | Lập trình tổng hợp, ghi chú, tài liệu học tập | 6 nhóm thư mục chuẩn kèm `Notes/`, `References/`, `active/`, `archive/`, `scripts/` |

</details>

<details>
<summary>Quản trị Gitignore</summary>

`smart-drive gitignore` kiểm toán và áp dụng `.gitignore` theo stack cho các kho Git trên ổ đĩa (<5ms cho Python, Node.js, Jupyter, Java, Rust, .NET, LaTeX, AI Models, Learning). Hệ thống duy trì khối quản lý idempotent (chạy lại nhiều lần vẫn cho cùng kết quả) giữa `# >>> smart-drive managed (do not edit inside) >>>` và `# <<< smart-drive managed <<<`, bảo toàn quy tắc riêng và CRLF/LF:
- **Phòng thủ Cluster Slack exFAT**: Loại trừ `**/.agents/*` (500+ tệp nháp chiếm >260 MB trên cluster 512 KB; `.agents/skills/`, `.agents/workflows/`, `.agents/rules/` vẫn giữ), `.claude/settings.local.json`, `__pycache__/`, `.pytest_cache/`, `node_modules/`, `target/`/`dist/`/`bin/`/`obj/`.
- **Bảo vệ hợp đồng `personal-data` của Constellation**: Khóa `.vega/`, `**/progress.jsonl`, `aurora-progress-*.jsonl`, `orion-attempts.jsonl`, `**/polaris/.cache/`, `**/polaris/captures/`, `.polaris/` và `.sirius/`, cảnh báo nếu bị track trong repo công khai hoặc chưa có remote.

```bash
smart-drive gitignore audit                                 # kiểm toán repo own & local (--all gồm third-party)
smart-drive gitignore apply D:\teamwork_projects\my_project # xem trước (dry-run)
smart-drive gitignore apply D:\teamwork_projects\my_project --yes --untrack
```

- **Phân loại quyền sở hữu**: Tự chia thành `own` (khớp `--owner`, `SMART_DRIVE_GIT_OWNER`, `smartdrive.owner` hoặc chủ sở hữu phổ biến nhất), `third-party` (`open_source/`, `vendor/`, `third_party/` hoặc khác chủ sở hữu—tôn trọng `dist/`/`build/` gốc) và `local` (không có remote).
- **Kiểm tra Visibility qua GitHub CLI (`gh`)**: Truy vấn `public`, `private`, `internal` hoặc `missing` ("remote not found on GitHub") có cache theo phiên, tránh báo động giả trên repo private hoặc remote đã xóa.
- **Bảo toàn file build chủ đích & Keep list**: Tệp trong `package.json` (`bin`, `main`, `module`, `types`, `exports`), phủ định (`!dist/bundle.mjs`) hoặc `smartdrive.keep` / `--keep <mẫu>` không bị báo rác hay untrack. Với tệp chủ đích trong `dist/`, khối quản lý sinh `<dir>/*` kèm `!<file>` (hoặc lược bỏ mẫu nếu lồng sâu).
- **Kiểm toán secret & Untrack an toàn**: Phát hiện `.env`, `.env.*`, `*.pem`, `*.key`, `credentials*.json` bị track và **chỉ hiện tên biến / key** (không lộ giá trị). `--untrack` chạy `git rm -r --cached` (giữ tệp trên đĩa) và từ chối nếu có staged chưa commit.

</details>

<details>
<summary>Snapshot và sao lưu</summary>

- **Snapshot SHA-256 (`smart-drive snapshot`)**: Băm tệp theo khối 64 KB (bộ nhớ $O(1)$) vào `.smart_drive/snapshots/<tên>.json` để phát hiện suy thoái dữ liệu (bit rot) hoặc chỉnh sửa trái phép.
- **Sao lưu gia tăng (`smart-drive backup`)**: Chỉ chép tệp mới/thay đổi và loại trừ rác hệ điều hành. Mặc định phủ `02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox` (đổi bằng `--partitions`); bỏ qua thư mục ẩn (`.git`, `.github`, `.vscode`) trừ khi có `--include-hidden`, và luôn liệt kê phần chưa được phủ.
- **Tệp trùng (`smart-drive dup`) & Clone Git (`smart-drive repos`)**: Quét 3 giai đoạn SHA-256 (hard link tính một lần; không quét `node_modules`, virtualenv, cache và ứng dụng đã cài) và kiểm toán chỉ đọc clone Git để kiểm tra **bản đối chiếu** SSD–máy tính (`commit`, `push`, `pull`, `fetch to compare`) cùng **bản trùng** cùng nơi (giữ bản mới nhất, xóa bản dư cùng nhánh, hoặc chuyển thành `git worktree` với `--relative-paths` trên git 2.48+; bản còn thay đổi chưa commit/push hoặc stash bị chặn xóa).

```bash
smart-drive snapshot create truoc_khi_nop
smart-drive snapshot verify truoc_khi_nop
smart-drive backup --target E:\Backups\SSD_Mirror --dry-run
```

</details>

<details>
<summary>Workstation Hybrid và dời cache ổ C</summary>

`FilesystemAdapter` và `DriveDetectorBackend` tự thích ứng giữa **exFAT 512 KB** (lá chắn cluster slack, cấm symlink, khiên Spotlight/FSEvents) và **NTFS 4 KB** (tính cụm 4.096 bytes, kiểm tra TRIM qua `fsutil`, dời cache ổ `C:`). `smart-drive offload` chuyển cache lớn (HuggingFace, Ollama, PyTorch, Docker WSL2, pip, uv, npm, Conda, Gradle, Cargo) từ ổ `C:` sang `<target>\04_System_Offload_Caches\<tên>` trên ổ phụ của bạn (`D:`, `E:`…) theo **quy trình giao dịch 7 bước** có kiểm tra tiến trình đang dùng cache (`--wait`, `--force`), rollback tự động và nối bằng NTFS Junction (`mklink /J`) không cần Admin. Xem [README_INTERNAL.md](README_INTERNAL.md).

- **AcademicClassifier (FPTU)**: Bóc tách mã môn (`DBI202`, `WED201c`, `SWE202c`, `PRN211`, `PRJ301`, `OSG`), nhận diện đề thi/lab (`PE`, `de thi`, `on luyen`, `lab1_sp26`, `sp26`, `fa25`, `su25`), gom vào `02_Learning_Knowledge/FPTU/Ky_1/` .. `Ky_7/` và sửa mojibake (`h?c k? 3 fptu` $\rightarrow$ `Hoc_Ky_3_fptu`, `n luy?n pe dbi202` $\rightarrow$ `On_Luyen_pe_dbi202`, `k 1 fptu` $\rightarrow$ `Ky_1_fptu`, `dich truyen` $\rightarrow$ `Dich_Truyen`).
- **Phân luồng phi mã nguồn**: `dich truyen` $\rightarrow$ `02_Learning_Knowledge/Personal_Books/dich truyen`, `lenovo` $\rightarrow$ `05_Dev_Toolbox/OEM_Drivers/LENOVO`, `sql2022` $\rightarrow$ `05_Dev_Toolbox/Installers/SQL2022`.
- **Tự Bảo Vệ của AutoZoner & Lá Chắn Hệ Thống (`protected_by_system`)**: Neo `_CURRENT_FILE` tới `_SMART_DRIVE_REPO_DIR` và thư mục cha để `smart-drive-os` không tự dời chính mình; bảo vệ thư mục Windows (`WindowsApps`, `Program Files`, `Program Files (x86)`, `$Recycle.Bin`, `System Volume Information`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`, `DumpStack.log.tmp`), thư mục nằm trong `PATH`, Windows Services, Ứng dụng đã cài (`Uninstall` registry), Startup (`Run`), đích shortcut `.lnk`, thư mục có `unins000.exe`/`uninstall.exe`, thư viện game (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`, `fo4`, `32837`, `Downloads`) và SQL Server (`MSSQL16.MSSQLSERVER`, `MSSQLSERVER`, `MSSQL`).

</details>

<details>
<summary>Tài liệu học tập (Aurora Slides và Polaris)</summary>

SmartDrive-OS tích hợp với [Aurora Slides MCP](https://github.com/DuongNAD/aurora-slides-mcp) và [Polaris MCP](https://github.com/DuongNAD/polaris-mcp) trong hệ sinh thái học tập DuongNAD:

| Dự án | Vai trò | Kho mã |
| :--- | :--- | :--- |
| **Polaris** | Kiến trúc khóa học: nguồn, dữ kiện có vị trí trích, lộ trình, lịch học, Sổ lỗi | [polaris-mcp](https://github.com/DuongNAD/polaris-mcp) |
| **Aurora Slides** | Bài học kiểu keynote: trắc nghiệm, slide dự đoán và dò code, sự kiện tiến độ | [aurora-slides-mcp](https://github.com/DuongNAD/aurora-slides-mcp) |
| **Vega** | Bộ nhớ FSRS và hàng đợi ôn tập duy nhất (hộp thư thẻ) | [vega-mcp](https://github.com/DuongNAD/vega-mcp) |
| **Orion** | Lab thực hành chấm bằng test thật, bậc thang gợi ý, bậc khởi động PRIMM | [orion-mcp](https://github.com/DuongNAD/orion-mcp) |
| **Mira** | Dò code: trace Python thật, trình phát từng dòng có điểm dừng dự đoán | [mira-mcp](https://github.com/DuongNAD/mira-mcp) |
| **Rigel** | Gia sư bám tài liệu: trả lời từ tài liệu bài học, có trích dẫn [ref] | [rigel-mcp](https://github.com/DuongNAD/rigel-mcp) |
| **Sirius** | Vòng thực thi: hạn chót, phân loại việc, hôm nay học gì, bấm giờ, tổng kết tuần | [sirius-mcp](https://github.com/DuongNAD/sirius-mcp) |
| **Constellation** | Đăng ký MCP, kiểm tra sức khỏe, hợp đồng dữ liệu chung | [constellation-mcp](https://github.com/DuongNAD/constellation-mcp) |
| **SmartDrive-OS** | Quản trị SSD, chỉ mục và tìm kiếm tài liệu học | [smart-drive-os](https://github.com/DuongNAD/smart-drive-os) |

Chỉ mục SmartDrive-OS phục vụ Polaris (tìm kiếm học liệu), Sirius và Rigel (khám phá tệp), nhận đồng bộ tức thì (`smart-drive update <dir> --quiet`) từ Aurora ($\ge$ 0.6.2), Polaris ($\ge$ 0.3.2) và Mira, đồng thời thực thi mẫu `.gitignore` của hợp đồng `personal-data` trong [contracts của Constellation](https://github.com/DuongNAD/constellation-mcp/tree/main/contracts).

| Phân Loại (`kind`) | Tệp Nguồn / Cấu Trúc | Nội Dung Trích Xuất & Lập Chỉ Mục |
|---|---|---|
| `slide` | `deck.json` (Aurora Slides) | Tiêu đề slide, gạch đầu dòng, ghi chú thuyết trình và ý chính |
| `lesson` | `course.json` (Aurora Courses) | Đề cương bài giảng, mục tiêu bài học, điều kiện tiên quyết, mô-đun |
| `module` | `polaris/roadmap.json` (Polaris) | Mô-đun trong lộ trình, mốc học và môn tiên quyết |
| `fact` | `polaris/facts.json` (Polaris) | Sự thật nguyên tử (atomic facts), phát biểu, khái niệm, điểm tin cậy |
| `source` | `polaris/sources.json` (Polaris) | Trích dẫn thư mục, URL, ghi chú tác giả và bản tóm tắt |
| `capture` | `polaris/captures/` (Polaris) | Đoạn trích, điểm nhấn tài liệu, trang học tập và trích dẫn |
| `outline` | `polaris/lessons/*.outline.md` (Polaris) | Đề cương bài học (các đề mục) |
| `research` | `polaris/research/*.md` (Polaris) | Tổng hợp nghiên cứu, tóm tắt tài liệu và phân tích học thuật |
| `error` | `polaris/errorlog.md` (Polaris) | Nhật ký lỗi sai, nhận thức sai lầm và cách khắc phục |
| `learning` | Toàn bộ dữ liệu học tập | Tìm kiếm tổng hợp trên tất cả phân loại tri thức giáo dục |

Thư mục bài giảng Aurora và môn học Polaris (`polaris/brief.json`) được ghim thành **Learning Unit** nguyên khối (`pinned: Aurora course/deck or Polaris subject`): AutoZoner không chia tách/di chuyển, trình dọn rác và tìm tệp trùng không xóa tệp bên trong, còn cache cũ `polaris/.cache/**` thu hồi ở Tier-2 (*"Polaris regenerable API cache (Polaris 0.3.2+ keeps it on C:)"*).

</details>

<details>
<summary>Vì sao ổ exFAT cần công cụ này: cluster slack</summary>

Các ổ SSD di động dung lượng lớn (như Kingston XS2000 2 TB) định dạng **exFAT** có kích thước cụm (**cluster size**) mặc định **512 KB (524.288 bytes)**:
- Dung lượng vật lý là $\lceil \text{Kích thước File} / 524.288 \rceil \times 524.288\text{ B}$ (0 B $\rightarrow$ 0 B; 1 B $\rightarrow$ 512 KB / phí 99,9998%; 10 KB $\rightarrow$ 512 KB / phí 98,05%; 100 KB $\rightarrow$ 512 KB / phí 80,47%; 512 KB + 1 B $\rightarrow$ 1.024 KB / phí 50%).
- Một dự án gồm **10.000 tệp nhỏ** (15 MB danh nghĩa) chiếm hơn **5,12 GB** trên exFAT (lãng phí >99% vì cluster slack).
- Trình lập chỉ mục của hệ điều hành (macOS Spotlight `mds`, Windows Search) liên tục ghi metadata ẩn (`.DS_Store`, `Thumbs.db`, `.fseventsd`), gây mòn chip nhớ và hao pin.
- Trên ổ phụ của bạn (`D:`, `E:`… NTFS 4 KB), ổ `C:` nhanh đầy bởi 50–200 GB cache AI/lập trình, còn ổ phụ chứa ứng dụng Windows, thư viện game và SQL Server đang chạy cần bảo vệ tuyệt đối.

| Tiêu chí | Ổ Di động Ngoài (exFAT) | Ổ Phụ Gắn trong (NTFS) |
|---|---|---|
| **Điểm gắn thông thường** | `/Volumes/KINGSTON` hoặc `E:\` | `D:\` hoặc `E:\` (Ổ đĩa cố định) |
| **Kích thước Cluster** | **512 KB** (524.288 bytes) | **4 KB** (4.096 bytes) |
| **Vấn đề cốt lõi** | Lãng phí cluster slack cực lớn trên file nhỏ | Ổ C: cạn dung lượng do cache AI & Dev |
| **Hỗ trợ Symlink** | ❌ Không hỗ trợ (gây lỗi đa nền tảng) | ⚡ Hỗ trợ qua NTFS Junctions (`mklink /J`) |
| **Chiến lược tối ưu** | Đóng gói file vi mô, khiên chống quét rác | Di chuyển cache 7 bước, bảo vệ dịch vụ đang chạy |
| **Đối tượng bảo vệ** | Manifests gốc, scripts khởi chạy, 6 taxonomy | Ứng dụng Windows, Game libraries, Database SQL Server |

</details>

## Bảng Tra Cứu Lệnh CLI

<details>
<summary>Toàn bộ 25 lệnh và tham số chính</summary>

Mọi lệnh chạy qua `smart-drive <lệnh>`, `python -m smart_drive <lệnh>` hoặc `py -m smart_drive <lệnh>` (trên Windows khi `python` không có trong `PATH`):

| Lệnh | Tham số chính | Mô tả chức năng |
|---|---|---|
| `self-path-check` | *(không có)* | Kiểm tra `PATH` hệ thống và in câu lệnh cấu hình 1-chạm. |
| `checkup` | `--root <đd>`, `--dup`, `--lang {en,vi}`, `--json` | Khám tổng quát 1 màn hình: chấm điểm sức khỏe ổ đĩa (`0–100`), so với lần chạy trước và gợi ý bước tiếp theo. |
| `init` | `--profile <tên>`, `--root <đd>`, `--force`, `--json` | Khởi tạo ổ đĩa, tạo 6 nhóm thư mục, cài khiên bảo vệ và gieo chỉ mục FTS5. |
| `status` | `--root <đd>`, `--json` | Kiểm tra điểm gắn ổ đĩa, hệ tệp nhận diện, cấu trúc hình học, khiên bảo vệ và phân vùng. |
| `audit` | `--root <đd>`, `--html [tệp]`, `--json`, `--markdown`, `--export <tệp>` | Kiểm toán dung lượng và cluster slack theo kích thước cụm thật của ổ đĩa (4 KB NTFS, 512 KB exFAT…). `--html` xuất báo cáo treemap tự chứa chạy offline (mặc định `<root>/.smart_drive/reports/audit.html`). Hard link tính một lần; symlink/junction bỏ qua và đếm. |
| `clean` | `--dry-run` *(mặc định)*, `--apply`, `--tier {1,2,3}`, `--only-tier {1,2,3}`, `--no-quarantine`, `--list-quarantine`, `--restore <batch\|latest>`, `--purge-quarantine`, `--older-than <ngày>` *(mặc định 30, `0` = mọi đợt)*, `--log`, `--json` | Dọn rác bắt buộc chạy thử kèm whitelist; bỏ qua gói đã cài, thư mục hệ thống đang dùng và mục git đang theo dõi, và báo cáo phần đã giữ lại. `--apply` xóa vĩnh viễn ngay rác Tier 1-2 (tái tạo được; hãy xem trước) và chỉ cách ly rác Tier 3 vào `<root>/.smart_drive/quarantine/<batch_id>/` (khôi phục qua `--restore`; `--no-quarantine` xóa luôn cả Tier 3). |
| `search` | `<truy_vấn>`, `--ext <đuôi>`, `--size <cỡ>`, `--category <nhóm>`, `--kind <loại>`, `--limit <n>`, `--json` | Tìm kiếm siêu tốc (<10ms) bằng SQLite FTS5 kết hợp xếp hạng BM25. |
| `organize` | `--dry-run`, `--apply`, `--clean`, `--json` | Tự phân loại file rời, định tuyến môn FPTU bằng `AcademicClassifier`, bảo vệ mục hệ thống (`protected_by_system`), giảm slack và cập nhật đường dẫn agent/IDE/shortcut; lưu đợt dời vào `.smart_drive/moves.jsonl`. |
| `undo` | `--apply`, `--dry-run`, `--root <đd>`, `--json` | Hoàn tác đợt dời `organize`/`classify` gần nhất chưa được hoàn tác, bỏ qua mục (không ghi đè) khi chỗ cũ đã bị chiếm và relink agent/IDE/shortcut về đường dẫn gốc (mặc định dry-run). |
| `relink` | `[OLD NEW]`, `--find`, `--apply`, `--ask`, `--wait [PHÚT]`, `--root <đd>`, `--dry-run`, `--json` | Cập nhật đường dẫn dự án và MCP trong coding agent, VS Code/Cursor/Windsurf/Antigravity (`storage.json` & `state.vscdb`), JetBrains (`recentProjects.xml`), Obsidian (`obsidian.json`), shortcut `.lnk`, lịch sử Claude Code, git worktree và `.venv`. |
| `paths` | `--broken`, `--fix`, `--dry-run`, `--root <đd>`, `--json` | Quét lịch sử IDE, cấu hình coding agent, vault Obsidian, shortcut `.lnk` và `PATH` tìm đường dẫn đứt; đoán nơi thư mục đã tới và sửa các khớp chắc chắn với `--fix`. |
| `sentinel` | `--root <đd>`, `--auto-heal`, `--no-heal`, `--json` | Kiểm tra sức khỏe ổ cứng (hệ tệp nhận diện, trạng thái git), phục hồi khiên bảo vệ thiếu (alias: `agent-check`). |
| `mcp` | `[{serve,register}]`, `--root <đd>`, `--all`, `--auth-token <token>`, `--require-auth` | Khởi chạy máy chủ MCP stdio (`serve`) hoặc đăng ký cấu hình cho AI agent (`register`). |
| `mcp-config` | `--all`, `--antigravity`, `--claude`, `--cursor`, `--windsurf`, `--workspace`, `--json` | Tự đăng ký máy chủ MCP vào tệp cấu hình của IDE và AI Agent. |
| `setup` | `--check`, `--dry-run`, `--json`, `--agents <ds>`, `--only <ds>`, `--dest <đd>`, `--no-clone`, `--no-health`, `--repair`, `--yes` | Hỏi cài máy chủ MCP nào (Constellation, Polaris, Aurora Slides, Vega, Orion, Sirius, Mira), rồi tải, đăng ký vào coding agent đã cài (Claude Code, Antigravity, Cursor, Codex) và kiểm tra sức khỏe. `--yes` bỏ qua câu hỏi. `--check` thoát mã `3` = cần cài đặt; `4` = khi không có terminal và không có `--yes` cần người dùng đồng ý (chưa cài gì). |
| `snapshot create` | `[tên]`, `--partitions <ds>`, `--include-hidden`, `--root <đd>`, `--json` | Tạo bản ghi trạng thái kèm băm SHA-256 dạng dòng (khối 64KB) cho phân vùng chỉ định. |
| `snapshot list` | `--root <đd>`, `--json` | Liệt kê các bản snapshot đã lưu kèm dung lượng và số lượng tệp. |
| `snapshot verify` | `<tên>`, `--no-untracked`, `--root <đd>`, `--json` | Kiểm toán toàn vẹn dữ liệu so với snapshot để phát hiện tệp bị sửa hoặc bit rot. |
| `backup` | `--target <đích>`, `--dry-run`, `--hash`, `--partitions <ds>`, `--include-hidden`, `--json` | Sao lưu gia tăng, chỉ chép tệp mới hoặc thay đổi sang ổ đích. |
| `classify` | `[đd]`, `--suggest`, `--dry-run`, `--apply`, `--recursive`, `--json` | Nhận diện sâu magic bytes và cấu trúc tệp cho mô hình AI, dataset, tài liệu, dự án. Chỉ xét các tệp nằm trực tiếp trong thư mục được quét; thêm `--recursive` mới xét cả thư mục con. Không bao giờ tách thư mục ứng dụng, thư viện hay dự án (có chương trình, script khởi chạy, tệp manifest, `.git`) hay thư mục chứa một thư mục chương trình, nên cây tài liệu có thư mục cài đặt con cũng bị bỏ qua; mỗi thư mục được liệt kê ở `skipped_bundles`, và thư mục bạn chỉ định cũng bị kiểm tra như vậy (chỉ gốc ổ được miễn; vẫn nhận `--no-recursive`). Không đi theo/dời symlink hay junction, không đụng tệp của SmartDrive và hệ thống. |
| `offload` | `--scan`, `--move <tên>`, `--target <ổ>`, `--revert <tên>`, `--wait [PHÚT]`, `--force`, `--dry-run`, `--json` | Quét và dời cache lớn sang ổ phụ của bạn (`D:`, `E:`…) qua NTFS junction (Windows) hoặc symlink (macOS/Linux); chặn khi tiến trình đang dùng cache còn chạy hoặc khi không đọc được danh sách chương trình đang chạy, trừ khi có `--wait` hoặc `--force`. |
| `health` | `[ổ_đĩa]`, `--root <đd>`, `--json` | Kiểm tra sức khỏe SSD, TRIM (Windows), cluster geometry và dung lượng (ký tự ổ đĩa trên Windows hoặc `/Volumes/MySSD` trên macOS/Linux). |
| `dup` | `--root <đd>`, `--min-size <byte>`, `--json` | Tìm tệp trùng 3 giai đoạn SHA-256 kèm dung lượng slack thu hồi theo cluster thật của ổ (512 KB nếu không đọc được; `--json` ghi rõ ở `cluster_size` và `cluster_size_source`: `detected`, hoặc `assumed` khi dùng 512 KB). Hard link cùng dữ liệu tính là một tệp, không đề xuất xóa. Không quét cây phụ thuộc, môi trường và cache (`node_modules`, `site-packages`, virtualenv, thư mục gắn thẻ `CACHEDIR.TAG` như `target` của Cargo, `__pycache__`, `.git`, …) cũng như thư mục Windows hoặc ứng dụng đã cài đang dùng (`protected_by_system`); kế hoạch ghi số thư mục bị bỏ qua. Chỉ mở tệp khi có tệp khác cùng kích thước, mở nhiều tệp cùng lúc, và tiến độ in ra stderr mỗi ~2 giây nên `--json` trên stdout vẫn đọc được. |
| `repos` | `[đd...]`, `--root <ssd>`, `--portable <đd>`, `--max-depth <n>`, `--json` | Báo cáo chỉ đọc về các clone git: bản trùng cùng nơi (giữ / xóa / chuyển thành worktree) và trạng thái đồng bộ bản SSD–máy tính. |
| `gitignore audit` | `[repo]`, `--root <đd>`, `--all`, `--owner <tên>`, `--keep <mẫu>`, `--json` | Kiểm toán repo git tìm mẫu `.gitignore` thiếu, rác bị commit (`.agents`, pycache) và dữ liệu học tập cá nhân lộ trong repo công khai. |
| `gitignore apply` | `<repo>`, `--yes`, `--untrack`, `--keep <mẫu>`, `--json` | Áp dụng khối `.gitignore` theo stack (mặc định dry-run; `--yes` ghi file; `--untrack` gỡ file rác khỏi chỉ mục git). |
| `index` | `--root <đd>`, `--db <đd>`, `--batch <n>` | Lập chỉ mục toàn bộ ổ đĩa với SQLite FTS5 (>15.000 tệp/giây). |
| `update` | `[đd]`, `--root <đd>`, `--db <đd>`, `--quiet`, `--json` | Đồng bộ chỉ mục tìm kiếm gia tăng siêu nhanh $O(1)$ (<2 giây). |

</details>

## Bảo Mật & Quyền Riêng Tư Dữ Liệu

SmartDrive-OS tuân thủ nguyên tắc **Local-First & Quyền Riêng Tư Tối Đa**:
- **Hoạt động 100% Cục bộ (Local-Only)**: Quét ổ đĩa, chỉ mục SQLite FTS5 và xác thực snapshot SHA-256 diễn ra trực tiếp trên máy của bạn.
- **Zero Telemetry & Zero PII Logging**: Không thu thập hành vi, không kết nối ngầm, không đọc mã nguồn bí mật, token hay private key; chỉ lưu metadata tệp trong `.smart_drive/index.db`.
- **Sẵn sàng cho Air-Gap & Khiên Bảo vệ**: Không có dependencies ngoài (`dependencies = []`), MCP qua `stdio`, `smart-drive setup --no-clone` ngoại tuyến, mặc định chạy thử khi dọn rác và bảo vệ vĩnh viễn tệp cốt lõi (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `PRIVACY.md`).

Xem chi tiết tại [PRIVACY.md](PRIVACY.md).

## Kiểm Thử

2.000+ bài kiểm thử tự động chạy trên CI với Windows, macOS và Linux (Python 3.9 và 3.13) bằng `unittest`:

```bash
python -m unittest discover tests -v
```

Bộ kiểm thử bao phủ quản trị `.gitignore` (quyền sở hữu, file build chủ đích, phủ định `!`, danh sách `keep`, visibility qua `gh`, kiểm toán secret chỉ hiện tên biến, bảo toàn CRLF, chặn khi có staged changes), bảo vệ Learning Units, bảo vệ tham chiếu hệ thống (`protected_by_system`), cập nhật đường dẫn agent/IDE/Obsidian/shortcut (`storage.json`, `state.vscdb`, `recentProjects.xml`, `.lnk`, Claude desktop MSIX, `--find`, `--wait`), `undo`, `checkup`, `paths --broken`, cách ly/khôi phục `clean`, xuất treemap `audit --html`, `Smart_Organize` (`SD_SELFTEST=1`), mô phỏng cluster (512 B–32 MB), tự bảo vệ và `AcademicClassifier`.

## Đóng Góp Mã Nguồn & Giấy Phép

- 🌟 **Star & Fork** trên GitHub: [DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os) · 🐛 [GitHub Issues](https://github.com/DuongNAD/smart-drive-os/issues) · 🔀 [Pull Requests](https://github.com/DuongNAD/smart-drive-os/pulls) · 📖 [CONTRIBUTING.md](CONTRIBUTING.md)
- 📄 Phân phối theo **Giấy phép MIT** — xem [LICENSE](LICENSE).

*Vành đai Sao Thổ tựa như những làn lưu trữ ngăn nắp: mỗi tệp tin đúng làn của mình trên toàn bộ ổ đĩa.*
