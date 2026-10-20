"""tests/classifier_fixtures.py - A drive holding the folders that `classify` used to take apart.

The three cases come from a real drive on which a recursive `classify --dry-run` planned 861 moves:

- a portable app (``EOSClient.exe`` with the DLLs and config it loads) sent file by file to ``Unclassified``;
- ``composer.bat`` sent to Scripts while the ``composer.phar`` it runs went to ``Unclassified``;
- a nested ``SEB/SEB`` folder whose ``windows.bat`` and ``macos.command`` were separated from their ``cert`` folder,
  with a second copy of the folder next to it, so the moved files got ``_1`` names.

Plus one plain folder of documents, which is the kind of thing a recursive scan is for. ``add_sonarqube_like`` and
``add_docs_with_installer`` add the two kinds of folder that only the folder around a program folder gives away.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

from smart_drive.cli.main import main
from smart_drive.core.system_refs import SystemRefIndex

LOOSE_AT_ROOT = ("loose_model.gguf", "loose_notes.md")
APP_FOLDER = "EOS_Client_FA24_Version1"
APP_SUBFOLDER = APP_FOLDER + "/Data"
SCRIPT_FOLDER = "APP"
# Where the launcher scripts are, and the folders around them: a scan stops at the wrapper, which holds a program folder
SEB_FOLDERS = (
    "LuuTru_Desktop/New folder/SEB/SEB",
    "LuuTru_Desktop/New folder/SETUP0126/SEB",
)
SEB_WRAPPERS = (
    "LuuTru_Desktop/New folder/SEB",
    "LuuTru_Desktop/New folder/SETUP0126",
)
DOCS_FOLDER = "Course_Notes"
BUNDLE_FOLDERS = (APP_FOLDER, SCRIPT_FOLDER, *SEB_WRAPPERS)  # what a recursive scan of the drive reports
SONAR_FOLDER = "apps_dist/sonarqube-1.0"
INSTALLER_DOCS = "Course_With_Installer"


def write(path: Path, data: bytes) -> Path:
    """Writes ``data`` to ``path``, creating the folders above it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def build_real_world_drive(root: Path) -> Path:
    """Creates the folders above under ``root`` and returns it. Everything is small and deterministic."""
    write(root / "loose_model.gguf", b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)
    write(root / "loose_notes.md", b"# Notes lying at the drive root\n")

    app = root / APP_FOLDER
    write(app / "EOSClient.exe", b"MZ\x90\x00 client")
    write(app / "EncryptData.dll", b"MZ\x90\x00 encrypt")
    write(app / "NAudio.dll", b"MZ\x90\x00 audio")
    write(app / "QuestionLib.dll", b"MZ\x90\x00 questions")
    write(app / "EOSClient.exe.config", b"<configuration><startup/></configuration>\n")
    write(app / "EOS_Server_Info.dat", b"\x01\x02\x03 server")
    write(root / APP_SUBFOLDER / "exam_notes.md", b"# Exam notes kept inside the app folder\n")

    scripts = root / SCRIPT_FOLDER
    write(scripts / "composer.bat", b"@echo off\r\nphp \"%~dp0composer.phar\" %*\r\n")
    write(scripts / "composer.phar", b"#!/usr/bin/env php\n<?php echo 'composer';\n")

    for relative in SEB_FOLDERS:
        seb = root / relative
        write(seb / "windows.bat", b"@echo off\r\nstart seb.exe\r\n")
        write(seb / "macos.command", b"#!/bin/bash\nopen seb.app\n")
        write(seb / "cert" / "mitmproxy-ca-cert.pem", b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")

    docs = root / DOCS_FOLDER
    write(docs / "week1" / "lecture.md", b"# Lecture 1\n")
    write(docs / "week1" / "reading.pdf", b"%PDF-1.7\n%%EOF\n")
    write(docs / "slides" / "deck.md", b"# Slides\n")
    return root


def add_sonarqube_like(root: Path) -> Path:
    """An app whose own folder shows no program: lib holds the jars, the rest (conf, data, logs) is just data.

    It sits in a plain container that has a loose note of its own.
    """
    app = root / SONAR_FOLDER
    write(app / "lib" / "sonar-application.jar", b"PK\x03\x04 jar")
    write(app / "bin" / "windows" / "StartSonar.bat", b"@echo off\r\njava -jar ..\\..\\lib\\sonar-application.jar\r\n")
    write(app / "conf" / "sonar.properties", b"sonar.web.port=9000\n")
    write(app / "data" / "es8" / "_state" / "state-1.st", b"\x00\x01 index state")
    write(app / "logs" / "sonar.log", b"started\n")
    write(root / "apps_dist" / "tools_overview.md", b"# Tools lying next to the app\n")
    return app


def add_docs_with_installer(root: Path) -> Path:
    """A tree of documents with one subfolder holding an installer."""
    docs = root / INSTALLER_DOCS
    write(docs / "notes.md", b"# Course notes\n")
    write(docs / "week1" / "lecture2.md", b"# Lecture 2\n")
    write(docs / "installer" / "setup.exe", b"MZ\x90\x00 setup")
    return docs


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


class DriveCase(unittest.TestCase):
    """``self.drive`` holds the folders above; the host's registry and shortcuts are never read.

    ``self.refs`` is what the system "uses" (nothing, unless a test fills it) and ``classify`` runs the real CLI.
    """

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory(prefix="sd_classify_")
        self.addCleanup(tmp.cleanup)
        self.drive = build_real_world_drive(Path(tmp.name).resolve() / "drive")
        self.refs = SystemRefIndex([])
        patcher = patch("smart_drive.core.system_refs.SystemRefIndex.collect", side_effect=lambda **_: self.refs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def classify(self, *argv: str) -> Tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["classify", "--root", str(self.drive), *argv])
        return code, out.getvalue(), err.getvalue()

    def plan(self, *argv: str) -> Dict[str, Any]:
        code, out, err = self.classify(*argv, "--dry-run", "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def relative(self, path: str) -> str:
        """``path`` below the drive, with '/' separators."""
        return Path(path).relative_to(self.drive).as_posix()

    def bundles(self, data: Dict[str, Any]) -> Dict[str, str]:
        """The skipped bundles of a report as {path below the drive: reason}."""
        return {self.relative(item["path"]): item["reason"] for item in data["skipped_bundles"]}

    def planned(self, data: Dict[str, Any]) -> List[str]:
        """The sources of the planned moves of a report, below the drive."""
        return [self.relative(action["source_path"]) for action in data["actions"]]
