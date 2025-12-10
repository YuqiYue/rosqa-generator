from __future__ import annotations

import argparse
import json
from pathlib import Path

from .rospec_loader import load_graph_from_rospec
from .questions import generate_questions


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate comprehension questions from a rospec specification."
    )
    parser.add_argument("rospec", type=Path, help="Input .rospec file")
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("questions.json"),
        help="Output JSON file (default: questions.json)",
    )
    args = parser.parse_args(argv)

    graph = load_graph_from_rospec(args.rospec)
    questions = generate_questions(graph)

    payload = [
        {
            "level": int(q.level),
            "category": q.category.value,
            "type": q.qtype.value,
            "question": q.question,
            "answer": q.answer,
        }
        for q in questions
    ]

    args.output.write_text(json.dumps(payload, indent=2))