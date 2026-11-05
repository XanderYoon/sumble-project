"""Safe, explicit git operations for one worktree per epic."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from core import SDLCError, require, skeleton_files


def git(cwd: Path, *args, check=True):
    cmd = ["git", "-C", str(cwd), *map(str, args)]
    result = subprocess.run(cmd, text=True, capture_output=True)
    if check and result.returncode:
        raise SDLCError(f"git {' '.join(map(str, args))}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip() if check else result


def validate_repo(root):
    require(git(root, "rev-parse", "--show-toplevel") == str(root.resolve()),
            f"{root} must be the primary git checkout root (not a nested folder/worktree)")


def branch_name(epic):
    name = re.sub(r"[^a-z0-9]+", "-", epic["title"].lower()).strip("-")[:35]
    return f"epic/{epic['id'].lower()}-{name or 'work'}"


def ensure_worktree(root, config, epic):
    validate_repo(root)
    base = config.get("base_branch", "main")
    git(root, "rev-parse", "--verify", base)
    for entry in skeleton_files(root):
        require(git(root, "cat-file", "-e", f"{base}:{entry['path']}", check=False).returncode == 0,
                f"Skeleton file {entry['path']} is not committed on {base}. "
                "Commit the approved skeleton to the base branch before starting tickets.")
    location = config.get("worktree_dir", ".worktrees")
    require(isinstance(location, str), "worktree_dir must be a path")
    path = (root / location / epic["id"]).resolve()
    require(root.resolve() in path.parents, "Worktree directory must be inside project root")
    require(".git" not in path.parts[len(root.parts):], "Do not put worktrees in .git")
    branch = epic.get("branch") or branch_name(epic)
    if path.exists():
        require((path / ".git").exists(), f"Path occupied but not a git worktree: {path}")
        current = git(path, "branch", "--show-current")
        require(current == branch, f"Worktree {path} on {current}, expected {branch}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = git(root, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0
        if exists:
            git(root, "worktree", "add", str(path), branch)
        else:
            git(root, "worktree", "add", "-b", branch, str(path), base)
    epic["branch"], epic["worktree"] = branch, str(path)
    return path


def epic_path(epic):
    path = epic.get("worktree")
    require(isinstance(path, str) and Path(path).is_dir(),
            f"No epic worktree for {epic['id']}; start a ticket first")
    return Path(path)


def head(epic):
    return git(epic_path(epic), "rev-parse", "HEAD")


def verify_ticket_commit(epic, ticket):
    path = epic_path(epic)
    require(not git(path, "status", "--porcelain"), "Epic worktree has uncommitted changes; commit first")
    current = git(path, "rev-parse", "HEAD")
    start = ticket.get("start_head")
    require(start, "Missing ticket start commit; use start first")
    require(current != start, "No new commit since ticket start")
    require(git(path, "merge-base", "--is-ancestor", start, current, check=False).returncode == 0,
            "History was rewritten since ticket start; inspect manually")
    return current


def verify_merged(root, config, epic):
    base = config.get("base_branch", "main")
    git(root, "rev-parse", "--verify", base)
    return git(root, "merge-base", "--is-ancestor", epic["reviewed_head"], base,
               check=False).returncode == 0
