"""Agent logic ported exactly from core/reference/memtrace_reference.py."""
from collections import defaultdict

from core.params import DECAY, K, TOOLS

# ---------------- Cypher queries (reads) ----------------

_Q_RETRIEVE = (
    "MATCH (m:Memory {topic: $topic})-[r:RECOMMENDS]->(t:Tool) "
    "WHERE m.status = 'active' AND m.created_at <= $ts AND NOT (m.id IN $blocked) "
    "RETURN m.id AS id, m.confidence AS conf, m.created_at AS ts, "
    "t.name AS tool, r.weight AS weight"
)

_Q_EPISODIC_RECS = (
    "MATCH (m:Memory {topic: $topic})-[r:RECOMMENDS]->(t:Tool) "
    "WHERE m.status = 'active' AND m.kind = 'episodic' AND m.created_at <= $ts "
    "RETURN m.id AS id, m.confidence AS conf, m.created_at AS ts, "
    "t.name AS tool, r.weight AS weight"
)

# ---------------- Cypher queries (writes) ----------------

_W_CREATE_TASK = (
    "CREATE (:Task {id: $id, topic: $topic, amount: $amount, ts: $ts})"
)

_W_CREATE_DECISION = (
    "CREATE (:Decision {id: $id, ts: $ts, day: $day, chosen_tool: $chosen_tool})"
)

_W_DECISION_FOR_TASK = (
    "MATCH (d:Decision {id: $did}), (t:Task {id: $tid}) "
    "CREATE (d)-[:FOR]->(t)"
)

_W_DECISION_CHOSE_TOOL = (
    "MATCH (d:Decision {id: $did}), (t:Tool {name: $tool}) "
    "CREATE (d)-[:CHOSE]->(t)"
)

_W_AGENT_MADE_DECISION = (
    "MATCH (a:Agent {name: $agent}), (d:Decision {id: $did}) "
    "CREATE (a)-[:MADE]->(d)"
)

_W_MEMORY_RETRIEVED_IN = (
    "MATCH (m:Memory {id: $mid}), (d:Decision {id: $did}) "
    "CREATE (m)-[:RETRIEVED_IN {rank: $rank}]->(d)"
)

_W_CREATE_MEMORY = (
    "CREATE (:Memory {id: $id, text: $text, kind: $kind, topic: $topic, "
    "confidence: $confidence, created_at: $created_at, status: 'active'})"
)

_W_LINK_FROM = (
    "MATCH (m:Memory {id: $mid}), (s:Source {id: $sid}) "
    "CREATE (m)-[:FROM]->(s)"
)

_W_LINK_RECOMMENDS = (
    "MATCH (m:Memory {id: $mid}), (t:Tool {name: $tool}) "
    "CREATE (m)-[:RECOMMENDS {weight: $weight}]->(t)"
)

_W_AGENT_WROTE_MEMORY = (
    "MATCH (a:Agent {name: $agent}), (m:Memory {id: $mid}) "
    "CREATE (a)-[:WROTE]->(m)"
)

_W_DERIVED_FROM = (
    "MATCH (new:Memory {id: $new_id}), (src:Memory {id: $src_id}) "
    "CREATE (new)-[:DERIVED_FROM]->(src)"
)


# ---------------- agent functions ----------------

def rank(conf, created_at, now_ts):
    """Recency-weighted confidence: conf * DECAY^(age_in_days)."""
    age_days = (now_ts - created_at) / 24.0
    return conf * (DECAY ** age_days)


def retrieve(g, topic, now_ts, blocked=()):
    """Return top-K memories as dicts {id, conf, ts, recs: {tool: weight}}."""
    rows = g.ro_query(
        _Q_RETRIEVE,
        {"topic": topic, "ts": now_ts, "blocked": list(blocked)},
    ).result_set

    # Group one row-per-(memory,tool) into per-memory dicts
    memories = {}
    for row in rows:
        mid, conf, ts, tool, weight = row[0], row[1], row[2], row[3], row[4]
        if mid not in memories:
            memories[mid] = {"id": mid, "conf": conf, "ts": ts, "recs": {}}
        memories[mid]["recs"][tool] = weight

    # Sort by (-rank, id) and take top K
    ranked = sorted(
        memories.values(),
        key=lambda m: (-rank(m["conf"], m["ts"], now_ts), m["id"]),
    )
    return ranked[:K]


def choose(retrieved, now_ts, default):
    """Return the highest-scoring tool; ties broken by TOOLS list order."""
    score = defaultdict(float)
    for m in retrieved:
        for tool, w in m["recs"].items():
            score[tool] += w * rank(m["conf"], m["ts"], now_ts)
    if not score:
        return default
    return max(TOOLS, key=lambda t: (score[t], -TOOLS.index(t)))


def ops_decide(g, decision_index, ts, day, topic, amount):
    """Run one decision: retrieve, choose, write Task/Decision/edges."""
    default = "bank_transfer" if topic == "large_payment" else "card_processor"
    retrieved = retrieve(g, topic, ts)
    chosen_tool = choose(retrieved, ts, default)

    task_id = f"task_{decision_index}"
    decision_id = f"d{decision_index}"

    # Task node
    g.query(_W_CREATE_TASK, {
        "id": task_id,
        "topic": topic,
        "amount": amount,
        "ts": ts,
    })

    # Decision node
    g.query(_W_CREATE_DECISION, {
        "id": decision_id,
        "ts": ts,
        "day": day,
        "chosen_tool": chosen_tool,
    })

    # (Decision)-[:FOR]->(Task)
    g.query(_W_DECISION_FOR_TASK, {"did": decision_id, "tid": task_id})

    # (Decision)-[:CHOSE]->(Tool)
    g.query(_W_DECISION_CHOSE_TOOL, {"did": decision_id, "tool": chosen_tool})

    # (Agent {name:'Ops'})-[:MADE]->(Decision)
    g.query(_W_AGENT_MADE_DECISION, {"agent": "Ops", "did": decision_id})

    # (Memory)-[:RETRIEVED_IN {rank: 1..K}]->(Decision)
    for position, mem in enumerate(retrieved, start=1):
        g.query(_W_MEMORY_RETRIEVED_IN, {
            "mid": mem["id"],
            "did": decision_id,
            "rank": position,
        })

    return chosen_tool


def reader_ingest(g, mem):
    """Create a Memory node from dict; link FROM Source, RECOMMENDS Tool, Agent wrote it."""
    g.query(_W_CREATE_MEMORY, {
        "id":         mem["id"],
        "text":       mem["text"],
        "kind":       mem["kind"],
        "topic":      mem["topic"],
        "confidence": mem["confidence"],
        "created_at": mem["created_at"],
    })
    g.query(_W_LINK_FROM, {"mid": mem["id"], "sid": mem["source"]})
    for tool, weight in mem["recs"].items():
        g.query(_W_LINK_RECOMMENDS, {"mid": mem["id"], "tool": tool, "weight": weight})
    g.query(_W_AGENT_WROTE_MEMORY, {"agent": "Reader", "mid": mem["id"]})


def consolidate(g, topic, ts, new_id):
    """Derive a semantic memory when >=2 episodic memories recommend the same tool."""
    rows = g.ro_query(
        _Q_EPISODIC_RECS,
        {"topic": topic, "ts": ts},
    ).result_set

    # Group by tool -> list of memory dicts
    groups = defaultdict(list)
    seen = {}  # mid -> memory dict (avoid duplicates across tools)
    for row in rows:
        mid, conf, mem_ts, tool, weight = row[0], row[1], row[2], row[3], row[4]
        if mid not in seen:
            seen[mid] = {"id": mid, "conf": conf, "ts": mem_ts, "recs": {}}
        seen[mid]["recs"][tool] = weight

    # Rebuild groups from the de-duplicated memory objects
    for m in seen.values():
        for tool in m["recs"]:
            groups[tool].append(m)

    # Iterate tools in TOOLS order (matches reference iteration logic)
    for tool in TOOLS:
        ms = groups.get(tool, [])
        if len(ms) >= 2:
            conf = min(0.95, sum(m["conf"] for m in ms) / len(ms) + 0.05)
            w = sum(m["recs"][tool] for m in ms) / len(ms)
            text = f"Summary: {tool} preferred for {topic}"

            g.query(_W_CREATE_MEMORY, {
                "id":         new_id,
                "text":       text,
                "kind":       "semantic",
                "topic":      topic,
                "confidence": conf,
                "created_at": ts,
            })
            g.query(_W_LINK_FROM, {"mid": new_id, "sid": "src_agent"})
            g.query(_W_LINK_RECOMMENDS, {"mid": new_id, "tool": tool, "weight": w})
            g.query(_W_AGENT_WROTE_MEMORY, {"agent": "Consolidator", "mid": new_id})

            for src in ms:
                g.query(_W_DERIVED_FROM, {"new_id": new_id, "src_id": src["id"]})

            return new_id

    return None
