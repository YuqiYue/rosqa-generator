from __future__ import annotations

import re
from pathlib import Path

from .model import (
    ContextAssign,
    ContextDef,
    Graph,
    MessageAlias,
    MessageField,
    Node,
    NodeType,
    ParameterAssign,
    ParameterDef,
    QoSPolicy,
    Remap,
    Service,
    TFEdge,
    Topic,
    TypeAlias,
)

# ---------------------------
# Top level blocks and helpers
# ---------------------------

NODE_TYPE_BLOCK_RE = re.compile(
    r"node\s+type\s+(?P<name>\w+)\s*\{(?P<body>.*?)\}\s*(?:where\s*\{(?P<where>.*?)\}\s*)?",
    re.DOTALL,
)

SYSTEM_BLOCK_RE = re.compile(
    r"system\s*\{(?P<body>.*?)\}",
    re.DOTALL,
)

NODE_INSTANCE_BLOCK_RE = re.compile(
    r"node\s+instance\s+(?P<name>\w+)\s*:\s*(?P<type>\w+)\s*\{(?P<body>.*?)\}",
    re.DOTALL,
)

QOS_POLICY_BLOCK_RE = re.compile(
    r"policy\s+instance\s+(?P<name>\w+)\s*:\s*(?P<kind>\w+)\s*\{(?P<body>.*?)\}",
    re.DOTALL,
)

TYPE_ALIAS_RE = re.compile(
    r"type\s+alias\s+(?P<name>\w+)\s*:\s*(?P<def>.*?);",
    re.DOTALL,
)

MESSAGE_ALIAS_BLOCK_RE = re.compile(
    r"message\s+alias\s+(?P<name>\w+)\s*:\s*(?P<base>[\w/]+)\s*\{(?P<body>.*?)\}",
    re.DOTALL,
)

MESSAGE_FIELD_RE = re.compile(
    r"field\s+(?P<name>\w+)\s*:\s*(?P<type>[\w/\[\]]+)\s*;",
    re.DOTALL,
)


def _strip_comments(text: str) -> str:
    # Remove // comments
    return re.sub(r"//.*?$", "", text, flags=re.MULTILINE)


# ---------------------------
# Inside node type parsing
# ---------------------------

COMM_PUBLISH_RE = re.compile(
    r"publishes\s+to\s+(?P<name>[^\s:]+)\s*:\s*(?P<type>[^;]+)\s*;"
)
COMM_SUBSCRIBE_RE = re.compile(
    r"subscribes\s+to\s+(?P<name>[^\s:]+)\s*:\s*(?P<type>[^;]+)\s*;"
)

COMM_PROVIDES_RE = re.compile(
    r"provides\s+service\s+(?P<name>[^\s:]+)\s*:\s*(?P<type>[^;]+)\s*;"
)
COMM_USES_RE = re.compile(
    r"uses\s+service\s+(?P<name>[^\s:]+)\s*:\s*(?P<type>[^;]+)\s*;"
)

# consumes service content(distance_to_obstacle_service): hector_nav_msgs/GetDistanceToObstacle;
COMM_CONSUMES_CONTENT_RE = re.compile(
    r"consumes\s+service\s+content\((?P<param>\w+)\)\s*:\s*(?P<type>[^;]+)\s*;"
)

# param elbow_joint/max_acceleration: double where {_ >= 0};
# optional param elbow_joint/max_velocity: double = 1.2211;
PARAM_DEF_RE = re.compile(
    r"(?P<optional>optional\s+)?param\s+"
    r"(?P<name>[\w/]+)\s*:\s*(?P<type>[\w/]+)"
    r"(?:\s*=\s*(?P<default>[^;]+?))?"
    r"(?:\s+where\s*\{(?P<constraint>[^}]*)\})?"
    r"\s*;",
    re.DOTALL,
)

# context is_simulation: bool;
CONTEXT_DEF_RE = re.compile(
    r"context\s+(?P<name>\w+)\s*:\s*(?P<type>[\w/]+)\s*;",
)

# Attachments like @qos{best_effort_qos} or @color_format{Grayscale}
ATTACHMENT_RE = re.compile(
    r"@(?P<key>\w+)\s*\{\s*(?P<value>[^}]+)\s*\}\s*",
)

# TF lines
TF_BROADCAST_RE = re.compile(r"broadcast\s+(?P<frm>[\w/]+)\s+to\s+(?P<to>[\w/]+)\s*;")
TF_LISTENS_RE = re.compile(r"listens\s+(?P<frm>[\w/]+)\s+to\s+(?P<to>[\w/]+)\s*;")


# ---------------------------
# Inside system instance parsing
# ---------------------------

PARAM_ASSIGN_RE = re.compile(
    r"param\s+(?P<name>[\w/]+)\s*=\s*(?P<value>[^;]+)\s*;",
    re.DOTALL,
)

CONTEXT_ASSIGN_RE = re.compile(
    r"context\s+(?P<name>\w+)\s*=\s*(?P<value>[^;]+)\s*;",
    re.DOTALL,
)

REMAP_RE = re.compile(
    r"remap\s+(?P<frm>[^\s]+)\s+to\s+(?P<to>[^\s;]+)\s*;",
)


# ---------------------------
# QoS policy parsing
# ---------------------------

QOS_SETTING_RE = re.compile(
    r"setting\s+(?P<key>\w+)\s*=\s*(?P<value>[^;]+)\s*;",
)


def _parse_qos_policies(text: str, g: Graph) -> None:
    for m in QOS_POLICY_BLOCK_RE.finditer(text):
        name = m.group("name")
        kind = m.group("kind")
        body = m.group("body")

        pol = QoSPolicy(name=name, kind=kind)
        for sm in QOS_SETTING_RE.finditer(body):
            pol.settings[sm.group("key")] = sm.group("value").strip()

        g.qos_policies[name] = pol


def _parse_type_aliases(text: str, g: Graph) -> None:
    for m in TYPE_ALIAS_RE.finditer(text):
        name = m.group("name")
        definition = m.group("def").strip()
        g.type_aliases[name] = TypeAlias(name=name, definition=definition)


def _parse_message_aliases(text: str, g: Graph) -> None:
    for m in MESSAGE_ALIAS_BLOCK_RE.finditer(text):
        name = m.group("name")
        base = m.group("base")
        body = m.group("body")

        ma = MessageAlias(name=name, base_type=base)
        for fm in MESSAGE_FIELD_RE.finditer(body):
            ma.fields.append(MessageField(name=fm.group("name"), type=fm.group("type")))
        g.message_aliases[name] = ma


def _parse_node_types(text: str, g: Graph) -> None:
    for m in NODE_TYPE_BLOCK_RE.finditer(text):
        name = m.group("name")
        body = m.group("body")
        where_block = m.group("where")

        nt = NodeType(name=name)
        nt.where_block = where_block.strip() if where_block else None

        # --- comm (topics/services) ---
        for pm in COMM_PUBLISH_RE.finditer(body):
            topic = pm.group("name").strip()
            typ = pm.group("type").strip() or None
            nt.publishes.add((topic, typ))
            if topic not in g.topics:
                g.topics[topic] = Topic(name=topic, type=typ)

        for sm in COMM_SUBSCRIBE_RE.finditer(body):
            topic = sm.group("name").strip()
            typ = sm.group("type").strip() or None
            nt.subscribes.add((topic, typ))
            if topic not in g.topics:
                g.topics[topic] = Topic(name=topic, type=typ)

        for prm in COMM_PROVIDES_RE.finditer(body):
            srv = prm.group("name").strip()
            typ = prm.group("type").strip() or None
            nt.provides.add((srv, typ))
            if srv not in g.services:
                g.services[srv] = Service(name=srv, type=typ)

        for um in COMM_USES_RE.finditer(body):
            srv = um.group("name").strip()
            typ = um.group("type").strip() or None
            nt.uses.add((srv, typ))
            if srv not in g.services:
                g.services[srv] = Service(name=srv, type=typ)

        # --- dynamic content(service) ---
        for cm in COMM_CONSUMES_CONTENT_RE.finditer(body):
            param_name = cm.group("param").strip()
            srv_type = cm.group("type").strip() or None
            nt.consumes_content_services.add(("<content>", param_name, srv_type))

        # --- parameters ---
        for dm in PARAM_DEF_RE.finditer(body):
            p = ParameterDef(
                name=dm.group("name").strip(),
                type=dm.group("type").strip(),
                optional=bool(dm.group("optional")),
                default=dm.group("default").strip() if dm.group("default") else None,
                constraint=dm.group("constraint").strip() if dm.group("constraint") else None,
            )
            nt.parameters[p.name] = p

        # --- contexts ---
        for xm in CONTEXT_DEF_RE.finditer(body):
            c = ContextDef(name=xm.group("name").strip(), type=xm.group("type").strip())
            nt.contexts[c.name] = c

        # --- attachments ---
        for am in ATTACHMENT_RE.finditer(body):
            key = am.group("key").strip()
            value = am.group("value").strip()
            if key == "qos":
                nt.qos_attachments.add(value)
            else:
                nt.other_attachments[key] = value

        # --- TF ---
        for tm in TF_BROADCAST_RE.finditer(body):
            nt.tf_edges.append(TFEdge(relation="broadcast", frm=tm.group("frm"), to=tm.group("to")))

        for tm in TF_LISTENS_RE.finditer(body):
            nt.tf_edges.append(TFEdge(relation="listens", frm=tm.group("frm"), to=tm.group("to")))

        g.node_types[name] = nt


def _parse_system_instances(text: str, g: Graph) -> None:
    """
    System block is OPTIONAL.

    If missing: do nothing (graph.nodes remains empty).
    If present: parse node instances, assignments, and remaps.
    """
    m = SYSTEM_BLOCK_RE.search(text)
    if not m:
        return  # IMPORTANT CHANGE: no crash if no system block

    sys_body = m.group("body")

    for im in NODE_INSTANCE_BLOCK_RE.finditer(sys_body):
        inst_name = im.group("name")
        type_name = im.group("type")
        body = im.group("body")

        nt = g.node_types.get(type_name)
        if not nt:
            # Unknown node type; skip instance rather than fail
            continue

        node = Node(name=inst_name, node_type=nt)

        # parameter assignments
        for pm in PARAM_ASSIGN_RE.finditer(body):
            key = pm.group("name").strip()
            val = pm.group("value").strip()
            node.param_assigns[key] = ParameterAssign(name=key, value=val)

        # context assignments
        for cm in CONTEXT_ASSIGN_RE.finditer(body):
            key = cm.group("name").strip()
            val = cm.group("value").strip()
            node.context_assigns[key] = ContextAssign(name=key, value=val)

        # remaps
        for rm in REMAP_RE.finditer(body):
            node.remaps.append(Remap(frm=rm.group("frm").strip(), to=rm.group("to").strip()))

        g.nodes[node.name] = node


def load_graph_from_rospec(path: Path) -> Graph:
    text = _strip_comments(path.read_text())

    g = Graph()

    _parse_qos_policies(text, g)
    _parse_type_aliases(text, g)
    _parse_message_aliases(text, g)

    _parse_node_types(text, g)
    _parse_system_instances(text, g)

    return g