"""SQLite persistence for Local Semantic Overlay."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DB_PATH


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
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> dict[str, Any]:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS routes (
                route_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                brief TEXT NOT NULL,
                route_tags_json TEXT NOT NULL DEFAULT '[]',
                facets_json TEXT NOT NULL DEFAULT '[]',
                tier TEXT NOT NULL DEFAULT 'cold',
                confidence REAL NOT NULL DEFAULT 0.3,
                usage_score REAL NOT NULL DEFAULT 0,
                quality_score REAL NOT NULL DEFAULT 0,
                risk_score REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT,
                usage_verification TEXT NOT NULL DEFAULT 'observed',
                evidence_confidence TEXT NOT NULL DEFAULT 'medium',
                route_terms_json TEXT NOT NULL DEFAULT '[]',
                task_affordances_json TEXT NOT NULL DEFAULT '[]',
                search_hints_json TEXT NOT NULL DEFAULT '[]',
                uncertainty_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used TEXT
            );

            CREATE TABLE IF NOT EXISTS route_anchors (
                anchor_id TEXT PRIMARY KEY,
                route_id TEXT NOT NULL REFERENCES routes(route_id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                note TEXT,
                weight REAL NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_used TEXT,
                UNIQUE(route_id, path)
            );

            CREATE TABLE IF NOT EXISTS route_evidence (
                evidence_id TEXT PRIMARY KEY,
                route_id TEXT NOT NULL REFERENCES routes(route_id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'evidence',
                note TEXT,
                weight REAL NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_used TEXT,
                UNIQUE(route_id, path, role)
            );

            CREATE TABLE IF NOT EXISTS task_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT,
                event_type TEXT NOT NULL,
                query TEXT,
                route_ids_json TEXT NOT NULL DEFAULT '[]',
                paths_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS route_query_stats (
                route_id TEXT NOT NULL REFERENCES routes(route_id) ON DELETE CASCADE,
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

            CREATE TABLE IF NOT EXISTS seed_map_sessions (
                session_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                route_budget INTEGER NOT NULL,
                max_clusters INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                report_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS terrain_clusters (
                cluster_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES seed_map_sessions(session_id) ON DELETE CASCADE,
                anchor_path TEXT NOT NULL,
                signals_json TEXT NOT NULL DEFAULT '[]',
                representative_paths_json TEXT NOT NULL DEFAULT '[]',
                child_areas_json TEXT NOT NULL DEFAULT '[]',
                evidence_potential TEXT NOT NULL DEFAULT 'weak',
                mappability_score REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'unmapped',
                route_id TEXT,
                uncertainty_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, anchor_path)
            );

            CREATE TABLE IF NOT EXISTS cluster_evidence (
                evidence_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES seed_map_sessions(session_id) ON DELETE CASCADE,
                cluster_id TEXT NOT NULL REFERENCES terrain_clusters(cluster_id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                path TEXT,
                text_head TEXT,
                note TEXT,
                weight REAL NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_routes_tier ON routes(tier);
            CREATE INDEX IF NOT EXISTS idx_routes_updated ON routes(updated_at);
            CREATE INDEX IF NOT EXISTS idx_anchors_path ON route_anchors(path);
            CREATE INDEX IF NOT EXISTS idx_evidence_path ON route_evidence(path);
            CREATE INDEX IF NOT EXISTS idx_events_type ON task_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_plans_status ON update_plans(status);
            CREATE INDEX IF NOT EXISTS idx_seed_sessions_status ON seed_map_sessions(status);
            CREATE INDEX IF NOT EXISTS idx_clusters_session ON terrain_clusters(session_id);
            CREATE INDEX IF NOT EXISTS idx_clusters_status ON terrain_clusters(status);
            CREATE INDEX IF NOT EXISTS idx_cluster_evidence_cluster ON cluster_evidence(cluster_id);
            """
        )
        for column, definition in {
            "usage_verification": "TEXT NOT NULL DEFAULT 'observed'",
            "evidence_confidence": "TEXT NOT NULL DEFAULT 'medium'",
            "route_terms_json": "TEXT NOT NULL DEFAULT '[]'",
            "task_affordances_json": "TEXT NOT NULL DEFAULT '[]'",
            "search_hints_json": "TEXT NOT NULL DEFAULT '[]'",
            "uncertainty_note": "TEXT",
        }.items():
            _ensure_column(conn, "routes", column, definition)
    return {"ok": True, "db_path": str(DB_PATH)}


def row_to_route(row: sqlite3.Row) -> dict[str, Any]:
    route = dict(row)
    route["route_tags"] = json_loads(route.pop("route_tags_json"), [])
    route["facets"] = json_loads(route.pop("facets_json"), [])
    route["route_terms"] = json_loads(route.pop("route_terms_json", None), [])
    route["task_affordances"] = json_loads(route.pop("task_affordances_json", None), [])
    route["search_hints"] = json_loads(route.pop("search_hints_json", None), [])
    return route


def create_route(
    title: str,
    brief: str,
    *,
    route_tags: list[str] | None = None,
    facets: list[str] | None = None,
    route_terms: list[str] | None = None,
    task_affordances: list[str] | None = None,
    search_hints: list[dict[str, Any]] | None = None,
    anchors: list[str] | None = None,
    evidence_paths: list[str] | None = None,
    tier: str = "cold",
    confidence: float = 0.3,
    usage_verification: str = "observed",
    evidence_confidence: str = "medium",
    uncertainty_note: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    init_db()
    route_id = new_id("route")
    ts = now_iso()
    tags = sorted({str(tag).strip() for tag in (route_tags or []) if str(tag).strip()})
    facet_values = sorted({str(facet).strip() for facet in (facets or []) if str(facet).strip()})
    terms = sorted({str(term).strip().lower() for term in (route_terms or []) if str(term).strip()})
    affordances = [str(item).strip() for item in (task_affordances or []) if str(item).strip()]
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO routes (
                route_id, title, brief, route_tags_json, facets_json, tier,
                confidence, quality_score, source, usage_verification,
                evidence_confidence, route_terms_json, task_affordances_json,
                search_hints_json, uncertainty_note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                route_id,
                title.strip(),
                brief.strip(),
                json_dumps(tags),
                json_dumps(facet_values),
                tier,
                confidence,
                confidence,
                source,
                usage_verification,
                evidence_confidence,
                json_dumps(terms),
                json_dumps(affordances),
                json_dumps(search_hints or []),
                uncertainty_note,
                ts,
                ts,
            ),
        )
        for path in anchors or []:
            add_anchor(route_id, path, conn=conn)
        for path in evidence_paths or []:
            add_evidence(route_id, path, role="evidence", conn=conn)
    return get_route(route_id) or {"route_id": route_id}


def get_route(route_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM routes WHERE route_id = ?", (route_id,)).fetchone()
    return row_to_route(row) if row else None


def update_route_verification(
    route_id: str,
    *,
    usage_verification: str | None = None,
    evidence_confidence: str | None = None,
    uncertainty_note: str | None = None,
) -> None:
    route = get_route(route_id)
    if not route:
        return
    with connect() as conn:
        conn.execute(
            """
            UPDATE routes
            SET usage_verification = ?,
                evidence_confidence = ?,
                uncertainty_note = COALESCE(?, uncertainty_note),
                updated_at = ?
            WHERE route_id = ?
            """,
            (
                usage_verification or route.get("usage_verification") or "observed",
                evidence_confidence or route.get("evidence_confidence") or "medium",
                uncertainty_note,
                now_iso(),
                route_id,
            ),
        )


def update_route_labels(
    route_id: str,
    *,
    route_tags: list[str] | None = None,
    facets: list[str] | None = None,
    route_terms: list[str] | None = None,
    uncertainty_note: str | None = None,
) -> dict[str, Any] | None:
    route = get_route(route_id)
    if not route:
        return None
    next_tags = sorted({str(tag).strip() for tag in (route_tags if route_tags is not None else route.get("route_tags") or []) if str(tag).strip()})
    next_facets = sorted({str(facet).strip() for facet in (facets if facets is not None else route.get("facets") or []) if str(facet).strip()})
    next_terms = sorted({str(term).strip().lower() for term in (route_terms if route_terms is not None else route.get("route_terms") or []) if str(term).strip()})
    with connect() as conn:
        conn.execute(
            """
            UPDATE routes
            SET route_tags_json = ?,
                facets_json = ?,
                route_terms_json = ?,
                uncertainty_note = COALESCE(?, uncertainty_note),
                updated_at = ?
            WHERE route_id = ?
            """,
            (json_dumps(next_tags), json_dumps(next_facets), json_dumps(next_terms), uncertainty_note, now_iso(), route_id),
        )
    return get_route(route_id)


def list_routes(status: str = "active") -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM routes WHERE status = ? ORDER BY tier, usage_score DESC, confidence DESC, updated_at DESC",
            (status,),
        ).fetchall()
    return [row_to_route(row) for row in rows]


def create_seed_session(scope: str, route_budget: int, max_clusters: int) -> dict[str, Any]:
    init_db()
    session_id = new_id("seed")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO seed_map_sessions (session_id, scope, route_budget, max_clusters, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'open', ?, ?)
            """,
            (session_id, normalize_path(scope), int(route_budget), int(max_clusters), ts, ts),
        )
    return get_seed_session(session_id) or {"session_id": session_id}


def get_seed_session(session_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM seed_map_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["report"] = json_loads(item.pop("report_json"), {})
    return item


def update_seed_session(session_id: str, *, status: str | None = None, report: dict[str, Any] | None = None) -> None:
    session = get_seed_session(session_id)
    if not session:
        return
    ts = now_iso()
    next_status = status or session["status"]
    finished_at = ts if next_status in {"complete", "usable_partial", "incomplete", "protocol_unhealthy", "failed"} else session.get("finished_at")
    with connect() as conn:
        conn.execute(
            """
            UPDATE seed_map_sessions
            SET status = ?, report_json = ?, updated_at = ?, finished_at = ?
            WHERE session_id = ?
            """,
            (next_status, json_dumps(report if report is not None else session.get("report") or {}), ts, finished_at, session_id),
        )


def upsert_terrain_cluster(
    session_id: str,
    anchor_path: str | Path,
    *,
    signals: list[str],
    representative_paths: list[str],
    child_areas: list[str],
    evidence_potential: str,
    mappability_score: float,
    status: str = "unmapped",
) -> dict[str, Any]:
    init_db()
    cluster_id = new_id("cluster")
    ts = now_iso()
    path_text = normalize_path(anchor_path)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO terrain_clusters (
                cluster_id, session_id, anchor_path, signals_json,
                representative_paths_json, child_areas_json, evidence_potential,
                mappability_score, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, anchor_path) DO UPDATE SET
                signals_json = excluded.signals_json,
                representative_paths_json = excluded.representative_paths_json,
                child_areas_json = excluded.child_areas_json,
                evidence_potential = excluded.evidence_potential,
                mappability_score = excluded.mappability_score,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                cluster_id,
                session_id,
                path_text,
                json_dumps(signals),
                json_dumps(representative_paths),
                json_dumps(child_areas),
                evidence_potential,
                float(mappability_score),
                status,
                ts,
                ts,
            ),
        )
        row = conn.execute(
            "SELECT * FROM terrain_clusters WHERE session_id = ? AND anchor_path = ?",
            (session_id, path_text),
        ).fetchone()
    return row_to_cluster(row)


def row_to_cluster(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["signals"] = json_loads(item.pop("signals_json"), [])
    item["representative_paths"] = json_loads(item.pop("representative_paths_json"), [])
    item["child_areas"] = json_loads(item.pop("child_areas_json"), [])
    return item


def list_terrain_clusters(session_id: str, status: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM terrain_clusters WHERE session_id = ?"
    params: list[Any] = [session_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY mappability_score DESC, anchor_path"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [row_to_cluster(row) for row in rows]


def get_terrain_cluster(cluster_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM terrain_clusters WHERE cluster_id = ?", (cluster_id,)).fetchone()
    return row_to_cluster(row) if row else None


def update_cluster_status(
    cluster_id: str,
    status: str,
    *,
    route_id: str | None = None,
    uncertainty_note: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE terrain_clusters
            SET status = ?,
                route_id = COALESCE(?, route_id),
                uncertainty_note = COALESCE(?, uncertainty_note),
                updated_at = ?
            WHERE cluster_id = ?
            """,
            (status, route_id, uncertainty_note, now_iso(), cluster_id),
        )


def add_cluster_evidence(
    session_id: str,
    cluster_id: str,
    *,
    kind: str,
    path: str | Path | None = None,
    text_head: str | None = None,
    note: str | None = None,
    weight: float = 1,
) -> dict[str, Any]:
    init_db()
    evidence_id = new_id("ev")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO cluster_evidence (evidence_id, session_id, cluster_id, kind, path, text_head, note, weight, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (evidence_id, session_id, cluster_id, kind, normalize_path(path) if path else None, text_head, note, float(weight), ts),
        )
    return {
        "evidence_id": evidence_id,
        "session_id": session_id,
        "cluster_id": cluster_id,
        "kind": kind,
        "path": normalize_path(path) if path else None,
        "text_head": text_head,
        "note": note,
        "weight": weight,
        "created_at": ts,
    }


def list_cluster_evidence(cluster_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cluster_evidence WHERE cluster_id = ? ORDER BY weight DESC, created_at",
            (cluster_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_anchor(route_id: str, path: str | Path, note: str | None = None, weight: float = 1, *, conn: sqlite3.Connection | None = None) -> str:
    anchor_id = new_id("anchor")
    ts = now_iso()
    close = conn is None
    conn = conn or connect()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO route_anchors (anchor_id, route_id, path, note, weight, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (anchor_id, route_id, normalize_path(path), note, weight, ts),
        )
        conn.execute("UPDATE routes SET updated_at = ? WHERE route_id = ?", (ts, route_id))
        if close:
            conn.commit()
    finally:
        if close:
            conn.close()
    return anchor_id


def add_evidence(
    route_id: str,
    path: str | Path,
    *,
    role: str = "evidence",
    note: str | None = None,
    weight: float = 1,
    touch_used: bool = False,
    conn: sqlite3.Connection | None = None,
) -> str:
    evidence_id = new_id("evidence")
    ts = now_iso()
    last_used = ts if touch_used else None
    close = conn is None
    conn = conn or connect()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO route_evidence (evidence_id, route_id, path, role, note, weight, created_at, last_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (evidence_id, route_id, normalize_path(path), role, note, weight, ts, last_used),
        )
        if touch_used:
            conn.execute(
                "UPDATE route_evidence SET last_used = ? WHERE route_id = ? AND path = ? AND role = ?",
                (ts, route_id, normalize_path(path), role),
            )
        conn.execute("UPDATE routes SET updated_at = ? WHERE route_id = ?", (ts, route_id))
        if close:
            conn.commit()
    finally:
        if close:
            conn.close()
    return evidence_id


def list_anchors(route_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM route_anchors WHERE route_id = ? ORDER BY weight DESC, path",
            (route_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def route_by_anchor(path: str | Path) -> dict[str, Any] | None:
    init_db()
    normalized = normalize_path(path)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT r.*
            FROM routes r
            JOIN route_anchors a ON a.route_id = r.route_id
            WHERE a.path = ? AND r.status = 'active'
            ORDER BY r.usage_score DESC, r.confidence DESC, r.updated_at DESC
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
    return row_to_route(row) if row else None


def list_evidence(route_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM route_evidence WHERE route_id = ? ORDER BY weight DESC, last_used DESC, created_at DESC, path"
    params: list[Any] = [route_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def create_event(
    event_type: str,
    *,
    query: str | None = None,
    task_id: str | None = None,
    route_ids: list[str] | None = None,
    paths: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_db()
    event_id = new_id("event")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO task_events (event_id, task_id, event_type, query, route_ids_json, paths_json, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, task_id, event_type, query, json_dumps(route_ids or []), json_dumps(paths or []), json_dumps(payload or {}), ts),
        )
    return {"event_id": event_id, "created_at": ts}


def list_events(event_type: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    if event_type:
        sql = "SELECT * FROM task_events WHERE event_type = ? ORDER BY created_at DESC LIMIT ?"
        params: tuple[Any, ...] = (event_type, limit)
    else:
        sql = "SELECT * FROM task_events ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["route_ids"] = json_loads(item.pop("route_ids_json"), [])
        item["paths"] = json_loads(item.pop("paths_json"), [])
        item["payload"] = json_loads(item.pop("payload_json"), {})
        events.append(item)
    return events


def create_update_plan(kind: str, *, query: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    init_db()
    plan_id = new_id("plan")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO update_plans (plan_id, kind, status, query, payload_json, created_at, updated_at)
            VALUES (?, ?, 'draft', ?, ?, ?, ?)
            """,
            (plan_id, kind, query, json_dumps(payload or {}), ts, ts),
        )
    return get_update_plan(plan_id) or {"plan_id": plan_id}


def get_update_plan(plan_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM update_plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["payload"] = json_loads(item.pop("payload_json"), {})
    return item


def list_update_plans(status: str | None = "draft", limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    if status:
        sql = "SELECT * FROM update_plans WHERE status = ? ORDER BY created_at DESC LIMIT ?"
        params: tuple[Any, ...] = (status, limit)
    else:
        sql = "SELECT * FROM update_plans ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    plans: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = json_loads(item.pop("payload_json"), {})
        plans.append(item)
    return plans


def mark_update_plan(plan_id: str, status: str) -> None:
    ts = now_iso()
    applied_at = ts if status == "applied" else None
    with connect() as conn:
        conn.execute(
            "UPDATE update_plans SET status = ?, updated_at = ?, applied_at = COALESCE(?, applied_at) WHERE plan_id = ?",
            (status, ts, applied_at, plan_id),
        )


def bump_route(
    route_id: str,
    *,
    usage_delta: float = 0,
    risk_delta: float = 0,
    confidence_delta: float = 0,
    tier: str | None = None,
    used: bool = False,
) -> None:
    ts = now_iso()
    route = get_route(route_id)
    if not route:
        return
    next_confidence = max(0.0, min(1.0, float(route["confidence"]) + confidence_delta))
    next_tier = tier or route["tier"]
    with connect() as conn:
        conn.execute(
            """
            UPDATE routes
            SET usage_score = usage_score + ?,
                risk_score = MAX(0, risk_score + ?),
                confidence = ?,
                tier = ?,
                updated_at = ?,
                last_used = CASE WHEN ? THEN ? ELSE last_used END
            WHERE route_id = ?
            """,
            (usage_delta, risk_delta, next_confidence, next_tier, ts, 1 if used else 0, ts, route_id),
        )


def set_route_tier(route_id: str, tier: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE routes SET tier = ?, updated_at = ? WHERE route_id = ?", (tier, now_iso(), route_id))


def upsert_query_stats(route_id: str, query: str, *, positive: int = 0, negative: int = 0, note: str | None = None) -> None:
    key = query_key(query)
    ts = now_iso()
    with connect() as conn:
        row = conn.execute(
            "SELECT notes_json FROM route_query_stats WHERE route_id = ? AND query_key = ?",
            (route_id, key),
        ).fetchone()
        notes = json_loads(row["notes_json"], []) if row else []
        if note:
            notes.append({"at": ts, "note": note})
        conn.execute(
            """
            INSERT INTO route_query_stats (
                route_id, query_key, query, positive_count, negative_count,
                last_positive, last_negative, notes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(route_id, query_key) DO UPDATE SET
                positive_count = positive_count + excluded.positive_count,
                negative_count = negative_count + excluded.negative_count,
                last_positive = COALESCE(excluded.last_positive, last_positive),
                last_negative = COALESCE(excluded.last_negative, last_negative),
                notes_json = excluded.notes_json
            """,
            (
                route_id,
                key,
                query,
                positive,
                negative,
                ts if positive else None,
                ts if negative else None,
                json_dumps(notes),
            ),
        )


def query_stats_for(route_id: str, query: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM route_query_stats WHERE route_id = ? AND query_key = ?",
            (route_id, query_key(query)),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["notes"] = json_loads(item.pop("notes_json"), [])
    return item


def all_query_stats() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM route_query_stats ORDER BY negative_count DESC, positive_count DESC").fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["notes"] = json_loads(item.pop("notes_json"), [])
        items.append(item)
    return items


def create_correction(query: str, wrong_paths: list[str], missed_paths: list[str], note: str = "") -> dict[str, Any]:
    init_db()
    correction_id = new_id("correction")
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO corrections (correction_id, query, wrong_paths_json, missed_paths_json, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (correction_id, query, json_dumps(wrong_paths), json_dumps(missed_paths), note, ts),
        )
    return {"correction_id": correction_id, "created_at": ts}


def lso_counts() -> dict[str, Any]:
    init_db()
    with connect() as conn:
        values = {
            "routes": conn.execute("SELECT COUNT(*) FROM routes WHERE status = 'active'").fetchone()[0],
            "predicted_routes": conn.execute("SELECT COUNT(*) FROM routes WHERE status = 'active' AND usage_verification = 'predicted'").fetchone()[0],
            "observed_routes": conn.execute("SELECT COUNT(*) FROM routes WHERE status = 'active' AND usage_verification != 'predicted'").fetchone()[0],
            "anchors": conn.execute("SELECT COUNT(*) FROM route_anchors").fetchone()[0],
            "evidence_paths": conn.execute("SELECT COUNT(*) FROM route_evidence").fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0],
            "draft_update_plans": conn.execute("SELECT COUNT(*) FROM update_plans WHERE status = 'draft'").fetchone()[0],
            "corrections": conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0],
            "seed_sessions": conn.execute("SELECT COUNT(*) FROM seed_map_sessions").fetchone()[0],
            "terrain_clusters": conn.execute("SELECT COUNT(*) FROM terrain_clusters").fetchone()[0],
        }
        tiers = {
            row["tier"]: row["count"]
            for row in conn.execute("SELECT tier, COUNT(*) AS count FROM routes WHERE status = 'active' GROUP BY tier").fetchall()
        }
    values["tiers"] = tiers
    values["db_path"] = str(DB_PATH)
    return values
