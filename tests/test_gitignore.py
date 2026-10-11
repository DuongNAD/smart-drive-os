"""tests/test_gitignore.py - Unit and integration tests for Gitignore Governance.

Tests:
1. Stack detection per repo root (python, node, jupyter, java, rust, dotnet, latex, ai-models, learning).
2. Block insertion preserves user rules and CRLF line endings.
3. Idempotent re-apply (no-op diff, no duplicated blocks).
4. Dry run changes nothing on disk.
5. Audit finds tracked junk (.agents/, __pycache__) and personal learning data (.vega, progress.jsonl).
6. Untrack removes files from the git index only and preserves working files on disk.
7. Refusal when repository has staged changes.
8. Repository discovery reuses pruning without recursive scans of deep junk trees.
9. Remote host detection (public vs internal/local).
10. CLI and MCP tool execution.

100% Python Standard Library unittest.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from typing import Optional

from smart_drive.cli.main import main
from smart_drive.core.gitignore import (
    MANAGED_FOOTER,
    MANAGED_HEADER,
    PERSONAL_DATA_PATTERNS,
    GitignoreApplyResult,
    GitignoreAuditResult,
    apply_repo,
    audit_all,
    audit_repo,
    detect_line_ending,
    detect_stacks,
    generate_managed_block,
    is_personal_data_path,
    is_remote_possibly_public,
    merge_managed_block,
)
from smart_drive.mcp.server import SmartDriveMCPServer

GIT = shutil.which("git")
ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="Test",
    GIT_AUTHOR_EMAIL="test@example.com",
    GIT_COMMITTER_NAME="Test",
    GIT_COMMITTER_EMAIL="test@example.com",
    GIT_CONFIG_NOSYSTEM="1",
)


def _force_remove(func, path, _exc) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def git_cmd(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-c", "gc.auto=0", *args],
        cwd=str(cwd),
        env=ENV,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


class TestGitignoreStackDetection(unittest.TestCase):
    """Verifies stack detection across all supported frameworks and domains."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="sd_gi_stack_")).resolve()
        self.addCleanup(shutil.rmtree, self.temp_dir, onerror=_force_remove)

    def test_empty_dir_has_no_stacks(self) -> None:
        self.assertEqual(detect_stacks(str(self.temp_dir)), [])

    def test_python_stack_detection(self) -> None:
        # pyproject.toml
        p1 = self.temp_dir / "p1"
        p1.mkdir()
        (p1 / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
        self.assertIn("python", detect_stacks(str(p1)))

        # setup.py
        p2 = self.temp_dir / "p2"
        p2.mkdir()
        (p2 / "setup.py").write_text("from setuptools import setup\nsetup()\n", encoding="utf-8")
        self.assertIn("python", detect_stacks(str(p2)))

        # requirements-dev.txt
        p3 = self.temp_dir / "p3"
        p3.mkdir()
        (p3 / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
        self.assertIn("python", detect_stacks(str(p3)))

        # Top-level python file
        p4 = self.temp_dir / "p4"
        p4.mkdir()
        (p4 / "main.py").write_text("print('hi')\n", encoding="utf-8")
        self.assertIn("python", detect_stacks(str(p4)))

    def test_node_stack_detection(self) -> None:
        (self.temp_dir / "package.json").write_text('{"name": "app"}', encoding="utf-8")
        self.assertEqual(detect_stacks(str(self.temp_dir)), ["node"])

    def test_jupyter_stack_detection(self) -> None:
        (self.temp_dir / "notebook.ipynb").write_text('{}', encoding="utf-8")
        self.assertEqual(detect_stacks(str(self.temp_dir)), ["jupyter"])

    def test_java_stack_detection(self) -> None:
        (self.temp_dir / "pom.xml").write_text('<project></project>', encoding="utf-8")
        self.assertEqual(detect_stacks(str(self.temp_dir)), ["java"])

    def test_rust_stack_detection(self) -> None:
        (self.temp_dir / "Cargo.toml").write_text('[package]\nname="rs"', encoding="utf-8")
        self.assertEqual(detect_stacks(str(self.temp_dir)), ["rust"])

    def test_dotnet_stack_detection(self) -> None:
        (self.temp_dir / "MyApp.csproj").write_text('<Project></Project>', encoding="utf-8")
        self.assertEqual(detect_stacks(str(self.temp_dir)), ["dotnet"])

    def test_latex_stack_detection(self) -> None:
        (self.temp_dir / "document.tex").write_text('\\documentclass{article}', encoding="utf-8")
        self.assertEqual(detect_stacks(str(self.temp_dir)), ["latex"])

    def test_ai_models_stack_detection(self) -> None:
        (self.temp_dir / "weights.safetensors").write_bytes(b"")
        self.assertEqual(detect_stacks(str(self.temp_dir)), ["ai-models"])

    def test_learning_stack_detection(self) -> None:
        # course.json
        l1 = self.temp_dir / "l1"
        l1.mkdir()
        (l1 / "course.json").write_text('{}', encoding="utf-8")
        self.assertIn("learning", detect_stacks(str(l1)))

        # polaris/brief.json
        l2 = self.temp_dir / "l2"
        (l2 / "polaris").mkdir(parents=True)
        (l2 / "polaris" / "brief.json").write_text('{}', encoding="utf-8")
        self.assertIn("learning", detect_stacks(str(l2)))

        # .vega directory
        l3 = self.temp_dir / "l3"
        (l3 / ".vega").mkdir(parents=True)
        self.assertIn("learning", detect_stacks(str(l3)))

    def test_multi_stack_combination(self) -> None:
        (self.temp_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (self.temp_dir / "model.onnx").write_bytes(b"")
        (self.temp_dir / "deck.json").write_text("{}", encoding="utf-8")
        stacks = detect_stacks(str(self.temp_dir))
        self.assertEqual(stacks, ["ai-models", "learning", "python"])


class TestGitignoreBlockManagement(unittest.TestCase):
    """Verifies insertion, line-endings, and idempotence of managed blocks."""

    def test_line_ending_detection(self) -> None:
        self.assertEqual(detect_line_ending("line1\r\nline2\r\n"), "\r\n")
        self.assertEqual(detect_line_ending("line1\nline2\n"), "\n")
        self.assertEqual(detect_line_ending(""), "\n")

    def test_block_insertion_preserves_user_lines_and_crlf(self) -> None:
        user_content = "# My Custom Rules\r\nmy_secret_dir/\r\n*.local\r\n"
        managed_block = generate_managed_block(["python"], newline="\r\n")
        merged = merge_managed_block(user_content, managed_block, newline="\r\n")

        # Must preserve original user rules
        self.assertTrue(merged.startswith("# My Custom Rules\r\nmy_secret_dir/\r\n*.local\r\n"))
        # Must contain managed header and footer
        self.assertIn(MANAGED_HEADER, merged)
        self.assertIn(MANAGED_FOOTER, merged)
        self.assertIn("__pycache__/", merged)
        # Must not contain single LF without CR
        self.assertNotIn("\r\r\n", merged)
        non_crlf_newlines = [idx for idx, ch in enumerate(merged) if ch == "\n" and (idx == 0 or merged[idx - 1] != "\r")]
        self.assertEqual(len(non_crlf_newlines), 0, "All newlines must be CRLF")

    def test_idempotent_reapply(self) -> None:
        user_content = "custom_folder/\n"
        block1 = generate_managed_block(["node"], newline="\n")
        applied_once = merge_managed_block(user_content, block1, newline="\n")

        block2 = generate_managed_block(["node"], newline="\n")
        applied_twice = merge_managed_block(applied_once, block2, newline="\n")

        self.assertEqual(applied_once, applied_twice)
        self.assertEqual(applied_twice.count(MANAGED_HEADER), 1)
        self.assertEqual(applied_twice.count(MANAGED_FOOTER), 1)

    def test_reapply_with_changed_stacks_replaces_block(self) -> None:
        user_content = "custom_folder/\n"
        block_python = generate_managed_block(["python"], newline="\n")
        applied_python = merge_managed_block(user_content, block_python, newline="\n")
        self.assertIn("__pycache__/", applied_python)

        # Update to rust
        block_rust = generate_managed_block(["rust"], newline="\n")
        applied_rust = merge_managed_block(applied_python, block_rust, newline="\n")
        self.assertIn("custom_folder/\n", applied_rust)
        self.assertIn("target/", applied_rust)
        self.assertNotIn("__pycache__/", applied_rust)


class TestRemoteUrlAnalysis(unittest.TestCase):
    """Verifies privacy analysis of remote URL hosts without network calls."""

    def test_remote_url_privacy(self) -> None:
        # Public or unknown hosts
        pub, _ = is_remote_possibly_public("https://github.com/DuongNAD/smart-drive-os.git")
        self.assertTrue(pub)

        pub, _ = is_remote_possibly_public("git@gitlab.com:group/project.git")
        self.assertTrue(pub)

        pub, _ = is_remote_possibly_public("https://codeberg.org/user/repo")
        self.assertTrue(pub)

        pub, _ = is_remote_possibly_public("")
        self.assertTrue(pub, "No remote treated as possibly public")

        # Private / local hosts
        pub, _ = is_remote_possibly_public("file:///D:/repos/local_repo")
        self.assertFalse(pub)

        pub, _ = is_remote_possibly_public("D:\\repos\\local_repo")
        self.assertFalse(pub)

        pub, _ = is_remote_possibly_public("http://localhost:3000/user/repo.git")
        self.assertFalse(pub)

        pub, _ = is_remote_possibly_public("git@mycompany.internal:team/repo.git")
        self.assertFalse(pub)

        pub, _ = is_remote_possibly_public("https://server.local/repo.git")
        self.assertFalse(pub)


@unittest.skipIf(GIT is None, "git executable not found on PATH")
class TestGitignoreGovernanceWorkflow(unittest.TestCase):
    """End-to-end integration tests using isolated git repositories."""

    def setUp(self) -> None:
        self.temp_root = Path(tempfile.mkdtemp(prefix="sd_gi_workflow_")).resolve()
        self.addCleanup(shutil.rmtree, self.temp_root, onerror=_force_remove)
        self.repo = self.temp_root / "test_repo"
        self.repo.mkdir()

        # Initialize git repo
        git_cmd(self.repo, "init", "-b", "main")

    def test_dry_run_changes_nothing(self) -> None:
        (self.repo / "main.py").write_text("print('hello')", encoding="utf-8")
        gi_path = self.repo / ".gitignore"
        self.assertFalse(gi_path.exists())

        res = apply_repo(str(self.repo), dry_run=True, untrack=False)
        self.assertTrue(res.dry_run)
        self.assertFalse(res.applied)
        self.assertTrue(len(res.diff) > 0)
        self.assertFalse(gi_path.exists(), "Dry-run must not create .gitignore on disk")

    def test_apply_writes_file_and_preserves_user_rules(self) -> None:
        (self.repo / "main.py").write_text("print('hello')", encoding="utf-8")
        gi_path = self.repo / ".gitignore"
        gi_path.write_text("my_custom_user_rule/\n", encoding="utf-8")

        res = apply_repo(str(self.repo), dry_run=False, untrack=False)
        self.assertFalse(res.dry_run)
        self.assertTrue(res.applied)
        self.assertTrue(gi_path.exists())

        content = gi_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("my_custom_user_rule/\n"))
        self.assertIn(MANAGED_HEADER, content)
        self.assertIn(MANAGED_FOOTER, content)
        self.assertIn(".agents/", content)
        self.assertIn("__pycache__/", content)

    def test_audit_finds_tracked_junk_and_personal_data(self) -> None:
        # Create python & learning repo files
        (self.repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (self.repo / "course.json").write_text("{}", encoding="utf-8")

        # Create tracked junk
        agents_dir = self.repo / ".agents"
        agents_dir.mkdir()
        (agents_dir / "scratch.json").write_text("{}", encoding="utf-8")

        pycache_dir = self.repo / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "app.cpython-311.pyc").write_bytes(b"")

        # Create tracked personal learning data
        vega_dir = self.repo / ".vega"
        vega_dir.mkdir()
        (vega_dir / "cards.json").write_text("[]", encoding="utf-8")
        (self.repo / "progress.jsonl").write_text('{"progress": 1}\n', encoding="utf-8")

        # Commit them all
        git_cmd(self.repo, "add", ".")
        git_cmd(self.repo, "commit", "-m", "initial commit with junk and personal data")

        # Run audit
        audit = audit_repo(str(self.repo))
        self.assertFalse(audit.has_gitignore)
        self.assertIn("python", audit.stacks)
        self.assertIn("learning", audit.stacks)

        # Tracked junk detected
        self.assertGreaterEqual(audit.tracked_junk_count, 2)
        self.assertIn(".agents/", audit.tracked_junk_by_pattern)
        self.assertTrue(any(".agents" in f for f in audit.tracked_junk_files))

        # Personal data detected
        self.assertIn(".vega/cards.json", [f.replace("\\", "/") for f in audit.tracked_personal_data])
        self.assertIn("progress.jsonl", [f.replace("\\", "/") for f in audit.tracked_personal_data])

        # Personal data is at risk because remote is unset (treated as possibly public)
        self.assertTrue(audit.personal_data_at_risk)

    def test_untrack_removes_from_index_only_and_keeps_files(self) -> None:
        (self.repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        agents_dir = self.repo / ".agents"
        agents_dir.mkdir()
        scratch_file = agents_dir / "scratch.json"
        scratch_file.write_text("{}", encoding="utf-8")

        git_cmd(self.repo, "add", ".")
        git_cmd(self.repo, "commit", "-m", "commit scratch file")

        # Verify tracked
        tracked_before = git_cmd(self.repo, "ls-files")
        self.assertIn(".agents/scratch.json", tracked_before)

        # Apply with untrack=True
        res = apply_repo(str(self.repo), dry_run=False, untrack=True)
        self.assertTrue(res.applied)
        self.assertIn(".agents/scratch.json", [f.replace("\\", "/") for f in res.untracked_files])

        # Verify file STILL exists on disk
        self.assertTrue(scratch_file.exists(), "Working file must NOT be deleted!")

        # Verify file is NO LONGER tracked in git index
        tracked_after = git_cmd(self.repo, "ls-files")
        self.assertNotIn(".agents/scratch.json", tracked_after)

    def test_refuses_when_staged_changes_exist(self) -> None:
        (self.repo / "init.txt").write_text("initial", encoding="utf-8")
        git_cmd(self.repo, "add", "init.txt")
        git_cmd(self.repo, "commit", "-m", "initial")

        # Stage a new change
        (self.repo / "staged.txt").write_text("pending", encoding="utf-8")
        git_cmd(self.repo, "add", "staged.txt")

        # untrack must refuse
        with self.assertRaises(ValueError) as ctx:
            apply_repo(str(self.repo), dry_run=False, untrack=True)
        self.assertIn("staged changes", str(ctx.exception).lower())

    def test_repos_discovery_without_recursive_scans(self) -> None:
        # Create sub repo
        sub_repo = self.temp_root / "projects" / "app"
        sub_repo.mkdir(parents=True)
        git_cmd(sub_repo, "init", "-b", "main")

        # Create heavy node_modules with fake git that should be pruned
        heavy = self.temp_root / "projects" / "node_modules" / "fake_repo"
        heavy.mkdir(parents=True)
        (heavy / ".git").mkdir()

        # Run audit_all
        results = audit_all(str(self.temp_root))
        audited_paths = [Path(r.repo).resolve() for r in results]

        self.assertIn(self.repo.resolve(), audited_paths)
        self.assertIn(sub_repo.resolve(), audited_paths)
        self.assertNotIn(heavy.resolve(), audited_paths, "node_modules must be pruned from scan")

    def test_cli_audit_and_apply(self) -> None:
        # CLI audit JSON
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            ret = main(["gitignore", "audit", str(self.repo), "--json"])
        self.assertEqual(ret, 0)
        data = json.loads(stdout.getvalue())
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["has_gitignore"], False)

        # CLI apply dry-run (no --yes)
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            ret = main(["gitignore", "apply", str(self.repo)])
        self.assertEqual(ret, 0)
        self.assertIn("DRY-RUN", stdout.getvalue())
        self.assertFalse((self.repo / ".gitignore").exists())

        # CLI apply execution (--yes)
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            ret = main(["gitignore", "apply", str(self.repo), "--yes"])
        self.assertEqual(ret, 0)
        self.assertTrue((self.repo / ".gitignore").exists())

    def test_mcp_ssd_gitignore_tool(self) -> None:
        server = SmartDriveMCPServer(root=str(self.temp_root))

        # 1. Audit via MCP
        res_audit = server.dispatch_tool("ssd_gitignore", {"action": "audit", "repo": str(self.repo)})
        self.assertIn("summary", res_audit)
        self.assertIn("repos", res_audit)
        self.assertEqual(res_audit["summary"]["repos_audited"], 1)

        # 2. Apply dry-run via MCP
        res_apply_dry = server.dispatch_tool(
            "ssd_gitignore",
            {"action": "apply", "repo": str(self.repo), "apply": False},
        )
        self.assertTrue(res_apply_dry["dry_run"])
        self.assertFalse(res_apply_dry["applied"])

        # 3. Apply execute via MCP
        res_apply = server.dispatch_tool(
            "ssd_gitignore",
            {"action": "apply", "repo": str(self.repo), "apply": True},
        )
        self.assertFalse(res_apply["dry_run"])
        self.assertTrue(res_apply["applied"])
        self.assertTrue((self.repo / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
