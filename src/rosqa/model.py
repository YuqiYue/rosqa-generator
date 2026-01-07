from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# --------------------------
# Core ROS entities
# --------------------------

@dataclass(frozen=True)
class Topic:
    name: str
    type: str | None = None


@dataclass(frozen=True)
class Service:
    name: str
    type: str | None = None


# --------------------------
# Parameters + contexts
# --------------------------

@dataclass
class ParameterDef:
    name: str
    type: str
    optional: bool = False
    default: Optional[str] = None
    constraint: Optional[str] = None


@dataclass
class ParameterAssign:
    name: str
    value: str


@dataclass
class ContextDef:
    name: str
    type: str


@dataclass
class ContextAssign:
    name: str
    value: str


# --------------------------
# System wiring (instances)
# --------------------------

@dataclass
class Remap:
    frm: str
    to: str


# --------------------------
# QoS policies
# --------------------------

@dataclass
class QoSPolicy:
    name: str
    kind: str
    settings: Dict[str, str] = field(default_factory=dict)


# --------------------------
# Type + message aliases
# --------------------------

@dataclass
class TypeAlias:
    name: str
    definition: str


@dataclass
class MessageField:
    name: str
    type: str


@dataclass
class MessageAlias:
    name: str
    base_type: str
    fields: List[MessageField] = field(default_factory=list)


# --------------------------
# TF edges
# --------------------------

@dataclass
class TFEdge:
    relation: str  # e.g., "broadcast" or "listens"
    frm: str
    to: str


# --------------------------
# Node type + node instance
# --------------------------

@dataclass
class NodeType:
    name: str

    # Communication declared in node type
    publishes: Set[Tuple[str, str | None]] = field(default_factory=set)    # (topic_name, topic_type)
    subscribes: Set[Tuple[str, str | None]] = field(default_factory=set)
    provides: Set[Tuple[str, str | None]] = field(default_factory=set)     # (service_name, service_type)
    uses: Set[Tuple[str, str | None]] = field(default_factory=set)

    # Dynamic “content(…)” relations (name comes from param assignment at instance time)
    # Stored as ("<content>", param_name, declared_type)
    consumes_content_services: Set[Tuple[str, str, str | None]] = field(default_factory=set)

    # Optional future-proofing for content topics (if you add parser support later)
    publishes_content_topics: Set[Tuple[str, str, str | None]] = field(default_factory=set)
    subscribes_content_topics: Set[Tuple[str, str, str | None]] = field(default_factory=set)

    # Configuration
    parameters: Dict[str, ParameterDef] = field(default_factory=dict)
    contexts: Dict[str, ContextDef] = field(default_factory=dict)

    # Attachments
    qos_attachments: Set[str] = field(default_factory=set)     # e.g., {"best_effort_qos"}
    other_attachments: Dict[str, str] = field(default_factory=dict)

    # TF
    tf_edges: List[TFEdge] = field(default_factory=list)

    # where { ... } block (raw text)
    where_block: Optional[str] = None


@dataclass
class Node:
    name: str
    node_type: NodeType

    # Assignments inside node instance { ... }
    param_assigns: Dict[str, ParameterAssign] = field(default_factory=dict)
    context_assigns: Dict[str, ContextAssign] = field(default_factory=dict)

    # System wiring
    remaps: List[Remap] = field(default_factory=list)


# --------------------------
# Graph (global model)
# --------------------------

@dataclass
class Graph:
    # Instances + types
    nodes: Dict[str, Node] = field(default_factory=dict)
    node_types: Dict[str, NodeType] = field(default_factory=dict)

    # Named comm entities discovered while parsing node types
    topics: Dict[str, Topic] = field(default_factory=dict)
    services: Dict[str, Service] = field(default_factory=dict)

    # Other RO-Spec entities
    qos_policies: Dict[str, QoSPolicy] = field(default_factory=dict)
    type_aliases: Dict[str, TypeAlias] = field(default_factory=dict)
    message_aliases: Dict[str, MessageAlias] = field(default_factory=dict)