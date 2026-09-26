import os
from pathlib import Path

root = Path(r"d:\teamwork_projects\smart_drive_os")

print("=== CHECKING FOR PRE-POPULATED TEST/AUDIT ARTIFACTS ===")

suspicious = []
for p in root.rglob("*"):
    if ".git" in p.parts:
        continue
    if p.is_file():
        name_lower = p.name.lower()
        if any(keyword in name_lower for keyword in ["result", "output", ".log", "test_report", "attestation"]):
            suspicious.append(p)

print(f"Found {len(suspicious)} matching files:")
for s in suspicious:
    rel = s.relative_to(root)
    print(f"  {rel} ({s.stat().st_size} bytes)")

print("\n=== ARTIFACT CHECK COMPLETE ===")
