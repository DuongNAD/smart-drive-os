"""tests/test_compliance.py - Privacy Policy & Marketplace Compliance Test Suite.

100% Python Standard Library unittest. Zero external dependencies.
Verifies:
1. PRIVACY.md existence, non-empty substantive content, and mandatory sections:
   - Local-only storage & strict data isolation
   - Zero telemetry & zero analytics
   - Zero PII logging
   - Zero external network transmission & air-gap readiness
   - Ecosystem & marketplace compliance (OpenAI, Claude, M8ven)
   - Model Context Protocol (MCP) trust and safety
   - Security vulnerability disclosure SLA
2. README.md & README_VN.md privacy shields and clickable links to PRIVACY.md.
3. Inviolable whitelist protection for PRIVACY.md via PROTECTED_ROOT_FILES and is_protected_root_file.
4. pyproject.toml marketplace metadata completeness:
   - Author name and email
   - Official URLs: Homepage, Documentation, Repository, Issues, Changelog
   - 20+ keywords
   - 20+ Trove classifiers
   - Runtime dependencies strictly empty (`dependencies = []`).
5. Codebase-wide static audit for zero external runtime imports and zero telemetry libraries.
"""

from __future__ import annotations

import ast
import os
import re
import sys
import unittest
from pathlib import Path
from typing import Dict, List, Set

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import tomllib
except ImportError:  # Python < 3.11 fallback
    import tomli as tomllib  # type: ignore

from smart_drive.core.config import (
    PROTECTED_ROOT_FILES,
    is_protected_root_file,
)
from tests.helpers import SmartDriveTestCase


class TestPrivacyPolicyCompliance(SmartDriveTestCase):
    """Verifies PRIVACY.md structure, compliance guarantees, and documentation references."""

    def setUp(self) -> None:
        super().setUp()
        self.privacy_path = PROJECT_ROOT / "PRIVACY.md"
        self.readme_path = PROJECT_ROOT / "README.md"
        self.readme_vn_path = PROJECT_ROOT / "README_VN.md"

    def test_privacy_file_exists_and_substantive(self) -> None:
        """PRIVACY.md must exist at repository root and be substantive (> 2000 bytes, > 50 lines)."""
        self.assertTrue(self.privacy_path.is_file(), f"Missing PRIVACY.md at {self.privacy_path}")
        content = self.privacy_path.read_text(encoding="utf-8")
        self.assertGreater(len(content), 2000, "PRIVACY.md is too short to be a substantive policy")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        self.assertGreater(len(lines), 50, "PRIVACY.md must contain detailed policy clauses")

    def test_privacy_core_pledge_and_isolation(self) -> None:
        """PRIVACY.md must explicitly proclaim local-only, zero-telemetry, and air-gap pledges."""
        content = self.privacy_path.read_text(encoding="utf-8")
        self.assertIn("100% Local-Only Operation", content)
        self.assertIn("Zero Telemetry", content)
        self.assertIn("Zero PII Logging", content)
        self.assertIn("Zero External Network Transmission", content)
        self.assertIn("Air-Gap", content)

    def test_privacy_mandatory_sections_present(self) -> None:
        """PRIVACY.md must articulate all 6 required compliance and technical sections."""
        content = self.privacy_path.read_text(encoding="utf-8")

        required_sections = [
            "Executive Summary",
            "Core Privacy Guarantees",
            "Security Architecture & Data Handling Model",
            "Model Context Protocol (MCP) Trust & Safety",
            "Ecosystem & Marketplace Directory Compliance",
            "Security Vulnerability Reporting",
        ]
        for sec in required_sections:
            self.assertIn(sec, content, f"PRIVACY.md missing required section: '{sec}'")

    def test_privacy_marketplace_directory_standards(self) -> None:
        """PRIVACY.md must reference OpenAI, Claude/Anthropic, and M8ven directory standards."""
        content = self.privacy_path.read_text(encoding="utf-8")
        self.assertIn("OpenAI GPT Store", content)
        self.assertIn("Anthropic Claude", content)
        self.assertIn("M8ven", content)

    def test_privacy_tool_hint_standards_documented(self) -> None:
        """PRIVACY.md must document MCP tool safety hints (readOnlyHint, destructiveHint, etc.)."""
        content = self.privacy_path.read_text(encoding="utf-8")
        self.assertIn("readOnlyHint", content)
        self.assertIn("destructiveHint", content)
        self.assertIn("idempotentHint", content)
        self.assertIn("openWorldHint", content)

    def test_privacy_vulnerability_contact_and_sla(self) -> None:
        """PRIVACY.md must state responsible disclosure channel and response commitment."""
        content = self.privacy_path.read_text(encoding="utf-8")
        self.assertIn("smartdrive.os@proton.me", content)
        self.assertIn("GitHub Issues", content)
        self.assertIn("48 hours", content)

    def test_readme_contains_clickable_privacy_link_and_badge(self) -> None:
        """README.md must feature clickable badge and link to PRIVACY.md."""
        self.assertTrue(self.readme_path.is_file(), "README.md not found")
        content = self.readme_path.read_text(encoding="utf-8")

        # Must have privacy badge linking to PRIVACY.md
        self.assertRegex(
            content,
            r"\[!\[Privacy:[^\]]+\]\([^\)]+\)\]\(PRIVACY\.md\)",
            "README.md missing clickable privacy shield badge",
        )
        # Must have explicit markdown link to PRIVACY.md
        self.assertIn("[PRIVACY.md](PRIVACY.md)", content)
        # Must have dedicated privacy section
        self.assertIn("## Privacy, Security & Data Isolation", content)

    def test_readme_vn_contains_clickable_privacy_link_and_badge(self) -> None:
        """README_VN.md must feature clickable badge and Vietnamese privacy section."""
        self.assertTrue(self.readme_vn_path.is_file(), "README_VN.md not found")
        content = self.readme_vn_path.read_text(encoding="utf-8")

        # Must have privacy badge linking to PRIVACY.md
        self.assertRegex(
            content,
            r"\[!\[Privacy:[^\]]+\]\([^\)]+\)\]\(PRIVACY\.md\)",
            "README_VN.md missing clickable privacy shield badge",
        )
        # Must have explicit markdown link to PRIVACY.md
        self.assertIn("[PRIVACY.md](PRIVACY.md)", content)
        # Must have dedicated privacy section
        self.assertIn("Bảo Mật & Quyền Riêng Tư Dữ Liệu", content)

    def test_privacy_md_inviolable_in_whitelist(self) -> None:
        """privacy.md must be immutably protected against deletion in core configuration."""
        # Check frozenset membership
        self.assertIn("privacy.md", PROTECTED_ROOT_FILES)

        # Check case-insensitivity and path variants
        self.assertTrue(is_protected_root_file("privacy.md"))
        self.assertTrue(is_protected_root_file("PRIVACY.md"))
        self.assertTrue(is_protected_root_file("Privacy.Md"))
        self.assertTrue(is_protected_root_file(os.path.join("root", "PRIVACY.md")))
        self.assertTrue(is_protected_root_file("root/PRIVACY.MD"))


class TestMarketplaceMetadataCompliance(SmartDriveTestCase):
    """Verifies pyproject.toml compliance with PyPI, OpenAI, and Claude marketplace specs."""

    def setUp(self) -> None:
        super().setUp()
        self.pyproject_path = PROJECT_ROOT / "pyproject.toml"
        self.assertTrue(self.pyproject_path.is_file(), "pyproject.toml not found")
        with open(self.pyproject_path, "rb") as f:
            self.data = tomllib.load(f)
        self.project = self.data.get("project", {})

    def test_author_email_present(self) -> None:
        """pyproject.toml must declare author name and authoritative contact email."""
        authors = self.project.get("authors", [])
        self.assertIsInstance(authors, list)
        self.assertGreater(len(authors), 0)

        author_emails = [a.get("email") for a in authors if isinstance(a, dict) and "email" in a]
        self.assertIn("smartdrive.os@proton.me", author_emails)

    def test_marketplace_urls_complete_and_valid(self) -> None:
        """pyproject.toml must specify all standard repository and documentation URLs."""
        urls = self.project.get("urls", {})
        self.assertIsInstance(urls, dict)

        required_keys = ["Homepage", "Documentation", "Repository", "Issues", "Changelog"]
        for key in required_keys:
            self.assertIn(key, urls, f"Missing required URL in pyproject.toml: {key}")
            url_val = urls[key]
            self.assertTrue(
                url_val.startswith("https://github.com/DuongNAD/smart-drive-os"),
                f"URL for {key} ({url_val}) must point to authoritative repo",
            )

    def test_keywords_count_and_domain_coverage(self) -> None:
        """pyproject.toml must contain >= 20 keywords covering domain essentials."""
        keywords = self.project.get("keywords", [])
        self.assertIsInstance(keywords, list)
        self.assertGreaterEqual(
            len(keywords),
            20,
            f"Expected at least 20 keywords, found {len(keywords)}",
        )

        expected_subset = {
            "ssd",
            "exfat",
            "cluster-slack",
            "fts5",
            "mcp",
            "model-context-protocol",
            "privacy",
            "zero-telemetry",
            "zero-dependency",
            "local-first",
            "air-gapped",
        }
        keywords_lower = {k.lower() for k in keywords}
        missing = expected_subset - keywords_lower
        self.assertEqual(missing, set(), f"Missing essential domain keywords: {missing}")

    def test_trove_classifiers_count_and_breadth(self) -> None:
        """pyproject.toml must contain >= 20 Trove classifiers covering OS and Python versions."""
        classifiers = self.project.get("classifiers", [])
        self.assertIsInstance(classifiers, list)
        self.assertGreaterEqual(
            len(classifiers),
            20,
            f"Expected at least 20 Trove classifiers, found {len(classifiers)}",
        )

        # OS coverage
        self.assertTrue(any("Microsoft :: Windows" in c for c in classifiers))
        self.assertTrue(any("MacOS" in c for c in classifiers))
        self.assertTrue(any("Linux" in c for c in classifiers))

        # License & Topic
        self.assertTrue(any("MIT License" in c for c in classifiers))
        self.assertTrue(any("Topic :: System :: Filesystems" in c for c in classifiers))
        self.assertTrue(any("Topic :: Security" in c for c in classifiers))


class TestZeroDependencyInvariant(SmartDriveTestCase):
    """Hard invariant verification: strictly zero external runtime dependencies."""

    def test_runtime_dependencies_strictly_empty(self) -> None:
        """pyproject.toml must define runtime dependencies as an empty list."""
        pyproject_path = PROJECT_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        deps = data.get("project", {}).get("dependencies")
        self.assertEqual(deps, [], f"Runtime dependencies must be strictly empty, got {deps}")

        # Regex check on raw file text to ensure formatting compliance
        raw_text = pyproject_path.read_text(encoding="utf-8")
        self.assertRegex(
            raw_text,
            r"dependencies\s*=\s*\[\s*\]",
            "Raw pyproject.toml does not contain literal 'dependencies = []'",
        )

    def test_no_external_runtime_imports_across_codebase(self) -> None:
        """Every import across smart_drive must belong to Python Standard Library."""
        stdlib_modules: Set[str] = set(sys.stdlib_module_names)
        # Built-in or standard pseudomodules
        stdlib_modules.update({"_thread", "_winapi", "nt", "posix"})

        smart_drive_dir = PROJECT_ROOT / "smart_drive"
        self.assertTrue(smart_drive_dir.is_dir(), "smart_drive package directory not found")

        non_stdlib_imports: List[str] = []

        for py_file in smart_drive_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            except Exception as err:
                self.fail(f"Failed to parse AST for {py_file}: {err}")

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_pkg = alias.name.split(".")[0]
                        if top_pkg != "smart_drive" and top_pkg not in stdlib_modules:
                            non_stdlib_imports.append(f"{top_pkg} in {py_file.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.level == 0:
                        top_pkg = node.module.split(".")[0]
                        if top_pkg != "smart_drive" and top_pkg not in stdlib_modules:
                            non_stdlib_imports.append(f"{top_pkg} in {py_file.name}")

        self.assertEqual(
            non_stdlib_imports,
            [],
            f"Detected non-stdlib imports in smart_drive codebase: {non_stdlib_imports}",
        )

    def test_no_tracking_or_telemetry_modules_in_codebase(self) -> None:
        """Strict verification: no telemetry or third-party network libraries appear anywhere."""
        forbidden_modules = {
            "requests",
            "urllib3",
            "aiohttp",
            "httpx",
            "telemetry",
            "posthog",
            "sentry_sdk",
            "mixpanel",
            "segment",
        }
        smart_drive_dir = PROJECT_ROOT / "smart_drive"

        for py_file in smart_drive_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for mod in forbidden_modules:
                pattern = rf"\bimport\s+{mod}\b|\bfrom\s+{mod}\b"
                match = re.search(pattern, text)
                self.assertIsNone(
                    match,
                    f"Forbidden tracking/network library '{mod}' referenced in {py_file.name}",
                )


if __name__ == "__main__":
    unittest.main()
