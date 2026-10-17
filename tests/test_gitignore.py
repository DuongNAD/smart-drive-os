"""tests/test_gitignore.py - Unit and integration tests for Gitignore Governance.

Tests:
1. Stack detection per repo root (python, node, jupyter, java, rust, dotnet, latex, ai-models, learning).
2. Block insertion preserves user rules and CRLF line endings.
3. Idempotent re-apply (no-op diff, no duplicated blocks).
4. Dry run changes nothing on disk.
5. Audit finds tracked junk (.agents/, __pycache__) and personal learning data (.vega, .sirius, progress.jsonl).
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

from smart_drive.cli.cmd_gitignore import format_audit_report
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
    check_gh_authenticated,
    clear_gh_cache,
    detect_line_ending,
    detect_stacks,
    extract_github_owner_repo,
    extract_remote_owner,
    extract_secret_var_names,
    generate_managed_block,
    get_configured_git_owner,
    is_personal_data_path,
    is_remote_possibly_public,
    is_secret_path,
    is_third_party_path,
    merge_managed_block,
    query_gh_visibility,
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

        # .sirius directory (Sirius execution journal)
        l4 = self.temp_dir / "l4"
        (l4 / ".sirius").mkdir(parents=True)
        self.assertEqual(detect_stacks(str(l4)), ["learning"])

        # .polaris hub directory and Orion/Aurora personal-data files
        l5 = self.temp_dir / "l5"
        (l5 / ".polaris").mkdir(parents=True)
        self.assertEqual(detect_stacks(str(l5)), ["learning"])

        l6 = self.temp_dir / "l6"
        l6.mkdir()
        (l6 / "orion-attempts.jsonl").write_text("{}\n", encoding="utf-8")
        self.assertEqual(detect_stacks(str(l6)), ["learning"])

        if GIT is not None:
            l7 = self.temp_dir / "l7"
            l7.mkdir()
            git_cmd(l7, "init")
            (l7 / ".polaris").mkdir()
            (l7 / ".polaris" / "state.json").write_text("{}\n", encoding="utf-8")
            (l7 / "orion-attempts.jsonl").write_text("{}\n", encoding="utf-8")
            git_cmd(l7, "add", ".")
            git_cmd(l7, "commit", "-m", "track personal data")

            res = apply_repo(str(l7), dry_run=False, untrack=True)
            self.assertIn("learning", res.stacks)
            untracked_norm = {f.replace("\\", "/") for f in res.untracked_files}
            self.assertIn(".polaris/state.json", untracked_norm)
            self.assertIn("orion-attempts.jsonl", untracked_norm)

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
        self.assertIn("**/.agents/*", content)
        self.assertIn("!**/.agents/skills/", content)
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
        self.assertIn("**/.agents/*", audit.tracked_junk_by_pattern)
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

    def test_agent_skills_workflows_and_rules_stay_tracked(self) -> None:
        """Agent scratch under .agents/ is junk at any depth; .agents/skills|workflows|rules are config."""
        files = {
            ".agents/teamwork/progress.md": "scratch",
            ".agents/skills/tutor/SKILL.md": "skill",
            "course/.agents/orchestrator/BRIEFING.md": "scratch",
            "course/.agents/skills/tutor/SKILL.md": "skill",
            "course/.agents/workflows/review.md": "workflow",
            "course/.agents/rules/style.md": "rule",
        }
        for rel, text in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git_cmd(self.repo, "add", ".")
        git_cmd(self.repo, "commit", "-m", "agent scratch and agent config")

        audit = audit_repo(str(self.repo))
        junk = sorted(f.replace("\\", "/") for f in audit.tracked_junk_files)
        self.assertEqual(junk, [".agents/teamwork/progress.md", "course/.agents/orchestrator/BRIEFING.md"])

        res = apply_repo(str(self.repo), dry_run=False, untrack=True)
        self.assertTrue(res.applied)
        tracked = git_cmd(self.repo, "ls-files")
        for keep in (".agents/skills/tutor/SKILL.md", "course/.agents/skills/tutor/SKILL.md",
                     "course/.agents/workflows/review.md", "course/.agents/rules/style.md"):
            self.assertIn(keep, tracked)
        self.assertNotIn(".agents/teamwork/progress.md", tracked)
        self.assertNotIn("course/.agents/orchestrator/BRIEFING.md", tracked)

        # Real git agrees: new scratch is ignored at any depth, new skills are not.
        (self.repo / "course/.agents/worker").mkdir(parents=True)
        (self.repo / "course/.agents/worker/notes.md").write_text("x", encoding="utf-8")
        (self.repo / "course/.agents/skills/new").mkdir(parents=True)
        (self.repo / "course/.agents/skills/new/SKILL.md").write_text("x", encoding="utf-8")
        ignored = subprocess.run(["git", "check-ignore", "course/.agents/worker/notes.md"],
                                 cwd=str(self.repo), capture_output=True, text=True)
        self.assertEqual(ignored.returncode, 0, "agent scratch should be ignored")
        kept = subprocess.run(["git", "check-ignore", "course/.agents/skills/new/SKILL.md"],
                              cwd=str(self.repo), capture_output=True, text=True)
        self.assertNotEqual(kept.returncode, 0, "agent skills must not be ignored")

    def test_learning_stack_matches_the_personal_data_contract(self) -> None:
        from smart_drive.core.gitignore import STACK_PATTERNS
        self.assertEqual(STACK_PATTERNS["learning"], PERSONAL_DATA_PATTERNS)
        self.assertIn(".polaris/", PERSONAL_DATA_PATTERNS)
        self.assertIn(".sirius/", STACK_PATTERNS["learning"])
        self.assertIn(".sirius/", PERSONAL_DATA_PATTERNS)

    def test_state_folders_beyond_the_shallow_scan_are_found_through_git(self) -> None:
        """The shallow scan stops two levels down; tracked files must still reveal a deeper .vega/.sirius."""
        for name in (".vega", ".sirius"):
            with self.subTest(folder=name):
                repo = self.temp_root / ("deep_" + name.lstrip("."))
                state_file = repo / "hub" / "a" / "b" / name / "state.jsonl"
                state_file.parent.mkdir(parents=True)
                state_file.write_text("{}\n", encoding="utf-8")
                git_cmd(repo, "init", "-b", "main")
                self.assertEqual(detect_stacks(str(repo)), [], "an untracked deep folder is outside the shallow scan")

                git_cmd(repo, "add", ".")
                self.assertEqual(detect_stacks(str(repo)), ["learning"])

    def test_audit_flags_a_tracked_sirius_journal(self) -> None:
        self.assertTrue(is_personal_data_path(".sirius/journal.jsonl"))
        self.assertTrue(is_personal_data_path("hub/.sirius/journal.jsonl"))
        self.assertFalse(is_personal_data_path("sirius/journal.jsonl"))

        journal = self.repo / ".sirius" / "journal.jsonl"
        journal.parent.mkdir()
        journal.write_text('{"event": "session_start"}\n', encoding="utf-8")
        git_cmd(self.repo, "add", ".")
        git_cmd(self.repo, "commit", "-m", "track the sirius journal by mistake")

        audit = audit_repo(str(self.repo))
        self.assertIn("learning", audit.stacks)
        self.assertIn(".sirius/journal.jsonl", [f.replace("\\", "/") for f in audit.tracked_personal_data])
        self.assertIn(".sirius/", audit.missing_patterns)
        # No remote is configured, so the audit treats the repository as possibly public.
        self.assertTrue(audit.personal_data_at_risk)

    def test_apply_ignores_the_sirius_journal(self) -> None:
        journal = self.repo / ".sirius" / "journal.jsonl"
        journal.parent.mkdir()
        journal.write_text('{"event": "session_start"}\n', encoding="utf-8")

        res = apply_repo(str(self.repo), dry_run=False)
        self.assertTrue(res.applied)
        self.assertIn("learning", res.stacks)
        content = (self.repo / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".sirius/", content.splitlines())

        # Real git agrees: the journal can no longer be committed by accident.
        ignored = subprocess.run(["git", "check-ignore", ".sirius/journal.jsonl"],
                                 cwd=str(self.repo), capture_output=True, text=True)
        self.assertEqual(ignored.returncode, 0, "the Sirius journal should be ignored")

    def test_line_endings_of_an_existing_gitignore_are_kept(self) -> None:
        for name, eol in (("crlf", b"\r\n"), ("lf", b"\n")):
            with self.subTest(eol=name):
                gi_path = self.repo / ".gitignore"
                gi_path.write_bytes(b"my_rule/" + eol + b"*.local" + eol)
                res = apply_repo(str(self.repo), dry_run=False)
                self.assertTrue(res.applied)
                data = gi_path.read_bytes()
                self.assertNotIn(b"\r\r\n", data)
                self.assertTrue(data.startswith(b"my_rule/" + eol + b"*.local" + eol))
                if eol == b"\r\n":
                    self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))
                else:
                    self.assertNotIn(b"\r\n", data)

    @unittest.skipUnless(os.name == "nt", "the hidden attribute is a Windows file attribute")
    def test_a_hidden_gitignore_is_rewritten_in_place(self) -> None:
        """open(path, "w") is refused for a hidden file on Windows; apply must still work and keep it hidden."""
        gi_path = self.repo / ".gitignore"
        gi_path.write_text("my_rule/\n", encoding="utf-8")
        subprocess.run(["attrib", "+h", str(gi_path)], check=True, capture_output=True)
        self.addCleanup(lambda: subprocess.run(["attrib", "-h", str(gi_path)], capture_output=True))

        res = apply_repo(str(self.repo), dry_run=False)
        self.assertTrue(res.applied)
        content = gi_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("my_rule/"))
        self.assertIn(MANAGED_HEADER, content)
        self.assertTrue(os.stat(gi_path).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)

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

class TestOwnershipClassification(unittest.TestCase):
    """Verifies repository ownership classification and detection."""

    def test_extract_remote_owner(self) -> None:
        self.assertEqual(extract_remote_owner("https://github.com/myorg/myrepo.git"), "myorg")
        self.assertEqual(extract_remote_owner("https://github.com/myorg/myrepo"), "myorg")
        self.assertEqual(extract_remote_owner("git@github.com:myorg/myrepo.git"), "myorg")
        self.assertEqual(extract_remote_owner("ssh://git@github.com/myorg/myrepo"), "myorg")
        self.assertEqual(extract_remote_owner("https://gitlab.com/group/repo.git"), "group")
        self.assertEqual(extract_remote_owner("https://gitlab.com/group/subgroup/repo.git"), "group")
        self.assertIsNone(extract_remote_owner("C:/local/path"))
        self.assertIsNone(extract_remote_owner(""))
        self.assertIsNone(extract_remote_owner(None))

    def test_extract_github_owner_repo(self) -> None:
        self.assertEqual(extract_github_owner_repo("https://github.com/octocat/hello-world.git"), "octocat/hello-world")
        self.assertEqual(extract_github_owner_repo("git@github.com:octocat/hello-world.git"), "octocat/hello-world")
        self.assertIsNone(extract_github_owner_repo("https://gitlab.com/octocat/hello-world.git"))
        self.assertIsNone(extract_github_owner_repo("C:/local/path"))

    def test_is_third_party_path(self) -> None:
        self.assertTrue(is_third_party_path("D:/repos/open_source/awesome-project"))
        self.assertTrue(is_third_party_path("D:/repos/vendor/libfoo"))
        self.assertTrue(is_third_party_path("D:/repos/third_party/bar"))
        self.assertTrue(is_third_party_path("D:/repos/third-party/baz"))
        self.assertTrue(is_third_party_path("D:/repos/open-source/qux"))
        self.assertFalse(is_third_party_path("D:/repos/my_projects/app"))
        self.assertFalse(is_third_party_path("D:/03_Development_Projects/smart_drive_os"))

    def test_configured_owner_lookup(self) -> None:
        with patch.dict(os.environ, {"SMART_DRIVE_GIT_OWNER": "env-owner"}):
            self.assertEqual(get_configured_git_owner(), "env-owner")
        with patch.dict(os.environ, {}, clear=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "git-config-owner\n"
                self.assertEqual(get_configured_git_owner(), "git-config-owner")

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_ownership_in_audit_repo_and_audit_all(self) -> None:
        temp_root = Path(tempfile.mkdtemp(prefix="sd_gi_owner_")).resolve()
        self.addCleanup(shutil.rmtree, temp_root, onerror=_force_remove)

        # 1. Own repo
        repo_own = temp_root / "repo_own"
        repo_own.mkdir()
        git_cmd(repo_own, "init")
        git_cmd(repo_own, "remote", "add", "origin", "https://github.com/alice/repo_own.git")
        (repo_own / "README.md").write_text("hello", encoding="utf-8")
        git_cmd(repo_own, "add", "README.md")
        git_cmd(repo_own, "commit", "-m", "init")

        # 2. Third-party repo by remote owner
        repo_tp = temp_root / "repo_tp"
        repo_tp.mkdir()
        git_cmd(repo_tp, "init")
        git_cmd(repo_tp, "remote", "add", "origin", "https://github.com/bob/repo_tp.git")
        (repo_tp / "README.md").write_text("hello", encoding="utf-8")
        git_cmd(repo_tp, "add", "README.md")
        git_cmd(repo_tp, "commit", "-m", "init")

        # 3. Third-party repo by directory name (open_source)
        repo_vendor = temp_root / "open_source" / "upstream_lib"
        repo_vendor.mkdir(parents=True)
        git_cmd(repo_vendor, "init")
        (repo_vendor / "README.md").write_text("hello", encoding="utf-8")
        git_cmd(repo_vendor, "add", "README.md")
        git_cmd(repo_vendor, "commit", "-m", "init")

        # 4. Local repo (no remote)
        repo_local = temp_root / "repo_local"
        repo_local.mkdir()
        git_cmd(repo_local, "init")
        (repo_local / "README.md").write_text("hello", encoding="utf-8")
        git_cmd(repo_local, "add", "README.md")
        git_cmd(repo_local, "commit", "-m", "init")

        # Audit with configured owner = "alice"
        res_own = audit_repo(str(repo_own), configured_owner="alice")
        self.assertEqual(res_own.owner_class, "own")

        res_tp = audit_repo(str(repo_tp), configured_owner="alice")
        self.assertEqual(res_tp.owner_class, "third-party")

        res_vendor = audit_repo(str(repo_vendor), configured_owner="alice")
        self.assertEqual(res_vendor.owner_class, "third-party")

        res_local = audit_repo(str(repo_local), configured_owner="alice")
        self.assertEqual(res_local.owner_class, "local")

        # repo_own2 also owned by alice
        repo_own2 = temp_root / "repo_own2"
        repo_own2.mkdir()
        git_cmd(repo_own2, "init")
        git_cmd(repo_own2, "remote", "add", "origin", "https://github.com/alice/repo_own2.git")
        (repo_own2 / "README.md").write_text("hello", encoding="utf-8")
        git_cmd(repo_own2, "add", "README.md")
        git_cmd(repo_own2, "commit", "-m", "init")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SMART_DRIVE_GIT_OWNER", None)
            with patch("smart_drive.core.gitignore.get_configured_git_owner", return_value=None):
                results = audit_all(str(temp_root))
                result_map = {Path(r.repo).name: r for r in results}
                # Alice has 2 repos, Bob has 1 -> Alice is dominant inferred owner
                self.assertEqual(result_map["repo_own"].owner_class, "own")
                self.assertEqual(result_map["repo_own2"].owner_class, "own")
                self.assertEqual(result_map["repo_tp"].owner_class, "third-party")
                self.assertEqual(result_map["upstream_lib"].owner_class, "third-party")
                self.assertEqual(result_map["repo_local"].owner_class, "local")


class TestThirdPartyExclusion(unittest.TestCase):
    """Verifies that third-party repos do not report upstream build/dist choices as junk."""

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_third_party_junk_suppression(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="sd_gi_tp_")).resolve()
        self.addCleanup(shutil.rmtree, temp_dir, onerror=_force_remove)

        tp_repo = temp_dir / "vendor" / "cool_lib"
        tp_repo.mkdir(parents=True)
        git_cmd(tp_repo, "init")
        git_cmd(tp_repo, "remote", "add", "origin", "https://github.com/upstream/cool_lib.git")

        dist_dir = tp_repo / "dist"
        dist_dir.mkdir()
        (dist_dir / "bundle.js").write_text("// bundled", encoding="utf-8")
        (tp_repo / "package.json").write_text("{}", encoding="utf-8")
        git_cmd(tp_repo, "add", ".")
        git_cmd(tp_repo, "commit", "-m", "upstream commit")

        res = audit_repo(str(tp_repo), configured_owner="myuser")
        self.assertEqual(res.owner_class, "third-party")
        self.assertEqual(res.tracked_junk_count, 0)
        self.assertEqual(res.missing_patterns, [])

        report_default = format_audit_report([res], str(tp_repo), show_all=False)
        self.assertIn("Third-Party Repositories", report_default)
        self.assertIn("upstream-managed - junk checks skipped", report_default)
        self.assertNotIn("Tracked Junk Files:", report_default)

        report_all = format_audit_report([res], str(tp_repo), show_all=True)
        self.assertIn("owner: third-party", report_all)


class TestVisibilityFromMockedGh(unittest.TestCase):
    """Verifies GitHub CLI visibility resolution and caching."""

    def setUp(self) -> None:
        clear_gh_cache()

    def tearDown(self) -> None:
        clear_gh_cache()

    def test_check_gh_authenticated(self) -> None:
        with patch("smart_drive.core.gitignore._run_gh") as mock_gh:
            mock_gh.return_value = (0, "Logged in to github.com account alice\n", "")
            self.assertTrue(check_gh_authenticated())

        clear_gh_cache()
        with patch("smart_drive.core.gitignore._run_gh") as mock_gh:
            mock_gh.return_value = (1, "You are not logged in to any GitHub hosts.\n", "")
            self.assertFalse(check_gh_authenticated())

    def test_query_gh_visibility_public_private_internal(self) -> None:
        with patch("smart_drive.core.gitignore.check_gh_authenticated", return_value=True):
            with patch("smart_drive.core.gitignore._run_gh") as mock_gh:
                mock_gh.return_value = (0, '{"visibility": "PUBLIC"}', "")
                vis = query_gh_visibility("alice/public-repo")
                self.assertEqual(vis, "public")
                self.assertEqual(mock_gh.call_count, 1)

                # Test cache: second call does not invoke _run_gh again
                vis2 = query_gh_visibility("alice/public-repo")
                self.assertEqual(vis2, "public")
                self.assertEqual(mock_gh.call_count, 1)

            clear_gh_cache()
            with patch("smart_drive.core.gitignore._run_gh") as mock_gh:
                mock_gh.return_value = (0, '{"visibility": "PRIVATE"}', "")
                self.assertEqual(query_gh_visibility("alice/private-repo"), "private")

            clear_gh_cache()
            with patch("smart_drive.core.gitignore._run_gh") as mock_gh:
                mock_gh.return_value = (0, '{"visibility": "INTERNAL"}', "")
                self.assertEqual(query_gh_visibility("alice/internal-repo"), "internal")

            clear_gh_cache()
            with patch("smart_drive.core.gitignore._run_gh") as mock_gh:
                mock_gh.return_value = (1, "", "error")
                self.assertEqual(query_gh_visibility("alice/err-repo"), "unknown")

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_audit_repo_visibility_warnings(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="sd_gi_vis_")).resolve()
        self.addCleanup(shutil.rmtree, temp_dir, onerror=_force_remove)

        repo = temp_dir / "repo"
        repo.mkdir()
        git_cmd(repo, "init")
        git_cmd(repo, "remote", "add", "origin", "https://github.com/alice/repo.git")

        vega = repo / ".vega"
        vega.mkdir()
        (vega / "cards.json").write_text("[]", encoding="utf-8")
        git_cmd(repo, "add", ".")
        git_cmd(repo, "commit", "-m", "add vega cards")

        # 1. Mock visibility as public -> warning "with public remote!"
        with patch("smart_drive.core.gitignore.query_gh_visibility", return_value="public"):
            audit_pub = audit_repo(str(repo), configured_owner="alice")
            self.assertEqual(audit_pub.visibility, "public")
            self.assertTrue(audit_pub.personal_data_at_risk)
            report_pub = format_audit_report([audit_pub], str(repo))
            self.assertIn("with public remote!", report_pub)

        # 2. Mock visibility as private -> personal_data_at_risk is False, no public exposure warning
        with patch("smart_drive.core.gitignore.query_gh_visibility", return_value="private"):
            audit_priv = audit_repo(str(repo), configured_owner="alice")
            self.assertEqual(audit_priv.visibility, "private")
            self.assertFalse(audit_priv.personal_data_at_risk)
            report_priv = format_audit_report([audit_priv], str(repo))
            self.assertNotIn("with public remote!", report_priv)
            self.assertNotIn("with possibly public remote!", report_priv)

        # 3. Mock visibility as unknown -> warning "with possibly public remote!"
        with patch("smart_drive.core.gitignore.query_gh_visibility", return_value="unknown"):
            audit_unk = audit_repo(str(repo), configured_owner="alice")
            self.assertEqual(audit_unk.visibility, "unknown")
            self.assertTrue(audit_unk.personal_data_at_risk)
            report_unk = format_audit_report([audit_unk], str(repo))
            self.assertIn("with possibly public remote!", report_unk)


class TestTrackedSecretsSection(unittest.TestCase):
    """Verifies secret detection, extraction of variable names only, and untracking."""

    def test_is_secret_path(self) -> None:
        self.assertTrue(is_secret_path(".env"))
        self.assertTrue(is_secret_path(".env.local"))
        self.assertTrue(is_secret_path(".env.production"))
        self.assertTrue(is_secret_path("secrets/server.key"))
        self.assertTrue(is_secret_path("certs/cert.pem"))
        self.assertTrue(is_secret_path("credentials.json"))
        self.assertTrue(is_secret_path("credentials_gcp.json"))

        self.assertFalse(is_secret_path(".env.sample"))
        self.assertFalse(is_secret_path(".env.example"))
        self.assertFalse(is_secret_path(".env.template"))
        self.assertFalse(is_secret_path("config.json"))
        self.assertFalse(is_secret_path("app.py"))

    def test_extract_secret_var_names_env_and_json(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="sd_gi_sec_")).resolve()
        self.addCleanup(shutil.rmtree, temp_dir, onerror=_force_remove)

        env_file = temp_dir / ".env"
        env_file.write_text(
            "# Configuration\n"
            "DB_PASS=SuperSecretP@ssword123\n"
            "OPENAI_API_KEY=\"sk-proj-999988887777\"\n"
            "export STRIPE_SECRET_KEY=whsec_abc123\n"
            "EMPTY_VAR=\n"
            "123INVALID_ID=should_be_skipped\n",
            encoding="utf-8",
        )
        vars_found = extract_secret_var_names(env_file)
        self.assertEqual(vars_found, ["DB_PASS", "OPENAI_API_KEY", "STRIPE_SECRET_KEY", "EMPTY_VAR"])
        vars_str = " ".join(vars_found)
        self.assertNotIn("SuperSecretP@ssword123", vars_str)
        self.assertNotIn("sk-proj-999988887777", vars_str)
        self.assertNotIn("whsec_abc123", vars_str)

        # Built at runtime so secret scanners do not flag this fake key header in the repo.
        pem_header = "-----BEGIN " + "PRIVATE KEY-----"
        json_file = temp_dir / "credentials.json"
        json_file.write_text(
            json.dumps({
                "type": "service_account",
                "private_key_id": "secret-key-id",
                "private_key": pem_header + "\nMIIEv...",
                "client_email": "svc@proj.iam.gserviceaccount.com",
            }),
            encoding="utf-8",
        )
        json_keys = extract_secret_var_names(json_file)
        self.assertEqual(json_keys, ["type", "private_key_id", "private_key", "client_email"])
        keys_str = " ".join(json_keys)
        self.assertNotIn(pem_header, keys_str)
        self.assertNotIn("svc@proj.iam.gserviceaccount.com", keys_str)

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_audit_and_untrack_secrets(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="sd_gi_sec_audit_")).resolve()
        self.addCleanup(shutil.rmtree, temp_dir, onerror=_force_remove)

        repo = temp_dir / "repo"
        repo.mkdir()
        git_cmd(repo, "init")

        env_file = repo / ".env"
        env_file.write_text("SECRET_TOKEN=xyz987token\n", encoding="utf-8")
        cred_file = repo / "credentials.json"
        cred_file.write_text('{"api_token": "token123"}', encoding="utf-8")
        git_cmd(repo, "add", ".")
        git_cmd(repo, "commit", "-m", "accidental secret commit")

        audit = audit_repo(str(repo))
        self.assertEqual(len(audit.tracked_secrets), 2)
        sec_paths = [s["path"].replace("\\", "/") for s in audit.tracked_secrets]
        self.assertIn(".env", sec_paths)
        self.assertIn("credentials.json", sec_paths)

        env_entry = next(s for s in audit.tracked_secrets if s["path"].endswith(".env"))
        self.assertEqual(env_entry["vars"], ["SECRET_TOKEN"])

        report = format_audit_report([audit], str(repo))
        self.assertIn("HIGH SEVERITY: Tracked Secrets Detected", report)
        self.assertIn("variables: SECRET_TOKEN", report)
        self.assertIn("Advice: Rotate secrets", report)
        self.assertNotIn("xyz987token", report)
        self.assertNotIn("token123", report)

        res = apply_repo(str(repo), dry_run=False, untrack=True)
        self.assertTrue(res.applied)
        untracked_norm = [f.replace("\\", "/") for f in res.untracked_files]
        self.assertIn(".env", untracked_norm)
        self.assertIn("credentials.json", untracked_norm)

        self.assertTrue(env_file.exists())
        self.assertTrue(cred_file.exists())

        tracked_after = git_cmd(repo, "ls-files")
        self.assertNotIn(".env", tracked_after)
        self.assertNotIn("credentials.json", tracked_after)


class TestGitignoreCliAuditOptions(unittest.TestCase):
    """Verifies CLI --all and --owner options and JSON fields."""

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_cli_all_and_owner_flags(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="sd_gi_cli_")).resolve()
        self.addCleanup(shutil.rmtree, temp_dir, onerror=_force_remove)

        repo = temp_dir / "my_repo"
        repo.mkdir()
        git_cmd(repo, "init")
        git_cmd(repo, "remote", "add", "origin", "https://github.com/myorg/my_repo.git")
        (repo / "README.md").write_text("hi", encoding="utf-8")
        git_cmd(repo, "add", ".")
        git_cmd(repo, "commit", "-m", "init")

        tp = temp_dir / "vendor" / "upstream_pkg"
        tp.mkdir(parents=True)
        git_cmd(tp, "init")
        git_cmd(tp, "remote", "add", "origin", "https://github.com/other/upstream_pkg.git")
        (tp / "package.json").write_text("{}", encoding="utf-8")
        (tp / "dist").mkdir()
        (tp / "dist" / "bundle.js").write_text("// bundle\n", encoding="utf-8")
        git_cmd(tp, "add", ".")
        git_cmd(tp, "commit", "-m", "upstream build")

        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            ret = main(["gitignore", "audit", str(temp_dir), "--owner", "myorg", "--json"])
        self.assertEqual(ret, 0)
        data = {os.path.basename(r["repo"]): r for r in json.loads(stdout.getvalue())}
        self.assertEqual(data["my_repo"]["owner_class"], "own")
        self.assertEqual(data["upstream_pkg"]["owner_class"], "third-party")
        self.assertEqual(data["upstream_pkg"]["tracked_junk_count"], 0)
        self.assertEqual(data["upstream_pkg"]["missing_patterns"], [])

        stdout_all = io.StringIO()
        with patch("sys.stdout", stdout_all):
            ret_all = main(["gitignore", "audit", str(temp_dir), "--owner", "myorg", "--all", "--json"])
        self.assertEqual(ret_all, 0)
        data_all = {os.path.basename(r["repo"]): r for r in json.loads(stdout_all.getvalue())}
        self.assertEqual(data_all["upstream_pkg"]["owner_class"], "third-party")
        self.assertGreater(data_all["upstream_pkg"]["tracked_junk_count"], 0)
        self.assertIn("dist/", data_all["upstream_pkg"]["missing_patterns"])

        stdout_text = io.StringIO()
        with patch("sys.stdout", stdout_text):
            main(["gitignore", "audit", str(temp_dir), "--owner", "myorg", "--all"])
        self.assertIn("Files to untrack:", stdout_text.getvalue())

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_mcp_options_all_and_owner(self) -> None:
        temp_root = Path(tempfile.mkdtemp(prefix="sd_gi_mcp_")).resolve()
        self.addCleanup(shutil.rmtree, temp_root, onerror=_force_remove)

        own_repo = temp_root / "own_repo"
        own_repo.mkdir()
        git_cmd(own_repo, "init")
        git_cmd(own_repo, "remote", "add", "origin", "https://github.com/testuser/own_repo.git")
        (own_repo / "README.md").write_text("hi\n", encoding="utf-8")
        git_cmd(own_repo, "add", ".")
        git_cmd(own_repo, "commit", "-m", "init")

        tp_repo = temp_root / "vendor" / "tp_repo"
        tp_repo.mkdir(parents=True)
        git_cmd(tp_repo, "init")
        git_cmd(tp_repo, "remote", "add", "origin", "https://github.com/other/tp_repo.git")
        (tp_repo / "package.json").write_text("{}", encoding="utf-8")
        (tp_repo / "dist").mkdir()
        (tp_repo / "dist" / "bundle.js").write_text("// bundle\n", encoding="utf-8")
        git_cmd(tp_repo, "add", ".")
        git_cmd(tp_repo, "commit", "-m", "init")

        server = SmartDriveMCPServer(root=str(temp_root))
        # Also exercises drive-wide owner inference when owner is omitted
        res_default = server.dispatch_tool("ssd_gitignore", {"action": "audit", "all": False})
        by_name_def = {os.path.basename(r["repo"]): r for r in res_default["repos"]}
        self.assertEqual(by_name_def["tp_repo"]["tracked_junk_count"], 0)

        res_all = server.dispatch_tool("ssd_gitignore", {"action": "audit", "all": True, "owner": "testuser"})
        self.assertIn("summary", res_all)
        self.assertEqual(res_all["summary"]["own_repos"], 1)
        self.assertEqual(res_all["summary"]["third_party_repos"], 1)
        by_name_all = {os.path.basename(r["repo"]): r for r in res_all["repos"]}
        self.assertGreater(by_name_all["tp_repo"]["tracked_junk_count"], 0)


class TestGitignoreIntentionalFilesAndMissingRemotes(unittest.TestCase):
    """Verifies intentional build outputs, keep lists, and missing remote handling."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="sd_gi_intent_")).resolve()
        self.addCleanup(shutil.rmtree, self.temp_dir, onerror=_force_remove)
        clear_gh_cache()
        self.addCleanup(clear_gh_cache)

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_node_repo_intentional_bin_tracked_dist_preserved(self) -> None:
        """Node repo with bin ./dist/x.mjs tracked and dist/other.js untracked:
        x.mjs not junk, managed block has dist/* and !dist/x.mjs,
        git check-ignore confirms dist/x.mjs is not ignored and dist/other.js is;
        apply --untrack keeps dist/x.mjs tracked.
        """
        repo = self.temp_dir / "node_repo"
        repo.mkdir()
        git_cmd(repo, "init")

        pkg_json = {
            "name": "my-tool",
            "version": "1.0.0",
            "bin": {
                "my-tool": "./dist/x.mjs",
            },
        }
        (repo / "package.json").write_text(json.dumps(pkg_json, indent=2), encoding="utf-8")
        dist_dir = repo / "dist"
        dist_dir.mkdir()
        (dist_dir / "x.mjs").write_text("console.log('hello');\n", encoding="utf-8")
        git_cmd(repo, "add", "package.json", "dist/x.mjs")
        git_cmd(repo, "commit", "-m", "add bin bundle")

        # Create dist/other.js (untracked working file)
        (dist_dir / "other.js").write_text("console.log('temp');\n", encoding="utf-8")

        # Audit repo
        audit = audit_repo(str(repo))
        self.assertEqual(audit.tracked_junk_count, 0)
        self.assertIn("dist/x.mjs", audit.intentional_files)
        self.assertNotIn("dist/x.mjs", audit.tracked_junk_files)

        # Apply repo with --untrack
        res = apply_repo(str(repo), dry_run=False, untrack=True)
        self.assertTrue(res.applied)
        self.assertIn("dist/x.mjs", res.intentional_files)
        self.assertNotIn("dist/x.mjs", res.untracked_files)

        # Verify .gitignore content on disk
        gi_content = (repo / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("dist/*", gi_content)
        self.assertIn("!dist/x.mjs", gi_content)

        # git check-ignore confirms dist/x.mjs is not ignored and dist/other.js is
        res_x = subprocess.run(
            ["git", "check-ignore", "dist/x.mjs"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_x.returncode, 0, "dist/x.mjs should NOT be ignored by git")

        res_other = subprocess.run(
            ["git", "check-ignore", "-v", "dist/other.js"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_other.returncode, 0, "dist/other.js should be ignored by git")
        self.assertIn("dist/*", res_other.stdout)

        # apply --untrack keeps dist/x.mjs tracked
        tracked = git_cmd(repo, "ls-files")
        self.assertIn("dist/x.mjs", tracked)
        self.assertTrue((dist_dir / "x.mjs").exists())

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_dependency_package_json_never_makes_files_intentional(self) -> None:
        """Tracked node_modules packages reference their own dist/ files; they stay junk
        and node_modules/ stays in the managed block."""
        repo = self.temp_dir / "hub_repo"
        repo.mkdir()
        git_cmd(repo, "init")

        app = repo / "React"
        app.mkdir()
        (app / "package.json").write_text(json.dumps({"name": "app", "main": "src/index.js"}), encoding="utf-8")
        (app / "src").mkdir()
        (app / "src" / "index.js").write_text("export {};\n", encoding="utf-8")
        dep = app / "node_modules" / "axios"
        (dep / "dist").mkdir(parents=True)
        (dep / "package.json").write_text(
            json.dumps({"name": "axios", "main": "dist/axios.js", "exports": {".": "./dist/axios.js"}}),
            encoding="utf-8",
        )
        (dep / "dist" / "axios.js").write_text("module.exports = {};\n", encoding="utf-8")
        git_cmd(repo, "add", ".")
        git_cmd(repo, "commit", "-m", "commit node_modules by mistake")

        audit = audit_repo(str(repo))
        self.assertNotIn("React/node_modules/axios/dist/axios.js", audit.intentional_files)
        self.assertEqual(audit.tracked_junk_count, 2)
        self.assertNotIn("node_modules/", audit.omitted_patterns)
        self.assertIn("node_modules/", audit.missing_patterns)

        res = apply_repo(str(repo), dry_run=False, untrack=True)
        self.assertIn("React/node_modules/axios/dist/axios.js", res.untracked_files)
        tracked = git_cmd(repo, "ls-files")
        self.assertNotIn("node_modules", tracked)
        self.assertIn("React/src/index.js", tracked)

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_nested_intentional_file_omits_pattern(self) -> None:
        """A nested intentional file makes the directory pattern omitted."""
        repo = self.temp_dir / "nested_repo"
        repo.mkdir()
        git_cmd(repo, "init")

        pkg_json = {
            "name": "nested-tool",
            "main": "./dist/nested/sub/bundle.js",
        }
        (repo / "package.json").write_text(json.dumps(pkg_json, indent=2), encoding="utf-8")
        sub_dir = repo / "dist" / "nested" / "sub"
        sub_dir.mkdir(parents=True)
        (sub_dir / "bundle.js").write_text("export default {};\n", encoding="utf-8")
        git_cmd(repo, "add", ".")
        git_cmd(repo, "commit", "-m", "add nested bundle")

        audit = audit_repo(str(repo))
        self.assertIn("dist/nested/sub/bundle.js", audit.intentional_files)
        self.assertIn("dist/", audit.omitted_patterns)
        self.assertNotIn("dist/", audit.missing_patterns)
        self.assertIn("nested intentional file", audit.omitted_patterns["dist/"])

        res = apply_repo(str(repo), dry_run=False)
        gi_content = (repo / ".gitignore").read_text(encoding="utf-8")
        self.assertNotIn("dist/", gi_content)
        self.assertNotIn("dist/*", gi_content)
        self.assertIn("dist/", res.omitted_patterns)

        report = format_audit_report([audit], str(repo))
        self.assertIn("Omitted Recommended Patterns: dist/", report)
        self.assertIn("nested intentional file", report)

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_gitignore_negation_honoured(self) -> None:
        """.gitignore user negation re-includes tracked files as intentional."""
        repo = self.temp_dir / "neg_repo"
        repo.mkdir()
        git_cmd(repo, "init")

        (repo / ".gitignore").write_text("dist/\n!dist/custom.js\n", encoding="utf-8")
        dist_dir = repo / "dist"
        dist_dir.mkdir()
        (dist_dir / "custom.js").write_text("console.log('custom');\n", encoding="utf-8")
        (dist_dir / "junk.tmp").write_text("junk\n", encoding="utf-8")
        git_cmd(repo, "add", "-f", ".gitignore", "dist/custom.js", "dist/junk.tmp")
        git_cmd(repo, "commit", "-m", "commit custom")

        audit = audit_repo(str(repo))
        self.assertIn("dist/custom.js", audit.intentional_files)
        self.assertNotIn("dist/junk.tmp", audit.intentional_files)
        self.assertNotIn("dist/custom.js", audit.tracked_junk_files)

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_keep_list_git_config_and_cli_flag_honoured(self) -> None:
        """Keep list via git config smartdrive.keep and --keep CLI flag are honoured."""
        repo = self.temp_dir / "keep_repo"
        repo.mkdir()
        git_cmd(repo, "init")

        dist_dir = repo / "dist"
        dist_dir.mkdir()
        (dist_dir / "config_kept.js").write_text("var a = 1;\n", encoding="utf-8")
        (dist_dir / "flag_kept.js").write_text("var b = 2;\n", encoding="utf-8")
        git_cmd(repo, "add", ".")
        git_cmd(repo, "commit", "-m", "commit files")

        git_cmd(repo, "config", "--add", "smartdrive.keep", "dist/config_kept.js")

        audit = audit_repo(str(repo), keep_patterns=["dist/flag_kept.js"])
        self.assertIn("dist/config_kept.js", audit.intentional_files)
        self.assertIn("dist/flag_kept.js", audit.intentional_files)
        self.assertEqual(audit.tracked_junk_count, 0)

        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            ret = main(["gitignore", "audit", str(repo), "--keep", "dist/flag_kept.js", "--json"])
        self.assertEqual(ret, 0)
        data = json.loads(stdout.getvalue())
        self.assertIn("dist/config_kept.js", data[0]["intentional_files"])
        self.assertIn("dist/flag_kept.js", data[0]["intentional_files"])
        self.assertEqual(data[0]["tracked_junk_count"], 0)

    @unittest.skipIf(GIT is None, "git executable not found")
    def test_mocked_gh_missing_remote_visibility(self) -> None:
        """Mocked gh 'Could not resolve to a Repository' gives visibility missing and no public warning."""
        repo = self.temp_dir / "missing_remote_repo"
        repo.mkdir()
        git_cmd(repo, "init")
        git_cmd(repo, "remote", "add", "origin", "https://github.com/myorg/deleted-project.git")
        (repo / "README.md").write_text("# Test\n", encoding="utf-8")
        git_cmd(repo, "add", ".")
        git_cmd(repo, "commit", "-m", "init")

        def mock_run_gh(*args, **kwargs):
            if len(args) >= 2 and args[0] == "auth":
                return 0, "Logged in to github.com account myorg", ""
            if len(args) >= 2 and args[0] == "repo" and args[1] == "view":
                return 1, "", "GraphQL: Could not resolve to a Repository with the name 'myorg/deleted-project'. (repository)"
            return 0, "", ""

        with patch("smart_drive.core.gitignore._run_gh", side_effect=mock_run_gh), \
             patch("shutil.which", return_value="gh"):
            clear_gh_cache()
            audit = audit_repo(str(repo), configured_owner="myorg")
            self.assertEqual(audit.visibility, "missing")
            self.assertFalse(audit.is_possibly_public)
            self.assertFalse(audit.personal_data_at_risk)

            report = format_audit_report([audit], str(repo))
            self.assertIn("remote not found on GitHub", report)
            self.assertNotIn("with public remote!", report)
            self.assertNotIn("with possibly public remote!", report)
            self.assertNotIn("possibly public", report)
            self.assertIn("1 remote missing", report)

    def test_mcp_ssd_gitignore_keep_and_remote_missing(self) -> None:
        """MCP ssd_gitignore returns remote_missing in summary and intentional_files in repos."""
        temp_root = Path(tempfile.mkdtemp(prefix="sd_gi_mcp2_")).resolve()
        self.addCleanup(shutil.rmtree, temp_root, onerror=_force_remove)

        server = SmartDriveMCPServer(root=str(temp_root))
        res = server.dispatch_tool("ssd_gitignore", {"action": "audit", "keep": ["dist/*.mjs"]})
        self.assertIn("summary", res)
        self.assertIn("remote_missing", res["summary"])


if __name__ == "__main__":
    unittest.main()
