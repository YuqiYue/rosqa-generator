from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List

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


def generate_questions(graph: Graph) -> List[Question]:
    qs: List[Question] = []

    nodes = list(graph.nodes.values())
    topics = list(graph.topics.values())
    services = list(graph.services.values())

    # ENTITY questions (presence only, simple version)
    entity_names = (
        [n.name for n in nodes]
        + [t.name for t in topics]
        + [s.name for s in services]
    )

    for e in entity_names:
        qs.append(Question(
            level=Level.ENTITY,
            category=Category.ENTITY,
            qtype=QType.BOOL,
            question=f"Is there a ROS2 entity called {e}?",
            answer="Yes",
        ))

    # PUBLISH / SUBSCRIBE questions per node-topic pair
    for n in nodes:
        for t in topics:
            qs.append(Question(
                level=Level.RELATION,
                category=Category.PUBLISH,
                qtype=QType.BOOL,
                question=f"Does node {n.name} publish to topic {t.name}?",
                answer=_bool_yes_no(t.name in n.publishes),
            ))
            qs.append(Question(
                level=Level.RELATION,
                category=Category.SUBSCRIBE,
                qtype=QType.BOOL,
                question=f"Is node {n.name} subscribed to topic {t.name}?",
                answer=_bool_yes_no(t.name in n.subscribes),
            ))

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

    # SERVICE / CLIENT questions
    for n in nodes:
        for s in services:
            qs.append(Question(
                level=Level.RELATION,
                category=Category.SERVICE,
                qtype=QType.BOOL,
                question=f"Does node {n.name} provide service {s.name}?",
                answer=_bool_yes_no(s.name in n.provides),
            ))
            qs.append(Question(
                level=Level.RELATION,
                category=Category.CLIENT,
                qtype=QType.BOOL,
                question=f"Does node {n.name} use service {s.name} as a client?",
                answer=_bool_yes_no(s.name in n.uses),
            ))

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

    # PATH questions (Level.PATH) can be added later
    return qs