# MemTrace

A Django + FalkorDB application that simulates AI agent memory poisoning, forensic investigation, and blast-radius quarantine. Everything is a deterministic simulation — no LLM calls, no external APIs.

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
2. Pick a tenant (`acme` or `globex`) and click **Run healthy week** or **Run week under attack**.
3. Toggle **Attack scenario** to inject poisoned memories.

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

```
1. simulate acme --attack          # inject poisoned memories, run 6 days
2. GET /api/state?tenant=acme      # see drift appear from day 3 onwards
3. GET /api/investigate?tenant=acme # identify top culprit memories
4. GET /api/prove?tenant=acme&source=src_web  # measure blast radius
5. POST /api/quarantine {"tenant":"acme","source":"src_web"}  # quarantine
6. POST /api/replay {"tenant":"acme"}          # days 6-7 are now clean
```

Run `python manage.py acceptance` at any point to verify all expected values match.
