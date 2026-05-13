"""Seed-map cold start for Local Semantic Overlay.

This module does not build a file-level index. It surveys a scope, identifies
high-signal terrain clusters, expands evidence packets, and writes predicted
semantic routes only when evidence and task affordances are present.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import store
from .config import (
    DEFAULT_SEED_ROUTE_BUDGET,
    DEFAULT_SURVEY_MAX_CLUSTERS,
    DEFAULT_SURVEY_MAX_DIRS,
    DEFAULT_TEXT_HEAD_CHARS,
    GENERIC_ROUTE_TAGS,
    HARD_IGNORE_DIRS,
    MARKER_FILE_NAMES,
    REPRESENTATIVE_DOC_EXTENSIONS,
    TEXT_EVIDENCE_EXTENSIONS,
)


def _is_hard_ignored(path: Path) -> bool:
    return path.name in HARD_IGNORE_DIRS


def _safe_scandir(path: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(path) as entries:
            return list(entries)
    except OSError:
        return []


def _is_text_evidence(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EVIDENCE_EXTENSIONS or path.name in MARKER_FILE_NAMES


def _is_representative_doc(path: Path) -> bool:
    return path.suffix.lower() in REPRESENTATIVE_DOC_EXTENSIONS


def _read_head(path: Path, max_chars: int = DEFAULT_TEXT_HEAD_CHARS) -> str:
    try:
        data = path.read_bytes()[: max_chars * 4]
    except OSError:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gbk", "cp936", "utf-16le"):
        try:
            return data.decode(encoding)[:max_chars]
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")[:max_chars]


def _tokens(text: str, limit: int = 24) -> list[str]:
    found = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text)
    stop = {"readme", "index", "config", "file", "folder", "project", "document", "documents"}
    values: list[str] = []
    seen: set[str] = set()
    for item in found:
        value = item.lower().strip("_-")
        if value in stop or value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= limit:
            break
    return values


def _scan_cluster(path: Path, depth: int) -> dict[str, Any]:
    dirs: list[str] = []
    files: list[Path] = []
    marker_paths: list[Path] = []
    text_paths: list[Path] = []
    doc_paths: list[Path] = []

    for entry in _safe_scandir(path)[:300]:
        entry_path = Path(entry.path)
        if entry.is_dir(follow_symlinks=False):
            if not _is_hard_ignored(entry_path):
                dirs.append(str(entry_path))
            continue
        if not entry.is_file(follow_symlinks=False):
            continue
        files.append(entry_path)
        if entry.name in MARKER_FILE_NAMES:
            marker_paths.append(entry_path)
        if _is_text_evidence(entry_path):
            text_paths.append(entry_path)
        if _is_representative_doc(entry_path):
            doc_paths.append(entry_path)

    signals: list[str] = []
    score = 0.0
    if depth <= 1:
        signals.append("top-level-area")
        score += 2.0
    if marker_paths:
        signals.append("has-marker")
        score += 4.0
    if any(path.name.lower().startswith("readme") for path in marker_paths):
        signals.append("has-readme")
        score += 2.0
    if len(text_paths) >= 2:
        signals.append("text-evidence-cluster")
        score += 2.0
    if len(doc_paths) >= 2:
        signals.append("representative-doc-cluster")
        score += 2.0
    if len(dirs) >= 2:
        signals.append("has-child-areas")
        score += 1.0
    if len(files) >= 8:
        signals.append("has-file-density")
        score += 1.0

    representative = marker_paths + text_paths[:4] + doc_paths[:6]
    seen: set[str] = set()
    representative_paths: list[str] = []
    for item in representative:
        text = str(item)
        if text not in seen:
            seen.add(text)
            representative_paths.append(text)

    if marker_paths or len(text_paths) >= 2:
        potential = "strong"
    elif doc_paths or dirs:
        potential = "medium"
    else:
        potential = "weak"

    return {
        "signals": signals,
        "representative_paths": representative_paths[:12],
        "child_areas": dirs[:16],
        "evidence_potential": potential,
        "mappability_score": round(score, 3),
    }


def begin_seed_map(scope: str, route_budget: int = DEFAULT_SEED_ROUTE_BUDGET, max_clusters: int = DEFAULT_SURVEY_MAX_CLUSTERS) -> dict[str, Any]:
    return {"ok": True, "session": store.create_seed_session(scope, route_budget, max_clusters)}


def survey_scope(session_id: str, max_dirs: int = DEFAULT_SURVEY_MAX_DIRS, max_depth: int = 3) -> dict[str, Any]:
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "session_id": session_id, "error": "seed map session not found"}

    scope = Path(session["scope"])
    if not scope.exists():
        store.update_seed_session(session_id, status="failed", report={"reason": "scope_not_found"})
        return {"ok": False, "session_id": session_id, "error": "scope not found", "scope": str(scope)}

    queue: list[tuple[Path, int]] = [(scope, 0)]
    scanned = 0
    created: list[dict[str, Any]] = []
    while queue and scanned < max_dirs and len(created) < int(session["max_clusters"]):
        path, depth = queue.pop(0)
        if _is_hard_ignored(path):
            continue
        scanned += 1
        cluster = _scan_cluster(path, depth)
        if cluster["mappability_score"] >= 2.0:
            created.append(
                store.upsert_terrain_cluster(
                    session_id,
                    path,
                    signals=cluster["signals"],
                    representative_paths=cluster["representative_paths"],
                    child_areas=cluster["child_areas"],
                    evidence_potential=cluster["evidence_potential"],
                    mappability_score=cluster["mappability_score"],
                )
            )
        if depth < max_depth:
            for child in cluster["child_areas"][:40]:
                queue.append((Path(child), depth + 1))

    report = seed_map_report(session_id)
    store.update_seed_session(session_id, report=report)
    return {
        "ok": True,
        "session_id": session_id,
        "scope": str(scope),
        "scanned_dirs": scanned,
        "cluster_count": len(created),
        "clusters": created[:20],
        "report": report,
    }


def list_terrain_clusters(session_id: str, status: str | None = None, limit: int | None = None) -> dict[str, Any]:
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "session_id": session_id, "error": "seed map session not found"}
    clusters = store.list_terrain_clusters(session_id, status=status, limit=limit)
    return {"ok": True, "session_id": session_id, "clusters": clusters, "items": clusters, "cluster_count": len(clusters)}


def expand_cluster_evidence(session_id: str, cluster_id: str, budget: str = "normal") -> dict[str, Any]:
    session = store.get_seed_session(session_id)
    cluster = store.get_terrain_cluster(cluster_id)
    if not session or not cluster or cluster["session_id"] != session_id:
        return {"ok": False, "session_id": session_id, "cluster_id": cluster_id, "error": "cluster not found in session"}

    existing = store.list_cluster_evidence(cluster_id)
    if existing:
        return _evidence_packet(cluster, existing, budget)

    limits = {"brief": 4, "normal": 10, "full": 24}
    limit = limits.get(budget, 10)
    added: list[dict[str, Any]] = []

    for raw in cluster["representative_paths"][:limit]:
        path = Path(raw)
        if path.name in MARKER_FILE_NAMES or _is_text_evidence(path):
            head = _read_head(path)
            kind = "readme_head" if path.name.lower().startswith("readme") else "text_head"
            if path.name in MARKER_FILE_NAMES and kind != "readme_head":
                kind = "manifest_head"
            added.append(store.add_cluster_evidence(session_id, cluster_id, kind=kind, path=path, text_head=head, weight=2.0 if head else 1.0))
        elif _is_representative_doc(path):
            added.append(store.add_cluster_evidence(session_id, cluster_id, kind="representative_doc", path=path, note="content_unread", weight=1.0))

    for child in cluster["child_areas"][: max(0, limit - len(added))]:
        added.append(store.add_cluster_evidence(session_id, cluster_id, kind="child_area", path=child, note="structure evidence", weight=0.5))

    if not added:
        added.append(store.add_cluster_evidence(session_id, cluster_id, kind="anchor_structure", path=cluster["anchor_path"], note="anchor had weak but mappable structure", weight=0.25))

    return _evidence_packet(cluster, added, budget)


def _evidence_packet(cluster: dict[str, Any], evidence: list[dict[str, Any]], budget: str) -> dict[str, Any]:
    raw_chars = sum(len(item.get("text_head") or "") + len(item.get("path") or "") for item in evidence)
    uncertainties = []
    if not any(item["kind"] in {"readme_head", "manifest_head", "text_head"} and item.get("text_head") for item in evidence):
        uncertainties.append("no readable text head; semantic route needs cautious wording")
    return {
        "ok": True,
        "cluster": cluster,
        "evidence_items": evidence,
        "raw_token_estimate": max(1, raw_chars // 4),
        "uncertainties": uncertainties,
        "instruction": "Commit at most one seed route for this cluster, based only on these evidence ids.",
        "budget": budget,
    }


def _derive_terms(anchor_path: str, evidence: list[dict[str, Any]], extra_terms: list[str] | None = None) -> list[str]:
    text = " ".join([Path(anchor_path).name] + [Path(item.get("path") or "").stem for item in evidence] + [item.get("text_head") or "" for item in evidence])
    values = _tokens(text, limit=32)
    for term in extra_terms or []:
        cleaned = str(term).lower().strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return values[:32]


def _evidence_confidence(evidence: list[dict[str, Any]]) -> str:
    if any(item["kind"] in {"readme_head", "manifest_head", "text_head"} and item.get("text_head") for item in evidence):
        return "strong"
    if any(item["kind"] == "representative_doc" for item in evidence):
        return "medium"
    return "weak"


def commit_seed_route(
    session_id: str,
    cluster_id: str,
    *,
    title: str,
    brief: str,
    task_affordances: list[str],
    search_hints: list[dict[str, Any]],
    evidence_refs: list[str],
    uncertainty_note: str = "",
    route_tags: list[str] | None = None,
    cue_terms: list[str] | None = None,
) -> dict[str, Any]:
    session = store.get_seed_session(session_id)
    cluster = store.get_terrain_cluster(cluster_id)
    if not session or not cluster or cluster["session_id"] != session_id:
        return {"ok": False, "error": "cluster not found in seed session", "session_id": session_id, "cluster_id": cluster_id}

    if cluster["status"] == "mapped":
        return {"ok": False, "error": "cluster already mapped", "cluster_id": cluster_id, "route_id": cluster.get("route_id")}
    if not title.strip() or not brief.strip():
        return {"ok": False, "error": "title and brief are required"}
    if "\\" in title or "/" in title or ":" in title:
        return {"ok": False, "error": "title must be a semantic label, not a path"}
    if not task_affordances:
        return {"ok": False, "error": "task_affordances are required"}
    if not search_hints:
        return {"ok": False, "error": "search_hints are required"}
    tags = [tag.strip() for tag in (route_tags or []) if tag.strip()]
    generic = sorted({tag.lower() for tag in tags} & GENERIC_ROUTE_TAGS)
    if generic:
        return {"ok": False, "error": "route_tags contain generic or file-type tags", "tags": generic}

    evidence_by_id = {item["evidence_id"]: item for item in store.list_cluster_evidence(cluster_id)}
    refs = [evidence_by_id[ref] for ref in evidence_refs if ref in evidence_by_id]
    if not refs:
        return {"ok": False, "error": "valid evidence_refs are required"}
    if all(item["kind"] in {"child_area", "anchor_structure"} for item in refs):
        return {"ok": False, "error": "path-only structure evidence is insufficient for a seed route"}

    confidence_label = _evidence_confidence(refs)
    confidence = {"strong": 0.62, "medium": 0.48, "weak": 0.32}[confidence_label]
    anchor = cluster["anchor_path"]
    existing = store.route_by_anchor(anchor)
    if existing:
        store.update_cluster_status(cluster_id, "mapped", route_id=existing["route_id"], uncertainty_note=uncertainty_note or "existing route reused for this anchor")
        store.create_event(
            "seed_route_reused",
            route_ids=[existing["route_id"]],
            paths=[anchor],
            payload={"session_id": session_id, "cluster_id": cluster_id, "evidence_refs": evidence_refs},
        )
        return {"ok": True, "reused": True, "route": existing, "cluster_id": cluster_id, "session_id": session_id}
    route = store.create_route(
        title=title.strip(),
        brief=brief.strip(),
        route_tags=tags,
        facets=cluster["signals"],
        route_terms=_derive_terms(anchor, refs, cue_terms),
        task_affordances=task_affordances,
        search_hints=search_hints,
        anchors=[anchor],
        evidence_paths=[item["path"] for item in refs if item.get("path")],
        tier="cold",
        confidence=confidence,
        usage_verification="predicted",
        evidence_confidence=confidence_label,
        uncertainty_note=uncertainty_note or "; ".join(_evidence_packet(cluster, refs, "brief")["uncertainties"]),
        source=f"seed_map:{session_id}:{cluster_id}",
    )
    store.update_cluster_status(cluster_id, "mapped", route_id=route["route_id"], uncertainty_note=uncertainty_note)
    store.create_event(
        "seed_route",
        route_ids=[route["route_id"]],
        paths=[anchor],
        payload={"session_id": session_id, "cluster_id": cluster_id, "evidence_refs": evidence_refs},
    )
    return {"ok": True, "route": route, "cluster_id": cluster_id, "session_id": session_id}


def _auto_seed_for_cluster(session_id: str, cluster: dict[str, Any]) -> dict[str, Any]:
    packet = expand_cluster_evidence(session_id, cluster["cluster_id"], budget="normal")
    if not packet["ok"]:
        return packet
    evidence = packet["evidence_items"]
    refs = [item["evidence_id"] for item in evidence if item["kind"] not in {"child_area", "anchor_structure"}][:4]
    if not refs:
        store.update_cluster_status(cluster["cluster_id"], "deferred", uncertainty_note="insufficient route-supporting evidence")
        return {"ok": False, "cluster_id": cluster["cluster_id"], "deferred": True, "reason": "insufficient route-supporting evidence"}

    anchor = cluster["anchor_path"]
    title = Path(anchor).name or "Scope root"
    terms = _derive_terms(anchor, evidence)
    evidence_names = [Path(item.get("path") or anchor).name for item in evidence[:4]]
    brief = f"Predicted route anchored at {anchor}. Evidence includes {', '.join(evidence_names)}. Expand before relying on it."
    hints = [{"scope": anchor, "query": term} for term in terms[:5]] or [{"scope": anchor, "query": title}]
    affordances = [
        "Use this route as a scoped search entry when the query overlaps its evidence terms.",
        "Expand evidence and search inside the anchor before treating the route as verified.",
    ]
    return commit_seed_route(
        session_id,
        cluster["cluster_id"],
        title=title,
        brief=brief,
        task_affordances=affordances,
        search_hints=hints,
        evidence_refs=refs,
        uncertainty_note="Automatically generated predicted route; requires task verification.",
        cue_terms=terms,
    )


def preview_seed_routes(scope: str, route_budget: int = DEFAULT_SEED_ROUTE_BUDGET, max_clusters: int = DEFAULT_SURVEY_MAX_CLUSTERS) -> dict[str, Any]:
    session = begin_seed_map(scope, route_budget=route_budget, max_clusters=max_clusters)
    survey = survey_scope(session["session"]["session_id"])
    return {"ok": survey["ok"], "session": session["session"], "survey": survey, "clusters": survey.get("clusters", [])}


def seed_lso_routes(scope: str, route_budget: int = DEFAULT_SEED_ROUTE_BUDGET, max_clusters: int = DEFAULT_SURVEY_MAX_CLUSTERS) -> dict[str, Any]:
    preview = preview_seed_routes(scope, route_budget=route_budget, max_clusters=max_clusters)
    if not preview["ok"]:
        return preview
    session_id = preview["session"]["session_id"]
    clusters = store.list_terrain_clusters(session_id, limit=max_clusters)
    committed: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for cluster in clusters:
        if len(committed) >= route_budget:
            store.update_cluster_status(cluster["cluster_id"], "deferred", uncertainty_note="route budget reached")
            continue
        result = _auto_seed_for_cluster(session_id, cluster)
        if result.get("ok"):
            committed.append(result)
        else:
            deferred.append(result)
    report = finish_seed_map(session_id)
    return {
        "ok": True,
        "session_id": session_id,
        "status": report.get("status"),
        "committed_count": len(committed),
        "routes_created": sum(1 for item in committed if not item.get("reused")),
        "routes_reused": sum(1 for item in committed if item.get("reused")),
        "committed": committed,
        "deferred": deferred[:20],
        "report": report,
        "coverage_report": report,
    }


def seed_map_report(session_id: str) -> dict[str, Any]:
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "session_id": session_id, "error": "seed map session not found"}
    clusters = store.list_terrain_clusters(session_id)
    mapped = [cluster for cluster in clusters if cluster["status"] == "mapped"]
    deferred = [cluster for cluster in clusters if cluster["status"] == "deferred"]
    unknown = [cluster for cluster in clusters if cluster["status"] == "unmapped"]
    total_weight = sum(max(0.1, float(cluster["mappability_score"])) for cluster in clusters)
    mapped_weight = sum(max(0.1, float(cluster["mappability_score"])) for cluster in mapped)
    coverage = mapped_weight / total_weight if total_weight else 0.0

    mapped_routes = [store.get_route(cluster["route_id"]) for cluster in mapped if cluster.get("route_id")]
    mapped_routes = [route for route in mapped_routes if route]
    strong_count = sum(1 for route in mapped_routes if route.get("evidence_confidence") == "strong")
    task_ready = sum(1 for route in mapped_routes if route.get("task_affordances") and route.get("search_hints"))
    strong_ratio = strong_count / len(mapped_routes) if mapped_routes else 0.0
    task_ratio = task_ready / len(mapped_routes) if mapped_routes else 0.0
    if not clusters:
        status = "failed"
    elif not mapped_routes:
        status = "incomplete"
    elif coverage >= 0.7 and task_ratio >= 0.8 and strong_ratio >= 0.4:
        status = "complete"
    elif coverage >= 0.3 and task_ratio >= 0.6:
        status = "usable_partial"
    else:
        status = "incomplete"
    return {
        "ok": True,
        "session_id": session_id,
        "scope": session["scope"],
        "status": status,
        "route_budget": session["route_budget"],
        "cluster_count": len(clusters),
        "mapped_count": len(mapped),
        "deferred_count": len(deferred),
        "unknown_count": len(unknown),
        "coverage_by_weight": round(coverage, 3),
        "strong_evidence_route_ratio": round(strong_ratio, 3),
        "task_probe_routeable_ratio": round(task_ratio, 3),
        "mapped_clusters": mapped[:20],
        "deferred_clusters": deferred[:20],
        "unknown_clusters": unknown[:20],
    }


def finish_seed_map(session_id: str) -> dict[str, Any]:
    report = seed_map_report(session_id)
    if report.get("ok"):
        store.update_seed_session(session_id, status=report["status"], report=report)
    return report


def audit_seed_routes() -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    for route in store.list_routes():
        if route.get("usage_verification") != "predicted":
            continue
        if route.get("tier") == "active" or float(route.get("usage_score") or 0) > 0:
            warnings.append({"kind": "predicted_route_has_usage_state", "route_id": route["route_id"], "title": route["title"]})
        if not route.get("task_affordances"):
            warnings.append({"kind": "predicted_route_missing_task_affordances", "route_id": route["route_id"], "title": route["title"]})
        if not route.get("search_hints"):
            warnings.append({"kind": "predicted_route_missing_search_hints", "route_id": route["route_id"], "title": route["title"]})
        if set(tag.lower() for tag in route.get("route_tags") or []) & GENERIC_ROUTE_TAGS:
            warnings.append({"kind": "predicted_route_has_generic_tag", "route_id": route["route_id"], "title": route["title"]})
    return {"ok": True, "warning_count": len(warnings), "warnings": warnings}


def prune_seed_routes(route_ids: list[str], reason: str = "pruned predicted route") -> dict[str, Any]:
    pruned: list[str] = []
    for route_id in route_ids:
        route = store.get_route(route_id)
        if not route or route.get("usage_verification") != "predicted":
            continue
        with store.connect() as conn:
            conn.execute("UPDATE routes SET status = 'rejected', risk_score = risk_score + 1, updated_at = ? WHERE route_id = ?", (store.now_iso(), route_id))
        store.create_event("prune_seed_route", route_ids=[route_id], payload={"reason": reason})
        pruned.append(route_id)
    return {"ok": True, "pruned": pruned, "count": len(pruned)}
