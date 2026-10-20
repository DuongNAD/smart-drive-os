# Kế hoạch nâng cấp SmartDrive-OS v1.4 — "An toàn cho người dùng thường"

> **Dành cho agent thực thi (Gemini).** Tài liệu này tự đủ: mọi bằng chứng, đường dẫn, tên hàm đều đã được kiểm chứng
> trên commit `b95011e` (2026-10-19). Số dòng có thể lệch vài dòng sau mỗi commit — tìm theo tên hàm nếu lệch.
> Làm **lần lượt từng task**, mỗi task một commit (trừ khi task ghi rõ tách nhiều commit).

---

## 0. Luật bắt buộc (đọc trước khi viết dòng code nào)

1. Đọc `AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `CONTRIBUTING.md`. Các luật ở đó vẫn áp dụng.
2. **Chỉ thư viện chuẩn Python** (`dependencies = []` trong `pyproject.toml`). Không thêm gói pip nào.
3. **Python 3.9+**: không dùng `match`, không dùng `X | Y` ngoài chú thích kiểu có `from __future__ import annotations`
   (theo cách các module hiện có đang làm).
4. **Chạy được trên Windows, macOS, Linux.** Code chỉ dành cho Windows (`winreg`, `.lnk`, `tasklist`) phải được bọc
   `sys.platform == "win32"` và trả về rỗng ở hệ khác. Test cho phần Windows phải dùng dữ liệu giả (fake reader) để
   chạy được trên mọi OS; chỉ test chạm hệ thống thật mới được `skipUnless(sys.platform == "win32")`.
5. **Test**: `unittest` trong `tests/`. Lệnh chạy trên máy này: `py -m unittest discover tests`
   (máy này **không có `python` trong PATH**, chỉ có `py`). CI chạy Python 3.9 và 3.13 trên 3 OS (`.github/workflows/tests.yml`).
6. **Không bao giờ chạm cấu hình thật của máy trong test.** Các hàm relink nhận `home=` và `app_data=`
   (`smart_drive/core/relink.py:393`, `:1105`) — luôn truyền thư mục tạm. Ổ đĩa giả tạo bằng `tempfile`.
   Đọc `tests/helpers.py` và test cùng module trước khi viết test mới, theo đúng phong cách đó.
7. **Commit**: một thay đổi logic mỗi commit, dạng `fix(scope): ...` / `feat(scope): ...` / `docs: ...`, **kèm test hồi quy
   trong cùng commit**, thân commit giải thích nguyên nhân gốc hoặc bất biến được bảo vệ. Trước mỗi commit:
   chạy toàn bộ test, `py -m smart_drive gitignore audit`, xem `git diff --staged`.
   Cuối thân commit thêm dòng `Co-Authored-By:` theo quy ước của agent bạn (nếu có).
8. **Làm trên nhánh mới** `feat/v1.4-safety` (tạo từ `main`). **Không push, không tạo PR, không đổi version**
   trừ khi người dùng bảo.
9. **Launcher**: 20 file ở gốc repo và 20 file trong `launchers/` phải **giống hệt từng byte**
   (`tests/test_launchers_and_invariants_tier5.py:92`). Sửa một bản thì chép sang bản kia.
10. **README**: khi đổi hành vi CLI, cập nhật cả `README.md` và `README_VN.md` (và `README_INTERNAL.md` nếu liên quan)
    — làm trong task docs ở M8, không rải rác.
11. **CẤM trên máy thật**: không chạy `organize --apply`, `classify --apply`, `clean --apply`, `offload --move`,
    `relink` (không có `--dry-run`), `undo --apply` trên `E:\` hay trên cấu hình agent/IDE thật.
    Chỉ được chạy `--dry-run` / lệnh chỉ đọc để kiểm tra thủ công (mục 4). Người dùng sẽ tự bấm `--apply`.
12. Không sửa file nào trên `E:\` nằm ngoài thư mục repo `E:\smart-drive-os`.

### Trạng thái test gốc (đo trên máy này, commit `b95011e`)
`py -m unittest discover tests` → **1109 test, 2 fail, 79 skip** (≈190 giây). Hai lỗi này **có sẵn từ trước**, không phải do bạn:
- `test_gitignore.TestVisibilityFromMockedGh.test_check_gh_authenticated` — fail **ổn định** vì máy không cài `gh`:
  `check_gh_authenticated` (`smart_drive/core/gitignore.py:882`) gọi `shutil.which("gh")` trước, mà test chỉ patch `_run_gh`.
  Sửa ở **T0** bên dưới.
- `test_relink.TestRelink.test_14_cli_classify_apply_and_relink_edge_cases` — **chập chờn**: tiến trình con thoát mã
  `3221225477` (0xC0000005, crash) khi chạy cả bộ; chạy riêng thì qua. Không sửa trong kế hoạch này; nếu nó fail sau commit của bạn,
  chạy lại riêng test đó để phân biệt với lỗi thật.

Sau mỗi commit, ngoài hai test trên (cho tới khi T0 xong) **không được có thêm test fail nào**.

### Khi nào phải DỪNG và hỏi người dùng
- Trước khi bạn sửa gì mà đã có test fail **ngoài hai test đã biết** ở trên → báo lại, đừng tự sửa test cho xanh.
- Một test hiện có **cố ý** khóa hành vi mà kế hoạch này muốn đổi (ví dụ so khớp từ khóa kiểu substring ở T14).
- Cần thêm dependency, đổi schema công cụ MCP đã có, bump version, push, phát hành.
- Bất kỳ thao tác nào có thể xóa/dời dữ liệu thật.

---

## 1. Bối cảnh — máy thật của người dùng (đã kiểm chứng)

- Chỉ có `C:` (NTFS) và `E:` (`DU LIEU SSD`, **NTFS 4 KB, ổ gắn trong**). **Không có ổ `D:`.** Tài liệu đang mặc định `D:`.
- Python cài qua Python Launcher: `py` chạy được, `python` thì không.
- `py -m smart_drive classify E:\ --root E:\ --no-recursive --dry-run --json` lên kế hoạch:
  - dời `E:\SmartDrive.bat` → `05_Dev_Toolbox/Scripts/SmartDrive.bat` (dời **chính launcher của mình**);
  - dời `E:\DumpStack.log.tmp` (file hệ thống Windows, đọc bị `Permission denied`) → `06_Archives_Storage/Unreadable/`.
- `py -m smart_drive organize --root E:\ --dry-run --json` chỉ lên kế hoạch dời `E:\tool`, báo
  `nominal_size = 3,574,867,574` nhưng `slack_bytes = 22,625,376,650` (tính theo cluster 512 KB trên ổ 4 KB).
  `E:\project` (≈20 repo bên trong) bị bỏ qua.
- Thứ trên máy đang **phụ thuộc vào đường dẫn trong `E:\`** (sẽ gãy nếu thư mục bị dời):
  - **PATH**: `E:\Git`, `E:\PyCharm 2025.3.1`, `E:\`.
  - **Dịch vụ Windows**: 3 dịch vụ SQL Server chạy từ `E:\SQL`; dịch vụ UltraViewer chạy từ `E:\UltraViewer`.
    Tên `SQL` **không** nằm trong `PROTECTED_ROOT_DIRS`.
  - **Registry Uninstall**: 10 app cài trên `E:` (Git, Cisco Packet Tracer, MSI Afterburner + RivaTuner,
    Adobe Digital Editions, emu8086, Iriun Webcam, UltraViewer, Arduino IDE, Obsidian).
    Lưu ý: khi đọc registry có key trả lỗi kiểu dữ liệu ("Specified cast is not valid") — phải bắt lỗi theo từng key/giá trị.
  - **Shortcut (.lnk)** ở Desktop/Start Menu/Taskbar: hơn 60 shortcut trỏ vào app trên `E:`.
  - **IDE**: VS Code (`storage.json`), Antigravity, IntelliJ IDEA 2025.3, PyCharm 2026.1 nhớ ≈60 đường dẫn dự án trên `E:`
    (chủ yếu `E:\project\...`, `E:\aptech-learning\...`).
  - **Obsidian**: vault `E:\Obsidian`.
- **Đã gãy sẵn** (bằng chứng cho task T16): `E:\aptech-learning`, `E:\Java_Learning`, `E:\Learning_Machine_AI`
  không còn tồn tại nhưng IDE còn nhớ ≈30 mục; shortcut `F99`, `GearUPBooster`, `Learning Obsidion` trỏ vào thư mục không còn.
- Steam library nằm ở `C:` → game không bị ảnh hưởng trên máy này, nhưng danh sách bảo vệ game còn thiếu cho người khác.

Những thứ `relink` **đang** xử lý (`smart_drive/core/relink.py:393` `agent_files`): Claude Code, Claude desktop (kể cả MSIX),
Codex, Gemini CLI, Antigravity (chỉ `mcp_config.json`), Cursor (chỉ `mcp.json`), Windsurf (chỉ `mcp_config.json`),
Constellation, `.mcp.json` của ổ, lịch sử Claude Code, git worktree, `.venv`. **Không** xử lý: danh sách dự án gần đây của
IDE, JetBrains, Obsidian, shortcut, PATH, dịch vụ, registry.

---

## 2. Danh sách task

Thứ tự khuyến nghị: M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8. Phụ thuộc bắt buộc ghi ở mục 3.

### M0 — Sửa lỗi thật (nhỏ, độc lập)

#### T0. Test `gh` không phụ thuộc máy có cài `gh`
- **Vấn đề**: xem "Trạng thái test gốc" ở mục 0.
- **Sửa**: trong `tests/test_gitignore.py` (lớp `TestVisibilityFromMockedGh`), patch thêm `shutil.which` của module
  `smart_drive.core.gitignore` trả về một đường dẫn giả cho các test giả lập `gh` đã đăng nhập; giữ một test riêng cho trường hợp
  `which` trả `None` → `False`. Không đổi code sản phẩm.
- **Commit**: `test(gitignore): stop depending on an installed gh in the auth check test`

#### T1. Bảo vệ launcher `SmartDrive.*` ở gốc ổ
- **Vấn đề**: `PROTECTED_ROOT_FILE_PATTERNS` (`smart_drive/core/config.py:288`) có `smart_*` (có gạch dưới) nhưng không có
  `smartdrive.*`. `is_protected_root_file` (`config.py:310`) hạ chữ thường rồi `fnmatch`, nên `Quick_*`/`Setup_*` đã được bảo vệ,
  chỉ `SmartDrive.bat/.ps1/.sh/.command` bị hở → classify định dời chúng.
- **Sửa**: thêm mẫu `"smartdrive.*"` vào `PROTECTED_ROOT_FILE_PATTERNS`.
- **Test** (thêm vào `tests/test_compliance.py` hoặc `tests/test_classifier.py`, chọn file đang test `is_protected_root_file`):
  - Với **mọi** tên trong `ALL_20_LAUNCHER_NAMES` (lấy từ `tests/test_launchers_and_invariants_tier5.py`),
    `is_protected_root_file(name)` là `True`.
  - Ổ giả có `SmartDrive.bat`, `SmartDrive.ps1`: `ClassifierEngine` quét không đệ quy và `AutoZoner.generate_plan()` đều không
    trả về hai file này.
- **Commit**: `fix(config): protect SmartDrive.* launchers at the drive root`

#### T2. Không dời/không dọn file hệ thống Windows ở gốc ổ
- **Vấn đề**: `DumpStack.log.tmp` bị classify xếp vào `Unreadable`; ngoài ra luật Tier 3 `*.tmp` (`config.py:559`) khiến
  `clean --tier 3` nhắm vào nó. Các file cùng loại: `pagefile.sys`, `hiberfil.sys`, `swapfile.sys`, `DumpStack.log`.
- **Sửa**: thêm (dạng chữ thường) `pagefile.sys`, `hiberfil.sys`, `swapfile.sys`, `dumpstack.log`, `dumpstack.log.tmp`
  vào `PROTECTED_ROOT_FILES` (`config.py:253`). Sau đó **kiểm tra** ba nơi dùng chung hàm này đều bỏ qua:
  `ClassifierEngine._scan_and_classify` (`classifier.py:853-858`), `AutoZoner.classify_item` (`auto_zoner.py:212`),
  và `SecurityGuard.is_protected` (`purge_engine.py:165`) — nếu `SecurityGuard` không gọi `is_protected_root_file` cho
  file ở gốc thì nối vào.
- **Test**: ổ giả có các file trên ở gốc → classify không lên kế hoạch; quét rác Tier 3 không liệt kê chúng;
  `PurgeEngine` từ chối xóa chúng.
- **Commit**: `fix(config): never move or purge Windows system files at the drive root`

#### T3. `organize` tính lãng phí theo cluster thật của ổ
- **Vấn đề**: `AutoZoner._get_item_size` (`auto_zoner.py:265-289`) gọi `calculate_slack_bytes(sz)` với mặc định
  `CLUSTER_SIZE` = 512 KB (`config.py:56`). Trên NTFS 4 KB con số bị thổi phồng (22,6 GB cho thư mục 3,57 GB).
  `StorageAuditor` đã nhận `cluster_size` (`auditor.py:435`) và có ghi chú `slack_model_note` (`auditor.py:727`).
- **Sửa**:
  - `AutoZoner.__init__(self, root, cluster_size: Optional[int] = None)`: nếu `None` thì
    `info = detect_filesystem(self.root)` (`smart_drive/core/fsinfo.py:193`), dùng `info.block_size` nếu `> 0`,
    ngược lại dùng `CLUSTER_SIZE_BYTES`. Lưu `self.cluster_size`.
  - `_get_item_size` truyền `self.cluster_size` vào `calculate_slack_bytes`.
  - JSON của `organize --dry-run` (và kết quả `--apply`, công cụ MCP `ssd_auto_organize`) thêm trường `cluster_size`.
- **Không đổi** `audit` trong task này (nó đã có ghi chú; đổi mặc định của audit là quyết định của người dùng).
- **Test** (`tests/test_auto_zoner.py`): `AutoZoner(root, cluster_size=4096)` với file 1 byte → slack 4095;
  `unittest.mock.patch` `detect_filesystem` trong module `auto_zoner` trả `block_size=4096` → được dùng; trả `0` → 512 KB.
- **Commit**: `fix(auto_zoner): measure slack with the volume's real cluster size`

#### T4. Launcher không được coi thư mục repo là gốc ổ
- **Vấn đề**: `SmartDrive.bat:7-14` đặt `DRIVE_ROOT=%~dp0` khi cạnh nó có `smart_drive\`, rồi
  `SMART_DRIVE_ROOT=%DRIVE_ROOT%` (`SmartDrive.bat:45`). Chạy `E:\smart-drive-os\SmartDrive.bat` → mọi lệnh coi
  `E:\smart-drive-os` là cả ổ. Cùng mẫu này có trong **cả 20 launcher** (mỗi file gán `SMART_DRIVE_ROOT` đúng một lần;
  ví dụ `SmartDrive.ps1` dòng 7-13 và 53, `SmartDrive.sh` dòng 6-10 và 44).
- **Cách root được tìm** (`smart_drive/core/root.py`): `--root` → biến `SMART_DRIVE_ROOT` → đi ngược lên từ thư mục hiện tại tìm thư mục
  có *marker* (`.smart_drive/`, `.smart_drive_manager/`, `.metadata_never_index`) **và** *anchor* (`AGENTS.md`/`GEMINI.md`) hoặc là
  điểm mount. `E:\` có đủ marker và anchor, còn repo không có marker → đi ngược từ repo sẽ ra đúng `E:\`.
- **Sửa** (cả 20 file × 2 bản): tách hai biến:
  - `CODE_DIR`: thư mục chứa `smart_drive\` (thư mục script, hoặc thư mục cha nếu script nằm trong `launchers\`) — dùng cho
    `PYTHONPATH` và `cd`.
  - `SMART_DRIVE_ROOT`: **chỉ đặt** khi script **không** nằm cạnh code (tức đã được chép ra gốc ổ) → bằng thư mục script.
    Khi script nằm trong repo: **không đặt** để `root.py` tự dò. Nếu người dùng đã tự đặt `SMART_DRIVE_ROOT` từ trước thì giữ nguyên.
  - Dòng "Drive Root:" trong menu hiển thị `(auto)` khi không ép.
- **Test** (`tests/test_launchers_and_invariants_tier5.py`): với mỗi launcher, kiểm tra tĩnh rằng không còn gán
  `SMART_DRIVE_ROOT` vô điều kiện từ thư mục chứa code; thêm test hành vi cho `.sh` (chỉ POSIX, `skipUnless` có `bash`):
  dựng `tmp/drive/{.smart_drive/,AGENTS.md}/repo/{smart_drive/__main__.py giả in ra root}` rồi chạy launcher không tương tác
  (các launcher đều hỏi input và chờ Enter khi thoát, nên truyền sẵn stdin, ví dụ cho `Quick_Search.sh` một truy vấn rồi một dòng trống).
  Hai bản launcher vẫn phải giống hệt nhau.
- **Commit**: `fix(launchers): resolve the drive root instead of pinning it to the repo`

### M1 — Không dời thứ hệ thống đang dùng

#### T5. Module mới `smart_drive/core/system_refs.py`
- **Mục tiêu**: biết thư mục nào đang được Windows/ứng dụng tham chiếu, thay vì đoán theo tên.
- **API đề xuất**:
  ```python
  @dataclass(frozen=True)
  class SystemRef:
      kind: str    # "path" | "service" | "installed_app" | "startup" | "shortcut" | "uninstaller"
      source: str  # vd. "PATH (User)", "HKLM\\...\\Services\\MSSQLSERVER", "C:\\Users\\X\\Desktop\\Git Bash.lnk"
      target: str  # đường dẫn trên đĩa mà nguồn đó trỏ tới

  class SystemRefIndex:
      @classmethod
      def collect(cls, home: Optional[Path] = None, reader: Optional["RegistryReader"] = None) -> "SystemRefIndex": ...
      def refs_under(self, folder: str) -> List[SystemRef]   # target == folder hoặc nằm bên trong
      def to_dict(self) -> Dict[str, Any]: ...

  def looks_like_installed_app(folder: str) -> Optional[str]  # tên uninstaller nếu thấy
  ```
- **Nguồn (Windows)**:
  1. PATH: `HKCU\Environment\Path` và `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\Path`
     (mở rộng `%VAR%` bằng `winreg.ExpandEnvironmentStrings`).
  2. Dịch vụ: `HKLM\SYSTEM\CurrentControlSet\Services\*\ImagePath` — bỏ ngoặc kép và tham số, xử lý tiền tố `\??\`,
     đường dẫn tương đối `system32\...`.
  3. App đã cài: `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*`, bản `WOW6432Node`, và `HKCU\...\Uninstall\*` —
     các giá trị `InstallLocation`, `DisplayIcon` (bỏ `,0`), `UninstallString` (token đầu).
  4. Khởi động: `HKCU/HKLM\Software\Microsoft\Windows\CurrentVersion\Run` và `RunOnce`.
  5. Shortcut `.lnk` trong `%APPDATA%\Microsoft\Windows\Start Menu`, `%ProgramData%\Microsoft\Windows\Start Menu`,
     `%USERPROFILE%\Desktop`, `%PUBLIC%\Desktop`, `%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar`.
     Đọc `.lnk` bằng Python thuần theo đặc tả MS-SHLLINK: header 0x4C byte, `LinkFlags` (u32 tại 0x14); nếu cờ
     `HasLinkTargetIDList` (0x1) thì bỏ qua IDList (u16 kích thước); nếu `HasLinkInfo` (0x2) đọc `LinkInfo` → `LocalBasePath`
     (offset tại +16, bản Unicode tại +28 khi `LinkInfoHeaderSize >= 0x24`). Lỗi parse → bỏ qua file. Giới hạn số file (vd. 5.000).
  6. `looks_like_installed_app`: thư mục có `unins000.exe`, `uninstall.exe`, `uninst.exe` hoặc `Uninstall*.exe` ở cấp đầu.
- **Hệ khác Windows**: chỉ PATH từ `os.environ`; còn lại rỗng.
- **Thiết kế cho test**: đọc registry qua một lớp `RegistryReader` nhỏ (mặc định dùng `winreg`), test truyền reader giả dạng dict.
  So khớp đường dẫn không phân biệt hoa thường trên Windows và **đúng ranh giới thư mục** (`E:\Git` không khớp `E:\GitHub`).
  Bắt lỗi theo từng key/giá trị, không để một key hỏng làm hỏng cả lần quét. Mục tiêu < 2 giây.
- **Test** `tests/test_system_refs.py`: parser `.lnk` với bytes tự dựng trong test (không commit file nhị phân);
  chuẩn hóa `ImagePath` (`"C:\\X\\a.exe" -k`, `\??\C:\...`, `system32\drivers\x.sys`); tách PATH có `%VAR%`;
  `refs_under` (hoa thường, ranh giới); reader giả có key lỗi.
- **Commit**: `feat(core): find folders that Windows services, PATH, installs and shortcuts depend on`

#### T6. `organize`/`classify` không bao giờ dời thư mục hệ thống đang dùng
- **Sửa**:
  - `AutoZoner`: tạo `SystemRefIndex` một lần cho mỗi lần chạy (cho phép inject qua tham số để test). Trong `classify_item`
    (sau guard whitelist, `auto_zoner.py:206-210`) và kiểm tra lại trong `apply_plan` (`auto_zoner.py:353-372`) trước khi dời:
    nếu `refs_under(path)` không rỗng hoặc `looks_like_installed_app(path)` → không dời.
  - `ClassifierEngine`: cùng kiểm tra ở chỗ đang gọi `is_protected_root_dir/file` (`classifier.py:599`, `:674`, `:800`, `:856`)
    và trong `execute_relocation` (`classifier.py:929`).
  - Kết quả dry-run và JSON có thêm danh sách `protected_by_system`: `[{path, refs: [{kind, source}] (tối đa 3)}]`;
    bản text in một dòng/thư mục, ví dụ `Skipped E:\Git — used by PATH, 4 shortcuts, installed app "Git"`.
  - **Không** thêm cờ để bỏ qua bảo vệ này.
- **Test**: ổ giả có `Git`, `SQL`, `tool`; index giả đánh dấu `Git` (PATH) và `SQL` (service) → `generate_plan` loại hai thư mục
  này và liệt kê trong `protected_by_system`; `apply_plan` với một kế hoạch cũ có chứa chúng vẫn từ chối dời.
  Thư mục có `unins000.exe` cũng bị loại.
- **Commit**: `fix(auto_zoner): never move folders that Windows depends on` (nếu phần classifier lớn, tách
  `fix(classifier): ...` thành commit riêng).

#### T7. Bảo vệ thư viện game phổ biến
- **Vấn đề**: `PROTECTED_ROOT_DIRS` (`config.py:147`) chỉ có `steamlibrary`, `riot games`, `ldplayer`, `fo4`, `32837`.
- **Sửa**: thêm `steamapps`, `steam`, `epic games`, `xboxgames`, `battle.net`, `gog games`, `gog galaxy`, `ea games`,
  `electronic arts`, `ubisoft`, `ubisoft game launcher`, `garena`, `bluestacks`, `games`.
  (`is_protected_root_dir` so khớp từng phần của đường dẫn, `config.py:235`, nên cả `clean` cũng không đụng vào file bên trong.)
- **Test**: `is_protected_root_dir` cho từng tên; `clean` dry-run trên ổ giả có `Epic Games/x/a.log` không liệt kê file đó.
- **Commit**: `fix(config): protect common game library folders`

### M2 — Relink cho IDE và ứng dụng ghi chú

#### T8. Relink danh sách dự án gần đây của VS Code-family và JetBrains
- **Vấn đề**: `PathMapper` chỉ hiểu đường dẫn thường (`_split`, `relink.py:143`), không hiểu URI `file:///e%3A/project/x`.
  `agent_files` không có file IDE nào.
- **Sửa**:
  - `PathMapper.map_uri(uri)`: nhận `file:///...`, giải mã phần path, ánh xạ, rồi mã hóa lại **đúng kiểu gốc**
    (giữ `%3A` hay `:`, giữ chữ hoa/thường của ký tự ổ, dấu `/`). URI khác (`vscode-remote://`, `http`) để nguyên.
  - VS Code-family: với `app` trong `Code`, `Code - Insiders`, `VSCodium`, `Cursor`, `Windsurf`, `Antigravity`:
    - `<config>/<app>/User/globalStorage/storage.json` (JSON).
    - `<config>/<app>/User/globalStorage/state.vscdb` (SQLite, bảng `ItemTable(key, value)`, key
      `history.recentlyOpenedPathsList` → JSON `{"entries": [{"folderUri"|"fileUri"|"workspace": {"configPath"}}]}`).
      Dùng `sqlite3` chuẩn; sao lưu thành `<file>.bak.smart-drive` trước khi ghi (theo `BACKUP_SUFFIX`, `relink.py:41`);
      ghi trong transaction; nếu file đang bị khóa → cảnh báo, không lỗi.
    - `<config>` = `%APPDATA%` (Windows), `~/Library/Application Support` (macOS), `~/.config` (Linux).
  - JetBrains: `<config>/JetBrains/<Product><ver>/options/recentProjects.xml` và `trusted-paths.xml`. Thay theo văn bản
    (giữ nguyên định dạng), hiểu dạng `E:/project/x` và macro `$USER_HOME$`.
  - Thêm tên tiến trình vào `AGENT_PROCESSES` (`relink.py:51`) để `--wait` chờ: `code`, `cursor`, `windsurf`, `antigravity`,
    `idea64`, `pycharm64`, `webstorm64`, `clion64`, `rider64`, `goland64`, `phpstorm64`, `datagrip64`, `rustrover64`.
  - Ghi chú trong README: trạng thái riêng của workspace (tab đang mở…) nằm trong thư mục băm `workspaceStorage` sẽ không theo sang;
    dự án vẫn mở được.
- **Test** `tests/test_relink_ide.py` (đọc `tests/test_relink.py` trước để theo phong cách): dựng `app_data` giả có
  `storage.json`, `state.vscdb` (tạo bằng `sqlite3` trong test), `recentProjects.xml`; chạy `relink([...], home=tmp, app_data=tmp)`
  ở dry-run rồi thật → đúng mục được sửa, mục khác giữ nguyên, có bản sao lưu, kiểu URI được giữ, so khớp ký tự ổ không phân biệt hoa thường.
- **Commit**: `feat(relink): follow moved folders in VS Code-family and JetBrains IDEs`

#### T9. Relink vault Obsidian và shortcut Windows (hai commit)
- **Obsidian**: `<config>/obsidian/obsidian.json` (`{"vaults": {"<id>": {"path": "E:\\Obsidian", ...}}}`) → thêm vào `agent_files`
  dạng JSON, nhãn `Obsidian`, tiến trình `obsidian`.
  Commit: `feat(relink): follow moved Obsidian vaults`
- **Shortcut**: chỉ sửa các `.lnk` mà `SystemRefIndex` (T5) tìm thấy trỏ vào thư mục đã dời. Ghi bằng PowerShell COM
  (`WScript.Shell` → `CreateShortcut` → `TargetPath`, `WorkingDirectory`, `IconLocation`) qua `subprocess`, chỉ trên Windows;
  sao lưu `.lnk` trước; không có PowerShell → cảnh báo. Dry-run liệt kê shortcut sẽ sửa.
  Test: kiểm tra lệnh được dựng đúng với `subprocess.run` giả; test thật chỉ chạy khi `win32` **và** biến môi trường
  `SMART_DRIVE_TEST_LNK=1`.
  Commit: `feat(relink): repoint Windows shortcuts after a move`

### M3 — Lưới an toàn

#### T10. `smart-drive undo` — hoàn tác đợt dời gần nhất
- **Nền có sẵn**: nhật ký `<root>/.smart_drive/moves.jsonl`, mỗi dòng `{"time","tool","moves":[[old,new],...]}`
  (`record_moves`, `relink.py:1170`; `last_moves`, `relink.py:1216`).
- **Sửa**:
  - Lệnh mới `smart-drive undo [--apply] [--json] [--root]` (mặc định chỉ xem trước, giống `clean`). Đăng ký trong
    `smart_drive/cli/main.py` (`add_parser` + bảng `dispatch` ở `main.py:560-585`), handler `cmd_undo` trong `smart_drive/cli/cmd_undo.py`.
  - Lấy đợt gần nhất **chưa bị hoàn tác**; với từng cặp, chỉ dời `new → old` khi `new` tồn tại và `old` chưa tồn tại,
    ngược lại báo xung đột và bỏ qua. Dùng lại `_move` (`auto_zoner.py:50`).
  - Ghi một dòng nhật ký mới `{"tool": "undo", "undoes": "<time của đợt gốc>", "moves": [[new, old], ...]}`; khi tìm đợt để hoàn tác,
    bỏ qua đợt đã có dòng `undoes` trỏ tới.
  - Sau khi dời: gọi `after_moves(root, moves_ngược, "undo")` (`relink.py:1643`) để sửa agent/IDE, và cập nhật chỉ mục như
    `cmd_organize` đang làm. Không xóa thư mục phân vùng chuẩn.
- **Test** `tests/test_undo.py`: ổ giả → `AutoZoner.apply_plan` → `undo` xem trước liệt kê đúng → `undo --apply` trả về chỗ cũ →
  chạy lại không còn gì để hoàn tác; trường hợp xung đột bị bỏ qua. Mọi relink trong test dùng `home`/`app_data` tạm
  (xem `cmd_organize.py:69` đang gọi `after_moves` thế nào và patch tương ứng).
- **Commit**: `feat(cli): add smart-drive undo to reverse the last organize/classify batch`

#### T11. `clean --apply` chuyển vào thư mục cách ly thay vì xóa vĩnh viễn
- **Vấn đề**: `PurgeEngine._make_writable_and_remove_file` dùng `os.unlink` (`purge_engine.py:311`), thư mục dùng `shutil.rmtree`
  (`purge_engine.py:348`).
- **Sửa**:
  - `PurgeEngine(..., quarantine: bool = True)` khi gọi từ CLI `clean --apply`: dời bằng `os.rename` vào
    `<root>/.smart_drive/quarantine/<YYYYmmdd-HHMMSS>/<đường dẫn tương đối>` và ghi `manifest.json` của đợt
    (`[{original, quarantined, size, tier}]`). Nếu rename lỗi → **không xóa**, báo lỗi.
  - Cờ mới: `clean --list-quarantine`, `clean --restore <đợt|latest>` (không ghi đè file đã tồn tại),
    `clean --purge-quarantine --older-than <ngày>` (mặc định xem trước, `--apply` mới xóa thật),
    `clean --apply --no-quarantine` (hành vi cũ).
  - Xác nhận scanner, indexer, junk detector, dup **không quét** `.smart_drive/quarantine` (kiểm tra cách chúng bỏ qua `.smart_drive`).
  - Công cụ MCP `ssd_clean`: mặc định cũng cách ly. Không đổi tên/kiểu các tham số đang có; nếu cần thêm tham số → hỏi người dùng (mục 0).
- **Test** (`tests/test_cleaner.py`, `tests/test_cli_clean_json.py`): apply → file nằm trong quarantine kèm manifest; restore trả lại;
  restore không ghi đè; purge theo `--older-than` (inject thời gian); mục được bảo vệ không bao giờ bị cách ly; `--no-quarantine` xóa thật.
- **Commit**: `feat(clean): quarantine purged items so they can be restored`

### M4 — Offload an toàn hơn

#### T12. `offload` chờ/từ chối khi ứng dụng của cache đang chạy
- **Vấn đề**: `offload_cache(name, target_drive, dry_run, force)` (`offloader.py:509`) không kiểm tra tiến trình đang chạy.
- **Sửa**: bảng tiến trình theo cache — `docker_wsl`: `Docker Desktop`, `com.docker.backend`, `vmmem`, `wsl`;
  `ollama`: `ollama`, `ollama app`; `npm`: `node` (chỉ cảnh báo); `gradle`: `java` (chỉ cảnh báo); `cargo`: `cargo`, `rustc`.
  Đưa `_running_processes` (`relink.py:61`) ra module chung `smart_drive/core/processes.py` (commit `refactor` riêng, không đổi hành vi).
  Đang chạy → từ chối, trừ khi `--force`; thêm `--wait [PHÚT]` giống `relink`.
- **Test**: patch danh sách tiến trình trả `Docker Desktop.exe` → `docker_wsl` bị từ chối; `--force` thì chạy (dùng chế độ sandbox
  của offloader, xem `offloader.py:418`).
- **Commit**: `refactor(core): share the running-process probe`, rồi `fix(offload): wait for Docker, Ollama and similar apps to close before moving their cache`

### M5 — Sắp xếp thông minh hơn

#### T13. Nhận ra thư mục chứa nhiều repo (vd. `E:\project`)
- **Vấn đề**: `classify_item` chỉ xét entry cấp đầu với `PROJECT_INDICATORS` (`auto_zoner.py:114`, `:224-231`).
- **Sửa**: thư mục có **≥ 2** thư mục con cấp đầu chứa chỉ báo dự án → `03_Development_Projects/<tên>`,
  lý do `"Folder of N development projects"`. Chỉ xét tối đa 200 thư mục con, bỏ qua link/junction.
- **Phụ thuộc**: **T8 phải xong trước** — trên máy này dời `E:\project` sẽ làm gãy ≈30 mục IDE nếu chưa có relink IDE.
- **Test**: `project/{a/.git, b/package.json, c/}` → được lên kế hoạch; `misc/{a/.git}` → không.
- **Commit**: `feat(auto_zoner): file folders of repositories under 03_Development_Projects`

#### T14. So khớp từ khóa tên thư mục theo nguyên từ
- **Vấn đề**: `auto_zoner.py:233-246` dùng `k in base_lower` → `note` khớp `Notepad++`, `book` khớp `Facebook`, `dump` khớp `DumpStack`.
- **Sửa**: tách tên thành token (theo ký tự không phải chữ/số và theo camelCase), so khớp token với tập từ:
  `tool(s)`, `utility/utilities`, `script(s)`, `note(s)`, `book(s)`, `course(s)`, `tutorial(s)`, `learn/learning`,
  `model(s)`, `weights`, `checkpoint(s)`, `gguf`, `archive(s)`, `backup(s)`, `dump(s)`, `old`.
- **Lưu ý**: nếu test hiện có cố ý khóa hành vi substring → **dừng và hỏi người dùng** (mục 0).
- **Commit**: `fix(auto_zoner): match folder keywords as whole words`

### M6 — "Khám ổ đĩa" cho người dùng thường

#### T15. `smart-drive checkup`
- **Mục tiêu**: một lệnh **chỉ đọc**, ra điểm sức khỏe 0–100 và 3 việc nên làm, mỗi việc kèm đúng lệnh cần chạy.
- **Gộp từ**: hệ tệp/cluster (`fsinfo`), `core/health.py`, tóm tắt dung lượng (`StorageAuditor`), rác Tier 1–2 có thể thu hồi
  (junk detector, dry-run), dung lượng quarantine (T11), đường dẫn agent cũ (`relink.find_moved`, `relink.py:1396`),
  tham chiếu gãy (T16), cache lớn trên `C:` (`offloader.get_scan_summary`, chỉ Windows), thư mục được bảo vệ vì hệ thống dùng (T5).
  `dup` **mặc định tắt** (tốn thời gian), bật bằng `--dup`.
- **Đầu ra**: `--json`; `--lang vi|en` (mặc định `vi` nếu locale bắt đầu bằng `vi`, ngược lại `en`); công thức điểm minh bạch,
  ghi rõ trọng số trong code. Lưu kết quả lần trước vào **một** file `<root>/.smart_drive/checkup.json` (ghi đè) để in
  "Lần trước 62 → giờ 85". Mục tiêu < 30 giây trên ổ 150 GB. Mã thoát luôn 0.
- **Launcher**: thêm mục `[9] Checkup` vào menu `SmartDrive.*` (4 loại × 2 bản, giữ giống hệt nhau).
- **MCP** (tùy chọn, commit riêng, hỏi người dùng trước): công cụ `ssd_checkup` theo mẫu `TOOL_HANDLERS` trong
  `smart_drive/mcp/server.py`, đủ annotation `readOnlyHint`…
- **Test** `tests/test_checkup.py`: ổ giả có rác + cấu hình cũ → JSON đủ khóa, điểm trong [0,100], hành động sắp theo ưu tiên, chuỗi vi/en.
- **Commit**: `feat(cli): add smart-drive checkup health score with next steps`

#### T16. "Bác sĩ đường dẫn": `smart-drive paths --broken`
- **Mục tiêu**: liệt kê shortcut, mục IDE (T8), mục PATH, cấu hình agent trỏ tới chỗ **không còn tồn tại**, và đoán nơi thư mục đã tới
  (dùng lại logic `find_moved`). `--fix` chỉ sửa các khớp chắc chắn (qua `relink`), có `--dry-run`. Shortcut mồ côi không có chỗ khớp
  → chỉ gợi ý, **không tự xóa**. Cũng được `checkup` gọi.
- **Kỳ vọng trên máy thật** (chỉ để kiểm tra thủ công, đừng ghi vào test): thấy các mục IDE trỏ `E:\aptech-learning`,
  `E:\Java_Learning`, `E:\Learning_Machine_AI` và shortcut `F99`, `GearUPBooster`, `Learning Obsidion`.
- **Test**: `app_data` giả có `storage.json` trỏ thư mục không tồn tại; `.lnk` tự dựng trỏ chỗ không tồn tại.
- **Commit**: `feat(cli): add smart-drive paths --broken to find stale shortcuts and IDE entries`

#### T20. `Smart_Organize` — launcher "dời an toàn" cho người dùng thường
- **Mục tiêu**: tạo đủ 4 bản `.bat`/`.ps1`/`.command`/`.sh` ở gốc repo và `launchers/` (giống hệt nhau như 24 launcher tổng cộng, cập nhật test inventory), cùng luồng: `dry-run` → chờ đóng `busy_apps` → gõ `YES` → `organize --apply --json` → hiện rõ mục KHÔNG dời được kèm lý do → kiểm tra (`relink --dry-run` không còn gì để sửa, git/worktree, venv) → `R` thử lại / `U` hoàn tác / `Enter` giữ nguyên; tiếng Anh mặc định, tiếng Việt khi hệ thống là `vi-*` hoặc `SMARTDRIVE_LANG=vi`; ghi log vào `<root>/.smart_drive/smart-organize.log`. Thêm vào menu `SmartDrive.*` một mục `[10]` gọi nó.
- **Test**: test cho chuỗi i18n và cho chế độ `SD_SELFTEST=1` (chỉ đọc).
- **Commit**: `feat(launchers): Smart_Organize — safe guided organizer launcher for everyday users`

### M7 — Báo cáo trực quan

#### T17. `smart-drive audit --html [file]`
- Một file HTML **tự chứa** (CSS/JS nội tuyến, không CDN, chạy offline): treemap squarified theo dung lượng thư mục (sâu ≤ 3, gộp mục nhỏ
  thành "khác"), bảng 50 thư mục/file lớn nhất, slack theo cluster thật (như T3). Dữ liệu lấy từ **một** lần quét của auditor, không quét hai lần.
  Mọi tên file phải qua `html.escape`. Mặc định ghi đè `<root>/.smart_drive/reports/audit.html` (một file, tránh rác file nhỏ trên exFAT).
- **Test**: sinh trên cây giả → HTML có tên đã escape, không chứa URL ngoài, kích thước hợp lý.
- **Commit**: `feat(audit): export a self-contained HTML treemap`

### M8 — Tài liệu

#### T18. Bỏ báo cáo test cũ
- `TEST_READY.md` ghi bản v1.1.0 và đường dẫn macOS `/Users/duongnad/...` (`TEST_READY.md:5-8`); không file nào tham chiếu tới nó.
- Xóa file. Commit: `docs: drop the stale TEST_READY report`

#### T19. Cập nhật README
- `README.md`, `README_VN.md`, `README_INTERNAL.md`: lệnh mới (`undo`, `checkup`, `paths --broken`, `audit --html`, các cờ quarantine của
  `clean`), relink giờ phủ IDE/Obsidian/shortcut, những gì được bảo vệ (thư mục hệ thống dùng, thư viện game), viết "ổ phụ của bạn
  (`D:`, `E:`…)" thay vì mặc định `D:`, nhắc dùng `py -m smart_drive` trên Windows khi `python` không có trong PATH,
  sửa số lượng lệnh trong bảng tính năng. Cập nhật dòng lệnh tương ứng trong `AGENTS.md`, `GEMINI.md`, `CLAUDE.md` nếu có.
- Commit: `docs: document undo, quarantine, checkup and the new safety guards`

### M9 — Tùy chọn (chỉ làm khi người dùng đồng ý)
- Quy tắc dọn cho người thường: `Downloads`, `Desktop`, `Zalo Received Files`, installer cũ, cặp `x.zip` + thư mục `x/` đã giải nén.
- Báo cáo game lâu không chơi (chỉ đọc): `steamapps/libraryfolders.vdf`, `appmanifest_*.acf` (`SizeOnDisk`),
  `userdata/*/config/localconfig.vdf` (`LastPlayed`) → gợi ý gỡ qua Steam, không tự xóa.
- Tìm theo nội dung `.docx`/`.txt`/`.md` (docx là zip + XML, làm được bằng thư viện chuẩn).

---

## 3. Phụ thuộc giữa các task

| Task | Cần xong trước |
|---|---|
| T0–T4, T7, T14, T18 | — (độc lập) |
| T6 | T5 |
| T9 (shortcut) | T5 |
| T13 | **T8** (không thì dời `E:\project` làm gãy IDE) |
| T15 | T11, T16 (phần nào chưa có thì bỏ qua mục đó trong checkup) |
| T16 | T5, T8 |
| T20 | T5, T8, T10 |
| T19 | các task đã xong |

---

## 4. Kiểm tra thủ công trên máy thật (CHỈ dry-run / chỉ đọc)

Chạy từ `E:\smart-drive-os`:

```bash
py -m smart_drive classify E:\ --root E:\ --no-recursive --dry-run --json
py -m smart_drive organize --root E:\ --dry-run --json
py -m smart_drive relink "E:\project" "E:\03_Development_Projects\project" --dry-run
py -m smart_drive checkup
py -m smart_drive paths --broken
```

Kỳ vọng:
- Sau M0: classify không còn `SmartDrive.bat` và `DumpStack.log.tmp`; `slack_bytes` của `E:\tool` nhỏ hơn rất nhiều so với 22,6 GB,
  `cluster_size` = 4096.
- Sau M1: kế hoạch organize/classify không bao giờ chứa `Git`, `SQL`, `PyCharm 2025.3.1`, `MSI Afterburner`, `UltraViewer`,
  `Obsidian`, `WinRAR`, `Cisco Packet Tracer 9.0.0`… và JSON giải thích lý do trong `protected_by_system`.
- Sau M2: relink dry-run liệt kê `storage.json`/`state.vscdb` của VS Code và Antigravity, `recentProjects.xml` của IntelliJ/PyCharm.
- Sau M6: `paths --broken` thấy các mục gãy liệt kê ở mục 1.

---

## 5. Hoàn thành khi

- `py -m unittest discover tests` xanh sau **mỗi** commit (trừ test chập chờn `test_14_...` đã nêu ở mục 0).
- Mỗi task đúng một commit (hoặc đúng số commit đã ghi), có test hồi quy, thông điệp đúng quy ước.
- README đã cập nhật (T19).
- Báo lại cho người dùng: danh sách commit, task nào bỏ qua/hoãn và vì sao, kết quả kiểm tra thủ công ở mục 4.

---

## 6. Sau v1.4 — kế hoạch phát triển tiếp

> Thống nhất với người dùng ngày 2026-10-20, sau khi chạy thử v1.4 trên ổ `E:` thật. Nguyên tắc: **đo trước rồi mới tối ưu**,
> **an toàn hơn tốc độ**, giữ Python làm thân chính và giữ `dependencies = []`. Đầu ra `--json` của các lệnh và schema công cụ MCP
> là "hợp đồng" giữa các phần: mọi thay đổi lớn (kể cả chuyển ngôn ngữ) phải giữ nguyên chúng hoặc đổi có chủ đích.

### 6.0 Hiện trạng (đo trên `E:`: NTFS 4 KB, 167 GB, 342 nghìn file, 40 nghìn thư mục)

| Lệnh | Trước | Hiện tại |
|---|---|---|
| `checkup` | 571 s | 47–65 s |
| `clean` (dry-run) | 491 s | 8–9 s |
| `organize --dry-run` | 41 s | 4 s |
| `dup` | > 15 phút, không in gì | 28 s lần đầu (Defender quét file mới mở), 2–3 s các lần sau |
| `classify` (gợi ý) · `audit` · `repos` · `update` | — | 54 s · 26 s · 33 s · 9,5 s |

- Đã vào `main`: PR #10 (7 lỗi nghiêm trọng, hỏi trước khi cài MCP, vá lỗ hổng bảo vệ thư mục, tài liệu), #11 (tăng tốc
  `checkup`/`clean`, dung lượng rác thư mục thật, chỉ trừ điểm đường dẫn hỏng trên ổ), #12 (hỏi trước khi đăng ký MCP vào agent).
- Chạy thử trên máy thật lộ ra thêm lỗi, sửa trên nhánh `fix/real-drive-safety`:
  - `clean --tier 2` từng xóa vĩnh viễn `dist`/`build` trong `node_modules`, `site-packages` và trong phần mềm đã cài
    (Arduino IDE, Git). Giờ: danh sách Tier 2 trên `E:` từ 4940 mục / 2,77 GiB còn 3828 mục / 0,55 GiB, 0 mục trong cây phụ
    thuộc hay phần mềm đã cài; `build`/`dist`/`.cache` chỉ tính ở gốc dự án, bỏ qua thứ git theo dõi; `checkup` nói rõ là xóa
    vĩnh viễn và gợi ý xem trước.
  - Quy tắc Tier 3 `core.*` khớp nhầm 334 file mã nguồn/`.json`/`.map` (như `core.py`); giờ chỉ khớp `core` và `core.<số>`.
  - `classify` từng định tách 861 file khỏi thư mục ứng dụng (EOS Client, Composer, SEB). Giờ mặc định chỉ xét file nằm ngay
    trong thư mục được quét (0 lần chuyển); `--recursive` giữ nguyên mọi thư mục có chương trình, script, dự án, git hay chứa
    thư mục chương trình (22 thư mục trên `E:`), kể cả khi chỉ định thẳng vào chúng.
  - Công cụ MCP từ chối đường dẫn tuyệt đối khi gốc lưu trữ là gốc ổ (`E:\`); `sub_dir` nằm trong phần mềm đã cài không được
    nhận ra. Cả hai đã sửa.
  - `dup` quét cả `node_modules`/venv nên không xong; slack tính theo cluster 512 KB. Giờ bỏ qua cây phụ thuộc, cache và phần
    mềm đã cài, băm song song, báo tiến độ, tính slack theo cluster thật.

### 6.1 Giai đoạn A — Ổn định và phát hành v1.4.x (làm trước)

#### A1. Chạy thử có hướng dẫn trên máy thật
Sao lưu → `checkup` → `organize --dry-run` / `clean` / `classify --dry-run` → đóng Claude, Antigravity, VS Code, JetBrains →
`Smart_Organize.bat` (hoặc `organize --apply`) → mở lại các IDE/agent và kiểm tra dự án → thử `undo --apply` trên một đợt nhỏ.
Ghi kết quả và số đo vào bảng 6.0. Đây là điều kiện để phát hành.

**Vòng đầu đã chạy (2026-10-20, người dùng cho phép chạy thật trên `E:`):**
- Sandbox trên `E:` (chỉ file do kịch bản tạo): `clean --apply` (Tier 1–2 xóa đúng chỗ, Tier 3 vào khu cách ly rồi
  `--restore latest`), `classify --apply` rồi `undo --apply`, `dup` — **42/42** kiểm tra đạt.
- Thật: `organize --apply` chuyển `E:\project` (17 repo) và `E:\tool`, cập nhật 13 file cấu hình IDE/agent, git và worktree vẫn
  chạy; `undo --apply` trả cả hai về, không thêm đường dẫn hỏng nào — **19/19** đạt.
- Phát hiện: tiến trình `git fsmonitor--daemon` của repo bật `core.fsmonitor` khóa `E:\project`, làm lần chuyển đầu báo
  "Access is denied" (công cụ báo lỗi đúng, không chuyển dở dang). Đã sửa trên nhánh này (xem A6).
- Còn lại cho người dùng: đóng IDE/agent, chạy `Smart_Organize.bat`, mở lại IDE/agent và kiểm tra bằng mắt.

#### A2. Phiên bản và nhật ký thay đổi ✅
Xong trên nhánh `fix/real-drive-safety`: `1.4.0` ở `pyproject.toml`, `smart_drive/__init__.py` và `SERVER_VERSION` của MCP,
`CHANGELOG.md`, test giữ ba chuỗi khớp nhau. Còn: tạo tag `v1.4.0` khi nhánh vào `main` (badge và link trong CHANGELOG trỏ tới tag).

#### A3. Ký tự xuống dòng của launcher ✅
Xong: `.gitattributes` (`*.sh`, `*.command` → LF; `*.bat`, `*.cmd`, `*.ps1` → CRLF; ảnh là binary) kèm test, kể cả bản ZIP
`git archive` mà GitHub cho tải. Checkout cũ cần một lần `git rm --cached -q -- '*.sh' '*.command'` rồi
`git checkout HEAD -- '*.sh' '*.command'`.

#### A4. Bản `.exe` cho người dùng thường
Đóng gói thành một thư mục chạy được không cần cài Python (PyInstaller hoặc Nuitka, hoặc Python embeddable kèm launcher),
build trong CI cho Windows. Công cụ build chỉ dùng lúc đóng gói, không thành dependency khi chạy. Lưu ý SmartScreen: cần ký số
nếu phát hành rộng.

**Hoãn (2026-10-21, theo quyết định của chủ dự án):** người dùng coding agent cài từ PyPI bằng `uvx` hoặc `pipx` (uv còn tự
tải được cả Python), nên bản `.exe` chỉ có ích cho người không có Python mà vẫn muốn chạy các launcher `.bat`. Xem lại khi có
nhóm người dùng đó. Thay vào đó workflow `.github/workflows/release.yml` phát hành gói lên PyPI khi có tag `v*`; chi phí và các
cách ký số (nếu sau này phát hành `.exe`) ghi trong `docs/RELEASING.md`.

#### A5. Ngân sách hiệu năng
Script benchmark tạo cây thư mục giả cỡ 200 nghìn file (có `node_modules`, venv, `__pycache__`) và đo `checkup`, `clean`,
`organize --dry-run`, `dup`. Ngân sách đề xuất trên ổ cỡ 200 GB / 400 nghìn file: `checkup` < 60 s, `clean` < 30 s, `dup` < 5 phút.
Chạy thủ công trước mỗi bản phát hành; vượt ngân sách thì sửa trước khi phát hành.

#### A6. Giới hạn đã biết (mỗi mục một commit, có test; mục "cần xác minh" phải tái hiện trước khi sửa)
- **Relink**: Android Studio (cấu hình ở `%APPDATA%\Google\AndroidStudio*`, không nằm trong `JetBrains`); `relink` chưa trỏ lại
  `.lnk` ở gốc ổ; shell item của Windows 7/XP, đích UNC và `%VAR%` trong khối extra-data của `.lnk`; trạng thái từng workspace
  của VS Code (`workspaceStorage`, các tab đang mở) không đi theo. Console script (`pip.exe`, `fastapi.exe`…) và script
  `activate*` của venv đã được tự trỏ lại từ 1.4.1 ✅; còn: launcher do uv tạo (chưa xác minh định dạng), venv nằm sâu hơn
  một cấp dưới thư mục được chuyển, dòng `command =` trong `pyvenv.cfg` (chỉ để tham khảo).
- **Khóa thư mục khi chuyển** ✅ (1.4.1): lỗi "Access is denied" giờ chỉ ra thư mục con đang bị giữ (dò chỉ đọc) và nhận ra
  thư mục ghim trong Quick Access của Explorer. Bài học trên `E:` (2026-10-20): `…\Nop_bai\Images` được ghim trong Quick
  Access, `explorer.exe` giữ handle và mở lại sau mỗi lần đăng nhập, nên khởi động lại máy hay chạy bằng quyền admin đều không
  giúp; phải bỏ ghim rồi khởi động lại Explorer. Còn: Restart Manager không nêu được tiến trình giữ một *thư mục*.
- **Chưa hỗ trợ**: Visual Studio (bản đầy đủ), Sublime Text, Zed, Eclipse, NetBeans, Notepad++.
- **`paths --broken` / `checkup`**: đường dẫn hỏng trỏ vào ổ nhưng không tự sửa được vẫn bị trừ điểm; thêm lựa chọn gỡ mục
  cũ khỏi danh sách gần đây của IDE/agent (có sao lưu, chỉ khi người dùng xác nhận).
- **Launcher `.bat`** ✅: đã xác nhận `EnableDelayedExpansion` làm hỏng đường dẫn và từ khóa chứa `!` (thư mục `drive!one`
  làm `cd` thất bại, `wow! a!b!c` thành `wowbc`) và đã tắt nó, chỉ bật quanh một dòng `echo`. Cùng đợt: `&` và `>` chưa escape
  trong `Quick_Audit.bat`/`Quick_Search.bat` (dòng `size:>10MB` từng ghi ra file rác tên `10MB`). Còn: tên thư mục chứa `%VAR%`
  bị `cmd /c` tự mở rộng, launcher không chặn được.
- **Dò tiến trình** ✅ (1.4.2): khi `tasklist`/`ps` lỗi hoặc quá 10 s, `offload --move`/`--revert` không còn coi là "không
  có gì đang chạy": cache có tiến trình chặn (Docker/WSL, Ollama, cargo) bị từ chối, `--wait` chờ tới khi đọc được danh sách,
  `--force` vẫn dời kèm cảnh báo; `organize`/`undo` báo `busy_apps_unknown` và `Smart_Organize` dừng ở bước 2 hỏi người dùng
  đóng agent/IDE. `relink` vốn đã coi danh sách không đọc được là "vẫn đang chạy". Còn: `verify_post_organize` của
  `Smart_Organize` chưa xét `busy_apps_unknown`, và khi bước kiểm tra lại bị lỗi (exception) thì vẫn coi là "không có gì mở".
- **Khu cách ly**: mã đợt theo giờ máy cục bộ (lệch khi đổi giờ mùa hè/múi giờ); manifest ghi sau khi đổi tên, nằm ngoài `try`;
  hai tiến trình trong cùng một giây dùng chung đợt. ✅ (1.4.2) `clean --purge-quarantine` giờ giữ các đợt dưới 30 ngày;
  muốn xóa mọi đợt phải ghi rõ `--older-than 0`, số âm bị từ chối.
- **MCP**: `ssd_check_safety` đọc `E:x` như đường dẫn tương đối của gốc; gốc lưu trữ được báo là an toàn. Mô tả công cụ
  `ssd_find_duplicates` vẫn ghi "512KB physical cluster" (bị `test_mcp_grade_a` ghim). `ssd_audit` vẫn tính slack theo cluster
  512 KB trong khi `smart-drive audit` đã dùng cluster thật của ổ (trên `E:`: 119 MB slack "giả định" cho 4 MB dữ liệu) —
  nên thống nhất qua cùng helper ở `fsinfo.py`, hoặc trả cả hai con số.
- **`dup`**: bản cài portable không đăng ký với Windows (ví dụ SonarQube trong `E:\tool`) vẫn có `.jar` trùng được liệt kê;
  cần dùng lại heuristic "thư mục ứng dụng" của `classify` hoặc cho người dùng tự loại trừ. Biểu thức "cluster đã phát hiện,
  nếu không thì 512 KB" lặp ở `cmd_audit`, `cmd_checkup`, `cmd_dup`, `server.py`: gom vào một helper trong `fsinfo.py`.
- **`clean`**: ✅ (1.4.2) `--only-tier 3 --apply` chỉ cách ly rác Tier 3, không xóa Tier 1–2. Còn: tệp Tier 3 nằm trong
  một thư mục rác Tier 1–2 (ví dụ `build/x.tmp`) không được `--only-tier 3` liệt kê; công cụ MCP `ssd_clean` chưa có
  `only_tier`; `*.dmp` có thể là file export của Oracle (Tier 3 nên chỉ vào khu cách ly, cần theo dõi); `*.pyc` lẻ không có
  mã nguồn vẫn là Tier 1; `__pycache__` trong `site-packages` của venv vẫn được liệt kê (vô hại, Python tạo lại).
- **Danh sách bảo vệ** ✅ (1.4.2): bỏ `DBI202_VuPT\MSSQL16.MSSQLSERVER` (tên thư mục riêng của một người dùng) khỏi
  `PROTECTED_ROOT_DIRS` và `DEFAULT_EXCLUDE_DIRS`, vì mục chung `MSSQL16.MSSQLSERVER` đã bảo vệ thư mục instance ở mọi thư mục
  cha; `32837` và `fo4` (thư mục cài FC Online của Garena) được giữ kèm chú thích. Chưa có danh sách để người dùng tự thêm
  thư mục cần giữ: README chỉ cách dùng shortcut trên Desktop/Start Menu/taskbar (`protected_by_system`).
- **`classify`**: rất thận trọng — giữ nguyên cả `E:\project` (một repo con có `tunnel.ps1`) và thư mục kiểu Downloads có file
  cài đặt; cân nhắc bỏ qua thư mục con là repo khi xét "chứa thư mục chương trình". `classify --apply` còn dời cả shortcut
  `.lnk` nằm ở gốc ổ vào `06_Archives_Storage/Unclassified/`, làm mất lớp bảo vệ `protected_by_system` mà shortcut đó tạo cho
  thư mục đích: nên giữ nguyên các `.lnk` ở gốc trỏ tới một thư mục trên ổ.
- **Menu** ✅: `SmartDrive.*` có mục [11] "Relink lại đợt chuyển gần nhất" (xem trước bằng `relink --dry-run`, hỏi, rồi
  `relink --wait`), cho quy trình: chạy organize trong Claude Code → tắt Claude → chạy [11]. Cùng đợt sửa: dấu `&` chưa escape
  trong menu `SmartDrive.bat`, mục 0 của `SmartDrive.ps1` không thoát được.
- **Khóa thư mục do git fsmonitor** ✅: repo bật `core.fsmonitor=true` để lại tiến trình `git fsmonitor--daemon` giữ handle
  bên trong (`status`, `ls-files`, `check-ignore`, `diff` đều bật nó), làm `organize`/`undo` báo "Access is denied". Đã sửa:
  dừng daemon của các repo bên trong (và của worktree liên kết) trước khi chuyển, mọi lệnh git do công cụ gọi dùng
  `-c core.fsmonitor=false`, lỗi khóa nêu nguyên nhân có thể (trên Windows còn nêu tên tiến trình qua Restart Manager); đã chạy
  thật trên `E:` với daemon đang bật. Còn: phần xem trước của `Smart_Organize` và dry-run của MCP `ssd_auto_organize` chưa liệt
  kê daemon sẽ dừng; chưa thử lại nếu IDE bật lại daemon giữa lúc dừng và lúc đổi tên.

### 6.2 Giai đoạn B — Tăng tốc có chọn lọc (chỉ khi số đo vượt ngân sách A5)

1. **Song song bằng Python trước**: `hashlib` nhả GIL với khối dữ liệu lớn, nên `ThreadPoolExecutor` cho `dup` (băm) và cho
   quét nhiều nhánh thư mục cùng lúc đã nhanh hơn rõ mà không cần code native.
2. **Dùng lại chỉ mục**: `update` đã đồng bộ tăng dần vào `index.db`; cho `audit`/`checkup`/`dup` đọc kích thước và mtime từ đó
   thay vì quét lại cả ổ, chỉ quét lại phần đã đổi.
3. **Trợ thủ native tùy chọn**: một chương trình nhỏ (Rust hoặc Go) chỉ làm quét thư mục, tính dung lượng và SHA-256, xuất
   NDJSON ra stdout. Python gọi nó nếu có (đi kèm bản `.exe` ở A4), không có thì chạy bản Python như cũ. Nhờ vậy vẫn giữ
   `dependencies = []`, và mọi lớp bảo vệ vẫn nằm ở Python.

### 6.3 Giai đoạn C — Viết lại bằng Rust hoặc Go (dài hạn, chỉ khi có lý do sản phẩm)

**Khi nào đáng làm**: muốn chạy nền theo dõi ổ liên tục (dịch vụ, icon khay), có giao diện đồ họa, ổ hàng triệu file, hoặc phát
hành một file chạy đã ký số cho nhiều người. Chỉ "nhanh hơn" thì chưa đủ lý do: chỗ chậm nằm ở thao tác đĩa và quét virus của
Windows, ngôn ngữ nào cũng phải chờ như nhau.

**Chọn ngôn ngữ**:
- **Rust**: nhanh và tiết kiệm bộ nhớ nhất, an toàn bộ nhớ, hợp với dịch vụ chạy nền lâu dài; học và viết chậm hơn.
- **Go**: viết nhanh hơn, dễ build `.exe` cho mọi hệ điều hành, đủ nhanh cho kiểu việc nặng về đọc ghi đĩa này.
- Cả hai đều có sẵn thư viện cho registry Windows, SQLite có FTS5, JSON/TOML/XML và SDK MCP; kiểm tra lại phiên bản và độ
  trưởng thành khi bắt đầu.

**Cách chuyển (thay dần từng phần, không viết lại một lần)**:
1. **Chốt hợp đồng**: viết JSON Schema cho đầu ra `--json` của từng lệnh, mã thoát và schema 9 công cụ MCP vào `docs/contracts/`.
2. **Biến test thành test hộp đen**: các test e2e gọi CLI chạy được với một binary bất kỳ qua biến môi trường
   (ví dụ `SMART_DRIVE_BIN`), để cùng một bộ test kiểm cả bản Python lẫn bản mới.
3. **Chuyển theo thứ tự rủi ro tăng dần**: `status`/`health`/`audit` (chỉ đọc) → `search`/`index` → `dup` → `clean` (kèm mọi lớp
   bảo vệ) → `checkup` → `organize`/`classify`/`undo` → `relink` (phức tạp nhất: SQLite của VS Code, XML của JetBrains, `.lnk`,
   cấu hình agent) → máy chủ MCP → `setup`.
4. **Mỗi phần chuyển xong** phải qua bộ test hộp đen, và cho ra JSON giống bản Python khi dry-run trên ổ thật (so sánh từng trường).
5. **Chạy song song**: bản Python vẫn là mặc định cho tới khi bản mới qua hết kiểm tra trong hai lần phát hành liên tiếp.

**Rủi ro chính**: làm lại các lớp chống mất dữ liệu (mỗi lớp từng là một lỗi thật ở v1.4); phần đọc đặc thù Windows (`.lnk`,
registry, `state.vscdb`); CI ba hệ điều hành; ký số `.exe`.

### 6.4 Thứ tự và tiêu chí xong

| Giai đoạn | Bắt đầu khi | Xong khi |
|---|---|---|
| A | PR sửa lỗi sau chạy thử đã vào `main` | A1 đạt trên máy thật, A2–A6 xong hoặc được người dùng hoãn |
| B | Một số đo ở A5 vượt ngân sách | Số đo trở lại trong ngân sách, kết quả JSON không đổi |
| C | Người dùng quyết định làm sản phẩm chạy nền hoặc có giao diện | Bản mới qua bộ test hộp đen và hai lần phát hành song song |
