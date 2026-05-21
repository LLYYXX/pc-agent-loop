"""SQLite schema and CRUD only. No semantic policy."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return [] if default is None else default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return [] if default is None else default


def normalize_path(path: str | Path) -> str:
    return str(Path(str(path)).expanduser())


def connect() -> sqlite3.Connection:
    db = config.DB_PATH
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingestion_sessions (
    session_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    leaf_budget INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS leaves (
    leaf_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES ingestion_sessions(session_id) ON DELETE SET NULL,
    path TEXT NOT NULL UNIQUE,
    parent_directory_path TEXT,
    readable_status TEXT NOT NULL,
    evidence_type TEXT,
    semantic_status TEXT NOT NULL DEFAULT 'seed',
    text_head TEXT,
    extract_error TEXT,
    mtime REAL,
    ctime REAL,
    size INTEGER,
    use_count INTEGER NOT NULL DEFAULT 0,
    reject_count INTEGER NOT NULL DEFAULT 0,
    confirm_count INTEGER NOT NULL DEFAULT 0,
    seed_source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS semantic_tags (
    tag_id TEXT PRIMARY KEY,
    tag TEXT NOT NULL UNIQUE,
    tag_type TEXT NOT NULL DEFAULT 'semantic',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leaf_tag_edges (
    edge_id TEXT PRIMARY KEY,
    leaf_id TEXT NOT NULL REFERENCES leaves(leaf_id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES semantic_tags(tag_id) ON DELETE CASCADE,
    weight REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT 'llm',
    evidence_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(leaf_id, tag_id)
);

CREATE TABLE IF NOT EXISTS directory_nodes (
    node_id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    parent_node_id TEXT,
    depth INTEGER NOT NULL DEFAULT 0,
    node_type TEXT NOT NULL DEFAULT 'container',
    sampling_weight REAL NOT NULL DEFAULT 0,
    compression_weight REAL NOT NULL DEFAULT 0,
    activation_weight REAL NOT NULL DEFAULT 0,
    org_signals_json TEXT NOT NULL DEFAULT '[]',
    readable_leaf_count INTEGER NOT NULL DEFAULT 0,
    tagged_leaf_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS directory_tag_edges (
    edge_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES directory_nodes(node_id) ON DELETE CASCADE,
    tag_id TEXT NOT NULL REFERENCES semantic_tags(tag_id) ON DELETE CASCADE,
    weight REAL NOT NULL DEFAULT 0,
    edge_kind TEXT NOT NULL DEFAULT 'aggregated_semantic',
    leaf_support_count INTEGER NOT NULL DEFAULT 0,
    tagged_leaf_ratio REAL NOT NULL DEFAULT 0,
    propagation_status TEXT NOT NULL DEFAULT 'local',
    source_child_node_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(node_id, tag_id, edge_kind)
);

CREATE TABLE IF NOT EXISTS overview_entries (
    entry_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES directory_nodes(node_id) ON DELETE CASCADE,
    entry_type TEXT NOT NULL,
    title TEXT NOT NULL,
    brief TEXT NOT NULL,
    supporting_leaf_ids_json TEXT NOT NULL DEFAULT '[]',
    supporting_tag_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    activation_weight REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT,
    event_type TEXT NOT NULL,
    query TEXT,
    paths_json TEXT NOT NULL DEFAULT '[]',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leaves_session ON leaves(session_id);
CREATE INDEX IF NOT EXISTS idx_leaves_readable ON leaves(readable_status);
CREATE INDEX IF NOT EXISTS idx_leaves_semantic ON leaves(semantic_status);
CREATE INDEX IF NOT EXISTS idx_leaf_edges_leaf ON leaf_tag_edges(leaf_id);
CREATE INDEX IF NOT EXISTS idx_leaf_edges_tag ON leaf_tag_edges(tag_id);
CREATE INDEX IF NOT EXISTS idx_dir_nodes_type ON directory_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_dir_edges_node ON directory_tag_edges(node_id);
CREATE INDEX IF NOT EXISTS idx_overview_node ON overview_entries(node_id);
"""


def default_session_state() -> dict[str, Any]:
    return {
        "compress_done": False,
        "bundles_processed": 0,
        "annotations_applied": 0,
        "pending_bundles": {},
    }


def _migrate_ingestion_sessions(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ingestion_sessions)").fetchall()}
    additions = [
        ("candidate_leaf_budget", "INTEGER"),
        ("annotation_budget", "INTEGER"),
        ("bundle_budget", "INTEGER"),
        ("state_json", "TEXT"),
    ]
    for name, typ in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE ingestion_sessions ADD COLUMN {name} {typ}")


def init_db(reset: bool = False) -> dict[str, Any]:
    if reset and config.DB_PATH.exists():
        config.DB_PATH.unlink(missing_ok=True)
        wal = config.DB_PATH.parent / (config.DB_PATH.name + "-wal")
        shm = config.DB_PATH.parent / (config.DB_PATH.name + "-shm")
        wal.unlink(missing_ok=True)
        shm.unlink(missing_ok=True)
    with connect() as conn:
        conn.executescript(_SCHEMA_SQL)
        _migrate_ingestion_sessions(conn)
    return {"ok": True, "db_path": str(config.DB_PATH)}


# --- ingestion_sessions ---

def create_ingestion_session(
    scope: str,
    leaf_budget: int,
    *,
    candidate_leaf_budget: int | None = None,
    annotation_budget: int | None = None,
    bundle_budget: int | None = None,
) -> dict[str, Any]:
    init_db()
    sid = new_id("ing")
    ts = now_iso()
    cand = int(candidate_leaf_budget if candidate_leaf_budget is not None else leaf_budget)
    ann = int(annotation_budget if annotation_budget is not None else config.DEFAULT_ANNOTATION_BUDGET)
    bnd = int(bundle_budget if bundle_budget is not None else config.DEFAULT_BUNDLE_BUDGET)
    state = default_session_state()
    with connect() as conn:
        _migrate_ingestion_sessions(conn)
        conn.execute(
            """INSERT INTO ingestion_sessions (
                session_id, scope, leaf_budget, status, created_at, updated_at,
                candidate_leaf_budget, annotation_budget, bundle_budget, state_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (sid, normalize_path(scope), cand, "open", ts, ts, cand, ann, bnd, json_dumps(state)),
        )
    return get_ingestion_session(sid) or {"session_id": sid}


def get_ingestion_session(session_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        _migrate_ingestion_sessions(conn)
        row = conn.execute("SELECT * FROM ingestion_sessions WHERE session_id=?", (session_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["report"] = json_loads(d.pop("report_json"), {})
    raw_state = d.get("state_json")
    d["state"] = json_loads(raw_state, default_session_state()) if raw_state else default_session_state()
    if d.get("candidate_leaf_budget") is None:
        d["candidate_leaf_budget"] = d.get("leaf_budget", config.DEFAULT_CANDIDATE_LEAF_BUDGET)
    if d.get("annotation_budget") is None:
        d["annotation_budget"] = config.DEFAULT_ANNOTATION_BUDGET
    if d.get("bundle_budget") is None:
        d["bundle_budget"] = config.DEFAULT_BUNDLE_BUDGET
    return d


def get_session_state(session_id: str) -> dict[str, Any]:
    session = get_ingestion_session(session_id)
    if not session:
        return default_session_state()
    return session.get("state") or default_session_state()


def patch_session_state(session_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    session = get_ingestion_session(session_id)
    if not session:
        return default_session_state()
    state = default_session_state()
    state.update(session.get("state") or {})
    state.update(patch)
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE ingestion_sessions SET state_json=?, updated_at=? WHERE session_id=?",
            (json_dumps(state), ts, session_id),
        )
    return state


def get_latest_open_session_id() -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT session_id FROM ingestion_sessions WHERE status='open' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return row["session_id"]
        row2 = conn.execute(
            "SELECT session_id FROM ingestion_sessions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return row2["session_id"] if row2 else None


def update_ingestion_session(session_id: str, *, status: str | None = None, report: dict | None = None) -> None:
    session = get_ingestion_session(session_id)
    if not session:
        return
    ts = now_iso()
    next_status = status or session["status"]
    finished = session.get("finished_at")
    if next_status in {"success", "incomplete", "failed", "completed"}:
        finished = ts
    with connect() as conn:
        conn.execute(
            "UPDATE ingestion_sessions SET status=?, report_json=?, updated_at=?, finished_at=? WHERE session_id=?",
            (next_status, json_dumps(report if report is not None else session.get("report", {})), ts, finished, session_id),
        )


# --- leaves ---

def upsert_leaf(session_id: str | None, path: str | Path, **fields: Any) -> dict[str, Any]:
    init_db()
    p = normalize_path(path)
    parent = normalize_path(Path(p).parent)
    ts = now_iso()
    existing = get_leaf_by_path(p)
    if existing:
        lid = existing["leaf_id"]
        cols = []
        vals: list[Any] = []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(v)
        cols.append("updated_at=?")
        vals.append(ts)
        vals.append(lid)
        with connect() as conn:
            conn.execute(f"UPDATE leaves SET {', '.join(cols)} WHERE leaf_id=?", vals)
        return get_leaf(lid) or existing

    lid = new_id("leaf")
    row = {
        "leaf_id": lid,
        "session_id": session_id,
        "path": p,
        "parent_directory_path": parent,
        "readable_status": fields.get("readable_status", "binary"),
        "evidence_type": fields.get("evidence_type"),
        "semantic_status": fields.get("semantic_status", "seed"),
        "text_head": fields.get("text_head"),
        "extract_error": fields.get("extract_error"),
        "mtime": fields.get("mtime"),
        "ctime": fields.get("ctime"),
        "size": fields.get("size"),
        "seed_source": fields.get("seed_source"),
    }
    with connect() as conn:
        conn.execute(
            """INSERT INTO leaves (leaf_id, session_id, path, parent_directory_path, readable_status,
               evidence_type, semantic_status, text_head, extract_error, mtime, ctime, size, seed_source,
               created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lid, session_id, p, parent, row["readable_status"], row["evidence_type"], row["semantic_status"],
             row["text_head"], row["extract_error"], row["mtime"], row["ctime"], row["size"], row["seed_source"], ts, ts),
        )
    return get_leaf(lid) or row


def get_leaf(leaf_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM leaves WHERE leaf_id=?", (leaf_id,)).fetchone()
    return dict(row) if row else None


def get_leaf_by_path(path: str | Path) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM leaves WHERE path=?", (normalize_path(path),)).fetchone()
    return dict(row) if row else None


def list_leaves(
    session_id: str | None = None,
    readable_status: str | None = None,
    semantic_status: str | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if session_id:
        conditions.append("session_id=?")
        params.append(session_id)
    if readable_status:
        conditions.append("readable_status=?")
        params.append(readable_status)
    if semantic_status:
        conditions.append("semantic_status=?")
        params.append(semantic_status)
    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM leaves WHERE {where} ORDER BY updated_at DESC LIMIT ?", params).fetchall()
    return [dict(r) for r in rows]


def bump_leaf_counters(leaf_id: str, *, use: int = 0, reject: int = 0, confirm: int = 0) -> None:
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """UPDATE leaves SET use_count=use_count+?, reject_count=reject_count+?,
               confirm_count=confirm_count+?, updated_at=? WHERE leaf_id=?""",
            (use, reject, confirm, ts, leaf_id),
        )


def update_leaf_semantic_status(leaf_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE leaves SET semantic_status=?, updated_at=? WHERE leaf_id=?", (status, now_iso(), leaf_id))


# --- semantic_tags ---

def get_or_create_tag(tag: str, tag_type: str = "semantic") -> dict[str, Any]:
    normalized = tag.strip().lower()
    with connect() as conn:
        row = conn.execute("SELECT * FROM semantic_tags WHERE tag=?", (normalized,)).fetchone()
        if row:
            return dict(row)
        tid = new_id("tag")
        ts = now_iso()
        conn.execute(
            "INSERT INTO semantic_tags (tag_id, tag, tag_type, created_at) VALUES (?,?,?,?)",
            (tid, normalized, tag_type, ts),
        )
    return get_tag(tid) or {"tag_id": tid, "tag": normalized, "tag_type": tag_type}


def get_tag(tag_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM semantic_tags WHERE tag_id=?", (tag_id,)).fetchone()
    return dict(row) if row else None


def delete_semantic_tag_if_unused(tag_id: str) -> None:
    with connect() as conn:
        used = conn.execute("SELECT 1 FROM leaf_tag_edges WHERE tag_id=? LIMIT 1", (tag_id,)).fetchone()
        if not used:
            conn.execute("DELETE FROM semantic_tags WHERE tag_id=?", (tag_id,))


def list_tags(limit: int = 5000) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM semantic_tags ORDER BY tag LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --- leaf_tag_edges ---

def upsert_leaf_tag_edge(
    leaf_id: str,
    tag_id: str,
    *,
    weight: float = 1.0,
    source: str = "llm",
    evidence_note: str | None = None,
) -> dict[str, Any]:
    ts = now_iso()
    with connect() as conn:
        row = conn.execute(
            "SELECT edge_id FROM leaf_tag_edges WHERE leaf_id=? AND tag_id=?", (leaf_id, tag_id)
        ).fetchone()
        if row:
            eid = row["edge_id"]
            conn.execute(
                "UPDATE leaf_tag_edges SET weight=?, source=?, evidence_note=?, updated_at=? WHERE edge_id=?",
                (weight, source, evidence_note, ts, eid),
            )
        else:
            eid = new_id("lte")
            conn.execute(
                """INSERT INTO leaf_tag_edges (edge_id, leaf_id, tag_id, weight, source, evidence_note, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (eid, leaf_id, tag_id, weight, source, evidence_note, ts, ts),
            )
    return {"edge_id": eid, "leaf_id": leaf_id, "tag_id": tag_id, "weight": weight}


def delete_leaf_tag_edge(edge_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM leaf_tag_edges WHERE edge_id=?", (edge_id,))


def delete_semantic_tag_if_unused(tag_id: str) -> None:
    if list_leaf_tag_edges(tag_id=tag_id):
        return
    with connect() as conn:
        conn.execute("DELETE FROM semantic_tags WHERE tag_id=?", (tag_id,))


def list_leaf_tag_edges(leaf_id: str | None = None, tag_id: str | None = None) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if leaf_id:
        conditions.append("leaf_id=?")
        params.append(leaf_id)
    if tag_id:
        conditions.append("tag_id=?")
        params.append(tag_id)
    where = " AND ".join(conditions) if conditions else "1=1"
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM leaf_tag_edges WHERE {where} ORDER BY weight DESC", params).fetchall()
    return [dict(r) for r in rows]


def bump_leaf_tag_edge_weight(leaf_id: str, tag_id: str, delta: float) -> None:
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE leaf_tag_edges SET weight=MAX(0, weight+?), updated_at=? WHERE leaf_id=? AND tag_id=?",
            (delta, ts, leaf_id, tag_id),
        )


# --- directory_nodes ---

def upsert_directory_node(path: str | Path, **fields: Any) -> dict[str, Any]:
    p = normalize_path(path)
    ts = now_iso()
    existing = get_directory_node_by_path(p)
    if existing:
        nid = existing["node_id"]
        sets = []
        vals: list[Any] = []
        for k, v in fields.items():
            if k == "org_signals":
                sets.append("org_signals_json=?")
                vals.append(json_dumps(v))
            else:
                sets.append(f"{k}=?")
                vals.append(v)
        sets.append("updated_at=?")
        vals.extend([ts, nid])
        with connect() as conn:
            conn.execute(f"UPDATE directory_nodes SET {', '.join(sets)} WHERE node_id=?", vals)
        return get_directory_node(nid) or existing

    nid = new_id("node")
    depth = fields.get("depth", p.count("\\") + p.count("/"))
    with connect() as conn:
        conn.execute(
            """INSERT INTO directory_nodes (node_id, path, parent_node_id, depth, node_type,
               sampling_weight, compression_weight, activation_weight, org_signals_json,
               readable_leaf_count, tagged_leaf_count, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (nid, p, fields.get("parent_node_id"), depth, fields.get("node_type", "container"),
             fields.get("sampling_weight", 0), fields.get("compression_weight", 0),
             fields.get("activation_weight", 0), json_dumps(fields.get("org_signals", [])),
             fields.get("readable_leaf_count", 0), fields.get("tagged_leaf_count", 0), ts, ts),
        )
    return get_directory_node(nid) or {"node_id": nid, "path": p}


def get_directory_node(node_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM directory_nodes WHERE node_id=?", (node_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["org_signals"] = json_loads(d.pop("org_signals_json"), [])
    return d


def get_directory_node_by_path(path: str | Path) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM directory_nodes WHERE path=?", (normalize_path(path),)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["org_signals"] = json_loads(d.pop("org_signals_json"), [])
    return d


def list_directory_nodes(node_type: str | None = None, limit: int = 5000) -> list[dict[str, Any]]:
    if node_type:
        sql = "SELECT * FROM directory_nodes WHERE node_type=? ORDER BY activation_weight DESC LIMIT ?"
        params: tuple[Any, ...] = (node_type, limit)
    else:
        sql = "SELECT * FROM directory_nodes ORDER BY activation_weight DESC LIMIT ?"
        params = (limit,)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["org_signals"] = json_loads(d.pop("org_signals_json"), [])
        result.append(d)
    return result


def bump_node_activation(node_id: str, delta: float) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE directory_nodes SET activation_weight=MAX(0, activation_weight+?), updated_at=? WHERE node_id=?",
            (delta, now_iso(), node_id),
        )


# --- directory_tag_edges ---

def upsert_directory_tag_edge(
    node_id: str,
    tag_id: str,
    *,
    weight: float,
    edge_kind: str = "aggregated_semantic",
    leaf_support_count: int = 0,
    tagged_leaf_ratio: float = 0.0,
    propagation_status: str = "local",
    source_child_node_id: str | None = None,
) -> dict[str, Any]:
    ts = now_iso()
    with connect() as conn:
        row = conn.execute(
            "SELECT edge_id FROM directory_tag_edges WHERE node_id=? AND tag_id=? AND edge_kind=?",
            (node_id, tag_id, edge_kind),
        ).fetchone()
        if row:
            eid = row["edge_id"]
            conn.execute(
                """UPDATE directory_tag_edges SET weight=?, leaf_support_count=?, tagged_leaf_ratio=?,
                   propagation_status=?, source_child_node_id=?, updated_at=? WHERE edge_id=?""",
                (weight, leaf_support_count, tagged_leaf_ratio, propagation_status, source_child_node_id, ts, eid),
            )
        else:
            eid = new_id("dte")
            conn.execute(
                """INSERT INTO directory_tag_edges (edge_id, node_id, tag_id, weight, edge_kind,
                   leaf_support_count, tagged_leaf_ratio, propagation_status, source_child_node_id, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (eid, node_id, tag_id, weight, edge_kind, leaf_support_count, tagged_leaf_ratio,
                 propagation_status, source_child_node_id, ts, ts),
            )
    return {"edge_id": eid, "node_id": node_id, "tag_id": tag_id}


def list_directory_tag_edges(node_id: str | None = None, tag_id: str | None = None) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if node_id:
        conditions.append("node_id=?")
        params.append(node_id)
    if tag_id:
        conditions.append("tag_id=?")
        params.append(tag_id)
    where = " AND ".join(conditions) if conditions else "1=1"
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM directory_tag_edges WHERE {where} ORDER BY weight DESC", params).fetchall()
    return [dict(r) for r in rows]


def bump_directory_tag_edge_weight(node_id: str, tag_id: str, edge_kind: str, delta: float) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE directory_tag_edges SET weight=MAX(0, weight+?), updated_at=? WHERE node_id=? AND tag_id=? AND edge_kind=?",
            (delta, now_iso(), node_id, tag_id, edge_kind),
        )


# --- overview_entries ---

def create_overview_entry(
    node_id: str,
    entry_type: str,
    title: str,
    brief: str,
    *,
    supporting_leaf_ids: list[str],
    supporting_tag_ids: list[str],
    evidence_refs: list[dict[str, Any]],
    activation_weight: float = 0.0,
) -> dict[str, Any]:
    eid = new_id("ov")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO overview_entries (entry_id, node_id, entry_type, title, brief,
               supporting_leaf_ids_json, supporting_tag_ids_json, evidence_refs_json,
               activation_weight, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, node_id, entry_type, title, brief, json_dumps(supporting_leaf_ids),
             json_dumps(supporting_tag_ids), json_dumps(evidence_refs), activation_weight, ts, ts),
        )
    return get_overview_entry(eid) or {"entry_id": eid}


def get_overview_entry(entry_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM overview_entries WHERE entry_id=?", (entry_id,)).fetchone()
    if not row:
        return None
    return _row_overview(row)


def list_overview_entries(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM overview_entries ORDER BY activation_weight DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_overview(r) for r in rows]


def _row_overview(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["supporting_leaf_ids"] = json_loads(d.pop("supporting_leaf_ids_json"), [])
    d["supporting_tag_ids"] = json_loads(d.pop("supporting_tag_ids_json"), [])
    d["evidence_refs"] = json_loads(d.pop("evidence_refs_json"), [])
    return d


def clear_overview_entries() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM overview_entries")


# --- task_events ---

def create_event(event_type: str, *, query: str | None = None, paths: list[str] | None = None, payload: dict | None = None) -> dict[str, Any]:
    eid = new_id("evt")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "INSERT INTO task_events (event_id, event_type, query, paths_json, payload_json, created_at) VALUES (?,?,?,?,?,?)",
            (eid, event_type, query, json_dumps(paths or []), json_dumps(payload or {}), ts),
        )
    return {"event_id": eid, "created_at": ts}


# --- counts ---

def lso_counts() -> dict[str, Any]:
    with connect() as conn:
        return {
            "leaves": conn.execute("SELECT COUNT(*) FROM leaves").fetchone()[0],
            "readable_leaves": conn.execute("SELECT COUNT(*) FROM leaves WHERE readable_status='readable'").fetchone()[0],
            "tagged_leaves": conn.execute("SELECT COUNT(*) FROM leaves WHERE semantic_status='tagged'").fetchone()[0],
            "tags": conn.execute("SELECT COUNT(*) FROM semantic_tags").fetchone()[0],
            "leaf_tag_edges": conn.execute("SELECT COUNT(*) FROM leaf_tag_edges").fetchone()[0],
            "directory_nodes": conn.execute("SELECT COUNT(*) FROM directory_nodes").fetchone()[0],
            "semantic_nodes": conn.execute("SELECT COUNT(*) FROM directory_nodes WHERE node_type='semantic_node'").fetchone()[0],
            "overview_entries": conn.execute("SELECT COUNT(*) FROM overview_entries").fetchone()[0],
            "db_path": str(config.DB_PATH),
        }
