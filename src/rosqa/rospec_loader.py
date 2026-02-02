from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple, Union

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

# ===========================
# Helpers: comments + braces
# ===========================

def _strip_comments(text: str) -> str:
    # Remove // comments (ROSpec uses //)
    return re.sub(r"//.*?$", "", text, flags=re.MULTILINE)


def _extract_brace_block(text: str, lbrace_idx: int) -> Tuple[str, int]:
    """
    Given text and an index pointing at '{', return (block_content, rbrace_idx)
    where block_content is inside braces, and rbrace_idx is the index of the matching '}'.
    Supports nested braces.
    """
    if lbrace_idx < 0 or lbrace_idx >= len(text) or text[lbrace_idx] != "{":
        raise ValueError("Expected '{' at lbrace_idx")

    depth = 0
    i = lbrace_idx
    begin = lbrace_idx + 1

    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[begin:i], i
        i += 1

    raise ValueError("Unbalanced braces in ROSpec input")


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _read_keyword_then_block(text: str, i: int, keyword: str) -> Tuple[Optional[str], int]:
    """
    If text at/after i starts with `keyword` (as a word), then parse `{...}` and return (body, new_i).
    Otherwise return (None, i).
    """
    j = _skip_ws(text, i)
    # word boundary match
    if not re.match(rf"{re.escape(keyword)}\b", text[j:]):
        return None, i

    j += len(keyword)
    j = _skip_ws(text, j)
    if j >= len(text) or text[j] != "{":
        return None, i

    body, rbrace = _extract_brace_block(text, j)
    return body, rbrace + 1


# ===========================
# Regex for non-nested lines
# ===========================

# Communication lines
# Supports:
#   publishes /a : T;
#   publishes to /a : T;
#   subscribes /b : T;
#   provides service /s : T;
#   provides /s : T;
#   uses service /s : T;
#   uses /s : T;

_COMM_NAME = r"(?P<name>[^:;]+?)"
_COMM_TYPE = r"(?P<type>[^;]+?)"

COMM_PUBLISH_RE = re.compile(
    rf"\bpublishes(?:\s+to)?\s+{_COMM_NAME}\s*:\s*{_COMM_TYPE}\s*;",
    flags=re.MULTILINE,
)
COMM_SUBSCRIBE_RE = re.compile(
    rf"\bsubscribes(?:\s+to)?\s+{_COMM_NAME}\s*:\s*{_COMM_TYPE}\s*;",
    flags=re.MULTILINE,
)
COMM_PROVIDES_RE = re.compile(
    rf"\bprovides(?:\s+service)?(?:\s+to)?\s+{_COMM_NAME}\s*:\s*{_COMM_TYPE}\s*;",
    flags=re.MULTILINE,
)
COMM_USES_RE = re.compile(
    rf"\buses(?:\s+service)?(?:\s+to)?\s+{_COMM_NAME}\s*:\s*{_COMM_TYPE}\s*;",
    flags=re.MULTILINE,
)

# consumes service content(distance_to_obstacle_service): hector_nav_msgs/GetDistanceToObstacle;
COMM_CONSUMES_CONTENT_RE = re.compile(
    r"\bconsumes\s+service\s+content\((?P<param>\w+)\)\s*:\s*(?P<type>[^;]+?)\s*;",
    flags=re.MULTILINE,
)

# Parameters and contexts
# param elbow_joint/max_acceleration: double where {_ >= 0};
# optional param elbow_joint/max_velocity: double = 1.2211;
PARAM_DEF_RE = re.compile(
    r"(?P<optional>optional\s+)?param\s+"
    r"(?P<name>[\w/]+)\s*:\s*(?P<type>[\w/]+)"
    r"(?:\s*=\s*(?P<default>[^;]+?))?"
    r"(?:\s+where\s*\{(?P<constraint>[^}]*)\})?"
    r"\s*;",
    flags=re.DOTALL,
)

CONTEXT_DEF_RE = re.compile(
    r"context\s+(?P<name>\w+)\s*:\s*(?P<type>[\w/]+)\s*;",
    flags=re.MULTILINE,
)

# Attachments like @qos{best_effort_qos} or @color_format{Grayscale}
ATTACHMENT_RE = re.compile(
    r"@(?P<key>\w+)\s*\{\s*(?P<value>[^}]+)\s*\}",
    flags=re.MULTILINE,
)

# TF
TF_BROADCAST_RE = re.compile(
    r"\bbroadcast\s+(?P<frm>[\w/]+)\s+to\s+(?P<to>[\w/]+)\s*;",
    flags=re.MULTILINE,
)
TF_LISTENS_RE = re.compile(
    r"\blistens\s+(?P<frm>[\w/]+)\s+to\s+(?P<to>[\w/]+)\s*;",
    flags=re.MULTILINE,
)

# QoS policies
QOS_POLICY_HEAD_RE = re.compile(
    r"\bpolicy\s+instance\s+(?P<name>\w+)\s*:\s*(?P<kind>\w+)\s*\{",
    flags=re.MULTILINE,
)
QOS_SETTING_RE = re.compile(
    r"\bsetting\s+(?P<key>\w+)\s*=\s*(?P<value>[^;]+?)\s*;",
    flags=re.MULTILINE,
)

# Type alias
TYPE_ALIAS_RE = re.compile(
    r"\btype\s+alias\s+(?P<name>\w+)\s*:\s*(?P<def>.*?);",
    flags=re.DOTALL,
)

# Message alias
MESSAGE_ALIAS_HEAD_RE = re.compile(
    r"\bmessage\s+alias\s+(?P<name>\w+)\s*:\s*(?P<base>[\w/]+)\s*\{",
    flags=re.MULTILINE,
)
MESSAGE_FIELD_RE = re.compile(
    r"\bfield\s+(?P<name>\w+)\s*:\s*(?P<type>[\w/\[\]]+)\s*;",
    flags=re.MULTILINE,
)

# System and node instances
SYSTEM_HEAD_RE = re.compile(r"\bsystem\s*\{", flags=re.MULTILINE)

NODE_INSTANCE_HEAD_RE = re.compile(
    r"\bnode\s+instance\s+(?P<name>\w+)\s*:\s*(?P<type>\w+)\s*\{",
    flags=re.MULTILINE,
)

PARAM_ASSIGN_RE = re.compile(
    r"\bparam\s+(?P<name>[\w/]+)\s*=\s*(?P<value>[^;]+?)\s*;",
    flags=re.DOTALL,
)
CONTEXT_ASSIGN_RE = re.compile(
    r"\bcontext\s+(?P<name>\w+)\s*=\s*(?P<value>[^;]+?)\s*;",
    flags=re.DOTALL,
)
REMAP_RE = re.compile(
    r"\bremap\s+(?P<frm>[^\s]+)\s+to\s+(?P<to>[^\s;]+)\s*;",
    flags=re.MULTILINE,
)

# Node types: we only use a HEAD regex + brace extraction (important!)
NODE_TYPE_HEAD_RE = re.compile(
    r"\bnode\s+type\s+(?P<name>\w+)\s*\{",
    flags=re.MULTILINE,
)


# ===========================
# Parsers
# ===========================

def _parse_qos_policies(text: str, g: Graph) -> None:
    for m in QOS_POLICY_HEAD_RE.finditer(text):
        name = m.group("name")
        kind = m.group("kind")
        lbrace_idx = m.end() - 1  # points to '{'
        body, rbrace_idx = _extract_brace_block(text, lbrace_idx)

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
    for m in MESSAGE_ALIAS_HEAD_RE.finditer(text):
        name = m.group("name")
        base = m.group("base")
        lbrace_idx = m.end() - 1
        body, _ = _extract_brace_block(text, lbrace_idx)

        ma = MessageAlias(name=name, base_type=base)
        for fm in MESSAGE_FIELD_RE.finditer(body):
            ma.fields.append(MessageField(name=fm.group("name"), type=fm.group("type")))
        g.message_aliases[name] = ma


def _parse_node_types(text: str, g: Graph) -> None:
    """
    Parse node types using brace matching to correctly handle nested blocks such as:
      communication { ... }
      attachments { ... }
      tf { ... }
    Also supports an optional trailing:
      where { ... }
    """
    for m in NODE_TYPE_HEAD_RE.finditer(text):
        name = m.group("name")
        lbrace_idx = m.end() - 1
        body, rbrace_idx = _extract_brace_block(text, lbrace_idx)

        # Optional: where { ... } after the node type block
        where_body, next_i = _read_keyword_then_block(text, rbrace_idx + 1, "where")

        nt = NodeType(name=name)
        nt.where_block = where_body.strip() if where_body else None

        # Parse communications anywhere inside body (works even if they are inside "communication { ... }")
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

        # Dynamic content(service) constructs
        for cm in COMM_CONSUMES_CONTENT_RE.finditer(body):
            param_name = cm.group("param").strip()
            srv_type = cm.group("type").strip() or None
            nt.consumes_content_services.add(("<content>", param_name, srv_type))

        # Parameters
        for dm in PARAM_DEF_RE.finditer(body):
            p = ParameterDef(
                name=dm.group("name").strip(),
                type=dm.group("type").strip(),
                optional=bool(dm.group("optional")),
                default=dm.group("default").strip() if dm.group("default") else None,
                constraint=dm.group("constraint").strip() if dm.group("constraint") else None,
            )
            nt.parameters[p.name] = p

        # Contexts
        for xm in CONTEXT_DEF_RE.finditer(body):
            c = ContextDef(name=xm.group("name").strip(), type=xm.group("type").strip())
            nt.contexts[c.name] = c

        # Attachments
        for am in ATTACHMENT_RE.finditer(body):
            key = am.group("key").strip()
            value = am.group("value").strip()
            if key == "qos":
                nt.qos_attachments.add(value)
            else:
                nt.other_attachments[key] = value

        # TF
        for tm in TF_BROADCAST_RE.finditer(body):
            nt.tf_edges.append(TFEdge(relation="broadcast", frm=tm.group("frm"), to=tm.group("to")))
        for tm in TF_LISTENS_RE.finditer(body):
            nt.tf_edges.append(TFEdge(relation="listens", frm=tm.group("frm"), to=tm.group("to")))

        g.node_types[name] = nt


def _parse_system_instances(text: str, g: Graph) -> None:
    """
    System block is OPTIONAL.
    If missing: do nothing (graph.nodes remains empty).
    If present: parse node instances, assignments, and remaps using brace matching.
    """
    m = SYSTEM_HEAD_RE.search(text)
    if not m:
        return

    lbrace_idx = m.end() - 1
    sys_body, _ = _extract_brace_block(text, lbrace_idx)

    for im in NODE_INSTANCE_HEAD_RE.finditer(sys_body):
        inst_name = im.group("name")
        type_name = im.group("type")
        inst_lbrace = im.end() - 1
        body, _ = _extract_brace_block(sys_body, inst_lbrace)

        nt = g.node_types.get(type_name)
        if not nt:
            # Unknown node type; skip instance rather than fail
            continue

        node = Node(name=inst_name, node_type=nt)

        for pm in PARAM_ASSIGN_RE.finditer(body):
            key = pm.group("name").strip()
            val = pm.group("value").strip()
            node.param_assigns[key] = ParameterAssign(name=key, value=val)

        for cm in CONTEXT_ASSIGN_RE.finditer(body):
            key = cm.group("name").strip()
            val = cm.group("value").strip()
            node.context_assigns[key] = ContextAssign(name=key, value=val)

        for rm in REMAP_RE.finditer(body):
            node.remaps.append(Remap(frm=rm.group("frm").strip(), to=rm.group("to").strip()))

        g.nodes[node.name] = node


# ===========================
# Public API
# ===========================

def load_graph_from_rospec(path: Union[Path, str]) -> Graph:
    """
    Load a ROSpec file and parse into a Graph.
    Accepts a Path or a string path.
    """
    p = Path(path) if not isinstance(path, Path) else path
    text = _strip_comments(p.read_text(encoding="utf-8"))

    g = Graph()

    _parse_qos_policies(text, g)
    _parse_type_aliases(text, g)
    _parse_message_aliases(text, g)

    _parse_node_types(text, g)
    _parse_system_instances(text, g)

    return g