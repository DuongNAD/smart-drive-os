# Original User Request

## 2026-09-26T05:58:52Z

Nghiên cứu, phát triển và nâng cấp bộ tính năng thế hệ mới cho **SmartDrive-OS (v1.1.0)** tại `d:\teamwork_projects\smart_drive_os`: Bổ sung Web Dashboard trực quan (`smart-drive ui`), Hệ thống Snapshot & Backup bảo vệ dữ liệu với SHA-256 (`smart-drive snapshot`), Công cụ phân loại & gắn nhãn thông minh (`smart-drive classify`), cập nhật tài liệu và tự động push bản phát hành v1.1.0 lên GitHub `DuongNAD/smart-drive-os`.

Working directory: `d:\teamwork_projects\smart_drive_os`
Integrity mode: development

## Requirements

### R1. Zero-Dependency Web Dashboard & Visual UI (`smart-drive ui`)
- Xây dựng giao diện web cục bộ tương tác chạy hoàn toàn trên `http.server` của Python Standard Library (không cần Flask, FastAPI hay bất kỳ pip package nào).
- Giao diện hiện đại (Dark mode, responsive):
  - Biểu đồ phân bổ dung lượng 6 taxonomy và trực quan hóa cluster slack 512KB lãng phí.
  - Thanh tìm kiếm FTS5 tương tác tức thì (<10ms) với bộ lọc loại tệp, dung lượng, danh mục.
  - Bảng điều khiển dọn rác 3-tier an toàn với chế độ Dry-run preview và nút dọn dẹp 1-chạm có xác nhận.
- Hỗ trợ cờ dòng lệnh: `smart-drive ui --port 8765 --no-browser` (mặc định tự mở trình duyệt mặc định).

### R2. Hệ thống Snapshot & Backup thông minh (`smart-drive snapshot` / `restore`)
- Cơ chế tạo snapshot điểm phục hồi (point-in-time snapshot) cho các phân vùng dữ liệu quan trọng (`02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox`).
- Kiểm tra tính toàn vẹn dữ liệu bằng mã băm SHA-256 manifest.
- Hỗ trợ lệnh:
  - `smart-drive snapshot create [name]`: Tạo snapshot bản ghi trạng thái và checksum.
  - `smart-drive snapshot list`: Liệt kê các bản snapshot đã lưu.
  - `smart-drive snapshot verify [name]`: Kiểm tra tính toàn vẹn các file xem có bị sửa đổi/hỏng hóc không.
  - `smart-drive backup --target <path>`: Sao lưu tăng số (incremental backup) an toàn sang ổ đĩa hoặc thư mục chỉ định.

### R3. Bộ phân loại thông minh & Auto-Tagger (`smart-drive classify`)
- Engine nhận diện sâu các loại định dạng tệp chuyên dụng:
  - AI Models & Weights: GGUF, Safetensors, ONNX, PyTorch `.pt`/`.pth`, HuggingFace model configs.
  - Datasets: Parquet, Arrow, JSONL, CSV, HDF5.
  - Nghiên cứu & Tài liệu: PDF, ePub, Markdown notes.
  - Source Code: Tự nhận diện cấu trúc repo git, dự án Node/Python/Rust.
- Hỗ trợ chế độ đề xuất di chuyển thông minh (`smart-drive classify --suggest`) và tự động gom file phân loại vào đúng taxonomy con an toàn.

### R4. Kiểm thử tự động, Cập nhật Tài liệu & Phát hành GitHub v1.1.0
- Mở rộng test suite `tests/` bao phủ toàn bộ tính năng mới (UI routes, snapshot/verify, classifier logic), đảm bảo 100% pass với pure standard library `unittest`.
- Cập nhật cả `README.md` và `README_VN.md`: thêm hướng dẫn các lệnh mới (`ui`, `snapshot`, `classify`), cập nhật bảng tính năng và ảnh minh họa/sơ đồ luồng hoạt động.
- Nâng phiên bản package trong `pyproject.toml` lên `1.1.0`.
- Tạo commit Git chuẩn Conventional Commits `feat: release SmartDrive-OS v1.1.0 with Web UI, Snapshot Engine & AI Classifier` và push cả commit lẫn tag `v1.1.0` lên GitHub `origin main` ([DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os)).

## Acceptance Criteria

### Verification & Testing
- [ ] Chạy `python -m unittest discover tests` đạt 100% pass với tất cả các module mới (test UI handlers, snapshot creation/verification, classifier rule engine).
- [ ] Kiểm thử lệnh `smart-drive ui` khởi động thành công HTTP server cục bộ, trả về mã HTTP 200, render đầy đủ HTML/CSS/JS nhúng mà không cần kết nối internet hay thư viện ngoài.
- [ ] Thử nghiệm `smart-drive snapshot create test_snap` và `smart-drive snapshot verify test_snap` xác thực chính xác SHA-256 của các tệp mục tiêu.
- [ ] Lệnh `smart-drive classify --dry-run` phân tích chính xác các định dạng tệp AI (.safetensors, .gguf) và datasets (.parquet, .jsonl).

### Code Quality & Packaging
- [ ] Giữ vững nguyên tắc Zero-Dependency: 100% code mới chỉ sử dụng Python Standard Library (`http.server`, `hashlib`, `json`, `sqlite3`, `urllib`).
- [ ] `pyproject.toml` cập nhật `version = "1.1.0"`.

### Documentation & Deliverables
- [ ] `README.md` và `README_VN.md` có đầy đủ mục hướng dẫn sử dụng `smart-drive ui`, `smart-drive snapshot`, `smart-drive classify`.
- [ ] Git commit và tag `v1.1.0` được push thành công lên GitHub `origin main`. Working tree sạch 100%.
