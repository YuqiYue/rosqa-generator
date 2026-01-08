from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional, Set, Tuple
import random
import re
import string

from .model import Graph


class Level(int, Enum):
    ENTITY = 0
    RELATION = 1
    PATH = 2


class Category(str, Enum):
    # Existing
    ENTITY = "ENTITY"
    PUBLISH = "PUBLISH"
    SUBSCRIBE = "SUBSCRIBE"
    SERVICE = "SERVICE"
    CLIENT = "CLIENT"
    MESSAGE = "MESSAGE"
    SERVICE_TYPE = "SERVICE_TYPE"
    TOPIC_TYPE = "TOPIC_TYPE"
    PARAMETER = "PARAMETER"
    PARAMETER_ASSIGN = "PARAMETER_ASSIGN"
    CONTENT_SERVICE = "CONTENT_SERVICE"

    # Added to cover “all entities supported by rospec”
    NODE_TYPE = "NODE_TYPE"
    NODE_INSTANCE = "NODE_INSTANCE"
    CONTEXT = "CONTEXT"
    CONTEXT_ASSIGN = "CONTEXT_ASSIGN"
    REMAP = "REMAP"
    TF = "TF"
    POLICY = "POLICY"
    TYPE_ALIAS = "TYPE_ALIAS"
    MESSAGE_ALIAS = "MESSAGE_ALIAS"
    MESSAGE_FIELD = "MESSAGE_FIELD"
    ATTACHMENT = "ATTACHMENT"
    WHERE = "WHERE"


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


# -----------------------
# Small helpers
# -----------------------

def _bool_yes_no(value: bool) -> str:
    return "Yes" if value else "No"


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


def _open_empty() -> str:
    # For “no items exist / empty set / not declared”
    return "None"


def _open_unknown() -> str:
    # For “should exist but could not be inferred / not parsed”
    return "Unknown"


def _comma_list(items: Iterable[str]) -> str:
    xs = [x for x in items if x]
    xs = sorted(set(xs))
    return ", ".join(xs) if xs else _open_empty()


def _opt_empty(x: Optional[str]) -> str:
    # For optional blocks that may legitimately be absent
    if x is None:
        return _open_empty()
    x = str(x).strip()
    return x if x else _open_empty()


def _opt_unknown(x: Optional[str]) -> str:
    # For fields that likely should be known (e.g. type) but may be missing
    if x is None:
        return _open_unknown()
    x = str(x).strip()
    return x if x else _open_unknown()


# -----------------------
# content(param_name) handling
# -----------------------

_CONTENT_RE = re.compile(r"^content\((?P<param>\w+)\)$")


def _maybe_content_param(name: str) -> Optional[str]:
    m = _CONTENT_RE.match(name.strip())
    return m.group("param") if m else None


def _resolve_content_name(raw_name: str, node) -> str:
    """
    If raw_name is content(PARAM), resolve using node.param_assigns[PARAM].value
    Otherwise return raw_name as is.
    """
    param = _maybe_content_param(raw_name)
    if not param:
        return raw_name
    assigns = getattr(node, "param_assigns", {}) or {}
    if param in assigns:
        return _strip_quotes(assigns[param].value)
    # Unresolved: keep original so questions still make sense
    return raw_name


def _entity_kind(name: str, graph: Graph) -> str:
    # MCQ: 1 topic, 2 service, 3 node
    if name in getattr(graph, "topics", {}):
        return "1"
    if name in getattr(graph, "services", {}):
        return "2"
    if name in getattr(graph, "nodes", {}):
        return "3"
    return "3"


# -----------------------
# Fake (negative) entities
# -----------------------

def _generate_fake_entities(real_names: List[str], count: int = 5) -> List[str]:
    base = set(real_names)
    fake: Set[str] = set()
    if not real_names or count <= 0:
        return []

    attempts = 0
    while len(fake) < count and attempts < count * 30:
        original = random.choice(real_names)
        suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(2))
        candidate = f"{original}_x{suffix}"
        if candidate not in base and candidate not in fake:
            fake.add(candidate)
        attempts += 1

    return sorted(fake)


# -----------------------
# Remaps and effective names
# -----------------------

def _apply_remaps(name: str, node) -> str:
    """
    Apply instance remaps (A->B) if the exact name matches a remap 'frm'.
    """
    for r in getattr(node, "remaps", []) or []:
        if r.frm == name:
            return r.to
    return name


def _effective_publishes(node) -> Set[str]:
    names: List[str] = []
    for (raw, _typ) in getattr(node.node_type, "publishes", set()) or set():
        resolved = _resolve_content_name(raw, node)
        resolved = _apply_remaps(resolved, node)
        names.append(resolved)
    return set(names)


def _effective_subscribes(node) -> Set[str]:
    names: List[str] = []
    for (raw, _typ) in getattr(node.node_type, "subscribes", set()) or set():
        resolved = _resolve_content_name(raw, node)
        resolved = _apply_remaps(resolved, node)
        names.append(resolved)
    return set(names)


def _effective_provides(node) -> Set[str]:
    names: List[str] = []
    for (srv, _typ) in getattr(node.node_type, "provides", set()) or set():
        resolved = _resolve_content_name(srv, node)
        resolved = _apply_remaps(resolved, node)
        names.append(resolved)
    return set(names)


def _effective_uses(node) -> Set[str]:
    """
    Explicit uses service X: T; plus content-based consumes service content(param): T;
    Loader stores consumes_content_services as tuples ("<content>", param_name, srv_type).
    """
    names: List[str] = []

    for (srv, _typ) in getattr(node.node_type, "uses", set()) or set():
        resolved = _resolve_content_name(srv, node)
        resolved = _apply_remaps(resolved, node)
        names.append(resolved)

    for (_placeholder, param_name, _srv_type) in getattr(node.node_type, "consumes_content_services", set()) or set():
        assigns = getattr(node, "param_assigns", {}) or {}
        if param_name in assigns:
            resolved = _strip_quotes(assigns[param_name].value)
            resolved = _apply_remaps(resolved, node)
            names.append(resolved)
        else:
            # unresolved content param: keep as content(param)
            names.append(f"content({param_name})")

    return set(names)


# -----------------------
# Connectivity (Level 2)
# -----------------------

def _build_adjacency(graph: Graph) -> Dict[str, Set[str]]:
    adj: Dict[str, Set[str]] = {name: set() for name in getattr(graph, "nodes", {})}
    nodes = list(getattr(graph, "nodes", {}).values())

    # Topic edges: publisher -> subscriber
    for src in nodes:
        src_pub = _effective_publishes(src)
        if not src_pub:
            continue
        for dst in nodes:
            if src.name == dst.name:
                continue
            dst_sub = _effective_subscribes(dst)
            if src_pub & dst_sub:
                adj[src.name].add(dst.name)

    # Service edges: client <-> server
    for client in nodes:
        client_uses = _effective_uses(client)
        if not client_uses:
            continue
        for server in nodes:
            if server.name == client.name:
                continue
            server_prov = _effective_provides(server)
            if client_uses & server_prov:
                adj[client.name].add(server.name)
                adj[server.name].add(client.name)

    return adj


def _has_communication_path(src: str, dst: str, graph: Graph) -> bool:
    if src == dst:
        return False

    adj = _build_adjacency(graph)
    visited: Set[str] = {src}
    q = deque([src])

    while q:
        cur = q.popleft()
        for nxt in adj.get(cur, set()):
            if nxt == dst:
                return True
            if nxt not in visited:
                visited.add(nxt)
                q.append(nxt)
    return False


# -----------------------
# “Families” of questions
# -----------------------

def generate_questions(
    graph: Graph,
    *,
    include_negative_entities: bool = True,
    negative_entities_per_file: int = 5,
) -> List[Question]:
    qs: List[Question] = []

    nodes = list(getattr(graph, "nodes", {}).values())
    node_types = list(getattr(graph, "node_types", {}).values())
    topics = list(getattr(graph, "topics", {}).values())
    services = list(getattr(graph, "services", {}).values())
    qos_policies = list(getattr(graph, "qos_policies", {}).values())
    type_aliases = list(getattr(graph, "type_aliases", {}).values())
    message_aliases = list(getattr(graph, "message_aliases", {}).values())

    # ------------------------------------------------------------
    # Level 0: ENTITY existence + kind (node/topic/service)
    # ------------------------------------------------------------

    entity_names = (
        [n.name for n in nodes]
        + [t.name for t in topics]
        + [s.name for s in services]
    )

    if include_negative_entities:
        for e in _generate_fake_entities(entity_names, count=negative_entities_per_file):
            qs.append(Question(
                level=Level.ENTITY,
                category=Category.ENTITY,
                qtype=QType.BOOL,
                question=f"Is there a ROS2 entity called {e}?",
                answer="No",
            ))

    for e in entity_names:
        qs.append(Question(
            level=Level.ENTITY,
            category=Category.ENTITY,
            qtype=QType.BOOL,
            question=f"Is there a ROS2 entity called {e}?",
            answer="Yes",
        ))
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

    # ------------------------------------------------------------
    # Level 1: NODE TYPE family
    # ------------------------------------------------------------

    for nt in node_types:
        qs.append(Question(
            level=Level.RELATION,
            category=Category.NODE_TYPE,
            qtype=QType.BOOL,
            question=f"Is there a ROSpec node type called {nt.name}?",
            answer="Yes",
        ))

        # Parameters (defs)
        param_defs = getattr(nt, "parameters", {}) or {}
        qs.append(Question(
            level=Level.RELATION,
            category=Category.PARAMETER,
            qtype=QType.OPEN,
            question=f"Which parameters are defined in node type {nt.name}?",
            answer=_comma_list(param_defs.keys()),
        ))

        for p in param_defs.values():
            qs.append(Question(
                level=Level.RELATION,
                category=Category.PARAMETER,
                qtype=QType.OPEN,
                question=f"What is the type of parameter {p.name} in node type {nt.name}?",
                answer=_opt_unknown(getattr(p, "type", None)),
            ))
            qs.append(Question(
                level=Level.RELATION,
                category=Category.PARAMETER,
                qtype=QType.BOOL,
                question=f"Is parameter {p.name} optional in node type {nt.name}?",
                answer=_bool_yes_no(bool(getattr(p, "optional", False))),
            ))
            default = getattr(p, "default", None)
            qs.append(Question(
                level=Level.RELATION,
                category=Category.PARAMETER,
                qtype=QType.OPEN,
                question=f"What is the default value of parameter {p.name} in node type {nt.name}?",
                answer=_opt_empty(str(default)) if default is not None else _open_empty(),
            ))
            constraint = getattr(p, "constraint", None)
            qs.append(Question(
                level=Level.RELATION,
                category=Category.WHERE,
                qtype=QType.BOOL,
                question=f"Does parameter {p.name} in node type {nt.name} have a constraint?",
                answer=_bool_yes_no(bool(constraint)),
            ))
            if constraint:
                qs.append(Question(
                    level=Level.RELATION,
                    category=Category.WHERE,
                    qtype=QType.OPEN,
                    question=f"What is the constraint of parameter {p.name} in node type {nt.name}?",
                    answer=str(constraint).strip() or _open_empty(),
                ))

        # Contexts (defs)
        ctx_defs = getattr(nt, "contexts", {}) or {}
        qs.append(Question(
            level=Level.RELATION,
            category=Category.CONTEXT,
            qtype=QType.OPEN,
            question=f"Which contexts are defined in node type {nt.name}?",
            answer=_comma_list(ctx_defs.keys()),
        ))
        for c in ctx_defs.values():
            qs.append(Question(
                level=Level.RELATION,
                category=Category.CONTEXT,
                qtype=QType.OPEN,
                question=f"What is the type of context {c.name} in node type {nt.name}?",
                answer=_opt_unknown(getattr(c, "type", None)),
            ))

        # Attachments (qos + other)
        qos_att = sorted(getattr(nt, "qos_attachments", set()) or set())
        other_att = getattr(nt, "other_attachments", {}) or {}

        qs.append(Question(
            level=Level.RELATION,
            category=Category.ATTACHMENT,
            qtype=QType.OPEN,
            question=f"Which QoS policy tags are attached in node type {nt.name}?",
            answer=_comma_list(qos_att),
        ))
        qs.append(Question(
            level=Level.RELATION,
            category=Category.ATTACHMENT,
            qtype=QType.OPEN,
            question=f"Which non-QoS attachments are declared in node type {nt.name}?",
            answer=_comma_list([f"{k}={v}" for k, v in other_att.items()]),
        ))
        for k, v in other_att.items():
            qs.append(Question(
                level=Level.RELATION,
                category=Category.ATTACHMENT,
                qtype=QType.OPEN,
                question=f"What is the value of attachment @{k} in node type {nt.name}?",
                answer=str(v).strip() or _open_empty(),
            ))

        # Connections declared at type level (raw names, may include content(...))
        pubs = [name for (name, _t) in (getattr(nt, "publishes", set()) or set())]
        subs = [name for (name, _t) in (getattr(nt, "subscribes", set()) or set())]
        prov = [name for (name, _t) in (getattr(nt, "provides", set()) or set())]
        uses = [name for (name, _t) in (getattr(nt, "uses", set()) or set())]
        consumes_content = list(getattr(nt, "consumes_content_services", set()) or set())

        qs.append(Question(
            Level.RELATION,
            Category.PUBLISH,
            QType.OPEN,
            f"Which topics can node type {nt.name} publish to (as declared)?",
            _comma_list(pubs),
        ))
        qs.append(Question(
            Level.RELATION,
            Category.SUBSCRIBE,
            QType.OPEN,
            f"Which topics can node type {nt.name} subscribe to (as declared)?",
            _comma_list(subs),
        ))
        qs.append(Question(
            Level.RELATION,
            Category.SERVICE,
            QType.OPEN,
            f"Which services can node type {nt.name} provide (as declared)?",
            _comma_list(prov),
        ))
        qs.append(Question(
            Level.RELATION,
            Category.CLIENT,
            QType.OPEN,
            f"Which services can node type {nt.name} use (as declared)?",
            _comma_list(uses),
        ))

        # where block at end of node type (optional)
        where_block = getattr(nt, "where_block", None)
        qs.append(Question(
            level=Level.RELATION,
            category=Category.WHERE,
            qtype=QType.BOOL,
            question=f"Does node type {nt.name} declare a where-clause?",
            answer=_bool_yes_no(bool(where_block)),
        ))
        qs.append(Question(
            level=Level.RELATION,
            category=Category.WHERE,
            qtype=QType.OPEN,
            question=f"What is the where-clause of node type {nt.name}?",
            answer=_opt_empty(where_block),
        ))

        # content(service_param) declarations (unresolved until instance time)
        for (_ph, param_name, srv_type) in consumes_content:
            qs.append(Question(
                level=Level.RELATION,
                category=Category.CONTENT_SERVICE,
                qtype=QType.OPEN,
                question=f"Which parameter provides the consumed service name via content(...) in node type {nt.name}?",
                answer=str(param_name).strip() or _open_unknown(),
            ))
            qs.append(Question(
                level=Level.RELATION,
                category=Category.CONTENT_SERVICE,
                qtype=QType.OPEN,
                question=f"What is the declared type of the consumed content-based service in node type {nt.name}?",
                answer=_opt_unknown(srv_type),
            ))

        # TF edges
        tf_edges = getattr(nt, "tf_edges", []) or []
        qs.append(Question(
            level=Level.RELATION,
            category=Category.TF,
            qtype=QType.OPEN,
            question=f"What TF relations are declared in node type {nt.name}?",
            answer=_comma_list([f"{e.relation} {e.frm}->{e.to}" for e in tf_edges]),
        ))

    # ------------------------------------------------------------
    # Level 1: NODE INSTANCE family
    # ------------------------------------------------------------

    for n in nodes:
        qs.append(Question(
            level=Level.RELATION,
            category=Category.NODE_INSTANCE,
            qtype=QType.OPEN,
            question=f"What is the node type of node instance {n.name}?",
            answer=_opt_unknown(getattr(getattr(n, "node_type", None), "name", None)),
        ))

        assigns = getattr(n, "param_assigns", {}) or {}
        qs.append(Question(
            level=Level.RELATION,
            category=Category.PARAMETER_ASSIGN,
            qtype=QType.OPEN,
            question=f"Which parameters are assigned in node instance {n.name}?",
            answer=_comma_list(assigns.keys()),
        ))
        for k, v in assigns.items():
            qs.append(Question(
                level=Level.RELATION,
                category=Category.PARAMETER_ASSIGN,
                qtype=QType.OPEN,
                question=f"What value is assigned to parameter {k} in node instance {n.name}?",
                answer=_strip_quotes(v.value) if v and getattr(v, "value", None) is not None else _open_unknown(),
            ))

        cassigns = getattr(n, "context_assigns", {}) or {}
        qs.append(Question(
            level=Level.RELATION,
            category=Category.CONTEXT_ASSIGN,
            qtype=QType.OPEN,
            question=f"Which contexts are assigned in node instance {n.name}?",
            answer=_comma_list(cassigns.keys()),
        ))
        for k, v in cassigns.items():
            qs.append(Question(
                level=Level.RELATION,
                category=Category.CONTEXT_ASSIGN,
                qtype=QType.OPEN,
                question=f"What value is assigned to context {k} in node instance {n.name}?",
                answer=_strip_quotes(v.value) if v and getattr(v, "value", None) is not None else _open_unknown(),
            ))

        remaps = getattr(n, "remaps", []) or []
        qs.append(Question(
            level=Level.RELATION,
            category=Category.REMAP,
            qtype=QType.OPEN,
            question=f"Which remaps are declared in node instance {n.name}?",
            answer=_comma_list([f"{r.frm}->{r.to}" for r in remaps]),
        ))
        for r in remaps:
            qs.append(Question(
                level=Level.RELATION,
                category=Category.REMAP,
                qtype=QType.BOOL,
                question=f"Does node instance {n.name} remap {r.frm} to {r.to}?",
                answer="Yes",
            ))

        qs.append(Question(
            Level.RELATION,
            Category.PUBLISH,
            QType.OPEN,
            f"To which topics can node {n.name} publish (after resolving content(...) and remaps)?",
            _comma_list(_effective_publishes(n)),
        ))
        qs.append(Question(
            Level.RELATION,
            Category.SUBSCRIBE,
            QType.OPEN,
            f"To which topics is node {n.name} subscribed (after resolving content(...) and remaps)?",
            _comma_list(_effective_subscribes(n)),
        ))
        qs.append(Question(
            Level.RELATION,
            Category.SERVICE,
            QType.OPEN,
            f"Which services does node {n.name} provide (after resolving content(...) and remaps)?",
            _comma_list(_effective_provides(n)),
        ))
        qs.append(Question(
            Level.RELATION,
            Category.CLIENT,
            QType.OPEN,
            f"Which services does node {n.name} use as a client (after resolving content(...) and remaps)?",
            _comma_list(_effective_uses(n)),
        ))

        for (_ph, param_name, srv_type) in getattr(n.node_type, "consumes_content_services", set()) or set():
            qs.append(Question(
                level=Level.RELATION,
                category=Category.CONTENT_SERVICE,
                qtype=QType.BOOL,
                question=f"Does node {n.name} consume a service whose name is provided by parameter {param_name}?",
                answer="Yes",
            ))
            assigned = param_name in assigns
            qs.append(Question(
                level=Level.RELATION,
                category=Category.CONTENT_SERVICE,
                qtype=QType.BOOL,
                question=f"Is parameter {param_name} assigned in node instance {n.name} for resolving the consumed service name?",
                answer=_bool_yes_no(assigned),
            ))
            if assigned:
                resolved_name = _strip_quotes(assigns[param_name].value)
                resolved_name = _apply_remaps(resolved_name, n)
                qs.append(Question(
                    level=Level.RELATION,
                    category=Category.CONTENT_SERVICE,
                    qtype=QType.OPEN,
                    question=f"What is the resolved consumed service name for node {n.name} (via parameter {param_name})?",
                    answer=resolved_name or _open_unknown(),
                ))
                qs.append(Question(
                    level=Level.RELATION,
                    category=Category.CONTENT_SERVICE,
                    qtype=QType.OPEN,
                    question=f"What is the declared type of the consumed service resolved via parameter {param_name} in node {n.name}?",
                    answer=_opt_unknown(srv_type),
                ))

    # ------------------------------------------------------------
    # Level 1: TOPIC family
    # ------------------------------------------------------------

    topic_publishers: Dict[str, Set[str]] = {}
    topic_subscribers: Dict[str, Set[str]] = {}

    for n in nodes:
        for tname in _effective_publishes(n):
            topic_publishers.setdefault(tname, set()).add(n.name)
        for tname in _effective_subscribes(n):
            topic_subscribers.setdefault(tname, set()).add(n.name)

    for t in topics:
        qs.append(Question(
            Level.RELATION,
            Category.TOPIC_TYPE,
            QType.OPEN,
            f"What is the type of topic {t.name}?",
            _opt_unknown(getattr(t, "type", None)),
        ))
        qs.append(Question(
            Level.RELATION,
            Category.PUBLISH,
            QType.OPEN,
            f"Which nodes publish to topic {t.name} (after resolving content(...) and remaps)?",
            _comma_list(topic_publishers.get(t.name, set())),
        ))
        qs.append(Question(
            Level.RELATION,
            Category.SUBSCRIBE,
            QType.OPEN,
            f"Which nodes subscribe to topic {t.name} (after resolving content(...) and remaps)?",
            _comma_list(topic_subscribers.get(t.name, set())),
        ))

    # ------------------------------------------------------------
    # Level 1: SERVICE family
    # ------------------------------------------------------------

    service_providers: Dict[str, Set[str]] = {}
    service_clients: Dict[str, Set[str]] = {}

    for n in nodes:
        for sname in _effective_provides(n):
            service_providers.setdefault(sname, set()).add(n.name)
        for sname in _effective_uses(n):
            if sname.startswith("content("):
                continue
            service_clients.setdefault(sname, set()).add(n.name)

    for s in services:
        qs.append(Question(
            Level.RELATION,
            Category.SERVICE_TYPE,
            QType.OPEN,
            f"What is the type of service {s.name}?",
            _opt_unknown(getattr(s, "type", None)),
        ))
        qs.append(Question(
            Level.RELATION,
            Category.SERVICE,
            QType.OPEN,
            f"Which nodes provide service {s.name} (after resolving content(...) and remaps)?",
            _comma_list(service_providers.get(s.name, set())),
        ))
        qs.append(Question(
            Level.RELATION,
            Category.CLIENT,
            QType.OPEN,
            f"Which nodes use service {s.name} as a client (after resolving content(...) and remaps)?",
            _comma_list(service_clients.get(s.name, set())),
        ))

    # ------------------------------------------------------------
    # Level 1: POLICY family
    # ------------------------------------------------------------

    for p in qos_policies:
        qs.append(Question(
            level=Level.RELATION,
            category=Category.POLICY,
            qtype=QType.BOOL,
            question=f"Is there a policy instance called {p.name}?",
            answer="Yes",
        ))
        qs.append(Question(
            level=Level.RELATION,
            category=Category.POLICY,
            qtype=QType.OPEN,
            question=f"What is the kind of policy instance {p.name}?",
            answer=_opt_unknown(getattr(p, "kind", None)),
        ))
        settings = getattr(p, "settings", {}) or {}
        qs.append(Question(
            level=Level.RELATION,
            category=Category.POLICY,
            qtype=QType.OPEN,
            question=f"What settings are defined in policy instance {p.name}?",
            answer=_comma_list([f"{k}={v}" for k, v in settings.items()]),
        ))
        for k, v in settings.items():
            qs.append(Question(
                level=Level.RELATION,
                category=Category.POLICY,
                qtype=QType.OPEN,
                question=f"What is the value of setting {k} in policy instance {p.name}?",
                answer=str(v).strip() or _open_unknown(),
            ))

    # ------------------------------------------------------------
    # Level 1: TYPE ALIAS family
    # ------------------------------------------------------------

    for a in type_aliases:
        qs.append(Question(
            Level.RELATION,
            Category.TYPE_ALIAS,
            QType.BOOL,
            f"Is there a type alias called {a.name}?",
            "Yes",
        ))
        qs.append(Question(
            Level.RELATION,
            Category.TYPE_ALIAS,
            QType.OPEN,
            f"What is the definition of type alias {a.name}?",
            _opt_unknown(getattr(a, "definition", None)),
        ))

    # ------------------------------------------------------------
    # Level 1: MESSAGE ALIAS + FIELD family
    # ------------------------------------------------------------

    for m in message_aliases:
        qs.append(Question(
            Level.RELATION,
            Category.MESSAGE_ALIAS,
            QType.BOOL,
            f"Is there a message alias called {m.name}?",
            "Yes",
        ))
        qs.append(Question(
            Level.RELATION,
            Category.MESSAGE_ALIAS,
            QType.OPEN,
            f"What is the base message type of message alias {m.name}?",
            _opt_unknown(getattr(m, "base_type", None)),
        ))
        fields = getattr(m, "fields", []) or []
        qs.append(Question(
            Level.RELATION,
            Category.MESSAGE_FIELD,
            QType.OPEN,
            f"Which fields are defined in message alias {m.name}?",
            _comma_list([getattr(f, "name", "") for f in fields]),
        ))
        for f in fields:
            qs.append(Question(
                Level.RELATION,
                Category.MESSAGE_FIELD,
                QType.OPEN,
                f"What is the type of field {getattr(f, 'name', '')} in message alias {m.name}?",
                _opt_unknown(getattr(f, "type", None)),
            ))

    # ------------------------------------------------------------
    # Level 2: PATH questions
    # ------------------------------------------------------------

    for src in nodes:
        for dst in nodes:
            if src.name == dst.name:
                continue
            qs.append(Question(
                level=Level.PATH,
                category=Category.MESSAGE,
                qtype=QType.BOOL,
                question=(
                    f"Is there a communication path from node {src.name} "
                    f"to node {dst.name} via a topic or service?"
                ),
                answer=_bool_yes_no(_has_communication_path(src.name, dst.name, graph)),
            ))

    return qs