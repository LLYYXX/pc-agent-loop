"""SQLite persistence for Local Semantic Overlay v2 (Area-Aware Annotation-First)."""

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


def query_key(query: str) -> str:
    return " ".join(str(query).lower().split())


def connect() -> sqlite3.Connection:
    db = config.DB_PATH
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS seed_map_sessions (
    session_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    route_budget INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS areas (
    area_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES seed_map_sessions(session_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    parent_area_id TEXT,
    depth INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unseen',
    file_count INTEGER NOT NULL DEFAULT 0,
    dir_count INTEGER NOT NULL DEFAULT 0,
    signals_json TEXT NOT NULL DEFAULT '[]',
    profile_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, path)
);

CREATE TABLE IF NOT EXISTS evidence_items (
    evidence_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES seed_map_sessions(session_id) ON DELETE CASCADE,
    area_id TEXT NOT NULL REFERENCES areas(area_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    bucket TEXT NOT NULL,
    text_head TEXT,
    extract_error TEXT,
    weight REAL NOT NULL DEFAULT 1.0,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_annotations (
    annotation_id TEXT PRIMARY KEY,
    session_id TEXT,
    evidence_id TEXT,
    area_id TEXT,
    path TEXT NOT NULL,
    decision TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    value_reason TEXT,
    evidence_summary TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    use_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_proposals (
    proposal_id TEXT PRIMARY KEY,
    session_id TEXT,
    status TEXT NOT NULL DEFAULT 'proposed',
    title TEXT,
    brief TEXT,
    supporting_annotation_ids_json TEXT NOT NULL DEFAULT '[]',
    anchor_path TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routes (
    route_id TEXT PRIMARY KEY,
    proposal_id TEXT,
    title TEXT NOT NULL,
    brief TEXT NOT NULL,
    use_when TEXT,
    anchor_path TEXT,
    entrypoints_json TEXT NOT NULL DEFAULT '[]',
    supporting_annotation_ids_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    route_terms_json TEXT NOT NULL DEFAULT '[]',
    route_meta_json TEXT NOT NULL DEFAULT '{}',
    tier TEXT NOT NULL DEFAULT 'warm',
    status TEXT NOT NULL DEFAULT 'active',
    usage_verification TEXT NOT NULL DEFAULT 'seeded',
    confidence REAL NOT NULL DEFAULT 0.5,
    usage_score REAL NOT NULL DEFAULT 0,
    quality_score REAL NOT NULL DEFAULT 0,
    risk_score REAL NOT NULL DEFAULT 0,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used TEXT
);

CREATE TABLE IF NOT EXISTS deferred_items (
    deferred_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    area_id TEXT,
    evidence_id TEXT,
    annotation_id TEXT,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT,
    event_type TEXT NOT NULL,
    query TEXT,
    route_ids_json TEXT NOT NULL DEFAULT '[]',
    annotation_ids_json TEXT NOT NULL DEFAULT '[]',
    paths_json TEXT NOT NULL DEFAULT '[]',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_query_stats (
    route_id TEXT NOT NULL,
    query_key TEXT NOT NULL,
    query TEXT NOT NULL,
    positive_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    last_positive TEXT,
    last_negative TEXT,
    notes_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(route_id, query_key)
);

CREATE TABLE IF NOT EXISTS update_plans (
    plan_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    query TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS corrections (
    correction_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    wrong_paths_json TEXT NOT NULL DEFAULT '[]',
    missed_paths_json TEXT NOT NULL DEFAULT '[]',
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_areas_session ON areas(session_id);
CREATE INDEX IF NOT EXISTS idx_areas_status ON areas(status);
CREATE INDEX IF NOT EXISTS idx_areas_path ON areas(path);
CREATE INDEX IF NOT EXISTS idx_evidence_session ON evidence_items(session_id);
CREATE INDEX IF NOT EXISTS idx_evidence_area ON evidence_items(area_id);
CREATE INDEX IF NOT EXISTS idx_evidence_path ON evidence_items(path);
CREATE INDEX IF NOT EXISTS idx_annotations_session ON file_annotations(session_id);
CREATE INDEX IF NOT EXISTS idx_annotations_area ON file_annotations(area_id);
CREATE INDEX IF NOT EXISTS idx_annotations_decision ON file_annotations(decision);
CREATE INDEX IF NOT EXISTS idx_annotations_path ON file_annotations(path);
CREATE INDEX IF NOT EXISTS idx_proposals_session ON route_proposals(session_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON route_proposals(status);
CREATE INDEX IF NOT EXISTS idx_routes_tier ON routes(tier);
CREATE INDEX IF NOT EXISTS idx_routes_status ON routes(status);
CREATE INDEX IF NOT EXISTS idx_routes_anchor ON routes(anchor_path);
CREATE INDEX IF NOT EXISTS idx_deferred_status ON deferred_items(status);
CREATE INDEX IF NOT EXISTS idx_events_type ON task_events(event_type);
CREATE INDEX IF NOT EXISTS idx_plans_status ON update_plans(status);
"""


def init_db() -> dict[str, Any]:
    with connect() as conn:
        conn.executescript(_SCHEMA_SQL)
    return {"ok": True, "db_path": str(config.DB_PATH)}


# ---------------------------------------------------------------------------
# Seed map sessions
# ---------------------------------------------------------------------------

def create_seed_session(scope: str, route_budget: int) -> dict[str, Any]:
    init_db()
    session_id = new_id("seed")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "INSERT INTO seed_map_sessions (session_id, scope, route_budget, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (session_id, normalize_path(scope), int(route_budget), "open", ts, ts),
        )
    return get_seed_session(session_id) or {"session_id": session_id}


def get_seed_session(session_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM seed_map_sessions WHERE session_id=?", (session_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["report"] = json_loads(d.pop("report_json"), {})
    return d


def update_seed_session(session_id: str, *, status: str | None = None, report: dict[str, Any] | None = None) -> None:
    session = get_seed_session(session_id)
    if not session:
        return
    ts = now_iso()
    next_status = status or session["status"]
    finished_at = ts if next_status in {"complete", "success", "incomplete", "failed"} else session.get("finished_at")
    with connect() as conn:
        conn.execute(
            "UPDATE seed_map_sessions SET status=?, report_json=?, updated_at=?, finished_at=? WHERE session_id=?",
            (next_status, json_dumps(report if report is not None else session.get("report", {})), ts, finished_at, session_id),
        )


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------

def upsert_area(
    session_id: str,
    path: str | Path,
    *,
    parent_area_id: str | None = None,
    depth: int = 0,
    status: str = "unseen",
    file_count: int = 0,
    dir_count: int = 0,
    signals: list[str] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_db()
    area_id = new_id("area")
    ts = now_iso()
    p = normalize_path(path)
    with connect() as conn:
        conn.execute(
            """INSERT INTO areas (area_id, session_id, path, parent_area_id, depth, status,
               file_count, dir_count, signals_json, profile_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id, path) DO UPDATE SET
               parent_area_id=COALESCE(excluded.parent_area_id, parent_area_id),
               depth=excluded.depth, status=excluded.status,
               file_count=excluded.file_count, dir_count=excluded.dir_count,
               signals_json=excluded.signals_json, profile_json=excluded.profile_json,
               updated_at=excluded.updated_at""",
            (area_id, session_id, p, parent_area_id, depth, status,
             file_count, dir_count, json_dumps(signals or []), json_dumps(profile or {}), ts, ts),
        )
        row = conn.execute("SELECT * FROM areas WHERE session_id=? AND path=?", (session_id, p)).fetchone()
    return _row_to_area(row)


def get_area(area_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM areas WHERE area_id=?", (area_id,)).fetchone()
    return _row_to_area(row) if row else None


def get_area_by_path(session_id: str, path: str | Path) -> dict[str, Any] | None:
    init_db()
    p = normalize_path(path)
    with connect() as conn:
        row = conn.execute("SELECT * FROM areas WHERE session_id=? AND path=?", (session_id, p)).fetchone()
    return _row_to_area(row) if row else None


def list_areas(session_id: str, status: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM areas WHERE session_id=?"
    params: list[Any] = [session_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY depth, path"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_area(r) for r in rows]


def update_area_status(area_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE areas SET status=?, updated_at=? WHERE area_id=?", (status, now_iso(), area_id))


def _row_to_area(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["signals"] = json_loads(d.pop("signals_json"), [])
    d["profile"] = json_loads(d.pop("profile_json"), {})
    return d


# ---------------------------------------------------------------------------
# Evidence items
# ---------------------------------------------------------------------------

def add_evidence_item(
    session_id: str,
    area_id: str,
    path: str | Path,
    bucket: str,
    *,
    text_head: str | None = None,
    extract_error: str | None = None,
    weight: float = 1.0,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_db()
    eid = new_id("ev")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO evidence_items (evidence_id, session_id, area_id, path, bucket,
               text_head, extract_error, weight, meta_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (eid, session_id, area_id, normalize_path(path), bucket,
             text_head, extract_error, float(weight), json_dumps(meta or {}), ts),
        )
    return {"evidence_id": eid, "session_id": session_id, "area_id": area_id,
            "path": normalize_path(path), "bucket": bucket, "text_head": text_head,
            "extract_error": extract_error, "weight": weight, "created_at": ts}


def list_evidence_items(session_id: str | None = None, area_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    conditions = []
    params: list[Any] = []
    if session_id:
        conditions.append("session_id=?")
        params.append(session_id)
    if area_id:
        conditions.append("area_id=?")
        params.append(area_id)
    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM evidence_items WHERE {where} ORDER BY weight DESC, created_at LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["meta"] = json_loads(d.pop("meta_json"), {})
        result.append(d)
    return result


def get_evidence_item(evidence_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM evidence_items WHERE evidence_id=?", (evidence_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["meta"] = json_loads(d.pop("meta_json"), {})
    return d


# ---------------------------------------------------------------------------
# File annotations
# ---------------------------------------------------------------------------

def create_annotation(
    *,
    session_id: str | None = None,
    evidence_id: str | None = None,
    area_id: str | None = None,
    path: str,
    decision: str,
    tags: list[str] | None = None,
    value_reason: str | None = None,
    evidence_summary: str | None = None,
    confidence: float = 0.5,
) -> dict[str, Any]:
    init_db()
    aid = new_id("ann")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO file_annotations (annotation_id, session_id, evidence_id, area_id,
               path, decision, tags_json, value_reason, evidence_summary, confidence,
               use_count, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?)""",
            (aid, session_id, evidence_id, area_id, normalize_path(path), decision,
             json_dumps(tags or []), value_reason, evidence_summary, float(confidence), ts, ts),
        )
    return get_annotation(aid) or {"annotation_id": aid}


def get_annotation(annotation_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM file_annotations WHERE annotation_id=?", (annotation_id,)).fetchone()
    return _row_to_annotation(row) if row else None


def list_annotations(
    session_id: str | None = None,
    area_id: str | None = None,
    decision: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    init_db()
    conditions = []
    params: list[Any] = []
    if session_id:
        conditions.append("session_id=?")
        params.append(session_id)
    if area_id:
        conditions.append("area_id=?")
        params.append(area_id)
    if decision:
        conditions.append("decision=?")
        params.append(decision)
    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM file_annotations WHERE {where} ORDER BY confidence DESC, updated_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_annotation(r) for r in rows]


def bump_annotation_use(annotation_id: str, delta: int = 1) -> None:
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE file_annotations SET use_count=use_count+?, updated_at=? WHERE annotation_id=?",
            (delta, ts, annotation_id),
        )


def _row_to_annotation(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = json_loads(d.pop("tags_json"), [])
    return d


# ---------------------------------------------------------------------------
# Route proposals
# ---------------------------------------------------------------------------

def create_proposal(
    *,
    session_id: str | None = None,
    title: str,
    brief: str,
    supporting_annotation_ids: list[str],
    anchor_path: str | None = None,
    tags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_db()
    pid = new_id("prop")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO route_proposals (proposal_id, session_id, status, title, brief,
               supporting_annotation_ids_json, anchor_path, tags_json, meta_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, session_id, "proposed", title, brief,
             json_dumps(supporting_annotation_ids), normalize_path(anchor_path) if anchor_path else None,
             json_dumps(tags or []), json_dumps(meta or {}), ts, ts),
        )
    return get_proposal(pid) or {"proposal_id": pid}


def get_proposal(proposal_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM route_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["supporting_annotation_ids"] = json_loads(d.pop("supporting_annotation_ids_json"), [])
    d["tags"] = json_loads(d.pop("tags_json"), [])
    d["meta"] = json_loads(d.pop("meta_json"), {})
    return d


def list_proposals(session_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    init_db()
    conditions = []
    params: list[Any] = []
    if session_id:
        conditions.append("session_id=?")
        params.append(session_id)
    if status:
        conditions.append("status=?")
        params.append(status)
    where = " AND ".join(conditions) if conditions else "1=1"
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM route_proposals WHERE {where} ORDER BY created_at", params).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["supporting_annotation_ids"] = json_loads(d.pop("supporting_annotation_ids_json"), [])
        d["tags"] = json_loads(d.pop("tags_json"), [])
        d["meta"] = json_loads(d.pop("meta_json"), {})
        results.append(d)
    return results


def update_proposal_status(proposal_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE route_proposals SET status=?, updated_at=? WHERE proposal_id=?", (status, now_iso(), proposal_id))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def create_route(
    *,
    title: str,
    brief: str,
    use_when: str | None = None,
    anchor_path: str | None = None,
    entrypoints: list[str] | None = None,
    supporting_annotation_ids: list[str] | None = None,
    tags: list[str] | None = None,
    route_terms: list[str] | None = None,
    route_meta: dict[str, Any] | None = None,
    tier: str = "warm",
    status: str = "active",
    usage_verification: str = "seeded",
    confidence: float = 0.5,
    proposal_id: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    init_db()
    rid = new_id("route")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO routes (route_id, proposal_id, title, brief, use_when, anchor_path,
               entrypoints_json, supporting_annotation_ids_json, tags_json, route_terms_json,
               route_meta_json, tier, status, usage_verification, confidence,
               quality_score, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, proposal_id, title.strip(), brief.strip(), use_when,
             normalize_path(anchor_path) if anchor_path else None,
             json_dumps(entrypoints or []), json_dumps(supporting_annotation_ids or []),
             json_dumps(tags or []), json_dumps(route_terms or []),
             json_dumps(route_meta or {}), tier, status, usage_verification,
             float(confidence), float(confidence), source, ts, ts),
        )
    return get_route(rid) or {"route_id": rid}


def get_route(route_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM routes WHERE route_id=?", (route_id,)).fetchone()
    return _row_to_route(row) if row else None


def list_routes(status: str = "active") -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM routes WHERE status=? ORDER BY tier, usage_score DESC, confidence DESC, updated_at DESC",
            (status,),
        ).fetchall()
    return [_row_to_route(r) for r in rows]


def bump_route(
    route_id: str,
    *,
    usage_delta: float = 0,
    risk_delta: float = 0,
    confidence_delta: float = 0,
    tier: str | None = None,
    used: bool = False,
) -> None:
    route = get_route(route_id)
    if not route:
        return
    ts = now_iso()
    next_confidence = max(0.0, min(1.0, float(route["confidence"]) + confidence_delta))
    next_tier = tier or route["tier"]
    with connect() as conn:
        conn.execute(
            """UPDATE routes SET usage_score=usage_score+?, risk_score=MAX(0,risk_score+?),
               confidence=?, tier=?, updated_at=?,
               last_used=CASE WHEN ? THEN ? ELSE last_used END
               WHERE route_id=?""",
            (usage_delta, risk_delta, next_confidence, next_tier, ts, 1 if used else 0, ts, route_id),
        )


def set_route_tier(route_id: str, tier: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE routes SET tier=?, updated_at=? WHERE route_id=?", (tier, now_iso(), route_id))


def _row_to_route(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["entrypoints"] = json_loads(d.pop("entrypoints_json"), [])
    d["supporting_annotation_ids"] = json_loads(d.pop("supporting_annotation_ids_json"), [])
    d["tags"] = json_loads(d.pop("tags_json"), [])
    d["route_terms"] = json_loads(d.pop("route_terms_json"), [])
    d["route_meta"] = json_loads(d.pop("route_meta_json"), {})
    return d


# ---------------------------------------------------------------------------
# Deferred items
# ---------------------------------------------------------------------------

def create_deferred(
    kind: str,
    reason: str,
    *,
    area_id: str | None = None,
    evidence_id: str | None = None,
    annotation_id: str | None = None,
) -> dict[str, Any]:
    init_db()
    did = new_id("defer")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO deferred_items (deferred_id, kind, area_id, evidence_id, annotation_id,
               reason, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (did, kind, area_id, evidence_id, annotation_id, reason, "pending", ts, ts),
        )
    return {"deferred_id": did, "kind": kind, "reason": reason, "status": "pending"}


def list_deferred(status: str | None = "pending", limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    if status:
        sql = "SELECT * FROM deferred_items WHERE status=? ORDER BY created_at DESC LIMIT ?"
        params: tuple[Any, ...] = (status, limit)
    else:
        sql = "SELECT * FROM deferred_items ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Task events
# ---------------------------------------------------------------------------

def create_event(
    event_type: str,
    *,
    query: str | None = None,
    task_id: str | None = None,
    route_ids: list[str] | None = None,
    annotation_ids: list[str] | None = None,
    paths: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_db()
    eid = new_id("event")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO task_events (event_id, task_id, event_type, query, route_ids_json,
               annotation_ids_json, paths_json, payload_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (eid, task_id, event_type, query,
             json_dumps(route_ids or []), json_dumps(annotation_ids or []),
             json_dumps(paths or []), json_dumps(payload or {}), ts),
        )
    return {"event_id": eid, "created_at": ts}


def list_events(event_type: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    if event_type:
        sql = "SELECT * FROM task_events WHERE event_type=? ORDER BY created_at DESC LIMIT ?"
        params: tuple[Any, ...] = (event_type, limit)
    else:
        sql = "SELECT * FROM task_events ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["route_ids"] = json_loads(d.pop("route_ids_json"), [])
        d["annotation_ids"] = json_loads(d.pop("annotation_ids_json"), [])
        d["paths"] = json_loads(d.pop("paths_json"), [])
        d["payload"] = json_loads(d.pop("payload_json"), {})
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Route query stats
# ---------------------------------------------------------------------------

def upsert_query_stats(route_id: str, query: str, *, positive: int = 0, negative: int = 0, note: str | None = None) -> None:
    key = query_key(query)
    ts = now_iso()
    with connect() as conn:
        row = conn.execute(
            "SELECT notes_json FROM route_query_stats WHERE route_id=? AND query_key=?",
            (route_id, key),
        ).fetchone()
        notes = json_loads(row["notes_json"], []) if row else []
        if note:
            notes.append({"at": ts, "note": note})
        conn.execute(
            """INSERT INTO route_query_stats (route_id, query_key, query, positive_count, negative_count,
               last_positive, last_negative, notes_json) VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(route_id, query_key) DO UPDATE SET
               positive_count=positive_count+excluded.positive_count,
               negative_count=negative_count+excluded.negative_count,
               last_positive=COALESCE(excluded.last_positive, last_positive),
               last_negative=COALESCE(excluded.last_negative, last_negative),
               notes_json=excluded.notes_json""",
            (route_id, key, query, positive, negative,
             ts if positive else None, ts if negative else None, json_dumps(notes)),
        )


def query_stats_for(route_id: str, query: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM route_query_stats WHERE route_id=? AND query_key=?",
            (route_id, query_key(query)),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["notes"] = json_loads(d.pop("notes_json"), [])
    return d


def all_query_stats() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM route_query_stats ORDER BY negative_count DESC, positive_count DESC").fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["notes"] = json_loads(d.pop("notes_json"), [])
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Update plans
# ---------------------------------------------------------------------------

def create_update_plan(kind: str, *, query: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    init_db()
    pid = new_id("plan")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "INSERT INTO update_plans (plan_id, kind, status, query, payload_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (pid, kind, "draft", query, json_dumps(payload or {}), ts, ts),
        )
    return get_update_plan(pid) or {"plan_id": pid}


def get_update_plan(plan_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM update_plans WHERE plan_id=?", (plan_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"] = json_loads(d.pop("payload_json"), {})
    return d


def list_update_plans(status: str | None = "draft", limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    if status:
        sql = "SELECT * FROM update_plans WHERE status=? ORDER BY created_at DESC LIMIT ?"
        params: tuple[Any, ...] = (status, limit)
    else:
        sql = "SELECT * FROM update_plans ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["payload"] = json_loads(d.pop("payload_json"), {})
        results.append(d)
    return results


def mark_update_plan(plan_id: str, status: str) -> None:
    ts = now_iso()
    applied_at = ts if status == "applied" else None
    with connect() as conn:
        conn.execute(
            "UPDATE update_plans SET status=?, updated_at=?, applied_at=COALESCE(?,applied_at) WHERE plan_id=?",
            (status, ts, applied_at, plan_id),
        )


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------

def create_correction(query: str, wrong_paths: list[str], missed_paths: list[str], note: str = "") -> dict[str, Any]:
    init_db()
    cid = new_id("correction")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "INSERT INTO corrections (correction_id, query, wrong_paths_json, missed_paths_json, note, created_at) VALUES (?,?,?,?,?,?)",
            (cid, query, json_dumps(wrong_paths), json_dumps(missed_paths), note, ts),
        )
    return {"correction_id": cid, "created_at": ts}


# ---------------------------------------------------------------------------
# Aggregate counts
# ---------------------------------------------------------------------------

def lso_counts() -> dict[str, Any]:
    init_db()
    with connect() as conn:
        values: dict[str, Any] = {
            "routes": conn.execute("SELECT COUNT(*) FROM routes WHERE status='active'").fetchone()[0],
            "candidate_routes": conn.execute("SELECT COUNT(*) FROM routes WHERE status='candidate'").fetchone()[0],
            "deferred_routes": conn.execute("SELECT COUNT(*) FROM routes WHERE status='deferred'").fetchone()[0],
            "annotations": conn.execute("SELECT COUNT(*) FROM file_annotations").fetchone()[0],
            "annotated": conn.execute("SELECT COUNT(*) FROM file_annotations WHERE decision='annotate'").fetchone()[0],
            "evidence_items": conn.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0],
            "areas": conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0],
            "draft_update_plans": conn.execute("SELECT COUNT(*) FROM update_plans WHERE status='draft'").fetchone()[0],
            "corrections": conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0],
            "deferred_items": conn.execute("SELECT COUNT(*) FROM deferred_items WHERE status='pending'").fetchone()[0],
            "seed_sessions": conn.execute("SELECT COUNT(*) FROM seed_map_sessions").fetchone()[0],
        }
        tiers = {
            row["tier"]: row["c"]
            for row in conn.execute("SELECT tier, COUNT(*) AS c FROM routes WHERE status='active' GROUP BY tier").fetchall()
        }
    values["tiers"] = tiers
    values["db_path"] = str(config.DB_PATH)
    return values
