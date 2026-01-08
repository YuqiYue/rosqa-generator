from __future__ import annotations
from dataclasses import asdict
from typing import Any, Dict, List
from .questions import Question

def questions_to_json(questions: List[Question]) -> List[Dict[str, Any]]:
    out = []
    for q in questions:
        d = asdict(q)
        # normalize field names to your JSON schema
        d["level"] = int(d["level"])
        d["category"] = str(d["category"])
        d["type"] = str(d.pop("qtype"))
        out.append(d)
    return out