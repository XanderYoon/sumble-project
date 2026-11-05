"""End-to-end smoke tests with real local git worktrees (no network or model calls)."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / "scripts" / "sdlc.py"


def save_yaml(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test Author")
        self.cli("init")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.root), *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def cli(self, *args, succeed=True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--project", str(self.root), *args],
                                capture_output=True, text=True)
        if succeed and result.returncode:
            self.fail(f"CLI failed: {args}\nSTDOUT={result.stdout}\nSTDERR={result.stderr}")
        return result

    def setup_plan(self, example=False, quality_commands=None):
        control = self.root / ".sdlc"
        prd = control / "PRD.md"
        s = prd.read_text().replace("Status: DRAFT", "Status: FINAL")
        s += "\n" + ("Detailed user-facing behavior, error cases, and acceptance conditions.\n" * 12)
        prd.write_text(s)
        self.cli("approve", "prd")
        architecture = control / "ARCHITECTURE.md"
        s = architecture.read_text().replace("Status: PROPOSED", "Status: APPROVED")
        s += "\n" + ("API design, module behavior and tradeoffs.\n" * 12)
        architecture.write_text(s)
        self.cli("approve", "structure")
        manifest = {"version": 1, "files": [
            {"path": "src/storage.py", "content": '"""Storage API."""\n'},
            {"path": "tests/test_storage.py", "content": '"""Tests."""\n'},
            {"path": "src/cli.py", "content": '"""CLI API."""\n'},
            {"path": "tests/test_cli.py", "content": '"""CLI tests."""\n'},
        ]}
        if quality_commands is not None:
            manifest["files"].append({"path": "pyproject.toml", "content": "[tool.ruff]\n"})
        save_yaml(control / "skeleton.yml", manifest)
        self.cli("scaffold")
        self.assertIn("skipped_existing", self.cli("scaffold").stdout)
        self.cli("approve", "skeleton")
        if example:
            config = yaml.safe_load((control / "config.yml").read_text())
            for tier in ("low", "medium", "high"):
                config["routing"][tier]["model"] = f"test-{tier}-model"
            config["runners"]["openai"] = ["/bin/echo", "--model", "{model}", "{prompt_file}"]
            if quality_commands is not None:
                config["quality"] = {"enabled": True, "commands": quality_commands}
            save_yaml(control / "config.yml", config)
            shutil.copy2(BASE / "assets" / "board.example.yml", control / "board.yml")
        self.cli("approve", "board") if example else None

    def test_initial_phase_and_approval(self):
        self.assertIn('"phase": "prd"', self.cli("resume").stdout)
        self.assertNotEqual(0, self.cli("approve", "prd", succeed=False).returncode)
        self.assertIn("Valid:", self.cli("validate").stdout)

    def test_end_to_end_two_epics_and_resume(self):
        self.setup_plan(example=True)
        self.git("add", ".")
        self.git("commit", "-m", "Approved plan and code skeleton")
        next_ = self.cli("next").stdout
        self.assertIn("T001", next_)
        self.assertNotIn("T003", next_)  # Epic dependency.
        self.assertNotEqual(0, self.cli("start", "T002", succeed=False).returncode)
        self.cli("start", "T001")
        self.assertIn("test-low-model", self.cli("brief", "T001").stdout)
        self.assertIn("/bin/echo", self.cli("launch", "T001", "--dry-run").stdout)
        self.assertIn("T001", self.cli("resume").stdout)
        recovered = self.cli("recover", "T001")
        self.assertIn("Resume ticket T001", recovered.stdout)
        self.assertTrue((self.root / ".sdlc/handoffs/recover-T001.md").exists())
        self.assertNotIn('"ticket": "T002"', self.cli("next").stdout)
        wt = self.root / ".worktrees" / "E001"
        (wt / "src/storage.py").write_text('"""Storage interface."""\nclass Storage: pass\n')
        self.git("add", "src/storage.py", cwd=wt)
        self.git("commit", "-m", "T001: define storage interface", cwd=wt)
        self.cli("finish", "T001", "--checks", "python -m unittest: PASS")
        self.assertIn("T002", self.cli("next").stdout)
        self.cli("decision", "T002", "--choice", "Atomic rename", "--why", "Crash safety")
        self.cli("start", "T002")
        (wt / "src/storage.py").write_text('"""Storage interface."""\nclass Storage: pass\n# durable writes\n')
        self.git("add", "src/storage.py", cwd=wt)
        self.git("commit", "-m", "T002: durable persistence", cwd=wt)
        self.cli("finish", "T002", "--checks", "python -m unittest: PASS")
        review_request = self.cli("review", "E001", "--reviewer", "GPT Sol maximum thinking")
        self.assertIn("Review packet", review_request.stdout)
        h = self.git("rev-parse", "HEAD", cwd=wt)
        report = self.root / "report.md"
        report.write_text(f"# E001 review\n\nReviewed HEAD: {h}\n\nVerdict: PASS\n\nNo remaining critical issues.\n")
        self.cli("review-pass", "E001", "--reviewer", "GPT Sol maximum thinking", "--report", str(report))
        self.assertIn("mr_body", self.cli("mr", "E001", "--forge", "github").stdout)
        self.cli("mr-created", "E001", "--url", "https://github.com/example/repo/pull/123")
        self.git("merge", "--no-ff", "epic/e001-local-storage", "-m", "Merge E001")
        self.cli("mark-merged", "E001")
        self.assertIn("T003", self.cli("next").stdout)

    def test_reapproval_required_after_prd_change(self):
        self.setup_plan(example=True)
        with (self.root / ".sdlc/PRD.md").open("a") as stream:
            stream.write("\nNew behavior\n")
        result = self.cli("resume", succeed=False)
        self.assertIn("changed", result.stderr)

    def test_reopen_before_implementation(self):
        self.setup_plan(example=True)
        self.cli("reopen", "prd")
        self.assertIn('"phase": "prd"', self.cli("resume").stdout)
        self.assertNotEqual(0, self.cli("next", succeed=False).returncode)

    def test_real_quality_failure_blocks_finish_and_can_retry(self):
        checker = [sys.executable, "-c", "from pathlib import Path; "
                   "assert 'READY' in Path('src/storage.py').read_text()"]
        self.setup_plan(example=True, quality_commands=[checker])
        self.git("add", ".")
        self.git("commit", "-m", "Approved Python skeleton and quality policy")
        self.cli("start", "T001")
        worktree = self.root / ".worktrees/E001"
        path = worktree / "src/storage.py"
        path.write_text('"""Broken implementation."""\n')
        self.git("add", "src/storage.py", cwd=worktree)
        self.git("commit", "-m", "T001 initial attempt", cwd=worktree)
        failure = self.cli("finish", "T001", "--checks", "agent-reported PASS", succeed=False)
        self.assertIn("Quality gate failed", failure.stderr)
        board = yaml.safe_load((self.root / ".sdlc/board.yml").read_text())
        self.assertEqual("in_progress", board["epics"][0]["tickets"][0]["status"])
        path.write_text('"""READY: corrected implementation."""\n')
        self.git("add", "src/storage.py", cwd=worktree)
        self.git("commit", "-m", "T001 fix quality gate", cwd=worktree)
        self.cli("finish", "T001", "--checks", "actual command passed")
        board = yaml.safe_load((self.root / ".sdlc/board.yml").read_text())
        ticket = board["epics"][0]["tickets"][0]
        self.assertEqual("done", ticket["status"])
        self.assertEqual(0, ticket["quality_evidence"][0]["exit_code"])

    def test_approved_quality_policy_cannot_be_silently_disabled(self):
        self.setup_plan(example=True)
        config_path = self.root / ".sdlc/config.yml"
        config = yaml.safe_load(config_path.read_text())
        config["quality"]["enabled"] = True
        save_yaml(config_path, config)
        failure = self.cli("resume", succeed=False)
        self.assertIn("quality gates changed", failure.stderr)

    def test_cycle_and_invalid_paths(self):
        self.setup_plan()
        board = yaml.safe_load((BASE / "assets" / "board.example.yml").read_text())
        board["epics"][0]["tickets"][0]["depends_on"] = ["T002"]
        save_yaml(self.root / ".sdlc/board.yml", board)
        self.assertIn("cycle", self.cli("validate", succeed=False).stderr)
        sys.path.insert(0, str(BASE / "scripts"))
        from core import safe_project_path, SDLCError
        with self.assertRaisesRegex(SDLCError, "escapes project"):
            safe_project_path(self.root, "../outside")


if __name__ == "__main__":
    unittest.main()
