from dataclasses import dataclass, field
from typing import Dict, Set


@dataclass(frozen=True)
class Topic:
    name: str
    type: str | None = None


@dataclass(frozen=True)
class Service:
    name: str
    type: str | None = None


@dataclass
class Node:
    name: str
    publishes: Set[str] = field(default_factory=set)   # topic names
    subscribes: Set[str] = field(default_factory=set)
    provides: Set[str] = field(default_factory=set)    # service names
    uses: Set[str] = field(default_factory=set)


@dataclass
class Graph:
    nodes: Dict[str, Node]
    topics: Dict[str, Topic]
    services: Dict[str, Service]