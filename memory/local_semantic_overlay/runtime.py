"""Query graph and fallback es only. No graph weight mutation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import store
from .config import DEFAULT_RECALL_LIMIT, GENERIC_TAGS
from .diagnostics import build_ingestion_diagnostics
from .directory_macro import build_macro_node_reports, node_has_key_evidence_compression
from .evidence_title import sanitize_display_text
from .search_substrate import search_files_rows

GENERIC_QUERY_TERMS = {
    "file", "files", "folder", "folders", "document", "documents", "project", "projects",
    "misc", "general", "code", "data", "文件", "目录", "项目", "文档",
}


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    toks = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered))
    toks.update(re.findall(r"[\u4e00-\u9fff]{2,}", lowered))
    return {t for t in toks if t not in GENERIC_QUERY_TERMS and t not in GENERIC_TAGS}


def _path_under(path: str, scope: str) -> bool:
    try:
        return Path(path).resolve().as_posix().lower().startswith(Path(scope).resolve().as_posix().lower())
    except OSError:
        return path.lower().startswith(scope.lower())


def _enrich_leaf_hit(edge: dict[str, Any], leaf: dict[str, Any], tag: dict[str, Any] | None, source: str) -> dict[str, Any]:
    excerpt = sanitize_display_text((leaf.get("text_head") or ""))[:300] or None
    return {
        "hit_type": "leaf",
        "source": source,
        "leaf_id": leaf["leaf_id"],
        "path": leaf["path"],
        "supporting_tags": [tag["tag"]] if tag else [],
        "supporting_leaf_paths": [leaf["path"]],
        "evidence_note": edge.get("evidence_note"),
        "text_head_excerpt": excerpt,
        "score": round(float(edge.get("weight", 1)) * 2, 3),
        "fallback_used": source == "fallback",
    }


def _enrich_tag_hit(tag: dict[str, Any], score: float, source: str = "map") -> dict[str, Any]:
    leaf_paths: list[str] = []
    notes: list[str] = []
    for edge in store.list_leaf_tag_edges(tag_id=tag["tag_id"])[:8]:
        leaf = store.get_leaf(edge["leaf_id"])
        if leaf:
            leaf_paths.append(leaf["path"])
            if edge.get("evidence_note"):
                notes.append(edge["evidence_note"])
    return {
        "hit_type": "tag",
        "source": source,
        "tag_id": tag["tag_id"],
        "tag": tag["tag"],
        "supporting_tags": [tag["tag"]],
        "supporting_leaf_paths": leaf_paths,
        "evidence_note": notes[0] if notes else None,
        "text_head_excerpt": None,
        "score": round(score, 3),
        "fallback_used": False,
    }


def _enrich_node_hit(node: dict[str, Any], source: str = "map") -> dict[str, Any]:
    edges = store.list_directory_tag_edges(node_id=node["node_id"])
    tag_labels: list[str] = []
    leaf_paths: list[str] = []
    notes: list[str] = []
    for e in edges[:5]:
        t = store.get_tag(e["tag_id"])
        if t:
            tag_labels.append(t["tag"])
    org = node.get("org_signals")
    if isinstance(org, dict) and node_has_key_evidence_compression(node):
        for lid in (org.get("key_evidence_leaf_ids") or [])[:5]:
            leaf = store.get_leaf(lid)
            if leaf:
                leaf_paths.append(leaf["path"])
                head = sanitize_display_text((leaf.get("text_head") or ""))
                if head:
                    notes.append(head[:200])
    for e in edges[:3]:
        for lte in store.list_leaf_tag_edges(tag_id=e["tag_id"])[:2]:
            leaf = store.get_leaf(lte["leaf_id"])
            if leaf and leaf["path"] not in leaf_paths:
                leaf_paths.append(leaf["path"])
                if lte.get("evidence_note"):
                    notes.append(lte["evidence_note"])
    excerpt = sanitize_display_text(notes[0])[:300] if notes else None
    if isinstance(org, dict) and not excerpt:
        brief = sanitize_display_text((org.get("compression_brief") or ""))
        if brief:
            excerpt = brief[:300]
    return {
        "hit_type": "node",
        "source": source,
        "node_id": node["node_id"],
        "path": node["path"],
        "node_type": node["node_type"],
        "supporting_tags": tag_labels,
        "supporting_leaf_paths": leaf_paths,
        "evidence_note": notes[0] if notes else None,
        "text_head_excerpt": excerpt,
        "activation_weight": node.get("activation_weight"),
        "score": float(node.get("activation_weight") or 0),
        "fallback_used": False,
    }


def query_to_tag_candidates(query: str, scope: str | None = None) -> list[dict[str, Any]]:
    """Match tags and overview; node matching uses activation + tag overlap only (no path-name boost)."""
    q_tokens = _tokens(query)
    q_lc = query.lower()
    candidates: list[tuple[float, str, str, dict[str, Any]]] = []

    for tag in store.list_tags():
        tag_lc = tag["tag"]
        score = 0.0
        if tag_lc in q_lc:
            score += 5
        if q_tokens & _tokens(tag_lc):
            score += len(q_tokens & _tokens(tag_lc)) * 3
        if score > 0:
            candidates.append((score, "tag", tag["tag_id"], {"tag": tag_lc, "tag_type": tag["tag_type"]}))

    for entry in store.list_overview_entries():
        if scope and entry.get("node_id"):
            node = store.get_directory_node(entry["node_id"])
            if node and not _path_under(node["path"], scope):
                continue
        text = f"{entry.get('title', '')} {entry.get('brief', '')}".lower()
        overlap = q_tokens & _tokens(text)
        score = len(overlap) * 2.5
        if score > 0:
            candidates.append((score, "overview", entry["entry_id"], {"title": entry["title"]}))

    for node in store.list_directory_nodes():
        if scope and not _path_under(node["path"], scope):
            continue
        edges = store.list_directory_tag_edges(node_id=node["node_id"])
        tag_overlap = 0.0
        for e in edges:
            t = store.get_tag(e["tag_id"])
            if t and q_tokens & _tokens(t["tag"]):
                tag_overlap += len(q_tokens & _tokens(t["tag"])) * 2
        score = tag_overlap + float(node.get("activation_weight") or 0) * 0.15
        if node_has_key_evidence_compression(node) and tag_overlap == 0:
            org = node.get("org_signals")
            if isinstance(org, dict):
                brief = sanitize_display_text((org.get("compression_brief") or ""))
                if brief and q_tokens & _tokens(brief):
                    score += 2
        if score > 0:
            candidates.append((score, "node", node["node_id"], {"path": node["path"], "node_type": node["node_type"]}))

    candidates.sort(key=lambda x: -x[0])
    return [{"score": s, "kind": k, "id": i, **payload} for s, k, i, payload in candidates[:DEFAULT_RECALL_LIMIT * 2]]


def _expand_leaves_for_tags(tag_ids: list[str], scope: str | None) -> list[dict[str, Any]]:
    hits: list[tuple[float, dict[str, Any]]] = []
    for tid in tag_ids:
        for edge in store.list_leaf_tag_edges(tag_id=tid):
            leaf = store.get_leaf(edge["leaf_id"])
            if not leaf or leaf.get("semantic_status") != "tagged":
                continue
            if scope and not _path_under(leaf["path"], scope):
                continue
            tag = store.get_tag(tid)
            score = float(edge["weight"]) * 2
            hits.append((score, _enrich_leaf_hit(edge, leaf, tag, "map")))
    hits.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    out = []
    for _, h in hits:
        if h["leaf_id"] in seen:
            continue
        seen.add(h["leaf_id"])
        out.append(h)
    return out[:DEFAULT_RECALL_LIMIT]


def _expand_nodes(node_ids: list[str], scope: str | None) -> list[dict[str, Any]]:
    hits = []
    for nid in node_ids:
        node = store.get_directory_node(nid)
        if not node:
            continue
        if scope and not _path_under(node["path"], scope):
            continue
        hits.append(_enrich_node_hit(node, "map"))
    return hits


def query_map(query: str, scope: str | None = None, limit: int = DEFAULT_RECALL_LIMIT) -> dict[str, Any]:
    store.init_db()
    candidates = query_to_tag_candidates(query, scope=scope)
    tag_ids = [c["id"] for c in candidates if c["kind"] == "tag"]
    node_ids = [c["id"] for c in candidates if c["kind"] == "node"]

    tag_hits = []
    for c in candidates:
        if c["kind"] != "tag":
            continue
        tag = store.get_tag(c["id"])
        if tag:
            tag_hits.append(_enrich_tag_hit(tag, c["score"], "map"))
    tag_hits = tag_hits[:limit]

    node_hits = _expand_nodes(node_ids, scope)[:limit]
    leaf_hits = _expand_leaves_for_tags(tag_ids, scope)[:limit]

    if not tag_ids and candidates:
        for c in candidates:
            if c["kind"] == "overview":
                entry = store.get_overview_entry(c["id"])
                if entry:
                    tag_ids.extend(entry.get("supporting_tag_ids", []))
                    if entry.get("node_id"):
                        node_ids.append(entry["node_id"])
        leaf_hits = _expand_leaves_for_tags(tag_ids, scope)[:limit]
        node_hits = _expand_nodes(list(set(node_ids)), scope)[:limit]

    return {
        "ok": True,
        "query": query,
        "tag_hits": tag_hits,
        "node_hits": node_hits,
        "leaf_hits": leaf_hits,
    }


def run_file_query(query: str, scope: str | None = None, limit: int = DEFAULT_RECALL_LIMIT, fallback: bool = True) -> dict[str, Any]:
    store.init_db()
    mapped = query_map(query, scope=scope, limit=limit)
    tag_hits = mapped.get("tag_hits", [])
    node_hits = mapped.get("node_hits", [])
    leaf_hits = mapped.get("leaf_hits", [])

    deferred_hits: list[dict[str, Any]] = []
    for leaf in store.list_leaves(semantic_status="deferred", limit=200):
        if scope and not _path_under(leaf["path"], scope):
            continue
        reason = leaf.get("extract_error") or leaf.get("readable_status") or ""
        if any(t in reason.lower() for t in _tokens(query)):
            deferred_hits.append({
                "hit_type": "deferred",
                "source": "map",
                "leaf_id": leaf["leaf_id"],
                "path": leaf["path"],
                "supporting_tags": [],
                "supporting_leaf_paths": [leaf["path"]],
                "evidence_note": reason,
                "text_head_excerpt": (leaf.get("text_head") or "")[:300] or None,
                "reason": reason,
                "score": 1.0,
                "fallback_used": False,
            })

    search_hits: list[dict[str, Any]] = []
    fallback_used = False
    fallback_reason = None
    strong = any(h.get("score", 0) > 3 for h in leaf_hits + tag_hits)

    if fallback and not strong and len(leaf_hits) + len(node_hits) < min(3, limit):
        try:
            rows = search_files_rows(query, scope=scope, limit=limit)
            for r in rows:
                search_hits.append({
                    **r,
                    "hit_type": "search",
                    "source": "fallback",
                    "supporting_tags": [],
                    "supporting_leaf_paths": [r.get("path")] if r.get("path") else [],
                    "evidence_note": "everything_search",
                    "text_head_excerpt": None,
                    "score": 1.0,
                    "fallback_used": True,
                })
            fallback_used = True
            fallback_reason = "weak map hits"
        except Exception as exc:
            fallback_reason = str(exc)

    all_hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in tag_hits + node_hits + leaf_hits:
        key = h.get("leaf_id") or h.get("node_id") or h.get("tag_id") or h.get("path")
        if key and key not in seen:
            seen.add(str(key))
            h["fallback_used"] = h.get("fallback_used", False)
            all_hits.append(h)
    for h in search_hits:
        p = h.get("path")
        if p and p not in seen:
            seen.add(p)
            all_hits.append(h)

    if leaf_hits:
        action = "inspect_leaf"
    elif node_hits:
        action = "inspect_node"
    elif search_hits:
        action = "fallback_search"
    else:
        action = "ask_user"

    store.create_event("query", query=query, paths=[h.get("path") for h in leaf_hits if h.get("path")],
                       payload={"tag_hits": len(tag_hits), "node_hits": len(node_hits)})

    return {
        "ok": True,
        "schema_version": "lso_runtime_v3",
        "query": query,
        "tag_hits": tag_hits,
        "node_hits": node_hits,
        "leaf_hits": leaf_hits,
        "route_hits": [],
        "file_annotation_hits": [],
        "deferred_hits": deferred_hits[:5],
        "search_hits": search_hits,
        "all_hits": all_hits,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "recommended_next_action": action,
        "finalize_required": True,
        "evidence_scope": {
            "map_hits": len(tag_hits) + len(node_hits) + len(leaf_hits),
            "fallback_hits": len(search_hits),
            "partial_index": True,
        },
    }


def system_overview(max_chars: int = 1500) -> dict[str, Any]:
    entries = store.list_overview_entries(limit=30)
    counts = store.lso_counts()
    with store.connect() as conn:
        row = conn.execute(
            "SELECT session_id, scope FROM ingestion_sessions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    diagnostics = None
    if row:
        diagnostics = build_ingestion_diagnostics(row["session_id"])

    lines = [f"LSO v3 overview: {len(entries)} entries (partial index)"]
    for e in entries:
        lines.append(f"- {e['title']}: {e['brief'][:120]}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."

    all_nodes = store.list_directory_nodes(limit=5000)
    directory_macro_type_nodes = sum(1 for n in all_nodes if n.get("node_type") == "directory_macro")
    key_evidence_compression_nodes = sum(1 for n in all_nodes if node_has_key_evidence_compression(n))

    coverage_basis = {
        "readable_leaves": counts.get("readable_leaves", 0),
        "tagged_leaves": counts.get("tagged_leaves", 0),
        "semantic_nodes": counts.get("semantic_nodes", 0),
        "overview_entries": counts.get("overview_entries", 0),
        "directory_macros": directory_macro_type_nodes,
        "directory_macro_type_nodes": directory_macro_type_nodes,
        "key_evidence_compression_nodes": key_evidence_compression_nodes,
    }
    weak_or_uncovered: list[str] = []
    dominant_sources: dict[str, int] = {}
    if diagnostics:
        weak_or_uncovered = (
            diagnostics.get("blind_spots", {}).get("readable_heavy_but_untagged", [])[:10]
            + diagnostics.get("blind_spots", {}).get("document_rich_but_unrepresented", [])[:5]
        )
        dominant_sources = diagnostics.get("by_seed_source", {})

    macro_nodes = build_macro_node_reports(limit=50)
    if diagnostics and diagnostics.get("macro_nodes"):
        macro_nodes = diagnostics.get("macro_nodes") or macro_nodes

    return {
        "ok": True,
        "overview": text,
        "entry_count": len(entries),
        "counts": counts,
        "coverage_basis": coverage_basis,
        "weak_or_uncovered_areas": weak_or_uncovered,
        "dominant_evidence_sources": dominant_sources,
        "macro_nodes": macro_nodes,
        "macro_coverage_note": (
            "macro_nodes list key-evidence compression "
            "(org_signals.compression_role=key_evidence_macro with key_evidence_leaf_ids); "
            "node_type may be semantic_node or directory_macro — not full directory coverage."
        ),
        "partial_map_warning": "Partial index only — reflects tagged/key-evidence coverage, not full disk.",
    }


def lso_summary() -> dict[str, Any]:
    return {"ok": True, "counts": store.lso_counts()}
