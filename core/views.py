"""JSON API views for MemTrace.

All POST views use @csrf_exempt (token auth is out of scope for this demo).
Valid tenants: acme, globex.  Any other value returns 400 JSON.
"""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.agents import choose, retrieve
from core.forensics import (
    blast_radius,
    counterfactual,
    culprits,
    first_drift_day,
    naive_flips,
    quarantine,
    share_by_day,
    source_seed_ids,
)
from core.graph import tg
from core.params import DAYS, TASKS_PER_DAY, UNVERIFIED
from core.seed import seed_tenant
from core.simulation import simulate_tenant

# ── helpers ──────────────────────────────────────────────────────────────────

VALID_TENANTS = {"acme", "globex"}

# Fetch every Memory, Source and Tool node plus the three edge types for /api/graph
_Q_GRAPH_NODES = (
    "MATCH (n) WHERE n:Memory OR n:Source OR n:Tool "
    "RETURN labels(n) AS labels, properties(n) AS props"
)

_Q_GRAPH_EDGES = (
    "MATCH (a)-[r]->(b) "
    "WHERE type(r) IN ['FROM', 'DERIVED_FROM', 'RECOMMENDS'] "
    "RETURN a.id AS src, b.id AS tgt, type(r) AS rtype, "
    "       coalesce(a.name, a.id) AS src_id, coalesce(b.name, b.id) AS tgt_id"
)

_Q_MEM_COUNTS = (
    "MATCH (m:Memory) "
    "RETURN m.status AS status, count(m) AS n"
)

_Q_DECISION_COUNT = (
    "MATCH (d:Decision) RETURN count(d) AS n"
)

# Cypher for replay decisions (mirrors _run_post_quarantine in acceptance.py)
_W_REPLAY_TASK = (
    "CREATE (:Task {id: $id, topic: $topic, amount: $amount, ts: $ts})"
)
_W_REPLAY_DECISION = (
    "CREATE (:Decision {id: $id, ts: $ts, day: $day, chosen_tool: $chosen_tool})"
)
_W_REPLAY_FOR = (
    "MATCH (d:Decision {id: $did}), (t:Task {id: $tid}) CREATE (d)-[:FOR]->(t)"
)
_W_REPLAY_CHOSE = (
    "MATCH (d:Decision {id: $did}), (t:Tool {name: $tool}) CREATE (d)-[:CHOSE]->(t)"
)
_W_REPLAY_RETRIEVED = (
    "MATCH (m:Memory {id: $mid}), (d:Decision {id: $did}) "
    "CREATE (m)-[:RETRIEVED_IN {rank: $rank}]->(d)"
)
_Q_COUNT_UNVERIFIED_POST = (
    "MATCH (d:Decision)-[:CHOSE]->(t:Tool {name: $tool}) "
    "WHERE d.id IN $dids RETURN count(d) AS n"
)


def _bad_tenant(tenant):
    """Return a 400 JsonResponse if *tenant* is not valid, else None."""
    if tenant not in VALID_TENANTS:
        return JsonResponse(
            {"error": f"invalid tenant '{tenant}'; valid values: acme, globex"},
            status=400,
        )
    return None


def _parse_body(request):
    """Decode JSON request body; return (data_dict, error_response)."""
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None, JsonResponse({"error": "invalid JSON body"}, status=400)
    return data, None


# ── GET /api/state ────────────────────────────────────────────────────────────

@require_GET
def api_state(request):
    """Return per-day share, first drift day, memory counts and decision count."""
    tenant = request.GET.get("tenant", "")
    err = _bad_tenant(tenant)
    if err:
        return err

    g = tg(tenant)
    sbd = share_by_day(g)
    fdd = first_drift_day(g)

    mem_rows = g.ro_query(_Q_MEM_COUNTS, {}).result_set
    counts = {row[0]: row[1] for row in mem_rows}

    dec_rows = g.ro_query(_Q_DECISION_COUNT, {}).result_set
    decision_count = dec_rows[0][0] if dec_rows else 0

    return JsonResponse({
        "tenant": tenant,
        "share_by_day": {str(k): v for k, v in sbd.items()},
        "first_drift_day": fdd,
        "memories": {
            "active":      counts.get("active", 0),
            "quarantined": counts.get("quarantined", 0),
        },
        "decision_count": decision_count,
    })


# ── GET /api/graph ────────────────────────────────────────────────────────────

@require_GET
def api_graph(request):
    """Return nodes and edges shaped for Cytoscape.js."""
    tenant = request.GET.get("tenant", "")
    err = _bad_tenant(tenant)
    if err:
        return err

    g = tg(tenant)

    # ── nodes ──
    node_rows = g.ro_query(_Q_GRAPH_NODES, {}).result_set
    nodes = []
    for row in node_rows:
        labels, props = row[0], row[1]
        label = labels[0] if labels else "Unknown"

        if label == "Memory":
            ntype = "memory"
        elif label == "Source":
            ntype = "source"
        elif label == "Tool":
            ntype = "tool"
        else:
            continue  # skip Agent/Task/Decision nodes – not needed for Cytoscape

        node = {
            "id":    props.get("id") or props.get("name"),
            "label": props.get("text") or props.get("name") or props.get("id"),
            "type":  ntype,
            "status": props.get("status", ""),
        }
        if ntype == "source":
            node["trust"] = props.get("trust")
        if ntype == "memory":
            node["kind"] = props.get("kind", "")
        nodes.append(node)

    # ── edges ──
    edge_rows = g.ro_query(_Q_GRAPH_EDGES, {}).result_set
    edges = []
    for row in edge_rows:
        src_id, tgt_id, rtype = row[0], row[1], row[2]
        # src_id/tgt_id come from coalesce(a.id, a.name); Tools use .name
        edges.append({"source": src_id, "target": tgt_id, "type": rtype})

    return JsonResponse({"nodes": nodes, "edges": edges})


# ── POST /api/simulate ────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def api_simulate(request):
    """Seed + run the 6-day timeline; return decision count."""
    data, err = _parse_body(request)
    if err:
        return err

    tenant = data.get("tenant", "")
    err = _bad_tenant(tenant)
    if err:
        return err

    attack = bool(data.get("attack", False))
    decision_count = simulate_tenant(tenant, attack)
    return JsonResponse({"tenant": tenant, "attack": attack, "decisions": decision_count})


# ── POST /api/reset ───────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def api_reset(request):
    """Seed only (no simulation); returns confirmation."""
    data, err = _parse_body(request)
    if err:
        return err

    tenant = data.get("tenant", "")
    err = _bad_tenant(tenant)
    if err:
        return err

    seed_tenant(tenant)
    return JsonResponse({"tenant": tenant, "seeded": True})


# ── GET /api/investigate ──────────────────────────────────────────────────────

@require_GET
def api_investigate(request):
    """Return first drift day, culprits ranked by suspicion, and suggested source."""
    tenant = request.GET.get("tenant", "")
    err = _bad_tenant(tenant)
    if err:
        return err

    g = tg(tenant)
    fdd = first_drift_day(g)
    since_ts = fdd * 24 if fdd is not None else 0

    cul = culprits(g, UNVERIFIED, since_ts)

    # Enrich culprits with memory text and source name via a single query
    _Q_MEM_INFO = (
        "MATCH (m:Memory)-[:FROM]->(s:Source) "
        "WHERE m.id IN $ids "
        "RETURN m.id AS id, m.text AS text, m.kind AS kind, s.id AS sid, s.name AS sname"
    )
    ids = [r[0] for r in cul]
    info_rows = g.ro_query(_Q_MEM_INFO, {"ids": ids}).result_set
    info = {row[0]: {"text": row[1], "kind": row[2], "sid": row[3], "sname": row[4]}
            for row in info_rows}

    culprit_list = []
    for mid, hits, total, suspicion in cul:
        m = info.get(mid, {})
        culprit_list.append({
            "id":          mid,
            "text":        m.get("text", ""),
            "kind":        m.get("kind", ""),
            "source_name": m.get("sname", ""),
            "trust":       round(1 - suspicion * total / hits, 3) if hits else None,
            "hits":        hits,
            "total":       total,
            "suspicion":   suspicion,
        })

    # Suggested source = source id of the top culprit
    suggested_source = info.get(ids[0], {}).get("sid") if ids else None

    return JsonResponse({
        "first_drift_day":  fdd,
        "since_ts":         since_ts,
        "culprits":         culprit_list,
        "suggested_source": suggested_source,
    })


# ── GET /api/prove ────────────────────────────────────────────────────────────

@require_GET
def api_prove(request):
    """Compute blast radius and counterfactual for the given source."""
    tenant = request.GET.get("tenant", "")
    err = _bad_tenant(tenant)
    if err:
        return err

    source = request.GET.get("source", "")
    if not source:
        return JsonResponse({"error": "missing query param: source"}, status=400)

    g = tg(tenant)
    seeds = source_seed_ids(g, source)
    all_ids, affected_decisions, exposure = blast_radius(g, seeds)
    cf_flips = counterfactual(g, all_ids)
    nf = naive_flips(g)

    bad_rows = g.ro_query(
        "MATCH (d:Decision)-[:CHOSE]->(t:Tool {name: $tool}) RETURN count(d) AS n",
        {"tool": UNVERIFIED},
    ).result_set
    bad_total = bad_rows[0][0] if bad_rows else 0

    return JsonResponse({
        "suspects":         all_ids,
        "touched":          affected_decisions,
        "flipped":          [{"decision": d, "old": o, "new": n} for d, o, n in cf_flips],
        "naive_flipped":    len(nf),
        "bad_total":        bad_total,
        "exposure_touched": exposure,
        "exposure_flipped": len(cf_flips),
    })


# ── POST /api/quarantine ──────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def api_quarantine(request):
    """Quarantine the blast radius of *source*; return quarantined ids."""
    data, err = _parse_body(request)
    if err:
        return err

    tenant = data.get("tenant", "")
    err = _bad_tenant(tenant)
    if err:
        return err

    source = data.get("source", "")
    if not source:
        return JsonResponse({"error": "missing field: source"}, status=400)

    g = tg(tenant)
    seeds = source_seed_ids(g, source)
    all_ids, _, _ = blast_radius(g, seeds)
    quarantine(g, all_ids)
    return JsonResponse({"quarantined": all_ids})


# ── POST /api/replay ──────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def api_replay(request):
    """Run 2 extra days (days 6 and 7) exactly as in the acceptance command."""
    data, err = _parse_body(request)
    if err:
        return err

    tenant = data.get("tenant", "")
    err = _bad_tenant(tenant)
    if err:
        return err

    g = tg(tenant)
    dids = []
    j = 0

    for day in range(DAYS, DAYS + 2):
        for slot in range(TASKS_PER_DAY):
            topic = "large_payment" if slot % 2 == 0 else "small_payment"
            ts = day * 24 + 9 + slot
            did = f"r{j}"
            task_id = f"rtask_{j}"
            default = "bank_transfer" if topic == "large_payment" else "card_processor"

            retrieved = retrieve(g, topic, ts)
            chosen_tool = choose(retrieved, ts, default)

            g.query(_W_REPLAY_TASK,     {"id": task_id, "topic": topic, "amount": 1000, "ts": ts})
            g.query(_W_REPLAY_DECISION, {"id": did, "ts": ts, "day": day, "chosen_tool": chosen_tool})
            g.query(_W_REPLAY_FOR,      {"did": did, "tid": task_id})
            g.query(_W_REPLAY_CHOSE,    {"did": did, "tool": chosen_tool})

            for rank_pos, mem in enumerate(retrieved, start=1):
                g.query(_W_REPLAY_RETRIEVED, {"mid": mem["id"], "did": did, "rank": rank_pos})

            dids.append(did)
            j += 1

    rows = g.ro_query(_Q_COUNT_UNVERIFIED_POST, {"tool": UNVERIFIED, "dids": dids}).result_set
    unverified = rows[0][0] if rows else 0

    return JsonResponse({"decisions": dids, "unverified": unverified})
