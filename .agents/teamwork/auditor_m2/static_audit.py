import ast
import os
import sys
from pathlib import Path

FILES = [
    Path(r"d:\teamwork_projects\smart_drive_os\smart_drive\core\snapshot.py"),
    Path(r"d:\teamwork_projects\smart_drive_os\smart_drive\cli\cmd_snapshot.py"),
    Path(r"d:\teamwork_projects\smart_drive_os\smart_drive\cli\cmd_backup.py"),
]

STDLIB_MODULES = {
    "__future__", "argparse", "dataclasses", "hashlib", "json", "logging",
    "math", "os", "pathlib", "shutil", "sys", "time", "typing", "collections",
    "functools", "itertools", "io", "copy", "tempfile", "unittest"
}

print("=== STATIC AST & DEPENDENCY AUDIT ===")

for filepath in FILES:
    print(f"\nScanning: {filepath.name}")
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    
    # 1. Imports
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])
    
    unique_imports = sorted(set(imports))
    print(f"  Imports detected: {unique_imports}")
    non_stdlib = [m for m in unique_imports if m not in STDLIB_MODULES and m != "smart_drive"]
    if non_stdlib:
        print(f"  [FAIL] Non-stdlib imports detected: {non_stdlib}")
    else:
        print(f"  [PASS] Zero external dependencies. All imports are stdlib or internal smart_drive.")

    # 2. String literal inspection for hardcoded 64-char hex strings
    hex_hashes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value.strip()
            if len(val) == 64 and all(c in "0123456789abcdefABCDEF" for c in val):
                hex_hashes.append((node.lineno, val))
    
    print(f"  64-char hex constants found: {len(hex_hashes)}")
    for line, h in hex_hashes:
        print(f"    Line {line}: {h}")
        if h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855":
            print("      -> Standard empty string SHA-256 (authoritative RFC constant for 0-byte file).")
        else:
            print("      -> [WARN] Non-empty hardcoded hash detected!")

print("\n=== STATIC AST AUDIT COMPLETE ===")
