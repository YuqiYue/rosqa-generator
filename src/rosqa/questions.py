from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Set
import random
import string

from .model import Graph


class Level(int, Enum):
    ENTITY = 0
    RELATION = 1
    PATH = 2


class Category(str, Enum):
    ENTITY = "ENTITY"
    PUBLISH = "PUBLISH"
    SUBSCRIBE = "SUBSCRIBE"
    SERVICE = "SERVICE"
    CLIENT = "CLIENT"
    MESSAGE = "MESSAGE"
    SERVICE_TYPE = "SERVICE_TYPE"
    TOPIC_TYPE = "TOPIC_TYPE"


class QType(str, Enum):
    BOOL = "BOOL"
    MCQ = "MCQ"
    OPEN = "OPEN"


@dataclass
class Question:
    level: Level
    category: Category
    qtype: QType
    question: str
    answer: str


def _bool_yes_no(value: bool) -> str:
    return "Yes" if value else "No"


# ---------- fake entities ----------

def _generate_fake_entities(real_names: List[str], count: int = 5) -> List[str]:
    """
    Generate 'fake' entity names that do not exist in the graph.
    Simple strategy: mutate real names a bit and ensure no collision.
    """
    base = set(real_names)
    fake: Set[str] = set()

    if not real_names:
        return []

    attempts = 0
    while len(fake) < count and attempts < count * 10:
        original = random.choice(real_names)
        # Append small random suffix so it still looks ROS-ish but is new
        suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(2))
        candidate = f"{original}_x{suffix}"
        if candidate not in base and candidate not in fake:
            fake.add(candidate)
        attempts += 1

    return sorted(fake)


def _entity_kind(name: str, graph: Graph) -> str:
    """
    Return the 'kind' of entity as:
      "1" = topic, "2" = service, "3" = node
    (matches the numbering used in Algorithm 1.)
    """
    if name in graph.topics:
        return "1"  # topic
    if name in graph.services:
        return "2"  # service
    if name in graph.nodes:
        return "3"  # node
    # Should not happen for real entities, but default to "3" (node)
    return "3"


# ---------- communication path (Level 2) ----------

def _build_adjacency(graph: Graph) -> Dict[str, Set[str]]:
    """
    Build a directed adjacency list over node *names*.
    Edges:
      - topic: publisher -> subscriber
      - service: client <-> server (bidirectional)
    """
    adj: Dict[str, Set[str]] = {name: set() for name in graph.nodes}

    nodes = list(graph.nodes.values())

    # Topic edges: publisher -> subscriber
    for src in nodes:
        for topic in src.publishes:
            for dst in nodes:
                if src.name == dst.name:
                    continue
                if topic in dst.subscribes:
                    adj[src.name].add(dst.name)

    # Service edges: client <-> server
    for src in nodes:
        for srv in src.uses:
            for dst in nodes:
                if srv in dst.provides and src.name != dst.name:
                    # client -> server
                    adj[src.name].add(dst.name)
                    # server -> client (responses)
                    adj[dst.name].add(src.name)

    return adj


def _has_communication_path(src: str, dst: str, graph: Graph) -> bool:
    """
    Return True if there is a path from node `src` to node `dst`
    via topics or services (using the adjacency above).
    """
    if src == dst:
        return False

    adj = _build_adjacency(graph)
    visited: Set[str] = set()
    queue = deque([src])
    visited.add(src)

    while queue:
        current = queue.popleft()
        for neighbor in adj.get(current, ()):
            if neighbor == dst:
                return True
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    return False


# ---------- Main generator (Algorithm 1) ----------

def generate_questions(graph: Graph) -> List[Question]:
    qs: List[Question] = []

    nodes = list(graph.nodes.values())
    topics = list(graph.topics.values())
    services = list(graph.services.values())

    # ---------------- Level 0: ENTITY questions ----------------

    entity_names = (
        [n.name for n in nodes]
        + [t.name for t in topics]
        + [s.name for s in services]
    )

    # Fake entities (BOOL, answer = No)
    fake_entities = _generate_fake_entities(entity_names, count=5)
    for e in fake_entities:
        qs.append(Question(
            level=Level.ENTITY,
            category=Category.ENTITY,
            qtype=QType.BOOL,
            question=f"Is there a ROS2 entity called {e}?",
            answer="No",
        ))

    # Real entities (BOOL + MCQ kind)
    for e in entity_names:
        # BOOL: existence
        qs.append(Question(
            level=Level.ENTITY,
            category=Category.ENTITY,
            qtype=QType.BOOL,
            question=f"Is there a ROS2 entity called {e}?",
            answer="Yes",
        ))

        # MCQ: type of entity
        qs.append(Question(
            level=Level.ENTITY,
            category=Category.ENTITY,
            qtype=QType.MCQ,
            question=(
                f"What kind of ROS2 entity is {e}? "
                "Possible answers: 1- ROS topic, 2- ROS service, 3- ROS node."
            ),
            answer=_entity_kind(e, graph),
        ))

    # ---------------- Level 1: relation questions ----------------

    # PUBLISH / SUBSCRIBE
    for n in nodes:
        for t in topics:
            # BOOL publish?
            qs.append(Question(
                level=Level.RELATION,
                category=Category.PUBLISH,
                qtype=QType.BOOL,
                question=f"Does node {n.name} publish to topic {t.name}?",
                answer=_bool_yes_no(t.name in n.publishes),
            ))
            # BOOL subscribe?
            qs.append(Question(
                level=Level.RELATION,
                category=Category.SUBSCRIBE,
                qtype=QType.BOOL,
                question=f"Is node {n.name} subscribed to topic {t.name}?",
                answer=_bool_yes_no(t.name in n.subscribes),
            ))

        # OPEN publish / subscribe
        qs.append(Question(
            level=Level.RELATION,
            category=Category.PUBLISH,
            qtype=QType.OPEN,
            question=f"To which topics can node {n.name} publish?",
            answer=", ".join(sorted(n.publishes)) or "None",
        ))
        qs.append(Question(
            level=Level.RELATION,
            category=Category.SUBSCRIBE,
            qtype=QType.OPEN,
            question=f"To which topics is node {n.name} subscribed?",
            answer=", ".join(sorted(n.subscribes)) or "None",
        ))

    # SERVICE / CLIENT
    for n in nodes:
        for s in services:
            # BOOL provide service?
            qs.append(Question(
                level=Level.RELATION,
                category=Category.SERVICE,
                qtype=QType.BOOL,
                question=f"Does node {n.name} provide service {s.name}?",
                answer=_bool_yes_no(s.name in n.provides),
            ))
            # BOOL use as client?
            qs.append(Question(
                level=Level.RELATION,
                category=Category.CLIENT,
                qtype=QType.BOOL,
                question=f"Does node {n.name} use service {s.name} as a client?",
                answer=_bool_yes_no(s.name in n.uses),
            ))

        # OPEN services provided / used
        qs.append(Question(
            level=Level.RELATION,
            category=Category.SERVICE,
            qtype=QType.OPEN,
            question=f"Which services does node {n.name} provide?",
            answer=", ".join(sorted(n.provides)) or "None",
        ))
        qs.append(Question(
            level=Level.RELATION,
            category=Category.CLIENT,
            qtype=QType.OPEN,
            question=f"Which services does node {n.name} use as a client?",
            answer=", ".join(sorted(n.uses)) or "None",
        ))

    # TYPE questions
    for s in services:
        qs.append(Question(
            level=Level.RELATION,
            category=Category.SERVICE_TYPE,
            qtype=QType.OPEN,
            question=f"What is the type of service {s.name}?",
            answer=s.type or "Unknown",
        ))
    for t in topics:
        qs.append(Question(
            level=Level.RELATION,
            category=Category.TOPIC_TYPE,
            qtype=QType.OPEN,
            question=f"What is the type of topic {t.name}?",
            answer=t.type or "Unknown",
        ))

    # ---------------- Level 2: path questions ----------------

    for src in nodes:
        for dst in nodes:
            if src.name == dst.name:
                continue
            has_path = _has_communication_path(src.name, dst.name, graph)
            qs.append(Question(
                level=Level.PATH,
                category=Category.MESSAGE,
                qtype=QType.BOOL,
                question=(
                    f"Is there a communication path from node {src.name} "
                    f"to node {dst.name} via a topic or service?"
                ),
                answer=_bool_yes_no(has_path),
            ))

    return qs