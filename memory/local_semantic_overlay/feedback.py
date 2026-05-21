"""Update leaf/tag/node weights and fallback seeds only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import store
from .leaf_read import classify_and_extract
from .leaf_seed import register_fallback_seed


def _session_id_from_result(result: dict[str, Any]) -> str | None:
    return result.get("session_id") or store.get_latest_open_session_id()


def finish_file_query(
    result: dict[str, Any],
    used: list[str] | None = None,
    found: list[str] | None = None,
    rejected: list[str] | None = None,
    selected_leaf_ids: list[str] | None = None,
    selected_node_ids: list[str] | None = None,
) -> dict[str, Any]:
    query = result.get("query") or ""
    session_id = _session_id_from_result(result)
    used_paths = [store.normalize_path(p) for p in (used or []) if p]
    found_paths = [store.normalize_path(p) for p in (found or []) if p]
    rejected_paths = [store.normalize_path(p) for p in (rejected or []) if p]

    for path in used_paths:
        leaf = store.get_leaf_by_path(path)
        if leaf:
            store.bump_leaf_counters(leaf["leaf_id"], use=1)
            for edge in store.list_leaf_tag_edges(leaf_id=leaf["leaf_id"]):
                store.bump_leaf_tag_edge_weight(leaf["leaf_id"], edge["tag_id"], 0.5)
            parent = leaf.get("parent_directory_path")
            if parent:
                node = store.get_directory_node_by_path(parent)
                if node:
                    store.bump_node_activation(node["node_id"], 0.5)
                    for de in store.list_directory_tag_edges(node_id=node["node_id"]):
                        store.bump_directory_tag_edge_weight(
                            node["node_id"], de["tag_id"], de["edge_kind"], 0.3
                        )

    for path in rejected_paths:
        leaf = store.get_leaf_by_path(path)
        if leaf:
            store.bump_leaf_counters(leaf["leaf_id"], reject=1)
            for edge in store.list_leaf_tag_edges(leaf_id=leaf["leaf_id"]):
                store.bump_leaf_tag_edge_weight(leaf["leaf_id"], edge["tag_id"], -0.5)

    for lid in selected_leaf_ids or []:
        leaf = store.get_leaf(lid)
        if leaf:
            store.bump_leaf_counters(lid, confirm=1)
            for edge in store.list_leaf_tag_edges(leaf_id=lid):
                store.upsert_leaf_tag_edge(
                    lid, edge["tag_id"],
                    weight=float(edge["weight"]) + 1,
                    source="user_confirmed",
                    evidence_note=edge.get("evidence_note"),
                )

    for nid in selected_node_ids or []:
        store.bump_node_activation(nid, 1.0)

    new_seeds = []
    for path in found_paths:
        if path in used_paths:
            continue
        info = classify_and_extract(path)
        if info["readable_status"] != "readable":
            reg = register_fallback_seed(session_id, path)
            new_seeds.append(reg.get("leaf"))
            continue
        existing = store.get_leaf_by_path(path)
        if existing:
            store.upsert_leaf(
                session_id,
                path,
                readable_status="readable",
                evidence_type=info.get("evidence_type"),
                semantic_status="seed",
                text_head=info.get("text_head"),
                extract_error=info.get("extract_error"),
                mtime=info.get("mtime"),
                ctime=info.get("ctime"),
                size=info.get("size"),
                seed_source="fallback_found",
            )
            leaf = store.get_leaf_by_path(path)
        else:
            reg = register_fallback_seed(session_id, path)
            leaf = reg.get("leaf")
        if leaf:
            new_seeds.append(leaf)

    store.create_event(
        "finish_query",
        query=query,
        paths=used_paths + found_paths + rejected_paths,
        payload={"used": used_paths, "found": found_paths, "rejected": rejected_paths},
    )

    return {
        "ok": True,
        "query": query,
        "updated_paths": len(used_paths) + len(rejected_paths),
        "new_seeds": new_seeds,
        "found_registered": len(new_seeds),
    }


def record_correction(query: str, wrong_paths: list[str] | None = None, missed_paths: list[str] | None = None, note: str = "") -> dict[str, Any]:
    wrong = [store.normalize_path(p) for p in (wrong_paths or []) if p]
    missed = [store.normalize_path(p) for p in (missed_paths or []) if p]
    session_id = store.get_latest_open_session_id()
    for path in wrong:
        leaf = store.get_leaf_by_path(path)
        if leaf:
            store.bump_leaf_counters(leaf["leaf_id"], reject=1)
    seeds = []
    for path in missed:
        seeds.append(register_fallback_seed(session_id, path))
    store.create_event("correction", query=query, paths=wrong + missed, payload={"note": note})
    return {"ok": True, "wrong": wrong, "missed_seeds": seeds}
