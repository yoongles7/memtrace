"""Seed a tenant graph with sources, tools, agents, and policy memories."""
from core.params import SOURCES, TOOLS, UNVERIFIED, POLICY_MEMORIES
from core.graph import tg, reset, init_schema

# ---------------- Cypher write statements ----------------

_CREATE_SOURCE = (
    "CREATE (:Source {id: $id, name: $name, kind: $kind, trust: $trust})"
)

_CREATE_TOOL = (
    "CREATE (:Tool {name: $name, verified: $verified})"
)

_CREATE_AGENT = (
    "CREATE (:Agent {name: $name, role: $role})"
)

_CREATE_MEMORY = (
    "CREATE (:Memory {id: $id, text: $text, kind: $kind, topic: $topic, "
    "confidence: $confidence, created_at: $created_at, status: $status})"
)

_LINK_FROM = (
    "MATCH (m:Memory {id: $mid}), (s:Source {id: $sid}) "
    "CREATE (m)-[:FROM]->(s)"
)

_LINK_RECOMMENDS = (
    "MATCH (m:Memory {id: $mid}), (t:Tool {name: $tool}) "
    "CREATE (m)-[:RECOMMENDS {weight: $weight}]->(t)"
)

# ---------------- agents ----------------
_AGENTS = [
    {"name": "Reader",      "role": "reader"},
    {"name": "Consolidator","role": "consolidator"},
    {"name": "Ops",         "role": "ops"},
]


def seed_tenant(tenant):
    """Reset the tenant graph and load all baseline nodes and edges."""
    reset(tenant)
    g = tg(tenant)

    # Source nodes
    for sid, props in SOURCES.items():
        g.query(_CREATE_SOURCE, {"id": sid, **props})

    # Tool nodes  (swiftsettle_gateway is verified=false, the others true)
    for name in TOOLS:
        g.query(_CREATE_TOOL, {"name": name, "verified": name != UNVERIFIED})

    # Agent nodes
    for agent in _AGENTS:
        g.query(_CREATE_AGENT, agent)

    # Policy Memory nodes + edges
    for pm in POLICY_MEMORIES:
        g.query(_CREATE_MEMORY, {
            "id":         pm["id"],
            "text":       pm["text"],
            "kind":       pm["kind"],
            "topic":      pm["topic"],
            "confidence": pm["confidence"],
            "created_at": pm["created_at"],
            "status":     "active",
        })
        g.query(_LINK_FROM, {"mid": pm["id"], "sid": pm["source"]})
        for tool, weight in pm["recs"].items():
            g.query(_LINK_RECOMMENDS, {"mid": pm["id"], "tool": tool, "weight": weight})

    # Indexes – created after nodes so they index existing data immediately
    init_schema(g)
