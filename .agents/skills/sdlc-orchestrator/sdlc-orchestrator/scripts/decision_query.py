#!/usr/bin/env python3
"""
decision_query.py — Extract decisions relevant to a ticket or epic from .sdlc/DECISIONS.md.
Usage (from project root after init):
  python /path/to/skill/scripts/decision_query.py --project . --ticket T001
  python /path/to/skill/scripts/decision_query.py --project . --epic E001
"""

import argparse
import re
from pathlib import Path

try:
    import yaml
except ImportError:
    pass  # not required for this helper


def control(root: Path) -> Path:
    return root / ".sdlc"


def load_decisions(root: Path) -> str:
    path = control(root) / "DECISIONS.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def filter_relevant(text: str, keyword: str) -> str:
    if not text.strip():
        return "(no decisions yet)"
    sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)
    relevant = [s for s in sections if keyword.lower() in s.lower()]
    if not relevant:
        return "(no matching decisions)"
    return "\n".join(relevant).strip()


def main():
    parser = argparse.ArgumentParser(description="Query relevant decisions from .sdlc/DECISIONS.md")
    parser.add_argument("--project", required=True, help="Path to primary project checkout")
    parser.add_argument("--ticket")
    parser.add_argument("--epic")
    args = parser.parse_args()

    if not args.ticket and not args.epic:
        parser.error("Provide --ticket or --epic")

    root = Path(args.project).resolve()
    text = load_decisions(root)
    keyword = args.ticket or args.epic
    print(filter_relevant(text, keyword))


if __name__ == "__main__":
    main()
