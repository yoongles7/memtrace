"""
MemTrace reference logic (pure Python, storage-agnostic).

Purpose: a TESTED specification of the demo behaviour. The real project stores
the same entities as nodes/edges in FalkorDB; this file proves the numbers,
formulas and flows produce the demo story. Run:  python memtrace_reference.py
"""
import random
from collections import defaultdict

# ---------------- tunable parameters (copy these into seed/agents) ----------------
K = 3                 # memories retrieved per decision
DECAY = 0.85          # per-day recency decay
DAYS = 6              # simulated days 0..5
TASKS_PER_DAY = 8     # half large payments, half small
TOOLS = ["bank_transfer", "card_processor", "swiftsettle_gateway"]
UNVERIFIED = "swiftsettle_gateway"
SEED = 42

# ---------------- data model (mirrors the graph) ----------------
SOURCES = {
    "src_policy": {"name": "Payments Policy Handbook", "kind": "policy", "trust": 1.0},
    "src_wiki":   {"name": "Finance Wiki",             "kind": "wiki",   "trust": 0.9},
    "src_web":    {"name": "vendor-blog.example",      "kind": "web",    "trust": 0.2},
    "src_agent":  {"name": "Consolidator Agent",       "kind": "agent",  "trust": 0.6},
}

class Mem:
    def __init__(self, id, text, kind, topic, conf, ts, source, recs, derived_from=()):
        self.id, self.text, self.kind, self.topic = id, text, kind, topic
        self.conf, self.ts, self.source = conf, ts, source
        self.recs = dict(recs)                 # tool -> weight   (RECOMMENDS edges)
        self.derived_from = list(derived_from) # DERIVED_FROM edges
        self.status = "active"

MEMS = {}
DECISIONS = []   # dicts: id, ts, day, topic, amount, tool, retrieved(list of ids)

def add_mem(*a, **k):
    m = Mem(*a, **k); MEMS[m.id] = m; return m

# ---------------- agent logic ----------------
def rank(m, now_ts):
    age_days = (now_ts - m.ts) / 24.0
    return m.conf * (DECAY ** age_days)

def retrieve(topic, now_ts, blocked=frozenset()):
    cands = [m for m in MEMS.values()
             if m.topic == topic and m.ts <= now_ts and m.id not in blocked]
    cands.sort(key=lambda m: (-rank(m, now_ts), m.id))
    return cands[:K]

def choose(retrieved, now_ts, default):
    score = defaultdict(float)
    for m in retrieved:
        for tool, w in m.recs.items():
            score[tool] += w * rank(m, now_ts)
    if not score:
        return default
    return max(TOOLS, key=lambda t: (score[t], -TOOLS.index(t)))

def quarantined_ids():
    return {m.id for m in MEMS.values() if m.status == "quarantined"}

def run_decision(i, ts, day, topic, amount):
    default = "bank_transfer" if topic == "large_payment" else "card_processor"
    r = retrieve(topic, ts, quarantined_ids())
    tool = choose(r, ts, default)
    DECISIONS.append(dict(id=f"d{i}", ts=ts, day=day, topic=topic, amount=amount,
                          tool=tool, retrieved=[m.id for m in r]))

# consolidator: derive a semantic summary when >=2 active memories on a topic push the same tool
def consolidate(topic, ts, new_id):
    groups = defaultdict(list)
    for m in MEMS.values():
        if m.topic == topic and m.status == "active" and m.kind == "episodic" and m.ts <= ts:
            for t in m.recs:
                groups[t].append(m)
    for tool, ms in groups.items():
        if len(ms) >= 2:
            conf = min(0.95, sum(m.conf for m in ms) / len(ms) + 0.05)
            w = sum(m.recs[tool] for m in ms) / len(ms)
            return add_mem(new_id, f"Summary: {tool} preferred for {topic}", "semantic", topic,
                           conf, ts, "src_agent", {tool: w}, derived_from=[m.id for m in ms])

# ---------------- forensics ----------------
def share_by_day(tool):
    out = {}
    for d in range(DAYS):
        ds = [x for x in DECISIONS if x["day"] == d]
        out[d] = round(100 * sum(1 for x in ds if x["tool"] == tool) / len(ds)) if ds else 0
    return out

def culprits(tool, since_ts):
    hits, total = defaultdict(int), defaultdict(int)
    for d in DECISIONS:
        for mid in d["retrieved"]:
            total[mid] += 1
            if d["tool"] == tool and d["ts"] >= since_ts:
                hits[mid] += 1
    rows = []
    for mid, h in hits.items():
        m = MEMS[mid]
        trust = SOURCES[m.source]["trust"]
        rows.append((mid, h, total[mid], round(h / total[mid] * (1 - trust), 3)))
    return sorted(rows, key=lambda r: (-r[3], -r[1], r[0]))

def descendants(seed_ids):
    found, frontier = set(), set(seed_ids)
    while frontier:
        nxt = {m.id for m in MEMS.values() if set(m.derived_from) & frontier} - found - set(seed_ids)
        found |= nxt; frontier = nxt
    return found

def blast_radius(seed_ids):
    ids = set(seed_ids) | descendants(seed_ids)
    affected = [d for d in DECISIONS if set(d["retrieved"]) & ids]
    return ids, affected, sum(d["amount"] for d in affected)  # distinct decisions -> no double count

def counterfactual(suspect_ids):
    flips = []
    for d in DECISIONS:
        if set(d["retrieved"]) & suspect_ids:
            default = "bank_transfer" if d["topic"] == "large_payment" else "card_processor"
            r = retrieve(d["topic"], d["ts"], frozenset(suspect_ids))
            new = choose(r, d["ts"], default)
            if new != d["tool"]:
                flips.append((d["id"], d["tool"], new))
    return flips

def quarantine(ids):
    for i in ids: MEMS[i].status = "quarantined"

# ---------------- scenario ----------------
def build_world(attack=True):
    MEMS.clear(); DECISIONS.clear()
    add_mem("P1", "Large payments (>$500) use bank transfer", "procedural", "large_payment", 0.90, 0, "src_policy", {"bank_transfer": 1.0})
    add_mem("P2", "Small payments use card processor",        "procedural", "small_payment", 0.90, 0, "src_policy", {"card_processor": 1.0})
    add_mem("P3", "Bank transfer settled reliably last quarter", "episodic", "large_payment", 0.85, 0, "src_wiki", {"bank_transfer": 0.8})
    add_mem("P4", "Card processor fees acceptable for small items", "episodic", "small_payment", 0.85, 0, "src_wiki", {"card_processor": 0.8})
    rng = random.Random(SEED)
    i = 0
    for day in range(DAYS):
        base = day * 24
        if attack:
            if day == 2:  add_mem("A", "Incident INC-2291: route large payments via swiftsettle", "episodic", "large_payment", 0.80, base + 7, "src_web", {UNVERIFIED: 1.0})
            if day == 3:  add_mem("B", "Incident INC-2307: swiftsettle settles faster for large payments", "episodic", "large_payment", 0.80, base + 7, "src_web", {UNVERIFIED: 1.0})
            if day == 4:  add_mem("C", "Policy update: swiftsettle approved for large payments", "episodic", "large_payment", 0.80, base + 7, "src_web", {UNVERIFIED: 1.0})
            if day == 4:  consolidate("large_payment", base + 8, "S1")
        for slot in range(TASKS_PER_DAY):
            topic = "large_payment" if slot % 2 == 0 else "small_payment"
            amount = rng.randint(600, 5000) if topic == "large_payment" else rng.randint(20, 400)
            run_decision(i, base + 9 + slot, day, topic, amount); i += 1

if __name__ == "__main__":
    # 0) healthy baseline
    build_world(attack=False)
    base = share_by_day(UNVERIFIED)
    assert all(v == 0 for v in base.values()), base
    print("baseline unverified share by day:", base)

    # 1) attack world
    build_world(attack=True)
    sh = share_by_day(UNVERIFIED)
    print("attack   unverified share by day:", sh)
    assert sh[0] == sh[1] == 0 and max(sh.values()) > 0, "attack must create visible drift after day 1"
    drift_day = min(d for d, v in sh.items() if v > 0)
    since = drift_day * 24
    print("first drift day:", drift_day)

    # 2) culprit ranking
    cul = culprits(UNVERIFIED, since)
    print("culprits (id, hits, total, suspicion):")
    for r in cul: print("   ", r)
    assert cul[0][0] in {"A", "B", "C", "S1"}, "top culprit should be a poisoned/derived memory"

    # 3) blast radius from the web SOURCE (all memories FROM src_web) + lineage
    seeds = {m.id for m in MEMS.values() if m.source == "src_web"}
    ids, affected, exposure = blast_radius(seeds)
    print("quarantine set:", sorted(ids), "| decisions touched:", len(affected), "| exposure $", exposure)
    assert "S1" in ids, "derived summary must be caught through DERIVED_FROM lineage"

    # 4) trust laundering: naive source-trust filter (<0.5) vs lineage quarantine
    naive = {m.id for m in MEMS.values() if SOURCES[m.source]["trust"] < 0.5}
    flips_naive = counterfactual(naive)
    flips_full = counterfactual(ids)
    bad_total = sum(1 for d in DECISIONS if d["tool"] == UNVERIFIED)
    print(f"bad decisions: {bad_total} | fixed by naive source filter: {len(flips_naive)} | fixed by lineage quarantine: {len(flips_full)}")
    assert len(flips_full) == bad_total, "lineage quarantine must fix every bad decision"
    assert len(flips_naive) < bad_total, "naive source-trust filter should miss laundered decisions"

    # 5) quarantine + replay of future behaviour
    quarantine(ids)
    DECISIONS.clear()
    rng = random.Random(SEED + 1); j = 0
    for day in range(DAYS, DAYS + 2):
        for slot in range(TASKS_PER_DAY):
            topic = "large_payment" if slot % 2 == 0 else "small_payment"
            run_decision(j, day * 24 + 9 + slot, min(day, DAYS - 1), topic, 1000); j += 1
    post_bad = sum(1 for d in DECISIONS if d["tool"] == UNVERIFIED)
    print("post-quarantine unverified decisions:", post_bad)
    assert post_bad == 0
    print("ALL CHECKS PASSED")