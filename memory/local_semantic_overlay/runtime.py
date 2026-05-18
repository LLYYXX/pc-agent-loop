"""Runtime APIs for Local Semantic Overlay v2 (Area-Aware Annotation-First).

Recall order: route -> file annotation -> deferred evidence -> Everything/es fallback.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import store
from .cold_start import search_file_annotations
from .config import DEFAULT_EVIDENCE_LIMIT, DEFAULT_RECALL_LIMIT, GENERIC_TAGS
from .maintenance import maintenance_tick, route_score
from .search_substrate import search_files_rows

GENERIC_QUERY_TERMS = {
    "file", "files", "folder", "folders", "directory", "directories",
    "document", "documents", "project", "projects", "misc", "general",
    "archive", "code", "data",
    "文件", "目录", "文件夹", "项目", "文档", "资料", "材料", "集合", "本机", "所有",
}


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered))
    tokens.update(re.findall(r"[\u4e00-\u9fff]{2,}", lowered))
    return {t for t in tokens if t not in GENERIC_QUERY_TERMS}


def _cue_matches(cue: str, query_tokens: set[str], query_lc: str) -> bool:
    """Check if a cue term matches the query. Respects Chinese 2-char minimum and exact-token for short terms."""
    cue = cue.strip().lower()
    if not cue or cue in GENERIC_QUERY_TERMS:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in cue):
        return len(cue) >= 2 and cue in query_lc
    if re.fullmatch(r"[a-z0-9]{1,2}", cue):
        return cue in query_tokens
    if len(cue) >= 3 and cue in query_lc:
        return True
    cue_tokens = _tokens(cue)
    return bool(cue_tokens & query_tokens)


def _route_text(route: dict[str, Any]) -> str:
    parts = [
        route.get("title") or "",
        route.get("brief") or "",
        route.get("use_when") or "",
        " ".join(route.get("tags") or []),
        " ".join(route.get("route_terms") or []),
    ]
    meta = route.get("route_meta") or {}
    for v in meta.values():
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif isinstance(v, str):
            parts.append(v)
    return " ".join(parts)


def _score_route(route: dict[str, Any], query: str) -> tuple[float, list[str]]:
    query_tokens = _tokens(query)
    query_lc = query.lower()
    why: list[str] = []
    score = route_score(route)
    real_matches = 0

    meta = route.get("route_meta") or {}
    positive_cues = list(route.get("route_terms") or []) + list(meta.get("positive_cues") or [])
    negative_cues = list(meta.get("negative_cues") or [])

    for cue in positive_cues:
        if _cue_matches(cue, query_tokens, query_lc):
            real_matches += 1
            score += 4
            why.append(f"cue:{cue}")

    for tag in route.get("tags") or []:
        tag_lc = tag.lower()
        if tag_lc and tag_lc not in GENERIC_TAGS and _cue_matches(tag_lc, query_tokens, query_lc):
            real_matches += 1
            score += 3
            why.append(f"tag:{tag}")

    role = str(meta.get("role") or "").strip()
    if role and _cue_matches(role, query_tokens, query_lc):
        real_matches += 1
        score += 2
        why.append("role cue")

    boundary = str(meta.get("boundary_note") or "")
    boundary_overlap = query_tokens & _tokens(boundary)
    if boundary_overlap:
        real_matches += 1
        score += min(3, len(boundary_overlap))
        why.append("boundary overlap")

    negative_hits = [c for c in negative_cues if _cue_matches(c, query_tokens, query_lc)]
    if negative_hits:
        score -= len(negative_hits) * 3
        why.append(f"negative_cue:{negative_hits[0]}")

    if real_matches == 0:
        text_tokens = _tokens(_route_text(route))
        overlap = query_tokens & text_tokens
        if overlap:
            score += len(overlap) * 1.5
            why.append("weak text overlap")
        else:
            score = -5
            why.append("no real match")

    stats = store.query_stats_for(route["route_id"], query)
    if stats:
        score += float(stats["positive_count"]) * 3
        score -= float(stats["negative_count"]) * 5
        if stats["positive_count"]:
            why.append("positive feedback")
        if stats["negative_count"]:
            why.append("negative feedback")

    return score, why or ["semantic route candidate"]


def _route_card(route: dict[str, Any], score: float, why: list[str]) -> dict[str, Any]:
    return {
        "route_id": route["route_id"],
        "title": route["title"],
        "brief": route["brief"],
        "use_when": route.get("use_when"),
        "anchor_path": route.get("anchor_path"),
        "entrypoints": route.get("entrypoints", [])[:5],
        "tags": route.get("tags", []),
        "route_terms": route.get("route_terms", [])[:8],
        "tier": route["tier"],
        "confidence": route["confidence"],
        "usage_verification": route.get("usage_verification", "seeded"),
        "score": round(score, 3),
        "why_match": why[:4],
    }


def _annotation_hit(ann: dict[str, Any], score: float) -> dict[str, Any]:
    return {
        "annotation_id": ann["annotation_id"],
        "path": ann["path"],
        "tags": ann.get("tags", []),
        "value_reason": ann.get("value_reason"),
        "evidence_summary": ann.get("evidence_summary"),
        "confidence": ann.get("confidence", 0),
        "score": round(score, 3),
        "hit_type": "annotation",
    }


def recall_routes(query: str | None = None, scope: str | None = None, limit: int = DEFAULT_RECALL_LIMIT, **kwargs: Any) -> dict[str, Any]:
    store.init_db()
    q = (query or kwargs.get("task_intent") or kwargs.get("q") or "").strip()
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for route in store.list_routes():
        # fix8: filter routes by scope when provided
        if scope and route.get("anchor_path"):
            if not _is_under(route["anchor_path"], scope) and not _is_under(scope, route["anchor_path"]):
                continue
        s, why = _score_route(route, q)
        if s > 0:
            scored.append((s, route, why))
    scored.sort(key=lambda x: x[0], reverse=True)
    hits = [_route_card(r, s, w) for s, r, w in scored[:limit]]
    event = store.create_event("recall", query=q, route_ids=[h["route_id"] for h in hits],
                               payload={"limit": limit, "hit_count": len(hits)})
    return {
        "ok": True,
        "query": q,
        "hits": hits,
        "routes": hits,
        "hit_count": len(hits),
        "fallback_suggested": len(hits) == 0,
        "event_id": event["event_id"],
    }


def recall_hits(query: str, limit: int = DEFAULT_RECALL_LIMIT, **kwargs: Any) -> list[dict[str, Any]]:
    return list(recall_routes(query, limit=limit, **kwargs).get("hits") or [])


def list_route_cards(limit: int = 50, tier: str | None = None, status: str = "active") -> list[dict[str, Any]]:
    routes = store.list_routes(status=status)
    if tier:
        routes = [r for r in routes if r.get("tier") == tier]
    routes = sorted(routes, key=route_score, reverse=True)[:max(0, int(limit))]
    return [_route_card(r, route_score(r), ["listed"]) for r in routes]


def run_file_query(query: str, scope: str | None = None, limit: int = DEFAULT_RECALL_LIMIT, fallback: bool = True) -> dict[str, Any]:
    """Multi-layer recall: route -> annotation -> deferred -> es fallback."""
    store.init_db()

    route_recall = recall_routes(query, scope=scope, limit=limit)
    route_hits = list(route_recall.get("hits") or [])

    ann_hits_raw = search_file_annotations(query, scope=scope, limit=limit)
    ann_hits = [_annotation_hit(a, 0) for a in ann_hits_raw]
    for i, h in enumerate(ann_hits):
        h["score"] = round(10.0 - i * 0.5, 3)
    ann_hits = [h for h in ann_hits if h["score"] > 2]

    # fix9: deferred_hits with query scoring and scope filter
    deferred_hits = _score_deferred(query, scope=scope, limit=5)

    search_hits: list[dict[str, Any]] = []
    fallback_used = False
    fallback_reason = None

    has_strong = any(h.get("score", 0) > 5 for h in route_hits)
    if fallback and not has_strong and len(route_hits) + len(ann_hits) < min(3, limit):
        try:
            search_hits = search_files_rows(query, scope=scope, limit=limit)
            fallback_used = True
            fallback_reason = "insufficient route and annotation hits"
        except Exception as exc:
            fallback_reason = str(exc)

    seen: set[str] = set()
    all_hits: list[dict[str, Any]] = []
    for h in route_hits + ann_hits:
        key = h.get("route_id") or h.get("annotation_id") or h.get("path") or str(h)
        if key not in seen:
            seen.add(key)
            all_hits.append(h)
    for h in search_hits:
        p = h.get("path") or ""
        if p and p not in seen:
            seen.add(p)
            all_hits.append(h)

    if route_hits:
        action = "use_route"
    elif ann_hits:
        action = "inspect_annotation"
    elif search_hits:
        action = "fallback_search"
    else:
        action = "ask_user"

    return {
        "ok": True,
        "schema_version": "lso_runtime_v2",
        "query": query,
        "route_hits": route_hits,
        "file_annotation_hits": ann_hits,
        "deferred_hits": deferred_hits,
        "search_hits": search_hits,
        "all_hits": all_hits,
        "routes": route_hits,
        "hits": all_hits,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "recommended_next_action": action,
        "finalize_required": True,
    }


def expand_route(route_id: str, query: str | None = None, budget: str = "brief") -> dict[str, Any]:
    route = store.get_route(route_id)
    if not route:
        return {"ok": False, "route_id": route_id, "error": "route not found"}
    limits = {"brief": 5, "normal": DEFAULT_EVIDENCE_LIMIT, "full": 50}
    limit = limits.get(budget, DEFAULT_EVIDENCE_LIMIT)

    ann_ids = route.get("supporting_annotation_ids") or []
    annotations = [store.get_annotation(aid) for aid in ann_ids]
    annotations = [a for a in annotations if a][:limit]

    store.create_event("expand", query=query, route_ids=[route_id], payload={"budget": budget})
    return {
        "ok": True,
        "route": _route_card(route, route_score(route), ["expanded"]),
        "entrypoints": route.get("entrypoints", []),
        "supporting_annotations": annotations,
        "route_meta": route.get("route_meta", {}),
    }


def system_overview(max_chars: int = 1500) -> dict[str, Any]:
    routes = sorted(store.list_routes(), key=route_score, reverse=True)[:20]
    ann_count = store.lso_counts().get("annotated", 0)
    lines = [f"LSO overview: {len(routes)} active routes, {ann_count} file annotations"]
    for r in routes:
        line = f"- {r['title']}: {r['brief']} | tier={r['tier']} anchor={r.get('anchor_path', '?')}"
        lines.append(line)
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars - 3] + "..."
    return {"ok": True, "overview": text, "route_count": len(routes), "annotation_count": ann_count}


def begin_file_task(query: str) -> dict[str, Any]:
    task_id = store.new_id("task")
    event = store.create_event("begin_task", query=query, task_id=task_id)
    return {"ok": True, "task_id": task_id, "query": query, "event_id": event["event_id"]}


def finish_file_query(
    result: dict[str, Any],
    used: list[str] | None = None,
    found: list[str] | None = None,
    rejected: list[str] | None = None,
    selected_routes: list[str] | None = None,
    selected_annotations: list[str] | None = None,
) -> dict[str, Any]:
    """Close the loop: record what was used/found/rejected."""
    query = result.get("query") or ""
    used_paths = [store.normalize_path(p) for p in (used or []) if p]
    found_paths = [store.normalize_path(p) for p in (found or []) if p]
    rejected_paths = [store.normalize_path(p) for p in (rejected or []) if p]
    route_ids = [rid for rid in (selected_routes or []) if store.get_route(rid)]
    ann_ids = [aid for aid in (selected_annotations or []) if store.get_annotation(aid)]

    updated_routes: set[str] = set()
    for rid in route_ids:
        store.bump_route(rid, usage_delta=1, confidence_delta=0.03, tier="active", used=True)
        store.upsert_query_stats(rid, query, positive=1, note="route selected")
        updated_routes.add(rid)

    for aid in ann_ids:
        store.bump_annotation_use(aid, delta=1)

    for rid in route_ids:
        if rejected_paths:
            store.bump_route(rid, risk_delta=0.2)
            store.upsert_query_stats(rid, query, negative=len(rejected_paths), note="rejected path")

    recalled_not_selected = set()
    for h in result.get("route_hits") or []:
        rid = h.get("route_id")
        if rid and rid not in route_ids:
            recalled_not_selected.add(rid)
            store.upsert_query_stats(rid, query, negative=0, note="recalled but not selected (weak negative)")

    plans: list[dict[str, Any]] = []
    fallback_found = [p for p in found_paths if p not in used_paths]
    if fallback_found:
        plan = store.create_update_plan("deferred_evidence", query=query,
                                        payload={"paths": fallback_found, "reason": "fallback_found"})
        plans.append(plan)
        for p in fallback_found:
            store.create_deferred("evidence", "found via fallback, not yet annotated",
                                  evidence_id=None, annotation_id=None)

    event = store.create_event(
        "finish_query", query=query,
        route_ids=route_ids, annotation_ids=ann_ids,
        paths=used_paths + found_paths + rejected_paths,
        payload={
            "used": used_paths, "found": found_paths, "rejected": rejected_paths,
            "selected_routes": route_ids, "selected_annotations": ann_ids,
            "created_plans": [p["plan_id"] for p in plans],
        },
    )
    maint = maintenance_tick()
    return {
        "ok": True,
        "query": query,
        "event_id": event["event_id"],
        "updated_routes": sorted(updated_routes),
        "updated_annotations": ann_ids,
        "created_update_plans": plans,
        "maintenance": maint,
    }


def finish_local_file_task(
    query: str,
    used: list[str] | None = None,
    found: list[str] | None = None,
    rejected: list[str] | None = None,
    selected_routes: list[str] | None = None,
    selected_annotations: list[str] | None = None,
) -> dict[str, Any]:
    """Alias for finish_file_query with a synthetic result dict."""
    result = {"query": query, "route_hits": [], "file_annotation_hits": []}
    return finish_file_query(result, used=used, found=found, rejected=rejected,
                             selected_routes=selected_routes, selected_annotations=selected_annotations)


def apply_update_plan(plan_id: str) -> dict[str, Any]:
    plan = store.get_update_plan(plan_id)
    if not plan:
        return {"ok": False, "plan_id": plan_id, "error": "update plan not found"}
    if plan["status"] != "draft":
        return {"ok": False, "plan_id": plan_id, "error": f"plan is {plan['status']}"}
    store.mark_update_plan(plan_id, "applied")
    return {"ok": True, "plan_id": plan_id, "applied": plan["kind"]}


def record_correction(query: str, wrong_paths: list[str] | None = None, missed_paths: list[str] | None = None, note: str = "") -> dict[str, Any]:
    wrong = [store.normalize_path(p) for p in (wrong_paths or []) if p]
    missed = [store.normalize_path(p) for p in (missed_paths or []) if p]
    correction = store.create_correction(query, wrong, missed, note)
    plans: list[dict[str, Any]] = []
    if missed:
        plan = store.create_update_plan("deferred_evidence", query=query,
                                        payload={"paths": missed, "reason": "user_correction"})
        plans.append(plan)
    for route in store.list_routes():
        anchor = route.get("anchor_path") or ""
        if anchor and any(_is_under(p, anchor) for p in wrong):
            store.bump_route(route["route_id"], risk_delta=0.5)
            store.upsert_query_stats(route["route_id"], query, negative=1, note=note or "user correction")
    event = store.create_event("correction", query=query, paths=wrong + missed,
                               payload={"note": note, "plans": [p["plan_id"] for p in plans]})
    return {"ok": True, "correction": correction, "event_id": event["event_id"], "created_plans": plans}


def _is_under(path: str, anchor: str) -> bool:
    try:
        return Path(path).resolve() == Path(anchor).resolve() or Path(anchor).resolve() in Path(path).resolve().parents
    except OSError:
        return path.lower().startswith(anchor.rstrip("\\/").lower() + os.sep.lower())


def _score_deferred(query: str, scope: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Score deferred items against the query instead of returning them blindly."""
    raw = store.list_deferred(status="pending", limit=200)
    if not raw:
        return []
    query_tokens = _tokens(query)
    query_lc = query.lower()
    scored: list[tuple[float, dict[str, Any]]] = []
    for d in raw:
        reason_lc = (d.get("reason") or "").lower()
        reason_tokens = _tokens(reason_lc)
        overlap = query_tokens & reason_tokens
        s = len(overlap) * 2.0
        for cue in query_tokens:
            if len(cue) >= 2 and cue in reason_lc:
                s += 1.5
        if s <= 0:
            continue
        hit = {"deferred_id": d["deferred_id"], "kind": d["kind"], "reason": d["reason"],
               "hit_type": "deferred", "score": round(s, 3)}
        scored.append((s, hit))
    scored.sort(key=lambda x: -x[0])
    return [h for _, h in scored[:limit]]


def update_route_tags(
    route_id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    evidence_note: str | None = None,
    mode: str = "draft",
) -> dict[str, Any]:
    note = (evidence_note or "").strip()
    if not note:
        return {"ok": False, "route_id": route_id, "error": "evidence_note required"}
    route = store.get_route(route_id)
    if not route:
        return {"ok": False, "route_id": route_id, "error": "route not found"}

    add_set = {t.strip().lower() for t in (add or []) if t.strip()}
    remove_set = {t.strip().lower() for t in (remove or []) if t.strip()}
    bad = sorted(add_set & GENERIC_TAGS)
    if bad:
        return {"ok": False, "route_id": route_id, "error": "generic tags not allowed", "tags": bad}

    current = {t.lower() for t in (route.get("tags") or [])}
    next_tags = sorted((current | add_set) - remove_set)
    payload = {"route_id": route_id, "add": sorted(add_set), "remove": sorted(remove_set),
               "next_tags": next_tags, "evidence_note": note}

    if mode == "draft":
        plan = store.create_update_plan("route_tag_update", payload=payload)
        return {"ok": True, "status": "draft", "draft_plan": plan, **payload}

    with store.connect() as conn:
        conn.execute("UPDATE routes SET tags_json=?, updated_at=? WHERE route_id=?",
                     (store.json_dumps(next_tags), store.now_iso(), route_id))
    store.create_event("route_tag_update", route_ids=[route_id], payload=payload)
    return {"ok": True, "status": "applied", "route_id": route_id, **payload}


def audit_runtime(limit: int = 200) -> dict[str, Any]:
    events = store.list_events(limit=limit)
    warnings: list[dict[str, Any]] = []
    recalls = [e for e in events if e["event_type"] == "recall"]
    finishes = [e for e in events if e["event_type"] == "finish_query"]
    finished_queries = {e.get("query") or "" for e in finishes}
    selected_routes: set[str] = set()
    for e in finishes:
        selected_routes.update(e.get("route_ids") or [])
        p = e.get("payload") or {}
        selected_routes.update(p.get("selected_routes") or [])
        if (p.get("found") or p.get("used")) and not p.get("created_plans"):
            warnings.append({"kind": "fallback_without_plan", "event_id": e["event_id"], "query": e.get("query")})

    recalled_routes: set[str] = set()
    for e in recalls:
        recalled_routes.update(e.get("route_ids") or [])
        if e.get("query") not in finished_queries:
            warnings.append({"kind": "recall_not_finalized", "event_id": e["event_id"], "query": e.get("query")})

    unconsumed = sorted(recalled_routes - selected_routes)
    if unconsumed:
        warnings.append({"kind": "recalled_not_consumed", "count": len(unconsumed), "route_ids": unconsumed[:20]})

    return {
        "ok": True,
        "event_count": len(events),
        "recall_count": len(recalls),
        "finish_count": len(finishes),
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def lso_summary() -> dict[str, Any]:
    counts = store.lso_counts()
    tiers = counts.get("tiers") or {}
    routes = list_route_cards(limit=50)
    return {
        "ok": True,
        "counts": counts,
        "route_count": counts.get("routes", 0),
        "annotation_count": counts.get("annotated", 0),
        "active_routes": int(tiers.get("active") or 0),
        "warm_routes": int(tiers.get("warm") or 0),
        "cold_routes": int(tiers.get("cold") or 0),
        "db_path": counts.get("db_path"),
        "routes": routes,
    }
