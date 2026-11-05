"""Make interrupted-ticket context portable without reopening full chat history."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from context import ticket_brief
from core import SDLCError, control, find_ticket, require
from gitops import epic_path, git


def process_hint(pid: object) -> str:
    """Return a conservative process hint, never permission to launch again."""
    if not isinstance(pid, int) or pid <= 0 or os.name == "nt":
        return "unknown (verify manually)"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "PID not present (check for detached child processes)"
    except PermissionError:
        return "PID exists but ownership is unknown"
    return "PID exists; could be live or reused (inspect before retrying)"


def log_tail(path: object, limit: int = 800) -> str:
    """Read at most the final `limit` bytes of a trusted local run log."""
    if not isinstance(path, str):
        return "No runner log recorded"
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(size - limit, 0))
            return stream.read(limit).decode("utf-8", errors="replace").strip()
    except OSError:
        return "Runner log not found on this machine"


def recovery_brief(
    root: Path, board: dict[str, Any], config: dict[str, Any],
    tid: str, max_chars: int = 10000,
) -> tuple[Path, str]:
    """Build a bounded ticket brief with current Git state and last agent run."""
    require(max_chars >= 3500, "Recovery brief budget must be at least 3500 chars")
    epic, ticket = find_ticket(board, tid)
    require(ticket.get("status") == "in_progress", f"{tid} is not currently in progress")
    path = epic_path(epic)
    start = ticket.get("start_head")
    require(isinstance(start, str), "Missing ticket start HEAD; inspect board manually")
    current = git(path, "rev-parse", "HEAD")
    status = git(path, "status", "--short")
    diff = git(path, "diff", "--stat")
    commits = git(path, "log", "--format=%h %s", "--max-count=8", f"{start}..HEAD")
    previous_run = ticket.get("agent_run") or {}
    if not isinstance(previous_run, dict):
        previous_run = {}
    recovery = (
        f"# Resume ticket {tid}\n\n"
        f"Worktree: `{path}`\n"
        f"Start commit: `{start}`\nCurrent HEAD: `{current}`\n"
        f"Last runner PID: `{previous_run.get('pid', 'n/a')}` — "
        f"{process_hint(previous_run.get('pid'))}\n\n"
        "## Current uncommitted paths\n"
        f"```text\n{(status or '(clean)')[:600]}\n```\n\n"
        "## Uncommitted diffstat\n"
        f"```text\n{(diff or '(none)')[:600]}\n```\n\n"
        "## Commits since ticket start\n"
        f"```text\n{(commits or '(none)')[:500]}\n```\n\n"
        "## Prior runner log tail (non-authoritative)\n"
        f"```text\n{log_tail(previous_run.get('log'))[:800]}\n```\n\n"
        "**Recovery rule:** Inspect the worktree and check the prior agent has exited. "
        "Resume existing changes rather than restarting from scratch. "
        "Never launch a second agent against a potentially live first agent.\n\n"
    )
    remaining = max_chars - len(recovery) - 30
    require(remaining >= 1000, "Recovery context too large; increase --max-chars")
    base = ticket_brief(root, board, config, tid, max_chars=remaining)
    packet = recovery + "---\n\n" + base
    require(len(packet) <= max_chars, "Recovery packet exceeded size budget")
    target = control(root) / "handoffs" / f"recover-{tid}.md"
    target.parent.mkdir(exist_ok=True)
    target.write_text(packet, encoding="utf-8")
    return target, packet
