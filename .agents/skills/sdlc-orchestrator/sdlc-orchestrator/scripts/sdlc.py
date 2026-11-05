#!/usr/bin/env python3
"""Minimal SDLC orchestrator CLI. Run `python scripts/sdlc.py --help`."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

from context import reviewer_brief, section, ticket_brief
from core import (
    SDLCError,
    approval_stamp,
    check_approvals,
    control,
    find_epic,
    find_ticket,
    load,
    model_for,
    plan_digest,
    project_lock,
    quality_digest,
    read_yaml,
    ready,
    require,
    safe_project_path,
    sha_file,
    skeleton_files,
    utcnow,
    validate_board,
    write_yaml,
)
from gitops import ensure_worktree, epic_path, git, head, verify_merged, verify_ticket_commit
from quality import run_quality
from recovery import recovery_brief

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def output(obj, as_json=False):
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj)


def save_board(root, board):
    write_yaml(control(root) / "board.yml", board)


def save_state(root, state):
    write_yaml(control(root) / "state.yml", state)


def checked(root, plan=False):
    board, state, config = load(root)
    validate_board(board, require_nonempty=plan)
    check_approvals(root, board, state)
    return board, state, config


def must_implement(state):
    require(state["phase"] == "implementation", "Approve PRD, structure, skeleton, and board first")


def init(root):
    target = control(root)
    require(not target.exists(), f"Already initialized: {target}")
    target.mkdir(parents=True)
    for name in ("board.yml", "state.yml", "config.yml", "skeleton.yml", "PRD.md",
                 "ARCHITECTURE.md", "DECISIONS.md"):
        shutil.copy2(ASSETS / name, target / name)
    # Keep the Python agent policy available inside committed epic worktrees.
    shutil.copy2(ASSETS.parent / "references" / "PYTHON_STYLE.md", target / "PYTHON_STYLE.md")
    (target / "reviews").mkdir()
    (target / "reviews" / ".gitkeep").touch()
    (target / "handoffs").mkdir()
    (target / "runs").mkdir()
    ignore = root / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    exclusions = (
        ".worktrees/", ".sdlc/handoffs/", ".sdlc/runs/", ".sdlc/.lock",
        "__pycache__/", ".pytest_cache/", ".ruff_cache/", ".venv/",
    )
    with ignore.open("a", encoding="utf-8") as stream:
        for line in exclusions:
            if line not in existing.splitlines():
                stream.write(("\n" if existing and not existing.endswith("\n") else "") + line + "\n")
                existing += line + "\n"
    print(f"Initialized {target}. Start by drafting .sdlc/PRD.md; do not approve on the agent's own authority.")


def approve(root, stage):
    board, state, config = checked(root)
    stages = {
        "prd": ("prd", "structure", "PRD.md"),
        "structure": ("structure", "skeleton", "ARCHITECTURE.md"),
        "skeleton": ("skeleton", "planning", "skeleton.yml"),
        "board": ("planning", "implementation", None),
    }
    expected, next_phase, doc = stages[stage]
    require(state["phase"] == expected, f"Cannot approve {stage} during {state['phase']} phase")
    if stage == "prd":
        content = (control(root) / doc).read_text(encoding="utf-8")
        require("Status: DRAFT" not in content, "PRD still says DRAFT; finalize it first")
        require(len(content.strip()) > 500, "PRD is too short; specify behaviors and acceptance scenarios")
    if stage == "structure":
        content = (control(root) / doc).read_text(encoding="utf-8")
        require("Status: PROPOSED" not in content, "Architecture still says PROPOSED; finalize first")
        require(len(content.strip()) > 300, "Architecture needs a concrete repo tree and interfaces")
    if stage == "skeleton":
        entries = skeleton_files(root)
        require(entries, "Fill .sdlc/skeleton.yml with the approved file manifest first")
        for entry in entries:
            path = safe_project_path(root, entry["path"])
            require(path.is_file(), f"Skeleton file missing: {path}; run `scaffold` first")
    if stage == "board":
        validate_board(board, require_nonempty=True)
        for difficulty in {t["difficulty"] for e in board["epics"] for t in e["tickets"]}:
            route = model_for(config, difficulty)
            require(isinstance(route.get("model"), str) and route["model"].strip(),
                    f"Configure a real model ID for routing.{difficulty}.model before approving the board")
        require(all(e.get("status", "todo") == "todo" and
                    all(t.get("status", "todo") == "todo" for t in e["tickets"])
                    for e in board["epics"]), "Board approval requires all work in todo status")
        # Catch stale section references before dispatching any agents.
        prd = (control(root) / "PRD.md").read_text(encoding="utf-8")
        architecture = (control(root) / "ARCHITECTURE.md").read_text(encoding="utf-8")
        for e in board["epics"]:
            for t in e["tickets"]:
                for title in t["prd_sections"]:
                    section(prd, title)
                for title in t["architecture_sections"]:
                    section(architecture, title)
    digest = plan_digest(board) if stage == "board" else sha_file(control(root) / doc)
    stamp = approval_stamp(digest)
    if stage == "board":
        stamp["quality_sha256"] = quality_digest(config)
        stamp["style_sha256"] = sha_file(control(root) / "PYTHON_STYLE.md")
    state["approvals"][stage] = stamp
    state["phase"] = next_phase
    save_state(root, state)
    print(f"Approved {stage}. Next phase: {next_phase}")


def reopen(root, stage):
    """Rewind approval phases only before code execution has begun."""
    board, state, _ = load(root)  # Approved content may already be stale.
    validate_board(board)
    require(stage in ("prd", "structure", "skeleton", "board"), "Unknown phase gate")
    require(not any(t.get("status", "todo") != "todo" or t.get("commit")
                    for e in board["epics"] for t in e["tickets"]),
            "Implementation has started. Freeze work and handle requirement changes explicitly; "
            "reopen cannot discard existing ticket history.")
    phases = {"prd": "prd", "structure": "structure", "skeleton": "skeleton", "board": "planning"}
    order = ("prd", "structure", "skeleton", "board")
    require(state.get("approvals", {}).get(stage) is not None,
            f"{stage} has no recorded approval to reopen")
    for later in order[order.index(stage):]:
        state["approvals"][later] = None
    state["phase"] = phases[stage]
    save_state(root, state)
    print(f"Reopened {stage}. Downstream approvals invalidated; repeat human review.")


def scaffold(root):
    board, state, _ = checked(root)
    require(state["phase"] == "skeleton", "Scaffold only after approving the structure")
    entries = skeleton_files(root)
    require(entries, "Define files in .sdlc/skeleton.yml before scaffold")
    created, skipped = [], []
    for entry in entries:
        path = safe_project_path(root, entry["path"])
        if path.exists():
            require(path.is_file(), f"Skeleton target is not a file: {path}")
            skipped.append(entry["path"])
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(entry.get("content", ""))
        created.append(entry["path"])
    output({"created": created, "skipped_existing": skipped}, as_json=True)


def report(root, concise=False):
    board, state, config = checked(root)
    phase = state["phase"]
    counts = {"todo": 0, "in_progress": 0, "done": 0}
    active, reviews, mr = [], [], []
    for e in board["epics"]:
        for t in e["tickets"]:
            counts[t.get("status", "todo")] += 1
            if t.get("status") == "in_progress":
                active.append({"epic": e["id"], "ticket": t["id"], "worktree": e.get("worktree"),
                               "last_run": t.get("agent_run")})
        if e.get("status") in ("review", "reviewed"):
            reviews.append({"epic": e["id"], "status": e["status"]})
        if e.get("status") == "mr_open":
            mr.append({"epic": e["id"], "url": e.get("mr_url")})
    hints = {"prd": "Discuss and finalize .sdlc/PRD.md, then explicitly approve prd",
             "structure": "Propose and finalize .sdlc/ARCHITECTURE.md, then approve structure",
             "skeleton": "Populate .sdlc/skeleton.yml, scaffold, inspect, then approve skeleton",
             "planning": "Convert the PRD into .sdlc/board.yml, validate, then approve board",
             "implementation": "Run ready tickets, complete reviews, open and merge per-epic MRs"}
    result = {"phase": phase, "next_step": hints[phase], "tickets": counts,
              "active": active, "ready": ready(board) if phase == "implementation" else [],
              "awaiting_review": reviews, "open_mrs": mr,
              "epics": [{"id": e["id"], "status": e.get("status", "todo")}
                        for e in board["epics"]]}
    if not concise:
        result["model_routes"] = config.get("routing", {})
        result["approvals"] = state.get("approvals", {})
    return result


def brief(root, tid, max_chars):
    board, state, config = checked(root, plan=True)
    must_implement(state)
    text = ticket_brief(root, board, config, tid, max_chars=max_chars)
    dest = control(root) / "handoffs" / f"{tid}.md"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest, text


def start(root, tid):
    board, state, config = checked(root, plan=True)
    must_implement(state)
    epic, ticket = find_ticket(board, tid)
    require(ticket.get("status", "todo") == "todo", f"{tid} not todo")
    require(tid in [x["ticket"] for x in ready(board)],
            f"{tid} blocked by dependencies or another active ticket in its epic. See `next`.")
    route = model_for(config, ticket["difficulty"])
    require(isinstance(route.get("model"), str) and route["model"].strip(),
            f"No model configured for {ticket['difficulty']}")
    path = ensure_worktree(root, config, epic)
    ticket["status"] = "in_progress"
    ticket["assigned_provider"], ticket["assigned_model"] = route["provider"], route["model"]
    ticket["start_head"] = git(path, "rev-parse", "HEAD")
    ticket["started_at"] = utcnow()
    epic["status"] = "in_progress"
    save_board(root, board)
    dest, _ = brief(root, tid, 10000)
    print(f"Started {tid} in {path}\nAgent handoff: {dest}\nModel tier: {ticket['difficulty']}")


def finish(root, tid, checks):
    board, state, config = checked(root, plan=True)
    must_implement(state)
    epic, ticket = find_ticket(board, tid)
    require(ticket.get("status") == "in_progress", f"{tid} must be in progress")
    require(isinstance(checks, str) and checks.strip(),
            "Provide factual test evidence with --checks (test command + result)")
    commit = verify_ticket_commit(epic, ticket)
    # The orchestrator, not the subagent's self-report, executes the configured gates.
    quality_evidence = run_quality(epic_path(epic), config)
    require(not git(epic_path(epic), "status", "--porcelain") and head(epic) == commit,
            "Quality checks changed the code/HEAD; commit changes and repeat checks")
    for e in board["epics"]:
        for t in e["tickets"]:
            require(t.get("commit") != commit, f"Commit already recorded on {t['id']}")
    ticket["status"], ticket["commit"], ticket["completed_at"] = "done", commit, utcnow()
    ticket["check_evidence"] = checks
    if quality_evidence:
        ticket["quality_evidence"] = quality_evidence
    # check_evidence is execution metadata, omitted from plan hash in core.py.
    save_board(root, board)
    print(f"Completed {tid} at {commit[:12]}. Run `next` or start epic review.")


def log_decision(root, reference, decision, rationale):
    board, state, _ = checked(root)
    if reference.startswith("E"):
        find_epic(board, reference)
    else:
        find_ticket(board, reference)
    require(decision.strip() and rationale.strip(), "Decision and rationale required")
    with (control(root) / "DECISIONS.md").open("a", encoding="utf-8") as stream:
        stream.write(f"\n## {utcnow()} [{reference}]\n- Decision: {decision.strip()}\n"
                     f"- Rationale: {rationale.strip()}\n")
    print(f"Recorded decision for {reference}")


def review(root, eid, reviewer):
    board, state, config = checked(root, plan=True)
    must_implement(state)
    epic = find_epic(board, eid)
    require(epic.get("status") in ("in_progress", "review", "reviewed"), f"{eid}: not ready for review")
    require(all(t.get("status") == "done" for t in epic["tickets"]), "Finish all epic tickets first")
    path = epic_path(epic)
    require(not git(path, "status", "--porcelain"), "Commit unfinished work before review")
    h = head(epic)
    request = reviewer_brief(board, epic, config.get("base_branch", "main"), h, reviewer)
    target = control(root) / "handoffs" / f"review-{eid}.md"
    target.parent.mkdir(exist_ok=True)
    target.write_text(request, encoding="utf-8")
    epic["status"] = "review"
    save_board(root, board)
    print(f"Review packet: {target}\nReviewer: {reviewer}\nReview target HEAD: {h}")


def review_pass(root, eid, reviewer, report_file):
    board, state, config = checked(root, plan=True)
    must_implement(state)
    epic = find_epic(board, eid)
    require(epic.get("status") == "review", "Start the epic review first")
    path = epic_path(epic)
    require(not git(path, "status", "--porcelain"), "Commit review fixes before passing review")
    h = head(epic)
    quality_evidence = run_quality(path, config)
    require(not git(path, "status", "--porcelain") and head(epic) == h,
            "Quality checks changed the reviewed code/HEAD; commit and repeat review")
    source = report_file.read_text(encoding="utf-8")
    require("Verdict: PASS" in source and f"Reviewed HEAD: {h}" in source,
            "Review report must contain `Verdict: PASS` and exact `Reviewed HEAD: <SHA>`; no unresolved blockers")
    report = control(root) / "reviews" / f"{eid}.md"
    report.parent.mkdir(exist_ok=True)
    report.write_text(source, encoding="utf-8")
    epic.update(status="reviewed", reviewed_head=h, reviewed_at=utcnow(), reviewer=reviewer)
    if quality_evidence:
        epic["review_quality_evidence"] = quality_evidence
    save_board(root, board)
    print(f"Review passed at {h[:12]}; report: {report}. Prepare the MR next.")


def mr_body(epic):
    items = "\n".join(f"- {t['id']} {t['title']} — `{t.get('commit', '')[:12]}` "
                      f"(checks: {t.get('check_evidence', 'not recorded')})" for t in epic["tickets"])
    return (f"## Epic: {epic['id']} — {epic['title']}\n\n{epic['goal']}\n\n"
            f"## Acceptance\n" + "\n".join(f"- {x}" for x in epic["acceptance"]) +
            f"\n\n## Tickets\n{items}\n\n## Review\n"
            f"Reviewed by {epic.get('reviewer')}; HEAD `{epic['reviewed_head'][:12]}`. "
            f"See `.sdlc/reviews/{epic['id']}.md` in the control checkout.\n")


def mr(root, eid, forge, create):
    board, state, config = checked(root, plan=True)
    must_implement(state)
    epic = find_epic(board, eid)
    require(epic.get("status") == "reviewed", "Epic must pass an independent review before MR")
    path = epic_path(epic)
    require(head(epic) == epic.get("reviewed_head"),
            "Epic changed since review; repeat independent review")
    require(not git(path, "status", "--porcelain"), "Commit changes before MR")
    body_path = control(root) / "handoffs" / f"mr-{eid}.md"
    body_path.parent.mkdir(exist_ok=True)
    body_path.write_text(mr_body(epic), encoding="utf-8")
    base, branch = config.get("base_branch", "main"), epic["branch"]
    title = f"[{eid}] {epic['title']}"
    if forge == "github":
        command = ["gh", "pr", "create", "--base", base, "--head", branch,
                   "--title", title, "--body-file", str(body_path)]
    else:
        command = ["glab", "mr", "create", "--source-branch", branch,
                   "--target-branch", base, "--title", title,
                   "--description-file", str(body_path), "--yes"]
    if not create:
        output({"mr_body": str(body_path), "branch": branch, "forge": forge,
                "next": "Push branch, then run `mr EPIC --forge ... --create`", "command": command}, True)
        return
    require(shutil.which(command[0]) is not None, f"{command[0]} not found; install/authenticate forge CLI")
    git(path, "push", "--set-upstream", "origin", branch)
    result = subprocess.run(command, cwd=path, capture_output=True, text=True)
    require(result.returncode == 0, f"MR creation failed: {result.stderr.strip()}")
    url = result.stdout.strip().splitlines()[-1]
    epic["status"], epic["mr_url"] = "mr_open", url
    save_board(root, board)
    print(f"MR created: {url}")


def mr_created(root, eid, url):
    board, state, _ = checked(root, plan=True)
    must_implement(state)
    epic = find_epic(board, eid)
    require(epic.get("status") == "reviewed", "Only reviewed epics may open MRs")
    require(url.startswith("https://"), "Supply the real HTTPS merge request URL")
    require(head(epic) == epic.get("reviewed_head"), "Code changed since review; re-review")
    epic["status"], epic["mr_url"] = "mr_open", url
    save_board(root, board)
    print(f"MR recorded: {url}")


def mark_merged(root, eid, squash_commit=None):
    board, state, config = checked(root, plan=True)
    must_implement(state)
    epic = find_epic(board, eid)
    require(epic.get("status") == "mr_open", "MR must be open first")
    if squash_commit:
        require(git(root, "merge-base", "--is-ancestor", squash_commit,
                    config.get("base_branch", "main"), check=False).returncode == 0,
                "Squash merge commit not on local base branch; fetch it first")
        print("Squash merge manually asserted; verify MR changes were integrated before relying on dependencies.")
    else:
        require(verify_merged(root, config, epic),
                "Reviewed epic tip is not on local base branch; fetch/merge first or specify --squash-commit")
    epic["status"], epic["merged_at"] = "merged", utcnow()
    save_board(root, board)
    print(f"Marked {eid} merged. Dependent epics are now eligible.")


def launch(root, tid, resume, dry_run=False):
    board, state, config = checked(root, plan=True)
    must_implement(state)
    epic, ticket = find_ticket(board, tid)
    require(ticket.get("status") == "in_progress", "Start ticket before launching an agent")
    if ticket.get("agent_run"):
        require(resume, "Ticket already launched; use `launch TICKET --resume` for recovery")
    route = model_for(config, ticket["difficulty"])
    model = ticket.get("assigned_model") or route.get("model")
    provider = ticket.get("assigned_provider") or route.get("provider")
    require(isinstance(model, str) and model, f"Configure routing.{ticket['difficulty']}.model")
    template = config.get("runners", {}).get(provider)
    require(isinstance(template, list) and template and all(isinstance(x, str) for x in template),
            f"Configure runners.{provider} as an argv list, or dispatch with your host's native subagent tool")
    path, _ = brief(root, tid, 10000)
    values = {"model": model, "ticket_id": tid, "worktree": str(epic_path(epic)),
              "prompt_file": str(path)}
    try:
        cmd = [value.format_map(values) for value in template]
    except KeyError as exc:
        raise SDLCError(f"Unknown runner template placeholder: {exc}") from exc
    if dry_run:
        output({"command": cmd, "cwd": str(epic_path(epic)), "brief": str(path)}, True)
        return
    logs = control(root) / "runs"
    logs.mkdir(exist_ok=True)
    logfile = logs / f"{tid}-{utcnow().replace(':', '-')}.log"
    with logfile.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(cmd, cwd=epic_path(epic), stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    ticket["agent_run"] = {"pid": process.pid, "started_at": utcnow(), "log": str(logfile),
                           "model": model, "provider": provider}
    save_board(root, board)
    print(f"Launched {tid}: pid={process.pid} log={logfile}\n"
          "If interrupted, inspect the worktree and log, then launch with --resume (avoid duplicate live agents).")


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", type=Path, default=Path.cwd(),
                   help="Primary project checkout (defaults to current directory)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create .sdlc files (does not initialize git)")
    sub.add_parser("validate", help="Validate board + all existing approval hashes")
    sub.add_parser("status", help="Full project status")
    sub.add_parser("resume", help="Minimal restart context: current phase, active work, ready tickets")
    sub.add_parser("next", help="Dependency-aware eligible tickets, max one per epic")
    a = sub.add_parser("quality", help="Run configured Ruff, ty and test gates for an epic")
    a.add_argument("epic", help="Epic ID whose worktree should be checked")
    a = sub.add_parser("approve", help="Record an explicit HUMAN phase approval")
    a.add_argument("stage", choices=("prd", "structure", "skeleton", "board"))
    a = sub.add_parser("reopen", help="Revisit a previously approved phase before implementation")
    a.add_argument("stage", choices=("prd", "structure", "skeleton", "board"))
    sub.add_parser("scaffold", help="Create approved skeleton files, without overwriting existing ones")
    a = sub.add_parser("brief", help="Create bounded handoff for a single ticket")
    a.add_argument("ticket")
    a.add_argument("--max-chars", type=int, default=10000)
    a = sub.add_parser("recover", help="Generate bounded context for an interrupted active ticket")
    a.add_argument("ticket")
    a.add_argument("--max-chars", type=int, default=10000)
    a = sub.add_parser("start", help="Allocate epic git worktree and start one eligible ticket")
    a.add_argument("ticket")
    a = sub.add_parser("finish", help="Verify ticket commit, record evidence, and mark done")
    a.add_argument("ticket")
    a.add_argument("--checks", required=True, help="Actual test command/result summary")
    a = sub.add_parser("decision", help="Append brief rationale to durable decision log")
    a.add_argument("reference", help="Epic or ticket ID")
    a.add_argument("--choice", required=True)
    a.add_argument("--why", required=True)
    a = sub.add_parser("launch", help="Optional external CLI subagent adapter")
    a.add_argument("ticket")
    a.add_argument("--resume", action="store_true")
    a.add_argument("--dry-run", action="store_true")
    a = sub.add_parser("review", help="Generate independent high-reasoning epic review handoff")
    a.add_argument("epic")
    a.add_argument("--reviewer", required=True, help="Review model label/version")
    a = sub.add_parser("review-pass", help="Record review only after a complete PASS report")
    a.add_argument("epic")
    a.add_argument("--reviewer", required=True)
    a.add_argument("--report", type=Path, required=True)
    a = sub.add_parser("mr", help="Prepare or create GitHub PR / GitLab MR")
    a.add_argument("epic")
    a.add_argument("--forge", choices=("github", "gitlab"), required=True)
    a.add_argument("--create", action="store_true", help="Push and create real remote MR")
    a = sub.add_parser("mr-created", help="Record an MR created outside this CLI")
    a.add_argument("epic")
    a.add_argument("--url", required=True)
    a = sub.add_parser("mark-merged", help="Verify merge before releasing dependent epics")
    a.add_argument("epic")
    a.add_argument("--squash-commit", help="Use only after manually confirming a squash merge")
    return p


def main():
    args = build_parser().parse_args()
    root = args.project.expanduser().resolve()
    try:
        read_only = {"status", "resume", "validate", "next"}
        guard = nullcontext() if args.command in read_only or args.command == "init" else project_lock(root)
        with guard:
            dispatch(args, root)
    except (SDLCError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"SDLC error: {exc}", file=sys.stderr)
        sys.exit(2)


def dispatch(args, root):
    if args.command == "init":
        root.mkdir(parents=True, exist_ok=True)
        init(root)
    elif args.command == "validate":
        board, state, _ = checked(root, plan=False)
        print(f"Valid: phase={state['phase']}; epics={len(board['epics'])}")
    elif args.command in ("status", "resume"):
        output(report(root, concise=args.command == "resume"), True)
    elif args.command == "quality":
        board, state, config = checked(root, plan=True)
        must_implement(state)
        epic = find_epic(board, args.epic)
        evidence = run_quality(epic_path(epic), config)
        output({"epic": args.epic, "enabled": bool(evidence), "results": evidence}, True)
    elif args.command == "next":
        board, state, _ = checked(root)
        must_implement(state)
        output(ready(board), True)
    elif args.command == "approve":
        approve(root, args.stage)
    elif args.command == "reopen":
        reopen(root, args.stage)
    elif args.command == "scaffold":
        scaffold(root)
    elif args.command == "brief":
        location, text = brief(root, args.ticket, args.max_chars)
        print(text)
        print(f"\n[Packet saved: {location}]", file=sys.stderr)
    elif args.command == "recover":
        board, state, config = checked(root, plan=True)
        must_implement(state)
        location, packet = recovery_brief(root, board, config, args.ticket, args.max_chars)
        print(packet)
        print(f"\n[Recovery packet saved: {location}]", file=sys.stderr)
    elif args.command == "start":
        start(root, args.ticket)
    elif args.command == "finish":
        finish(root, args.ticket, args.checks)
    elif args.command == "decision":
        log_decision(root, args.reference, args.choice, args.why)
    elif args.command == "launch":
        launch(root, args.ticket, args.resume, args.dry_run)
    elif args.command == "review":
        review(root, args.epic, args.reviewer)
    elif args.command == "review-pass":
        review_pass(root, args.epic, args.reviewer, args.report)
    elif args.command == "mr":
        mr(root, args.epic, args.forge, args.create)
    elif args.command == "mr-created":
        mr_created(root, args.epic, args.url)
    elif args.command == "mark-merged":
        mark_merged(root, args.epic, args.squash_commit)

if __name__ == "__main__":
    main()
