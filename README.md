# MemTrace

**Who poisoned your agent's memory? Trace. Prove. Quarantine.**

Built for Graph Hacks by HowitzerScript (solo) · Track: Agent Memory and Coordination · Powered by FalkorDB

AI agents with persistent memory can be quietly steered by a single poisoned record, and the damage shows up days later as behavior drift. MemTrace finds the memory that changed the agent's behavior, proves it by replaying decisions without it, and quarantines everything derived from it.

A Django + FalkorDB application that simulates AI agent memory poisoning, forensic investigation, and blast-radius quarantine. Everything is a deterministic simulation — no LLM calls, no external APIs.

---

## Why the graph is essential

Provenance (decision to memory to source), lineage (memory DERIVED_FROM memory, at any depth) and blast radius are variable-length path queries over connected memory. Counterfactual replay depends on RETRIEVED_IN edges that record exactly which memories each decision used. Each tenant has its own isolated graph (mt_acme, mt_globex). Remove FalkorDB and there is nothing left to trace.

---

## Graph data model

Derived from [`core/seed.py`](core/seed.py) and [`core/agents.py`](core/agents.py).

### Node labels

| Label | Properties |
|---|---|
| `Source` | `id`, `name`, `kind`, `trust` |
| `Tool` | `name`, `verified` |
| `Agent` | `name`, `role` |
| `Memory` | `id`, `text`, `kind`, `topic`, `confidence`, `created_at`, `status` |
| `Task` | `id`, `topic`, `amount`, `ts` |
| `Decision` | `id`, `ts`, `day`, `chosen_tool` |

### Relationship types

| Relationship | Direction | From → To | Properties |
|---|---|---|---|
| `FROM` | `(Memory)→(Source)` | Memory to Source | _(none)_ |
| `RECOMMENDS` | `(Memory)→(Tool)` | Memory to Tool | `weight` |
| `DERIVED_FROM` | `(Memory)→(Memory)` | Derived memory to source memory | _(none)_ |
| `FOR` | `(Decision)→(Task)` | Decision to Task | _(none)_ |
| `CHOSE` | `(Decision)→(Tool)` | Decision to Tool | _(none)_ |
| `MADE` | `(Agent)→(Decision)` | Agent to Decision | _(none)_ |
| `RETRIEVED_IN` | `(Memory)→(Decision)` | Memory to Decision | `rank` |
| `WROTE` | `(Agent)→(Memory)` | Agent to Memory | _(none)_ |

---

## Key Cypher queries

### [`core/agents.py`](core/agents.py)

Retrieve active memories for a topic up to a timestamp, excluding blocked ids:

```cypher
MATCH (m:Memory {topic: $topic})-[r:RECOMMENDS]->(t:Tool)
WHERE m.status = 'active' AND m.created_at <= $ts AND NOT (m.id IN $blocked)
RETURN m.id AS id, m.confidence AS conf, m.created_at AS ts,
t.name AS tool, r.weight AS weight
```

Retrieve active episodic memories for a topic (used by consolidation):

```cypher
MATCH (m:Memory {topic: $topic})-[r:RECOMMENDS]->(t:Tool)
WHERE m.status = 'active' AND m.kind = 'episodic' AND m.created_at <= $ts
RETURN m.id AS id, m.confidence AS conf, m.created_at AS ts,
t.name AS tool, r.weight AS weight
```

Create a Task node:

```cypher
CREATE (:Task {id: $id, topic: $topic, amount: $amount, ts: $ts})
```

Create a Decision node:

```cypher
CREATE (:Decision {id: $id, ts: $ts, day: $day, chosen_tool: $chosen_tool})
```

Link a Decision to its Task:

```cypher
MATCH (d:Decision {id: $did}), (t:Task {id: $tid})
CREATE (d)-[:FOR]->(t)
```

Link a Decision to the Tool it chose:

```cypher
MATCH (d:Decision {id: $did}), (t:Tool {name: $tool})
CREATE (d)-[:CHOSE]->(t)
```

Record which Agent made a Decision:

```cypher
MATCH (a:Agent {name: $agent}), (d:Decision {id: $did})
CREATE (a)-[:MADE]->(d)
```

Record that a Memory was retrieved during a Decision (with rank):

```cypher
MATCH (m:Memory {id: $mid}), (d:Decision {id: $did})
CREATE (m)-[:RETRIEVED_IN {rank: $rank}]->(d)
```

Create a Memory node:

```cypher
CREATE (:Memory {id: $id, text: $text, kind: $kind, topic: $topic,
confidence: $confidence, created_at: $created_at, status: 'active'})
```

Link a Memory to its Source:

```cypher
MATCH (m:Memory {id: $mid}), (s:Source {id: $sid})
CREATE (m)-[:FROM]->(s)
```

Link a Memory to a Tool it recommends (with weight):

```cypher
MATCH (m:Memory {id: $mid}), (t:Tool {name: $tool})
CREATE (m)-[:RECOMMENDS {weight: $weight}]->(t)
```

Record that an Agent wrote a Memory:

```cypher
MATCH (a:Agent {name: $agent}), (m:Memory {id: $mid})
CREATE (a)-[:WROTE]->(m)
```

Link a derived Memory back to its source Memory:

```cypher
MATCH (new:Memory {id: $new_id}), (src:Memory {id: $src_id})
CREATE (new)-[:DERIVED_FROM]->(src)
```

### [`core/forensics.py`](core/forensics.py)

Count decisions per tool per day (used by share_by_day):

```cypher
MATCH (d:Decision)-[r:CHOSE]->(t:Tool)
RETURN d.day AS day, t.name AS tool, count(r) AS n
```

Count total retrievals per memory across all decisions:

```cypher
MATCH (m:Memory)-[r:RETRIEVED_IN]->(d:Decision)
RETURN m.id AS id, count(d) AS total
```

Count retrievals per memory for decisions that chose a specific tool at or after a timestamp:

```cypher
MATCH (m:Memory)-[r:RETRIEVED_IN]->(d:Decision)-[c:CHOSE]->(t:Tool)
WHERE t.name = $tool AND d.ts >= $since
RETURN m.id AS id, count(r) AS hits
```

Get the trust score for each memory via its source:

```cypher
MATCH (m:Memory)-[f:FROM]->(s:Source)
RETURN m.id AS id, s.trust AS trust
```

Get the ids of all memories from a given source:

```cypher
MATCH (m:Memory)-[f:FROM]->(s:Source {id: $source_id})
RETURN m.id AS id
```

Get all memories derived (directly or transitively, depth 1–6) from a set of seed ids:

```cypher
MATCH (d:Memory)-[:DERIVED_FROM*1..6]->(bad:Memory)
WHERE bad.id IN $ids
RETURN DISTINCT d.id AS id
```

Get all decisions (and their task amounts) that retrieved any memory in a set:

```cypher
MATCH (m:Memory)-[r:RETRIEVED_IN]->(d:Decision)-[f:FOR]->(t:Task)
WHERE m.id IN $ids
RETURN DISTINCT d.id AS did, t.amount AS amount
```

Walk the full provenance path from a memory up to 5 hops to a root, then to its source:

```cypher
MATCH p = (m:Memory {id: $id})-[:DERIVED_FROM*1..5]->(root:Memory)
-[f:FROM]->(s:Source)
RETURN p
```

Get the direct source of a memory:

```cypher
MATCH (m:Memory {id: $id})-[f:FROM]->(s:Source)
RETURN s.id AS sid, s.name AS name, s.kind AS kind, s.trust AS trust
```

Set status = 'quarantined' on a list of memory ids:

```cypher
MATCH (m:Memory) WHERE m.id IN $ids
SET m.status = 'quarantined'
RETURN count(m) AS n
```

Counterfactual — re-fetch affected decisions' stored properties:

```cypher
MATCH (d:Decision)-[f:FOR]->(t:Task), (d)-[c:CHOSE]->(tool:Tool)
WHERE d.id IN $dids
RETURN d.id AS did, d.ts AS ts, t.topic AS topic, tool.name AS old_tool
```

Naive counterfactual — find memories whose source has trust < 0.5:

```cypher
MATCH (m:Memory)-[f:FROM]->(s:Source)
WHERE s.trust < 0.5
RETURN m.id AS id
```

### [`core/seed.py`](core/seed.py)

Create a Source node:

```cypher
CREATE (:Source {id: $id, name: $name, kind: $kind, trust: $trust})
```

Create a Tool node:

```cypher
CREATE (:Tool {name: $name, verified: $verified})
```

Create an Agent node:

```cypher
CREATE (:Agent {name: $name, role: $role})
```

Create a Memory node (seed variant, with explicit status):

```cypher
CREATE (:Memory {id: $id, text: $text, kind: $kind, topic: $topic,
confidence: $confidence, created_at: $created_at, status: $status})
```

Link a Memory to its Source (seed variant):

```cypher
MATCH (m:Memory {id: $mid}), (s:Source {id: $sid})
CREATE (m)-[:FROM]->(s)
```

Link a Memory to a Tool it recommends, with weight (seed variant):

```cypher
MATCH (m:Memory {id: $mid}), (t:Tool {name: $tool})
CREATE (m)-[:RECOMMENDS {weight: $weight}]->(t)
```

---

## Prerequisites

| Dependency | Minimum version | Notes |
|---|---|---|
| Python | 3.12 | |
| FalkorDB | any recent release | Runs on port **6379** by default |
| Docker (optional) | any | Easiest way to run FalkorDB locally |

---

## 1. Clone and set up the Python environment

```bash
git clone https://github.com/yoongles7/memtrace.git
cd memtrace

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

---

## 2. Start FalkorDB

FalkorDB exposes the same port as Redis (`6379`). The quickest way to start it is with Docker:

```bash
docker run -d --name falkordb -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest
```

Open http://localhost:3000. You should see the FalkorDB Browser. If you already have FalkorDB or a compatible Redis/FalkorDB instance running elsewhere, set the environment variables described in [Configuration](#configuration) before proceeding.

**FalkorDB Browser login:** host `localhost`, port `6379`, leave username and password empty, TLS off.

---

## 3. Apply Django migrations

The app uses SQLite for Django's built-in tables (sessions, admin, auth). Run migrations once:

```bash
python manage.py migrate
```

---

## 4. Run the development server

```bash
python manage.py runserver
```

Open **http://localhost:8000** in your browser. You will see the landing page. The interactive dashboard is at **http://localhost:8000/app/**.

The graph will be empty until you run a simulation (step 5).

---

## 5. Seed and simulate

### Option A — via the web dashboard

1. Open **http://localhost:8000/app/**.
2. Pick a tenant (`acme` or `globex`).
3. Click **Run week under attack** (or **Run healthy week** for no attack), then use **Investigate**, **Prove it**, **Quarantine** and **Replay next 2 days** in order.

### Option B — via management commands

**Seed only** (creates nodes, no decisions):

```bash
python manage.py seed --tenant acme
# or seed both tenants at once:
python manage.py seed --all
```

**Simulate** (seeds + runs the full 6-day timeline):

```bash
# Normal run — no attack memories
python manage.py simulate --tenant acme

# Attack run — injects poisoned memories on days 2-4
python manage.py simulate --tenant acme --attack
```

Valid tenants: `acme`, `globex`.

---

## 6. Verify with the acceptance suite

Run all end-to-end checks against the hardcoded reference values:

```bash
python manage.py acceptance
```

Expected output:

```
--- Step 1: globex (no attack) ---
  PASS  globex share_by_day all 0

--- Step 2: acme (attack) ---
  PASS  acme share_by_day
  PASS  acme first_drift_day
  PASS  acme since_ts
  PASS  acme culprits
  PASS  acme source_seed_ids('src_web') + descendants
  PASS  acme affected decisions count
  PASS  acme exposure
  PASS  acme decisions choosing UNVERIFIED
  PASS  acme counterfactual flips
  PASS  acme naive_flips

--- Step 3: quarantine blast radius, replay days 6-7 ---
  PASS  acme post-quarantine UNVERIFIED decisions

--- Step 4: globex tenant isolation ---
  PASS  globex quarantined memories

========================================
Result: 13/13 checks passed, 0 failed.
ALL CHECKS PASSED
```

> **Note:** `python manage.py acceptance` reseeds and resets the acme and globex tenants, so run the demo after it, not before.

---

## Configuration

All connection settings are read from environment variables. The defaults work with a local FalkorDB instance started via Docker (step 1).

| Variable | Default | Description |
|---|---|---|
| `FALKOR_HOST` | `localhost` | FalkorDB hostname |
| `FALKOR_PORT` | `6379` | FalkorDB port |
| `FALKOR_USERNAME` | _(unset)_ | Only needed if auth is enabled |
| `FALKOR_PASSWORD` | _(unset)_ | Only needed if auth is enabled |

Example override:

```bash
export FALKOR_HOST=my-falkordb-server
export FALKOR_PORT=6380
python manage.py runserver
```

---

## Project layout

```
memtrace/
├── config/               # Django project settings and URL root
│   ├── settings.py
│   └── urls.py
├── core/
│   ├── params.py         # Frozen constants (K, DECAY, DAYS, SOURCES, …)
│   ├── graph.py          # FalkorDB connection helper — tg(tenant)
│   ├── seed.py           # Builds the tenant graph (nodes + edges)
│   ├── agents.py         # Retrieval, ranking, and decision logic
│   ├── simulation.py     # Orchestrates the 6-day timeline
│   ├── forensics.py      # share_by_day, culprits, blast_radius, quarantine
│   ├── views.py          # JSON API endpoints
│   ├── urls.py           # API URL routes (/api/*)
│   ├── reference/
│   │   └── memtrace_reference.py  # Validated reference model (AI-assisted)
│   └── management/
│       └── commands/
│           ├── seed.py       # python manage.py seed --tenant acme
│           ├── simulate.py   # python manage.py simulate --tenant acme [--attack]
│           └── acceptance.py # python manage.py acceptance
├── templates/
│   ├── landing.html
│   ├── how_it_works.html
│   └── dashboard.html
├── manage.py
└── requirements.txt
```

---

## API reference

All endpoints are under `/api/`. Tenants must be `acme` or `globex`.

| Method | Path | Parameters | Description |
|---|---|---|---|
| `GET` | `/api/state` | `?tenant=acme` | Per-day share, memory counts, first drift day |
| `GET` | `/api/graph` | `?tenant=acme` | Nodes and edges shaped for Cytoscape.js |
| `GET` | `/api/investigate` | `?tenant=acme` | First drift day, ranked culprit memories |
| `GET` | `/api/prove` | `?tenant=acme&source=src_web` | Blast radius and counterfactual flips |
| `POST` | `/api/simulate` | `{"tenant":"acme","attack":true}` | Seed + run the 6-day timeline |
| `POST` | `/api/reset` | `{"tenant":"acme"}` | Seed only (clears existing data) |
| `POST` | `/api/quarantine` | `{"tenant":"acme","source":"src_web"}` | Mark blast radius memories as quarantined |
| `POST` | `/api/replay` | `{"tenant":"acme"}` | Run 2 extra days after quarantine |

---

## Typical demo flow

### UI flow

1. Open **http://localhost:8000/app/**, select tenant `acme`.
2. Click **Reset** to seed a clean graph.
3. Click **Run week under attack** to inject poisoned memories and run 6 days.
4. Click **Investigate** — the dashboard highlights the first drift day and ranks culprit memories.
5. Click **Prove it** — counterfactual replay shows which decisions would have flipped.
6. Click **Quarantine** — blast-radius memories are marked quarantined.
7. Click **Replay next 2 days** — the new decisions use no quarantined memories.
8. Switch tenant to `globex` to confirm it is entirely untouched.

### API flow

```
1. simulate acme --attack          # inject poisoned memories, run 6 days
2. GET /api/state?tenant=acme      # see drift appear from day 3 onwards
3. GET /api/investigate?tenant=acme # identify top culprit memories
4. GET /api/prove?tenant=acme&source=src_web  # measure blast radius
5. POST /api/quarantine {"tenant":"acme","source":"src_web"}  # quarantine
6. POST /api/replay {"tenant":"acme"}          # days 6-7 are now clean
```

Run `python manage.py acceptance` at any point to verify all expected values match.

---

## Demo results (synthetic data)

Unverified gateway share goes from 0% to 50% from day 3. 12 decisions used it. 16 decisions retrieved the four suspect memories (A, B, C and summary S1); 12 changed outcome when replayed without them. A source-only trust filter would fix only 4 of the 12. After quarantine, 0 of the next 16 decisions use the unverified gateway. Exposure: $47,625 touched, $35,805 flipped.

---

## Limitations

Agents and the attack are simulated on synthetic data (no LLMs, no real incidents). Retrieval shows correlation; counterfactual replay supports causation within this deterministic ranking model. The tracing layer needs agents that log which memories they retrieve. Memory-poisoning research already exists; this project is a working graph-based containment tool, not a claim of being first.

---

## Deployment

No live deployment is provided, because FalkorDB Cloud's free tier stops idle instances. Run it locally with the steps above and see the demo video: <VIDEO_LINK_PLACEHOLDER>.

---

## AI-use disclosure

IBM Bob generated most of the code from prompts and a tested reference model ([`core/reference/memtrace_reference.py`](core/reference/memtrace_reference.py), which was itself AI-assisted and is used only to validate results). Claude (Anthropic) was used for planning, research and drafting prompts and documentation. I reviewed the code and ran the acceptance suite (13 checks).

---

## Data

All data is synthetic and generated by the app. No private, personal or login-protected data is used.
