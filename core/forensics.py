"""Forensic analysis functions ported exactly from core/reference/memtrace_reference.py."""
from core.agents import retrieve, choose

# ── Cypher constants ─────────────────────────────────────────────────────────

# Counts decisions per (tool, day); used by share_by_day.
_Q_DECISIONS_PER_TOOL_DAY = (
    "MATCH (d:Decision)-[r:CHOSE]->(t:Tool) "
    "RETURN d.day AS day, t.name AS tool, count(r) AS n"
)

# Total retrievals per memory across all decisions.
_Q_TOTAL_RETRIEVALS = (
    "MATCH (m:Memory)-[r:RETRIEVED_IN]->(d:Decision) "
    "RETURN m.id AS id, count(d) AS total"
)

# Retrievals per memory for decisions that CHOSE *tool* at or after *since_ts*.
_Q_HIT_RETRIEVALS = (
    "MATCH (m:Memory)-[r:RETRIEVED_IN]->(d:Decision)-[c:CHOSE]->(t:Tool) "
    "WHERE t.name = $tool AND d.ts >= $since "
    "RETURN m.id AS id, count(r) AS hits"
)

# Trust score for each memory via its source.
_Q_MEMORY_TRUST = (
    "MATCH (m:Memory)-[f:FROM]->(s:Source) "
    "RETURN m.id AS id, s.trust AS trust"
)

# IDs of all memories from a given source.
_Q_SOURCE_SEED_IDS = (
    "MATCH (m:Memory)-[f:FROM]->(s:Source {id: $source_id}) "
    "RETURN m.id AS id"
)

# IDs of all memories derived (directly or transitively, depth 1–6) from any seed.
_Q_DESCENDANTS = (
    "MATCH (d:Memory)-[:DERIVED_FROM*1..6]->(bad:Memory) "
    "WHERE bad.id IN $ids "
    "RETURN DISTINCT d.id AS id"
)

# Affected decisions and their task amounts for a set of memory ids.
_Q_AFFECTED_DECISIONS = (
    "MATCH (m:Memory)-[r:RETRIEVED_IN]->(d:Decision)-[f:FOR]->(t:Task) "
    "WHERE m.id IN $ids "
    "RETURN DISTINCT d.id AS did, t.amount AS amount"
)

# Full provenance path from a memory up to 5 hops to a root, then its source.
_Q_PROVENANCE_PATH = (
    "MATCH p = (m:Memory {id: $id})-[:DERIVED_FROM*1..5]->(root:Memory)"
    "-[f:FROM]->(s:Source) "
    "RETURN p"
)

# Direct source of the memory itself.
_Q_OWN_SOURCE = (
    "MATCH (m:Memory {id: $id})-[f:FROM]->(s:Source) "
    "RETURN s.id AS sid, s.name AS name, s.kind AS kind, s.trust AS trust"
)

# Set status = 'quarantined' on a list of memory ids.
_W_QUARANTINE = (
    "MATCH (m:Memory) WHERE m.id IN $ids "
    "SET m.status = 'quarantined' "
    "RETURN count(m) AS n"
)


# ── share_by_day ─────────────────────────────────────────────────────────────

def share_by_day(g):
    """Return {day: % of decisions that chose UNVERIFIED} for every recorded day."""
    from core.params import UNVERIFIED
    rows = g.ro_query(_Q_DECISIONS_PER_TOOL_DAY, {}).result_set

    # Aggregate counts in Python: n_by_day[day] = total, unverified_by_day[day] = n_UNVERIFIED
    n_by_day = {}
    unverified_by_day = {}
    for row in rows:
        day, tool, n = row[0], row[1], row[2]
        n_by_day[day] = n_by_day.get(day, 0) + n
        if tool == UNVERIFIED:
            unverified_by_day[day] = n

    result = {}
    for day, total in n_by_day.items():
        n_tool = unverified_by_day.get(day, 0)
        result[day] = round(100 * n_tool / total)
    return result


def first_drift_day(g):
    """Return the smallest day whose share_by_day > 0, or None if no drift."""
    shares = share_by_day(g)
    days_with_drift = [d for d, v in shares.items() if v > 0]
    return min(days_with_drift) if days_with_drift else None


# ── culprits ─────────────────────────────────────────────────────────────────

def culprits(g, tool, since_ts):
    """Return (id, hits, total, suspicion) tuples for memories that co-occur with *tool* choices.

    suspicion = round(hits / total * (1 - trust), 3).
    Only memories with hits > 0 are returned, sorted by (-suspicion, -hits, id).
    """
    # (a) total retrievals per memory
    total_map = {
        row[0]: row[1]
        for row in g.ro_query(_Q_TOTAL_RETRIEVALS, {}).result_set
    }

    # (b) hits per memory for chosen-tool decisions since *since_ts*
    hits_map = {
        row[0]: row[1]
        for row in g.ro_query(_Q_HIT_RETRIEVALS, {"tool": tool, "since": since_ts}).result_set
    }

    # (c) trust per memory via its source
    trust_map = {
        row[0]: row[1]
        for row in g.ro_query(_Q_MEMORY_TRUST, {}).result_set
    }

    rows = []
    for mid, hits in hits_map.items():
        total = total_map.get(mid, hits)
        trust = trust_map.get(mid, 0.0)
        suspicion = round(hits / total * (1 - trust), 3)
        rows.append((mid, hits, total, suspicion))

    return sorted(rows, key=lambda r: (-r[3], -r[1], r[0]))


# ── source_seed_ids ───────────────────────────────────────────────────────────

def source_seed_ids(g, source_id):
    """Return the ids of all Memory nodes FROM *source_id*."""
    rows = g.ro_query(_Q_SOURCE_SEED_IDS, {"source_id": source_id}).result_set
    return [row[0] for row in rows]


# ── descendants ───────────────────────────────────────────────────────────────

def descendants(g, seed_ids):
    """Return ids of all memories derived (1–6 hops) from any seed, excluding seeds themselves."""
    rows = g.ro_query(_Q_DESCENDANTS, {"ids": list(seed_ids)}).result_set
    seed_set = set(seed_ids)
    return [row[0] for row in rows if row[0] not in seed_set]


# ── blast_radius ──────────────────────────────────────────────────────────────

def blast_radius(g, seed_ids):
    """Return (all_ids, affected_decisions, exposure).

    all_ids          – seeds ∪ descendants (list)
    affected_decisions – list of distinct decision ids whose retrieval set overlaps all_ids
    exposure         – sum of task amounts for those distinct decisions (computed in Python)
    """
    seed_set = set(seed_ids)
    desc = descendants(g, seed_ids)
    all_ids = list(seed_set | set(desc))

    rows = g.ro_query(_Q_AFFECTED_DECISIONS, {"ids": all_ids}).result_set

    seen_dids = set()
    affected_decisions = []
    exposure = 0
    for row in rows:
        did, amount = row[0], row[1]
        if did not in seen_dids:
            seen_dids.add(did)
            affected_decisions.append(did)
            exposure += amount

    return all_ids, affected_decisions, exposure


# ── counterfactual ────────────────────────────────────────────────────────────

def counterfactual(g, suspect_ids):
    """Re-run retrieve+choose for every affected decision with *suspect_ids* blocked.

    Returns list of (decision_id, old_tool, new_tool) for decisions that flip.
    Must be called BEFORE quarantine so retrieve() still sees active memories.
    """
    suspect_set = set(suspect_ids)
    _, affected_dids, _ = blast_radius(g, suspect_ids)

    # Fetch the stored properties of each affected decision (ts, topic, chosen_tool).
    if not affected_dids:
        return []

    q = (
        "MATCH (d:Decision)-[f:FOR]->(t:Task), (d)-[c:CHOSE]->(tool:Tool) "
        "WHERE d.id IN $dids "
        "RETURN d.id AS did, d.ts AS ts, t.topic AS topic, tool.name AS old_tool"
    )
    rows = g.ro_query(q, {"dids": affected_dids}).result_set

    flips = []
    for row in rows:
        did, ts, topic, old_tool = row[0], row[1], row[2], row[3]
        default = "bank_transfer" if topic == "large_payment" else "card_processor"
        retrieved = retrieve(g, topic, ts, blocked=suspect_set)
        new_tool = choose(retrieved, ts, default)
        if new_tool != old_tool:
            flips.append((did, old_tool, new_tool))

    return flips


# ── naive_flips ───────────────────────────────────────────────────────────────

def naive_flips(g):
    """Counterfactual using only the memories whose source trust < 0.5 as suspects."""
    q = (
        "MATCH (m:Memory)-[f:FROM]->(s:Source) "
        "WHERE s.trust < 0.5 "
        "RETURN m.id AS id"
    )
    rows = g.ro_query(q, {}).result_set
    suspect_ids = [row[0] for row in rows]
    return counterfactual(g, suspect_ids)


# ── quarantine ────────────────────────────────────────────────────────────────

def quarantine(g, ids):
    """Set status='quarantined' on all Memory nodes whose id is in *ids*.

    Returns the count of memories updated.
    """
    rows = g.query(_W_QUARANTINE, {"ids": list(ids)}).result_set
    return rows[0][0] if rows else 0


# ── provenance ────────────────────────────────────────────────────────────────

def provenance(g, memory_id):
    """Return the derivation chain and source for *memory_id* as plain dicts.

    Returns a dict with:
      'paths'  – list of path dicts, each with 'nodes' (list of id/label dicts)
                 and 'relationships' (list of type dicts)
      'source' – dict with sid, name, kind, trust (the memory's own FROM source)
    """
    path_rows = g.ro_query(_Q_PROVENANCE_PATH, {"id": memory_id}).result_set
    paths = []
    for row in path_rows:
        path = row[0]
        nodes = [{"id": n.id, "labels": n.labels, "properties": n.properties}
                 for n in path.nodes()]
        rels = [{"type": r.relation, "properties": r.properties}
                for r in path.edges()]
        paths.append({"nodes": nodes, "relationships": rels})

    src_rows = g.ro_query(_Q_OWN_SOURCE, {"id": memory_id}).result_set
    source = None
    if src_rows:
        r = src_rows[0]
        source = {"sid": r[0], "name": r[1], "kind": r[2], "trust": r[3]}

    return {"paths": paths, "source": source}
