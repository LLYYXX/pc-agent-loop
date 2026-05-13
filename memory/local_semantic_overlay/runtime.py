"""Runtime APIs for Local Semantic Overlay."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import store
from .config import DEFAULT_EVIDENCE_LIMIT, DEFAULT_RECALL_LIMIT, GENERIC_ROUTE_TAGS
from .maintenance import maintenance_tick, route_score
from .search_substrate import search_files_rows


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered))
    tokens.update(ch for ch in lowered if "\u4e00" <= ch <= "\u9fff")
    return tokens


def _route_text(route: dict[str, Any]) -> str:
    return " ".join(
        [
            route.get("title") or "",
            route.get("brief") or "",
            " ".join(route.get("route_tags") or []),
            " ".join(route.get("facets") or []),
            " ".join(route.get("route_terms") or []),
            " ".join(route.get("task_affordances") or []),
        ]
    )


def _short_anchors(route_id: str, limit: int = 3) -> list[str]:
    return [item["path"] for item in store.list_anchors(route_id)[:limit]]


def _score_route(route: dict[str, Any], query: str) -> tuple[float, list[str]]:
    query_tokens = _tokens(query)
    text_tokens = _tokens(_route_text(route))
    overlap = query_tokens & text_tokens
    query_lc = query.lower()
    why: list[str] = []
    score = route_score(route)
    if overlap:
        score += len(overlap) * 3
        why.append("query overlaps route text")
    for tag in route.get("route_tags") or []:
        tag_lc = tag.lower()
        if tag_lc and (tag_lc in query_lc or tag_lc in query_tokens):
            score += 4
            why.append(f"tag:{tag}")
    for term in route.get("route_terms") or []:
        term_lc = str(term).lower()
        if term_lc and (term_lc in query_lc or term_lc in query_tokens):
            score += 1.5
            why.append(f"cue:{term}")
    if route.get("title", "").lower() in query_lc:
        score += 2
        why.append("title phrase match")
    if route.get("usage_verification") == "predicted":
        score -= 0.75
        why.append("predicted route; verify before trusting")
    stats = store.query_stats_for(route["route_id"], query)
    if stats:
        score += float(stats["positive_count"]) * 3
        score -= float(stats["negative_count"]) * 5
        if stats["positive_count"]:
            why.append("positive task feedback")
        if stats["negative_count"]:
            why.append("negative task feedback")
    return score, why or ["semantic route candidate"]


def _route_card(route: dict[str, Any], score: float, why: list[str]) -> dict[str, Any]:
    anchors = _short_anchors(route["route_id"])
    search_hints = list(route.get("search_hints") or [])
    return {
        "route_id": route["route_id"],
        "title": route["title"],
        "brief": route["brief"],
        "path": anchors[0] if anchors else None,
        "scope": anchors[0] if anchors else None,
        "route_tags": route.get("route_tags", []),
        "route_terms": route.get("route_terms", [])[:8],
        "facets": route.get("facets", []),
        "task_affordances": route.get("task_affordances", []),
        "search_hints": search_hints[:8],
        "tier": route["tier"],
        "confidence": route["confidence"],
        "usage_verification": route.get("usage_verification", "observed"),
        "evidence_confidence": route.get("evidence_confidence", "medium"),
        "verification_required": route.get("usage_verification") == "predicted",
        "score": round(score, 3),
        "anchors": anchors,
        "why_match": why[:3],
        "next_actions": [
            f"expand_route('{route['route_id']}', query=query, budget='normal')",
            "search_files_rows(query, scope=<chosen anchor>, limit=50) if the route is insufficient",
        ],
    }


def _query_from_args(query: str | None = None, **kwargs: Any) -> str:
    value = query or kwargs.get("task_intent") or kwargs.get("q") or kwargs.get("query") or ""
    context = kwargs.get("context_hint")
    if context:
        value = f"{value} {context}".strip()
    return str(value).strip()


def recall_routes(query: str | None = None, limit: int = DEFAULT_RECALL_LIMIT, **kwargs: Any) -> dict[str, Any]:
    store.init_db()
    query_text = _query_from_args(query, **kwargs)
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for route in store.list_routes():
        score, why = _score_route(route, query_text)
        if score > -2:
            scored.append((score, route, why))
    scored.sort(key=lambda item: item[0], reverse=True)
    hits = [_route_card(route, score, why) for score, route, why in scored[:limit] if score > 0]
    event = store.create_event("recall", query=query_text, route_ids=[hit["route_id"] for hit in hits], payload={"limit": limit, "hit_count": len(hits)})
    return {
        "ok": True,
        "query": query_text,
        "hits": hits,
        "routes": hits,
        "hit_count": len(hits),
        "route_count": len(hits),
        "fallback_suggested": len(hits) == 0,
        "event_id": event["event_id"],
        "next": "expand a useful route, or call search_files_rows/search_files_paths as raw-terrain fallback",
    }


def recall_hits(query: str, limit: int = DEFAULT_RECALL_LIMIT, **kwargs: Any) -> list[dict[str, Any]]:
    """Low-friction recall: return just route cards."""

    return list(recall_routes(query, limit=limit, **kwargs).get("hits") or [])


def list_route_cards(limit: int = 50, tier: str | None = None, status: str = "active") -> list[dict[str, Any]]:
    """List route cards through the public API instead of raw SQLite."""

    routes = store.list_routes(status=status)
    if tier:
        routes = [route for route in routes if route.get("tier") == tier]
    routes = sorted(routes, key=route_score, reverse=True)[: max(0, int(limit))]
    return [_route_card(route, route_score(route), ["listed route"]) for route in routes]


def expand_route(route_id: str, query: str | None = None, budget: str = "brief") -> dict[str, Any]:
    route = store.get_route(route_id)
    if not route:
        return {"ok": False, "route_id": route_id, "error": "route not found"}
    limits = {"brief": 5, "normal": DEFAULT_EVIDENCE_LIMIT, "full": 50}
    limit = limits.get(budget, DEFAULT_EVIDENCE_LIMIT)
    anchors = store.list_anchors(route_id)
    evidence = store.list_evidence(route_id, limit=limit)
    search_hints = list(route.get("search_hints") or [])
    for anchor in anchors[:3]:
        if query:
            search_hints.append({"scope": anchor["path"], "query": query})
        for tag in route.get("route_tags") or []:
            search_hints.append({"scope": anchor["path"], "query": tag})
    store.create_event("expand", query=query, route_ids=[route_id], payload={"budget": budget})
    return {
        "ok": True,
        "route": _route_card(route, route_score(route), ["selected route"]),
        "anchors": anchors,
        "evidence_paths": evidence,
        "search_hints": search_hints[:8],
        "task_affordances": route.get("task_affordances", []),
        "uncertainty_note": route.get("uncertainty_note"),
        "cautions": ["Evidence paths are proof/expansion material, not default recall hits."],
    }


def system_overview(max_routes: int = 20, max_chars: int = 4000) -> dict[str, Any]:
    routes = sorted(store.list_routes(), key=route_score, reverse=True)[:max_routes]
    lines = ["Local Semantic Overlay overview:"]
    for route in routes:
        anchors = _short_anchors(route["route_id"], limit=2)
        line = f"- {route['title']}: {route['brief']} | tier={route['tier']} | anchors={anchors}"
        lines.append(line)
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return {"ok": True, "overview": text, "route_count": len(routes), "max_chars": max_chars}


def run_file_query(query: str, scope: str | None = None, limit: int = DEFAULT_RECALL_LIMIT, fallback: bool = True) -> dict[str, Any]:
    """Recall first, then thin Everything fallback, with one stable envelope."""

    recall = recall_routes(query, limit=limit)
    route_hits = list(recall.get("hits") or [])
    search_hits: list[dict[str, Any]] = []
    fallback_error = None
    if fallback and (not route_hits or len(route_hits) < min(3, int(limit))):
        try:
            search_hits = search_files_rows(query, scope=scope, limit=limit)
        except Exception as exc:
            fallback_error = str(exc)
    seen: set[str] = set()
    all_hits: list[dict[str, Any]] = []
    for row in route_hits + search_hits:
        path = row.get("path") or row.get("scope") or row.get("route_id")
        if path and path not in seen:
            seen.add(path)
            all_hits.append(row)
    return {
        "ok": True,
        "query": query,
        "route_hits": route_hits,
        "recall_hits": route_hits,
        "search_hits": search_hits,
        "filesystem_hits": search_hits,
        "all_hits": all_hits,
        "fallback_used": bool(search_hits),
        "fallback_error": fallback_error,
        "finalize_required": True,
        "recall_event_id": recall.get("event_id"),
    }


def begin_file_task(query: str) -> dict[str, Any]:
    task_id = store.new_id("task")
    event = store.create_event("begin_task", query=query, task_id=task_id)
    return {"ok": True, "task_id": task_id, "query": query, "event_id": event["event_id"]}


def _is_under(path: str, anchor: str) -> bool:
    try:
        path_resolved = Path(path).resolve()
        anchor_resolved = Path(anchor).resolve()
        return path_resolved == anchor_resolved or anchor_resolved in path_resolved.parents
    except OSError:
        path_lc = path.lower()
        anchor_lc = anchor.rstrip("\\/").lower()
        return path_lc == anchor_lc or path_lc.startswith(anchor_lc + os.sep.lower()) or path_lc.startswith(anchor_lc + "/")


def _assign_path_to_route(path: str, selected_routes: list[str]) -> str | None:
    if len(selected_routes) == 1:
        return selected_routes[0]
    for route_id in selected_routes:
        for anchor in store.list_anchors(route_id):
            if _is_under(path, anchor["path"]):
                return route_id
    return None


def _common_anchor(paths: list[str]) -> str | None:
    if not paths:
        return None
    parents = [str(Path(path).parent) for path in paths]
    try:
        return os.path.commonpath(parents)
    except ValueError:
        return parents[0] if parents else None


def _draft_plan_for_paths(query: str, paths: list[str], reason: str) -> dict[str, Any] | None:
    clean_paths = [store.normalize_path(path) for path in paths if path]
    if not clean_paths:
        return None
    anchor = _common_anchor(clean_paths)
    payload = {
        "title": f"Route for: {query[:80]}",
        "brief": "Draft semantic route from a completed local file task. Review before treating it as stable.",
        "route_tags": [],
        "facets": [],
        "anchors": [anchor] if anchor else [],
        "evidence_paths": clean_paths,
        "confidence": 0.35,
        "tier": "cold",
        "reason": reason,
    }
    return store.create_update_plan("new_route_candidate", query=query, payload=payload)


def finish_local_file_task(
    query: str,
    used: list[str] | None = None,
    found: list[str] | None = None,
    rejected: list[str] | None = None,
    selected_routes: list[str] | None = None,
) -> dict[str, Any]:
    used_paths = [store.normalize_path(path) for path in (used or []) if path]
    found_paths = [store.normalize_path(path) for path in (found or []) if path]
    rejected_paths = [store.normalize_path(path) for path in (rejected or []) if path]
    route_ids = [route_id for route_id in (selected_routes or []) if store.get_route(route_id)]

    updated_routes: set[str] = set()
    unassigned: list[str] = []
    for route_id in route_ids:
        store.bump_route(route_id, usage_delta=1, confidence_delta=0.03, tier="active", used=True)
        route = store.get_route(route_id)
        if route and route.get("usage_verification") == "predicted":
            store.update_route_verification(route_id, usage_verification="observed")
        store.upsert_query_stats(route_id, query, positive=1, note="route selected in finalized task")
        updated_routes.add(route_id)

    for path in used_paths:
        route_id = _assign_path_to_route(path, route_ids)
        if route_id:
            store.add_evidence(route_id, path, role="used", note=f"Used for query: {query}", touch_used=True)
            store.bump_route(route_id, usage_delta=0.5, confidence_delta=0.02, tier="active", used=True)
            updated_routes.add(route_id)
        else:
            unassigned.append(path)

    plans: list[dict[str, Any]] = []
    draft = _draft_plan_for_paths(query, unassigned, "used_path_unassigned")
    if draft:
        plans.append(draft)
    found_only = [path for path in found_paths if path not in used_paths]
    draft = _draft_plan_for_paths(query, found_only, "fallback_found_unassigned")
    if draft:
        plans.append(draft)

    for route_id in route_ids:
        if rejected_paths:
            store.bump_route(route_id, risk_delta=0.2)
            store.upsert_query_stats(route_id, query, negative=len(rejected_paths), note="rejected path in finalized task")

    event = store.create_event(
        "finish_task",
        query=query,
        route_ids=route_ids,
        paths=used_paths + found_paths + rejected_paths,
        payload={
            "used": used_paths,
            "found": found_paths,
            "rejected": rejected_paths,
            "selected_routes": route_ids,
            "created_update_plans": [plan["plan_id"] for plan in plans],
        },
    )
    maintenance = maintenance_tick()
    return {
        "ok": True,
        "query": query,
        "event_id": event["event_id"],
        "updated_routes": sorted(updated_routes),
        "created_update_plans": plans,
        "maintenance": maintenance,
    }


def apply_update_plan(plan_id: str) -> dict[str, Any]:
    plan = store.get_update_plan(plan_id)
    if not plan:
        return {"ok": False, "plan_id": plan_id, "error": "update plan not found"}
    if plan["status"] != "draft":
        return {"ok": False, "plan_id": plan_id, "error": f"update plan is {plan['status']}"}

    payload = plan["payload"]
    if plan["kind"] == "new_route_candidate":
        route = store.create_route(
            payload.get("title") or f"Route for: {plan.get('query') or 'local file task'}",
            payload.get("brief") or "Runtime-derived semantic route.",
            route_tags=payload.get("route_tags") or [],
            facets=payload.get("facets") or [],
            route_terms=payload.get("route_terms") or payload.get("cue_terms") or [],
            task_affordances=payload.get("task_affordances") or [],
            search_hints=payload.get("search_hints") or [],
            anchors=payload.get("anchors") or [],
            evidence_paths=payload.get("evidence_paths") or [],
            tier=payload.get("tier") or "cold",
            confidence=float(payload.get("confidence") or 0.35),
            usage_verification=payload.get("usage_verification") or "observed",
            evidence_confidence=payload.get("evidence_confidence") or "medium",
            uncertainty_note=payload.get("uncertainty_note"),
            source=plan_id,
        )
        store.mark_update_plan(plan_id, "applied")
        maintenance_tick()
        return {"ok": True, "plan_id": plan_id, "applied": "new_route", "route": route}

    if plan["kind"] == "attach_evidence":
        route_id = payload.get("route_id")
        if not route_id or not store.get_route(route_id):
            return {"ok": False, "plan_id": plan_id, "error": "attach_evidence plan has no valid route_id"}
        for path in payload.get("evidence_paths") or []:
            store.add_evidence(route_id, path, role=payload.get("role") or "evidence", note=payload.get("note"))
        store.mark_update_plan(plan_id, "applied")
        maintenance_tick()
        return {"ok": True, "plan_id": plan_id, "applied": "attach_evidence", "route_id": route_id}

    return {"ok": False, "plan_id": plan_id, "error": f"unsupported update plan kind: {plan['kind']}"}


def record_correction(query: str, wrong_paths: list[str] | None = None, missed_paths: list[str] | None = None, note: str = "") -> dict[str, Any]:
    wrong = [store.normalize_path(path) for path in (wrong_paths or []) if path]
    missed = [store.normalize_path(path) for path in (missed_paths or []) if path]
    correction = store.create_correction(query, wrong, missed, note)
    plans: list[dict[str, Any]] = []
    if missed:
        draft = _draft_plan_for_paths(query, missed, "user_correction_missed_path")
        if draft:
            plans.append(draft)
    for route in store.list_routes():
        anchors = [anchor["path"] for anchor in store.list_anchors(route["route_id"])]
        if any(any(_is_under(path, anchor) for anchor in anchors) for path in wrong):
            store.bump_route(route["route_id"], risk_delta=0.5)
            store.upsert_query_stats(route["route_id"], query, negative=1, note=note or "user correction")
    event = store.create_event("correction", query=query, paths=wrong + missed, payload={"note": note, "created_update_plans": [p["plan_id"] for p in plans]})
    return {"ok": True, "correction": correction, "event_id": event["event_id"], "created_update_plans": plans}


def _clean_label(value: str) -> str:
    return re.sub(r"\s+", "-", str(value).strip().lower())


def update_route_tags(
    route_id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    evidence_note: str | None = None,
    mode: str = "draft",
) -> dict[str, Any]:
    """Update route tags with explicit correction evidence."""

    note = str(evidence_note or "").strip()
    if not note:
        return {"ok": False, "route_id": route_id, "error": "evidence_note required for route tag update"}
    route = store.get_route(route_id)
    if not route:
        return {"ok": False, "route_id": route_id, "error": "route not found"}
    add_set = {_clean_label(item) for item in (add or []) if str(item).strip()}
    remove_set = {_clean_label(item) for item in (remove or []) if str(item).strip()}
    generic = sorted(add_set & GENERIC_ROUTE_TAGS)
    if generic:
        return {"ok": False, "route_id": route_id, "error": "generic route tags are not allowed", "tags": generic}
    current = {_clean_label(item) for item in (route.get("route_tags") or []) if str(item).strip()}
    next_tags = sorted((current | add_set) - remove_set)
    payload = {
        "route_id": route_id,
        "add": sorted(add_set),
        "remove": sorted(remove_set),
        "next_tags": next_tags,
        "evidence_note": note,
        "mode": mode,
    }
    if mode == "draft":
        plan = store.create_update_plan("route_tag_update", query=None, payload=payload)
        return {"ok": True, "status": "draft", "route_id": route_id, "draft_update_plan": plan, **payload}
    updated = store.update_route_labels(route_id, route_tags=next_tags, uncertainty_note=note)
    store.create_event("route_tag_update", route_ids=[route_id], payload=payload)
    maintenance = maintenance_tick()
    return {"ok": True, "status": "applied", "route": updated, "maintenance": maintenance, **payload}


def audit_runtime(limit: int = 200) -> dict[str, Any]:
    """Audit whether recall/fallback/corrections are being closed by tasks."""

    events = store.list_events(limit=limit)
    warnings: list[dict[str, Any]] = []
    recalls = [event for event in events if event["event_type"] == "recall"]
    expands = [event for event in events if event["event_type"] == "expand"]
    finishes = [event for event in events if event["event_type"] == "finish_task"]
    corrections = [event for event in events if event["event_type"] == "correction"]
    selected_routes: set[str] = set()
    finished_queries: set[str] = set()
    for event in finishes:
        finished_queries.add(event.get("query") or "")
        selected_routes.update(event.get("route_ids") or [])
        payload = event.get("payload") or {}
        selected_routes.update(payload.get("selected_routes") or [])
        if (payload.get("found") or payload.get("used")) and not payload.get("created_update_plans"):
            warnings.append({"kind": "fallback_or_external_paths_without_update_plan", "event_id": event["event_id"], "query": event.get("query")})
    recalled_routes: set[str] = set()
    for event in recalls:
        recalled_routes.update(event.get("route_ids") or [])
        if event.get("query") not in finished_queries:
            warnings.append({"kind": "recall_query_not_finalized", "event_id": event["event_id"], "query": event.get("query")})
    unconsumed = sorted(recalled_routes - selected_routes)
    if unconsumed:
        warnings.append({"kind": "recalled_routes_not_consumed", "count": len(unconsumed), "route_ids": unconsumed[:20]})
    return {
        "ok": True,
        "event_count": len(events),
        "recall_count": len(recalls),
        "expand_count": len(expands),
        "finish_count": len(finishes),
        "correction_count": len(corrections),
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
        "routes_total": counts.get("routes", 0),
        "predicted_route_count": counts.get("predicted_routes", 0),
        "observed_route_count": counts.get("observed_routes", 0),
        "active_routes": int(tiers.get("active") or 0),
        "warm_routes": int(tiers.get("warm") or 0),
        "cold_routes": int(tiers.get("cold") or 0),
        "anchors": counts.get("anchors", 0),
        "evidence_paths": counts.get("evidence_paths", 0),
        "draft_update_plans": counts.get("draft_update_plans", 0),
        "db_path": counts.get("db_path"),
        "routes": routes,
        "route_cards": routes,
    }
