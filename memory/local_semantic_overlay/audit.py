"""Audits for Local Semantic Overlay v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import store
from .config import GENERIC_TAGS, HARD_IGNORE_DIRS_LOWER


def audit_lso() -> dict[str, Any]:
    store.init_db()
    warnings: list[dict[str, Any]] = []
    routes = store.list_routes()

    for route in routes:
        tags = {t.lower() for t in route.get("tags") or []}
        generic = sorted(tags & GENERIC_TAGS)
        ann_ids = route.get("supporting_annotation_ids") or []
        entrypoints = route.get("entrypoints") or []
        anchor = route.get("anchor_path")

        if not ann_ids:
            warnings.append({"kind": "route_missing_annotations", "route_id": route["route_id"], "title": route["title"]})
        if not entrypoints:
            warnings.append({"kind": "route_missing_entrypoints", "route_id": route["route_id"], "title": route["title"]})
        if not anchor:
            warnings.append({"kind": "route_missing_anchor", "route_id": route["route_id"], "title": route["title"]})
        if anchor and Path(anchor).name.lower() in HARD_IGNORE_DIRS_LOWER:
            warnings.append({"kind": "route_anchor_is_noise", "route_id": route["route_id"], "anchor": anchor})
        if generic:
            warnings.append({"kind": "generic_route_tag", "route_id": route["route_id"], "tags": generic})
        if route["tier"] == "active" and not route.get("last_used"):
            warnings.append({"kind": "active_without_use", "route_id": route["route_id"], "title": route["title"]})

        valid_anns = [store.get_annotation(aid) for aid in ann_ids]
        valid_anns = [a for a in valid_anns if a]
        orphaned = len(ann_ids) - len(valid_anns)
        if orphaned > 0:
            warnings.append({"kind": "route_orphaned_annotations", "route_id": route["route_id"], "orphaned": orphaned})

    annotations = store.list_annotations(decision="annotate")
    route_ann_ids: set[str] = set()
    for r in routes:
        route_ann_ids.update(r.get("supporting_annotation_ids") or [])
    unlinked = [a for a in annotations if a["annotation_id"] not in route_ann_ids]
    if unlinked:
        warnings.append({"kind": "annotations_not_linked_to_routes", "count": len(unlinked),
                          "sample": [a["annotation_id"] for a in unlinked[:10]]})

    plans = store.list_update_plans(status="draft", limit=500)
    if plans:
        warnings.append({"kind": "unresolved_update_plans", "count": len(plans),
                          "plan_ids": [p["plan_id"] for p in plans[:10]]})

    stats = store.all_query_stats()
    neg_heavy = [s for s in stats if s["negative_count"] > s["positive_count"] and s["negative_count"] >= 2]
    if neg_heavy:
        warnings.append({"kind": "negative_query_feedback", "count": len(neg_heavy), "items": neg_heavy[:10]})

    return {
        "ok": True,
        "summary": store.lso_counts(),
        "warning_count": len(warnings),
        "warnings": warnings,
    }
