"""Bounded task packets; agents load only applicable requirements, not entire docs."""
from __future__ import annotations

import re
from pathlib import Path

from core import SDLCError, control, find_ticket, model_for, require


HEADER = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def section(markdown, title):
    """Return the named Markdown section, ending at an equal/higher-level heading."""
    lines = markdown.splitlines()
    start, depth = None, None
    for i, line in enumerate(lines):
        match = HEADER.match(line)
        if match and match.group(2).strip().casefold() == title.casefold():
            start, depth = i, len(match.group(1))
            break
    if start is None:
        raise SDLCError(f"Referenced document section is missing: {title!r}")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        match = HEADER.match(lines[i])
        if match and len(match.group(1)) <= depth:
            end = i
            break
    return "\n".join(lines[start:end]).strip()


def bullets(items):
    return "\n".join(f"- {item}" for item in items) if items else "- None"


def ticket_brief(root: Path, board, config, tid, max_chars=10000):
    epic, ticket = find_ticket(board, tid)
    route = model_for(config, ticket["difficulty"])
    model = ticket.get("assigned_model") or route.get("model")
    provider = ticket.get("assigned_provider") or route["provider"]
    local = {t["id"]: t for t in epic["tickets"]}
    hard = (
        f"# Task {tid}: {ticket['title']}\n\n"
        f"Epic: {epic['id']} — {epic['title']}\n"
        f"Model tier: {ticket['difficulty']} | provider: {provider} | "
        f"model: {model or 'NOT CONFIGURED'}\n"
        f"Worktree: {epic.get('worktree', '(created at ticket start)')}\n\n"
        f"## Objective\n{ticket['description']}\n\n"
        f"## Epic goal\n{epic['goal']}\n\n"
        f"## Ticket acceptance criteria\n{bullets(ticket['acceptance'])}\n\n"
        f"## Epic acceptance criteria\n{bullets(epic['acceptance'])}\n\n"
        f"## Validation commands (run; do not claim success without evidence)\n"
        f"{bullets(ticket['tests'])}\n\n"
        f"## Relevant paths (read locally only as needed)\n{bullets(ticket['files'])}\n\n"
        f"## Completed prerequisites\n"
        f"{bullets([f"{d}: {local[d].get('commit', 'commit not recorded')}" for d in ticket['depends_on']])}\n\n"
        "## Agent contract\n"
        "- Work only in the assigned epic worktree; one active ticket per epic.\n"
        "- Implement this ticket, not the whole epic. Respect approved PRD and architecture.\n"
        "- Prefer straightforward implementation; make internal technical decisions autonomously.\n"
        "- Ask the human only about externally observable product behavior; give concise options.\n"
        "- Explain consequential decisions in 1-3 bullets to the orchestrator for logging.\n"
        "- Do not modify .sdlc/board.yml or .sdlc/state.yml inside the worktree.\n"
        "- For Python: follow .sdlc/PYTHON_STYLE.md and pyproject.toml; fully type new APIs.\n"
        "- For Python: format with Ruff; run Ruff lint, ty, and behavior tests.\n"
        "- Run indicated tests, report evidence, and create a scoped git commit.\n"
        "- Report commit SHA and a concise result; orchestrator marks the ticket done.\n"
    )
    require(len(hard) <= max_chars, f"Mandatory ticket context uses {len(hard)} chars; raise --max-chars")
    parts = [hard]
    for source, headings in (("PRD.md", ticket["prd_sections"]),
                             ("ARCHITECTURE.md", ticket["architecture_sections"])):
        if not headings:
            continue
        doc = (control(root) / source).read_text(encoding="utf-8")
        for heading in headings:
            extracted = section(doc, heading)
            optional = f"\n\n## From {source}: {heading}\n{extracted}\n"
            if len("".join(parts)) + len(optional) > max_chars:
                parts.append(f"\n\n[Additional {source} sections omitted by context budget. Read on demand.]\n")
                break
            parts.append(optional)
    # Previous decision summaries are optional, not mandatory context.
    log = control(root) / "DECISIONS.md"
    if log.exists():
        entries = re.split(r"(?=^## )", log.read_text(encoding="utf-8"), flags=re.M)
        relevant = [x for x in entries if x.startswith("## ") and
                    (f"[{tid}]" in x.splitlines()[0] or f"[{epic['id']}]" in x.splitlines()[0])]
        for entry in relevant[-4:]:
            snippet = "\n\n## Prior decision\n" + entry.strip() + "\n"
            if len("".join(parts)) + len(snippet) <= max_chars:
                parts.append(snippet)
    return "".join(parts)


def reviewer_brief(board, epic, base, head, candidate):
    tasks = "\n".join(
        f"- {t['id']}: {t['title']} ({t.get('commit', 'no commit')})"
        for t in epic["tickets"]
    )
    return (
        f"# Independent epic review: {epic['id']} — {epic['title']}\n\n"
        f"Requested reviewer: {candidate}\n"
        f"Base branch: {base} | review HEAD: {head}\n"
        f"Epic objective: {epic['goal']}\n\n"
        f"Acceptance:\n{bullets(epic['acceptance'])}\n\n"
        f"Completed tickets:\n{tasks}\n\n"
        "Review the epic diff against the base branch (e.g. `git diff BASE...HEAD`), "
        "tests, contract behavior, security, error handling, concurrency, and regressions. "
        "Identify only actionable, evidenced defects, with file/line references. "
        "Fix legitimate issues in the epic worktree; commit review fixes separately. "
        "Re-run relevant tests, Ruff format/lint, and ty when Python gates are enabled. "
        "Record a short report with findings, fixes, remaining risks, "
        "reviewed HEAD and an explicit PASS/FAIL verdict. Do not open an MR on FAIL.\n"
    )
