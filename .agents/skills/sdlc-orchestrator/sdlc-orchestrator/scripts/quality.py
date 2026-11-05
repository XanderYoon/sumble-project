"""Execute configured Python quality gates with bounded, actionable diagnostics."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from core import SDLCError, require


def quality_commands(config: dict[str, Any]) -> list[list[str]]:
    """Return validated argv commands, or an empty list when disabled."""
    settings = config.get("quality", {})
    require(isinstance(settings, dict), "config.quality must be a mapping")
    enabled = settings.get("enabled", False)
    require(isinstance(enabled, bool), "config.quality.enabled must be a boolean")
    if not enabled:
        return []
    commands = settings.get("commands")
    require(isinstance(commands, list) and commands, "Enabled quality needs commands")
    for command in commands:
        require(isinstance(command, list) and bool(command), "Each quality command must be an argv list")
        require(all(isinstance(part, str) and part for part in command), "Quality argv must contain strings")
    return commands


def run_quality(path: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Run checks against a worktree; raise on failure, return compact evidence.

    Args:
        path: Directory with the approved Python project configuration.
        config: `.sdlc/config.yml` mapping containing `quality` settings.

    Returns:
        Command, exit status, and bounded output of each successful check.

    Raises:
        SDLCError: A checker is missing or reports failure.
    """
    commands = quality_commands(config)
    if not commands:
        return []
    require((path / "pyproject.toml").is_file(),
            "Python quality is enabled but pyproject.toml is missing in the epic worktree")
    evidence: list[dict[str, Any]] = []
    for argv in commands:
        try:
            result = subprocess.run(argv, cwd=path, capture_output=True, text=True, timeout=180)
        except FileNotFoundError as exc:
            raise SDLCError(f"Required quality checker not installed: {argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SDLCError(f"Quality command timed out after 180s: {' '.join(argv)}") from exc
        combined = (result.stdout + "\n" + result.stderr).strip()
        evidence.append({"command": argv, "exit_code": result.returncode,
                         "output_tail": combined[-600:]})
        if result.returncode:
            raise SDLCError(f"Quality gate failed: {' '.join(argv)} (exit {result.returncode})\n"
                            f"{combined[-2200:]}")
    return evidence
