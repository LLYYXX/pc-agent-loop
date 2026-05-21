"""Build traceable overview_entries from existing nodes. Rule template only, no LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import store
from .config import DEFAULT_OVERVIEW_BRIEF_MAX_CHARS
from .directory_macro import node_has_key_evidence_compression
from .evidence_title import build_evidence_title, _looks_like_raw_dump, build_node_overview_title, pick_primary_evidence_leaf
from .store import normalize_path


def _path_under(path: str, scope: str) -> bool:
    try:
        p = Path(path).resolve()
        s = Path(scope).resolve()
        return p == s or str(p).startswith(str(s) + "\\") or str(p).startswith(str(s) + "/")
    except (ValueError, OSError):
        p = path.replace("\\", "/").rstrip("/")
        s = scope.replace("\\", "/").rstrip("/")
        return p == s or p.startswith(s + "/")


def _node_in_session(node: dict[str, Any], session_id: str, scope: str) -> bool:
    if not _path_under(node["path"], scope):
        return False
    for leaf in store.list_leaves(session_id=session_id, limit=50000):
        parent = leaf.get("parent_directory_path") or str(Path(leaf["path"]).parent)
        if parent == node["path"] or _path_under(leaf["path"], node["path"]):
            return True
    return node_has_key_evidence_compression(node)


def _brief_from_refs(evidence_refs: list[dict[str, Any]], tag_labels: list[str]) -> str:
    parts: list[str] = []
    if tag_labels:
        parts.append(f"tags: {', '.join(tag_labels[:5])}")
    for ref in evidence_refs[:3]:
        note = (ref.get("evidence_note") or "").strip()
        if note and not note.startswith("key_evidence:") and not _looks_like_raw_dump(note):
            parts.append(note[:160])
            continue
        excerpt = (ref.get("text_head_excerpt") or "").strip()
        if excerpt and not _looks_like_raw_dump(excerpt):
            leaf = store.get_leaf(ref.get("leaf_id", ""))
            if leaf:
                subj = build_evidence_title(leaf)
                if subj:
                    parts.append(subj)
            elif len(excerpt) < 120:
                parts.append(excerpt[:160])
    return "; ".join(p for p in parts if p)[:DEFAULT_OVERVIEW_BRIEF_MAX_CHARS]


def _trace_chain_valid(
    node_id: str,
    tag_ids: list[str],
    leaf_ids: list[str],
    evidence_refs: list[dict[str, Any]],
    *,
    macro_only: bool = False,
) -> bool:
    node = store.get_directory_node(node_id)
    if not node:
        return False
    if macro_only:
        if node_has_key_evidence_compression(node):
            org = node.get("org_signals")
            for lid in org["key_evidence_leaf_ids"]:
                if not store.get_leaf(lid):
                    return False
            return bool(evidence_refs)
        return False
    for tid in tag_ids:
        if not store.list_directory_tag_edges(node_id=node_id, tag_id=tid):
            return False
    for lid in leaf_ids:
        leaf = store.get_leaf(lid)
        if not leaf:
            return False
        if not store.list_leaf_tag_edges(leaf_id=lid):
            return False
    for ref in evidence_refs:
        if not ref.get("leaf_id"):
            return False
        leaf = store.get_leaf(ref["leaf_id"])
        if not leaf:
            return False
        if not (ref.get("evidence_note") or leaf.get("text_head")):
            return False
    return bool(leaf_ids and tag_ids and evidence_refs)


def _build_macro_overview(node: dict[str, Any]) -> dict[str, Any] | None:
    org = node.get("org_signals")
    if not isinstance(org, dict) or not node_has_key_evidence_compression(node):
        return None
    key_ids = org.get("key_evidence_leaf_ids") or []
    if not key_ids:
        return None
    key_leaves = [store.get_leaf(lid) for lid in key_ids]
    key_leaves = [l for l in key_leaves if l]
    evidence_refs: list[dict[str, Any]] = []
    for leaf in key_leaves[:5]:
        evidence_refs.append({
            "leaf_id": leaf["leaf_id"],
            "evidence_note": f"key_evidence:{leaf.get('evidence_type') or 'file'}",
            "text_head_excerpt": (leaf.get("text_head") or "")[:200],
        })
    if not evidence_refs:
        return None
    title = build_node_overview_title(key_leaves)
    brief = _brief_from_refs(evidence_refs, [])
    if not brief and key_leaves:
        brief = build_evidence_title(pick_primary_evidence_leaf(key_leaves) or key_leaves[0])
    brief = brief[:DEFAULT_OVERVIEW_BRIEF_MAX_CHARS]
    if not _trace_chain_valid(node["node_id"], [], key_ids, evidence_refs, macro_only=True):
        return None
    return store.create_overview_entry(
        node["node_id"],
        "directory_macro",
        title,
        brief,
        supporting_leaf_ids=key_ids,
        supporting_tag_ids=[],
        evidence_refs=evidence_refs,
        activation_weight=float(node.get("activation_weight") or 0),
    )


def build_overview(session_id: str | None = None) -> dict[str, Any]:
    scope: str | None = None
    if session_id:
        session = store.get_ingestion_session(session_id)
        if session:
            scope = normalize_path(session["scope"])
    store.clear_overview_entries()
    created: list[dict[str, Any]] = []
    skipped = 0

    for node in store.list_directory_nodes(limit=5000):
        if session_id and scope and not _node_in_session(node, session_id, scope):
            continue
        has_key_evidence_compression = node_has_key_evidence_compression(node)
        if node["node_type"] == "directory_macro":
            entry = _build_macro_overview(node)
            if entry:
                created.append(entry)
            else:
                skipped += 1
            continue

        if node["node_type"] not in ("semantic_node", "organizational"):
            continue
        if node["node_type"] == "organizational" and float(node.get("activation_weight") or 0) < 2:
            continue

        edges = [e for e in store.list_directory_tag_edges(node_id=node["node_id"])
                 if e["propagation_status"] == "local"]
        if not edges:
            if has_key_evidence_compression:
                entry = _build_macro_overview(node)
                if entry:
                    created.append(entry)
                else:
                    skipped += 1
            else:
                skipped += 1
            continue

        edges.sort(key=lambda e: -e["weight"])
        top_edges = edges[:5]
        tag_ids = [e["tag_id"] for e in top_edges]
        tag_labels = []
        for tid in tag_ids:
            t = store.get_tag(tid)
            if t:
                tag_labels.append(t["tag"])

        leaf_ids: list[str] = []
        evidence_refs: list[dict[str, Any]] = []
        evidence_leaves: list[dict[str, Any]] = []
        seen_leaves: set[str] = set()

        for edge in top_edges:
            for lte in store.list_leaf_tag_edges(tag_id=edge["tag_id"]):
                lid = lte["leaf_id"]
                leaf = store.get_leaf(lid)
                if not leaf:
                    continue
                parent = leaf.get("parent_directory_path") or str(Path(leaf["path"]).parent)
                if parent != node["path"]:
                    if not _path_under(leaf["path"], node["path"]):
                        continue
                if lid in seen_leaves:
                    continue
                seen_leaves.add(lid)
                leaf_ids.append(lid)
                evidence_leaves.append(leaf)
                excerpt = (lte.get("evidence_note") or "")[:200]
                if not excerpt and leaf.get("text_head"):
                    excerpt = (leaf["text_head"] or "")[:200]
                evidence_refs.append({
                    "leaf_id": lid,
                    "evidence_note": lte.get("evidence_note"),
                    "text_head_excerpt": excerpt,
                })
                if len(leaf_ids) >= 5:
                    break
            if len(leaf_ids) >= 5:
                break

        if not leaf_ids:
            skipped += 1
            continue

        org = node.get("org_signals")
        if node_has_key_evidence_compression(node):
            for lid in org["key_evidence_leaf_ids"]:
                leaf = store.get_leaf(lid)
                if leaf and leaf["leaf_id"] not in seen_leaves:
                    evidence_leaves.append(leaf)

        title = build_node_overview_title(evidence_leaves, tag_labels)
        brief = _brief_from_refs(evidence_refs, tag_labels)

        entry_type = "high_confidence" if node["node_type"] == "semantic_node" else "organizational"
        if not _trace_chain_valid(node["node_id"], tag_ids, leaf_ids, evidence_refs):
            skipped += 1
            continue

        entry = store.create_overview_entry(
            node["node_id"],
            entry_type,
            title,
            brief,
            supporting_leaf_ids=leaf_ids,
            supporting_tag_ids=tag_ids,
            evidence_refs=evidence_refs,
            activation_weight=float(node.get("activation_weight") or 0),
        )
        created.append(entry)

    return {"ok": True, "created": len(created), "skipped": skipped, "entries": created[:20]}
