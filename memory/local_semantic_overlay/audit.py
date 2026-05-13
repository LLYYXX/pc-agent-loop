"""Audits for Local Semantic Overlay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import store
from .config import GENERIC_ROUTE_TAGS


def _looks_like_file(path: str) -> bool:
    suffix = Path(path).suffix
    return bool(suffix and len(suffix) <= 10)


def audit_lso() -> dict[str, Any]:
    store.init_db()
    warnings: list[dict[str, Any]] = []
    routes = store.list_routes()

    for route in routes:
        anchors = store.list_anchors(route["route_id"])
        evidence = store.list_evidence(route["route_id"], limit=5)
        tags = {tag.lower() for tag in route.get("route_tags") or []}
        generic = sorted(tags & GENERIC_ROUTE_TAGS)
        if not anchors:
            warnings.append({"kind": "route_missing_anchor", "route_id": route["route_id"], "title": route["title"]})
        if not evidence:
            warnings.append({"kind": "route_missing_evidence", "route_id": route["route_id"], "title": route["title"]})
        if generic:
            warnings.append({"kind": "generic_or_file_type_route_tag", "route_id": route["route_id"], "tags": generic})
        if len(anchors) == 1 and _looks_like_file(anchors[0]["path"]):
            warnings.append({"kind": "leaf_like_route_anchor", "route_id": route["route_id"], "path": anchors[0]["path"]})
        if route["tier"] == "active" and not route.get("last_used"):
            warnings.append({"kind": "active_route_without_use", "route_id": route["route_id"], "title": route["title"]})
        if route.get("usage_verification") == "predicted":
            if route["tier"] == "active" or float(route.get("usage_score") or 0) > 0:
                warnings.append({"kind": "predicted_route_has_usage_state", "route_id": route["route_id"], "title": route["title"]})
            if not route.get("task_affordances"):
                warnings.append({"kind": "predicted_route_missing_task_affordances", "route_id": route["route_id"], "title": route["title"]})
            if not route.get("search_hints"):
                warnings.append({"kind": "predicted_route_missing_search_hints", "route_id": route["route_id"], "title": route["title"]})

    draft_plans = store.list_update_plans(status="draft", limit=500)
    if draft_plans:
        warnings.append({"kind": "unresolved_update_plans", "count": len(draft_plans), "plan_ids": [plan["plan_id"] for plan in draft_plans[:10]]})

    recall_events = store.list_events("recall", limit=500)
    finish_events = store.list_events("finish_task", limit=500)
    selected: set[str] = set()
    for event in finish_events:
        payload = event.get("payload") or {}
        selected.update(payload.get("selected_routes") or [])
    recalled: set[str] = set()
    for event in recall_events:
        recalled.update(event.get("route_ids") or [])
    unconsumed = sorted(recalled - selected)
    if unconsumed:
        warnings.append({"kind": "recalled_routes_not_consumed", "count": len(unconsumed), "route_ids": unconsumed[:10]})

    stats = store.all_query_stats()
    negative_heavy = [item for item in stats if item["negative_count"] > item["positive_count"] and item["negative_count"] >= 2]
    if negative_heavy:
        warnings.append({"kind": "negative_query_feedback", "count": len(negative_heavy), "items": negative_heavy[:10]})

    return {
        "ok": True,
        "summary": store.lso_counts(),
        "warning_count": len(warnings),
        "warnings": warnings,
    }
