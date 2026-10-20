<p align="center"><img src="docs/banner.jpg" alt="SmartDrive-OS — Sao Thổ với các vành đai ngăn nắp giữa không gian vũ trụ" width="100%"></p>

# SmartDrive-OS — Hướng Dẫn Sử Dụng (Tiếng Việt)

*Vành đai Sao Thổ tựa như những làn lưu trữ ngăn nắp: mỗi tệp tin đúng làn của mình trên toàn bộ ổ đĩa.*

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Zero Pip Dependencies](https://img.shields.io/badge/dependencies-0%20external%20pip-success.svg)](pyproject.toml)
[![Privacy: 100% Cục bộ](https://img.shields.io/badge/Privacy-100%25%20C%E1%BB%A5c%20b%E1%BB%99-success?style=flat-square&logo=shield)](PRIVACY.md)
[![CI](https://github.com/DuongNAD/smart-drive-os/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/DuongNAD/smart-drive-os/actions/workflows/tests.yml)
[![M8ven Score](https://m8ven.ai/badge/mcp/duongnad-smart-drive-os-1kxkwu)](https://m8ven.ai/mcp/duongnad-smart-drive-os)
[![Phiên bản: v1.3.1](https://img.shields.io/badge/phi%C3%AAn%20b%E1%BA%A3n-v1.3.1-blue.svg)](https://github.com/DuongNAD/smart-drive-os/releases/tag/v1.3.1)
[![Giấy phép: MIT](https://img.shields.io/badge/Gi%E1%BA%A5y%20ph%C3%A9p-MIT-yellow.svg)](LICENSE)

SmartDrive-OS là bộ quản trị ổ đĩa và tìm kiếm tức thì (<10ms) bằng SQLite FTS5 cho ổ SSD di động (exFAT cluster 512 KB) và ổ phụ gắn trong máy trạm (NTFS cluster 4 KB). Dự án dành cho lập trình viên, sinh viên đại học (đặc biệt FPTU) và các trợ lý lập trình AI (Claude Code, Claude Desktop, Antigravity, Cursor, Codex, Windsurf) để tự quy hoạch thư mục không làm gãy đường dẫn agent, chuẩn hóa `.gitignore`, dời cache ổ `C:` và kiểm chứng tệp bằng SHA-256. Hệ thống chạy 100% cục bộ bằng thư viện chuẩn Python 3.9+ (`dependencies = []`), không thu thập dữ liệu.

> 🌐 **English**: [README.md](README.md) · 🚀 **Hướng dẫn Ổ phụ Gắn trong (NTFS) & Dời Cache Ổ C:**: [README_INTERNAL.md](README_INTERNAL.md)

---

## Mục Lục

- [Khởi Động Nhanh](#khởi-động-nhanh)
- [Sử Dụng Hằng Ngày](#sử-dụng-hằng-ngày)
- [Vì Sao Ổ exFAT Cần Công Cụ Này](#vì-sao-ổ-exfat-cần-công-cụ-này)
- [Tổng Quan Tính Năng](#tổng-quan-tính-năng)
  - [Bảng Tra Cứu Lệnh CLI](#bảng-tra-cứu-lệnh-cli)
  - [Dời Thư Mục Và Giữ Coding Agent Hoạt Động](#dời-thư-mục-và-giữ-coding-agent-hoạt-động)
  - [Máy Chủ MCP & Trợ Lý Lập Trình AI](#máy-chủ-mcp--trợ-lý-lập-trình-ai)
  - [Cú Pháp Tìm Kiếm](#cú-pháp-tìm-kiếm)
  - [Tài Liệu Học Tập (Aurora Slides Và Polaris)](#tài-liệu-học-tập-aurora-slides-và-polaris)
  - [Quản Trị Gitignore](#quản-trị-gitignore)
  - [Dọn Rác An Toàn](#dọn-rác-an-toàn)
  - [Snapshot Và Sao Lưu](#snapshot-và-sao-lưu)
  - [Workstation Hybrid Và Di Chuyển Cache Ổ C](#workstation-hybrid-và-di-chuyển-cache-ổ-c)
  - [Bộ Khởi Chạy 1-Chạm (Launchers)](#bộ-khởi-chạy-1-chạm-launchers)
  - [Cấu Hình Mẫu (Profiles)](#cấu-hình-mẫu-profiles)
- [Kiểm Thử](#kiểm-thử)
- [Bảo Mật & Quyền Riêng Tư Dữ Liệu](#bảo-mật--quyền-riêng-tư-dữ-liệu)
- [Đóng Góp Mã Nguồn & Giấy Phép](#đóng-góp-mã-nguồn--giấy-phép)

---

## Khởi Động Nhanh

### 1. Cài đặt (hoặc chạy trực tiếp không cần cài)
```bash
pip install -e .              # cài lệnh `smart-drive` từ bản clone
python -m smart_drive <lệnh>  # hoặc chạy trực tiếp trong dự án
py -m smart_drive <lệnh>      # Windows Python Launcher (khi `python` không có trong PATH)
smart-drive self-path-check   # kiểm tra PATH và in lệnh cấu hình 1-chạm
```

### 2. Khởi tạo ổ đĩa
Chạy `Setup_SSD.bat` / `Setup_SSD.ps1` (Windows), `Setup_SSD.command` (macOS), `./Setup_SSD.sh` (Linux) hoặc `smart-drive init --profile <tên>` (xem [Cấu Hình Mẫu (Profiles)](#cấu-hình-mẫu-profiles)):
- `workstation-hybrid`: ổ phụ của bạn (`D:`, `E:`…) dùng chung cho game, ứng dụng Windows và bài học.
- `internal-developer-vault`: ổ phụ NVMe/SATA nhận các cache AI và lập trình chuyển từ ổ `C:`.
- `ai-developer`: ổ SSD di động chứa mô hình LLM, checkpoint, dataset và AI agent.
- `data-science`: kỹ thuật dữ liệu, pipeline xử lý bảng biểu và Jupyter Notebook.
- `general-workspace` *(mặc định)*: cấu trúc chuẩn cho mã nguồn, lưu trữ, ghi chú học tập và tiện ích.
```bash
python -m smart_drive init --profile workstation-hybrid
python -m smart_drive init --profile ai-developer
```

### 3. Cài đặt hệ sinh thái MCP (một lệnh)
Tải Constellation, Polaris, Aurora Slides, Vega, Orion, Sirius và Mira vào `~/.smart-drive/mcp` (ngoài ổ exFAT), đăng ký cùng SmartDrive vào các coding agent đã cài (Claude Code, Antigravity, Cursor, Codex) và kiểm tra sức khỏe. Lệnh liệt kê các máy chủ và hỏi bạn muốn cài những máy nào trước khi tải hay đăng ký bất cứ thứ gì, chạy lại an toàn:
```bash
python3 -m smart_drive setup  # macOS / Linux (Windows: python -m smart_drive setup hoặc py -m smart_drive setup)
```
Trả lời `a` (tất cả), `n` (không cài, mặc định) hoặc số/tên như `1,3` hay `polaris,vega`; Constellation (mặt phẳng điều khiển) luôn được cài kèm. `--yes` bỏ qua câu hỏi (`--only polaris,vega --yes` chỉ cài những máy đó). Coding agent không có terminal để trả lời: lần chạy của agent không cài gì, thoát với mã `4` kèm danh sách, rồi agent hỏi bạn ngay trong khung chat của nó trước khi chạy `setup --only <lựa chọn của bạn> --yes`.

Cần Node.js >= 22.4 và git. `--dry-run` xem trước; `--check --json` báo trạng thái (mã thoát `3` = cần cài đặt); `--no-clone` ngoại tuyến; `--agents claude,cursor` giới hạn agent. Máy chủ đã đăng ký được dùng lại, không clone lần hai (`mcp-agy` tách riêng: venv riêng, Python >= 3.10, API key).

### 4. Đăng ký riêng máy chủ MCP của SmartDrive
```bash
smart-drive mcp register --all  # Antigravity, Claude, Cursor, Codex, Windsurf, .mcp.json
```

---

## Sử Dụng Hằng Ngày

**Khám tổng quát 1 màn hình (`checkup`)** — Chấm điểm sức khỏe ổ đĩa `0–100` (`A–F`) bằng ngôn ngữ dễ hiểu kèm các câu lệnh gợi ý tiếp theo:
```bash
smart-drive checkup
smart-drive checkup --lang vi
```

**Tìm kiếm (<10ms)** — Truy vấn chỉ mục SQLite FTS5 xếp hạng BM25 thay vì quét đĩa đệ quy (`find`, `grep`, `dir /s`):
```bash
smart-drive search "llama cat:ai_models"
smart-drive search "exam ext:pdf size:>1MB dir:DBI202"
smart-drive search --kind slide "vòng lặp"
```

**Tự động quy hoạch (`organize`) & Hoàn tác (`undo`)** — Đưa thư mục rời ở gốc ổ đĩa vào 6 phân vùng chuẩn, phân luồng môn FPTU và giảm cluster slack (hoặc chạy `Smart_Organize.bat` / `./Smart_Organize.sh` để được dẫn dắt từng bước):
```bash
smart-drive organize --dry-run
smart-drive organize --apply
smart-drive undo --apply        # hoàn tác đợt dời gần nhất và relink về chỗ cũ
```

**Dời thư mục không gãy coding agent, IDE hay shortcut** — `organize --apply` và `classify --apply` tự cập nhật đường dẫn. Sau khi tự dời thư mục tay (hoặc qua mục `[8]` trong menu `SmartDrive`), chạy:
```bash
smart-drive relink --find --apply --ask --wait
smart-drive relink "E:\Cu\du_an" "E:\Moi\du_an" --wait
smart-drive paths --broken      # quét mục IDE, cấu hình agent và shortcut .lnk bị đứt
```

**Dọn rác an toàn (`clean` có cách ly & khôi phục)** — Xem trước và chuyển rác metadata hệ điều hành, cache biên dịch hoặc log vào `.smart_drive/quarantine/` (mặc định `--dry-run`):
```bash
smart-drive clean
smart-drive clean --apply --tier 1
smart-drive clean --list-quarantine
smart-drive clean --restore latest
```

**Kiểm toán & Treemap HTML (`audit`)** — Thống kê dung lượng và cluster slack theo kích thước cụm thật của ổ đĩa, hoặc xuất báo cáo HTML treemap tự chứa chạy offline:
```bash
smart-drive audit
smart-drive audit --html
```

**Kiểm tra sức khỏe (`sentinel`)** — Kiểm tra điểm gắn ổ đĩa, hệ tệp, khiên chặn rác (tự phục hồi), chỉ mục FTS5 và Git:
```bash
smart-drive sentinel
smart-drive health
```

**Snapshot và sao lưu** — Tạo bản kê khai SHA-256 dạng dòng chống bit rot và sao lưu gia tăng:
```bash
smart-drive snapshot create truoc_khi_nop
smart-drive snapshot verify truoc_khi_nop
smart-drive backup --target E:\Backups\SSD_Mirror --dry-run
```

---

## Vì Sao Ổ exFAT Cần Công Cụ Này

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

---

## Tổng Quan Tính Năng

| Nhóm tính năng | Tóm tắt một dòng | Lệnh chính | Mục chi tiết |
|---|---|---|---|
| **CLI Hợp Nhất & Kiểm Toán** | 25 lệnh cho khám tổng quát (`checkup`), khởi tạo, FTS5, đo slack theo cluster thật, HTML treemap và quản trị ổ đĩa | `smart-drive checkup` / `smart-drive audit --html` | [Bảng Tra Cứu Lệnh CLI](#bảng-tra-cứu-lệnh-cli) |
| **Relink Agent, IDE & Shortcut** | Giữ coding agent, VS Code/JetBrains/Obsidian, shortcut `.lnk`, git worktree và `.venv` hoạt động sau khi dời thư mục (kèm `undo` & `paths --broken`) | `smart-drive relink --find --apply --ask --wait` | [Dời Thư Mục Và Giữ Coding Agent Hoạt Động](#dời-thư-mục-và-giữ-coding-agent-hoạt-động) |
| **Máy Chủ MCP & Setup** | 9 công cụ JSON-RPC 2.0 stdio (M8ven Hạng A 100/100) và trình cài hệ sinh thái 1 lệnh | `python -m smart_drive setup` | [Máy Chủ MCP & Trợ Lý Lập Trình AI](#máy-chủ-mcp--trợ-lý-lập-trình-ai) |
| **Tìm Kiếm FTS5 <10ms** | Tìm kiếm SQLite FTS5 `unicode61` BM25 theo từ khóa, đuôi tệp, dung lượng nhị phân, thư mục | `smart-drive search "<truy_vấn>"` | [Cú Pháp Tìm Kiếm](#cú-pháp-tìm-kiếm) |
| **Tài Liệu Học Tập** | Đánh chỉ mục Aurora Slides & Polaris (`slide`, `fact`, `capture`) và ghim bảo vệ nguyên khối | `smart-drive search --kind slide "<q>"` | [Tài Liệu Học Tập](#tài-liệu-học-tập-aurora-slides-và-polaris) |
| **Quản Trị Gitignore** | Khối `.gitignore` theo stack, bảo vệ hợp đồng `personal-data`, kiểm toán secret và rò rỉ | `smart-drive gitignore audit` | [Quản Trị Gitignore](#quản-trị-gitignore) |
| **Dọn Rác 3 Tầng & Cách Ly** | Dọn rác ưu tiên chạy thử (`--dry-run`), cách ly có khôi phục (`quarantine`) và whitelist bảo vệ hệ thống/ứng dụng | `smart-drive clean` | [Dọn Rác An Toàn](#dọn-rác-an-toàn) |
| **Snapshot, Sao Lưu & Repos** | Băm SHA-256 chống bit rot, sao lưu gia tăng, phát hiện tệp trùng và kiểm toán clone Git | `smart-drive snapshot create` | [Snapshot Và Sao Lưu](#snapshot-và-sao-lưu) |
| **Workstation Hybrid & Offload** | Tự thích ứng exFAT/NTFS, `AcademicClassifier` (FPTU), lá chắn `protected_by_system` và dời cache ổ `C:` | `smart-drive offload --scan` | [Workstation Hybrid Và Di Chuyển Cache Ổ C](#workstation-hybrid-và-di-chuyển-cache-ổ-c) |
| **24 Launcher Portable** | 24 tệp khởi chạy 1-chạm `.bat`, `.ps1`, `.command`, `.sh` (gồm `Smart_Organize`) không cần Git kèm `self-path-check` | `Smart_Organize.bat` / `SmartDrive.bat` | [Bộ Khởi Chạy 1-Chạm (Launchers)](#bộ-khởi-chạy-1-chạm-launchers) |
| **5 Cấu Hình Mẫu** | Bố cục thư mục cho máy trạm lai, kho cache ổ C:, kỹ sư AI, khoa học dữ liệu và đa năng | `smart-drive init --profile <tên>` | [Cấu Hình Mẫu (Profiles)](#cấu-hình-mẫu-profiles) |

---

## Bảng Tra Cứu Lệnh CLI

Mọi lệnh chạy qua `smart-drive <lệnh>`, `python -m smart_drive <lệnh>` hoặc `py -m smart_drive <lệnh>` (trên Windows khi `python` không có trong `PATH`):

| Lệnh | Tham số chính | Mô tả chức năng |
|---|---|---|
| `self-path-check` | *(không có)* | Kiểm tra `PATH` hệ thống và in câu lệnh cấu hình 1-chạm. |
| `checkup` | `--root <đd>`, `--dup`, `--lang {en,vi}`, `--json` | Khám tổng quát 1 màn hình: chấm điểm sức khỏe ổ đĩa (`0–100`), so với lần chạy trước và gợi ý bước tiếp theo. |
| `init` | `--profile <tên>`, `--root <đd>`, `--force`, `--json` | Khởi tạo ổ đĩa, tạo 6 nhóm thư mục, cài khiên bảo vệ và gieo chỉ mục FTS5. |
| `status` | `--root <đd>`, `--json` | Kiểm tra điểm gắn ổ đĩa, hệ tệp nhận diện, cấu trúc hình học, khiên bảo vệ và phân vùng. |
| `audit` | `--root <đd>`, `--html [tệp]`, `--json`, `--markdown`, `--export <tệp>` | Kiểm toán dung lượng và cluster slack theo kích thước cụm thật của ổ đĩa (4 KB NTFS, 512 KB exFAT…). `--html` xuất báo cáo treemap tự chứa chạy offline (mặc định `<root>/.smart_drive/reports/audit.html`). Hard link tính một lần; symlink/junction bỏ qua và đếm. |
| `clean` | `--dry-run` *(mặc định)*, `--apply`, `--tier {1,2,3}`, `--no-quarantine`, `--list-quarantine`, `--restore <batch\|latest>`, `--purge-quarantine`, `--older-than <ngày>`, `--log`, `--json` | Dọn rác bắt buộc chạy thử kèm whitelist và mặc định cách ly vào `<root>/.smart_drive/quarantine/<batch_id>/` (khôi phục qua `--restore`). |
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
| `classify` | `[đd]`, `--suggest`, `--dry-run`, `--apply`, `--no-recursive`, `--json` | Nhận diện sâu magic bytes và cấu trúc tệp cho mô hình AI, dataset, tài liệu, dự án. Không đi theo/dời symlink hay junction, không đụng tệp của SmartDrive và hệ thống. |
| `offload` | `--scan`, `--move <tên>`, `--target <ổ>`, `--revert <tên>`, `--wait [PHÚT]`, `--force`, `--dry-run`, `--json` | Quét và dời cache lớn sang ổ phụ của bạn (`D:`, `E:`…) qua NTFS junction (Windows) hoặc symlink (macOS/Linux); chặn khi tiến trình đang dùng cache còn chạy trừ khi có `--wait` hoặc `--force`. |
| `health` | `[ổ_đĩa]`, `--root <đd>`, `--json` | Kiểm tra sức khỏe SSD, TRIM (Windows), cluster geometry và dung lượng (ký tự ổ đĩa trên Windows hoặc `/Volumes/MySSD` trên macOS/Linux). |
| `dup` | `--root <đd>`, `--json` | Tìm tệp trùng 3 giai đoạn SHA-256 kèm dung lượng slack thu hồi theo cluster thật. Hard link cùng dữ liệu tính là một tệp, không đề xuất xóa. |
| `repos` | `[đd...]`, `--root <ssd>`, `--portable <đd>`, `--max-depth <n>`, `--json` | Báo cáo chỉ đọc về các clone git: bản trùng cùng nơi (giữ / xóa / chuyển thành worktree) và trạng thái đồng bộ bản SSD–máy tính. |
| `gitignore audit` | `[repo]`, `--root <đd>`, `--all`, `--owner <tên>`, `--keep <mẫu>`, `--json` | Kiểm toán repo git tìm mẫu `.gitignore` thiếu, rác bị commit (`.agents`, pycache) và dữ liệu học tập cá nhân lộ trong repo công khai. |
| `gitignore apply` | `<repo>`, `--yes`, `--untrack`, `--keep <mẫu>`, `--json` | Áp dụng khối `.gitignore` theo stack (mặc định dry-run; `--yes` ghi file; `--untrack` gỡ file rác khỏi chỉ mục git). |
| `index` | `--root <đd>`, `--db <đd>`, `--batch <n>` | Lập chỉ mục toàn bộ ổ đĩa với SQLite FTS5 (>15.000 tệp/giây). |
| `update` | `[đd]`, `--root <đd>`, `--db <đd>`, `--quiet`, `--json` | Đồng bộ chỉ mục tìm kiếm gia tăng siêu nhanh $O(1)$ (<2 giây). |

---

## Dời Thư Mục Và Giữ Coding Agent Hoạt Động

Các coding agent, IDE và shortcut màn hình nhớ dự án và máy chủ MCP bằng đường dẫn tuyệt đối, còn exFAT không để lại liên kết ở chỗ cũ. Khi `organize` hoặc `classify` dời thư mục, SmartDrive ghim bảo vệ các thư mục đang được hệ thống dùng trước rồi tự viết lại các đường dẫn còn lại:
- **Bảo vệ Thư mục Hệ thống & Ứng dụng (`protected_by_system`)**: Trước khi lên kế hoạch dời, SmartDrive quét biến môi trường `PATH` (User & System), Windows Services (`ImagePath`), Ứng dụng đã cài (`Uninstall` registry `InstallLocation` / `DisplayIcon` / `UninstallString`), Startup (`Run`), shortcut `.lnk` (Desktop, Start Menu, Quick Launch, gốc ổ đĩa), thư mục chứa `unins000.exe` / `uninstall.exe` và thư viện game (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`). Mọi thư mục khớp đều được ghim tại chỗ kèm lý do cụ thể trong `protected_by_system`.
- **Cấu hình Agent, IDE & Shortcut**: Claude Code (`~/.claude.json`), Claude desktop (cấu hình, session, worktree, kể cả bản Microsoft Store MSIX), Codex (`config.toml`), Gemini CLI, Antigravity, Cursor, Windsurf, VS Code, VSCodium, Positron (`storage.json` và SQLite `state.vscdb`), các IDE JetBrains (`recentProjects.xml` gồm cả macro `$USER_HOME$`), Obsidian (`obsidian.json`), shortcut `.lnk` trên Windows, manifest của Constellation và `.mcp.json` của ổ đĩa. Mỗi tệp giữ nguyên định dạng và sao lưu thành `<file>.bak.smart-drive`; tệp SQLite `state.vscdb` đang bị IDE mở sẽ được bỏ qua kèm cảnh báo (`busy_apps`).
- **Lịch sử và repository**: lịch sử và memory của Claude Code chuyển sang thư mục dự án mới, git worktree được sửa, và gói editable trong `.venv` trỏ sang chỗ mới.
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
smart-drive relink --find --apply --ask --wait           # ...và hỏi y/N cho từng mục ? trướ
smart-drive paths --broken                               # kiểm tra shortcut, mục IDE, PATH và cấu hình agent bị đứt
smart-drive paths --broken --fix                         # tự sửa các mục khớp chắc chắn qua relink
```
Mục `[8]` trong menu `SmartDrive` (hoặc mục `[10]` / `Smart_Organize.*` cho trình dời an toàn có phím `U` hoàn tác ngay) làm việc này trực quan. Đóng các coding agent và IDE trước khi relink, hoặc thêm `--wait` để SmartDrive chờ chúng đóng rồi mới ghi tệp. Nếu lệnh trong `.venv` đã dời vẫn trỏ về chỗ cũ, chạy `uv sync --reinstall` trong dự án đó.

`--find` chỉ đánh dấu ✓ khi nhật ký dời thư mục, hoặc những gì agent nhớ bên trong thư mục cũ, cho biết thư mục đã tới đâu. Thư mục chỉ trùng tên được đánh dấu `?` và `--apply` không bao giờ tự cập nhật nó, vì đó có thể là một dự án khác còn thư mục cũ đã bị xóa: hãy kiểm tra rồi chạy lệnh `smart-drive relink "CŨ" "MỚI"` được in ngay bên cạnh. Hoặc thêm `--ask` (mục `[8]` đã dùng sẵn): lệnh hỏi `Relink CŨ -> MỚI? [y/N]` cho từng thư mục `?` chỉ có một ứng viên, rồi cập nhật các thư mục bạn trả lời `y` cùng lúc với các mục ✓. Nhấn Enter nghĩa là không. Lệnh chỉ hỏi khi chạy trong terminal: với `--json` hoặc đầu vào qua pipe thì không hỏi gì.

---

## Máy Chủ MCP & Trợ Lý Lập Trình AI

SmartDrive-OS cung cấp máy chủ MCP chuẩn JSON-RPC 2.0 qua `stdio` với **9 công cụ**:

1. `ssd_search`: Tìm kiếm FTS5 tức thì trả về token rút gọn (<2.000 tokens/truy vấn). *(chỉ đọc)*
2. `ssd_audit`: Thống kê dung lượng và phân tích lãng phí cluster slack có chế độ nén gọn. *(chỉ đọc)*
3. `ssd_clean`: Dọn rác an toàn có bảo vệ whitelist và hỗ trợ dry-run. *(xóa rác)*
4. `ssd_find_duplicates`: Tìm file trùng qua 3 giai đoạn SHA-256 có phân trang và giới hạn xem trước. *(chỉ đọc)*
5. `ssd_update_index`: Đồng bộ chỉ mục tìm kiếm gia tăng (<2s). *(idempotent)*
6. `ssd_check_safety`: Kiểm tra tương thích exFAT (9 ký tự cấm Win32, 22 từ khóa DOS và symlink). *(chỉ đọc)*
7. `ssd_status`: Kiểm tra trạng thái gắn kết SSD, khiên bảo vệ và phân vùng. *(chỉ đọc)*
8. `ssd_auto_organize`: Tự phân luồng và gom file giảm lãng phí slack kết hợp `AcademicClassifier`. *(sắp xếp/xóa rác)*
9. `ssd_gitignore`: Kiểm toán và áp dụng khối `.gitignore` theo stack, bảo vệ dữ liệu cá nhân, kiểm toán secret và untrack an toàn. *(idempotent)*

Đăng ký bằng `smart-drive mcp register --all` (hoặc `smart-drive mcp-config --all`). Khi chạy từ mã nguồn (chưa `pip install`), mỗi mục tự kèm `PYTHONPATH` trỏ về dự án. Mẫu trong `configs/`: `.mcp.json` (workspace), `mcp_config.json` (Antigravity), `claude_desktop_config.json` (Claude Desktop & Code), `cursor_mcp.json` (Cursor / Codex), `windsurf_mcp.json` (Windsurf).

<details>
<summary>Chứng chỉ M8ven MCP Directory (Hạng A 100/100) & Tối ưu Token</summary>

Được kiểm định trên [M8ven MCP Directory](https://m8ven.ai/mcp/duongnad-smart-drive-os) với **Điểm Tin Cậy Hạng A (100/100)** (mã: `duongnad-smart-drive-os-1kxkwu`):
- **AST Handler Isolation**: Ánh xạ tĩnh qua `SmartDriveMCPServer.TOOL_HANDLERS`, không dùng `eval()` hay reflection động.
- **JSON nén kèm phân trang**: Trường `limit`, `offset`, `total_groups`, `returned_group_count`, `has_more`, `next_offset` và giới hạn 10 tệp xem trước/nhóm trùng (`truncated_files_count`).
- **Xác thực HMAC & Giới hạn tần suất**: `hmac.compare_digest` qua `auth/handshake` (`--auth-token`, `--require-auth`, `SMART_DRIVE_MCP_AUTH_TOKEN`) và `SlidingWindowRateLimiter` đa luồng (`-32000` kèm `Retry-After`).
- **Cô lập đường dẫn, Loopback & Tool Hints**: `_resolve_safe_path` chặn `../`, `\0` và nhảy ổ đĩa; khóa `ALLOWED_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")`; khai báo `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` cho cả 9 công cụ.
- **Không kết nối ngầm**: 100% `stdio` cục bộ; mạng duy nhất là `git clone` tường minh khi chạy `smart-drive setup` (bỏ qua bằng `--no-clone`).

</details>

---

## Cú Pháp Tìm Kiếm

Công cụ SQLite FTS5 (`unicode61` + BM25) xử lý truy vấn đa tiêu chí dưới 10ms:

- **Từ khóa tự do**: `smart-drive search "machine learning"`
- **Lọc phần mở rộng**: `smart-drive search "weights ext:gguf"`
- **Lọc dung lượng**: `smart-drive search "dataset size:>100MB"` (`size:<1MB`, `size:0`). `>`/`<` không tính biên, `>=`/`<=` có tính; đơn vị nhị phân (`1KB = 1024` bytes). Bộ lọc sai cú pháp (`size:>abc`, `size:>10 MB`) phát cảnh báo (stderr ở CLI, `warnings` ở MCP) thay vì bỏ qua âm thầm.
- **Lọc danh mục**: `smart-drive search "llama cat:ai_models"`
- **Lọc thư mục**: `smart-drive search "de thi dir:FPTU"`
- **Lọc loại học liệu**: `smart-drive search --kind slide "vòng lặp"` (hoặc `kind:fact`, `--kind capture`, `--kind lesson`)
- **Truy vấn kết hợp**: `smart-drive search "exam ext:pdf size:>1MB dir:DBI202"`

---

## Tài Liệu Học Tập (Aurora Slides Và Polaris)

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

<details>
<summary>Bảng phân loại học liệu được lập chỉ mục & Bảo vệ Learning-Unit nguyên khối</summary>

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

---

## Quản Trị Gitignore

`smart-drive gitignore` kiểm toán và áp dụng `.gitignore` theo stack cho các kho Git trên ổ đĩa (<5ms cho Python, Node.js, Jupyter, Java, Rust, .NET, LaTeX, AI Models, Learning). Hệ thống duy trì khối quản lý idempotent (chạy lại nhiều lần vẫn cho cùng kết quả) giữa `# >>> smart-drive managed (do not edit inside) >>>` và `# <<< smart-drive managed <<<`, bảo toàn quy tắc riêng và CRLF/LF:
- **Phòng thủ Cluster Slack exFAT**: Loại trừ `**/.agents/*` (500+ tệp nháp chiếm >260 MB trên cluster 512 KB; `.agents/skills/`, `.agents/workflows/`, `.agents/rules/` vẫn giữ), `.claude/settings.local.json`, `__pycache__/`, `.pytest_cache/`, `node_modules/`, `target/`/`dist/`/`bin/`/`obj/`.
- **Bảo vệ hợp đồng `personal-data` của Constellation**: Khóa `.vega/`, `**/progress.jsonl`, `aurora-progress-*.jsonl`, `orion-attempts.jsonl`, `**/polaris/.cache/`, `**/polaris/captures/`, `.polaris/` và `.sirius/`, cảnh báo nếu bị track trong repo công khai hoặc chưa có remote.

```bash
smart-drive gitignore audit                                 # kiểm toán repo own & local (--all gồm third-party)
smart-drive gitignore apply D:\teamwork_projects\my_project # xem trước (dry-run)
smart-drive gitignore apply D:\teamwork_projects\my_project --yes --untrack
```

<details>
<summary>Phân loại quyền sở hữu, kiểm tra visibility qua GitHub CLI, bảo toàn file build chủ đích, kiểm toán secret & untrack an toàn</summary>

- **Phân loại quyền sở hữu**: Tự chia thành `own` (khớp `--owner`, `SMART_DRIVE_GIT_OWNER`, `smartdrive.owner` hoặc chủ sở hữu phổ biến nhất), `third-party` (`open_source/`, `vendor/`, `third_party/` hoặc khác chủ sở hữu—tôn trọng `dist/`/`build/` gốc) và `local` (không có remote).
- **Kiểm tra Visibility qua GitHub CLI (`gh`)**: Truy vấn `public`, `private`, `internal` hoặc `missing` ("remote not found on GitHub") có cache theo phiên, tránh báo động giả trên repo private hoặc remote đã xóa.
- **Bảo toàn file build chủ đích & Keep list**: Tệp trong `package.json` (`bin`, `main`, `module`, `types`, `exports`), phủ định (`!dist/bundle.mjs`) hoặc `smartdrive.keep` / `--keep <mẫu>` không bị báo rác hay untrack. Với tệp chủ đích trong `dist/`, khối quản lý sinh `<dir>/*` kèm `!<file>` (hoặc lược bỏ mẫu nếu lồng sâu).
- **Kiểm toán secret & Untrack an toàn**: Phát hiện `.env`, `.env.*`, `*.pem`, `*.key`, `credentials*.json` bị track và **chỉ hiện tên biến / key** (không lộ giá trị). `--untrack` chạy `git rm -r --cached` (giữ tệp trên đĩa) và từ chối nếu có staged chưa commit.

</details>

---

## Dọn Rác An Toàn

Tệp rác chia làm 3 tầng với mặc định chạy thử (`--dry-run`, cần `--apply` để cách ly hoặc xóa thật):
1. **Tier 1 (Rác Metadata Hệ Điều Hành)**: macOS `.DS_Store`, `._*` AppleDouble, `.Spotlight-V100`, `.Trashes`; Windows `Thumbs.db`, `desktop.ini`, `ehthumbs.db`.
2. **Tier 2 (Cache Biên Dịch & Phát Triển)**: Python `__pycache__`, `*.pyc`, `*.pyo`, `.pytest_cache`; Node `node_modules/.cache`; Rust `target/debug/build`; cache cũ `polaris/.cache/**`.
3. **Tier 3 (Tệp Tạm & Nhật Ký)**: `*.log`, `*.tmp`, `*.bak`, crash dumps.

Mặc định, `smart-drive clean --apply` dời tệp vào thư mục cách ly có thể khôi phục tại `<root>/.smart_drive/quarantine/<batch_id>/` (kèm `manifest.json`) thay vì xóa vĩnh viễn:
- Xem các đợt cách ly: `smart-drive clean --list-quarantine`
- Khôi phục một đợt về vị trí gốc: `smart-drive clean --restore latest` (hoặc `--restore <batch_id>`)
- Xóa vĩnh viễn các đợt cách ly cũ: `smart-drive clean --purge-quarantine --older-than 14`
- Xóa thẳng không qua cách ly (hoặc khi ổ đĩa sắp hết chỗ): `smart-drive clean --apply --no-quarantine`

> **Whitelist Bất Khả Xâm Phạm**: Tệp chỉ thị gốc (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`, `PRIVACY.md`), kịch bản khởi chạy (`Setup_*`, `Quick_*`, `Smart_Organize.*`, `SmartDrive.*`), 6 phân vùng gốc, thư mục Windows (`WindowsApps`, `Program Files`, `DumpStack.log.tmp`), thư mục đang được hệ thống dùng (`protected_by_system`: Windows `PATH`, Services, Ứng dụng đã cài, Startup, đích `.lnk`, trình gỡ cài đặt), thư viện game (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`), cơ sở dữ liệu đang chạy (`Microsoft SQL Server 2022`) và `smart-drive-os` được bảo vệ vĩnh viễn.

---

## Snapshot Và Sao Lưu

- **Snapshot SHA-256 (`smart-drive snapshot`)**: Băm tệp theo khối 64 KB (bộ nhớ $O(1)$) vào `.smart_drive/snapshots/<tên>.json` để phát hiện suy thoái dữ liệu (bit rot) hoặc chỉnh sửa trái phép.
- **Sao lưu gia tăng (`smart-drive backup`)**: Chỉ chép tệp mới/thay đổi và loại trừ rác hệ điều hành. Mặc định phủ `02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox` (đổi bằng `--partitions`); bỏ qua thư mục ẩn (`.git`, `.github`, `.vscode`) trừ khi có `--include-hidden`, và luôn liệt kê phần chưa được phủ.
- **Tệp trùng (`smart-drive dup`) & Clone Git (`smart-drive repos`)**: Quét 3 giai đoạn SHA-256 (hard link tính một lần) và kiểm toán chỉ đọc clone Git để kiểm tra **bản đối chiếu** SSD–máy tính (`commit`, `push`, `pull`, `fetch to compare`) cùng **bản trùng** cùng nơi (giữ bản mới nhất, xóa bản dư cùng nhánh, hoặc chuyển thành `git worktree` với `--relative-paths` trên git 2.48+; bản còn thay đổi chưa commit/push hoặc stash bị chặn xóa).

---

## Workstation Hybrid Và Di Chuyển Cache Ổ C

`FilesystemAdapter` và `DriveDetectorBackend` tự thích ứng giữa **exFAT 512 KB** (lá chắn cluster slack, cấm symlink, khiên Spotlight/FSEvents) và **NTFS 4 KB** (tính cụm 4.096 bytes, kiểm tra TRIM qua `fsutil`, dời cache ổ `C:`). `smart-drive offload` chuyển cache lớn (HuggingFace, Ollama, PyTorch, Docker WSL2, pip, uv, npm, Conda, Gradle, Cargo) từ ổ `C:` sang `<target>\04_System_Offload_Caches\<tên>` trên ổ phụ của bạn (`D:`, `E:`…) theo **quy trình giao dịch 7 bước** có kiểm tra tiến trình đang dùng cache (`--wait`, `--force`), rollback tự động và nối bằng NTFS Junction (`mklink /J`) không cần Admin. Xem [README_INTERNAL.md](README_INTERNAL.md).

<details>
<summary>AcademicClassifier (FPTU), Phân luồng Sách/Driver/Installer & Cơ chế Tự Bảo Vệ</summary>

- **AcademicClassifier (FPTU)**: Bóc tách mã môn (`DBI202`, `WED201c`, `SWE202c`, `PRN211`, `PRJ301`, `OSG`), nhận diện đề thi/lab (`PE`, `de thi`, `on luyen`, `lab1_sp26`, `sp26`, `fa25`, `su25`), gom vào `02_Learning_Knowledge/FPTU/Ky_1/` .. `Ky_7/` và sửa mojibake (`h?c k? 3 fptu` $\rightarrow$ `Hoc_Ky_3_fptu`, `n luy?n pe dbi202` $\rightarrow$ `On_Luyen_pe_dbi202`, `k 1 fptu` $\rightarrow$ `Ky_1_fptu`, `dich truyen` $\rightarrow$ `Dich_Truyen`).
- **Phân luồng phi mã nguồn**: `dich truyen` $\rightarrow$ `02_Learning_Knowledge/Personal_Books/dich truyen`, `lenovo` $\rightarrow$ `05_Dev_Toolbox/OEM_Drivers/LENOVO`, `sql2022` $\rightarrow$ `05_Dev_Toolbox/Installers/SQL2022`.
- **Tự Bảo Vệ của AutoZoner & Lá Chắn Hệ Thống (`protected_by_system`)**: Neo `_CURRENT_FILE` tới `_SMART_DRIVE_REPO_DIR` và thư mục cha để `smart-drive-os` không tự dời chính mình; bảo vệ thư mục Windows (`WindowsApps`, `Program Files`, `Program Files (x86)`, `$Recycle.Bin`, `System Volume Information`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`, `DumpStack.log.tmp`), thư mục nằm trong `PATH`, Windows Services, Ứng dụng đã cài (`Uninstall` registry), Startup (`Run`), đích shortcut `.lnk`, thư mục có `unins000.exe`/`uninstall.exe`, thư viện game (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`, `fo4`, `32837`, `Downloads`) và SQL Server (`DBI202_VuPT\MSSQL16.MSSQLSERVER`, `MSSQLSERVER`, `MSSQL`).

</details>

---

## Bộ Khởi Chạy 1-Chạm (Launchers)

24 kịch bản khởi chạy nằm ở gốc dự án và `launchers/` (không cần Git):

| Luồng Tác vụ | Windows CMD | PowerShell | macOS App | Linux/POSIX | Công dụng |
|---|---|---|---|---|---|
| **Khởi tạo Ổ đĩa** | `Setup_SSD.bat` | `Setup_SSD.ps1` | `Setup_SSD.command` | `Setup_SSD.sh` | Hướng dẫn khởi tạo ổ đĩa và chọn profile |
| **Tìm kiếm Nhanh** | `Quick_Search.bat` | `Quick_Search.ps1` | `Quick_Search.command` | `Quick_Search.sh` | Nhập từ khóa tìm kiếm tức thì bằng SQLite FTS5 |
| **Dọn rác Nhanh** | `Quick_Clean.bat` | `Quick_Clean.ps1` | `Quick_Clean.command` | `Quick_Clean.sh` | Xem trước file rác (dry-run) rồi xác nhận cách ly Tier 1 |
| **Kiểm toán Dung lượng** | `Quick_Audit.bat` | `Quick_Audit.ps1` | `Quick_Audit.command` | `Quick_Audit.sh` | Thống kê dung lượng và đo lãng phí cluster slack |
| **Dời An Toàn Có Dẫn Dắt** | `Smart_Organize.bat` | `Smart_Organize.ps1` | `Smart_Organize.command` | `Smart_Organize.sh` | Xem trước dry-run, chờ đóng ứng dụng bận, xác nhận `YES`, kiểm tra sau dời và hoàn tác 1 phím `U` (EN/VI) |
| **Menu Tương tác** | `SmartDrive.bat` | `SmartDrive.ps1` | `SmartDrive.command` | `SmartDrive.sh` | Menu TUI đầy đủ (gồm mục `[8]` Relink Agents, `[9]` Checkup, `[10]` Smart_Organize) |

`smart-drive self-path-check` kiểm tra môi trường Python (venv, pyenv, user) và in lệnh PowerShell `SetEnvironmentVariable`, `export` cho `~/.bashrc`/`~/.zshrc` hoặc `pyenv rehash` nếu `smart-drive` chưa có trong `PATH`.

---

## Cấu Hình Mẫu (Profiles)

| Cấu hình mẫu | Đối tượng mục tiêu | Cấu trúc phân vùng & Thư mục con |
|---|---|---|
| **`workstation-hybrid`** *(Khuyên dùng)* | Ổ phụ của bạn (`D:`, `E:`…) chứa game, ứng dụng Windows, bài học | `01_AI_Models`, `02_Learning_Knowledge` (`FPTU/`, `Personal_Books/`, `Notes/`), `03_Development_Projects` (`active/`, `archive/`), `04_System_Workspaces`, `05_Dev_Toolbox` (`OEM_Drivers/`, `Installers/`, `scripts/`), `06_Archives_Storage` |
| **`internal-developer-vault`** | Ổ phụ NVMe/SATA (`D:`, `E:`…) nhận cache chuyển từ C: | `01_AI_Models`, `02_Development_Workspaces`, `03_Data_Vault`, `04_System_Offload_Caches` (`huggingface/`, `ollama/`, `pip/`, `uv/`), `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`ai-developer`** | Ổ SSD di động cho LLM, checkpoint, AI agent | `01_AI_Models` (`checkpoints/`, `gguf/`, `safetensors/`, `datasets/`), `02_Learning_Knowledge`, `03_Development_Projects` (`ai_agents/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`data-science`** | Kỹ sư dữ liệu, pipeline và Jupyter Notebook | `01_AI_Models`, `02_Learning_Knowledge`, `03_Development_Projects` (`notebooks/`, `data_raw/`, `pipelines/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`general-workspace`** *(Mặc định)* | Lập trình tổng hợp, ghi chú, tài liệu học tập | 6 nhóm thư mục chuẩn kèm `Notes/`, `References/`, `active/`, `archive/`, `scripts/` |

---

## Kiểm Thử

1.150+ bài kiểm thử tự động chạy trên CI với Windows, macOS và Linux (Python 3.9 và 3.13) bằng `unittest`:

```bash
python -m unittest discover tests -v
```

Bộ kiểm thử bao phủ quản trị `.gitignore` (quyền sở hữu, file build chủ đích, phủ định `!`, danh sách `keep`, visibility qua `gh`, kiểm toán secret chỉ hiện tên biến, bảo toàn CRLF, chặn khi có staged changes), bảo vệ Learning Units, bảo vệ tham chiếu hệ thống (`protected_by_system`), cập nhật đường dẫn agent/IDE/Obsidian/shortcut (`storage.json`, `state.vscdb`, `recentProjects.xml`, `.lnk`, Claude desktop MSIX, `--find`, `--wait`), `undo`, `checkup`, `paths --broken`, cách ly/khôi phục `clean`, xuất treemap `audit --html`, `Smart_Organize` (`SD_SELFTEST=1`), mô phỏng cluster (512 B–32 MB), tự bảo vệ và `AcademicClassifier`.

---

## Bảo Mật & Quyền Riêng Tư Dữ Liệu

SmartDrive-OS tuân thủ nguyên tắc **Local-First & Quyền Riêng Tư Tối Đa**:
- **Hoạt động 100% Cục bộ (Local-Only)**: Quét ổ đĩa, chỉ mục SQLite FTS5 và xác thực snapshot SHA-256 diễn ra trực tiếp trên máy của bạn.
- **Zero Telemetry & Zero PII Logging**: Không thu thập hành vi, không kết nối ngầm, không đọc mã nguồn bí mật, token hay private key; chỉ lưu metadata tệp trong `.smart_drive/index.db`.
- **Sẵn sàng cho Air-Gap & Khiên Bảo vệ**: Không có dependencies ngoài (`dependencies = []`), MCP qua `stdio`, `smart-drive setup --no-clone` ngoại tuyến, mặc định chạy thử khi dọn rác và bảo vệ vĩnh viễn tệp cốt lõi (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `PRIVACY.md`).

Xem chi tiết tại [PRIVACY.md](PRIVACY.md).

---

## Đóng Góp Mã Nguồn & Giấy Phép

- 🌟 **Star & Fork** trên GitHub: [DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os) · 🐛 [GitHub Issues](https://github.com/DuongNAD/smart-drive-os/issues) · 🔀 [Pull Requests](https://github.com/DuongNAD/smart-drive-os/pulls) · 📖 [CONTRIBUTING.md](CONTRIBUTING.md)
- 📄 Phân phối theo **Giấy phép MIT** — xem [LICENSE](LICENSE).
