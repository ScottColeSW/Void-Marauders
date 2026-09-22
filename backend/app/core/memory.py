"""Per-colonist episodic memory, built on Palimpsest instead of a flat
Qdrant vector index. Each colonist gets their own in-memory mesh (never
shared across agents — see Palimpsest's single-agent-by-design stance)
so a crewmate's read on another crewmate, or on a sector, can actually
reinforce or collide over time instead of just being "similar text
retrieved again."

Domains:
- crew_trust (ATTRIBUTE): one colonist's standing read on another,
  referent = that colonist's agent_id. Collides when someone's
  behavior contradicts the existing read — this is what gives Karl's
  "secretly distrusts Valerie" trait something to actually chew on.
- threat_assessment (ATTRIBUTE): "is this sector dangerous right now",
  referent = sector_id. Collides when a sector thought clear turns out
  to ambush someone.
- personal_log (EVENT): the colonist's own narrated history, referent
  = their own agent_id. Never collides — many unrelated things happen.
- captain_directives (EVENT, folded into crew_trust for now): order
  compliance/defiance is recorded as a crew_trust observation about
  the captain, since "keeps ordering me into danger" is a trust signal.

No embeddings needed for consult() itself — that's referent+domain
keyed, pure token overlap (see palimpsest.consult). Embeddings are used
for exactly one thing: ranking personal_log recall by relevance to the
current situation, the one place Qdrant's kNN actually earned its
keep. That's a local, best-effort layer (Ollama, same model Qdrant
used) — if it's unavailable, recall falls back to recency, it never
blocks or crashes a tick.

Persistence: each store is dumped to JSON under MEMORY_DIR. This is
deliberately independent of world state — the colony can reset
(new run) while colonists still remember; see engine.py's tick() for
the save cadence.

Palimpsest is imported lazily (inside the functions that need it, not
at module load) rather than at the top of this file. This module is
always imported by engine.py regardless of MEMORY_ENABLED, and the
README's "fastest path" quickstart promises zero Ollama/Docker/extra
setup in mock mode — a hard top-level `import palimpsest` would break
that promise for anyone who hasn't also cloned and pip-installed it.
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from app.schemas.agent import ActionType, AgentActionSchema

if TYPE_CHECKING:
    from palimpsest.memory_store import InMemoryStore
    from palimpsest.models import Edge, Node

MEMORY_ENABLED = os.getenv("MEMORY_ENABLED", "true").lower() == "true"
MEMORY_DIR = Path(os.getenv("MEMORY_DIR", str(Path(__file__).resolve().parents[1] / "data" / "memory")))
DECAY_HALF_LIFE_SECONDS = float(os.getenv("MEMORY_DECAY_HALF_LIFE_SECONDS", str(6 * 3600)))
DECAY_FLOOR = 0.1
SAVE_EVERY_N_TICKS = 10
PERSONAL_LOG_RECALL_LIMIT = 4

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
EMBED_FAILURE_COOLDOWN_SECONDS = 30

DOMAIN_CREW_TRUST = "crew_trust"
DOMAIN_THREAT_ASSESSMENT = "threat_assessment"
DOMAIN_PERSONAL_LOG = "personal_log"

_ALIEN_REACTION_ACTIONS = (ActionType.FIRE_WEAPON, ActionType.TAKE_COVER, ActionType.RETREAT)

_stores: Dict[str, "InMemoryStore"] = {}
_embed_unavailable_until: float = 0.0
_domains_registered = False


def _register_domain_kinds() -> None:
    # DOMAIN_KINDS defaults unregistered domains to EVENT (see palimpsest.consult)
    # -- an unsafe default here means "unregistered" silently gets no collision
    # detection. Both of ours are ATTRIBUTE: a colonist's read on another
    # colonist, or on a sector's safety, has exactly one standing value at a
    # time, so a contradicting observation is real tension, not just "a
    # different event." Registering here (rather than in Palimpsest itself)
    # keeps the library's own domain catalog free of one consumer's
    # vocabulary -- this is the extension point its own docs describe: a
    # plain dict, meant to be added to. Idempotent since it runs on every
    # call into consult()-using code, not just once at import time.
    global _domains_registered
    if _domains_registered:
        return
    from palimpsest.consult import DOMAIN_KINDS
    from palimpsest.models import DomainKind

    DOMAIN_KINDS[DOMAIN_CREW_TRUST] = DomainKind.ATTRIBUTE
    DOMAIN_KINDS[DOMAIN_THREAT_ASSESSMENT] = DomainKind.ATTRIBUTE
    _domains_registered = True


# -- persistence --------------------------------------------------------

def _node_to_dict(node: Node) -> dict:
    return {
        "id": node.id, "text": node.text, "domain": node.domain, "referent": node.referent,
        "scope": node.scope.value, "origin": node.origin.value, "why": node.why,
        "weight": node.weight, "evidence_count": node.evidence_count, "embedding": node.embedding,
        "origin_date": node.origin_date.isoformat(), "last_touched": node.last_touched.isoformat(),
    }


def _node_from_dict(d: dict) -> "Node":
    from palimpsest.models import Node, Origin, Scope

    return Node(
        id=d["id"], text=d["text"], domain=d["domain"], referent=d["referent"],
        scope=Scope(d["scope"]), origin=Origin(d["origin"]), why=d.get("why", ""),
        weight=d["weight"], evidence_count=d.get("evidence_count", 0), embedding=d.get("embedding"),
        origin_date=datetime.fromisoformat(d["origin_date"]),
        last_touched=datetime.fromisoformat(d["last_touched"]),
    )


def _edge_to_dict(edge: Edge) -> dict:
    return {
        "id": edge.id, "source_id": edge.source_id, "target_id": edge.target_id,
        "type": edge.type.value, "date": edge.date.isoformat(),
        "status": edge.status.value if edge.status else None,
        "tolerance_context": edge.tolerance_context, "resolution_why": edge.resolution_why,
        "resolved_at": edge.resolved_at.isoformat() if edge.resolved_at else None,
    }


def _edge_from_dict(d: dict) -> "Edge":
    from palimpsest.models import Edge, EdgeStatus, EdgeType

    return Edge(
        id=d["id"], source_id=d["source_id"], target_id=d["target_id"],
        type=EdgeType(d["type"]), date=datetime.fromisoformat(d["date"]),
        status=EdgeStatus(d["status"]) if d.get("status") else None,
        tolerance_context=d.get("tolerance_context", ""), resolution_why=d.get("resolution_why", ""),
        resolved_at=datetime.fromisoformat(d["resolved_at"]) if d.get("resolved_at") else None,
    )


def _path_for(agent_id: str) -> Path:
    return MEMORY_DIR / f"{agent_id}.json"


def _load_store(agent_id: str) -> "InMemoryStore":
    from palimpsest.memory_store import InMemoryStore

    store = InMemoryStore()
    path = _path_for(agent_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for nd in data.get("nodes", []):
                store.add_node(_node_from_dict(nd))
            for ed in data.get("edges", []):
                store.add_edge(_edge_from_dict(ed))
        except Exception:
            pass  # corrupt or old-shape file -- start this colonist fresh rather than crash the sim
    return store


def _store_for(agent_id: str) -> InMemoryStore:
    if agent_id not in _stores:
        _stores[agent_id] = _load_store(agent_id)
    return _stores[agent_id]


def save_agent(agent_id: str) -> None:
    store = _stores.get(agent_id)
    if store is None:
        return
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "nodes": [_node_to_dict(n) for n in store.all_nodes()],
        "edges": [_edge_to_dict(e) for e in store.all_edges()],
    }
    _path_for(agent_id).write_text(json.dumps(payload), encoding="utf-8")


def save_all() -> None:
    for agent_id in list(_stores.keys()):
        save_agent(agent_id)


# -- decay ---------------------------------------------------------------

def decay_all() -> None:
    if not _stores:
        return  # avoid the palimpsest import entirely when nothing's been recorded yet
    from palimpsest.decay import decay_store

    for store in _stores.values():
        decay_store(store, half_life_seconds=DECAY_HALF_LIFE_SECONDS, floor=DECAY_FLOOR)


# -- embeddings (best-effort, only used to rank personal_log recall) -----

def _embed(text: str) -> Optional[List[float]]:
    global _embed_unavailable_until
    if time.monotonic() < _embed_unavailable_until:
        return None
    try:
        import ollama

        client = ollama.Client(host=OLLAMA_HOST)
        response = client.embeddings(model=EMBED_MODEL, prompt=text)
        return response["embedding"]
    except Exception:
        _embed_unavailable_until = time.monotonic() + EMBED_FAILURE_COOLDOWN_SECONDS
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# -- recording -------------------------------------------------------------

def _crew_trust_observation(
    action: AgentActionSchema, crew_names: Dict[str, str]
) -> Optional[tuple]:
    """Returns (referent_agent_id, text) for the crewmate this action was
    concretely about, or None — most actions aren't about anyone in
    particular, and forcing a referent onto them would just be noise."""
    if action.action_type == ActionType.CONFRONT_CREW and action.target_id in crew_names:
        return action.target_id, f"{crew_names[action.target_id]} was confronted during high stress"
    if action.action_type == ActionType.ISSUE_ORDER and action.target_id in crew_names:
        verb = action.order_action.value.replace("_", " ") if action.order_action else "act"
        return action.target_id, f"ordered {crew_names[action.target_id]} to {verb}"
    return None


def record_action(
    agent_id: str,
    action: AgentActionSchema,
    *,
    current_sector: str,
    nearby_aliens: List[str],
    crew_names: Dict[str, str],
) -> None:
    if not MEMORY_ENABLED:
        return
    from palimpsest.consult import apply_consult, consult
    from palimpsest.models import Node, Origin, Scope

    _register_domain_kinds()
    store = _store_for(agent_id)

    log_text = (
        f"{action.inner_monologue} Action: {action.action_type.value} "
        f"target={action.target_id}. Said: '{action.spoken_dialogue}'"
    )[:300]
    log_node = Node(
        id=f"{agent_id}-log-{uuid.uuid4().hex[:8]}", text=log_text, domain=DOMAIN_PERSONAL_LOG,
        referent=agent_id, scope=Scope.GENERAL, origin=Origin.EPISODE, weight=0.5,
        embedding=_embed(log_text),
    )
    apply_consult(store, log_node, consult(store, log_node))

    if nearby_aliens or action.action_type in _ALIEN_REACTION_ACTIONS:
        threat_text = f"{current_sector} has hostiles present" if nearby_aliens else f"{current_sector} seemed clear"
        threat_node = Node(
            id=f"{agent_id}-threat-{uuid.uuid4().hex[:8]}", text=threat_text,
            domain=DOMAIN_THREAT_ASSESSMENT, referent=current_sector, scope=Scope.INSTANCE,
            origin=Origin.EPISODE, weight=0.6,
        )
        apply_consult(store, threat_node, consult(store, threat_node))

    trust_observation = _crew_trust_observation(action, crew_names)
    if trust_observation:
        trust_referent, trust_text = trust_observation
        trust_node = Node(
            id=f"{agent_id}-trust-{uuid.uuid4().hex[:8]}", text=trust_text, domain=DOMAIN_CREW_TRUST,
            referent=trust_referent, scope=Scope.GENERAL, origin=Origin.EPISODE, weight=0.6,
        )
        apply_consult(store, trust_node, consult(store, trust_node))


def record_order_outcome(agent_id: str, captain_id: str, captain_name: str, complied: bool) -> None:
    if not MEMORY_ENABLED:
        return
    from palimpsest.consult import apply_consult, consult
    from palimpsest.models import Node, Origin, Scope

    _register_domain_kinds()
    store = _store_for(agent_id)
    verb = "complied with" if complied else "ignored"
    node = Node(
        id=f"{agent_id}-trust-{uuid.uuid4().hex[:8]}", text=f"{verb} {captain_name}'s order",
        domain=DOMAIN_CREW_TRUST, referent=captain_id, scope=Scope.GENERAL,
        origin=Origin.EPISODE, weight=0.6,
    )
    apply_consult(store, node, consult(store, node))


# -- recall ------------------------------------------------------------

def _open_collision_partner(store: "InMemoryStore", node: "Node") -> "Optional[Node]":
    from palimpsest.models import EdgeStatus, EdgeType

    for edge in store.get_edges_for_node(node.id, edge_types=[EdgeType.COLLIDES]):
        if edge.status != EdgeStatus.OPEN:
            continue
        other_id = edge.target_id if edge.source_id == node.id else edge.source_id
        other = store.get_node(other_id)
        if other:
            return other
    return None


def recall(
    agent_id: str, *, current_sector: str, nearby_crew: List[str], nearby_aliens: List[str],
    crew_names: Dict[str, str],
) -> List[str]:
    if not MEMORY_ENABLED:
        return []
    from palimpsest.models import Origin

    store = _store_for(agent_id)
    lines: List[str] = []

    for crew_id in nearby_crew:
        candidates = [
            n for n in store.all_nodes()
            if n.domain == DOMAIN_CREW_TRUST and n.referent == crew_id and n.origin == Origin.EPISODE
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda n: n.weight)
        name = crew_names.get(crew_id, crew_id)
        lines.append(f"[read on {name}] {best.text} (confidence {best.weight:.2f})")
        collision = _open_collision_partner(store, best)
        if collision:
            lines.append(f"[unresolved] something about {name} doesn't add up: {collision.text}")

    threat_candidates = [
        n for n in store.all_nodes()
        if n.domain == DOMAIN_THREAT_ASSESSMENT and n.referent == current_sector and n.origin == Origin.EPISODE
    ]
    if threat_candidates:
        best = max(threat_candidates, key=lambda n: n.weight)
        lines.append(f"[threat read on {current_sector}] {best.text}")

    log_nodes = [n for n in store.all_nodes() if n.domain == DOMAIN_PERSONAL_LOG and n.origin == Origin.EPISODE]
    if log_nodes:
        query_embedding = _embed(f"sector {current_sector}, crew {nearby_crew}, aliens {nearby_aliens}")
        if query_embedding:
            ranked = sorted(
                (n for n in log_nodes if n.embedding),
                key=lambda n: _cosine(n.embedding, query_embedding),
                reverse=True,
            )
        else:
            ranked = sorted(log_nodes, key=lambda n: n.last_touched, reverse=True)
        lines.extend(n.text for n in ranked[:PERSONAL_LOG_RECALL_LIMIT])

    return lines
