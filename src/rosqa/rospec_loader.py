from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Set

from .model import Graph, Node, Topic, Service


@dataclass
class NodeType:
    name: str
    publishes: Set[tuple[str, str | None]] = field(default_factory=set)
    subscribes: Set[tuple[str, str | None]] = field(default_factory=set)
    provides: Set[tuple[str, str | None]] = field(default_factory=set)
    uses: Set[tuple[str, str | None]] = field(default_factory=set)


NODE_TYPE_RE = re.compile(
    r"node\s+type\s+(\w+)\s*{(.*?)}(?:\s*where\s*{.*?})?",
    re.DOTALL,
)

SYSTEM_RE = re.compile(
    r"system\s*{(.*)}\s*$",
    re.DOTALL,
)

NODE_INSTANCE_RE = re.compile(
    r"node\s+instance\s+(\w+)\s*:\s*(\w+)\s*{",
)


COMM_PUBLISH_RE = re.compile(
    r"publishes\s+to\s+([^\s:]+)\s*:\s*([^;]+);"
)
COMM_SUBSCRIBE_RE = re.compile(
    r"subscribes\s+to\s+([^\s:]+)\s*:\s*([^;]+);"
)
COMM_PROVIDES_RE = re.compile(
    r"provides\s+service\s+([^\s:]+)\s*:\s*([^;]+);"
)
COMM_USES_RE = re.compile(
    r"uses\s+service\s+([^\s:]+)\s*:\s*([^;]+);"
)


def _parse_node_types(text: str) -> Dict[str, NodeType]:
    node_types: Dict[str, NodeType] = {}

    for m in NODE_TYPE_RE.finditer(text):
        type_name = m.group(1)
        body = m.group(2)

        nt = NodeType(name=type_name)

        for topic, typ in COMM_PUBLISH_RE.findall(body):
            nt.publishes.add((topic.strip(), typ.strip() or None))

        for topic, typ in COMM_SUBSCRIBE_RE.findall(body):
            nt.subscribes.add((topic.strip(), typ.strip() or None))

        for srv, typ in COMM_PROVIDES_RE.findall(body):
            nt.provides.add((srv.strip(), typ.strip() or None))

        for srv, typ in COMM_USES_RE.findall(body):
            nt.uses.add((srv.strip(), typ.strip() or None))

        node_types[nt.name] = nt

    return node_types


def _parse_system_block(text: str) -> str | None:
    m = SYSTEM_RE.search(text)
    if not m:
        return None
    return m.group(1)


def _parse_node_instances(system_body: str) -> list[tuple[str, str]]:
    """Return list of (instance_name, type_name)."""
    instances: list[tuple[str, str]] = []
    for m in NODE_INSTANCE_RE.finditer(system_body):
        inst = m.group(1)
        ty = m.group(2)
        instances.append((inst, ty))
    return instances


def load_graph_from_rospec(path: Path) -> Graph:
    """
    Very small rospec parser focused only on:
      - node type communication (publish/subscribe, provides/uses)
      - node instances in the system block

    It builds a Graph over node *instances*.
    """
    text = path.read_text()

    node_types = _parse_node_types(text)
    system_body = _parse_system_block(text)
    if system_body is None:
        raise ValueError("No `system { ... }` block found in specification")

    instances = _parse_node_instances(system_body)

    # Build topics / services maps and instance-level nodes
    topics: Dict[str, Topic] = {}
    services: Dict[str, Service] = {}
    nodes: Dict[str, Node] = {}

    for inst_name, type_name in instances:
        nt = node_types.get(type_name)
        if nt is None:
            # For now, ignore instances whose type we didn't parse
            continue

        node = Node(name=inst_name)

        # inherit communication from node type
        for topic_name, topic_type in nt.publishes:
            node.publishes.add(topic_name)
            if topic_name not in topics:
                topics[topic_name] = Topic(name=topic_name, type=topic_type)
        for topic_name, topic_type in nt.subscribes:
            node.subscribes.add(topic_name)
            if topic_name not in topics:
                topics[topic_name] = Topic(name=topic_name, type=topic_type)

        for srv_name, srv_type in nt.provides:
            node.provides.add(srv_name)
            if srv_name not in services:
                services[srv_name] = Service(name=srv_name, type=srv_type)
        for srv_name, srv_type in nt.uses:
            node.uses.add(srv_name)
            if srv_name not in services:
                services[srv_name] = Service(name=srv_name, type=srv_type)

        nodes[node.name] = node

    return Graph(nodes=nodes, topics=topics, services=services)