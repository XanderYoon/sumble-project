"""Deterministic SDLC control plane: YAML validation, planning, and persistence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

PHASES = ("prd", "structure", "skeleton", "planning", "implementation")
TICKET_STATUS = ("todo", "in_progress", "done")
EPIC_STATUS = ("todo", "in_progress", "review", "reviewed", "mr_open", "merged")
DIFFICULTIES = ("low", "medium", "high")
ID_RE = re.compile(r"^[A-Z][0-9]{3,}$")


class SDLCError(Exception):
    """User-actionable validation or workflow failure."""


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def control(root: Path) -> Path:
    return root / ".sdlc"


def read_yaml(path: Path):
    if not path.is_file():
        raise SDLCError(f"Missing {path}. Run `init` from your project first.")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SDLCError(f"Bad YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SDLCError(f"Expected a YAML mapping in {path}")
    return value


def write_yaml(path: Path, value):
    """Atomic replacement: readers never see a half-written board/state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(value, handle, sort_keys=False, allow_unicode=True, width=100)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load(root):
    c = control(root)
    return read_yaml(c / "board.yml"), read_yaml(c / "state.yml"), read_yaml(c / "config.yml")


def require(cond, explanation):
    if not cond:
        raise SDLCError(explanation)


def check_list(value, name, required=False):
    require(isinstance(value, list), f"{name} must be a list")
    if required:
        require(bool(value), f"{name} cannot be empty")
    require(all(isinstance(x, str) and x.strip() for x in value), f"{name} entries must be strings")


def topo_check(nodes: dict, type_name: str):
    colors = {}

    def visit(node):
        if colors.get(node) == 1:
            raise SDLCError(f"{type_name} dependency cycle includes {node}")
        if colors.get(node) == 2:
            return
        colors[node] = 1
        for dependency in nodes[node]:
            require(dependency in nodes, f"{type_name} {node}: unknown dependency {dependency}")
            require(node != dependency, f"{type_name} {node}: cannot depend on itself")
            visit(dependency)
        colors[node] = 2

    for node in nodes:
        visit(node)


def validate_board(board, require_nonempty=False):
    require(board.get("version") == 1, "board.yml must have version: 1")
    require(isinstance(board.get("project"), str), "board.project must be a string")
    require(isinstance(board.get("epics"), list), "board.epics must be a list")
    if require_nonempty:
        require(board["epics"], "Create epics from the approved PRD before approving the board")
        require(board["project"] != "REPLACE_WITH_PROJECT_NAME", "Set a real project name")

    epic_deps, ids = {}, set()
    for epic in board["epics"]:
        require(isinstance(epic, dict), "Each epic must be a mapping")
        eid = epic.get("id")
        require(isinstance(eid, str) and ID_RE.fullmatch(eid), f"Bad epic ID: {eid!r}")
        require(eid not in epic_deps, f"Duplicate epic ID {eid}")
        require(isinstance(epic.get("title"), str) and epic["title"].strip(), f"{eid}: missing title")
        require(isinstance(epic.get("goal"), str) and epic["goal"].strip(), f"{eid}: missing goal")
        check_list(epic.get("acceptance"), f"{eid}.acceptance", required=True)
        check_list(epic.get("depends_on"), f"{eid}.depends_on")
        require(epic.get("status", "todo") in EPIC_STATUS, f"{eid}: invalid status")
        require(isinstance(epic.get("tickets"), list) and epic["tickets"], f"{eid}: needs tickets")
        epic_deps[eid] = epic["depends_on"]
        ticket_deps = {}
        for t in epic["tickets"]:
            require(isinstance(t, dict), f"{eid}: tickets must be mappings")
            tid = t.get("id")
            require(isinstance(tid, str) and ID_RE.fullmatch(tid), f"{eid}: bad ticket ID {tid!r}")
            require(tid not in ids, f"Duplicate ticket ID {tid}")
            ids.add(tid)
            for key in ("title", "description"):
                require(isinstance(t.get(key), str) and t[key].strip(), f"{tid}: missing {key}")
            require(t.get("difficulty") in DIFFICULTIES, f"{tid}: difficulty low|medium|high required")
            require(t.get("status", "todo") in TICKET_STATUS, f"{tid}: invalid status")
            for key in ("depends_on", "files", "prd_sections", "architecture_sections", "acceptance", "tests"):
                check_list(t.get(key), f"{tid}.{key}", required=key in ("acceptance", "tests"))
            ticket_deps[tid] = t["depends_on"]
        topo_check(ticket_deps, "ticket")  # Deliberately disallow cross-epic ticket dependencies.
    topo_check(epic_deps, "epic")
    return True


RUNTIME_TICKET = {"status", "commit", "start_head", "started_at", "completed_at", "agent_run", "check_evidence", "assigned_model", "assigned_provider", "quality_evidence"}
RUNTIME_EPIC = {"status", "branch", "worktree", "reviewed_head", "reviewer", "reviewed_at", "mr_url", "merged_at", "review_quality_evidence"}


def plan_digest(board):
    """Hash requirements/dependencies; ignore mutable execution metadata."""
    cleaned = {"version": board.get("version"), "project": board.get("project"), "epics": []}
    for epic in board.get("epics", []):
        e = {k: v for k, v in epic.items() if k not in RUNTIME_EPIC | {"tickets"}}
        e["tickets"] = [{k: v for k, v in t.items() if k not in RUNTIME_TICKET} for t in epic["tickets"]]
        cleaned["epics"].append(e)
    return hashlib.sha256(json.dumps(cleaned, sort_keys=True).encode()).hexdigest()


def quality_digest(config):
    """Hash approved quality enforcement settings, ignoring model/CLI adapters."""
    return hashlib.sha256(json.dumps(config.get("quality", {}), sort_keys=True).encode()).hexdigest()


def sha_file(path):
    require(path.is_file(), f"Required document absent: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def skeleton_files(root):
    manifest = read_yaml(control(root) / "skeleton.yml")
    require(manifest.get("version") == 1, "skeleton.yml version must be 1")
    require(isinstance(manifest.get("files"), list), "skeleton.files must be a list")
    paths = set()
    for entry in manifest["files"]:
        require(isinstance(entry, dict), "skeleton file entries must be mappings")
        p = entry.get("path")
        require(isinstance(p, str) and p.strip(), "Each skeleton file needs a path")
        path = safe_project_path(root, p)
        require(path not in paths, f"Duplicate skeleton path {p}")
        paths.add(path)
        require(isinstance(entry.get("content", ""), str), f"{p}.content must be a string")
    return manifest["files"]


def safe_project_path(root, rel):
    require(not Path(rel).is_absolute(), f"Absolute path prohibited: {rel}")
    path = (root / rel).resolve()
    require(path != root.resolve() and root.resolve() in path.parents, f"Path escapes project: {rel}")
    require(".git" not in Path(rel).parts and ".sdlc" not in Path(rel).parts, f"Reserved path: {rel}")
    return path


def check_approvals(root, board, state):
    documents = {"prd": "PRD.md", "structure": "ARCHITECTURE.md", "skeleton": "skeleton.yml"}
    for key, filename in documents.items():
        approval = state.get("approvals", {}).get(key)
        if approval is not None:
            require(isinstance(approval, dict) and approval.get("sha256") == sha_file(control(root) / filename),
                    f"Approved {filename} changed. Stop and get explicit reapproval before continuing.")
    approval = state.get("approvals", {}).get("board")
    if approval is not None:
        config = read_yaml(control(root) / "config.yml")
        if "quality_sha256" in approval:
            require(approval["quality_sha256"] == quality_digest(config),
                    "Approved Python quality gates changed. Reapproval is required.")
        if "style_sha256" in approval:
            require(approval["style_sha256"] == sha_file(control(root) / "PYTHON_STYLE.md"),
                    "Approved Python style policy changed. Reapproval is required.")
        require(isinstance(approval, dict) and approval.get("sha256") == plan_digest(board),
                "board.yml plan changed after approval. Stop and request an updated plan approval.")
    phase = state.get("phase")
    require(phase in PHASES, f"Invalid phase {phase!r}")
    prerequisites = {"structure": ("prd",), "skeleton": ("prd", "structure"),
                     "planning": ("prd", "structure", "skeleton"),
                     "implementation": ("prd", "structure", "skeleton", "board")}
    for key in prerequisites.get(phase, ()):
        require(state.get("approvals", {}).get(key), f"Phase {phase} requires approved {key}")


def find_epic(board, eid):
    for epic in board["epics"]:
        if epic["id"] == eid:
            return epic
    raise SDLCError(f"Unknown epic {eid}")


def find_ticket(board, tid):
    for epic in board["epics"]:
        for ticket in epic["tickets"]:
            if ticket["id"] == tid:
                return epic, ticket
    raise SDLCError(f"Unknown ticket {tid}")


def ready(board):
    """At most one ticket per epic; independent epics can execute concurrently."""
    result = []
    by_id = {e["id"]: e for e in board["epics"]}
    for epic in board["epics"]:
        if epic.get("status", "todo") not in ("todo", "in_progress"):
            continue
        if any(by_id[d].get("status") != "merged" for d in epic["depends_on"]):
            continue
        if any(t.get("status") == "in_progress" for t in epic["tickets"]):
            continue
        local = {t["id"]: t for t in epic["tickets"]}
        for ticket in epic["tickets"]:
            if ticket.get("status", "todo") == "todo" and all(
                local[d].get("status") == "done" for d in ticket["depends_on"]
            ):
                result.append({"epic": epic["id"], "ticket": ticket["id"],
                               "difficulty": ticket["difficulty"]})
                break
    return result


def model_for(config, difficulty):
    route = config.get("routing", {}).get(difficulty)
    require(isinstance(route, dict) and route.get("provider"), f"No provider configured for {difficulty}")
    return route


def approval_stamp(digest):
    return {"sha256": digest, "approved_at": utcnow()}


@contextmanager
def project_lock(root):
    """Serialize control-plane writes even when independent epics run concurrently.

    OS-held advisory lock is released automatically if the command crashes.
    """
    path = control(root) / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
