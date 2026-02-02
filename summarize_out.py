from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any


def summarize_file(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    total = len(data)

    per_level = {0: 0, 1: 0, 2: 0}
    for q in data:
        # your JSON uses "level": int(...)
        lvl = int(q.get("level", -1))
        if lvl in per_level:
            per_level[lvl] += 1

    return {
        "name": path.stem,
        "total": total,
        "l0": per_level[0],
        "l1": per_level[1],
        "l2": per_level[2],
    }


def main() -> None:
    out_dir = Path("out")
    files = sorted(out_dir.glob("*.json"))
    if not files:
        raise SystemExit("No JSON files found under ./out")

    rows = [summarize_file(p) for p in files]
    grand_total = sum(r["total"] for r in rows)
    grand_l0 = sum(r["l0"] for r in rows)
    grand_l1 = sum(r["l1"] for r in rows)
    grand_l2 = sum(r["l2"] for r in rows)

    print("Per-spec summary:")
    for r in rows:
        print(
            f"{r['name']}: total={r['total']} (L0={r['l0']}, L1={r['l1']}, L2={r['l2']})"
        )
    print()
    print(f"Grand total: {grand_total} (L0={grand_l0}, L1={grand_l1}, L2={grand_l2})")

    # Emit a LaTeX table you can paste into the paper
    print("\nLaTeX table:\n")
    print(r"\begin{table}[t]")
    print(r"\centering")
    print(r"\caption{Number of generated questions per ROSpec specification and abstraction level.}")
    print(r"\label{tab:questions-per-spec-level}")
    print(r"\begin{tabular}{l r r r r}")
    print(r"\hline")
    print(r"\textbf{Specification} & \textbf{\#Total} & \textbf{\#L0} & \textbf{\#L1} & \textbf{\#L2} \\")
    print(r"\hline")
    for r in rows:
        name = r["name"].replace("_", r"\_")
        print(f"\\texttt{{{name}}} & {r['total']} & {r['l0']} & {r['l1']} & {r['l2']} \\\\")
    print(r"\hline")
    print(f"\\textbf{{Total}} & \\textbf{{{grand_total}}} & \\textbf{{{grand_l0}}} & \\textbf{{{grand_l1}}} & \\textbf{{{grand_l2}}} \\\\")
    print(r"\hline")
    print(r"\end{tabular}")
    print(r"\end{table}")


if __name__ == "__main__":
    main()