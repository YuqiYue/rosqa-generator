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
# Sampling / budgeting helpers (to control question count)
# -----------------------

def _stable_seed_from_text(text: str) -> int:
    # Deterministic seed from a string (no external deps)
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return int(h)


def _sample(items: List[str], k: int, rng: random.Random) -> List[str]:
    if k <= 0:
        return []
    if len(items) <= k:
        return list(items)
    return rng.sample(items, k)


def _pairs(a: List[str], b: List[str]) -> List[Tuple[str, str]]:
    return [(x, y) for x in a for y in b]


def _stop_if_budget(qs: List[Question], budget: Optional[int]) -> bool:
    return budget is not None and len(qs) >= budget


def _add(qs: List[Question], q: Question, budget: Optional[int]) -> None:
    if budget is None or len(qs) < budget:
        qs.append(q)


# -----------------------
# “Families” of questions
# -----------------------

def generate_questions(
    graph: Graph,
    *,
    include_negative_entities: bool = True,
    negative_entities_per_file: int = 5,
    # New knobs to control scale
    target_questions: Optional[int] = None,  # e.g., 200
    seed: Optional[int] = None,              # deterministic sampling across runs
    profile: str = "balanced",               # currently: "balanced" | "full"
) -> List[Question]:
    """
    Generate a list of Question objects.

    - profile="full": generate everything (may be large)
    - profile="balanced": cap size via sampling to ~target_questions (if provided)
    """
    qs: List[Question] = []

    nodes = list(getattr(graph, "nodes", {}).values())
    node_types = list(getattr(graph, "node_types", {}).values())
    topics = list(getattr(graph, "topics", {}).values())
    services = list(getattr(graph, "services", {}).values())
    qos_policies = list(getattr(graph, "qos_policies", {}).values())
    type_aliases = list(getattr(graph, "type_aliases", {}).values())
    message_aliases = list(getattr(graph, "message_aliases", {}).values())

    # Prepare RNG
    if seed is None:
        seed = _stable_seed_from_text(
            "||".join(
                [n.name for n in nodes]
                + [nt.name for nt in node_types]
                + [t.name for t in topics]
                + [s.name for s in services]
            )
        )
    rng = random.Random(seed)

    # Balanced defaults if target not provided
    if profile == "balanced" and target_questions is None:
        target_questions = 200

    # Budget slices for balanced mode (soft guidance)
    if profile == "balanced":
        budget_total = target_questions
        budget_l0 = int(budget_total * 0.20)   # ~40
        budget_nt = int(budget_total * 0.40)   # ~80
        budget_ni = int(budget_total * 0.25)   # ~50
        budget_l2 = budget_total - (budget_l0 + budget_nt + budget_ni)  # ~30
    else:
        budget_total = None
        budget_l0 = None
        budget_nt = None
        budget_ni = None
        budget_l2 = None

    # ------------------------------------------------------------
    # Level 0: ENTITY existence + kind (node/topic/service)
    # ------------------------------------------------------------
    entity_names = [n.name for n in nodes] + [t.name for t in topics] + [s.name for s in services]

    # L0 sampling: choose subset in balanced mode
    if profile == "balanced":
        # K entities -> 2K questions + negs
        k_entities = max(0, (budget_l0 - max(0, negative_entities_per_file)) // 2)
        entity_names_l0 = _sample(entity_names, k_entities, rng)
        neg_count = min(negative_entities_per_file, max(0, budget_l0 - 2 * len(entity_names_l0)))
    else:
        entity_names_l0 = entity_names
        neg_count = negative_entities_per_file if include_negative_entities else 0

    if include_negative_entities and neg_count > 0:
        for e in _generate_fake_entities(entity_names, count=neg_count):
            _add(qs, Question(Level.ENTITY, Category.ENTITY, QType.BOOL, f"Is there a ROS2 entity called {e}?", "No"), budget_total)

    for e in entity_names_l0:
        _add(qs, Question(Level.ENTITY, Category.ENTITY, QType.BOOL, f"Is there a ROS2 entity called {e}?", "Yes"), budget_total)
        _add(
            qs,
            Question(
                Level.ENTITY,
                Category.ENTITY,
                QType.MCQ,
                f"What kind of ROS2 entity is {e}? Possible answers: 1- ROS topic, 2- ROS service, 3- ROS node.",
                _entity_kind(e, graph),
            ),
            budget_total,
        )

    if _stop_if_budget(qs, budget_total):
        return qs

    # ------------------------------------------------------------
    # Level 1: NODE TYPE family
    # ------------------------------------------------------------
    node_type_pool = node_types
    if profile == "balanced":
        # choose up to 4 node types to expand strongly
        node_type_pool = _sample(node_types, k=min(4, len(node_types)), rng=rng)

    # To generate more questions: add BOOL pairwise relations for topics/services
    all_topic_names = sorted({t.name for t in topics})
    all_service_names = sorted({s.name for s in services})

    for nt in node_type_pool:
        if _stop_if_budget(qs, budget_total):
            break

        _add(
            qs,
            Question(Level.RELATION, Category.NODE_TYPE, QType.BOOL, f"Is there a ROSpec node type called {nt.name}?", "Yes"),
            budget_total,
        )

        # Parameters (defs)
        param_defs = getattr(nt, "parameters", {}) or {}
        _add(
            qs,
            Question(Level.RELATION, Category.PARAMETER, QType.OPEN, f"Which parameters are defined in node type {nt.name}?", _comma_list(param_defs.keys())),
            budget_total,
        )

        for p in param_defs.values():
            if _stop_if_budget(qs, budget_total):
                break
            _add(
                qs,
                Question(Level.RELATION, Category.PARAMETER, QType.OPEN, f"What is the type of parameter {p.name} in node type {nt.name}?", _opt_unknown(getattr(p, "type", None))),
                budget_total,
            )
            _add(
                qs,
                Question(Level.RELATION, Category.PARAMETER, QType.BOOL, f"Is parameter {p.name} optional in node type {nt.name}?", _bool_yes_no(bool(getattr(p, "optional", False)))),
                budget_total,
            )
            default = getattr(p, "default", None)
            _add(
                qs,
                Question(Level.RELATION, Category.PARAMETER, QType.OPEN, f"What is the default value of parameter {p.name} in node type {nt.name}?", _opt_empty(default)),
                budget_total,
            )
            constraint = getattr(p, "constraint", None)
            _add(
                qs,
                Question(Level.RELATION, Category.WHERE, QType.BOOL, f"Does parameter {p.name} in node type {nt.name} have a constraint?", _bool_yes_no(bool(constraint))),
                budget_total,
            )
            if constraint:
                _add(
                    qs,
                    Question(Level.RELATION, Category.WHERE, QType.OPEN, f"What is the constraint of parameter {p.name} in node type {nt.name}?", str(constraint).strip() or _open_empty()),
                    budget_total,
                )

        # Contexts (defs)
        ctx_defs = getattr(nt, "contexts", {}) or {}
        _add(
            qs,
            Question(Level.RELATION, Category.CONTEXT, QType.OPEN, f"Which contexts are defined in node type {nt.name}?", _comma_list(ctx_defs.keys())),
            budget_total,
        )
        for c in ctx_defs.values():
            if _stop_if_budget(qs, budget_total):
                break
            _add(
                qs,
                Question(Level.RELATION, Category.CONTEXT, QType.OPEN, f"What is the type of context {c.name} in node type {nt.name}?", _opt_unknown(getattr(c, "type", None))),
                budget_total,
            )

        # Attachments
        qos_att = sorted(getattr(nt, "qos_attachments", set()) or set())
        other_att = getattr(nt, "other_attachments", {}) or {}

        _add(
            qs,
            Question(Level.RELATION, Category.ATTACHMENT, QType.OPEN, f"Which QoS policy tags are attached in node type {nt.name}?", _comma_list(qos_att)),
            budget_total,
        )
        _add(
            qs,
            Question(Level.RELATION, Category.ATTACHMENT, QType.OPEN, f"Which non-QoS attachments are declared in node type {nt.name}?", _comma_list([f"{k}={v}" for k, v in other_att.items()])),
            budget_total,
        )
        for k, v in other_att.items():
            if _stop_if_budget(qs, budget_total):
                break
            _add(
                qs,
                Question(Level.RELATION, Category.ATTACHMENT, QType.OPEN, f"What is the value of attachment @{k} in node type {nt.name}?", str(v).strip() or _open_empty()),
                budget_total,
            )

        # Declared connections OPEN summaries
        pubs = [name for (name, _t) in (getattr(nt, "publishes", set()) or set())]
        subs = [name for (name, _t) in (getattr(nt, "subscribes", set()) or set())]
        prov = [name for (name, _t) in (getattr(nt, "provides", set()) or set())]
        uses = [name for (name, _t) in (getattr(nt, "uses", set()) or set())]
        consumes_content = list(getattr(nt, "consumes_content_services", set()) or set())

        _add(qs, Question(Level.RELATION, Category.PUBLISH, QType.OPEN, f"Which topics can node type {nt.name} publish to (as declared)?", _comma_list(pubs)), budget_total)
        _add(qs, Question(Level.RELATION, Category.SUBSCRIBE, QType.OPEN, f"Which topics can node type {nt.name} subscribe to (as declared)?", _comma_list(subs)), budget_total)
        _add(qs, Question(Level.RELATION, Category.SERVICE, QType.OPEN, f"Which services can node type {nt.name} provide (as declared)?", _comma_list(prov)), budget_total)
        _add(qs, Question(Level.RELATION, Category.CLIENT, QType.OPEN, f"Which services can node type {nt.name} use (as declared)?", _comma_list(uses)), budget_total)

        # Where block
        where_block = getattr(nt, "where_block", None)
        _add(
            qs,
            Question(Level.RELATION, Category.WHERE, QType.BOOL, f"Does node type {nt.name} declare a where-clause?", _bool_yes_no(bool(where_block))),
            budget_total,
        )
        _add(
            qs,
            Question(Level.RELATION, Category.WHERE, QType.OPEN, f"What is the where-clause of node type {nt.name}?", _opt_empty(where_block)),
            budget_total,
        )

        # content(...) declarations
        for (_ph, param_name, srv_type) in consumes_content:
            if _stop_if_budget(qs, budget_total):
                break
            _add(
                qs,
                Question(Level.RELATION, Category.CONTENT_SERVICE, QType.OPEN, f"Which parameter provides the consumed service name via content(...) in node type {nt.name}?", str(param_name).strip() or _open_unknown()),
                budget_total,
            )
            _add(
                qs,
                Question(Level.RELATION, Category.CONTENT_SERVICE, QType.OPEN, f"What is the declared type of the consumed content-based service in node type {nt.name}?", _opt_unknown(srv_type)),
                budget_total,
            )

        # TF edges
        tf_edges = getattr(nt, "tf_edges", []) or []
        _add(
            qs,
            Question(Level.RELATION, Category.TF, QType.OPEN, f"What TF relations are declared in node type {nt.name}?", _comma_list([f"{e.relation} {e.frm}->{e.to}" for e in tf_edges])),
            budget_total,
        )

        # Extra BOOL expansion (balanced mode)
        if profile == "balanced":
            declared_pub = set(pubs)
            declared_sub = set(subs)
            declared_prov = set(prov)
            declared_use = set(uses)

            pub_samples = _sample(all_topic_names, k=8, rng=rng)
            sub_samples = _sample(all_topic_names, k=8, rng=rng)
            prov_samples = _sample(all_service_names, k=4, rng=rng)
            use_samples = _sample(all_service_names, k=4, rng=rng)

            for tname in pub_samples:
                if _stop_if_budget(qs, budget_total):
                    break
                _add(
                    qs,
                    Question(Level.RELATION, Category.PUBLISH, QType.BOOL, f"Does node type {nt.name} publish to topic {tname} (as declared)?", _bool_yes_no(tname in declared_pub)),
                    budget_total,
                )
            for tname in sub_samples:
                if _stop_if_budget(qs, budget_total):
                    break
                _add(
                    qs,
                    Question(Level.RELATION, Category.SUBSCRIBE, QType.BOOL, f"Is node type {nt.name} subscribed to topic {tname} (as declared)?", _bool_yes_no(tname in declared_sub)),
                    budget_total,
                )
            for sname in prov_samples:
                if _stop_if_budget(qs, budget_total):
                    break
                _add(
                    qs,
                    Question(Level.RELATION, Category.SERVICE, QType.BOOL, f"Does node type {nt.name} provide service {sname} (as declared)?", _bool_yes_no(sname in declared_prov)),
                    budget_total,
                )
            for sname in use_samples:
                if _stop_if_budget(qs, budget_total):
                    break
                _add(
                    qs,
                    Question(Level.RELATION, Category.CLIENT, QType.BOOL, f"Does node type {nt.name} use service {sname} as a client (as declared)?", _bool_yes_no(sname in declared_use)),
                    budget_total,
                )

    if _stop_if_budget(qs, budget_total):
        return qs

    # ------------------------------------------------------------
    # Level 1: NODE INSTANCE family
    # ------------------------------------------------------------
    node_instance_pool = nodes
    if profile == "balanced":
        node_instance_pool = _sample(nodes, k=min(3, len(nodes)), rng=rng)

    for n in node_instance_pool:
        if _stop_if_budget(qs, budget_total):
            break

        _add(
            qs,
            Question(Level.RELATION, Category.NODE_INSTANCE, QType.OPEN, f"What is the node type of node instance {n.name}?", _opt_unknown(getattr(getattr(n, "node_type", None), "name", None))),
            budget_total,
        )

        assigns = getattr(n, "param_assigns", {}) or {}
        _add(qs, Question(Level.RELATION, Category.PARAMETER_ASSIGN, QType.OPEN, f"Which parameters are assigned in node instance {n.name}?", _comma_list(assigns.keys())), budget_total)
        for k, v in assigns.items():
            if _stop_if_budget(qs, budget_total):
                break
            _add(
                qs,
                Question(Level.RELATION, Category.PARAMETER_ASSIGN, QType.OPEN, f"What value is assigned to parameter {k} in node instance {n.name}?", _strip_quotes(v.value) if v and getattr(v, "value", None) is not None else _open_unknown()),
                budget_total,
            )

        cassigns = getattr(n, "context_assigns", {}) or {}
        _add(qs, Question(Level.RELATION, Category.CONTEXT_ASSIGN, QType.OPEN, f"Which contexts are assigned in node instance {n.name}?", _comma_list(cassigns.keys())), budget_total)
        for k, v in cassigns.items():
            if _stop_if_budget(qs, budget_total):
                break
            _add(
                qs,
                Question(Level.RELATION, Category.CONTEXT_ASSIGN, QType.OPEN, f"What value is assigned to context {k} in node instance {n.name}?", _strip_quotes(v.value) if v and getattr(v, "value", None) is not None else _open_unknown()),
                budget_total,
            )

        remaps = getattr(n, "remaps", []) or []
        _add(qs, Question(Level.RELATION, Category.REMAP, QType.OPEN, f"Which remaps are declared in node instance {n.name}?", _comma_list([f"{r.frm}->{r.to}" for r in remaps])), budget_total)
        for r in remaps:
            if _stop_if_budget(qs, budget_total):
                break
            _add(qs, Question(Level.RELATION, Category.REMAP, QType.BOOL, f"Does node instance {n.name} remap {r.frm} to {r.to}?", "Yes"), budget_total)

        # Effective resolved connections (OPEN)
        eff_pub = _effective_publishes(n)
        eff_sub = _effective_subscribes(n)
        eff_prov = _effective_provides(n)
        eff_use = _effective_uses(n)

        _add(qs, Question(Level.RELATION, Category.PUBLISH, QType.OPEN, f"To which topics can node {n.name} publish (after resolving content(...) and remaps)?", _comma_list(eff_pub)), budget_total)
        _add(qs, Question(Level.RELATION, Category.SUBSCRIBE, QType.OPEN, f"To which topics is node {n.name} subscribed (after resolving content(...) and remaps)?", _comma_list(eff_sub)), budget_total)
        _add(qs, Question(Level.RELATION, Category.SERVICE, QType.OPEN, f"Which services does node {n.name} provide (after resolving content(...) and remaps)?", _comma_list(eff_prov)), budget_total)
        _add(qs, Question(Level.RELATION, Category.CLIENT, QType.OPEN, f"Which services does node {n.name} use as a client (after resolving content(...) and remaps)?", _comma_list(eff_use)), budget_total)

        # Extra BOOL expansion for instances (balanced)
        if profile == "balanced":
            pub_samples = _sample(all_topic_names, k=6, rng=rng)
            sub_samples = _sample(all_topic_names, k=6, rng=rng)
            prov_samples = _sample(all_service_names, k=3, rng=rng)
            use_samples = _sample(all_service_names, k=3, rng=rng)

            for tname in pub_samples:
                if _stop_if_budget(qs, budget_total):
                    break
                _add(qs, Question(Level.RELATION, Category.PUBLISH, QType.BOOL, f"Does node {n.name} publish to topic {tname} (after resolving content(...) and remaps)?", _bool_yes_no(tname in eff_pub)), budget_total)
            for tname in sub_samples:
                if _stop_if_budget(qs, budget_total):
                    break
                _add(qs, Question(Level.RELATION, Category.SUBSCRIBE, QType.BOOL, f"Is node {n.name} subscribed to topic {tname} (after resolving content(...) and remaps)?", _bool_yes_no(tname in eff_sub)), budget_total)
            for sname in prov_samples:
                if _stop_if_budget(qs, budget_total):
                    break
                _add(qs, Question(Level.RELATION, Category.SERVICE, QType.BOOL, f"Does node {n.name} provide service {sname} (after resolving content(...) and remaps)?", _bool_yes_no(sname in eff_prov)), budget_total)
            for sname in use_samples:
                if _stop_if_budget(qs, budget_total):
                    break
                _add(qs, Question(Level.RELATION, Category.CLIENT, QType.BOOL, f"Does node {n.name} use service {sname} as a client (after resolving content(...) and remaps)?", _bool_yes_no(sname in eff_use)), budget_total)

        # Content-service resolution questions
        for (_ph, param_name, srv_type) in getattr(n.node_type, "consumes_content_services", set()) or set():
            if _stop_if_budget(qs, budget_total):
                break
            _add(
                qs,
                Question(Level.RELATION, Category.CONTENT_SERVICE, QType.BOOL, f"Does node {n.name} consume a service whose name is provided by parameter {param_name}?", "Yes"),
                budget_total,
            )
            assigned = param_name in assigns
            _add(
                qs,
                Question(Level.RELATION, Category.CONTENT_SERVICE, QType.BOOL, f"Is parameter {param_name} assigned in node instance {n.name} for resolving the consumed service name?", _bool_yes_no(assigned)),
                budget_total,
            )
            if assigned:
                resolved_name = _apply_remaps(_strip_quotes(assigns[param_name].value), n)
                _add(
                    qs,
                    Question(Level.RELATION, Category.CONTENT_SERVICE, QType.OPEN, f"What is the resolved consumed service name for node {n.name} (via parameter {param_name})?", resolved_name or _open_unknown()),
                    budget_total,
                )
                _add(
                    qs,
                    Question(Level.RELATION, Category.CONTENT_SERVICE, QType.OPEN, f"What is the declared type of the consumed service resolved via parameter {param_name} in node {n.name}?", _opt_unknown(srv_type)),
                    budget_total,
                )

    if _stop_if_budget(qs, budget_total):
        return qs

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
        if _stop_if_budget(qs, budget_total):
            break
        _add(qs, Question(Level.RELATION, Category.TOPIC_TYPE, QType.OPEN, f"What is the type of topic {t.name}?", _opt_unknown(getattr(t, "type", None))), budget_total)
        _add(qs, Question(Level.RELATION, Category.PUBLISH, QType.OPEN, f"Which nodes publish to topic {t.name} (after resolving content(...) and remaps)?", _comma_list(topic_publishers.get(t.name, set()))), budget_total)
        _add(qs, Question(Level.RELATION, Category.SUBSCRIBE, QType.OPEN, f"Which nodes subscribe to topic {t.name} (after resolving content(...) and remaps)?", _comma_list(topic_subscribers.get(t.name, set()))), budget_total)

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
        if _stop_if_budget(qs, budget_total):
            break
        _add(qs, Question(Level.RELATION, Category.SERVICE_TYPE, QType.OPEN, f"What is the type of service {s.name}?", _opt_unknown(getattr(s, "type", None))), budget_total)
        _add(qs, Question(Level.RELATION, Category.SERVICE, QType.OPEN, f"Which nodes provide service {s.name} (after resolving content(...) and remaps)?", _comma_list(service_providers.get(s.name, set()))), budget_total)
        _add(qs, Question(Level.RELATION, Category.CLIENT, QType.OPEN, f"Which nodes use service {s.name} as a client (after resolving content(...) and remaps)?", _comma_list(service_clients.get(s.name, set()))), budget_total)

    # ------------------------------------------------------------
    # Level 1: POLICY family
    # ------------------------------------------------------------
    for p in qos_policies:
        if _stop_if_budget(qs, budget_total):
            break
        _add(qs, Question(Level.RELATION, Category.POLICY, QType.BOOL, f"Is there a policy instance called {p.name}?", "Yes"), budget_total)
        _add(qs, Question(Level.RELATION, Category.POLICY, QType.OPEN, f"What is the kind of policy instance {p.name}?", _opt_unknown(getattr(p, "kind", None))), budget_total)
        settings = getattr(p, "settings", {}) or {}
        _add(qs, Question(Level.RELATION, Category.POLICY, QType.OPEN, f"What settings are defined in policy instance {p.name}?", _comma_list([f"{k}={v}" for k, v in settings.items()])), budget_total)
        for k, v in settings.items():
            if _stop_if_budget(qs, budget_total):
                break
            _add(qs, Question(Level.RELATION, Category.POLICY, QType.OPEN, f"What is the value of setting {k} in policy instance {p.name}?", str(v).strip() or _open_unknown()), budget_total)

    # ------------------------------------------------------------
    # Level 1: TYPE ALIAS family
    # ------------------------------------------------------------
    for a in type_aliases:
        if _stop_if_budget(qs, budget_total):
            break
        _add(qs, Question(Level.RELATION, Category.TYPE_ALIAS, QType.BOOL, f"Is there a type alias called {a.name}?", "Yes"), budget_total)
        _add(qs, Question(Level.RELATION, Category.TYPE_ALIAS, QType.OPEN, f"What is the definition of type alias {a.name}?", _opt_unknown(getattr(a, "definition", None))), budget_total)

    # ------------------------------------------------------------
    # Level 1: MESSAGE ALIAS + FIELD family
    # ------------------------------------------------------------
    for m in message_aliases:
        if _stop_if_budget(qs, budget_total):
            break
        _add(qs, Question(Level.RELATION, Category.MESSAGE_ALIAS, QType.BOOL, f"Is there a message alias called {m.name}?", "Yes"), budget_total)
        _add(qs, Question(Level.RELATION, Category.MESSAGE_ALIAS, QType.OPEN, f"What is the base message type of message alias {m.name}?", _opt_unknown(getattr(m, "base_type", None))), budget_total)
        fields = getattr(m, "fields", []) or []
        _add(qs, Question(Level.RELATION, Category.MESSAGE_FIELD, QType.OPEN, f"Which fields are defined in message alias {m.name}?", _comma_list([getattr(f, 'name', '') for f in fields])), budget_total)
        for f in fields:
            if _stop_if_budget(qs, budget_total):
                break
            _add(qs, Question(Level.RELATION, Category.MESSAGE_FIELD, QType.OPEN, f"What is the type of field {getattr(f, 'name', '')} in message alias {m.name}?", _opt_unknown(getattr(f, "type", None))), budget_total)

    if _stop_if_budget(qs, budget_total):
        return qs

    # ------------------------------------------------------------
    # Level 2: PATH questions
    # ------------------------------------------------------------
    if profile == "balanced":
        # sample path pairs
        node_names = [n.name for n in nodes]
        pairs = [(a, b) for a in node_names for b in node_names if a != b]
        pairs = _sample(pairs, k=min(budget_l2, len(pairs)), rng=rng) if budget_l2 is not None else pairs
        for (src, dst) in pairs:
            if _stop_if_budget(qs, budget_total):
                break
            _add(
                qs,
                Question(Level.PATH, Category.MESSAGE, QType.BOOL, f"Is there a communication path from node {src} to node {dst} via a topic or service?", _bool_yes_no(_has_communication_path(src, dst, graph))),
                budget_total,
            )
    else:
        for src in nodes:
            for dst in nodes:
                if src.name == dst.name:
                    continue
                if _stop_if_budget(qs, budget_total):
                    break
                _add(
                    qs,
                    Question(Level.PATH, Category.MESSAGE, QType.BOOL, f"Is there a communication path from node {src.name} to node {dst.name} via a topic or service?", _bool_yes_no(_has_communication_path(src.name, dst.name, graph))),
                    budget_total,
                )

    # If balanced and we still didn't reach target, top up with negative relation BOOLs
    if profile == "balanced" and budget_total is not None and len(qs) < budget_total:
        # Fill remaining with random negative-ish relations
        node_type_names = [nt.name for nt in node_types]
        node_inst_names = [n.name for n in nodes]
        remaining = budget_total - len(qs)

        # Prepare candidate (subject, kind, object) tuples
        candidates: List[Tuple[str, str, str]] = []
        for nt in node_type_names:
            for t in all_topic_names:
                candidates.append((nt, "NT_PUB", t))
                candidates.append((nt, "NT_SUB", t))
            for s in all_service_names:
                candidates.append((nt, "NT_PROV", s))
                candidates.append((nt, "NT_USE", s))
        for ni in node_inst_names:
            for t in all_topic_names:
                candidates.append((ni, "N_PUB", t))
                candidates.append((ni, "N_SUB", t))
            for s in all_service_names:
                candidates.append((ni, "N_PROV", s))
                candidates.append((ni, "N_USE", s))

        for (subj, kind, obj) in _sample(candidates, k=min(remaining, len(candidates)), rng=rng):
            if _stop_if_budget(qs, budget_total):
                break
            if kind == "NT_PUB":
                nt = next((x for x in node_types if x.name == subj), None)
                declared = {name for (name, _t) in (getattr(nt, "publishes", set()) or set())} if nt else set()
                _add(qs, Question(Level.RELATION, Category.PUBLISH, QType.BOOL, f"Does node type {subj} publish to topic {obj} (as declared)?", _bool_yes_no(obj in declared)), budget_total)
            elif kind == "NT_SUB":
                nt = next((x for x in node_types if x.name == subj), None)
                declared = {name for (name, _t) in (getattr(nt, "subscribes", set()) or set())} if nt else set()
                _add(qs, Question(Level.RELATION, Category.SUBSCRIBE, QType.BOOL, f"Is node type {subj} subscribed to topic {obj} (as declared)?", _bool_yes_no(obj in declared)), budget_total)
            elif kind == "NT_PROV":
                nt = next((x for x in node_types if x.name == subj), None)
                declared = {name for (name, _t) in (getattr(nt, "provides", set()) or set())} if nt else set()
                _add(qs, Question(Level.RELATION, Category.SERVICE, QType.BOOL, f"Does node type {subj} provide service {obj} (as declared)?", _bool_yes_no(obj in declared)), budget_total)
            elif kind == "NT_USE":
                nt = next((x for x in node_types if x.name == subj), None)
                declared = {name for (name, _t) in (getattr(nt, "uses", set()) or set())} if nt else set()
                _add(qs, Question(Level.RELATION, Category.CLIENT, QType.BOOL, f"Does node type {subj} use service {obj} as a client (as declared)?", _bool_yes_no(obj in declared)), budget_total)
            elif kind == "N_PUB":
                n = next((x for x in nodes if x.name == subj), None)
                eff = _effective_publishes(n) if n else set()
                _add(qs, Question(Level.RELATION, Category.PUBLISH, QType.BOOL, f"Does node {subj} publish to topic {obj} (after resolving content(...) and remaps)?", _bool_yes_no(obj in eff)), budget_total)
            elif kind == "N_SUB":
                n = next((x for x in nodes if x.name == subj), None)
                eff = _effective_subscribes(n) if n else set()
                _add(qs, Question(Level.RELATION, Category.SUBSCRIBE, QType.BOOL, f"Is node {subj} subscribed to topic {obj} (after resolving content(...) and remaps)?", _bool_yes_no(obj in eff)), budget_total)
            elif kind == "N_PROV":
                n = next((x for x in nodes if x.name == subj), None)
                eff = _effective_provides(n) if n else set()
                _add(qs, Question(Level.RELATION, Category.SERVICE, QType.BOOL, f"Does node {subj} provide service {obj} (after resolving content(...) and remaps)?", _bool_yes_no(obj in eff)), budget_total)
            elif kind == "N_USE":
                n = next((x for x in nodes if x.name == subj), None)
                eff = _effective_uses(n) if n else set()
                _add(qs, Question(Level.RELATION, Category.CLIENT, QType.BOOL, f"Does node {subj} use service {obj} as a client (after resolving content(...) and remaps)?", _bool_yes_no(obj in eff)), budget_total)

    return qs