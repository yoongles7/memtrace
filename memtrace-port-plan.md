# MemTrace Port Plan — Django + FalkorDB

## Top-Level Overview

Port `core/reference/memtrace_reference.py` (pure-Python, storage-agnostic) into a
working Django + FalkorDB application.  Every number, formula, name, text, id and
timeline in the reference is **frozen**; only the storage layer changes.  The graph
database replaces in-memory dicts; Django management commands drive seeding and
acceptance testing; API views and a dashboard template expose the results.

One graph per tenant, named `mt_<tenant>`.  All Cypher parameters are passed as
`$variables`; no f-string interpolation into queries.  All Cypher constants live at the
top of their module.

---

## Sub-Tasks

### 1 — `core/params.py` — frozen constants

**Intent**  
Single authoritative place for every number and name copied verbatim from the reference.
Nothing else may hard-code these values.

**Expected Outcomes**
- Module importable with no side-effects.
- Every constant in the reference (`K`, `DECAY`, `DAYS`, `TASKS_PER_DAY`, `TOOLS`,
  `UNVERIFIED`, `SEED`, `SOURCES`) present, identical.

**Todo List**
- [ ] Create `core/params.py`.
- [ ] Copy `K = 3`, `DECAY = 0.85`, `DAYS = 6`, `TASKS_PER_DAY = 8` verbatim.
- [ ] Copy `TOOLS`, `UNVERIFIED`, `SEED` verbatim.
- [ ] Copy `SOURCES` dict verbatim (all four source ids, names, kinds, trust values).

**Relevant Context**
- Reference lines 12–26: all constants and `SOURCES`.

**Status** `[ ] pending`

---

### 2 — `core/graph.py` — FalkorDB connection helper

**Intent**  
Thin wrapper that opens (or reuses) a FalkorDB connection and returns the named graph
for a tenant.  Keeps connection config in one place.

**Expected Outcomes**
- `get_graph(tenant)` returns `db.select_graph("mt_<tenant>")`.
- Host `localhost`, port `6379` are the only connection values; they are not repeated
  elsewhere.

**Todo List**
- [ ] Create `core/graph.py`.
- [ ] Add module-level `FalkorDB` instance (`host="localhost"`, `port=6379`).
- [ ] Implement `get_graph(tenant: str)` — one-liner calling `db.select_graph`.

**Relevant Context**
- BOB_RULES: `FalkorDB(host=..., port=...)`, `db.select_graph(name)`.

**Status** `[ ] pending`

---

### 3 — `core/seed.py` — graph schema + world builder

**Intent**  
Write `Source`, `Memory` and `Decision` nodes plus `RECOMMENDS`, `DERIVED_FROM` and
`RETRIEVED` edges to FalkorDB, replicating `build_world()` exactly (including the
attack-memory schedule and the `consolidate()` call on day 4).

**Expected Outcomes**
- After `seed_world(tenant, attack=True)` the graph contains exactly the same nodes
  and edges that the reference produces.
- After `seed_world(tenant, attack=False)` no attack memories or derived summaries
  exist.
- Running twice clears the previous graph first (idempotent).

**Cypher queries needed**  
All defined as module-level string constants:

| Constant | Purpose |
|---|---|
| `Q_DROP_GRAPH` | `CALL db.dropGraph($name)` — wipe the tenant graph |
| `Q_CREATE_SOURCE` | `MERGE (:Source {id:$id, name:$name, kind:$kind, trust:$trust})` |
| `Q_CREATE_MEMORY` | `CREATE (:Memory {id:$id, text:$text, kind:$kind, topic:$topic, conf:$conf, ts:$ts, source:$source, status:'active'})` |
| `Q_CREATE_RECOMMENDS` | `MATCH (m:Memory {id:$mid}), (s:Source {id:$sid}) MERGE (m)-[:RECOMMENDS {tool:$tool, weight:$w}]->(:Tool {name:$tool})` — one edge per (memory, tool) pair |
| `Q_CREATE_DERIVED_FROM` | `MATCH (child:Memory {id:$child}), (parent:Memory {id:$parent}) MERGE (child)-[:DERIVED_FROM]->(parent)` |
| `Q_CREATE_DECISION` | `CREATE (:Decision {id:$id, ts:$ts, day:$day, topic:$topic, amount:$amount, tool:$tool})` |
| `Q_CREATE_RETRIEVED` | `MATCH (d:Decision {id:$did}), (m:Memory {id:$mid}) MERGE (d)-[:RETRIEVED]->(m)` |

**Todo List**
- [ ] Create `core/seed.py`; import `params`, `graph`.
- [ ] Define all Cypher constants listed above.
- [ ] Implement `_drop_and_init(g)` — drops graph if it exists, creates `Source` nodes
  for all four entries in `params.SOURCES`.
- [ ] Implement `_add_memory(g, id, text, kind, topic, conf, ts, source, recs,
  derived_from=())` — creates `Memory` node, `RECOMMENDS` edges to `Tool` nodes,
  `DERIVED_FROM` edges to parent memories.
- [ ] Implement `_consolidate(g, topic, ts, new_id)` — graph-read version of
  `consolidate()`: reads active episodic memories for the topic, groups by tool, fires
  if `len >= 2`, writes the semantic summary node with `conf = min(0.95, avg_conf +
  0.05)` and `w = avg_weight`.
- [ ] Implement `seed_world(tenant, attack=True)` — replicate `build_world()` loop:
  days 0-5, attack memories at day 2/3/4 hours +7, consolidate call at day 4 hour +8,
  then the 8 `run_decision` calls per day (stored as `Decision` nodes +
  `RETRIEVED` edges using the agent logic from `core/agents.py`).
- [ ] Import `random.Random(SEED)` with the fixed seed; use `rng.randint` for amounts.

**Relevant Context**
- Reference lines 135–153 (`build_world`), lines 72–84 (`consolidate`).
- Sub-task 4 (agents) must be implemented **before** this sub-task can store decisions.

**Status** `[ ] pending`

---

### 4 — `core/agents.py` — retrieval + decision logic

**Intent**  
Pure functions that operate against FalkorDB (read-only) to replicate `rank()`,
`retrieve()`, `choose()`, `run_decision()`, and `quarantined_ids()`.

**Expected Outcomes**
- `retrieve(g, topic, now_ts, blocked)` returns up to `K` `Memory` node dicts sorted
  by `conf * DECAY^age_days DESC`, ties broken by `id ASC`.
- `choose(retrieved, now_ts, default)` returns the same tool name as the reference.
- `run_decision(g, i, ts, day, topic, amount)` creates a `Decision` node and
  `RETRIEVED` edges without touching Python state.

**Cypher queries needed**

| Constant | Purpose |
|---|---|
| `Q_GET_QUARANTINED` | `MATCH (m:Memory {status:'quarantined'}) RETURN m.id` |
| `Q_RETRIEVE_MEMS` | `MATCH (m:Memory) WHERE m.topic=$topic AND m.ts<=$ts AND NOT m.id IN $blocked RETURN m.id, m.conf, m.ts, m.status` — Python side sorts and slices to K |
| `Q_GET_RECS` | `MATCH (m:Memory {id:$mid})-[r:RECOMMENDS]->() RETURN r.tool, r.weight` |

**Todo List**
- [ ] Create `core/agents.py`; import `params`.
- [ ] Define Cypher constants.
- [ ] Implement `rank(conf, ts, now_ts)` — `conf * (DECAY ** ((now_ts - ts) / 24.0))`.
- [ ] Implement `retrieve(g, topic, now_ts, blocked=frozenset())` — query, sort,
  slice to `K`.
- [ ] Implement `choose(retrieved_rows, now_ts, default)` — accumulate
  `score[tool] += w * rank(conf, ts, now_ts)` across all `RECOMMENDS` edges of the
  retrieved memories; return `max(TOOLS, key=lambda t: (score[t], -TOOLS.index(t)))`.
- [ ] Implement `quarantined_ids(g)` — returns a set of quarantined memory ids.
- [ ] Implement `run_decision(g, i, ts, day, topic, amount)` — calls `retrieve` and
  `choose`, writes `Decision` node and `RETRIEVED` edges (uses `Q_CREATE_DECISION`
  and `Q_CREATE_RETRIEVED` from `seed.py`).

**Relevant Context**
- Reference lines 43–70.
- `run_decision` in this module writes to the graph; `seed.py` calls it.

**Status** `[ ] pending`

---

### 5 — `core/forensics.py` — analysis queries

**Intent**  
Graph-backed implementations of `share_by_day()`, `culprits()`, `descendants()`,
`blast_radius()`, `counterfactual()`, and `quarantine()`.

**Expected Outcomes**
- Each function returns the same values as its reference counterpart when run against
  a seeded `attack=True` graph.

**Cypher queries needed**

| Constant | Purpose |
|---|---|
| `Q_SHARE_BY_DAY` | `MATCH (d:Decision {day:$day}) RETURN d.tool, count(d) AS n` — one call per day, Python computes share |
| `Q_CULPRITS_HITS` | `MATCH (d:Decision)-[:RETRIEVED]->(m:Memory) WHERE d.tool=$tool AND d.ts>=$since RETURN m.id, count(d) AS hits` |
| `Q_CULPRITS_TOTAL` | `MATCH (d:Decision)-[:RETRIEVED]->(m:Memory) RETURN m.id, count(d) AS total` |
| `Q_MEM_SOURCE` | `MATCH (m:Memory {id:$id}) RETURN m.source` |
| `Q_DESCENDANTS` | `MATCH (child:Memory)-[:DERIVED_FROM*1..6]->(ancestor:Memory) WHERE ancestor.id IN $seeds RETURN DISTINCT child.id` |
| `Q_DECISIONS_TOUCHING` | `MATCH (d:Decision)-[:RETRIEVED]->(m:Memory) WHERE m.id IN $ids RETURN DISTINCT d.id, d.ts, d.day, d.topic, d.amount, d.tool, d.retrieved` — note: `d.retrieved` not stored; reconstruct by pulling `RETRIEVED` edges per decision in Python |
| `Q_RETRIEVED_FOR_DEC` | `MATCH (d:Decision {id:$did})-[:RETRIEVED]->(m:Memory) RETURN m.id` |
| `Q_QUARANTINE` | `MATCH (m:Memory) WHERE m.id IN $ids SET m.status='quarantined'` |

**Todo List**
- [ ] Create `core/forensics.py`; import `params`, `graph`, `agents`.
- [ ] Define all Cypher constants.
- [ ] Implement `share_by_day(g, tool)` — for each day 0..DAYS-1, query decisions by
  day, compute `round(100 * tool_count / total)`.
- [ ] Implement `culprits(g, tool, since_ts)` — join hits and total per memory id,
  look up source trust from `params.SOURCES`, compute suspicion score
  `h / total * (1 - trust)`, return sorted list of `(mid, hits, total, suspicion)`.
- [ ] Implement `descendants(g, seed_ids)` — single variable-length DERIVED_FROM query.
- [ ] Implement `blast_radius(g, seed_ids)` — union seeds + descendants, find all
  touching decisions, sum amounts.
- [ ] Implement `counterfactual(g, suspect_ids)` — for each decision that retrieved
  a suspect memory, replay `retrieve` with suspect ids blocked and `choose`; collect
  flips.
- [ ] Implement `quarantine(g, ids)` — set `status='quarantined'` on matched memories.

**Relevant Context**
- Reference lines 86–132.
- `counterfactual` must re-read the decision's original `topic` and `ts` from the
  graph, then call `agents.retrieve` and `agents.choose`.

**Status** `[ ] pending`

---

### 6 — Management command `simulate`

**Intent**  
`python manage.py simulate [--tenant demo] [--no-attack]` drops and re-seeds the
FalkorDB graph for the named tenant so every demo starts from a clean known state.

**Expected Outcomes**
- Command prints a one-line summary (node count, decision count).
- Running it twice leaves the graph in the same state (idempotent).

**Todo List**
- [ ] Create `core/management/commands/simulate.py`.
- [ ] Add `--tenant` option (default `"demo"`).
- [ ] Add `--no-attack` flag (maps to `attack=False`).
- [ ] Call `seed.seed_world(tenant, attack=...)`.
- [ ] Print counts by querying `MATCH (n) RETURN labels(n)[0], count(n)`.

**Relevant Context**
- `core/management/commands/` directory already exists.

**Status** `[ ] pending`

---

### 7 — Management command `acceptance`

**Intent**  
`python manage.py acceptance [--tenant demo]` runs the five assertions from the
reference `__main__` block against the live FalkorDB graph, printing PASS/FAIL for each.

**Expected Outcomes**
- All five checks pass against an `attack=True` seeded graph.
- Output mirrors the reference `print` statements so the numbers are visible.

**Todo List**
- [ ] Create `core/management/commands/acceptance.py`.
- [ ] Add `--tenant` option (default `"demo"`).
- [ ] Replicate the five checks in order:
  1. Baseline: seed with `attack=False`, assert all days have 0% unverified share.
  2. Attack: seed with `attack=True`, check drift appears after day 1.
  3. Culprits: top culprit is in `{A, B, C, S1}`.
  4. Blast radius: `S1` is in the quarantine set.
  5. Flips: lineage quarantine fixes all bad decisions; naive filter misses some.
- [ ] Print PASS/FAIL after each check; exit with non-zero code on any failure.

**Relevant Context**
- Reference lines 155–202.
- Each check calls functions from `forensics.py` and `agents.py`.

**Status** `[ ] pending`

---

### 8 — API views (`core/views.py`) + URL wiring

**Intent**  
Lightweight JSON endpoints that the dashboard template uses via `fetch`.  No DRF;
plain `JsonResponse`.

**Expected Outcomes**
- `GET /api/share/?tenant=demo` → `{"days": [0,1,2,3,4,5], "share": [0,0,n,...]}`
- `GET /api/culprits/?tenant=demo&since=48` → `[{"id":…, "hits":…, "total":…, "suspicion":…}, …]`
- `GET /api/blast/?tenant=demo&seeds=A,B,C` → `{"ids":[…], "decisions":…, "exposure":…}`
- `GET /` → renders `templates/dashboard.html`

**Todo List**
- [ ] Add four view functions in `core/views.py`:
  - `dashboard(request)` — renders `dashboard.html`.
  - `share_view(request)` — calls `forensics.share_by_day`.
  - `culprits_view(request)` — calls `forensics.culprits`.
  - `blast_view(request)` — calls `forensics.blast_radius`.
- [ ] Wire all four routes in `config/urls.py`.

**Relevant Context**
- `config/urls.py` currently has only `admin/`.
- `config/settings.py` already sets `DIRS: [BASE_DIR / 'templates']`.

**Status** `[ ] pending`

---

### 9 — Dashboard template (`templates/dashboard.html`)

**Intent**  
Single-page HTML dashboard that visualises the three forensics stories: share drift,
culprit ranking, blast radius.  Static HTML + vanilla JS; no extra frontend tooling.

**Expected Outcomes**
- Page loads at `http://localhost:8000/`.
- Three sections: drift chart (table or simple bar), culprit table, blast radius summary.
- Each section fetches its JSON from the API views on page load.

**Todo List**
- [ ] Create `templates/dashboard.html`.
- [ ] Section 1 "Tool share by day" — table with rows for each day.
- [ ] Section 2 "Culprit ranking" — table with columns id, hits, total, suspicion.
- [ ] Section 3 "Blast radius" — inputs for seed ids, button to run, shows exposure.
- [ ] JS `fetch` calls to `/api/share/`, `/api/culprits/`, `/api/blast/`.

**Relevant Context**
- `config/settings.py`: `TEMPLATES[0]['DIRS'] = [BASE_DIR / 'templates']`.

**Status** `[ ] pending`

---

## Implementation Order

```
params → graph → agents → seed → forensics
                                      ↓
                           management: simulate → acceptance
                                      ↓
                           views + urls → dashboard template
```

Sub-tasks 1 and 2 have no dependencies and can be implemented first in any order.
Sub-task 4 (agents) must precede Sub-task 3 (seed) because seed calls run_decision.
Sub-tasks 6–9 depend on all core modules being complete.

---

## Graph Schema Summary

**Node labels & properties**

| Label | Properties |
|---|---|
| `Source` | `id`, `name`, `kind`, `trust` |
| `Memory` | `id`, `text`, `kind`, `topic`, `conf`, `ts`, `source`, `status` |
| `Tool` | `name` |
| `Decision` | `id`, `ts`, `day`, `topic`, `amount`, `tool` |

**Relationship types**

| Type | From → To | Properties |
|---|---|---|
| `RECOMMENDS` | `Memory → Tool` | `tool`, `weight` |
| `DERIVED_FROM` | `Memory → Memory` | — |
| `RETRIEVED` | `Decision → Memory` | — |
