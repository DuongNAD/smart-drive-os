# SmartDrive-OS — Hướng Dẫn Sử Dụng (Tiếng Việt)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Zero Pip Dependencies](https://img.shields.io/badge/dependencies-0%20external%20pip-success.svg)](#)
[![Privacy: 100% Cục bộ](https://img.shields.io/badge/Privacy-100%25%20C%E1%BB%A5c%20b%E1%BB%99-success?style=flat-square&logo=shield)](PRIVACY.md)
[![Kiến trúc: Workstation Hybrid](https://img.shields.io/badge/ki%E1%BA%BFn%20tr%C3%BAc-Workstation%20Hybrid%20Native-blueviolet.svg)](#)
[![Hệ thống tệp: exFAT 512KB Guard](https://img.shields.io/badge/h%E1%BB%87%20th%E1%BB%91ng%20t%E1%BB%87p-exFAT%20512KB%20Guard-orange.svg)](#)
[![Hệ thống tệp: NTFS 4KB Native](https://img.shields.io/badge/h%E1%BB%87%20th%E1%BB%91ng%20t%E1%BB%87p-NTFS%204KB%20Native-cyan.svg)](#)
[![Giao thức MCP: JSON-RPC 2.0](https://img.shields.io/badge/MCP-JSON--RPC%202.0%20stdio-purple.svg)](https://modelcontextprotocol.io/)
[![Kiểm định Bảo mật MCP: Hạng A (100/100)](https://img.shields.io/badge/MCP%20Audit-H%E1%BA%A1ng%20A%20(100%2F100)-brightgreen.svg)](#)
[![M8ven Score](https://m8ven.ai/badge/mcp/duongnad-smart-drive-os-1kxkwu)](https://m8ven.ai/mcp/duongnad-smart-drive-os)
[![CI](https://github.com/DuongNAD/smart-drive-os/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/DuongNAD/smart-drive-os/actions/workflows/tests.yml)
[![Kiểm thử: 915/915 Vượt qua (100%)](https://img.shields.io/badge/ki%E1%BB%83m%20th%E1%BB%AD-915%2F915%20passed%20(100%25)-brightgreen.svg)](#)
[![20 Launcher Portable](https://img.shields.io/badge/launchers-20%20t%E1%BB%87p%20kh%E1%BB%9Fi%20ch%E1%BA%A1y-blue.svg)](#)
[![Phiên bản: v1.1.0](https://img.shields.io/badge/phi%C3%AAn%20b%E1%BA%A3n-v1.1.0-blue.svg)](https://github.com/DuongNAD/smart-drive-os/releases/tag/v1.1.0)
[![Giấy phép: MIT](https://img.shields.io/badge/Gi%E1%BA%A5y%20ph%C3%A9p-MIT-yellow.svg)](LICENSE)

> **Hệ điều hành Quản trị Dữ liệu Tự hành Máy trạm (Workstation Hybrid), Hệ thống Snapshot Toàn vẹn Dữ liệu SHA-256 và Công cụ Tìm kiếm Siêu tốc SQLite FTS5 (<10ms) dành cho Ổ cứng Di động Ngoài (exFAT), Ổ phụ Gắn trong (NTFS) và các Trợ lý Lập trình AI.**
>
> 🌐 **English Documentation**: Full GitHub repository documentation is available at [README.md](README.md)
>
> 🚀 **Kiến trúc Ổ phụ Gắn trong & Di chuyển Cache Ổ C:**: Xem chi tiết hướng dẫn quy hoạch ổ phụ NVMe/SATA (`D:`, `E:`), giải phóng hàng chục GB ổ C: qua NTFS Directory Junctions (`mklink /J`) và giám sát SSD TRIM: [README_INTERNAL.md](README_INTERNAL.md)

---

## Mục lục

1. [Vấn đề cốt lõi: "Cạm bẫy" Hình học Ổ đĩa Kép](#1-vấn-đề-cốt-lõi-cạm-bẫy-hình-học-ổ-đĩa-kép)
   - [Cạm bẫy Cluster Slack 512KB trên exFAT](#cạm-bẫy-cluster-slack-512kb-trên-exfat)
   - [Thách thức Ổ phụ Gắn trong Máy trạm (NTFS 4KB)](#thách-thức-ổ-phụ-gắn-trong-máy-trạm-ntfs-4kb)
   - [Bảng So sánh Trực quan: Ổ ngoài exFAT vs. Ổ trong NTFS](#bảng-so-sánh-trực-quan-ổ-ngoài-exfat-vs-ổ-trong-ntfs)
2. [Kiến trúc Tổng thể Workstation Hybrid](#2-kiến-trúc-tổng-thể-workstation-hybrid)
3. [Các Tính Năng & Đột Phá Nổi Bật](#3-các-tính-năng--đột-phá-nổi-bật)
   - [1. Kiến trúc Workstation Hybrid & Tự Thích ứng Hình học Ổ cứng](#1-kiến-trúc-workstation-hybrid--tự-thích-ứng-hình-học-ổ-cứng)
   - [2. AcademicClassifier & Bản địa hóa Môn học Đại học Việt Nam (FPTU)](#2-academicclassifier--bản-địa-hóa-môn-học-đại-học-việt-nam-fptu)
   - [3. Tự động Phân luồng Sách Cá Nhân, Trình điều khiển OEM & Bộ Cài đặt](#3-tự-động-phân-luồng-sách-cá-nhân-trình-điều-khiển-oem--bộ-cài-đặt)
   - [4. Cơ chế Tự Bảo Vệ Bất Biến & Vùng An toàn Ứng dụng / Game / Database](#4-cơ-chế-tự-bảo-vệ-bất-biến--vùng-an-toàn-ứng-dụng--game--database)
   - [5. Tiện ích Kiểm tra Biến Môi trường & PATH (`self-path-check`)](#5-tiện-ích-kiểm-tra-biến-môi-trường--path-self-path-check)
   - [6. Máy chủ MCP Server Hạng A (100/100) & Tối ưu Token cho AI Coding Agents](#6-máy-chủ-mcp-server-hạng-a-100100--tối-ưu-token-cho-ai-coding-agents)
   - [7. Bộ 20 Launcher Portable Độc lập Đa Nền tảng](#7-bộ-20-launcher-portable-độc-lập-đa-nền-tảng)
   - [8. Động cơ Di chuyển Cache Ổ C: & NTFS Directory Junctions (`smart-drive offload`)](#8-động-cơ-di-chuyển-cache-ổ-c--ntfs-directory-junctions-smart-drive-offload)
   - [9. Động cơ Snapshot SHA-256 Dạng Dòng & Sao lưu Tăng số](#9-động-cơ-snapshot-sha-256-dạng-dòng--sao-lưu-tăng-số)
   - [10. Cam kết 100% Zero External Pip Dependencies](#10-cam-kết-100-zero-external-pip-dependencies)
4. [Khởi Động Nhanh Trong 3 Bước](#4-khởi-động-nhanh-trong-3-bước)
5. [5 Cấu Hình Mẫu Chuyên Biệt (Preset Profiles)](#5-5-cấu-hình-mẫu-chuyên-biệt-preset-profiles)
6. [Hướng Dẫn Sử Dụng Bộ Khởi Chạy 1-Chạm (20 Launchers)](#6-hướng-dẫn-sử-dụng-bộ-khởi-chạy-1-chạm-20-launchers)
7. [Bảng Tra Cứu Toàn Diện Lệnh CLI](#7-bảng-tra-cứu-toàn-diện-lệnh-cli)
8. [Cú Pháp Tìm Kiếm Nâng Cao](#8-cú-pháp-tìm-kiếm-nâng-cao)
9. [Cơ Chế Dọn Rác An Toàn 3 Tầng & Danh Sách Bảo Vệ Whitelist](#9-cơ-chế-dọn-rác-an-toàn-3-tầng--danh-sách-bảo-vệ-whitelist)
10. [Tích Hợp Máy Chủ MCP Với Các Trợ Lý AI Lập Trình](#10-tích-hợp-máy-chủ-mcp-với-các-trợ-lý-ai-lập-trình)
11. [Báo Cáo Kiểm Thử Tự Động (1033 Bài Test, Pass 100%)](#11-báo-cáo-kiểm-thử-tự-động-1033-bài-test-pass-100)
12. [Bảo Mật & Quyền Riêng Tư Dữ Liệu](#12-bảo-mật--quyền-riêng-tư-dữ-liệu)
13. [Đóng Góp Mã Nguồn & Giấy Phép](#13-đóng-góp-mã-nguồn--giấy-phép)

---

## 1. Vấn đề cốt lõi: "Cạm bẫy" Hình học Ổ đĩa Kép

Lập trình viên, kỹ sư AI và sinh viên kỹ thuật thường sử dụng kết hợp hai loại không gian lưu trữ với đặc tính phần cứng hoàn toàn trái ngược nhau:

### Cạm bẫy Cluster Slack 512KB trên exFAT

Các dòng ổ cứng SSD di động dung lượng cao (tiêu biểu như **Kingston XS2000 2TB**) khi xuất xưởng hoặc định dạng theo chuẩn **exFAT** thường thiết lập kích thước cụm phân bổ mặc định lên đến **512 KB (524.288 bytes)**. Kích thước này rất tốt cho video 4K/8K, nhưng lại là thảm họa đối với các thư mục dự án phần mềm gồm hàng nghìn file mã nguồn nhỏ:

$$\text{Dung lượng Vật lý} = \left\lceil \frac{\text{Kích thước File}}{524.288} \right\rceil \times 524.288 \text{ bytes}$$

$$\text{Dung lượng Lãng phí (Slack)} = \text{Dung lượng Vật lý} - \text{Dung lượng Danh nghĩa}$$

| Dung lượng danh nghĩa file | Dung lượng vật lý chiếm dụng | Dung lượng lãng phí (Slack) | Tỷ lệ lãng phí |
|---|---|---|---|
| **0 bytes** | 0 bytes (ghi vào bảng thư mục) | 0 bytes | 0.0% |
| **1 byte** | **524.288 bytes** (512 KB) | **524.287 bytes** | **99.9998%** |
| **10 KB** (code / config) | **524.288 bytes** (512 KB) | **514.048 bytes** | **98.05%** |
| **100 KB** (JSON / docs) | **524.288 bytes** (512 KB) | **421.888 bytes** | **80.47%** |
| **524.289 bytes** (512 KB + 1B) | **1.048.576 bytes** (1.024 KB) | **524.287 bytes** | **50.00%** |

- **Thiệt hại thực tế**: Một dự án chứa **10.000 file nhỏ** với tổng dung lượng chỉ **15 MB** sẽ nuốt chửng tới **5.12 GB** dung lượng vật lý thực tế trên SSD exFAT — **lãng phí hơn 99%** ổ đĩa!
- Đồng thời, các tiến trình lập chỉ mục ngầm của hệ điều hành (macOS Spotlight `mds`, Windows Search) liên tục ghi tệp metadata rác (`.DS_Store`, `Thumbs.db`, `.fseventsd`), gây nóng ổ đĩa, hao pin và giảm tuổi thọ chip nhớ SSD.

### Thách thức Ổ phụ Gắn trong Máy trạm (NTFS 4KB)

Ngược lại, trên máy trạm (PC/Laptop) có ổ phụ gắn trong NVMe/SATA (`D:`, `E:`) định dạng **NTFS 4KB**, người dùng gặp phải bài toán hoàn toàn khác:
- Ổ hệ thống (`C:`) liên tục bị đầy bởi các thư mục cache khổng lồ (HuggingFace models, Ollama weights, Docker WSL2, pip, uv, npm, Gradle cache) với dung lượng từ 50GB đến hàng trăm GB.
- Di chuyển thủ công sẽ làm gãy đường dẫn của phần mềm, còn tạo symlink thông thường (`mklink /D`) lại đòi hỏi quyền Administrator hoặc Developer Mode.
- Ổ phụ `D:` thường dùng chung cho cả game (`SteamLibrary`, `Riot Games`, `LDPlayer`), ứng dụng Windows (`WindowsApps`, `Program Files`), cơ sở dữ liệu đang hoạt động (`Microsoft SQL Server 2022`) và tài liệu học tập của sinh viên. Các công cụ dọn dẹp mù quáng nếu can thiệp vào các thư mục này sẽ làm hỏng dữ liệu hoặc làm sập database.

### Bảng So sánh Trực quan: Ổ ngoài exFAT vs. Ổ trong NTFS

| Tiêu chí | Ổ Di động Ngoài (exFAT) | Ổ Phụ Gắn trong (NTFS) |
|---|---|---|
| **Điểm gắn thông thường** | `/Volumes/KINGSTON` hoặc `E:\` | `D:\` hoặc `E:\` (Ổ đĩa cố định) |
| **Kích thước Cluster** | **512 KB** (524.288 bytes) | **4 KB** (4.096 bytes) |
| **Vấn đề cốt lõi** | Lãng phí cluster slack cực lớn trên file nhỏ | Ổ C: cạn kiệt dung lượng do cache AI & Dev |
| **Hỗ trợ Symlink** | ❌ Không hỗ trợ (gây lỗi đa nền tảng) | ⚡ Hỗ trợ qua NTFS Directory Junctions (`mklink /J`) |
| **Chiến lược tối ưu** | Đóng gói file vi mô, khiên chống quét rác | Di chuyển cache 7 bước, bảo vệ dịch vụ đang chạy |
| **Đối tượng bảo vệ** | Manifests gốc, scripts khởi chạy, 6 taxonomy | Ứng dụng Windows, Game libraries, Database SQL Server |

---

## 2. Kiến trúc Tổng thể Workstation Hybrid

SmartDrive-OS thống nhất mọi thao tác trên cả hai loại ổ đĩa với kiến trúc thuần Python Standard Library 100%:

```
+---------------------------------------------------------------------------------------------------------+
|                                    Giao diện Người dùng & AI Agents                                     |
|   +-----------------------+   +-----------------------+   +---------------------+                       |
|   |  20 Launchers Tự hành |   |   Python CLI & TUI    |   |  Trợ lý Lập trình AI|                       |
|   | (.bat/.ps1/.cmd/.sh)  |   | (smart-drive CLI/TUI) |   | (Antigravity/Claude)|                       |
|   +-----------+-----------+   +-----------+-----------+   +----------+----------+                       |
+---------------|---------------------------|--------------------------|----------------------------------+
                |                           |                          |
                +---------------------------+                          | JSON-RPC 2.0 stdio
                                            |                          v
                                            v             +-----------------------------------------------+
+---------------------------------------------------------|        Máy chủ MCP Server Hạng A (100/100)    |
|                                                         |  - 100% Phân tích tĩnh AST tách biệt handler |
|                   Nhân Điều Hành SmartDrive-OS          |  - Định dạng JSON nén có phân trang tối ưu    |
|                                                         |  - Xác thực Token Handshake HMAC bất biến     |
|  +--------------------------------+  +------------------+  - Giao tiếp stdio, không mở cổng mạng        |
|  | Drive Initializer              |  | Storage Auditor  +-----------------------------------------------+
|  | - 5 Cấu hình mẫu chuyên biệt   |  | - Thích ứng hình học (512KB exFAT vs 4KB NTFS)                   |
|  | - Cài khiên chống quét rác    |  | - Đo lường chính xác Cluster Slack và dung lượng lãng phí        |
|  +--------------------------------+  +------------------------------------------------------------------+
|  +--------------------------------+  +------------------------------------------------------------------+
|  | AcademicClassifier & Bản địa   |  | AutoZoner & Cơ chế Tự Bảo Vệ Bất Biến                            |
|  | - Bóc tách mã môn học FPTU     |  | - Neo chặt thư mục mã nguồn chạy (_SMART_DRIVE_REPO_DIR)         |
|  | - Phục hồi lỗi font Mojibake   |  | - Bảo vệ ứng dụng Windows (WindowsApps, Program Files)           |
|  | - Gom nhóm học kỳ (Ky_1-7)     |  | - Bảo vệ Game (SteamLibrary, Riot Games, LDPlayer)               |
|  | - Phân loại Sách & Driver OEM  |  | - Bảo vệ Database đang chạy (Microsoft SQL Server 2022)          |
|  +--------------------------------+  +------------------------------------------------------------------+
|  +--------------------------------+  +------------------------------------------------------------------+
|  | Động cơ Dọn Rác An Toàn        |  | Động cơ Di Chuyển Cache Ổ C: & NTFS Junctions                    |
|  | - Phân loại rác 3 tầng rõ ràng |  | - Quy trình chuyển giao 7 bước với cơ chế hoàn tác Rollback      |
|  | - Mặc định Dry-Run an toàn     |  | - Điểm nối phần cứng mklink /J không cần quyền Admin             |
|  | - Khiên Whitelist bất khả xâm  |  | - Mục tiêu: HuggingFace, Ollama, PyTorch, Docker, Pip, Npm, v.v. |
|  +--------------------------------+  +------------------------------------------------------------------+
|  +--------------------------------+  +------------------------------------------------------------------+
|  | Tìm kiếm Tức thì SQLite FTS5   |  | Động cơ Snapshot SHA-256 Dạng Dòng & Sao lưu                     |
|  | - Phân tích từ tố unicode61    |  | - Băm SHA-256 khối 64KB với bộ nhớ O(1) chống suy thoái tệp      |
|  | - Phản hồi đa tiêu chí <10ms   |  | - Bản kê khai điểm phục hồi & Kiểm toán tính toàn vẹn            |
|  +--------------------------------+  +------------------------------------------------------------------+
+---------------------------------------------------------------------------------------------------------+
                                            |
                   +------------------------+------------------------+
                   |                                                 |
                   v                                                 v
+-------------------------------------------------+ +-----------------------------------------------------+
| Ổ 1: Ổ Di Động Ngoài (exFAT 512KB)              | | Ổ 2: Ổ Phụ Gắn Trong Máy Trạm (NTFS 4KB)            |
|  01_AI_Models/        02_Learning_Knowledge/    | |  01_AI_Models/        02_Learning_Knowledge/ (FPTU) |
|  03_Development_Proj/ 04_System_Workspaces/     | |  03_Development_Proj/ 04_System_Workspaces/         |
|  05_Dev_Toolbox/      06_Archives_Storage/      | |  05_Dev_Toolbox/      06_Archives_Storage/          |
|  .metadata_never_index .fseventsd/no_log        | |  [BẢO VỆ]: WindowsApps, Program Files, SteamLibrary |
|  .smart_drive/index.db (Chỉ mục tìm kiếm FTS5)  | |  Riot Games, LDPlayer, SQL2022, Downloads, fo4      |
|  .smart_drive/snapshots/<manifest>.json         | |  [DATABASE]: DBI202_VuPT\MSSQL16.MSSQLSERVER        |
+-------------------------------------------------+ +-----------------------------------------------------+
```

---

## 3. Các Tính Năng & Đột Phá Nổi Bật

### 1. Kiến trúc Workstation Hybrid & Tự Thích ứng Hình học Ổ cứng
SmartDrive-OS tự động nhận diện phần cứng và cấu trúc phân vùng thông qua `FilesystemAdapter` và `DriveDetectorBackend`:
- **Ổ đĩa exFAT (Cluster 512KB)**: Kích hoạt bộ kiểm soát **exFAT 512KB Cluster Slack Guard**, thực thi nghiêm ngặt lệnh cấm symlink, tự động gieo các khiên chặn Spotlight và FSEvents (`.metadata_never_index`, `.fseventsd/no_log`), và khuyến nghị đóng gói các tệp siêu nhỏ.
- **Ổ đĩa NTFS (Cluster 4KB)**: Tự động điều chỉnh công thức tính cluster slack về kích thước 4.096 bytes tiêu chuẩn, cho phép tạo các liên kết phần cứng NTFS Directory Junctions (`mklink /J`), kiểm tra sức khỏe lệnh TRIM hệ thống (`fsutil behavior query DisableDeleteNotify`), và kích hoạt tính năng chuyển dọn cache từ ổ C:.

### 2. AcademicClassifier & Bản địa hóa Môn học Đại học Việt Nam (FPTU)
Được phát triển đặc biệt cho sinh viên đại học kỹ thuật, giảng viên và lập trình viên Việt Nam (với khả năng am hiểu tường tận chương trình đào tạo của **Đại học FPT / FPTU**):
- **Bóc tách mã môn học qua Regex chuyên sâu**: Tự động nhận diện chính xác các mã môn học chuẩn (`DBI202`, `WED201c`, `SWE202c`, `PRN211`, `PRJ301`, `OSG`, v.v.).
- **Nhận diện tài liệu học tập & thi cử**: Nhận diện các thư mục luyện thi Practical Exam (`PE`, `de thi`, `on luyen`, `pe_dbi202`), bài thực hành lab (`lab1_sp26`, `testjava`), và các kỳ học (`sp26`, `fa25`, `su25`).
- **Tự động gom nhóm học kỳ**: Tự động quy hoạch tài liệu vào đúng cấu trúc học kỳ chuẩn `02_Learning_Knowledge/FPTU/Ky_1/` đến `Ky_7/`.
- **Khôi phục và chuẩn hóa lỗi font tiếng Việt Mojibake**: Tự động nhận diện và sửa chữa các tên thư mục bị hỏng font (dấu chấm hỏi `?`) do giải nén tệp zip cũ hoặc phần mềm nén không tương thích UTF-8:
  - `h?c k? 3 fptu` $\rightarrow$ `Hoc_Ky_3_fptu`
  - `n luy?n pe dbi202` $\rightarrow$ `On_Luyen_pe_dbi202`
  - `k 1 fptu` $\rightarrow$ `Ky_1_fptu`
  - `dich truyen` $\rightarrow$ `Dich_Truyen`

### 3. Tự động Phân luồng Sách Cá Nhân, Trình điều khiển OEM & Bộ Cài đặt
Hệ thống tự động nhận diện và quy hoạch các thư mục phi mã nguồn thường thấy trên máy cá nhân:
- `dich truyen` (truyện dịch, sách đọc cá nhân) $\rightarrow$ `02_Learning_Knowledge/Personal_Books/dich truyen`
- `lenovo` (driver cứu hộ và phần mềm OEM) $\rightarrow$ `05_Dev_Toolbox/OEM_Drivers/LENOVO`
- `sql2022` (gói cài đặt cơ sở dữ liệu) $\rightarrow$ `05_Dev_Toolbox/Installers/SQL2022`

### 4. Cơ chế Tự Bảo Vệ Bất Biến & Vùng An toàn Ứng dụng / Game / Database
Để loại trừ 100% rủi ro khi chạy lệnh tự động sắp xếp trên thư mục gốc của ổ cứng máy trạm, SmartDrive-OS được trang bị **lá chắn bảo vệ đa tầng**:
- **Tự bảo vệ mã nguồn dự án**: AutoZoner ghi nhớ chính xác đường dẫn tệp thực thi (`_CURRENT_FILE`), module lõi (`_SMART_DRIVE_CORE_DIR`), thư mục gói (`_SMART_DRIVE_PKG_DIR`), thư mục gốc repo (`_SMART_DRIVE_REPO_DIR`) và toàn bộ các thư mục cha. Hàm `classify_item()` đảm bảo `smart-drive-os` **tuyệt đối không bao giờ bị di chuyển, xóa hay đổi tên**.
- **Bảo vệ ứng dụng hệ thống Windows**: Tự động đưa vào danh sách loại trừ các thư mục nhạy cảm của Windows (`WindowsApps`, `Program Files`, `Program Files (x86)`, `$Recycle.Bin`, `System Volume Information`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`).
- **Bảo vệ kho game và giả lập**: Tuyệt đối không can thiệp vào các thư mục game đồ sộ (`SteamLibrary`, `Riot Games`, `LDPlayer`, `fo4`, `32837`, `Downloads`).
- **Bảo vệ dịch vụ Database đang chạy**: Bảo vệ đặc biệt cơ sở dữ liệu Microsoft SQL Server 2022 và các tệp dữ liệu LDF/MDF (`DBI202_VuPT\MSSQL16.MSSQLSERVER`, `MSSQLSERVER`, `MSSQL`).
- **Quét tệp êm ái (Quiet Scanning)**: Các thư mục bị loại trừ được lọc ngay từ tầng duyệt danh mục, không phát sinh syscall thừa và ghi nhận nhật ký quyền truy cập ở cấp độ `DEBUG` không gây phiền nhiễu cho người dùng.

### 5. Tiện ích Kiểm tra Biến Môi trường & PATH (`self-path-check`)
Hỗ trợ kiểm tra và hướng dẫn thiết lập nhanh môi trường dòng lệnh:
```bash
smart-drive self-path-check
```
- Kiểm tra biến môi trường `PATH` và xác minh xem script thực thi `smart-drive` đã có thể gọi trực tiếp từ terminal hay chưa.
- **Tìm lệnh đúng nơi pip đã đặt**: tìm trong môi trường của chính Python đang chạy (venv, một phiên bản pyenv, ...) rồi mới đến thư mục cài theo người dùng, và chỉ gợi ý thêm vào PATH thư mục thực sự chứa lệnh. Nếu gói chưa được cài cho Python đó, tiện ích nói rõ và in sẵn lệnh `pip install`. Người dùng pyenv được hướng dẫn chạy `pyenv rehash`.
- **Cấu hình 1-chạm cho Windows PowerShell**: Nếu chưa có trong PATH, tiện ích cung cấp sẵn câu lệnh PowerShell để người dùng dán vào là xong:
  ```powershell
  [Environment]::SetEnvironmentVariable("Path", $env:Path + ";$env:APPDATA\Python\Python311\Scripts", "User")
  ```
- **Cấu hình cho macOS/Linux**: Cung cấp sẵn lệnh bổ sung vào `~/.bashrc` hoặc `~/.zshrc`.
- **Cơ chế dự phòng không cần cấu hình**: Sau khi đã cài gói, có thể gọi mọi tính năng từ bất kỳ đâu mà không cần cấu hình PATH thông qua cú pháp sau (với mã nguồn chưa cài thì chỉ chạy được bên trong thư mục dự án):
  ```bash
  python -m smart_drive <lệnh>
  ```

### 6. Máy chủ MCP Server Hạng A (100/100) & Tối ưu Token cho AI Coding Agents
Tích hợp máy chủ MCP stdio chuẩn JSON-RPC 2.0 cấp doanh nghiệp:
- **Đạt điểm tuyệt đối Hạng A (100/100)**: Vượt qua toàn diện các bài kiểm toán bảo mật của MCP Registry / Inspector không có cảnh báo.
- **Cách ly Handler độc lập phân tích tĩnh AST**: Toàn bộ công cụ được định tuyến qua bảng ánh xạ rõ ràng và chuỗi kiểm tra tĩnh, cho phép các công cụ phân tích bảo mật kiểm toán độc lập 100% mã nguồn.
- **Định dạng JSON nén tối ưu Token kèm phân trang**: Bổ sung các trường phân trang (`limit`, `offset`, `total_groups`, `returned_group_count`, `has_more`, `next_offset`). Danh sách tệp trùng lặp được giới hạn tối đa 10 tệp xem trước cho mỗi nhóm (`truncated_files_count`) nhằm chống tràn cửa sổ ngữ cảnh (Context Window) của các mô hình AI.
- **8 Công cụ Chuyên dụng**: `ssd_search`, `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index`, `ssd_check_safety`, `ssd_status`, `ssd_auto_organize`.
- **Đăng ký 1-click cho tất cả trợ lý AI**:
  ```bash
  smart-drive mcp register --all
  # Hoặc:
  smart-drive mcp-config --all
  ```
  Tự động nhận diện và đăng ký cấu hình cho **Google Antigravity 2.0**, **Claude Code & Claude Desktop**, **Cursor IDE / OpenAI Codex**, **Windsurf**, và tệp cấu hình cục bộ `.mcp.json`.
- **Xác thực Handshake thời gian bất biến**: Sử dụng thuật toán chuẩn `hmac.compare_digest` với cờ lệnh `--auth-token` và `--require-auth`.
- **Khóa chặt Socket trên Loopback**: Từ chối tuyệt đối việc mở kết nối ra mạng ngoài (`0.0.0.0`), chỉ chấp nhận `127.0.0.1` và `localhost`.

### 7. Bộ 20 Launcher Portable Độc lập Đa Nền tảng
Không cần cài Git, không phụ thuộc tài khoản. Cung cấp 5 luồng tác vụ với 4 định dạng kịch bản chạy nhanh:

| Luồng Tác vụ | Windows CMD (`.bat`) | Windows PowerShell (`.ps1`) | macOS App (`.command`) | Linux/POSIX (`.sh`) |
|---|---|---|---|---|
| **Khởi tạo Ổ đĩa** | `Setup_SSD.bat` | `Setup_SSD.ps1` | `Setup_SSD.command` | `Setup_SSD.sh` |
| **Tìm kiếm Nhanh** | `Quick_Search.bat` | `Quick_Search.ps1` | `Quick_Search.command` | `Quick_Search.sh` |
| **Dọn rác Nhanh** | `Quick_Clean.bat` | `Quick_Clean.ps1` | `Quick_Clean.command` | `Quick_Clean.sh` |
| **Kiểm toán Dung lượng** | `Quick_Audit.bat` | `Quick_Audit.ps1` | `Quick_Audit.command` | `Quick_Audit.sh` |
| **Menu Tương tác Toàn năng**| `SmartDrive.bat` | `SmartDrive.ps1` | `SmartDrive.command` | `SmartDrive.sh` |

Tất cả 20 tệp khởi chạy có sẵn ở cả thư mục gốc của ổ cứng và thư mục `launchers/` để dùng ngay bằng thao tác nhấp đúp chuột.

### 8. Động cơ Di chuyển Cache Ổ C: & NTFS Directory Junctions (`smart-drive offload`)
- **Quy trình chuyển giao 7 bước an toàn**: Tự động phát hiện và chuyển các cache khổng lồ (HuggingFace, Ollama, PyTorch, Docker WSL2, pip, uv, npm, Conda, Gradle, Cargo) từ `C:` sang ổ phụ (`D:\04_System_Offload_Caches\<tên>`).
- **Điểm nối phần cứng NTFS Directory Junctions (`mklink /J`)**: Tạo liên kết trong suốt mà không cần quyền Admin hay Developer Mode.
- **Cơ chế hoàn tác Rollback tức thì**: Tự động phục hồi nguyên trạng nếu có bất kỳ bước nào gặp lỗi gián đoạn.

### 9. Động cơ Snapshot SHA-256 Dạng Dòng & Sao lưu Tăng số
- **Băm SHA-256 theo khối 64KB**: Thuật toán tính toán với bộ nhớ hằng số $O(1)$, kiểm tra chính xác hiện tượng suy thoái dữ liệu (bit rot) và tệp bị sửa đổi.
- **Bản kê khai trạng thái điểm phục hồi**: Lưu trữ trạng thái vào `.smart_drive/snapshots/<tên>.json`.
- **Sao lưu tăng số thông minh**: Tự động nhận diện và chỉ sao chép các tệp mới hoặc có thay đổi sang ổ đích, tự động loại trừ file rác.
- **Báo cáo phạm vi trung thực**: Snapshot và sao lưu mặc định phủ `02_Learning_Knowledge`, `03_Development_Projects` và `05_Dev_Toolbox` (chọn phân vùng khác bằng `--partitions`) và bỏ qua các thư mục ẩn như `.git`, `.github`, `.vscode` (thêm lại bằng `--include-hidden`). Mỗi lần chạy đều liệt kê phần chưa được phủ, nên không bao giờ có khoảng trống âm thầm.

### 10. Cam kết 100% Zero External Pip Dependencies
- Tệp `pyproject.toml` khai báo tường minh: `dependencies = []`.
- Toàn bộ tính năng được xây dựng trên thư viện chuẩn Python 3.9+ (`sqlite3`, `hashlib`, `hmac`, `json`, `urllib`, `shutil`, `pathlib`, `ctypes`, `subprocess`, `argparse`).
- Cài đặt và hoạt động ngay trên mọi hệ máy tính mà không lo xung đột gói thư viện.

---

## 4. Khởi Động Nhanh Trong 3 Bước

### Bước 1: Kiểm tra môi trường
Chạy kiểm tra môi trường và biến đường dẫn:
```bash
python -m smart_drive self-path-check
```

Hoặc cài đặt chế độ phát triển (nếu muốn dùng lệnh `smart-drive` toàn cục):
```bash
pip install -e .
```

### Bước 2: Khởi tạo ổ đĩa 1-chạm
Khởi tạo cấu trúc thư mục chuẩn, cài khiên bảo vệ và tạo cơ sở dữ liệu tìm kiếm FTS5 bằng thao tác nhấp đúp hoặc dòng lệnh:

- **Windows**: Nhấp đúp vào `Setup_SSD.bat` hoặc chạy `Setup_SSD.ps1`
- **macOS**: Nhấp đúp vào `Setup_SSD.command`
- **Linux**: Chạy `./Setup_SSD.sh`
- **Dòng lệnh CLI**:
  ```bash
  # Dành cho ổ phụ gắn trong (D:, E:) có bài học sinh viên & game:
  python -m smart_drive init --profile workstation-hybrid

  # Hoặc dành cho ổ cứng ngoài di động chứa mô hình AI:
  python -m smart_drive init --profile ai-developer
  ```

### Bước 3: Đăng ký AI Agent
Đăng ký SmartDrive-OS làm máy chủ MCP cho toàn bộ các trợ lý lập trình AI chỉ với 1 lệnh duy nhất:
```bash
smart-drive mcp register --all
```

---

## 5. 5 Cấu Hình Mẫu Chuyên Biệt (Preset Profiles)

| Cấu hình mẫu | Đối tượng mục tiêu | Cấu trúc phân vùng & Thư mục con |
|---|---|---|
| **`workstation-hybrid`** *(Khuyên dùng cho PC có ổ D:)* | Ổ phụ gắn trong (`D:`, `E:`) chứa lẫn game, ứng dụng Windows và bài học sinh viên | `01_AI_Models`, `02_Learning_Knowledge` (`FPTU/`, `Personal_Books/`, `Notes/`), `03_Development_Projects` (`active/`, `archive/`), `04_System_Workspaces`, `05_Dev_Toolbox` (`OEM_Drivers/`, `Installers/`, `scripts/`), `06_Archives_Storage` |
| **`internal-developer-vault`** | Ổ phụ NVMe/SATA chuyên dụng làm kho lưu trữ cache chuyển từ C: và dữ liệu AI | `01_AI_Models`, `02_Development_Workspaces`, `03_Data_Vault`, `04_System_Offload_Caches` (`huggingface/`, `ollama/`, `pip/`, `uv/`), `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`ai-developer`** | Ổ di động SSD tốc độ cao chuyên chứa mô hình LLM, checkpoint và AI agent | `01_AI_Models` (`checkpoints/`, `gguf/`, `safetensors/`, `datasets/`), `02_Learning_Knowledge`, `03_Development_Projects` (`ai_agents/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`data-science`** | Kỹ sư dữ liệu, phân tích pipeline và sổ tay Jupyter Notebook | `01_AI_Models`, `02_Learning_Knowledge`, `03_Development_Projects` (`notebooks/`, `data_raw/`, `pipelines/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`general-workspace`** *(Mặc định)* | Cấu trúc tiêu chuẩn cho lập trình tổng hợp, ghi chú và tài liệu học tập | 6 nhóm thư mục chuẩn kèm các thư mục con `Notes/`, `References/`, `active/`, `archive/`, `scripts/` |

---

## 6. Hướng Dẫn Sử Dụng Bộ Khởi Chạy 1-Chạm (20 Launchers)

Hệ thống cung cấp sẵn 20 kịch bản khởi chạy không cần cài đặt Git:

```
smart-drive-os/
├── launchers/
│   ├── Setup_SSD.bat        Quick_Search.bat        Quick_Clean.bat        Quick_Audit.bat        SmartDrive.bat
│   ├── Setup_SSD.ps1        Quick_Search.ps1        Quick_Clean.ps1        Quick_Audit.ps1        SmartDrive.ps1
│   ├── Setup_SSD.command    Quick_Search.command    Quick_Clean.command    Quick_Audit.command    SmartDrive.command
│   └── Setup_SSD.sh         Quick_Search.sh         Quick_Clean.sh         Quick_Audit.sh         SmartDrive.sh
```

- **`Setup_SSD`**: Trình hướng dẫn tương tác cài đặt ổ đĩa và chọn cấu hình mẫu.
- **`Quick_Search`**: Cửa sổ tìm kiếm nhanh FTS5 tương tác tức thì.
- **`Quick_Clean`**: Quét mô phỏng file rác và xác nhận dọn dẹp an toàn tầng Tier 1.
- **`Quick_Audit`**: Kiểm toán thống kê dung lượng và đo lường cluster slack.
- **`SmartDrive`**: Menu TUI đầy đủ tính năng cho người dùng thích thao tác phím số.

---

## 7. Bảng Tra Cứu Toàn Diện Lệnh CLI

Mọi câu lệnh đều có thể gọi qua `smart-drive <lệnh>` hoặc `python -m smart_drive <lệnh>`:

| Lệnh | Tham số chính | Mô tả chức năng |
|---|---|---|
| `self-path-check` | *(không có)* | Kiểm tra đường dẫn PATH của hệ thống và đưa ra câu lệnh PowerShell cấu hình 1-chạm. |
| `init` | `--profile <tên>`, `--root <đường_dẫn>`, `--force`, `--json` | Khởi tạo ổ đĩa, tạo 6 nhóm thư mục, cài khiên và gieo cơ sở dữ liệu FTS5. |
| `status` | `--root <đường_dẫn>`, `--json` | Kiểm tra tình trạng điểm gắn ổ đĩa, hệ tệp được nhận diện, cấu trúc hình học, khiên bảo vệ và phân vùng. |
| `audit` | `--root <đường_dẫn>`, `--json`, `--markdown`, `--export <tệp>` | Kiểm toán chi tiết dung lượng và tỷ lệ lãng phí cluster slack, mô hình hoá theo cụm exFAT 512 KB; báo cáo nêu hệ tệp được nhận diện và cho biết khi nào mô hình đó không áp dụng (APFS, NTFS, ext4 ...). Dữ liệu có nhiều hard link chỉ được tính một lần và các đường dẫn thừa được nêu riêng ("Hard Links Not Counted"); symlink được bỏ qua và đếm. |
| `clean` | `--dry-run` *(mặc định)*, `--apply`, `--tier {1,2,3}`, `--log`, `--json` | Dọn rác hệ thống với cơ chế bắt buộc chạy thử trước và bảo vệ whitelist bất biến. Với `--apply`, lệnh trả mã `1` nếu có mục xoá thất bại (mục bị lá chắn an toàn từ chối được báo riêng, không tính là thất bại). |
| `search` | `<từ_khóa>`, `--ext <đuôi>`, `--size <kích_thước>`, `--category <nhóm>`, `--limit <n>`, `--json` | Tìm kiếm siêu tốc (<10ms) bằng SQLite FTS5 kết hợp thuật toán xếp hạng BM25. |
| `organize` | `--dry-run`, `--apply`, `--clean`, `--json` | Tự động phân loại file tự do, định tuyến môn học FPTU bằng AcademicClassifier và giảm lãng phí slack. |
| `sentinel` | `--root <đường_dẫn>`, `--auto-heal`, `--no-heal`, `--json` | Kiểm tra sức khỏe ổ cứng (hệ tệp được nhận diện, trạng thái git), phục hồi khiên bảo vệ bị thiếu (alias: `agent-check`). |
| `mcp` | `[{serve,register}]`, `--root <đường_dẫn>`, `--all`, `--auth-token <token>`, `--require-auth` | Khởi chạy máy chủ MCP stdio (`serve`) hoặc đăng ký cấu hình cho các AI agent (`register`). |
| `mcp-config` | `--all`, `--antigravity`, `--claude`, `--cursor`, `--windsurf`, `--workspace`, `--json` | Tự động đăng ký máy chủ MCP vào các tệp cấu hình của các IDE và AI Agent. |
| `snapshot create` | `[tên]`, `--partitions <danh_sách>`, `--include-hidden`, `--root <đường_dẫn>`, `--json` | Tạo bản ghi nhận trạng thái kèm băm SHA-256 dạng dòng cho các phân vùng chỉ định. |
| `snapshot list` | `--root <đường_dẫn>`, `--json` | Liệt kê toàn bộ các bản snapshot đã lưu kèm dung lượng và số lượng tệp tin. |
| `snapshot verify` | `<tên>`, `--no-untracked`, `--root <đường_dẫn>`, `--json` | Kiểm toán tính toàn vẹn dữ liệu so với bản snapshot để phát hiện tệp bị sửa đổi hoặc suy thoái (bit rot). |
| `backup` | `--target <đích>`, `--dry-run`, `--hash`, `--partitions <danh_sách>`, `--include-hidden`, `--json` | Sao lưu tăng số thông minh, chỉ sao chép các tệp mới hoặc có thay đổi sang ổ đích. |
| `classify` | `[đường_dẫn]`, `--suggest`, `--dry-run`, `--apply`, `--no-recursive`, `--json` | Nhận diện sâu magic bytes và cấu trúc tệp để phân loại mô hình AI, dataset, tài liệu, dự án. Không bao giờ đi theo hay di chuyển symlink/junction, và không đọc hay ghi tại nơi phải đi qua chúng. |
| `offload` | `--scan`, `--move <tên>`, `--target <ổ>`, `--revert <tên>`, `--dry-run`, `--json` | Quét và di chuyển các cache khổng lồ sang ổ phụ (NTFS junction trên Windows, symbolic link trên macOS/Linux). |
| `health` | `[ổ_đĩa]`, `--root <đường_dẫn>`, `--json` | Kiểm tra sức khỏe ổ cứng SSD, trạng thái TRIM (Windows), cluster geometry và dung lượng trống. Nhận ký tự ổ đĩa trên Windows hoặc đường dẫn ổ (`/Volumes/MySSD`) trên macOS/Linux. |
| `dup` | `--root <đường_dẫn>`, `--json` | Tìm kiếm tệp tin trùng lặp qua 3 giai đoạn SHA-256 kèm thống kê dung lượng slack thu hồi được. Nhiều tên cho cùng một dữ liệu (hard link) được coi là một tệp, không bao giờ được đề xuất như bản sao để xoá. |
| `index` | `--root <đường_dẫn>`, `--db <đường_dẫn>`, `--batch <n>` | Lập chỉ mục toàn bộ ổ đĩa với SQLite FTS5 (tốc độ >15.000 tệp/giây). |
| `update` | `--root <đường_dẫn>`, `--db <đường_dẫn>`, `--json` | Đồng bộ chỉ mục tìm kiếm tăng số siêu nhanh với độ phức tạp $O(1)$ (<2 giây). |

---

## 8. Cú Pháp Tìm Kiếm Nâng Cao

Công cụ SQLite FTS5 xử lý truy vấn phức tạp dưới 10 milliseconds:

- **Từ khóa tự do**: `smart-drive search "machine learning"`
- **Lọc theo phần mở rộng**: `smart-drive search "weights ext:gguf"`
- **Lọc theo dung lượng**: `smart-drive search "dataset size:>100MB"` (hoặc `size:<1MB`, `size:0`). `>` và `<` không tính chính giá trị đó, `>=` và `<=` thì có; đơn vị là nhị phân (1KB = 1024 byte). Bộ lọc không đọc được (`size:>abc`, `size:>10 MB`) sẽ hiện cảnh báo (stderr ở CLI, trường `warnings` ở MCP) thay vì bị bỏ qua âm thầm.
- **Lọc theo danh mục**: `smart-drive search "llama cat:ai_models"`
- **Lọc theo thư mục**: `smart-drive search "de thi dir:FPTU"`
- **Truy vấn kết hợp**: `smart-drive search "exam ext:pdf size:>1MB dir:DBI202"`

---

## 9. Cơ Chế Dọn Rác An Toàn 3 Tầng & Danh Sách Bảo Vệ Whitelist

SmartDrive-OS phân tầng rác hệ thống thành 3 mức độ rõ ràng:

1. **Tier 1 (Rác Metadata Hệ Điều Hành - An toàn 100%)**:
   - macOS `.DS_Store`, `._*` AppleDouble resource forks.
   - Windows `Thumbs.db`, `desktop.ini`, `ehthumbs.db`.
   - `.Spotlight-V100`, `.Trashes`.
2. **Tier 2 (Cache Trình Biên Dịch & Môi Trường Phát Triển)**:
   - Python `__pycache__`, `*.pyc`, `*.pyo`, `.pytest_cache`.
   - Node `node_modules/.cache`.
   - Rust `target/debug/build`.
3. **Tier 3 (Tệp Tạm Thời & Nhật Ký Ghi Đè)**:
   - `*.log`, `*.tmp`, `*.bak`, crash dumps.

> **Lá chắn Whitelist Bất Khả Xâm Phạm**: Các tệp chỉ thị gốc (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`, `PRIVACY.md`), các kịch bản khởi chạy (`Setup_*.bat`, `Setup_*.command`), 6 thư mục phân vùng gốc, các thư mục hệ thống Windows (`WindowsApps`, `Program Files`), game (`SteamLibrary`, `Riot Games`, `LDPlayer`), database đang hoạt động (`Microsoft SQL Server 2022`) và chính mã nguồn `smart-drive-os` **được bảo vệ vĩnh viễn** và không bao giờ bị xóa hay di chuyển.

---

## 10. Tích Hợp Máy Chủ MCP Với Các Trợ Lý AI Lập Trình

SmartDrive-OS hỗ trợ giao thức MCP tiêu chuẩn, cung cấp 8 công cụ chuyên dụng cho AI Agents:

1. `ssd_search`: Tìm kiếm FTS5 tức thì trả về token rút gọn (<2.000 tokens/lần truy vấn). *(chỉ đọc)*
2. `ssd_audit`: Thống kê dung lượng và phân tích lãng phí cluster slack có chế độ nén gọn. *(chỉ đọc)*
3. `ssd_clean`: Dọn rác an toàn có khiên bảo vệ whitelist và hỗ trợ dry-run. *(xóa rác)*
4. `ssd_find_duplicates`: Tìm file trùng lặp qua 3 giai đoạn SHA-256 có phân trang và giới hạn xem trước. *(chỉ đọc)*
5. `ssd_update_index`: Đồng bộ chỉ mục tìm kiếm tăng số (<2s). *(idempotent)*
6. `ssd_check_safety`: Kiểm tra tính tương thích exFAT (quét 9 ký tự cấm Win32, 22 từ khóa DOS, và phát hiện symlink). *(chỉ đọc)*
7. `ssd_status`: Kiểm tra trạng thái gắn kết SSD, khiên bảo vệ và phân vùng. *(chỉ đọc)*
8. `ssd_auto_organize`: Tự động phân luồng và gom file giảm thiểu lãng phí slack kết hợp AcademicClassifier. *(sắp xếp/xóa rác)*

### Đăng ký 1-Chạm Cho Tất Cả Trợ Lý AI
```bash
smart-drive mcp register --all
```

Khi chạy từ mã nguồn (không `pip install`), mỗi cấu hình được ghi còn kèm `PYTHONPATH` trỏ vào thư mục dự án, nên ứng dụng khởi động được máy chủ từ bất kỳ thư mục làm việc nào. Bản đã cài bằng pip không cần thiết lập thêm.

Các mẫu cấu hình dựng sẵn cũng được lưu trữ trong `configs/`:
- `configs/.mcp.json` — Thư mục gốc dự án / workspace
- `configs/mcp_config.json` — Google Antigravity 2.0 (`~/.gemini/antigravity/mcp_config.json`)
- `configs/claude_desktop_config.json` — Claude Desktop & Claude Code (`~/.claude.json`)
- `configs/cursor_mcp.json` — Cursor IDE / OpenAI Codex (`~/.cursor/mcp.json`)
- `configs/windsurf_mcp.json` — Windsurf IDE (`~/.codeium/windsurf/mcp_config.json`)

### Chứng Chỉ & Chỉ Số Tin Cậy M8ven MCP (Grade A 100/100)
SmartDrive-OS được kiểm định độc lập và xếp hạng chính thức trên thư mục [M8ven MCP Directory](https://m8ven.ai/mcp/duongnad-smart-drive-os) với **Điểm Tin Cậy Tuyệt Đối Hạng A (100/100)**:
- **Mã Xác Thực (Verification Token)**: `duongnad-smart-drive-os-1kxkwu`
- **100% AST Handler Isolation**: Toàn bộ handler công cụ được phân giải tĩnh qua chuỗi `if-elif` và class-level handler map, tuyệt đối không dùng `eval()` hay reflection động gây rủi ro bảo mật.
- **Xác Thực Handshake Thời Gian Hằng Số**: Hỗ trợ xác thực token an toàn chống timing attack qua `hmac.compare_digest` chuẩn JSON-RPC `auth/handshake`. Hỗ trợ các tham số `--auth-token`, `--require-auth` hoặc biến môi trường `SMART_DRIVE_MCP_AUTH_TOKEN`.
- **Bộ Giới Hạn Tần Suất Trượt (Rate Limiter)**: Cơ chế `SlidingWindowRateLimiter` thread-safe ngăn ngừa agent gọi lặp vô tận (runaway loop), phản hồi mã lỗi chuẩn `-32000` kèm header `Retry-After` chính xác tới millisecond.
- **Chặn Đứng Path Traversal & Escape**: Hàm `_resolve_safe_path` cô lập 100% các vector tấn công vượt cấp (`..`), null-byte (`\0`) và nhảy ổ đĩa.
- **Tuân Thủ Chuẩn M8ven, Claude & OpenAI**: Khai báo minh bạch các thuộc tính an toàn `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` cho toàn bộ 8 công cụ.
- **Không Gửi Dữ Liệu Ra Ngoài**: Đảm bảo 100% giao tiếp qua luồng `stdio` cục bộ, không mở port mạng, không chứa thư viện tracking/telemetry.

---

## 11. Báo Cáo Kiểm Thử Tự Động (1033 Bài Test, Pass 100%)

Hệ thống được bảo vệ và kiểm chứng bởi **1033 ca kiểm thử tự động** (unit tests, integration tests, stress tests, adversarial tests) chạy hoàn toàn trên thư viện chuẩn `unittest`:

```bash
# Chạy toàn bộ bài test bằng pytest:
pytest -q

# Hoặc chạy bằng unittest của Python Standard Library:
python -m unittest discover tests -v
```

### Kết Quả Kiểm Thử Thực Tế:
```text
============================= test session starts ==============================
collected 1033 items

........................................................................ [  6%]
..................................................................... [ 13%]
.................................................................... [ 20%]
............................s.s.......................................................... [ 28%]
........................................................................ [ 35%]
........................................................................ [ 42%]
........................................................................ [ 49%]
........................................................................... [ 57%]
...................................................... [ 62%]
............................................. [ 66%]
....................................................... [ 71%]
............................................................................................................ [ 82%]
........sss....ssssss................................................... [ 89%]
........................................................................ [ 96%]
......................................                       [100%]
================== 1022 passed, 11 skipped, 313 subtests passed ==================
```

- **Tỷ lệ đậu**: **100.0%** (1022 passed, 11 bài bỏ qua do đặc thù nền tảng Windows trên máy Mac/Linux, 0 failures, 0 errors).
- **Độ bao phủ đối kháng**: Kiểm tra các kích thước cluster khắc nghiệt từ 512B đến 32MB, các giá trị biên (0B, 1B, 512KB-1B, 512KB, 512KB+1B) và kiểm toán ứng suất hàng triệu tệp tin.
- **Độ bao phủ tự bảo vệ**: Kiểm chứng cơ chế tự bảo vệ repo, thư mục Windows Apps, thư mục Game và database SQL Server.
- **Độ bao phủ AcademicClassifier**: Kiểm chứng trích xuất regex mã môn học, sửa lỗi font mojibake và quy hoạch học kỳ.

---

## 12. Bảo Mật & Quyền Riêng Tư Dữ Liệu

SmartDrive-OS tuân thủ triệt để nguyên tắc **Local-First & Quyền Riêng Tư Tối Đa**:

- **Hoạt động 100% Cục bộ (Local-Only)**: Toàn bộ quá trình quét ổ đĩa, đánh chỉ mục tìm kiếm SQLite FTS5 và xác thực snapshot SHA-256 đều diễn ra trực tiếp trên phần cứng của bạn. Tuyệt đối không gửi dữ liệu ra ngoài.
- **Zero Telemetry**: Hoàn toàn không chứa mã thu thập hành vi, không gửi báo cáo sử dụng và không có kết nối ngầm.
- **Zero PII Logging**: Tuyệt đối không đọc, bóc tách hay thu thập nội dung nhạy cảm (mã nguồn bí mật, token, private key); chỉ lưu metadata tệp tin cần thiết trong cơ sở dữ liệu nội bộ `.smart_drive/index.db`.
- **Sẵn sàng cho môi trường Air-Gap**: Không có dependencies ngoài (`dependencies = []`). Máy chủ MCP giao tiếp thuần túy qua luồng `stdio`, và SmartDrive-OS không mở bất kỳ cổng mạng nào.
- **Khiên Bảo vệ Bất khả Xâm phạm**: Ngăn chặn hoàn toàn việc xóa nhầm các tệp cấu hình cốt lõi (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `PRIVACY.md`) và các ứng dụng hệ thống.

Xem toàn văn cam kết bảo mật và quyền riêng tư tại [PRIVACY.md](PRIVACY.md).

---

## 13. Đóng Góp Mã Nguồn & Giấy Phép

### Đóng Góp Mã Nguồn
SmartDrive-OS là dự án mã nguồn mở vì cộng đồng và luôn chào đón mọi sự đóng góp:
- 🌟 **Star & Fork** repository trên GitHub: [DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os)
- 🐛 Báo cáo lỗi hoặc đề xuất tính năng mới tại [GitHub Issues](https://github.com/DuongNAD/smart-drive-os/issues)
- 🔀 Gửi mã nguồn cải tiến qua [Pull Requests](https://github.com/DuongNAD/smart-drive-os/pulls)
- 📖 Xem quy chuẩn đóng góp chi tiết tại [CONTRIBUTING.md](CONTRIBUTING.md)

### Giấy Phép
Dự án được phân phối hoàn toàn miễn phí theo **Giấy phép MIT** — xem chi tiết tại tệp [LICENSE](LICENSE).  
Bạn có toàn quyền sử dụng, chỉnh sửa, phân phối và tích hợp vào các dự án cá nhân hoặc thương mại mà không gặp bất kỳ rào cản pháp lý nào.
