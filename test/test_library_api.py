from pathlib import Path
from rosqa.rospec_loader import load_graph_from_rospec
from rosqa.questions import generate_questions


def test_library_api_smoke():
    g = load_graph_from_rospec(Path("examples/airdrone_driver.rospec"))
    qs = generate_questions(g)
    assert len(qs) > 0