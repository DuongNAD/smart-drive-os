# SmartDrive-OS — Hướng Dẫn Sử Dụng (Tiếng Việt)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Zero Pip Dependencies](https://img.shields.io/badge/dependencies-0%20external%20pip-success.svg)](#)
[![exFAT 512KB Optimized](https://img.shields.io/badge/filesystem-exFAT%20512KB%20Guard-orange.svg)](#)
[![MCP Protocol](https://img.shields.io/badge/MCP-JSON--RPC%202.0%20stdio-purple.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Hệ điều phối & Quản trị ổ cứng SSD di động (exFAT) tối ưu hóa cho AI Coding Agents và lập trình viên.**

---

## 1. Vấn đề cốt lõi: "Cạm bẫy" Cluster Slack 512KB trên exFAT

Các dòng ổ cứng SSD di động dung lượng lớn (như **Kingston XS2000 2TB**) khi định dạng theo chuẩn **exFAT** thường mặc định kích thước đơn vị phân bổ (cluster size) lên đến **512 KB (524.288 bytes)**.

Điều này gây lãng phí dung lượng nghiêm trọng đối với lập trình viên:
- Một file mã nguồn hoặc tệp cấu hình chỉ nặng **1 byte** vẫn chiếm trọn **512 KB** trên ổ đĩa (**lãng phí 99.999%**).
- Một file JSON hoặc markdown nặng **10 KB** vẫn ngốn **512 KB** (**lãng phí 98.05%**).
- Một thư mục dự án chứa **10.000 file nhỏ** (tổng dung lượng thực chỉ **15 MB**) khi sao chép sang SSD exFAT sẽ chiếm dụng hơn **5.12 GB** dung lượng vật lý!
- Trình lập chỉ mục của hệ điều hành (macOS Spotlight `mds`, Windows Search) liên tục quét và ghi đè các file rác nhỏ (`.DS_Store`, `Thumbs.db`), gây nóng ổ, hao pin và giảm tuổi thọ chip nhớ.

**SmartDrive-OS** giải quyết triệt để vấn đề này với triết lý **Zero-Dependency** (100% Python Standard Library, không cần cài bất kỳ thư viện ngoài nào), tự động cài đặt khiên chống quét rác, tích hợp công cụ tìm kiếm SQLite FTS5 siêu tốc (<10ms) và giao thức Model Context Protocol (MCP) chuẩn hóa cho AI Coding Agents.

---

## 2. Khởi động nhanh trong 3 bước

### Bước 1: Chuẩn bị môi trường
Dự án chỉ yêu cầu máy tính có sẵn **Python 3.9+** (không cần cài thêm bất kỳ thư viện pip nào):
```bash
python --version
```

### Bước 2: Khởi tạo ổ đĩa 1-chạm
Bạn có thể nhấp đúp chuột vào tệp khởi chạy nhanh:
- **Windows**: Nhấp đúp vào `Setup_SSD.bat`
- **macOS / Linux**: Nhấp đúp vào `Setup_SSD.command`

Hoặc chạy lệnh từ dòng lệnh:
```bash
python -m smart_drive init --profile ai-developer
```

#### 3 Cấu hình mẫu có sẵn:
1. `ai-developer`: Phù hợp lập trình AI/LLM, chứa sẵn thư mục mô hình (`checkpoints`, `gguf`, `safetensors`), môi trường ảo có gắn shield `.noindex`, thư mục workspace cho AI agent.
2. `data-science`: Phù hợp phân tích dữ liệu, sổ tay Jupyter notebook, pipeline dữ liệu, hướng dẫn lưu trữ định dạng Parquet thay cho CSV nhỏ.
3. `general-workspace` (Mặc định): Cấu trúc tiêu chuẩn cho lập trình tổng quát, tài liệu học tập, sách và công cụ tiện ích.

### Bước 3: Kết nối với AI Coding Agent (MCP)
Tự động thêm cấu hình MCP Server vào IDE của bạn bằng một lệnh duy nhất:
```bash
python -m smart_drive mcp-config
```
Hỗ trợ trực tiếp: **Google Antigravity 2.0**, **Claude Desktop / Claude Code**, **Cursor IDE**, và **Windsurf**.

---

## 3. Bộ khởi chạy 1-chạm (Cross-Platform Launchers)

Các tệp khởi chạy có sẵn ở cả thư mục gốc và thư mục `launchers/` để bạn sử dụng ngay:

| Tệp Windows (`.bat`) | Tệp macOS / Linux (`.command`) | Chức năng |
|---|---|---|
| `Setup_SSD.bat` | `Setup_SSD.command` | Khởi tạo ổ đĩa, tạo 6 nhóm thư mục và cài khiên bảo vệ. |
| `Quick_Search.bat` | `Quick_Search.command` | Tìm kiếm tệp tin tức thì bằng SQLite FTS5. |
| `Quick_Clean.bat` | `Quick_Clean.command` | Quét xem trước file rác (Dry-run) và dọn dẹp an toàn khi xác nhận. |
| `Quick_Audit.bat` | `Quick_Audit.command` | Thống kê cấu trúc dung lượng và đo lường lãng phí cluster slack. |

---

## 4. Bảng tra cứu các lệnh CLI

Chạy qua lệnh `smart-drive <lệnh>` hoặc `python -m smart_drive <lệnh>`:

| Lệnh | Tham số chính | Mô tả chi tiết |
|---|---|---|
| `init` | `--profile`, `--root`, `--force`, `--json` | Khởi tạo ổ đĩa, tạo cấu trúc phân loại, cài khiên và cơ sở dữ liệu FTS5. |
| `status` | `--root`, `--json` | Kiểm tra tình trạng điểm gắn ổ đĩa, khiên bảo vệ và các thư mục nghiệp vụ. |
| `audit` | `--root`, `--json`, `--markdown`, `--export` | Kiểm toán chi tiết dung lượng và tỷ lệ lãng phí cluster slack 512KB. |
| `clean` | `--dry-run`, `--apply`, `--tier {1,2,3}` | Dọn dẹp rác hệ thống (mặc định luôn chạy mô phỏng trước, bảo vệ whitelist). |
| `search` | `<từ_khóa>`, `--ext`, `--size`, `--category` | Tìm kiếm siêu tốc (<10ms) với bộ lọc đa tiêu chí và thuật toán BM25. |
| `organize` | `--dry-run`, `--apply`, `--clean` | Tự động phân loại file tự do vào đúng thư mục nghiệp vụ để giảm slack. |
| `sentinel` | `--root`, `--auto-heal`, `--json` | Kiểm tra sức khỏe ổ cứng, phục hồi khiên bảo vệ bị thiếu (alias: `agent-check`). |
| `mcp` | `--root` | Khởi chạy máy chủ MCP Server stdio chuẩn JSON-RPC 2.0 cho AI Agent. |
| `mcp-config`| `--target-dir`, `--antigravity`, `--claude`, v.v. | Tự động cấu hình MCP vào file thiết lập của các IDE. |
| `dup` | `--root`, `--json` | Tìm file trùng lặp qua 3 giai đoạn (kích thước -> băm nhanh 8KB -> SHA-256). |

---

## 5. Cú pháp tìm kiếm nâng cao

Công cụ tìm kiếm hỗ trợ lọc linh hoạt:
- Tìm theo từ khóa: `smart-drive search "deep learning"`
- Lọc theo đuôi mở rộng: `smart-drive search "weights ext:gguf"`
- Lọc theo kích thước: `smart-drive search "dataset size:>50MB"` (hoặc `size:<500KB`)
- Lọc theo danh mục: `smart-drive search "transformer cat:ai_models"`
- Lọc theo thư mục con: `smart-drive search "setup dir:03_Development_Projects"`

---

## 6. Cơ chế dọn dẹp rác an toàn tuyệt đối (Safe Cleaner)

Hệ thống phân tầng rác làm 3 cấp độ:
- **Tier 1 (An toàn 100%)**: File rác hệ điều hành macOS `.DS_Store`, `._*` AppleDouble; Windows `Thumbs.db`, `desktop.ini`, `.Spotlight-V100`.
- **Tier 2 (Cache phát triển)**: Python `__pycache__`, `*.pyc`, `.pytest_cache`, Node cache, Rust build artifacts.
- **Tier 3 (File tạm thời)**: `*.log`, `*.tmp`, crash dumps.

> **Danh sách bảo vệ bất khả xâm phạm (Whitelist Guard)**: Các tệp chỉ thị gốc (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`), các script khởi chạy (`Setup_*.bat`, `Setup_*.command`), và 6 thư mục phân loại gốc **không bao giờ bị xóa** trong bất kỳ trường hợp nào.

---

## 7. Đóng góp mã nguồn mở (Contributing)

SmartDrive-OS là dự án **hoàn toàn mở** và chào đón mọi sự đóng góp từ cộng đồng:
- 🌟 **Star & Fork** repository trên GitHub: [DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os)
- 🐛 Báo lỗi hoặc đề xuất ý tưởng mới tại [GitHub Issues](https://github.com/DuongNAD/smart-drive-os/issues)
- 🔀 Gửi code cải tiến qua [Pull Requests](https://github.com/DuongNAD/smart-drive-os/pulls)
- 📖 Xem hướng dẫn quy trình đóng góp tại [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 8. Giấy phép mã nguồn mở (License)

Dự án được phát hành tự do 100% theo **Giấy phép MIT** (xem chi tiết tại tệp [LICENSE](LICENSE)).  
Bạn và bất kỳ ai trong cộng đồng đều có toàn quyền **sử dụng, sao chép, chỉnh sửa, ghép nối, phân phối hoặc dùng cho mục đích thương mại** hoàn toàn miễn phí và không bị ràng buộc.
