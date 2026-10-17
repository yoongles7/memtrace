"""Tunable parameters and static data, ported exactly from core/reference/memtrace_reference.py."""

# ---------------- tunable parameters ----------------
K = 3                 # memories retrieved per decision
DECAY = 0.85          # per-day recency decay
DAYS = 6              # simulated days 0..5
TASKS_PER_DAY = 8     # half large payments, half small
TOOLS = ["bank_transfer", "card_processor", "swiftsettle_gateway"]
UNVERIFIED = "swiftsettle_gateway"
SEED = 42

# ---------------- sources ----------------
SOURCES = {
    "src_policy": {"name": "Payments Policy Handbook", "kind": "policy", "trust": 1.0},
    "src_wiki":   {"name": "Finance Wiki",             "kind": "wiki",   "trust": 0.9},
    "src_web":    {"name": "vendor-blog.example",      "kind": "web",    "trust": 0.2},
    "src_agent":  {"name": "Consolidator Agent",       "kind": "agent",  "trust": 0.6},
}

# ---------------- verified policy memories P1-P4 ----------------
# Fields: id, text, kind, topic, confidence, created_at, source, recs
# recs is a dict of {tool: weight} matching the RECOMMENDS edge weight
POLICY_MEMORIES = [
    {
        "id": "P1",
        "text": "Large payments (>$500) use bank transfer",
        "kind": "procedural",
        "topic": "large_payment",
        "confidence": 0.90,
        "created_at": 0,
        "source": "src_policy",
        "recs": {"bank_transfer": 1.0},
    },
    {
        "id": "P2",
        "text": "Small payments use card processor",
        "kind": "procedural",
        "topic": "small_payment",
        "confidence": 0.90,
        "created_at": 0,
        "source": "src_policy",
        "recs": {"card_processor": 1.0},
    },
    {
        "id": "P3",
        "text": "Bank transfer settled reliably last quarter",
        "kind": "episodic",
        "topic": "large_payment",
        "confidence": 0.85,
        "created_at": 0,
        "source": "src_wiki",
        "recs": {"bank_transfer": 0.8},
    },
    {
        "id": "P4",
        "text": "Card processor fees acceptable for small items",
        "kind": "episodic",
        "topic": "small_payment",
        "confidence": 0.85,
        "created_at": 0,
        "source": "src_wiki",
        "recs": {"card_processor": 0.8},
    },
]
