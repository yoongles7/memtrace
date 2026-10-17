"""FalkorDB connection helpers and schema initialisation."""
import os
from falkordb import FalkorDB

# Cypher index creation statements
_IDX_MEMORY_ID   = "CREATE INDEX FOR (m:Memory)   ON (m.id)"
_IDX_DECISION_ID = "CREATE INDEX FOR (d:Decision) ON (d.id)"
_IDX_SOURCE_ID   = "CREATE INDEX FOR (s:Source)   ON (s.id)"
_IDX_TASK_ID     = "CREATE INDEX FOR (t:Task)     ON (t.id)"
_IDX_TOOL_NAME   = "CREATE INDEX FOR (t:Tool)     ON (t.name)"

_db = None  # module-level singleton


def get_db():
    """Return a single lazy FalkorDB connection (reused across calls)."""
    global _db
    if _db is None:
        host = os.environ.get("FALKOR_HOST", "localhost")
        port = int(os.environ.get("FALKOR_PORT", 6379))
        username = os.environ.get("FALKOR_USERNAME")
        password = os.environ.get("FALKOR_PASSWORD")
        kwargs = {}
        if username:
            kwargs["username"] = username
        if password:
            kwargs["password"] = password
        _db = FalkorDB(host=host, port=port, **kwargs)
    return _db


def tg(tenant):
    """Return the FalkorDB graph for *tenant* (graph name: mt_<tenant>)."""
    return get_db().select_graph(f"mt_{tenant}")


def reset(tenant):
    """Delete every node and edge in the tenant graph."""
    g = tg(tenant)
    g.query("MATCH (n) DETACH DELETE n")


def init_schema(g):
    """Create indexes on key properties; silently ignores duplicate-index errors."""
    for stmt in (
        _IDX_MEMORY_ID,
        _IDX_DECISION_ID,
        _IDX_SOURCE_ID,
        _IDX_TASK_ID,
        _IDX_TOOL_NAME,
    ):
        try:
            g.query(stmt)
        except Exception:
            pass  # index already exists – safe to ignore on re-runs
