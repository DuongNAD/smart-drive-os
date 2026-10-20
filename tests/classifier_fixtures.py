"""tests/classifier_fixtures.py - A drive holding the folders that `classify` used to take apart.

The three cases come from a real drive on which a recursive `classify --dry-run` planned 861 moves:

- a portable app (``EOSClient.exe`` with the DLLs and config it loads) sent file by file to ``Unclassified``;
- ``composer.bat`` sent to Scripts while the ``composer.phar`` it runs went to ``Unclassified``;
- a nested ``SEB/SEB`` folder whose ``windows.bat`` and ``macos.command`` were separated from their ``cert`` folder,
  with a second copy of the folder next to it, so the moved files got ``_1`` names.

Plus one plain folder of documents, which is the kind of thing a recursive scan is for.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Dict

LOOSE_AT_ROOT = ("loose_model.gguf", "loose_notes.md")
APP_FOLDER = "EOS_Client_FA24_Version1"
SCRIPT_FOLDER = "APP"
SEB_FOLDERS = (
    "LuuTru_Desktop/New folder/SEB/SEB",
    "LuuTru_Desktop/New folder/SETUP0126/SEB",
)
DOCS_FOLDER = "Course_Notes"
BUNDLE_FOLDERS = (APP_FOLDER, SCRIPT_FOLDER, *SEB_FOLDERS)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def build_real_world_drive(root: Path) -> Path:
    """Creates the folders above under ``root`` and returns it. Everything is small and deterministic."""
    gguf = b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32
    _write(root / "loose_model.gguf", gguf)
    _write(root / "loose_notes.md", b"# Notes lying at the drive root\n")

    app = root / APP_FOLDER
    _write(app / "EOSClient.exe", b"MZ\x90\x00 client")
    _write(app / "EncryptData.dll", b"MZ\x90\x00 encrypt")
    _write(app / "NAudio.dll", b"MZ\x90\x00 audio")
    _write(app / "QuestionLib.dll", b"MZ\x90\x00 questions")
    _write(app / "EOSClient.exe.config", b"<configuration><startup/></configuration>\n")
    _write(app / "EOS_Server_Info.dat", b"\x01\x02\x03 server")

    scripts = root / SCRIPT_FOLDER
    _write(scripts / "composer.bat", b"@echo off\r\nphp \"%~dp0composer.phar\" %*\r\n")
    _write(scripts / "composer.phar", b"#!/usr/bin/env php\n<?php echo 'composer';\n")

    for relative in SEB_FOLDERS:
        seb = root / relative
        _write(seb / "windows.bat", b"@echo off\r\nstart seb.exe\r\n")
        _write(seb / "macos.command", b"#!/bin/bash\nopen seb.app\n")
        _write(seb / "cert" / "mitmproxy-ca-cert.pem", b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")

    docs = root / DOCS_FOLDER
    _write(docs / "week1" / "lecture.md", b"# Lecture 1\n")
    _write(docs / "week1" / "reading.pdf", b"%PDF-1.7\n%%EOF\n")
    _write(docs / "slides" / "deck.md", b"# Slides\n")
    return root


def snapshot(folder: Path) -> Dict[str, bytes]:
    """Every file below ``folder`` as {relative POSIX path: content}; empty folders show up as 'name/'."""
    found: Dict[str, bytes] = {}
    for current, dirs, files in os.walk(folder):
        here = Path(current)
        if not dirs and not files and here != folder:
            found[here.relative_to(folder).as_posix() + "/"] = b""
        for name in files:
            path = here / name
            found[path.relative_to(folder).as_posix()] = path.read_bytes()
    return found
