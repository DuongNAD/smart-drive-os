"""tests/test_system_refs.py - Unit tests for smart_drive.core.system_refs (M1 / T5).

Tests:
- MS-SHLLINK (.lnk) binary parser (ANSI, Unicode, IDList skip, CommonPathSuffix, corrupt/truncated tolerance).
- ImagePath / DisplayIcon / UninstallString / Run command line normalization (`"C:\\X\\a.exe" -k`, `\\??\\C:\\...`, `system32\\drivers\\x.sys`).
- PATH splitting and `%VAR%` expansion.
- `refs_under` case-insensitivity and strict directory boundary enforcement (`E:\\Git` vs `E:\\GitHub`).
- `looks_like_installed_app` top-level uninstaller detection (`unins000.exe`, `uninstall.exe`, `Uninstall*.exe`).
- `SystemRefIndex.collect` resilience when individual registry keys raise OSError/PermissionError.
"""

from __future__ import annotations

import os
import struct
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from smart_drive.core.system_refs import (
    RegistryReader,
    SystemRef,
    SystemRefIndex,
    extract_argument_paths,
    extract_executable_path,
    is_path_executable,
    looks_like_installed_app,
    parse_lnk_bytes,
    parse_lnk_details,
    parse_lnk_file,
    split_and_expand_path,
    summarize_system_refs,
)
from tests.helpers import TempWorkspace


def _build_lnk_bytes(
    local_base_path: str,
    common_suffix: str = "",
    include_id_list: bool = True,
    use_unicode: bool = False,
) -> bytes:
    """Constructs a minimal valid MS-SHLLINK (.lnk) binary buffer in memory for testing."""
    link_flags = 0x00000002  # HasLinkInfo
    id_list_section = b""
    if include_id_list:
        link_flags |= 0x00000001  # HasLinkTargetIDList
        dummy_id_list = b"\x14\x00" + (b"\x00" * 18) + b"\x00\x00"
        id_list_section = struct.pack("<H", len(dummy_id_list)) + dummy_id_list

    header = bytearray(0x4C)
    struct.pack_into("<I", header, 0x00, 0x0000004C)  # HeaderSize
    struct.pack_into("<I", header, 0x14, link_flags)  # LinkFlags

    base_ansi = local_base_path.encode("cp1252", errors="replace") + b"\x00"
    suffix_ansi = common_suffix.encode("cp1252", errors="replace") + b"\x00"
    volume_id = struct.pack("<IIII", 0x10, 3, 0x12345678, 0x10) + b"\x00"

    if use_unicode:
        info_header_size = 0x24
        base_uni = local_base_path.encode("utf-16-le") + b"\x00\x00"
        suffix_uni = common_suffix.encode("utf-16-le") + b"\x00\x00"
        vol_offset = info_header_size
        base_ansi_offset = vol_offset + len(volume_id)
        suffix_ansi_offset = base_ansi_offset + len(base_ansi)
        base_uni_offset = suffix_ansi_offset + len(suffix_ansi)
        suffix_uni_offset = base_uni_offset + len(base_uni)
        total_info_size = suffix_uni_offset + len(suffix_uni)

        link_info_hdr = struct.pack(
            "<IIIIIIIII",
            total_info_size,
            info_header_size,
            0x00000001,  # VolumeIDAndLocalBasePath
            vol_offset,
            base_ansi_offset,
            0,
            suffix_ansi_offset,
            base_uni_offset,
            suffix_uni_offset,
        )
        link_info = link_info_hdr + volume_id + base_ansi + suffix_ansi + base_uni + suffix_uni
    else:
        info_header_size = 0x1C
        vol_offset = info_header_size
        base_ansi_offset = vol_offset + len(volume_id)
        suffix_ansi_offset = base_ansi_offset + len(base_ansi)
        total_info_size = suffix_ansi_offset + len(suffix_ansi)

        link_info_hdr = struct.pack(
            "<IIIIIII",
            total_info_size,
            info_header_size,
            0x00000001,  # VolumeIDAndLocalBasePath
            vol_offset,
            base_ansi_offset,
            0,
            suffix_ansi_offset,
        )
        link_info = link_info_hdr + volume_id + base_ansi + suffix_ansi

    return bytes(header) + id_list_section + link_info


_MY_COMPUTER_CLSID = bytes.fromhex("e04fd020ea3a6910a2d808002b30309d")


def _shell_item(class_type: int, primary_name: str, long_name: str, version: int = 9, with_ext: bool = True) -> bytes:
    """A file-system shell item laid out like the ones Explorer writes (primary ANSI name + 0xBEEF0004 block)."""
    primary = primary_name.encode("cp1252") + b"\x00"
    fixed = bytes([class_type, 0]) + b"\x00" * 8 + b"\x10\x00"  # class, unknown, file size, date/time, attributes
    name_part = primary + (b"\x00" if (14 + len(primary)) % 2 else b"")
    if not with_ext:
        body = fixed + name_part + struct.pack("<H", 0)
        return struct.pack("<H", 2 + len(body)) + body

    ext_off = 14 + len(name_part)
    name_off = {8: 42, 9: 46}.get(version, 46)
    ext = bytearray(struct.pack("<HH", 0, version) + b"\x04\x00\xef\xbe" + b"\x00" * 8 + struct.pack("<H", 0x2E))
    ext = ext.ljust(name_off, b"\x00") + long_name.encode("utf-16-le") + b"\x00\x00"
    struct.pack_into("<H", ext, 0, len(ext) + 2)
    body = fixed + name_part + bytes(ext) + struct.pack("<H", ext_off)
    return struct.pack("<H", 2 + len(body)) + body


def _id_list_items(drive: str, *names: tuple, root: bytes = _MY_COMPUTER_CLSID) -> list:
    """Shell items for `drive` + folders + a final file; each name is (long_name, primary_name_or_None)."""
    items = [
        struct.pack("<H", 20) + bytes([0x1F, 0x50]) + root,
        struct.pack("<H", 25) + bytes([0x2F]) + drive.encode("ascii") + b"\\" + b"\x00" * 19,
    ]
    for i, (long_name, primary) in enumerate(names):
        class_type = 0x32 if i == len(names) - 1 else 0x31
        items.append(_shell_item(class_type, primary or long_name, long_name))
    return items


def _build_lnk_without_link_info(
    items: list,
    working_dir: Optional[str] = None,
    arguments: Optional[str] = None,
    relative_path: Optional[str] = None,
) -> bytes:
    """A .lnk that has an ID list and string data but no LinkInfo block (what many installers write)."""
    link_flags = 0x00000001 | 0x00000080  # HasLinkTargetIDList | IsUnicode
    strings = b""
    for bit, value in ((0x08, relative_path), (0x10, working_dir), (0x20, arguments)):
        if value is not None:
            link_flags |= bit
            strings += struct.pack("<H", len(value)) + value.encode("utf-16-le")
    header = bytearray(0x4C)
    struct.pack_into("<I", header, 0x00, 0x0000004C)
    struct.pack_into("<I", header, 0x14, link_flags)
    id_list = b"".join(items) + b"\x00\x00"
    return bytes(header) + struct.pack("<H", len(id_list)) + id_list + strings


def _with_string_data(
    lnk: bytes,
    working_dir: Optional[str] = None,
    arguments: Optional[str] = None,
) -> bytes:
    """Appends Unicode string data (after LinkInfo) to a .lnk built by `_build_lnk_bytes`."""
    link_flags = struct.unpack_from("<I", lnk, 0x14)[0] | 0x00000080
    strings = b""
    for bit, value in ((0x10, working_dir), (0x20, arguments)):
        if value is not None:
            link_flags |= bit
            strings += struct.pack("<H", len(value)) + value.encode("utf-16-le")
    patched = bytearray(lnk)
    struct.pack_into("<I", patched, 0x14, link_flags)
    return bytes(patched) + strings


class TestLnkParser(unittest.TestCase):
    """Tests pure-Python MS-SHLLINK (.lnk) parsing."""

    def test_parse_ansi_lnk_with_and_without_idlist(self) -> None:
        buf_with_id = _build_lnk_bytes(r"E:\Git\git-bash.exe", include_id_list=True, use_unicode=False)
        self.assertEqual(parse_lnk_bytes(buf_with_id), r"E:\Git\git-bash.exe")

        buf_no_id = _build_lnk_bytes(r"E:\SQL\ssms.exe", include_id_list=False, use_unicode=False)
        self.assertEqual(parse_lnk_bytes(buf_no_id), r"E:\SQL\ssms.exe")

    def test_parse_unicode_lnk_and_common_suffix(self) -> None:
        buf_uni = _build_lnk_bytes(
            r"E:\Apps\MyTool",
            common_suffix=r"bin\launcher.exe",
            include_id_list=True,
            use_unicode=True,
        )
        self.assertEqual(parse_lnk_bytes(buf_uni), r"E:\Apps\MyTool\bin\launcher.exe")

    def test_corrupt_or_truncated_lnk_returns_none(self) -> None:
        self.assertIsNone(parse_lnk_bytes(b""))
        self.assertIsNone(parse_lnk_bytes(b"not a lnk file" * 10))
        valid = _build_lnk_bytes(r"E:\Git\cmd\git.exe")
        self.assertIsNone(parse_lnk_bytes(valid[:0x4C + 4]))

    def test_lnk_without_link_info_resolves_target_from_id_list(self) -> None:
        # Shaped like a Start Menu shortcut written without LinkInfo: My Computer > E:\ > folder > file
        items = _id_list_items("E:", ("Fotor Photo Editor", None), ("Fotor.exe", None))
        self.assertEqual(parse_lnk_bytes(_build_lnk_without_link_info(items)), r"E:\Fotor Photo Editor\Fotor.exe")

        # Windows 8 puts the long name 4 bytes earlier in the extension block than Windows 10/11
        win8 = [items[0], items[1]] + [
            _shell_item(0x31, "Fotor Photo Editor", "Fotor Photo Editor", version=8),
            _shell_item(0x32, "Fotor.exe", "Fotor.exe", version=8),
        ]
        self.assertEqual(parse_lnk_bytes(_build_lnk_without_link_info(win8)), r"E:\Fotor Photo Editor\Fotor.exe")

        # A long Unicode name wins over the ANSI 8.3 alias stored as the primary name
        viet = _id_list_items("D:", ("Ứng dụng", "UNGDNG~1"), ("ảnh.exe", "ANH~1.EXE"))
        self.assertEqual(parse_lnk_bytes(_build_lnk_without_link_info(viet)), "D:\\Ứng dụng\\ảnh.exe")

    def test_lnk_id_list_falls_back_to_primary_name_but_never_to_an_alias(self) -> None:
        plain = [
            *_id_list_items("E:", ("Tools", None))[:2],
            _shell_item(0x31, "Tools", "", with_ext=False),
            _shell_item(0x32, "run.exe", "", with_ext=False),
        ]
        self.assertEqual(parse_lnk_bytes(_build_lnk_without_link_info(plain)), r"E:\Tools\run.exe")

        aliased = [*plain[:2], _shell_item(0x31, "PROGRA~1", "", with_ext=False), plain[3]]
        self.assertIsNone(parse_lnk_bytes(_build_lnk_without_link_info(aliased)))

    def test_lnk_id_list_not_rooted_at_a_drive_gives_no_target(self) -> None:
        control_panel_root = bytes.fromhex("8094e69953e56c6c4d8fce1d8870fdcb")
        items = _id_list_items("E:", ("Tools", None), root=control_panel_root)
        self.assertIsNone(parse_lnk_bytes(_build_lnk_without_link_info(items)))

    def test_lnk_link_info_target_wins_over_id_list_and_string_data_is_read(self) -> None:
        lnk = _with_string_data(
            _build_lnk_bytes(r"C:\Tools\runner.exe", use_unicode=True),
            working_dir=r"E:\Work Dir",
            arguments=r'"E:\scripts\go.py" --fast',
        )
        details = parse_lnk_details(lnk)
        assert details is not None
        self.assertEqual(details.target, r"C:\Tools\runner.exe")
        self.assertEqual(details.working_dir, r"E:\Work Dir")
        self.assertEqual(details.arguments, r'"E:\scripts\go.py" --fast')

    def test_lnk_relative_path_is_resolved_against_the_shortcut_folder(self) -> None:
        lnk = _build_lnk_without_link_info([], relative_path=r"..\..\Apps\Tool\tool.exe")
        self.assertIsNone(parse_lnk_details(lnk))  # no folder to resolve against
        details = parse_lnk_details(lnk, lnk_dir=r"E:\Links\Desktop")
        assert details is not None
        self.assertEqual(details.target, r"E:\Apps\Tool\tool.exe")
        # Crossing a drive is impossible in a relative path: a result that is not a drive path is dropped
        self.assertIsNone(parse_lnk_details(lnk, lnk_dir="/home/user/Desktop"))


class TestExecutableAndPathParsers(unittest.TestCase):
    """Tests ImagePath normalization and PATH %VAR% expansion."""

    def test_extract_executable_path_variants(self) -> None:
        # 1. Quoted path with arguments
        self.assertEqual(
            extract_executable_path(r'"C:\X\a.exe" -k --service'),
            r"C:\X\a.exe",
        )
        # 2. NT namespace prefix \??\
        self.assertEqual(
            extract_executable_path(r"\??\E:\Drivers\nvlddmkm.sys"),
            r"E:\Drivers\nvlddmkm.sys",
        )
        # 3. Relative system32\... and \SystemRoot\...
        self.assertEqual(
            extract_executable_path(r"system32\drivers\x.sys", system_root=r"C:\Windows"),
            r"C:\Windows\system32\drivers\x.sys",
        )
        self.assertEqual(
            extract_executable_path(r"\SystemRoot\System32\svchost.exe -k netsvcs", system_root=r"C:\Windows"),
            r"C:\Windows\System32\svchost.exe",
        )
        # 4. Unquoted executable with flags and DisplayIcon with `,0`
        self.assertEqual(
            extract_executable_path(r"E:\SQL\MSSQL\Binn\sqlservr.exe -sMSSQLSERVER"),
            r"E:\SQL\MSSQL\Binn\sqlservr.exe",
        )
        self.assertEqual(
            extract_executable_path(r'"E:\Git\mingw64\share\git\git-for-windows.ico",0'),
            r"E:\Git\mingw64\share\git\git-for-windows.ico",
        )

    def test_unquoted_program_keeps_folder_names_that_contain_an_extension(self) -> None:
        # `E:\My.com` is a folder here: the program only ends at `app.exe`, not at the first `.com` + space
        self.assertEqual(
            extract_executable_path(r"E:\My.com Tools\app.exe -k"),
            r"E:\My.com Tools\app.exe",
        )
        self.assertEqual(
            extract_executable_path(r"E:\Apps\v1.exe Files\bin\run.exe /silent"),
            r"E:\Apps\v1.exe Files\bin\run.exe",
        )
        # A second absolute path is an argument, not the rest of the program
        self.assertEqual(
            extract_executable_path(r"C:\Tools\a.exe C:\Tools\b.exe"),
            r"C:\Tools\a.exe",
        )
        # Nothing after the extension that continues a path: first match still wins
        self.assertEqual(
            extract_executable_path(r"E:\Tools\run.exe -k"),
            r"E:\Tools\run.exe",
        )

    def test_backslash_escaped_quotes_in_run_values_are_unwrapped(self) -> None:
        self.assertEqual(
            extract_executable_path(r'"\"C:\Users\X\AppData\Local\AnthropicClaude\claude.exe\" --startup"'),
            r"C:\Users\X\AppData\Local\AnthropicClaude\claude.exe",
        )
        self.assertEqual(
            extract_executable_path(r'\"E:\Tools\tray.exe\" --minimized'),
            r"E:\Tools\tray.exe",
        )
        # A path that merely ends in a backslash before its closing quote is left alone
        self.assertEqual(
            extract_executable_path(r'"E:\Tools\run.exe" "E:\Data\"'),
            r"E:\Tools\run.exe",
        )

        mock_tree = {
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run": {
                "Claude": r'"\"E:\Apps\Claude\claude.exe\" --startup"',
            },
        }
        with TempWorkspace(prefix="test_escaped_quotes_") as tmp:
            idx = SystemRefIndex.collect(home=tmp, reader=RegistryReader(tree=mock_tree, env={}))
        refs = idx.refs_under(r"E:\Apps\Claude")
        self.assertEqual([r.kind for r in refs], ["startup"])

    def test_extract_argument_paths_finds_scripts_and_dlls_passed_to_a_program(self) -> None:
        cases = [
            (r'"C:\Python313\pythonw.exe" "E:\tool\tray.py"', [r"E:\tool\tray.py"]),
            (r"wscript.exe E:\x.vbs", [r"E:\x.vbs"]),
            (
                r'"C:\Program Files\AutoHotkey\AutoHotkey.exe" "E:\scripts\keys.ahk"',
                [r"E:\scripts\keys.ahk"],
            ),
            (r'rundll32.exe "E:\tool\x.dll",Start', [r"E:\tool\x.dll"]),
            (r"rundll32.exe E:\tool\x.dll,Start", [r"E:\tool\x.dll"]),
            (r"python E:\tool\main.py --port 8080", [r"E:\tool\main.py"]),
            (
                r'"C:\Tools\app.exe" --config=E:\cfg\a.json /log:"E:\My Logs\app.log"',
                [r"E:\cfg\a.json", r"E:\My Logs\app.log"],
            ),
            (r"E:/tool/run.exe E:/tool/data/in.csv", [r"E:\tool\data\in.csv"]),
            # No path among the arguments
            (r"C:\Windows\system32\svchost.exe -k netsvcs -p", []),
            (r'"C:\Tools\a.exe" -x /silent', []),
            (r"MsiExec.exe /X{0A1B2C3D-0000-1111-2222-333344445555}", []),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(extract_argument_paths(command), expected)

        # An unquoted path may contain spaces: the rest of the line is reported too
        spaced = extract_argument_paths(
            r"rundll32.exe setupapi,InstallHinfSection DefaultInstall 128 C:\Program Files (x86)\Pkg\Video Codec\z.inf"
        )
        self.assertIn(r"C:\Program Files (x86)\Pkg\Video Codec\z.inf", spaced)

        # %VAR% in the arguments is expanded through the reader
        reader = RegistryReader(env={"TOOLS": r"E:\tool"})
        self.assertEqual(
            extract_argument_paths(r'"C:\Python313\pythonw.exe" "%TOOLS%\tray.py"', reader=reader),
            [r"E:\tool\tray.py"],
        )

    def test_split_and_expand_path_with_env_vars(self) -> None:
        reader = RegistryReader(env={"GIT_HOME": r"E:\Git", "DEV_TOOLS": r"E:\DevTools"})
        raw = r"%GIT_HOME%\cmd;%DEV_TOOLS%\bin;E:\PlainDir;%UNRESOLVED_VAR%\bin"
        parts = split_and_expand_path(raw, sep=";", reader=reader)
        self.assertEqual(parts, [r"E:\Git\cmd", r"E:\DevTools\bin", r"E:\PlainDir"])


class TestSystemRefIndexAndBoundaries(unittest.TestCase):
    """Tests refs_under boundary matching, mock registry collection, and uninstaller detection."""

    def test_refs_under_case_insensitivity_and_strict_boundary(self) -> None:
        idx = SystemRefIndex([
            SystemRef(kind="path", source="PATH (User)", target=r"E:\Git\cmd"),
            SystemRef(kind="service", source="service MSSQL", target=r"E:\SQL\Binn\sqlservr.exe"),
        ])

        # Exact and nested matches (case-insensitive)
        self.assertEqual(len(idx.refs_under(r"e:\git")), 1)
        self.assertEqual(len(idx.refs_under(r"E:\GIT\cmd")), 1)
        self.assertEqual(len(idx.refs_under(r"E:\sql")), 1)

        # Prefix-collision non-matches: E:\Git must NOT match E:\GitHub or E:\GitLFS
        idx_github = SystemRefIndex([
            SystemRef(kind="path", source="PATH (User)", target=r"E:\GitHub\bin"),
        ])
        self.assertEqual(idx_github.refs_under(r"E:\Git"), [])
        self.assertEqual(len(idx_github.refs_under(r"E:\GitHub")), 1)

    def test_only_executable_files_directly_in_a_path_folder_are_references(self) -> None:
        # The drive root on PATH must not pin every loose PDF, archive or model file on the drive
        idx = SystemRefIndex([SystemRef(kind="path", source="PATH (User)", target="E:\\")])
        with patch.dict(os.environ, {"PATHEXT": ".COM;.EXE;.RB"}):
            for name in ("tool.exe", "run.CMD", "setup.ps1", "hook.bat", "lib.dll", "gem.rb", "bin.COM"):
                with self.subTest(runnable=name):
                    self.assertEqual(len(idx.refs_under("E:\\" + name, is_file=True)), 1)
            for name in ("manual.pdf", "backup.zip", "model.gguf", "notes.txt", "README", "archive.tar.gz"):
                with self.subTest(inert=name):
                    self.assertEqual(idx.refs_under("E:\\" + name, is_file=True), [])
        self.assertTrue(is_path_executable("Setup.EXE"))
        self.assertFalse(is_path_executable("model.gguf"))
        self.assertFalse(is_path_executable(".bashrc"))

        # A file that a service, shortcut or startup entry points at is still a reference whatever its extension
        idx_target = SystemRefIndex([SystemRef(kind="startup", source="startup X", target=r"E:\data.pdf")])
        self.assertEqual(len(idx_target.refs_under(r"E:\data.pdf", is_file=True)), 1)

    def test_collect_with_mock_registry_shortcuts_and_broken_key_resilience(self) -> None:
        def broken_key() -> None:
            raise PermissionError("Access denied to registry key")

        mock_tree = {
            r"HKCU\Environment": {
                "Path": r"%CUSTOM_DRIVE%\Git\cmd;C:\Windows\System32",
            },
            r"HKLM\SYSTEM\CurrentControlSet\Services": {
                "MSSQLSERVER": {
                    "ImagePath": r'"E:\SQL\MSSQL16\Binn\sqlservr.exe" -sMSSQLSERVER',
                },
                "BrokenService": broken_key,
            },
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall": {
                "Git_is1": {
                    "DisplayName": "Git",
                    "InstallLocation": r"E:\Git",
                    "UninstallString": r'"E:\Git\unins000.exe"',
                },
                "CorruptUninstall": broken_key,
            },
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run": {
                "MyTray": r'"E:\TrayApp\tray.exe" --minimized',
            },
        }
        reader = RegistryReader(tree=mock_tree, env={"CUSTOM_DRIVE": "E:"})

        with TempWorkspace(prefix="test_sysrefs_") as tmp:
            desktop = tmp / "Desktop"
            desktop.mkdir(parents=True, exist_ok=True)
            lnk_file = desktop / "Git Bash.lnk"
            lnk_file.write_bytes(_build_lnk_bytes(r"E:\Git\git-bash.exe", use_unicode=True))
            (desktop / "broken.lnk").write_bytes(b"corrupt")

            idx = SystemRefIndex.collect(home=tmp, reader=reader)

            git_refs = idx.refs_under(r"E:\Git")
            git_kinds = {r.kind for r in git_refs}
            self.assertIn("path", git_kinds)
            self.assertIn("installed_app", git_kinds)
            self.assertIn("shortcut", git_kinds)

            sql_refs = idx.refs_under(r"E:\SQL")
            self.assertEqual(len(sql_refs), 1)
            self.assertEqual(sql_refs[0].kind, "service")

            tray_refs = idx.refs_under(r"E:\TrayApp")
            self.assertEqual(len(tray_refs), 1)
            self.assertEqual(tray_refs[0].kind, "startup")

            summary = summarize_system_refs(git_refs)
            self.assertIn("PATH", summary)
            self.assertIn('installed app "Git"', summary)
            self.assertIn("1 shortcut", summary)

    def test_collect_protects_folders_of_scripts_passed_as_command_line_arguments(self) -> None:
        mock_tree = {
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run": {
                "Tray": r'"C:\Python313\pythonw.exe" "E:\tool\tray.py"',
                "Hotkeys": r"wscript.exe E:\launchers\hotkeys.vbs",
                "Shell": r'rundll32.exe "E:\shellext\x.dll",Start',
            },
            r"HKLM\SYSTEM\CurrentControlSet\Services": {
                "MySvc": {"ImagePath": r'"C:\Python313\python.exe" E:\svc\run.py --port 80'},
            },
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall": {
                "Pkg": {
                    "DisplayName": "Pkg",
                    "UninstallString": r'"C:\Windows\System32\msiexec.exe" /x "E:\Pkg\a.msi"',
                },
            },
        }
        reader = RegistryReader(tree=mock_tree, env={})
        with TempWorkspace(prefix="test_arg_paths_") as tmp:
            idx = SystemRefIndex.collect(home=tmp, reader=reader)

        by_folder = {
            r"E:\tool": "startup",
            r"E:\launchers": "startup",
            r"E:\shellext": "startup",
            r"E:\svc": "service",
            r"E:\Pkg": "installed_app",
        }
        for folder, kind in by_folder.items():
            with self.subTest(folder=folder):
                self.assertEqual([r.kind for r in idx.refs_under(folder)], [kind])
        # The programs themselves (outside E:) are still recorded; the folders next door are not
        self.assertEqual(idx.refs_under(r"E:\unrelated"), [])

    def test_environment_variables_holding_absolute_paths_are_references(self) -> None:
        mock_tree = {
            r"HKCU\Environment": {
                "Path": r"E:\Git\cmd",
                "JAVA_HOME": r"E:\Java\jdk-21",
                "PyCharm": r"E:\PyCharm 2025.3.1\bin;",
                "TOOLS_ROOT": r"%DEV_DRIVE%\Tools",
                "TEMP": r"%USERPROFILE%\AppData\Local\Temp",
                "PATHEXT": ".COM;.EXE;.BAT",
                "Retries": 3,
                "Unresolved": r"%NOT_DEFINED%\x",
            },
            r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment": {
                "Path": r"E:\Sys\bin",
                "PT8HOME": r"E:\Packet Tracer",
            },
        }
        reader = RegistryReader(tree=mock_tree, env={"DEV_DRIVE": "E:", "USERPROFILE": r"C:\Users\X"})
        with TempWorkspace(prefix="test_env_vars_") as tmp:
            idx = SystemRefIndex.collect(home=tmp, reader=reader)

        expected = {
            r"E:\Java": ("env", "JAVA_HOME (User)"),
            r"E:\PyCharm 2025.3.1": ("env", "PyCharm (User)"),
            r"E:\Tools": ("env", "TOOLS_ROOT (User)"),
            r"E:\Packet Tracer": ("env", "PT8HOME (System)"),
            r"C:\Users\X\AppData": ("env", "TEMP (User)"),
            # Path stays a "path" reference, which also covers loose executables sitting in the folder
            r"E:\Git": ("path", "PATH (User)"),
            r"E:\Sys": ("path", "PATH (System)"),
        }
        for folder, (kind, source) in expected.items():
            with self.subTest(folder=folder):
                refs = idx.refs_under(folder)
                self.assertEqual([(r.kind, r.source) for r in refs], [(kind, source)])
        self.assertEqual(
            {r.source for r in idx.refs if r.kind == "env"},
            {"JAVA_HOME (User)", "PyCharm (User)", "TOOLS_ROOT (User)", "PT8HOME (System)", "TEMP (User)"},
        )
        self.assertIn(
            "environment variable JAVA_HOME (User)",
            summarize_system_refs(idx.refs_under(r"E:\Java")),
        )

    def test_shortcut_without_link_info_and_its_start_in_folder_and_arguments_are_references(self) -> None:
        with TempWorkspace(prefix="test_lnk_no_link_info_") as tmp:
            desktop = tmp / "Desktop"
            desktop.mkdir()
            items = _id_list_items("E:", ("Fotor Photo Editor", None), ("Fotor.exe", None))
            (desktop / "Fotor.lnk").write_bytes(
                _build_lnk_without_link_info(items, working_dir="E:\\\\Fotor Photo Editor")
            )
            (desktop / "Tray.lnk").write_bytes(
                _with_string_data(
                    _build_lnk_bytes(r"C:\Python313\pythonw.exe", use_unicode=True),
                    working_dir=r"%TOOLS%\Tray",
                    arguments=r'"E:\scripts\tray.py" --quiet',
                )
            )
            reader = RegistryReader(tree={}, env={"TOOLS": r"E:\Tools"})
            idx = SystemRefIndex.collect(home=tmp, reader=reader)

        fotor = idx.refs_under(r"E:\Fotor Photo Editor")
        self.assertEqual({r.kind for r in fotor}, {"shortcut", "shortcut_dep"})
        self.assertIn("1 shortcut", summarize_system_refs(fotor))
        self.assertNotIn("2 shortcuts", summarize_system_refs(fotor))
        self.assertEqual([r.kind for r in idx.refs_under(r"E:\Tools\Tray")], ["shortcut_dep"])
        self.assertEqual([r.kind for r in idx.refs_under(r"E:\scripts")], ["shortcut_dep"])
        # Only the real targets are "shortcut" refs: relink repoints a shortcut to its target, never to a start-in folder
        self.assertEqual(
            sorted(r.target for r in idx.refs if r.kind == "shortcut"),
            [r"C:\Python313\pythonw.exe", r"E:\Fotor Photo Editor\Fotor.exe"],
        )

    def test_looks_like_installed_app_detects_top_level_uninstallers(self) -> None:
        with TempWorkspace(prefix="test_uninst_") as tmp:
            app_dir = tmp / "SomeInstalledApp"
            app_dir.mkdir()
            self.assertIsNone(looks_like_installed_app(app_dir))

            (app_dir / "unins000.exe").write_bytes(b"MZ")
            self.assertEqual(looks_like_installed_app(app_dir), "unins000.exe")

    def test_desktop_resolved_from_user_shell_folders_and_common_desktop(self) -> None:
        with TempWorkspace(prefix="test_usf_desktop_") as tmp:
            fake_userprofile = tmp / "User"
            onedrive_desktop = fake_userprofile / "OneDrive" / "Máy tính"
            legacy_desktop = fake_userprofile / "Desktop"
            common_desktop = tmp / "PublicDesktop"

            onedrive_desktop.mkdir(parents=True, exist_ok=True)
            legacy_desktop.mkdir(parents=True, exist_ok=True)
            common_desktop.mkdir(parents=True, exist_ok=True)

            (onedrive_desktop / "OneDriveApp.lnk").write_bytes(
                _build_lnk_bytes(r"E:\OneDriveApp\app.exe", use_unicode=True)
            )
            # Shortcut in %USERPROFILE%\Desktop must NOT be scanned when User Shell Folders defines Desktop
            (legacy_desktop / "LegacyApp.lnk").write_bytes(
                _build_lnk_bytes(r"E:\LegacyApp\app.exe", use_unicode=True)
            )
            (common_desktop / "CommonApp.lnk").write_bytes(
                _build_lnk_bytes(r"E:\CommonApp\app.exe", use_unicode=True)
            )

            mock_tree = {
                r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders": {
                    "Desktop": r"%USERPROFILE%\OneDrive\Máy tính",
                },
                r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders": {
                    "Common Desktop": r"%PUBLIC_DESKTOP%",
                },
            }
            reader = RegistryReader(
                tree=mock_tree,
                env={
                    "USERPROFILE": str(fake_userprofile),
                    "PUBLIC_DESKTOP": str(common_desktop),
                },
            )

            idx = SystemRefIndex.collect(home=fake_userprofile, reader=reader)
            self.assertEqual(len(idx.refs_under(r"E:\OneDriveApp")), 1)
            self.assertEqual(len(idx.refs_under(r"E:\CommonApp")), 1)
            self.assertEqual(idx.refs_under(r"E:\LegacyApp"), [])

    def test_desktop_walk_depth_limit_and_max_shortcut_files_per_dir(self) -> None:
        from unittest.mock import patch
        import smart_drive.core.system_refs as sr_mod

        with TempWorkspace(prefix="test_depth_cap_") as tmp:
            desktop = tmp / "Desktop"
            lvl2 = desktop / "lvl2"
            lvl3 = lvl2 / "lvl3"
            lvl4 = lvl3 / "lvl4"
            lvl4.mkdir(parents=True, exist_ok=True)

            # Depth 1 (Desktop), Depth 2 (Desktop/lvl2), Depth 3 (Desktop/lvl2/lvl3) are scanned; Depth 4 is skipped
            (desktop / "d1.lnk").write_bytes(_build_lnk_bytes(r"E:\D1\a.exe", use_unicode=True))
            (lvl2 / "d2.lnk").write_bytes(_build_lnk_bytes(r"E:\D2\a.exe", use_unicode=True))
            (lvl3 / "d3.lnk").write_bytes(_build_lnk_bytes(r"E:\D3\a.exe", use_unicode=True))
            (lvl4 / "d4.lnk").write_bytes(_build_lnk_bytes(r"E:\D4\a.exe", use_unicode=True))

            idx = SystemRefIndex.collect(home=tmp, reader={})
            self.assertEqual(len(idx.refs_under(r"E:\D1")), 1)
            self.assertEqual(len(idx.refs_under(r"E:\D2")), 1)
            self.assertEqual(len(idx.refs_under(r"E:\D3")), 1)
            self.assertEqual(idx.refs_under(r"E:\D4"), [])

            # Hitting MAX_SHORTCUT_FILES in Desktop stops only Desktop and still scans Start Menu
            start_menu = tmp / "Start Menu"
            start_menu.mkdir(parents=True, exist_ok=True)
            (start_menu / "sm.lnk").write_bytes(_build_lnk_bytes(r"E:\StartMenuApp\sm.exe", use_unicode=True))

            with patch.object(sr_mod, "MAX_SHORTCUT_FILES", 2):
                idx_capped = SystemRefIndex.collect(home=tmp, reader={})

            # Start Menu shortcut was still collected despite Desktop hitting MAX_SHORTCUT_FILES=2
            self.assertEqual(len(idx_capped.refs_under(r"E:\StartMenuApp")), 1)
            desktop_shortcuts = [r for r in idx_capped.refs if r.kind == "shortcut" and "Start Menu" not in r.source]
            self.assertEqual(len(desktop_shortcuts), 2)


if __name__ == "__main__":
    unittest.main()
