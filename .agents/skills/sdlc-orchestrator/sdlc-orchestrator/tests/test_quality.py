"""Quality checks must fail closed and preserve small test evidence."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from core import SDLCError  # noqa: E402
from quality import run_quality  # noqa: E402


class QualityTests(unittest.TestCase):
    def test_disabled_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual([], run_quality(Path(folder), {"quality": {"enabled": False}}))

    def test_success_records_actual_exit_status(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            config = {"quality": {"enabled": True, "commands": [
                [sys.executable, "-c", "print('verified')"],
            ]}}
            result = run_quality(root, config)
            self.assertEqual(0, result[0]["exit_code"])
            self.assertIn("verified", result[0]["output_tail"])

    def test_failing_check_blocks_completion(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            config = {"quality": {"enabled": True, "commands": [
                [sys.executable, "-c", "raise SystemExit(3)"],
            ]}}
            with self.assertRaisesRegex(SDLCError, "Quality gate failed"):
                run_quality(root, config)

    def test_invalid_command_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(SDLCError, "argv list"):
                run_quality(Path(folder), {"quality": {"enabled": True, "commands": ["ruff check"]}})
